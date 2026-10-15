"""Explicitly approved desktop candidates go to private, reversible holding.

This is not the Windows recycle bin and never permanently deletes user files.
All names and paths come from a fresh inventory, never a request body.
"""
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

import desktop_judgment
import desktop_workspace
import file_executor as engine

RUNTIME = pathlib.Path(__file__).resolve().parent / 'desktop_recovery'
HOLDING = '.AI_OFFICE_복구보관함'
LOCK = threading.RLock()
ALLOWED = {'.txt', '.md', '.csv', '.png', '.jpg', '.jpeg', '.webp'}
IMAGES = {'.png', '.jpg', '.jpeg', '.webp'}
DESIGN = {'.ai', '.psd', '.psb', '.indd', '.aep', '.prproj'}
STATES = {'approved', 'moving', 'held', 'restoring', 'restored', 'exception'}


def _id(value, length=32):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def _root():
    roots = desktop_workspace._desktop_roots()
    if not roots:
        raise ValueError('사용자 바탕화면을 찾을 수 없습니다.')
    return engine._safe_path(pathlib.Path(roots[0]).absolute())


def _journal(batch_id):
    if not _id(batch_id):
        raise ValueError('복구 작업 번호가 올바르지 않습니다.')
    engine._safe_path(RUNTIME)
    return RUNTIME / (batch_id + '.json')


def _save(batch):
    RUNTIME.mkdir(exist_ok=True)
    engine._safe_path(RUNTIME)
    engine._save(_journal(batch['batch_id']), batch, initial=not _journal(batch['batch_id']).exists())


def _paths(batch, row):
    root = _root()
    if str(root) != batch['root'] or engine.directory_identity(root) != batch['root_identity']:
        raise ValueError('바탕화면 폴더가 변경되어 작업을 보류했습니다.')
    source = root / row['name']
    destination = root / HOLDING / batch['batch_id'] / row['id'] / row['name']
    if row['source'] != str(source) or row['holding'] != str(destination):
        raise ValueError('복구 경로가 승인 범위를 벗어났습니다.')
    return source, destination


def _load(batch_id):
    path = _journal(batch_id)
    engine._safe_path(path)
    if path.stat().st_size > 512 * 1024:
        raise ValueError('복구 기록 크기가 올바르지 않습니다.')
    try:
        batch = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(batch, dict) or batch['batch_id'] != batch_id or not _id(batch['request_id'])
                or not _id(batch['revision']) or not isinstance(batch['items'], list)
                or not 1 <= len(batch['items']) <= 100
                or not isinstance(batch['root'], str) or not _identity_valid(batch['root_identity'])
                or not isinstance(batch['created_at'], str)):
            raise ValueError()
        if datetime.datetime.fromisoformat(batch['created_at']).tzinfo is None:
            raise ValueError()
        seen = set()
        total = 0
        for row in batch['items']:
            if not isinstance(row, dict):
                raise ValueError()
            name = row['name']
            if (not _id(row['id'], 64) or row['id'] in seen or not isinstance(name, str)
                    or pathlib.Path(name).name != name or any(c in name for c in '\\/:')
                    or name in {'.', '..'} or name.startswith('.') or row['status'] not in STATES
                    or pathlib.Path(name).suffix.lower() not in ALLOWED
                    or not _identity_valid(row['holding_identity'])
                    or not isinstance(row['source'], str) or not isinstance(row['holding'], str)
                    or not isinstance(row['reason'], str)):
                raise ValueError()
            seen.add(row['id'])
            source, _ = _paths(batch, row)
            if desktop_workspace._identifier(source) != row['id']:
                raise ValueError()
            fp = row['fingerprint']
            if (not isinstance(fp, dict) or set(fp) != {'dev', 'ino', 'size', 'mtime_ns', 'sha256'}
                    or any(type(fp[k]) is not int or fp[k] < 0 for k in ('dev', 'ino', 'size', 'mtime_ns'))
                    or not fp['ino'] or fp['size'] > engine.MAX_FILE_BYTES or not _id(fp['sha256'], 64)):
                raise ValueError()
            total += fp['size']
        if total > engine.MAX_TOTAL_BYTES:
            raise ValueError()
        return batch
    except (KeyError, TypeError, json.JSONDecodeError, ValueError):
        raise ValueError('복구 기록이 올바르지 않습니다. 파일을 변경하지 않았습니다.') from None


def _identity_valid(value):
    return (isinstance(value, dict) and set(value) == {'dev', 'ino'}
            and all(type(value[k]) is int and value[k] >= 0 for k in ('dev', 'ino'))
            and value['ino'] > 0)


def _eligible(path, scan_text=True):
    engine._safe_path(path)
    info = path.lstat()
    if (path.suffix.lower() not in ALLOWED or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & (2 | 4)
            or path.name.startswith('.') or desktop_judgment.SENSITIVE_NAME.search(path.name)):
        raise ValueError('보호 대상 파일은 복구 보관하지 않습니다.')
    if path.suffix.lower() in IMAGES and any(p.suffix.lower() in DESIGN for p in path.parent.iterdir()):
        raise ValueError('디자인 원본의 연결 이미지일 수 있어 보존했습니다.')
    if scan_text and path.suffix.lower() not in IMAGES:
        if info.st_size > engine.MAX_FILE_BYTES:
            raise ValueError('파일 크기 제한을 넘었습니다.')
        raw = path.read_bytes()
        if len(raw) > engine.MAX_FILE_BYTES:
            raise ValueError('읽는 동안 파일이 변경되었습니다.')
        if any(desktop_judgment.SECRET.search(raw.decode(codec, errors='replace'))
               for codec in ('utf-8-sig', 'cp949', 'utf-16')):
            raise ValueError('민감정보 가능성이 있어 파일을 보존했습니다.')


def _mkdir_below(root, parent):
    current = root
    for part in parent.relative_to(root).parts:
        with engine._directory_guards((current,)):
            target = current / part
            engine._safe_path(target, missing=True)
            target.mkdir(exist_ok=True)
            engine.directory_identity(target)
        current = target


def _hide_holding(root):
    import ctypes
    from ctypes import wintypes
    path = root / HOLDING
    with engine._directory_guards((path,)):
        engine._safe_path(path)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get_attributes = kernel.GetFileAttributesW
        get_attributes.argtypes = [wintypes.LPCWSTR]
        get_attributes.restype = wintypes.DWORD
        set_attributes = kernel.SetFileAttributesW
        set_attributes.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        set_attributes.restype = wintypes.BOOL
        flags = get_attributes(str(path))
        if flags == 0xffffffff or not set_attributes(str(path), flags | 2):
            raise ctypes.WinError(ctypes.get_last_error())


def _guard(batch, row, source, destination, restoring):
    original, holding = _paths(batch, row)
    if (source, destination) != ((holding, original) if restoring else (original, holding)):
        raise ValueError('복구 경로가 올바르지 않습니다.')
    engine._safe_path(source)
    engine._safe_path(destination, missing=True)
    if engine.directory_identity(holding.parent) != row['holding_identity']:
        raise ValueError('복구 보관 폴더가 변경되었습니다.')
    if not restoring:
        _eligible(source, scan_text=False)


def _move(batch, row, restoring=False):
    """Pin source contents and all ancestor directories until no-replace rename."""
    if sys.platform != 'win32':
        raise ValueError('안전한 복구 보관은 Windows에서만 지원합니다.')
    import ctypes
    import msvcrt
    from ctypes import wintypes
    original, holding = _paths(batch, row)
    source, destination = (holding, original) if restoring else (original, holding)
    with engine._directory_guards((source.parent, destination.parent)):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        rename = kernel.SetFileInformationByHandle
        rename.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        rename.restype = wintypes.BOOL
        handle = create(str(source), 0x80000000 | 0x10000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(fd)
            expected = row['fingerprint']
            if (engine._stamp(info) != {k: expected[k] for k in ('dev', 'ino', 'size', 'mtime_ns')}
                    or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode)):
                raise ValueError('승인 이후 파일이 변경되어 보존했습니다.')
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
            if digest.hexdigest() != expected['sha256'] or engine._stamp(os.fstat(fd)) != engine._stamp(info):
                raise ValueError('승인 이후 내용이 변경되어 보존했습니다.')
            _guard(batch, row, source, destination, restoring)
            class RenameInfo(ctypes.Structure):
                _fields_ = [('Flags', wintypes.DWORD), ('RootDirectory', wintypes.HANDLE),
                            ('FileNameLength', wintypes.DWORD), ('FileName', wintypes.WCHAR * 1)]
            encoded = str(destination).encode('utf-16-le')
            buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded) + 2)
            record = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
            record.Flags, record.RootDirectory, record.FileNameLength = 0, None, len(encoded)
            ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
            if not rename(handle, 3, buffer, len(buffer)):
                raise ctypes.WinError(ctypes.get_last_error())


def _view(batch):
    rows = [{k: row.get(k) for k in ('id', 'name', 'status', 'reason')} for row in batch['items']]
    held = sum(r['status'] == 'held' for r in rows)
    restored = sum(r['status'] == 'restored' for r in rows)
    return {'batch_id': batch['batch_id'], 'status': 'held' if held == len(rows) else
            'restored' if restored == len(rows) else 'partial', 'created_at': batch['created_at'],
            'items': rows, 'held_count': held, 'restored_count': restored,
            'exceptions': [r for r in rows if r['status'] in {'exception', 'moving', 'restoring'}]}


def holdability(candidate_ids):
    """Read-only metadata gate for explaining unavailable approval checkboxes."""
    if (not isinstance(candidate_ids, list) or len(candidate_ids) > 2000
            or any(not _id(i, 64) for i in candidate_ids)):
        raise ValueError('확인할 후보 목록이 올바르지 않습니다.')
    root = _root()
    inventory = {i['id']: i for i in desktop_workspace.inventory()['items']}
    result = {}
    for item_id in dict.fromkeys(candidate_ids):
        try:
            item = inventory.get(item_id)
            if not item or item.get('kind') != 'file' or pathlib.Path(item['path']).parent != root:
                raise ValueError('현재 사용자 바탕화면의 일반 파일만 보관할 수 있습니다.')
            _eligible(pathlib.Path(item['path']), scan_text=False)
            result[item_id] = {'allowed': True, 'reason': ''}
        except (OSError, ValueError) as error:
            result[item_id] = {'allowed': False, 'reason': str(error) if isinstance(error, ValueError)
                               else '파일의 현재 상태를 확인할 수 없어 보존합니다.'}
    return result


def hold(revision, candidate_ids, request_id):
    if (not _id(revision) or not _id(request_id) or not isinstance(candidate_ids, list)
            or not 1 <= len(candidate_ids) <= 100 or len(set(candidate_ids)) != len(candidate_ids)
            or any(not _id(i, 64) for i in candidate_ids)):
        raise ValueError('승인 요청이 올바르지 않습니다.')
    with LOCK:
        if RUNTIME.exists():
            for path in RUNTIME.glob('*.json'):
                prior = _load(path.stem)
                if prior['request_id'] == request_id:
                    if prior['revision'] != revision or [r['id'] for r in prior['items']] != candidate_ids:
                        raise ValueError('같은 요청 번호로 다른 파일을 처리할 수 없습니다.')
                    return _view(_reconcile(prior))
        snapshot = desktop_judgment.status()
        shown = {r['id'] for r in snapshot.get('candidates', [])}
        if snapshot.get('revision') != revision or not set(candidate_ids) <= shown:
            raise ValueError('후보가 변경되었습니다. 최신 결과를 확인하세요.')
        evidence = {r['id']: r for r in desktop_judgment.inspect_candidates(candidate_ids)}
        inventory = {i['id']: i for i in desktop_workspace.inventory()['items']}
        root = _root()
        batch = {'batch_id': uuid.uuid4().hex, 'request_id': request_id, 'revision': revision,
                 'root': str(root), 'root_identity': engine.directory_identity(root),
                 'created_at': _now(), 'items': []}
        total = 0
        # Validate every approved member before creating any holding directory.
        for item_id in candidate_ids:
            row = evidence.get(item_id)
            item = inventory.get(item_id)
            if not row or not item or pathlib.Path(item['path']).parent != root or item.get('kind') != 'file':
                raise ValueError('승인 이후 후보가 변경되어 파일을 보존했습니다.')
            source = pathlib.Path(item['path'])
            _eligible(source)
            fp = engine._fingerprint(source)
            if any(row['stamp'].get(k) != v for k, v in fp.items()):
                raise ValueError('승인 이후 파일이 변경되어 보존했습니다.')
            total += fp['size']
            if total > engine.MAX_TOTAL_BYTES:
                raise ValueError('한 번에 보관할 수 있는 크기를 넘었습니다.')
            destination = root / HOLDING / batch['batch_id'] / item_id / source.name
            batch['items'].append({'id': item_id, 'name': source.name, 'source': str(source),
                                   'holding': str(destination), 'fingerprint': fp, 'status': 'approved', 'reason': ''})
        with engine._directory_guards((root,)):
            for row in batch['items']:
                parent = pathlib.Path(row['holding']).parent
                _mkdir_below(root, parent)
                engine._safe_path(parent)
                row['holding_identity'] = engine.directory_identity(parent)
            _hide_holding(root)
            _save(batch)
        for row in batch['items']:
            row['status'] = 'moving'
            _save(batch)
            try:
                _move(batch, row)
            except (OSError, ValueError):
                row['reason'] = '변경·잠금·경로 충돌을 확인하세요. 기존 파일은 덮어쓰지 않습니다.'
                _reconcile_row(batch, row)
                _save(batch)
                continue
            # If verification/save fails, persisted moving intent remains recoverable.
            _reconcile_row(batch, row)
            _save(batch)
        return _view(batch)


def _matches(path, fp):
    try:
        return engine._fingerprint(path) == fp
    except (OSError, ValueError):
        return False


def _reconcile_row(batch, row):
    source, holding = _paths(batch, row)
    engine._safe_path(holding.parent)
    if engine.directory_identity(holding.parent) != row['holding_identity']:
        raise ValueError('복구 보관 폴더가 변경되었습니다.')
    original_ok, held_ok = _matches(source, row['fingerprint']), _matches(holding, row['fingerprint'])
    if row['status'] == 'moving':
        if held_ok and not source.exists():
            row.update(status='held', reason='')
        elif original_ok and not holding.exists():
            row.update(status='exception', reason=row.get('reason') or '중단된 보관은 다시 승인해야 합니다.')
    elif row['status'] == 'restoring':
        if original_ok and not holding.exists():
            row.update(status='restored', reason='')
        elif held_ok:
            row.update(status='held', reason=row.get('reason') or '원래 위치의 충돌 또는 잠금을 확인하세요.')


def _reconcile(batch):
    for row in batch['items']:
        if row['status'] in {'moving', 'restoring'}:
            _reconcile_row(batch, row)
    _save(batch)
    return batch


def restore(batch_id):
    with LOCK:
        batch = _reconcile(_load(batch_id))
        for row in batch['items']:
            if row['status'] != 'held':
                continue
            row.update(status='restoring', reason='')
            _save(batch)
            try:
                _move(batch, row, restoring=True)
            except (OSError, ValueError):
                row['reason'] = '보관 파일 변경 또는 원래 위치 충돌로 복원을 보류했습니다.'
            _reconcile_row(batch, row)
            _save(batch)
        return _view(batch)


def status():
    with LOCK:
        if not RUNTIME.exists():
            return {'batches': []}
        engine._safe_path(RUNTIME)
        paths = sorted(RUNTIME.glob('*.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        batches = []
        for path in paths[:10]:
            try:
                batches.append(_view(_load(path.stem)))
            except (ValueError, OSError):
                batches.append({'batch_id': path.stem if _id(path.stem) else None,
                                'status': 'unavailable', 'created_at': None, 'items': [],
                                'held_count': 0, 'restored_count': 0, 'exceptions': [],
                                'error': '복구 기록을 확인할 수 없습니다. 보관 파일은 변경하지 않았습니다.'})
        return {'batches': batches}


def reconcile():
    """Recheck interrupted intents and update journals only; never rename files."""
    with LOCK:
        if RUNTIME.exists():
            engine._safe_path(RUNTIME)
            for path in RUNTIME.glob('*.json'):
                try:
                    _reconcile(_load(path.stem))
                except (ValueError, OSError):
                    # Preserve both the unavailable journal and any held user files.
                    continue
        return status()
