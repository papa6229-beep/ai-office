"""Bounded shared development records, written by trusted local maintainers.

Records are reference data, not current authorization or proof of live state.
The model gets a read tool only. The fixed journal is never a source-edit target.
"""
from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import uuid

ROOT = Path(__file__).absolute().parent
DOCUMENT = ROOT / 'PROJECT_WORKLOG.jsonl'
MAX_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 24000
TEXT_FIELDS = {'author': 100, 'title': 160, 'user_request': 3000,
               'reason': 2000, 'result': 3000, 'status': 80}
LIST_FIELDS = {'process': 16, 'verification': 12, 'remaining': 12,
               'files': 40, 'commits': 20}
REQUIRED = ('author', 'title', 'user_request', 'reason', 'result', 'status')
REFERENCE_NOTE = ('공유 작업 기록은 이전 요청·판단·과정·결과의 참고 자료입니다. '
                  '현재 사용자 요청과 실제 코드·실행 상태가 우선하며 기록만으로 완료를 단정하지 마세요. '
                  '자동 공유 범위는 이 파일에 명시적으로 기록된 항목이며 전체 대화가 아닙니다.')
LOCK = threading.RLock()


def _safe(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('공유 기록의 링크 경로는 읽거나 쓸 수 없습니다.')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('공유 기록은 일반 단일 파일이어야 합니다.')
    return info


def _raw():
    before = _safe(DOCUMENT)
    if before.st_size > MAX_BYTES:
        raise ValueError('공유 기록이 허용 크기를 초과했습니다.')
    with DOCUMENT.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_BYTES
                or opened.st_nlink != 1):
            raise ValueError('공유 기록 파일이 변경되었습니다.')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('공유 기록이 허용 크기를 초과했습니다.')
    return raw


def _validate(entry, *, defaults=False):
    if not isinstance(entry, dict):
        raise ValueError('공유 기록은 객체여야 합니다.')
    entry = dict(entry)
    allowed = set(TEXT_FIELDS) | set(LIST_FIELDS) | {'id', 'time'}
    if set(entry) - allowed:
        raise ValueError('지원하지 않는 공유 기록 항목입니다.')
    if defaults:
        entry.setdefault('id', uuid.uuid4().hex)
        entry.setdefault('time', datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds'))
        for key in LIST_FIELDS:
            entry.setdefault(key, [])
    for key in ('id', 'time', *REQUIRED):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ValueError('공유 기록의 필수 항목이 없습니다: ' + key)
    if len(entry['id']) > 120 or len(entry['time']) > 80:
        raise ValueError('공유 기록 식별자 또는 날짜가 너무 깁니다.')
    try:
        date = datetime.datetime.fromisoformat(entry['time'])
        if date.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('공유 기록 날짜에는 ISO 시간대가 필요합니다.') from None
    for key, maximum in TEXT_FIELDS.items():
        if not isinstance(entry.get(key), str) or len(entry[key]) > maximum:
            raise ValueError('공유 기록 문자열의 크기를 확인하세요: ' + key)
    for key, maximum in LIST_FIELDS.items():
        value = entry.get(key)
        if (not isinstance(value, list) or len(value) > maximum
                or any(not isinstance(item, str) or len(item) > 1200 for item in value)):
            raise ValueError('공유 기록 목록의 크기를 확인하세요: ' + key)
    if len(_encode(entry)) > MAX_RECORD_BYTES:
        raise ValueError('공유 기록 한 항목의 허용 크기를 초과했습니다.')
    return entry


def _encode(entry):
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def _load():
    try:
        raw = _raw()
        lines = raw.decode('utf-8-sig').splitlines()
    except FileNotFoundError:
        return [], {'available': False, 'error': '공유 기록 파일이 아직 없습니다.', 'invalid_records': 0}
    except (OSError, UnicodeError, ValueError):
        return [], {'available': False, 'error': '공유 기록 파일을 안전하게 읽을 수 없습니다.', 'invalid_records': 0}
    records, invalid, seen = [], 0, set()
    for line in lines:
        if not line.strip():
            continue
        try:
            if len(line.encode('utf-8')) > MAX_RECORD_BYTES:
                raise ValueError()
            row = _validate(json.loads(line))
            if row['id'] in seen:
                raise ValueError()
            seen.add(row['id'])
            records.append(row)
        except (ValueError, TypeError, RecursionError):
            invalid += 1
    return records, {'available': True, 'invalid_records': invalid}


def read(limit=5, offset=0, query=''):
    """Newest-first pages; all record fields remain available in the read tool."""
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('한 번에 읽을 기록 수는 1~10입니다.')
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('기록 위치를 확인하세요.')
    if not isinstance(query, str) or len(query) > 300:
        raise ValueError('공유 기록 검색어를 확인하세요.')
    rows, meta = _load()
    total = len(rows)
    terms = query.casefold().split()
    rows = [row for row in reversed(rows)
            if all(term in json.dumps(row, ensure_ascii=False).casefold() for term in terms)]
    items = rows[offset:offset + limit]
    next_offset = offset + len(items)
    return dict(meta, source=DOCUMENT.name, reference_note=REFERENCE_NOTE,
                total=total, matched=len(rows), offset=offset, items=items,
                truncated=next_offset < len(rows),
                next_offset=next_offset if next_offset < len(rows) else None)


def brief():
    """Refresh on each request, including persisted Codex thread resume."""
    data = read(limit=3)
    lines = ['AI OFFICE 공유 개발 기록: ' + REFERENCE_NOTE,
             '전체 상세·이전 항목 검색: office_project_journal 도구.']
    if not data['available']:
        return '\n'.join(lines + [data['error']])
    lines.append('유효 기록 %s건, 최근 %s건 요약; 잘못된 항목 %s건 제외.' % (
        data['total'], len(data['items']), data['invalid_records']))
    for row in data['items']:
        compact = {key: row[key] for key in ('id', 'time', 'author', 'title', 'status')}
        for key in ('user_request', 'reason', 'result'):
            value = row[key]
            compact[key] = value[:180] + ('… [상세 도구로 확인]' if len(value) > 180 else '')
        for key in LIST_FIELDS:
            compact[key] = [value[:80] for value in row[key][:2]]
            if len(row[key]) > 2:
                compact[key].append('… [상세 도구로 확인]')
        encoded = json.dumps(compact, ensure_ascii=False)
        if sum(len(line) + 1 for line in lines) + len(encoded) > 7900:
            lines.append('추가 항목은 office_project_journal로 확인하세요.')
            break
        lines.append(encoded)
    return '\n'.join(lines)


@contextmanager
def _writer_lock():
    # OS lock is released after a crash; no stale lock-directory recovery guess.
    with LOCK:
        lock_dir = ROOT / 'local_backups'
        lock_dir.mkdir(exist_ok=True)
        for item in (lock_dir, *lock_dir.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('공유 기록 잠금 경로를 확인하세요.')
        lock_path = lock_dir / 'project-journal.lock'
        if os.path.lexists(lock_path):
            _safe(lock_path)
        with lock_path.open('a+b') as stream:
            _safe(lock_path)
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def append(entry):
    """Trusted local writer only. Retrying a supplied id preserves its timestamp."""
    with _writer_lock():
        rows, meta = _load()
        if not meta['available'] and os.path.lexists(DOCUMENT):
            raise ValueError('기존 공유 기록을 안전하게 읽을 수 없어 저장하지 않았습니다.')
        if meta['invalid_records']:
            raise ValueError('기존 공유 기록의 잘못된 항목을 먼저 확인하세요.')
        candidate = dict(entry) if isinstance(entry, dict) else entry
        previous = next((row for row in rows if row['id'] == candidate.get('id')), None) if isinstance(candidate, dict) else None
        if previous and 'time' not in candidate:
            candidate['time'] = previous['time']
        candidate = _validate(candidate, defaults=True)
        if previous:
            if previous != candidate:
                raise ValueError('동일한 기록 ID에 다른 내용을 덮어쓸 수 없습니다.')
            return dict(candidate, appended=False)
        raw = b''.join(_encode(row) + b'\n' for row in [*rows, candidate])
        if len(raw) > MAX_BYTES:
            raise ValueError('공유 기록 파일의 허용 크기를 초과했습니다.')
        # Check an existing fixed destination again immediately before replacement.
        if os.path.lexists(DOCUMENT):
            _safe(DOCUMENT)
        descriptor, temporary = tempfile.mkstemp(prefix='.project-worklog-', dir=str(ROOT))
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, DOCUMENT)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return dict(candidate, appended=True)
