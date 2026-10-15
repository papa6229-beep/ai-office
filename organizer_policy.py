"""Once-authorized local folder policies; immutable private job authority snapshots."""
import datetime
import hashlib
import json
import os
import pathlib
import re
import threading
import uuid
import file_executor as engine

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / 'organizer_policy.json'
RUNTIME = ROOT / 'organizer_runtime'
LOCK = threading.RLock()
STOP = threading.Event()
WORKER = None
LAST_ERROR = ''
RUNNING = False
INTERVAL = 60


def _read():
    if not STATE.exists():
        return {'policy': None, 'jobs': [], 'fingerprint': ''}
    engine._safe_path(STATE)
    if STATE.stat().st_size > 512 * 1024:
        raise ValueError('정리 설정이 너무 큽니다.')
    data = json.loads(STATE.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
        raise ValueError('정리 설정 형식이 올바르지 않습니다.')
    if len(data['jobs']) > 100:
        raise ValueError('작업 기록 제한을 초과했습니다.')
    policy = data.get('policy')
    if policy is not None:
        _validate_scope(policy)
        if type(policy.get('authorized')) is not bool or type(policy.get('auto_enabled')) is not bool:
            raise ValueError('정리 권한 설정 형식이 올바르지 않습니다.')
    ids = set()
    for record in data['jobs']:
        if (not isinstance(record, dict) or not re.fullmatch('[0-9a-f]{32}', str(record.get('id', '')))
                or not isinstance(record.get('scope'), dict)):
            raise ValueError('정리 기록 형식이 올바르지 않습니다.')
        if record['id'] in ids:
            raise ValueError('중복 작업 기록입니다.')
        ids.add(record['id'])
        _validate_scope(record['scope'])
    return data


def _validate_scope(scope):
    if (not isinstance(scope, dict) or not re.fullmatch('[0-9a-f]{32}', str(scope.get('policy_id', '')))
            or type(scope.get('revision')) is not int or scope['revision'] < 1
            or not isinstance(scope.get('exclusions'), list) or len(scope['exclusions']) > 50
            or any(not isinstance(x, str) or len(x) > 120 or not x.strip()
                   or any(ord(c) < 32 or c in '/\\:' for c in x) for x in scope['exclusions'])):
        raise ValueError('정리 범위 기록 형식이 올바르지 않습니다.')
    for name in ('source', 'destination'):
        try:
            engine._path(scope.get(name + '_dir', ''))
        except (TypeError, ValueError):
            raise ValueError('정리 폴더 기록이 올바르지 않습니다.')
        identity = scope.get(name + '_identity')
        if (not isinstance(identity, dict) or set(identity) != {'dev', 'ino'}
                or any(type(v) is not int or v <= 0 for v in identity.values())):
            raise ValueError('정리 폴더 신원 기록이 올바르지 않습니다.')


def _write(data):
    engine._safe_path(STATE, missing=True)
    temp = STATE.with_name(STATE.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, STATE)
    finally:
        if temp.exists():
            temp.unlink()


def _journal(record):
    path = RUNTIME / (record['id'] + '.json')
    engine._safe_path(RUNTIME)
    return path


def _job(record):
    return engine.load(_journal(record), scope=record['scope'])


def _public(job):
    return dict(job, scope='policy', counts={name: sum(i['status'] == name for i in job['items'])
                for name in engine.ITEM_STATUSES})


def status():
    with LOCK:
        data = _read()
        jobs = []
        for record in reversed(data['jobs'][-10:]):
            try:
                jobs.append(_public(_job(record)))
            except (OSError, ValueError) as error:
                jobs.append({'id': record['id'], 'scope': 'policy', 'status': 'unavailable',
                             'counts': {}, 'items': [], 'error': str(error)})
        return {'policy': data['policy'], 'jobs': jobs, 'last_error': LAST_ERROR, 'running': RUNNING}


def _pending(job):
    return (any(i['status'] in ('planned', 'moving', 'undoing') for i in job['items'])
            or (job['direction'] == 'undo' and job['status'] not in ('undone', 'undo_partial')))


def _validate_dirs(source, destination):
    source, destination = engine._path(source), engine._path(destination)
    home = pathlib.Path.home()
    protected = [ROOT, home, pathlib.Path(os.environ.get('WINDIR', 'C:/Windows')),
                 pathlib.Path(os.environ.get('ProgramFiles', 'C:/Program Files')),
                 pathlib.Path(os.environ.get('ProgramData', 'C:/ProgramData'))]
    protected.extend(pathlib.Path(os.environ[name]) for name in
                     ('APPDATA', 'LOCALAPPDATA', 'ProgramFiles(x86)') if os.environ.get(name))
    for path in (source, destination):
        engine.directory_identity(path)
        if path == pathlib.Path(path.anchor) or path == home or str(path).startswith('\\\\'):
            raise ValueError('드라이브 루트, 사용자 루트, 네트워크 폴더는 사용할 수 없습니다.')
        if any(engine._inside(path, p) for p in protected if p != home):
            raise ValueError('시스템 또는 AI OFFICE 폴더는 사용할 수 없습니다.')
        if any((parent / '.git').exists() for parent in (path, *path.parents)):
            raise ValueError('Git 저장소는 자동 정리할 수 없습니다.')
    if engine._inside(source, destination) or engine._inside(destination, source):
        raise ValueError('원본과 정리 위치는 서로 분리된 폴더여야 합니다.')
    if source.stat().st_dev != destination.stat().st_dev:
        raise ValueError('같은 드라이브의 폴더를 선택하세요.')
    return source, destination


def save(data):
    global LAST_ERROR
    with LOCK:
        current = _read()
        old = current['policy']
        source, destination = _validate_dirs(data.get('source_dir', ''), data.get('destination_dir', ''))
        exclusions = data.get('exclusions', [])
        if (not isinstance(exclusions, list) or len(exclusions) > 50
                or any(not isinstance(x, str) or not x.strip() or len(x) > 120
                       or any(ord(c) < 32 or c in '/\\:' for c in x) for x in exclusions)):
            raise ValueError('제외 규칙은 파일명 또는 *.확장자 형식으로 입력하세요.')
        enabled = data.get('auto_enabled', False)
        if type(enabled) is not bool:
            raise ValueError('자동 정리 설정 형식이 올바르지 않습니다.')
        source_identity = engine.directory_identity(source)
        destination_identity = engine.directory_identity(destination)
        changed = old is None or any(old[k] != v for k, v in
            [('source_dir', str(source)), ('destination_dir', str(destination)), ('exclusions', exclusions),
             ('source_identity', source_identity), ('destination_identity', destination_identity)])
        if (changed or enabled) and data.get('authorized') is not True:
            raise ValueError('이 범위의 파일 이동 허용을 확인하세요.')
        if changed and old:
            if old['auto_enabled'] or any(_pending(_job(record)) for record in current['jobs']):
                raise ValueError('자동 정리를 멈추고 기존 대기 작업을 완료한 뒤 범위를 변경하세요.')
        policy = dict(source_dir=str(source), destination_dir=str(destination), exclusions=exclusions,
                      auto_enabled=enabled, authorized=True,
                      policy_id=old['policy_id'] if old else uuid.uuid4().hex,
                      revision=(old['revision'] + 1 if changed else old['revision']) if old else 1,
                      source_identity=source_identity, destination_identity=destination_identity)
        current['policy'] = policy
        if changed:
            current['fingerprint'] = ''
        _write(current)
        LAST_ERROR = ''
        return status()


def _scope(policy):
    return {k: v for k, v in policy.items() if k not in ('auto_enabled', 'authorized')}


def _signature(policy):
    scope = _scope(policy)
    engine._roots(policy['source_dir'], policy['destination_dir'], RUNTIME / 'probe.json', scope)
    entries = []
    for p in sorted(pathlib.Path(policy['source_dir']).iterdir(), key=lambda p: p.name.casefold()):
        info = p.lstat()
        # Include whether settling time elapsed, so a new file is reconsidered once.
        import time
        entries.append((p.name, info.st_ino, info.st_size, info.st_mtime_ns,
                        time.time_ns() - info.st_mtime_ns >= 10_000_000_000))
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def _require_policy(data):
    policy = data['policy']
    if not policy or policy.get('authorized') is not True:
        raise ValueError('정리 범위를 먼저 저장하세요.')
    if not RUNTIME.exists():
        RUNTIME.mkdir()
    engine._safe_path(RUNTIME)
    return policy


def _preview(data):
    policy = _require_policy(data)
    for record in reversed(data['jobs']):
        if _pending(_job(record)):
            return record
    signature = _signature(policy)
    if signature == data.get('fingerprint'):
        return None
    if len(data['jobs']) >= 100:
        raise ValueError('100개 작업 기록에 도달했습니다. 자동 실행을 보류합니다.')
    job_id = uuid.uuid4().hex
    scope = _scope(policy)
    job = engine.plan(policy['source_dir'], policy['destination_dir'], RUNTIME / (job_id + '.json'), scope)
    # Engine job IDs are the API IDs; rename only the generated journal to match.
    journal = RUNTIME / (job_id + '.json')
    canonical = RUNTIME / (job['id'] + '.json')
    engine._move_no_replace(journal, canonical)
    record = {'id': job['id'], 'scope': scope}
    data['jobs'].append(record)
    data['fingerprint'] = signature
    _write(data)
    return record


def preview():
    global LAST_ERROR
    with LOCK:
        _preview(_read())
        LAST_ERROR = ''
        return status()


def run():
    global RUNNING, LAST_ERROR
    with LOCK:
        RUNNING = True
        try:
            data = _read()
            record = _preview(data)
            if record:
                job = _job(record)
                if job['direction'] == 'undo':
                    raise ValueError('되돌리기 대기 작업을 먼저 재개하세요.')
                result = engine.execute(_journal(record), record['scope'])
                # Capture the post-move inventory to avoid an empty follow-up job.
                data['fingerprint'] = ('' if result.get('remaining_candidates', 0)
                    and any(i['status'] == 'moved' for i in result['items'])
                    else _signature(data['policy']))
                _write(data)
            LAST_ERROR = ''
        except (OSError, ValueError) as error:
            LAST_ERROR = str(error)
            raise
        finally:
            RUNNING = False
        return status()


def set_enabled(enabled):
    with LOCK:
        if type(enabled) is not bool:
            raise ValueError('자동 정리 설정 형식이 올바르지 않습니다.')
        data = _read()
        _require_policy(data)
        data['policy']['auto_enabled'] = enabled
        _write(data)
        return status()


def _record(data, job_id):
    if not isinstance(job_id, str) or not re.fullmatch('[0-9a-f]{32}', job_id):
        raise ValueError('작업 ID가 올바르지 않습니다.')
    for record in data['jobs']:
        if record['id'] == job_id:
            return record
    raise ValueError('저장된 작업을 찾을 수 없습니다.')


def undo(job_id):
    with LOCK:
        data = _read()
        record = _record(data, job_id)
        if data['policy']:
            data['policy']['auto_enabled'] = False
            _write(data)
        engine.undo(_journal(record), record['scope'])
        return status()


def resume(job_id):
    with LOCK:
        data = _read()
        record = _record(data, job_id)
        job = _job(record)
        # Explicit resume retries exceptions only in the journal's own direction.
        # The engine still revalidates original identity, bytes and collisions.
        with engine._locked():
            for item in job['items']:
                if item['status'] == 'exception':
                    if job['direction'] == 'undo' and item.get('operation') == 'undo':
                        item['status'] = 'moved'
                    elif job['direction'] == 'forward' and item.get('operation') != 'undo':
                        item['status'] = 'planned'
            engine._save(_journal(record), job)
        if job['direction'] == 'undo':
            engine.undo(_journal(record), record['scope'])
        else:
            engine.execute(_journal(record), record['scope'])
        return status()


def _worker():
    global LAST_ERROR
    while not STOP.wait(max(60, INTERVAL)):
        try:
            with LOCK:
                data = _read()
                if data['policy'] and data['policy']['auto_enabled']:
                    run()
        except Exception as error:
            LAST_ERROR = str(error)
            with LOCK:
                try:
                    data = _read()
                    if data['policy']:
                        data['policy']['auto_enabled'] = False
                        _write(data)
                except Exception:
                    pass


def ensure_worker():
    global WORKER
    with LOCK:
        if WORKER is None or not WORKER.is_alive():
            STOP.clear()
            WORKER = threading.Thread(target=_worker, name='AI OFFICE folder organizer', daemon=True)
            WORKER.start()


def stop_worker():
    STOP.set()
    worker = WORKER
    if worker and worker is not threading.current_thread():
        worker.join(timeout=5)
