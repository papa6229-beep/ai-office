"""Read-only Hanbiro window classification; no app input or external network calls."""
import ctypes
from ctypes import wintypes as w
from http.client import HTTPConnection
import json
import sys

from messenger_native_window import installed_hanbiro, presentation_settings


def _main_renderer_title(port):
    connection = HTTPConnection('127.0.0.1', port, timeout=1.5)
    try:
        connection.request('GET', '/json/list')
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Hanbiro target metadata is unavailable')
        targets = json.loads(response.read(512 * 1024))
    finally:
        connection.close()

    main = []
    for target in targets:
        if target.get('type') != 'page':
            continue
        url = target.get('url', '').replace(chr(92), '/')
        if '/hanbiro-talk/resources/app.asar/dist/renderer/index.html' in url.lower() and '#/main' in url:
            main.append(target.get('title', '').strip())
    if len(main) != 1 or not main[0]:
        raise ValueError('Main renderer is ambiguous')
    return main[0]


def inspect(pids, main_title=None, *, visible_only=False):
    import messenger_attachment_worker as cache
    _,_,data=cache._storage()
    members=data.get('recoil-atomAllMembers',{}).get('members',[])
    rooms=data.get('recoil-atomAllRooms',{}).get('rooms',{})
    names=set()
    def labels(value):
        if not isinstance(value,dict):return
        for key in ('name','fullname','nickname','shortname','userAlias','alias','aliasPrivate','aliasPublic'):
            text=value.get(key)
            if isinstance(text,str) and text.strip():names.add(text.strip())
    for row in cache._values(members):labels(row)
    for row in cache._values(rooms):
        labels(row)
        if isinstance(row,dict):
            for member in cache._values(row.get('members',{})):labels(member)
    if not names or not rooms:raise ValueError('No current room metadata')
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    u.GetWindowLongPtrW.argtypes=[w.HWND,ctypes.c_int];u.GetWindowLongPtrW.restype=ctypes.c_ssize_t
    u.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    u.IsIconic.argtypes=[w.HWND]
    u.IsWindowVisible.argtypes=[w.HWND]
    u.GetWindow.argtypes=[w.HWND,w.UINT];u.GetWindow.restype=w.HWND
    class Placement(ctypes.Structure):
        _fields_=[('length',w.UINT),('flags',w.UINT),('show',w.UINT),('minimum',w.POINT),('maximum',w.POINT),('normal',w.RECT)]
    u.GetWindowPlacement.argtypes=[w.HWND,ctypes.POINTER(Placement)]
    candidates=[]
    @ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    def visit(handle,_):
        pid=w.DWORD();u.GetWindowThreadProcessId(handle,ctypes.byref(pid))
        if pid.value not in pids:return True
        if visible_only and (not u.IsWindowVisible(handle) or u.IsIconic(handle) or u.GetWindow(handle,4)):return True
        text=ctypes.create_unicode_buffer(512);cls=ctypes.create_unicode_buffer(128)
        u.GetWindowTextW(handle,text,len(text));u.GetClassNameW(handle,cls,len(cls))
        if cls.value!='Chrome_WidgetWin_1' or not text.value.strip() or text.value.strip() in names:return True
        if u.GetWindowLongPtrW(handle,-16)&0x40000000:return True
        rect=w.RECT();u.GetWindowRect(handle,ctypes.byref(rect))
        if u.IsIconic(handle):
            placement=Placement();placement.length=ctypes.sizeof(placement)
            if not u.GetWindowPlacement(handle,ctypes.byref(placement)):return True
            rect=placement.normal
        if rect.right-rect.left<200 or rect.bottom-rect.top<140:return True
        candidates.append({'handle':int(handle),'pid':pid.value,'title':text.value})
        return True
    u.EnumWindows.argtypes=[type(visit),w.LPARAM]
    u.EnumWindows(visit,0)
    if len(candidates)>1 and main_title:
        # An auxiliary window can survive hidden after leaving a conversation.
        # Match Hanbiro's main renderer, since the roster itself can be hidden.
        candidates = [row for row in candidates if row['title'].strip() == main_title]
    # Unknown/duplicate windows never become a guessed conversation or main.
    if len(candidates)!=1:raise ValueError('Main window is ambiguous')
    return dict(ok=True,**candidates[0])

def target(value):
    pids=value.get('pids')
    if not isinstance(pids,list) or not 1<=len(pids)<=32 or any(type(p) is not int or p<=0 for p in pids):raise ValueError('Invalid process scope')
    if value.get('allowNativeVisible') is True and installed_hanbiro()[1]=='2.1.283':
        return dict(inspect(set(pids), visible_only=True), native_visible=True)
    try:
        _, port=presentation_settings()
    except ValueError as error:
        return {'ok':False,'error':str(error)}
    try:
        main_title=_main_renderer_title(port)
    except (OSError,ValueError):
        return {'ok':False,'error':'한비로톡 PC 원본 창을 열고 통합메신저에서 다시 선택해 주세요.'}
    return inspect(set(pids),main_title)


def main():
    try:
        raw=sys.stdin.read(4097)
        if len(raw)>4096:raise ValueError('Input too long')
        result=target(json.loads(raw))
    except Exception:
        result={'ok':False,'error':'한비로 대화 목록창을 하나로 확인하지 못했어요. 한비로 목록을 한 번 연 뒤 다시 선택해 주세요.'}
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':main()
