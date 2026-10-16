"""Apply requested source updates at a quiet point; never restore private data."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'local_backups' / 'self-edit' / 'reload-status.json'
_lock = threading.RLock()
_active = 0
_draining = False
_closing = False
_thread = None


def _save(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, STATE)


def snapshot():
    with _lock:
        try:
            data = json.loads(STATE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {'id': '', 'state': 'idle', 'message': ''}
        return {**data, 'pid': os.getpid()}


@contextmanager
def action(readonly=False):
    global _active
    with _lock:
        admitted = not _closing and (not _draining or readonly)
        if admitted:_active += 1
    try:
        yield admitted
    finally:
        if admitted:
            with _lock:_active -= 1


def draining():
    with _lock:
        return _draining


def prepare(changes, request_id):
    global _draining
    changes = [dict(row) for row in changes if isinstance(row, dict)
               and re.fullmatch('[0-9a-f]{32}', str(row.get('change_id', '')))]
    if not changes:
        return None
    backend = any(row.get('kind') == 'backend' for row in changes)
    data = {'id': uuid.uuid4().hex, 'state': 'queued' if backend else 'saved',
            'mode': 'restart' if backend else 'refresh', 'request_id': request_id,
            'previous_pid': os.getpid(), 'request_ids':[request_id], 'changes': [row['change_id'] for row in changes],
            'message': '소스 저장됨 · 진행 중 작업 종료 후 자동 반영' if backend else '소스 저장됨 · 화면을 새로 반영합니다.'}
    with _lock:
        prior=snapshot()
        if request_id in prior.get('request_ids',[]) and all(row['change_id'] in prior.get('changes',[]) for row in changes):
            return prior
        # Pre-admitted requests can finish while another request drains the app.
        # Keep one ticket and one ordered rollback batch for all their changes.
        if prior.get('state')=='queued' and prior.get('previous_pid')==os.getpid():
            prior['changes']=list(dict.fromkeys(prior['changes']+data['changes']))
            prior['request_ids']=list(dict.fromkeys(prior.get('request_ids',[])+[request_id]))
            data=prior
        _save(data)
        if data.get('mode')=='restart':
            _draining = True
    return data


def schedule(server, ticket):
    global _thread
    if not ticket or ticket.get('mode') != 'restart' or ticket.get('state')!='queued':
        return
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_wait_and_restart, args=(server, ticket), daemon=True)
        _thread.start()


def _wait_and_restart(server, ticket):
    global _draining, _closing
    import model_requests
    import organizer_policy
    import scanner
    import desktop_judgment
    try:
        # Prevent a new automatic organizer pass; the in-flight pass may finish.
        organizer_policy.STOP.set()
        while True:
            with _lock:
                idle = _active == 0
            if idle and model_requests.is_idle() and not organizer_policy.RUNNING and scanner.status().get('status') != 'running' and not desktop_judgment.status().get('running'):
                with _lock:
                    if _active==0:
                        _closing=True
                        ticket=snapshot()
                        break
            time.sleep(0.25)
        with organizer_policy.LOCK:
            if organizer_policy.RUNNING:
                raise RuntimeError('폴더 정리 작업이 아직 실행 중입니다.')
            with _lock:
                ticket = dict(ticket, state='restarting', message='변경한 기능을 반영하기 위해 재시작합니다.')
                _save(ticket)
            environment = dict(os.environ, AI_OFFICE_NO_BROWSER='1', PYTHONIOENCODING='utf-8')
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--restart', ticket['id']],
                             cwd=str(ROOT), env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            server.shutdown()
            server.server_close()
    except Exception:
        with _lock:
            _draining = False
            _closing = False
            _save(dict(ticket, state='failed', message='소스는 저장됐지만 자동 재시작에 실패했습니다. 앱을 다시 실행해 주세요.'))
        organizer_policy.ensure_worker()


def _health():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8765/api/work/reload-status', timeout=1) as response:
            return json.load(response)
    except Exception:
        return None


def _launch():
    environment = dict(os.environ, AI_OFFICE_NO_BROWSER='1', PYTHONIOENCODING='utf-8')
    log = STATE.parent / 'reload.log'
    with log.open('ab') as stream:
        return subprocess.Popen([sys.executable, '-u', str(ROOT / 'app.py')], cwd=str(ROOT),
                                env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _restart(ticket_id):
    if not re.fullmatch('[0-9a-f]{32}', ticket_id):
        return
    data = snapshot()
    if data.get('id') != ticket_id or data.get('state') != 'restarting':
        return
    old_pid = data.get('previous_pid')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        health = _health()
        if health is None:
            try:
                with socket.create_connection(('127.0.0.1',8765),timeout=1):
                    pass
            except OSError:
                break
        if health and health.get('pid') != old_pid:
            return
        time.sleep(0.25)
    else:
        _save(dict(data, state='failed', message='기존 앱 종료를 확인하지 못했습니다. 소스는 저장됐습니다.'))
        return
    process = _launch()
    for attempt in range(2):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            health = _health()
            if health and health.get('pid') == process.pid:
                _save(dict(data, state='active' if attempt == 0 else 'rolled_back',
                           message='변경한 기능이 실행 중입니다.' if attempt == 0 else '시작에 실패한 소스 변경을 되돌려 앱을 복구했습니다.'))
                return
            if process.poll() is not None:
                break
            time.sleep(0.3)
        if process.poll() is None:
            # Never terminate an instance that might already be doing real work.
            _save(dict(data, state='failed', message='실행 상태를 확인하지 못했습니다. 소스와 작업 기록을 유지했습니다.'))
            return
        if attempt == 0:
            import self_edit
            restoration = self_edit.restore_changes(data['changes'])
            if restoration.get('conflicts'):
                _save(dict(data, state='failed', message='다른 소스 편집이 있어 자동 복원을 멈췄습니다. 백업과 작업 기록을 유지했습니다.'))
                return
            process = _launch()
    _save(dict(data, state='failed', message='앱을 자동 복구하지 못했습니다. 소스 백업과 개인 자료를 유지했습니다.'))


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--restart':
    _restart(sys.argv[2])
