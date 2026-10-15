"""Read-only Windows Start app inventory and explicit registered-app activation."""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import threading
import time

from desktop_workspace import _safe_route

MAX_APPS = 5000
_TTL = 120
_LOCK = threading.RLock()
_cache = None
_SCRIPT = "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); @(Get-StartApps | Select-Object Name,AppID) | ConvertTo-Json -Compress"


def clear_cache():
    global _cache
    with _LOCK:
        _cache = None


def _windows_file(relative):
    root = pathlib.Path(os.environ.get('SystemRoot', r'C:\Windows'))
    path = root / relative
    if not root.is_absolute() or str(root).startswith(('\\\\', '//')) or not path.is_file() or not _safe_route(path):
        raise OSError('Windows component unavailable')
    return str(path)


def _explorer():
    return _windows_file('explorer.exe')


def _read_apps():
    if os.name != 'nt':
        raise OSError('Windows required')
    command = _windows_file(pathlib.Path('System32') / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')
    result = subprocess.run([command, '-NoProfile', '-NonInteractive', '-Command', _SCRIPT],
                            capture_output=True, timeout=15, check=True, shell=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError('Inventory too large')
    rows = json.loads(result.stdout.decode('utf-8-sig'))
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise ValueError('Invalid inventory')
    return rows


def _valid_app_id(value):
    # These are identifiers from Get-StartApps, never commands or client-provided AUMIDs.
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in ('/', '"', '<', '>', '|'))):
        return False
    if '\\' not in value and ':' not in value:
        return True
    # Win32 Start entries also use known-folder GUID paths or drive-qualified paths.
    # Keep those exact registered IDs, while rejecting URI namespaces and traversal.
    if not re.match(r'^(?:\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}|[A-Za-z]:)\\', value):
        return False
    remainder = value.split('\\', 1)[1]
    return bool(remainder) and ':' not in remainder and all(part and part not in {'.', '..'} for part in remainder.split('\\'))


def catalog(refresh=False):
    global _cache
    with _LOCK:
        if not refresh and _cache and time.monotonic() - _cache[0] < _TTL:
            return json.loads(json.dumps(_cache[1]))
        result = {'available': False, 'source': 'windows-start-menu', 'items': [], 'warnings': [], 'total': 0, 'truncated': False, 'filtered': 0}
        identifiers = {}
        try:
            rows = _read_apps()
            if not isinstance(rows, list):
                raise ValueError()
            result['truncated'] = len(rows) > MAX_APPS
            for row in rows[:MAX_APPS]:
                if not isinstance(row, dict):
                    continue
                name, app_id = row.get('Name'), row.get('AppID')
                if not isinstance(name, str) or not name.strip() or len(name) > 512 or any(ord(c) < 32 for c in name) or not _valid_app_id(app_id):
                    result['filtered'] += 1
                    continue
                target = 'startapp:' + hashlib.sha256(app_id.encode('utf-8')).hexdigest()
                if target in identifiers:
                    continue
                identifiers[target] = app_id
                result['items'].append({'title': name.strip()[:120], 'kind': 'app', 'target': target, 'icon': '🚀'})
            result['items'].sort(key=lambda i: (i['title'].casefold(), i['target']))
            result['available'] = True
            result['total'] = len(result['items'])
            if result['truncated']:
                result['warnings'].append('시작 메뉴 앱은 최대 5,000개까지 불러옵니다.')
            if result['filtered']:
                result['warnings'].append('웹 주소 또는 지원하지 않는 시작 메뉴 항목 ' + str(result['filtered']) + '개를 제외했습니다.')
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            result['warnings'].append('Windows 시작 메뉴 앱 목록을 읽지 못해 로컬 바로가기를 표시합니다.')
        _cache = (time.monotonic(), result, identifiers)
        return json.loads(json.dumps(result))


def resolve(target, refresh=False):
    if not isinstance(target, str) or not re.fullmatch(r'startapp:[0-9a-f]{64}', target):
        raise ValueError('시작 메뉴에서 프로그램을 다시 선택하세요.')
    with _LOCK:
        catalog(refresh=refresh)
        app_id = _cache[2].get(target)
        if app_id is None:
            raise ValueError('시작 메뉴에서 찾을 수 없는 프로그램입니다. 목록을 새로고침하세요.')
        return app_id


def launch(target):
    app_id = resolve(target, refresh=True)
    subprocess.Popen([_explorer(), 'shell:AppsFolder\\' + app_id], shell=False,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
