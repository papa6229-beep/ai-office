"""Bounded Windows UI Automation, with native objects owned by one MTA thread.

Only observed, expiring references may be acted on. This module never accepts
coordinates, selectors, shell commands, or keyboard sequences from its caller.
"""
import atexit
import datetime
import hashlib
import importlib.util
import itertools
import os
import queue
import re
import sys
import threading
import time
import uuid

from browser_workspace import _redact

MAX_WINDOWS = 60
MAX_CONTROLS = 180
MAX_DEPTH = 8
MAX_TEXT = 12000
OPERATIONS = frozenset({'focus', 'invoke', 'select', 'expand', 'collapse', 'toggle', 'set_value', 'scroll'})
_BLOCKED_PROCESS = re.compile(r'(?i)^(?:cmd|powershell|pwsh|windowsterminal|wt|conhost|openconsole|regedit|regedt32|taskmgr|mmc|consent|credentialuibroker|logonui|lsass|secpol|wscript|cscript|keepass(?:xc)?|1password|bitwarden|lastpass|dashlane)(?:\.exe)?$')
_BLOCKED_TITLE = re.compile(r'(?i)(?:\b(?:windows powershell|command prompt|administrator:|windows security|user account control|credential|password manager|sign[ -]?in|log[ -]?in|authentication)\b|명령 프롬프트|관리자:|Windows 보안|사용자 계정 컨트롤|비밀번호 관리자|로그인|본인 인증|자격 증명|실행\s*$)')
_SENSITIVE_LABEL = re.compile(r'(?i)(?:\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|password|passwd|secret|authorization|credential)\b|비밀번호|암호|인증\s*키)')
_MESSAGES = {
    'dependency': 'Windows 화면 연결 모듈이 설치되지 않았습니다.',
    'unavailable': 'Windows 화면 연결을 사용할 수 없습니다.',
    'stale': '화면이 변경되었거나 참조가 만료되었습니다. 화면을 다시 확인하세요.',
    'denied': '보호된 앱이나 관리자 권한 창에는 접근하지 않습니다.',
    'unsupported': '이 화면 요소는 요청한 UI Automation 동작을 지원하지 않습니다.',
    'invalid': '관찰한 창과 요소, 지원하는 동작을 지정하세요.',
    'busy': 'Windows 작업이 처리 중입니다. 잠시 후 다시 시도하세요.',
    'uncertain': '화면 작업의 완료 여부를 확인하지 못했습니다. 다시 실행하기 전에 화면을 확인하세요.',
    'timeout': '대기 중인 화면 작업을 취소했습니다. 화면을 다시 확인하세요.',
}


class WindowsWorkspaceError(ValueError):
    def __init__(self, code='unavailable'):
        self.code = code if code in _MESSAGES else 'unavailable'
        super().__init__(_MESSAGES[self.code])


def available():
    return sys.platform == 'win32' and importlib.util.find_spec('pywinauto') is not None


def capabilities():
    return {'available': available(), 'backend': 'windows_uia', 'operations': sorted(OPERATIONS),
            'scope': '관찰한 일반 Windows 앱의 UI Automation 요소',
            'screenshots': False, 'keyboard_or_coordinates': False,
            'protected_windows': False, 'verification_required': True}


def _new_id(kind):
    return kind + '_' + uuid.uuid4().hex


def _access_denied(error):
    hresult = getattr(error, 'hresult', None)
    return isinstance(error, PermissionError) or getattr(error, 'winerror', None) == 5 or (isinstance(hresult, int) and hresult & 0xffffffff == 0x80070005)


def _window_identity(info):
    return (info.get('handle'), info.get('pid'), info.get('process'), info.get('started_at'))


def _fingerprint(info):
    runtime = info.get('runtime_id')
    return (tuple(runtime) if runtime else None, str(info.get('name', '')),
            str(info.get('control_type', '')), bool(info.get('password')),
            tuple(sorted(set(info.get('patterns', [])) & OPERATIONS)), info.get('process_id'),
            info.get('_value_digest', info.get('value')), info.get('toggle_state'), info.get('selected'), info.get('expanded'))


def _sensitive(info):
    return bool(info.get('password')) or bool(_SENSITIVE_LABEL.search(str(info.get('name', ''))))


def _context_fingerprint(collected):
    # The same button can survive switching documents or accounts. Bind all
    # observed context, not merely that button. Keyboard focus is deliberately
    # absent: approving in AI OFFICE necessarily changes foreground focus.
    return tuple((_fingerprint(info), bool(info.get('enabled')),
                  hashlib.sha256(str(info.get('text', '')).encode('utf-8')).hexdigest())
                 for _, info in collected)


def _safe_window(info):
    if info.get('elevated') is not False or not info.get('pid') or not info.get('handle'):
        raise WindowsWorkspaceError('denied')
    title = str(info.get('title', ''))
    if _BLOCKED_PROCESS.search(str(info.get('process', ''))) or _BLOCKED_TITLE.search(title) or title.strip().casefold() == 'run':
        raise WindowsWorkspaceError('denied')


class WindowsWorkspace:
    def __init__(self, backend=None, timeout=30, snapshot_ttl=90):
        self._backend = backend
        self._timeout = timeout
        self._snapshot_ttl = snapshot_ttl
        self._queue = queue.Queue(maxsize=16)
        self._windows = {}
        self._snapshots = {}
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, name='ai-office-uia', daemon=True)
        self._thread.start()

    def windows(self, query=''):
        if not isinstance(query, str) or len(query) > 300:
            raise WindowsWorkspaceError('invalid')
        return self._request('windows', query)

    def inspect(self, window_id):
        if not isinstance(window_id, str):
            raise WindowsWorkspaceError('invalid')
        return self._request('inspect', window_id)

    def act(self, window_id, control_id, snapshot_id, operation, value=None):
        if not all(isinstance(x, str) for x in (window_id, control_id, snapshot_id, operation)) or operation not in OPERATIONS:
            raise WindowsWorkspaceError('invalid')
        if operation == 'set_value':
            if not isinstance(value, str) or len(value) > 4000 or '\x00' in value:
                raise WindowsWorkspaceError('invalid')
        elif operation == 'scroll':
            if value not in ('up', 'down'):
                raise WindowsWorkspaceError('invalid')
        elif value is not None:
            raise WindowsWorkspaceError('invalid')
        return self._request('act', window_id, control_id, snapshot_id, operation, value)

    def _request(self, method, *args):
        job = {'method': method, 'args': args, 'event': threading.Event(), 'started': False, 'cancelled': False}
        with self._lock:
            if self._closed:
                raise WindowsWorkspaceError('unavailable')
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                raise WindowsWorkspaceError('busy') from None
        if not job['event'].wait(self._timeout):
            with self._lock:
                if not job['event'].is_set():
                    job['cancelled'] = True
                    raise WindowsWorkspaceError('uncertain' if job['started'] else 'timeout')
        if 'error' in job:
            raise job['error']
        return job.get('result')

    def _worker(self):
        try:
            while True:
                job = self._queue.get()
                if job is None:
                    break
                with self._lock:
                    if job['cancelled'] or self._closed:
                        job['error'] = WindowsWorkspaceError('timeout')
                        job['event'].set()
                        continue
                    job['started'] = True
                try:
                    if self._backend is None:
                        self._backend = _PywinautoBackend()
                    job['result'] = getattr(self, '_do_' + job['method'])(*job['args'])
                except WindowsWorkspaceError as error:
                    job['error'] = error
                except (PermissionError, OSError) as error:
                    job['error'] = WindowsWorkspaceError('denied' if _access_denied(error) else 'unavailable')
                except Exception as error:
                    job['error'] = WindowsWorkspaceError('denied' if _access_denied(error) else 'unavailable')
                finally:
                    job['event'].set()
        finally:
            if self._backend is not None and hasattr(self._backend, 'close'):
                try:
                    self._backend.close()
                except Exception:
                    pass

    def _enumerate(self):
        # Backend performs a bounded top-level enumeration before yielding.
        return list(self._backend.windows())[:MAX_WINDOWS]

    def _do_windows(self, query):
        result = []
        seen = set()
        for window in self._enumerate():
            try:
                info = self._backend.describe_window(window)
                _safe_window(info)
            except Exception:
                continue
            identity = _window_identity(info)
            seen.add(identity)
            existing = next((key for key, saved in self._windows.items() if saved == identity), None)
            window_id = existing or _new_id('win')
            self._windows[window_id] = identity
            if query.casefold() not in (str(info.get('title', '')) + ' ' + str(info.get('process', ''))).casefold():
                continue
            result.append({'window_id': window_id, 'title': _redact(info.get('title', ''))[:500],
                           'process': str(info.get('process', ''))[:120]})
        self._windows = {key: identity for key, identity in self._windows.items() if identity in seen}
        self._snapshots = {key: snapshot for key, snapshot in self._snapshots.items() if key in self._windows}
        return result

    def _resolve_window(self, window_id):
        identity = self._windows.get(window_id)
        if identity is None:
            raise WindowsWorkspaceError('stale')
        for window in self._enumerate():
            try:
                info = self._backend.describe_window(window)
            except Exception:
                continue
            if _window_identity(info) == identity:
                _safe_window(info)
                return window, info
        self._windows.pop(window_id, None)
        self._snapshots.pop(window_id, None)
        raise WindowsWorkspaceError('stale')

    def _collect(self, window):
        controls = []
        for raw in self._backend.controls(window, MAX_CONTROLS + 1, MAX_DEPTH):
            if len(controls) >= MAX_CONTROLS + 1:
                break
            try:
                info = self._backend.describe_control(raw)
                controls.append((raw, info))
            except Exception:
                continue
        return controls

    def _do_inspect(self, window_id):
        window, info = self._resolve_window(window_id)
        collected = self._collect(window)
        if not collected:
            raise WindowsWorkspaceError('unsupported')
        control_map, public, parts = {}, [], []
        runtime_ids = [_fingerprint(item)[0] for _, item in collected]
        for raw, item in collected[:MAX_CONTROLS]:
            control_id = _new_id('ctl')
            password = bool(item.get('password'))
            sensitive = _sensitive(item)
            fingerprint = _fingerprint(item)
            patterns = sorted(set(item.get('patterns', [])) & OPERATIONS)
            # Controls without a unique runtime ID can be read, never acted on.
            if sensitive or not fingerprint[0] or runtime_ids.count(fingerprint[0]) != 1:
                patterns = []
            entry = {'id': control_id, 'name': '[비밀번호 가림]' if password else _redact(item.get('name', ''))[:500],
                     'control_type': str(item.get('control_type', ''))[:80], 'enabled': bool(item.get('enabled')),
                     'password': password, 'patterns': patterns}
            for field in ('value', 'text', 'toggle_state', 'selected', 'expanded'):
                if not sensitive and field in item:
                    limit = MAX_TEXT if field == 'text' else 1000
                    entry[field] = _redact(item[field])[:limit] if isinstance(item[field], str) else item[field]
            public.append(entry)
            parts.append(entry['name'] + (' ' + str(entry.get('value', '')) if 'value' in entry else '')
                         + (' ' + entry['text'] if 'text' in entry else ''))
            control_map[control_id] = {'fingerprint': fingerprint, 'patterns': patterns}
        text = '\n'.join(parts)
        snapshot_id = _new_id('snap')
        self._snapshots[window_id] = {'id': snapshot_id, 'created': time.monotonic(), 'controls': control_map,
                                      'identity': _window_identity(info), 'title': str(info.get('title', '')),
                                      'context': _context_fingerprint(collected)}
        return {'window_id': window_id, 'title': _redact(info.get('title', ''))[:500],
                'process': str(info.get('process', ''))[:120], 'snapshot_id': snapshot_id,
                'controls': public, 'text': text[:MAX_TEXT],
                'truncated': len(collected) > MAX_CONTROLS or len(text) > MAX_TEXT,
                'scope': 'UI Automation 텍스트와 요소; 이미지와 비표준 UI는 읽지 못할 수 있습니다.',
                'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}

    def _do_act(self, window_id, control_id, snapshot_id, operation, value):
        snapshot = self._snapshots.get(window_id)
        if not snapshot or snapshot['id'] != snapshot_id or time.monotonic() - snapshot['created'] > self._snapshot_ttl:
            raise WindowsWorkspaceError('stale')
        saved = snapshot['controls'].get(control_id)
        if not saved:
            raise WindowsWorkspaceError('stale')
        if operation not in saved['patterns']:
            raise WindowsWorkspaceError('unsupported')
        window, window_info = self._resolve_window(window_id)
        if (_window_identity(window_info) != snapshot['identity']
                or str(window_info.get('title', '')) != snapshot['title']):
            raise WindowsWorkspaceError('stale')
        collected = self._collect(window)
        if _context_fingerprint(collected) != snapshot['context']:
            raise WindowsWorkspaceError('stale')
        matches = [(raw, info) for raw, info in collected if _fingerprint(info) == saved['fingerprint']]
        if len(matches) != 1:
            raise WindowsWorkspaceError('stale')
        raw, before = matches[0]
        if _sensitive(before) or not before.get('enabled') or operation not in before.get('patterns', []):
            raise WindowsWorkspaceError('unsupported')
        # Consume before effect, including uncertain failures, so retries cannot
        # blindly repeat an Invoke with the same snapshot.
        self._snapshots.pop(window_id, None)
        try:
            self._backend.perform(raw, operation, value)
        except PermissionError:
            raise WindowsWorkspaceError('denied') from None
        except Exception as error:
            raise WindowsWorkspaceError('denied' if _access_denied(error) else 'uncertain') from None
        verification = {'status': 'observed_after_dispatch', 'goal_verified': False,
                        'note': '동작을 전달했습니다. 업무 완료 여부는 새 화면의 근거로 판단하세요.'}
        try:
            observation = self._do_inspect(window_id)
            current = self._collect(self._resolve_window(window_id)[0])
            same = [info for _, info in current if info.get('runtime_id') == before.get('runtime_id') and info.get('control_type') == before.get('control_type')]
            if len(same) == 1:
                after = same[0]
                field, expected = {'set_value': ('value', value), 'select': ('selected', True),
                                   'expand': ('expanded', True), 'collapse': ('expanded', False)}.get(operation, (None, None))
                if operation == 'toggle' and before.get('toggle_state') in (0, 1) and after.get('toggle_state') in (0, 1):
                    field, expected = 'toggle_state', 1 - before['toggle_state']
                if field and field in after:
                    matched = after[field] == expected
                    if field == 'value' and '_value_digest' in after:
                        matched = after['_value_digest'] == hashlib.sha256(expected.encode('utf-8')).hexdigest()
                    verification = {'status': 'readback_verified' if matched else 'readback_mismatch',
                                    'goal_verified': matched,
                                    'field': field, 'note': '관찰한 요소의 상태를 다시 읽어 비교했습니다.'}
        except Exception:
            observation = None
            verification = {'status': 'observation_unavailable', 'goal_verified': False,
                            'note': '동작은 전달했지만 창이 닫히거나 바뀌어 결과를 확인하지 못했습니다.'}
        return {'action_dispatched': True, 'operation': operation, 'observation': observation, 'verification': verification}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                # Drain queued requests, never interrupt an already started COM call.
                while True:
                    try:
                        job = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if job:
                        job['error'] = WindowsWorkspaceError('timeout')
                        job['event'].set()
                self._queue.put_nowait(None)
        self._thread.join(timeout=2)


class _PywinautoBackend:
    """All methods, including COM initialization and release, run on the owner."""
    def __init__(self):
        if not available():
            raise WindowsWorkspaceError('dependency')
        self._com = None
        try:
            # pywinauto follows the official sys.coinit_flags MTA convention.
            # Explicitly initialize this dedicated thread as well: importing a
            # cached comtypes module does not initialize a new thread.
            if not hasattr(sys, 'coinit_flags'):
                sys.coinit_flags = 0
            import comtypes
            comtypes.CoInitializeEx(0)
            self._com = comtypes
            from pywinauto import Desktop
            from pywinauto.controls.uiawrapper import UIAWrapper
            self._desktop = Desktop(backend='uia')
            self._wrapper_class = UIAWrapper
        except Exception:
            self.close()
            raise WindowsWorkspaceError('unavailable') from None

    def windows(self):
        # Win32 metadata cannot invoke an unrelated UIA provider. Do not create
        # wrappers for every app just to discover the requested window.
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        handles = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        def collect(handle, parameter):
            if user32.IsWindowVisible(handle):
                handles.append(handle)
            return len(handles) < MAX_WINDOWS
        callback = callback_type(collect)
        user32.EnumWindows(callback, 0)
        result = []
        for handle in handles:
            try:
                title = ctypes.create_unicode_buffer(min(user32.GetWindowTextLengthW(handle) + 1, 4096))
                user32.GetWindowTextW(handle, title, len(title))
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
                process, elevated, started_at = _process_info(pid.value)
                result.append({'title': title.value, 'process': process, 'pid': pid.value,
                               'handle': handle, 'elevated': elevated, 'started_at': started_at})
            except Exception:
                continue
        return result

    def describe_window(self, window):
        return dict(window)

    def controls(self, window, max_controls, max_depth):
        wrapper = self._desktop.window(handle=window['handle']).wrapper_object()
        pending = [(wrapper, 0)]
        result = []
        while pending and len(result) < max_controls:
            node, depth = pending.pop(0)
            result.append(node)
            if depth >= max_depth:
                continue
            try:
                remaining = max_controls - len(result) - len(pending)
                if remaining > 0:
                    # iter_children uses a TreeWalker; children() materializes
                    # an unbounded FindAll array before Python can slice it.
                    pending.extend((self._wrapper_class(child), depth + 1)
                                   for child in itertools.islice(node.element_info.iter_children(), remaining))
            except Exception:
                continue
        return result

    def describe_control(self, control):
        info = control.element_info
        password = bool(info.element.CurrentIsPassword)
        result = {'runtime_id': tuple(info.runtime_id or ()), 'name': '' if password else info.name or '', 'process_id': info.process_id,
                  'control_type': info.control_type, 'enabled': bool(info.enabled), 'password': password, 'patterns': []}
        if _sensitive(result):
            return result
        patterns = []
        try:
            if info.element.CurrentIsKeyboardFocusable:
                patterns.append('focus')
        except Exception:
            pass
        for attribute, operations in [('iface_invoke', ['invoke']), ('iface_selection_item', ['select']),
                                      ('iface_expand_collapse', ['expand', 'collapse']), ('iface_toggle', ['toggle']),
                                      ('iface_value', ['set_value']), ('iface_scroll', ['scroll'])]:
            try:
                interface = getattr(control, attribute)
                if attribute == 'iface_value':
                    current_value = str(interface.CurrentValue)
                    result['_value_digest'] = hashlib.sha256(current_value.encode('utf-8')).hexdigest()
                    result['value'] = current_value[:1000]
                    if interface.CurrentIsReadOnly:
                        operations = []
                elif attribute == 'iface_toggle':
                    result['toggle_state'] = int(interface.CurrentToggleState)
                elif attribute == 'iface_selection_item':
                    result['selected'] = bool(interface.CurrentIsSelected)
                elif attribute == 'iface_expand_collapse':
                    state = int(interface.CurrentExpandCollapseState)
                    result['expanded'] = state in (1, 2)
                    if state == 3:  # LeafNode
                        operations = []
                elif attribute == 'iface_scroll' and not interface.CurrentVerticallyScrollable:
                    operations = []
                patterns.extend(operations)
            except Exception:
                continue
        result['patterns'] = patterns
        if info.control_type in ('Document', 'Edit', 'Text'):
            try:
                result['text'] = str(control.iface_text.DocumentRange.GetText(MAX_TEXT))[:MAX_TEXT]
            except Exception:
                pass
        return result

    def perform(self, control, operation, value):
        if operation == 'focus':
            control.element_info.element.SetFocus()
        elif operation == 'invoke':
            control.iface_invoke.Invoke()
        elif operation == 'select':
            control.iface_selection_item.Select()
        elif operation == 'expand':
            control.iface_expand_collapse.Expand()
        elif operation == 'collapse':
            control.iface_expand_collapse.Collapse()
        elif operation == 'toggle':
            control.iface_toggle.Toggle()
        elif operation == 'set_value':
            control.iface_value.SetValue(value)
        elif operation == 'scroll':
            control.iface_scroll.Scroll(2, 0 if value == 'up' else 3)
        else:
            raise WindowsWorkspaceError('unsupported')

    def close(self):
        self._desktop = None
        if self._com is not None:
            self._com.CoUninitialize()
            self._com = None


def _process_info(pid):
    """Read executable identity and elevation without exposing its disk path."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        raise PermissionError()
    token = wintypes.HANDLE()
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
            raise PermissionError()
        if not advapi.OpenProcessToken(process, 8, ctypes.byref(token)):
            raise PermissionError()
        elevation, returned = wintypes.DWORD(), wintypes.DWORD()
        if not advapi.GetTokenInformation(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(returned)):
            raise PermissionError()
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(process, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise PermissionError()
        birth = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return os.path.basename(buffer.value), bool(elevation.value), birth
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def manager():
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None or _MANAGER._closed:
            _MANAGER = WindowsWorkspace()
            atexit.register(_MANAGER.close)
        return _MANAGER
