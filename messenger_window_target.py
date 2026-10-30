"""Read-only Hanbiro window classification; no app input or network calls."""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import sys

def inspect(pids):
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
    class Placement(ctypes.Structure):
        _fields_=[('length',w.UINT),('flags',w.UINT),('show',w.UINT),('minimum',w.POINT),('maximum',w.POINT),('normal',w.RECT)]
    u.GetWindowPlacement.argtypes=[w.HWND,ctypes.POINTER(Placement)]
    candidates=[]
    @ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    def visit(handle,_):
        pid=w.DWORD();u.GetWindowThreadProcessId(handle,ctypes.byref(pid))
        if pid.value not in pids:return True
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
    # Unknown/duplicate windows never become a guessed conversation or main.
    if len(candidates)!=1:raise ValueError('Main window is ambiguous')
    return dict(ok=True,**candidates[0])

def main():
    try:
        raw=sys.stdin.read(4097)
        if len(raw)>4096:raise ValueError('Input too long')
        value=json.loads(raw);pids=value.get('pids')
        if not isinstance(pids,list) or not 1<=len(pids)<=32 or any(type(p) is not int or p<=0 for p in pids):raise ValueError('Invalid process scope')
        result=inspect(set(pids))
    except Exception:
        result={'ok':False,'error':'한비로 대화 목록창을 하나로 확인하지 못했어요. 한비로 목록을 한 번 연 뒤 다시 선택해 주세요.'}
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':main()
