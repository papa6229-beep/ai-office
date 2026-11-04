"""Connect to the privately provisioned NateOn Android device on demand."""
from contextlib import contextmanager
from pathlib import Path
import os
import socket
import subprocess
import time

import task_control


ROOT = Path(__file__).resolve().parent / '.provider_runtime' / 'nateon-avd'
AVD = 'AemeathNateOnProbe'
SERIAL = 'emulator-5580'
PORTS = (5580, 5581)
PACKAGE = 'Uxpp.UC'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


@contextmanager
def _startup_lock(deadline):
    import msvcrt

    with (ROOT / 'startup.lock').open('a+b') as lock:
        while True:
            task_control.checkpoint()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise ValueError('네이트온 가상기기 준비가 아직 끝나지 않았어요.') from error
                time.sleep(0.5)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except OSError:
        return False


def connect():
    """Reuse or start the private guest. Never start an Office-wide service."""
    if os.name != 'nt':
        raise ValueError('네이트온 가상기기는 준비된 Windows PC에서 연결할 수 있어요.')
    sdk = ROOT / 'sdk'
    adb = sdk / 'platform-tools' / 'adb.exe'
    emulator = sdk / 'emulator' / 'emulator.exe'
    if not all(path.is_file() for path in (adb, emulator, ROOT / 'avd' / (AVD + '.ini'))):
        raise ValueError('이 PC에 네이트온 전용 Android 기기가 준비되어 있지 않아요.')
    env = dict(os.environ, ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk),
               ANDROID_USER_HOME=str(ROOT / 'android-home'), ANDROID_AVD_HOME=str(ROOT / 'avd'))

    def read(*args):
        task_control.checkpoint()
        try:
            result = subprocess.run([str(adb), '-s', SERIAL, *args], env=env, capture_output=True,
                                    timeout=5, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return ''
        return result.stdout.decode('utf-8', errors='replace').strip() if result.returncode == 0 else ''

    started_at = time.monotonic()
    deadline = started_at + 100
    process = None
    try:
        with _startup_lock(deadline):
            if read('get-state') != 'device' and not any(_port_open(port) for port in PORTS):
                with (ROOT / 'office-emulator.log').open('ab') as log:
                    process = subprocess.Popen(
                        [str(emulator), '-avd', AVD, '-port', str(PORTS[0]), '-no-window',
                         '-no-audio', '-no-boot-anim', '-gpu', 'swiftshader', '-no-snapshot-load'],
                        env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=NO_WINDOW)
            while time.monotonic() < deadline:
                if read('get-state') == 'device':
                    identity = read('shell', 'getprop', 'ro.boot.qemu.avd_name')
                    if identity and identity != AVD:
                        raise ValueError('네이트온 전용 기기와 다른 Android가 연결돼 있어요.')
                    if identity == AVD and read('shell', 'getprop', 'sys.boot_completed') == '1':
                        if not read('shell', 'pm', 'path', PACKAGE).startswith('package:'):
                            raise ValueError('가상기기에 설치된 네이트온 앱을 확인하지 못했어요.')
                        return {'state': 'ready', 'serial': SERIAL, 'started': process is not None,
                                'elapsed_ms': round((time.monotonic() - started_at) * 1000),
                                'login_verified': False, 'sent': False}
                if process is not None and process.poll() is not None:
                    raise ValueError('네이트온 가상기기가 부팅 전에 종료됐어요.')
                time.sleep(0.5)
    except OSError as error:
        raise ValueError('네이트온 전용 Android 실행 경로에 접근하지 못했어요.') from error
    raise ValueError('네이트온 가상기기의 부팅을 확인하지 못했어요.')


if __name__ == '__main__':
    import json

    try:
        print(json.dumps(connect(), ensure_ascii=False))
    except ValueError as error:
        raise SystemExit(str(error)) from None
