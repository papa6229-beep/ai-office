"""Private native conversation identity and non-replaying tool receipts."""
import contextlib
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import uuid

_locks = {}
_guard = threading.Lock()


def _now():
    return datetime.datetime.now().astimezone().isoformat()


def conversation_lock(db):
    key = str(Path(db).resolve()).casefold()
    with _guard:
        return _locks.setdefault(key, threading.RLock())


@contextlib.contextmanager
def _db(path):
    connection = sqlite3.connect(str(path), timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS assistant_agent_sessions (
            name TEXT PRIMARY KEY,data TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assistant_agent_calls (
            thread_id TEXT NOT NULL,call_id TEXT NOT NULL,request_id TEXT NOT NULL,
            tool TEXT NOT NULL,signature TEXT NOT NULL,status TEXT NOT NULL,
            result TEXT,created TEXT NOT NULL,PRIMARY KEY(thread_id,call_id));
    ''')


def load(db):
    with _db(db) as c:
        initialize(c)
        row = c.execute("SELECT data FROM assistant_agent_sessions WHERE name='office'").fetchone()
        if row:
            data = json.loads(row['data'])
        else:
            data = {'session_id': uuid.uuid4().hex, 'refs': {}, 'status': 'new'}
            c.execute('INSERT INTO assistant_agent_sessions VALUES (?,?,?)',
                      ('office', json.dumps(data), _now()))
        return data


def save(db, updates):
    allowed = {'thread_id', 'tools_sha256', 'turn_id', 'status', 'request_id', 'refs'}
    with _db(db) as c:
        row = c.execute("SELECT data FROM assistant_agent_sessions WHERE name='office'").fetchone()
        if not row:
            raise ValueError('대화 세션을 먼저 확인해야 합니다.')
        data = json.loads(row['data'])
        data.update({key: value for key, value in updates.items() if key in allowed})
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(raw.encode('utf-8')) > 8 * 1024 * 1024:
            raise ValueError('대화의 실행 참조 용량을 넘겼습니다.')
        c.execute("UPDATE assistant_agent_sessions SET data=?,updated=? WHERE name='office'",
                  (raw, _now()))


def claim_call(db, context, name, arguments, identity):
    thread_id, call_id = identity.get('thread_id'), identity.get('call_id')
    if not all(isinstance(value, str) and 0 < len(value) <= 200 for value in (thread_id, call_id)):
        raise ValueError('Codex 도구 호출의 연결 정보를 확인하지 못했습니다.')
    signature = hashlib.sha256(json.dumps([name, arguments], ensure_ascii=False,
                                         sort_keys=True, allow_nan=False).encode()).hexdigest()
    with _db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM assistant_agent_calls WHERE thread_id=? AND call_id=?',
                        (thread_id, call_id)).fetchone()
        if row:
            if row['tool'] != name or row['signature'] != signature:
                raise ValueError('이미 사용한 도구 호출의 내용이 달라졌습니다.')
            if row['status'] == 'done' and row['result'] is not None:
                return dict(json.loads(row['result']), replayed=True)
            return {'status': 'error', 'message':
                    '이전 실행의 결과가 불확실해서 같은 동작을 반복하지 않았어. 현재 상태를 먼저 확인해야 해.',
                    'data': {'execution_uncertain': True}, 'replayed': True}
        c.execute('INSERT INTO assistant_agent_calls VALUES (?,?,?,?,?,?,?,?)',
                  (thread_id, call_id, context['request_id'], name, signature, 'running', None, _now()))
    return None


def finish_call(db, identity, result):
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(raw) > 300000:
        raise ValueError('실행 기록이 너무 큽니다.')
    with _db(db) as c:
        c.execute("UPDATE assistant_agent_calls SET status='done',result=? WHERE thread_id=? AND call_id=?",
                  (raw, identity['thread_id'], identity['call_id']))


_NATIVE_LABELS = {
    'commandExecution': ('codex_command', '작업 공간에서 코드 실행', 'external'),
    'fileChange': ('codex_file_change', '작업 공간 파일 수정', 'write'),
    'imageView': ('codex_image_view', '이미지 확인', 'read'),
    'webSearch': ('codex_web_search', '웹 검색', 'read'),
    'contextCompaction': ('codex_context', '대화 맥락 정리', 'read'),
}


def record_event(db, context, event):
    kind, item_id = event.get('kind'), event.get('item_id')
    if kind not in _NATIVE_LABELS or not isinstance(item_id, str):
        return
    name, label, effect = _NATIVE_LABELS[kind]
    step_id = hashlib.sha256((str(event.get('thread_id')) + ':' + item_id).encode()).hexdigest()[:32]
    completed = event.get('phase') == 'completed'
    failed = event.get('status') in {'failed', 'declined', 'interrupted'} or event.get('exit_code') not in {None, 0}
    result = {'status': 'error' if failed else 'ok',
              'message': label + ('을 마치지 못했어.' if failed else '을 마쳤어.')}
    with _db(db) as c:
        c.execute('INSERT OR IGNORE INTO assistant_steps VALUES (?,?,?,?,?,?,?,?)',
                  (step_id, context['request_id'], name, effect, '{}',
                   'running', None, _now()))
        if completed:
            c.execute("UPDATE assistant_steps SET status='done',result=? WHERE id=?",
                      (json.dumps(result, ensure_ascii=False), step_id))
