"""Journaled, bounded Windows moves. Caller supplies the authorized folder scope.

No deletion, recursive traversal, overwrite, or model-generated commands are used.
The journal is private runtime data and must not be published with source code.
"""
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import threading
import uuid

LOCK = threading.Lock()
MAX_FILES = 100
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
CATEGORIES = {**dict.fromkeys(('.txt', '.md', '.csv', '.json', '.log'), '텍스트'),
              **dict.fromkeys(('.png', '.jpg', '.jpeg', '.webp'), '이미지'),
              **dict.fromkeys(('.pdf', '.docx', '.xlsx', '.pptx'), '문서')}
ITEM_STATUSES = {'planned', 'moving', 'moved', 'skipped', 'undoing', 'undone', 'exception'}
JOB_STATUSES = {'planned', 'completed', 'partial', 'undone', 'undo_partial'}


@contextlib.contextmanager
def _locked():
    if not LOCK.acquire(blocking=False):
        raise ValueError('다른 파일 정리 작업이 진행 중입니다.')
    try:
        yield
    finally:
        LOCK.release()


def _path(value):
    path = pathlib.Path(value)
    if not path.is_absolute() or str(path) != os.path.abspath(str(path)):
        raise ValueError('절대 경로만 사용할 수 있습니다.')
    return path


def _inside(path, parent):
    return os.path.commonpath([str(path), str(parent)]) == str(parent)


def _safe_path(path, missing=False):
    path = _path(path)
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if part == path and missing:
                continue
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise ValueError('링크 또는 재분석 경로는 정리하지 않습니다.')
    return path


def _roots(source, destination, journal):
    source, destination, journal = _path(source), _path(destination), _path(journal)
    if (source == journal.parent or destination == journal.parent
            or not _inside(source, journal.parent) or not _inside(destination, journal.parent)):
        raise ValueError('이번 시험 작업은 기록 폴더 안에서만 실행할 수 있습니다.')
    if _inside(source, destination) or _inside(destination, source):
        raise ValueError('원본과 정리 위치는 서로 분리된 폴더여야 합니다.')
    if _inside(journal, source) or _inside(journal, destination):
        raise ValueError('작업 기록은 정리 폴더 밖에 보관해야 합니다.')
    for path in (source, destination, journal.parent):
        _safe_path(path)
        if not path.is_dir():
            raise ValueError('작업 폴더를 찾을 수 없습니다.')
    if source.stat().st_dev != destination.stat().st_dev:
        raise ValueError('현재는 같은 드라이브 안에서만 정리할 수 있습니다.')
    _safe_path(journal, missing=True)
    return source, destination, journal


def _stamp(info):
    return {name: int(getattr(info, 'st_' + name)) for name in ('dev', 'ino', 'size', 'mtime_ns')}


def _fingerprint(path):
    _safe_path(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES
            or not before.st_ino or before.st_nlink != 1):
        raise ValueError('안전하게 비교할 수 없는 파일입니다.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        fd_before = os.fstat(stream.fileno())
        if _stamp(before) != _stamp(fd_before):
            raise ValueError('파일이 비교 중 변경되었습니다.')
        count = 0
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > MAX_FILE_BYTES:
                raise ValueError('파일 크기 제한을 초과했습니다.')
            digest.update(block)
        fd_after = os.fstat(stream.fileno())
    _safe_path(path)
    after = path.lstat()
    if (_stamp(before) != _stamp(after) or _stamp(fd_before) != _stamp(fd_after)
            or before.st_ctime_ns != after.st_ctime_ns
            or fd_before.st_ctime_ns != fd_after.st_ctime_ns):
        raise ValueError('파일이 비교 중 변경되었습니다.')
    return dict(_stamp(after), sha256=digest.hexdigest())


def _matches(path, expected):
    try:
        return _fingerprint(path) == expected
    except (OSError, ValueError):
        return False


def _validate(job, journal):
    if (not isinstance(job, dict) or job.get('schema') != 1
            or not re.fullmatch('[0-9a-f]{32}', str(job.get('id', '')))
            or job.get('status') not in JOB_STATUSES
            or not isinstance(job.get('items'), list) or len(job['items']) > MAX_FILES):
        raise ValueError('작업 기록 형식이 올바르지 않습니다.')
    if 'direction' not in job:
        # Older journals still retain per-item undo evidence across a crash.
        job['direction'] = 'undo' if (job['status'] in ('undone', 'undo_partial')
            or any(isinstance(i, dict) and (i.get('status') in ('undoing', 'undone')
                       or i.get('operation') == 'undo') for i in job['items'])) else 'forward'
    if job['direction'] not in ('forward', 'undo'):
        raise ValueError('작업 진행 방향이 올바르지 않습니다.')
    source, destination, _ = _roots(job.get('source_dir'), job.get('destination_dir'), journal)
    names = set()
    total = 0
    for item in job['items']:
        if not isinstance(item, dict) or item.get('status') not in ITEM_STATUSES:
            raise ValueError('파일 작업 기록 형식이 올바르지 않습니다.')
        name = item.get('name')
        if (not isinstance(name, str) or not name or name in ('.', '..')
                or any(c in name for c in '/\\:') or name.endswith((' ', '.'))
                or name.casefold() in names or item.get('source') != str(source / name)):
            raise ValueError('파일 작업 경로가 허용 범위를 벗어났습니다.')
        names.add(name.casefold())
        fingerprint = item.get('fingerprint')
        if fingerprint is None and item['status'] == 'skipped':
            if item.get('destination') is not None:
                raise ValueError('제외 파일의 목적지가 올바르지 않습니다.')
            continue
        category = CATEGORIES.get(pathlib.Path(name).suffix.lower())
        if not category or item.get('destination') != str(destination / category / name):
            raise ValueError('정리 목적지가 허용 범위를 벗어났습니다.')
        if (not isinstance(fingerprint, dict) or set(fingerprint) != {'dev', 'ino', 'size', 'mtime_ns', 'sha256'}
                or any(type(fingerprint.get(k)) is not int or fingerprint[k] < 0
                       for k in ('dev', 'ino', 'size', 'mtime_ns'))
                or not fingerprint['ino'] or fingerprint['size'] > MAX_FILE_BYTES
                or not re.fullmatch('[0-9a-f]{64}', str(fingerprint.get('sha256', '')))):
            raise ValueError('파일 비교 기록이 올바르지 않습니다.')
        total += fingerprint['size']
    if total > MAX_TOTAL_BYTES:
        raise ValueError('작업 크기 제한을 초과했습니다.')
    return job


def _load(journal):
    journal = _path(journal)
    _safe_path(journal)
    if journal.stat().st_size > 512 * 1024:
        raise ValueError('작업 기록이 너무 큽니다.')
    try:
        job = json.loads(journal.read_text(encoding='utf-8'))
        return _validate(job, journal)
    except (TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError('작업 기록이 올바르지 않습니다.') from error


def _save(journal, job, initial=False):
    _safe_path(journal, missing=True)
    temp = journal.with_name(journal.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(job, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if initial:
            _move_no_replace(temp, journal)
        else:
            os.replace(temp, journal)
    finally:
        # Only our uniquely generated incomplete journal temp, never user data.
        if temp.exists():
            temp.unlink()


def _move_no_replace(source, destination):
    if sys.platform != 'win32':
        raise ValueError('안전한 파일 정리는 Windows에서만 지원합니다.')
    os.rename(source, destination)  # Windows rename fails if target exists.


def load(journal_path):
    with _locked():
        return _load(journal_path)


def plan(source_dir, destination_dir, journal_path):
    with _locked():
        source, destination, journal = _roots(source_dir, destination_dir, journal_path)
        if journal.exists():
            raise ValueError('기존 작업 기록은 덮어쓸 수 없습니다.')
        job = {'schema': 1, 'id': uuid.uuid4().hex, 'status': 'planned', 'direction': 'forward',
               'source_dir': str(source), 'destination_dir': str(destination),
               'items': [], 'warnings': [],
               'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        total = 0
        for path in sorted(source.iterdir(), key=lambda p: p.name.casefold()):
            if len(job['items']) == MAX_FILES:
                job['warnings'].append('최대 100개 항목만 이번 작업에 포함했습니다.')
                break
            item = {'name': path.name, 'source': str(path), 'destination': None,
                    'status': 'skipped', 'reason': '', 'fingerprint': None}
            category = CATEGORIES.get(path.suffix.lower())
            try:
                _safe_path(path)
                if path.suffix.lower() in ('.ai', '.psd'):
                    raise ValueError('외부 연결 자료 확인이 필요한 디자인 원본입니다.')
                if not category or not path.is_file():
                    raise ValueError('지원하지 않는 파일 또는 하위 폴더입니다.')
                fingerprint = _fingerprint(path)
                if total + fingerprint['size'] > MAX_TOTAL_BYTES:
                    raise ValueError('전체 파일 크기 제한을 초과했습니다.')
                total += fingerprint['size']
                item.update(destination=str(destination / category / path.name),
                            fingerprint=fingerprint, status='planned')
            except (OSError, ValueError) as error:
                item['reason'] = str(error)
            job['items'].append(item)
        _validate(job, journal)
        _save(journal, job, initial=True)
        return job


def _run(journal_path, reverse):
    if sys.platform != 'win32':
        raise ValueError('안전한 파일 정리는 Windows에서만 지원합니다.')
    journal = _path(journal_path)
    job = _load(journal)
    if not reverse and (job['direction'] == 'undo'
            or any(i['status'] in ('undoing', 'undone') or i.get('operation') == 'undo'
                   for i in job['items'])):
        if job['status'] == 'undone':
            return job
        raise ValueError('되돌리기 작업이 시작되어 정리를 재실행할 수 없습니다. 되돌리기를 재개하세요.')
    if not reverse and job['status'] in ('undone', 'undo_partial'):
        return job
    if reverse:
        job['direction'] = 'undo'
        _save(journal, job)
    for item in job['items']:
        if item['status'] == 'skipped':
            continue
        src, dst = pathlib.Path(item['source']), pathlib.Path(item['destination'])
        original, target = (dst, src) if reverse else (src, dst)
        pending, done = ('undoing', 'undone') if reverse else ('moving', 'moved')
        # Forward intent may have been committed before the first rename occurred.
        # Reconcile that already-restored endpoint without moving any file.
        if reverse and item['status'] == 'moving' and not dst.exists() and _matches(src, item['fingerprint']):
            item.update(status='undone', reason='')
            _save(journal, job)
            continue
        # Recover an intent written before a crash, using identity as well as bytes.
        if item['status'] == pending:
            if not original.exists() and _matches(target, item['fingerprint']):
                item.update(status=done, reason='')
                _save(journal, job)
                continue
            if not original.exists():
                item['reason'] = '중단된 이동 결과를 아직 확인할 수 없습니다. 파일을 보존하고 재확인합니다.'
                _save(journal, job)
                continue
        if item['status'] == done:
            continue
        if reverse and item['status'] not in ('moved', 'moving', 'undoing'):
            continue
        if not reverse and item['status'] not in ('planned', 'moving'):
            continue
        renamed = False
        previous_status = item['status']
        try:
            _roots(job['source_dir'], job['destination_dir'], journal)
            _safe_path(original)
            if not _matches(original, item['fingerprint']):
                raise ValueError('계획 이후 변경되었거나 원본 파일을 찾을 수 없습니다.')
            if not target.parent.exists():
                _safe_path(target.parent.parent)
                target.parent.mkdir()
            _safe_path(target.parent)
            _safe_path(target, missing=True)
            if target.exists():
                raise ValueError('같은 이름의 파일이 있어 덮어쓰지 않았습니다.')
            item.update(status=pending, reason='')
            _save(journal, job)
            # Recheck after journal IO immediately before the atomic no-replace move.
            _safe_path(original)
            _safe_path(target, missing=True)
            if not _matches(original, item['fingerprint']):
                raise ValueError('실행 직전 파일이 변경되었습니다.')
            _move_no_replace(original, target)
            renamed = True
            if not _matches(target, item['fingerprint']):
                raise ValueError('이동 이후 파일 변경을 확인했습니다. 자동 복구를 보류합니다.')
            item.update(status=done, reason='')
        except (OSError, ValueError) as error:
            item.update(status=pending if renamed else (previous_status
                        if previous_status in ('moving', 'undoing') else 'exception'), reason=str(error),
                        operation='undo' if reverse else 'move')
        _save(journal, job)
    if reverse:
        job['status'] = 'undo_partial' if any(i['status'] in ('moved', 'moving', 'undoing', 'exception')
                                             for i in job['items']) else 'undone'
    else:
        job['status'] = 'partial' if any(i['status'] in ('planned', 'moving', 'exception')
                                        for i in job['items']) else 'completed'
    _save(journal, job)
    return job


def execute(journal_path):
    with _locked():
        return _run(journal_path, False)


def undo(journal_path):
    with _locked():
        return _run(journal_path, True)
