"""Prepare the installed Phone Link app once when AI OFFICE starts."""
import ctypes
from ctypes import wintypes
import hashlib
import os
import time

APP_ID='Microsoft.YourPhone_8wekyb3d8bbwe!App'
PROCESSES={'phoneexperiencehost.exe','yourphone.exe'}


def windows():
    if os.name!='nt':return []
    from windows_workspace import _process_info
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.IsWindowVisible.argtypes=[wintypes.HWND]
    user32.IsIconic.argtypes=[wintypes.HWND]
    user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    result=[]
    @ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def collect(handle,_):
        if not user32.IsWindowVisible(handle):return True
        title=ctypes.create_unicode_buffer(300)
        user32.GetWindowTextW(handle,title,len(title))
        if title.value not in {'휴대폰과 연결','Phone Link','Your Phone'}:return True
        try:
            pid=wintypes.DWORD();user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
            process,elevated,started=_process_info(pid.value)
            if process.lower() in PROCESSES and not elevated:
                result.append({'handle':handle,'pid':pid.value,'process':process,
                               'minimized':bool(user32.IsIconic(handle))})
        except (OSError,ValueError):pass
        return True
    user32.EnumWindows.argtypes=[type(collect),wintypes.LPARAM]
    user32.EnumWindows(collect,0)
    return result


def ensure_ready(timeout=25):
    """Reuse an existing window. Minimize only the window we just requested."""
    if os.name!='nt':return {'state':'unsupported','message':'Windows에서 사용할 수 있어요.'}
    if windows():return {'state':'already_running','message':'실행 중인 휴대폰과 연결을 사용해요.'}
    try:
        # Reuse the same registered Start-menu activation as the launcher.
        import windows_app_catalog
        windows_app_catalog.launch('startapp:'+hashlib.sha256(APP_ID.encode()).hexdigest())
    except (OSError,ValueError):
        return {'state':'launch_failed','message':'휴대폰과 연결을 자동으로 열지 못했어요.'}
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=windows()
        if current:
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.ShowWindowAsync.argtypes=[wintypes.HWND,ctypes.c_int]
            user32.IsIconic.argtypes=[wintypes.HWND]
            for window in current:
                # SW_SHOWMINNOACTIVE: no pointer, keyboard, or focus activation.
                user32.ShowWindowAsync(window['handle'],7)
            minimized_until=time.monotonic()+3
            while time.monotonic()<minimized_until:
                if all(user32.IsIconic(window['handle']) for window in current):
                    return {'state':'started_minimized','message':'휴대폰과 연결을 자동 실행하고 최소화했어요.'}
                time.sleep(.1)
            return {'state':'started','message':'휴대폰과 연결을 열었어요. 최소화 상태는 확인하지 못했어요.'}
        time.sleep(.25)
    return {'state':'window_unavailable','message':'휴대폰과 연결 시작을 요청했지만 창을 확인하지 못했어요.'}
