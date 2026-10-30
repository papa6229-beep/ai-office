"""Shared, UI-free messenger attachment intake and durable download receipts."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid

import file_operations as ops

ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
ACTIVE = set()


def _db(path):
    c = sqlite3.connect(str(path), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS messenger_downloads '
              '(id TEXT PRIMARY KEY,ref TEXT NOT NULL,source TEXT NOT NULL,state TEXT NOT NULL,'
              'data TEXT NOT NULL,updated REAL NOT NULL)')
    c.commit()
    return c


def _runtime():
    # Chromium readers run separately; never mix their interpreter's file-identity
    # metadata with the server's existing file-operation plans.
    python = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    deps = ROOT/'.provider_runtime/messenger/python/ccl_chromium_reader'
    if not python.is_file() or not deps.is_dir():
        raise ValueError('메신저 백그라운드 수신 런타임을 먼저 준비해 주세요.')
    return python


def _call(payload, timeout=40):
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    result = subprocess.run([str(_runtime()), str(ROOT/'messenger_attachment_worker.py')],
                            input=json.dumps(payload, ensure_ascii=False), text=True,
                            encoding='utf-8', capture_output=True, timeout=timeout, env=env,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or len(result.stdout) > 2*1024*1024:
        raise ValueError('백그라운드 첨부 조회를 완료하지 못했어요.')
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError('백그라운드 첨부 응답을 확인할 수 없어요.') from None
    if not value.get('ok'):
        raise ValueError(value.get('error', '첨부 수신을 완료하지 못했어요.'))
    return value['data']


def capabilities(db=None):
    try:
        _runtime()
        ready = (Path.home()/'AppData/Roaming/HanbiroTalk2/config.json').is_file()
    except ValueError:
        ready = False
    verified = False
    if db:
        with closing(_db(db)) as c:
            verified = bool(c.execute("SELECT 1 FROM messenger_downloads WHERE source='hanbiro' AND state='downloaded' LIMIT 1").fetchone())
    return {'mode': 'code', 'foreground_ui': False, 'items': [
        dict(source='hanbiro', title='한비로톡', saved_files=True,
             direct_download=ready, verified_download=verified,
             state='cache_adapter_ready' if ready else 'setup_required',
             note='현재 PC 앱에 캐시된 첨부를 기존 로그인으로 받습니다. 실제 수신 성공은 작업 기록으로 확인하세요.'),
        dict(source='nateon', title='네이트온', saved_files=True, direct_download=False,
             verified_download=False, state='saved_files_only',
             note='PC에 저장된 파일의 백그라운드 정리는 지원합니다. 대화방 미다운로드 첨부의 직접 수신 API는 아직 연결되지 않았어요.')],
        'ui_fallback': False}


def listing(source, query='', room='', offset=0):
    if source != 'hanbiro':
        raise ValueError('네이트온은 PC에 저장된 받은파일 조회를 사용해 주세요. 대화 첨부 직접 수신은 아직 지원하지 않아요.')
    if not isinstance(query, str) or len(query) > 200 or not isinstance(room, str) or len(room) > 100:
        raise ValueError('첨부 검색 조건을 확인해 주세요.')
    if not isinstance(offset, int) or not 0 <= offset <= 100000:
        raise ValueError('목록 위치를 확인해 주세요.')
    return _call(dict(action='list', query=query, room=room, offset=offset))


def status(db, ident):
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
        raise ValueError('다운로드 작업을 다시 선택해 주세요.')
    with closing(_db(db)) as c:
        row = c.execute('SELECT * FROM messenger_downloads WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('다운로드 작업을 찾을 수 없어요.')
    data = json.loads(row['data'])
    state = row['state']
    if state == 'downloaded':
        with closing(_db(db)) as c:
            current = _completed_path(c, data)
        if current:
            data['download_path'] = data.get('path')
            data['path'] = str(current)
        else:
            state = 'file_unavailable'
            data['error'] = '수신 완료 기록은 있지만 현재 파일 위치를 확인하지 못했어요.'
    if state in {'queued', 'downloading'} and ident not in ACTIVE:
        state = 'interrupted'
    return dict(data, id=ident, ref=row['ref'], source=row['source'], state=state,
                mode='code', foreground_ui=False)


def _save(db, ident, state, data):
    with closing(_db(db)) as c, c:
        c.execute('UPDATE messenger_downloads SET state=?,data=?,updated=? WHERE id=?',
                  (state, json.dumps(data, ensure_ascii=False), time.time(), ident))


def _completed_path(c, data):
    paths = [Path(data.get('path', ''))]
    # A user may have already organized this exact download with the existing
    # durable intake flow. Do not receive it again merely because it was moved.
    try:
        row = c.execute("SELECT target FROM received_items WHERE path=? AND state='organized' ORDER BY updated DESC LIMIT 1",
                        (data.get('path', ''),)).fetchone()
        if row and row['target']:
            paths.append(Path(row['target']))
    except sqlite3.OperationalError:
        pass
    for path in paths:
        if not path.is_file() or path.stat().st_size != data.get('size'):
            continue
        ops._guard(path)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
        if digest.hexdigest() == data.get('sha256'):
            return path
    return None


def _finish(db, ident, ref, destination):
    cancel = ROOT/'.provider_runtime/messenger/cancel'/ident
    try:
        _save(db, ident, 'downloading', {'destination': str(destination)})
        result = _call(dict(action='receive', ref=ref, destination=str(destination),
                            cancelled=str(cancel)), timeout=340)
        path = ops._guard(Path(result['path']))
        if path.parent != destination or path.stat().st_size != result['size']:
            raise ValueError('다운로드 파일 확인을 완료하지 못했어요.')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != result['sha256']:
            raise ValueError('다운로드 파일 검증값이 달라요.')
        _save(db, ident, 'downloaded', result)
    except (ValueError, OSError, subprocess.SubprocessError):
        _save(db, ident, 'cancelled' if cancel.exists() else 'error',
              {'destination': str(destination),
               'error': '다운로드를 중지했어요.' if cancel.exists() else '원본 첨부·로그인·통신 상태를 확인해 주세요. 저장 완료로 처리하지 않았어요.'})
    finally:
        ACTIVE.discard(ident)
        if cancel.exists():
            cancel.unlink()


def download(db, ref):
    if not isinstance(ref, str) or not re.fullmatch('[a-f0-9]{64}', ref):
        raise ValueError('실제 목록에서 첨부를 다시 선택해 주세요.')
    _runtime()
    with LOCK, closing(_db(db)) as c, c:
        source = c.execute("SELECT root FROM received_sources WHERE id='hanbiro'").fetchone()
        if not source or not source['root']:
            raise ValueError('한비로 수신 폴더를 먼저 지정해 주세요.')
        destination = ops._guard(Path(source['root']), True)
        if not destination.is_dir():
            raise ValueError('실제 수신 폴더를 확인해 주세요.')
        for old in c.execute('SELECT * FROM messenger_downloads WHERE ref=? ORDER BY updated DESC', (ref,)):
            data = json.loads(old['data'])
            if old['state'] in {'queued', 'downloading'} and old['id'] in ACTIVE:
                return dict(id=old['id'], state=old['state'], reused=True)
            if old['state'] == 'downloaded':
                path = _completed_path(c, data)
                if path:
                    return dict(id=old['id'], state='downloaded', reused=True, path=str(path))
        ident = uuid.uuid4().hex
        c.execute('INSERT INTO messenger_downloads VALUES(?,?,?,?,?,?)',
                  (ident, ref, 'hanbiro', 'queued', '{}', time.time()))
        ACTIVE.add(ident)
    threading.Thread(target=_finish, args=(db, ident, ref, destination), daemon=True,
                     name='messenger-attachment-'+ident[:8]).start()
    return dict(id=ident, state='queued', mode='code', model_calls=0, foreground_ui=False)


def cancel(db, ident):
    row = status(db, ident)
    if row['state'] not in {'queued', 'downloading'}:
        return row
    path = ROOT/'.provider_runtime/messenger/cancel'/ident
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return dict(id=ident, state='cancel_requested', note='진행 중 네트워크 응답이 끝나는 지점에서 중지합니다.')
