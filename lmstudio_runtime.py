"""Prepare the explicitly selected local LM Studio model without changing settings."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

MODEL = 'google/gemma-4-e4b'
BASE_URL = 'http://127.0.0.1:1234/v1'
_lock = threading.Lock()
_cached = None
_cached_at = 0.0
_last_error = ''
_failed_at = 0.0


@contextmanager
def _process_lock():
    """Serialize launcher and app preparation; each process rechecks inventory."""
    lock_path = Path(__file__).resolve().parent / 'local_backups' / '.lmstudio-prepare.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'\0')
            handle.flush()
        deadline = time.monotonic() + 330
        if os.name == 'nt':
            import msvcrt
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('다른 AI OFFICE가 모델을 준비 중이야. LM Studio 상태를 확인하고 다시 요청해줘.')
                time.sleep(0.15)
        try:
            yield
        finally:
            release()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('LM Studio 연결 주소가 예상과 달라. 로컬 서버 설정을 확인해줘.')


def _models():
    # Never send the local readiness request through a configured system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open('http://127.0.0.1:1234/api/v1/models', timeout=3) as response:
        if response.geturl() != 'http://127.0.0.1:1234/api/v1/models':
            raise RuntimeError('LM Studio 연결 주소가 예상과 달라. 로컬 서버 설정을 확인해줘.')
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise RuntimeError('LM Studio 모델 목록이 너무 커서 확인하지 못했어.')
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise RuntimeError('LM Studio 모델 목록 형식을 읽지 못했어. 버전과 서버 설정을 확인해줘.')
    return data['models']


def _run(cli, arguments, timeout):
    try:
        result = subprocess.run(
            [str(cli), *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', timeout=timeout,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('LM Studio 준비 시간이 초과됐어. LM Studio에서 선택한 모델 상태를 확인해줘.') from error
    if result.returncode:
        detail = result.stdout.strip()[-1200:]
        raise RuntimeError('LM Studio 준비에 실패했어. 로컬 서버 상태를 확인해줘. ' + detail)


def _ready_model(models, context_length):
    selected = next((model for model in models if model.get('key') == MODEL), None)
    if selected is None:
        raise RuntimeError('Gemma E4B가 설치되어 있지 않아. LM Studio에서 google/gemma-4-e4b를 먼저 설치해줘.')
    instances = selected.get('loaded_instances') or []
    for instance in instances:
        if instance.get('id') == MODEL:
            actual = instance.get('config', {}).get('context_length', 0)
            if isinstance(actual, int) and actual >= context_length:
                return {'status': 'ready', 'model': MODEL, 'context_length': actual}
            raise RuntimeError('E4B 컨텍스트가 부족해. LM Studio에서 최소 %s 토큰으로 다시 로드해줘. 사용 중인 모델은 자동으로 해제하지 않았어.' % context_length)
    if instances:
        raise RuntimeError('E4B가 다른 식별자로 로드되어 있어. LM Studio에서 식별자를 %s로 설정해줘. 기존 세션은 유지했어.' % MODEL)
    return None


def is_managed(settings):
    # Provider compatibility is independent of the startup preference.
    return (settings.get('api_type') == 'openai'
            and settings.get('gemma_url', '').rstrip('/') == BASE_URL
            and settings.get('gemma_model') == MODEL)


def ensure_ready(settings):
    """Return ready/disabled, or a bounded actionable error. No settings are written."""
    global _cached, _cached_at, _last_error, _failed_at
    if settings.get('lmstudio_auto_start') is not True or not is_managed(settings):
        return {'status': 'disabled'}
    context_length = settings.get('lmstudio_context_length', 16384)
    parallel = settings.get('lmstudio_parallel', 1)
    if type(context_length) is not int or not 16384 <= context_length <= 32768:
        raise RuntimeError('lmstudio_context_length는 16384~32768 범위의 정수여야 해.')
    if type(parallel) is not int or not 1 <= parallel <= 4:
        raise RuntimeError('lmstudio_parallel은 1~4 범위의 정수여야 해.')
    if not _lock.acquire(timeout=330):
        raise RuntimeError('LM Studio 준비를 기다리는 시간이 초과됐어. LM Studio 상태를 확인하고 다시 요청해줘.')
    try:
        now = time.monotonic()
        if _cached and now - _cached_at < 3 and _cached['context_length'] >= context_length:
            return dict(_cached)
        if _last_error and now - _failed_at < 5:
            raise RuntimeError(_last_error)
        try:
            with _process_lock():
                cli = Path.home() / '.lmstudio' / 'bin' / 'lms.exe'
                if not cli.is_file():
                    found = shutil.which('lms')
                    cli = Path(found) if found else None
                try:
                    models = _models()
                except (urllib.error.URLError, TimeoutError, OSError):
                    if cli is None:
                        raise RuntimeError('LM Studio CLI를 찾지 못했어. LM Studio를 열고 127.0.0.1:1234 로컬 서버를 켜줘.')
                    _run(cli, ['daemon', 'up'], 75)
                    _run(cli, ['server', 'start', '--port', '1234', '--bind', '127.0.0.1'], 30)
                    models = _models()
                ready = _ready_model(models, context_length)
                if ready is None:
                    if cli is None:
                        raise RuntimeError('LM Studio CLI를 찾지 못했어. LM Studio에서 Gemma E4B를 먼저 로드해줘.')
                    _run(cli, ['load', MODEL, '--context-length', str(context_length),
                               '--parallel', str(parallel), '--identifier', MODEL, '--yes'], 180)
                    ready = _ready_model(_models(), context_length)
                    if ready is None:
                        raise RuntimeError('선택한 E4B가 로드된 것을 확인하지 못했어. LM Studio 상태를 확인해줘.')
            _cached, _cached_at = ready, time.monotonic()
            _last_error = ''
            return dict(ready)
        except Exception as error:
            _cached = None
            _last_error, _failed_at = str(error), time.monotonic()
            raise RuntimeError('로컬 E4B가 준비되지 않았어: ' + str(error)) from error
    finally:
        _lock.release()


if __name__ == '__main__':
    try:
        settings_path = Path(__file__).resolve().with_name('settings.json')
        settings = json.loads(settings_path.read_text(encoding='utf-8-sig')) if settings_path.exists() else {}
        print(json.dumps(ensure_ready(settings), ensure_ascii=False), flush=True)
    except Exception as error:
        print('AI OFFICE 로컬 모델 준비 실패: ' + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
