"""Read only currently visible notification banners; never focus, click, or type."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import re
import sys
import time

SOURCES=re.compile(r'휴대폰과\s*연결|Phone\s*Link|YourPhone|카카오|Kakao|비트윈|Between|네이트온|Nate\s*On|한비로|Hanbiro|메시지|Messages|문자',re.I)
BANNERS=re.compile(r'새\s*알림|알림\s*창|new\s*notification|toast',re.I)
SHELL={'shellexperiencehost.exe','sihost.exe','explorer.exe'}
MESSENGERS={'yourphone.exe','phoneexperiencehost.exe','kakaotalk.exe','between.exe','nateon.exe','hanbirotalk.exe'}

def emit(kind,**data):
    print(json.dumps(dict(kind=kind,**data),ensure_ascii=False),flush=True)

def run(parent):
    # Reuse the app's bounded COM tree walker on this dedicated owner process.
    import windows_workspace
    backend=windows_workspace._PywinautoBackend()
    user32=ctypes.WinDLL('user32',use_last_error=True)
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel32.OpenProcess.restype=wintypes.HANDLE
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    owner=kernel32.OpenProcess(0x00100000,False,parent)
    if not owner:raise RuntimeError('parent process unavailable')
    seen={};last_heartbeat=0
    try:
        emit('ready')
        while kernel32.WaitForSingleObject(owner,0)==258:
            active={}
            for window in backend.windows():
                process=window['process'].lower();name=window['title']
                # Never read arbitrary apps, the notification center's past history,
                # or the Phone Link conversation pane as a new notification.
                if window.get('elevated'):continue
                if process not in SHELL|MESSENGERS:continue
                if not BANNERS.search(name):continue
                texts=[]
                for control in backend.controls(window,80,7):
                    try:
                        info=control.element_info
                        if info.element.CurrentIsPassword:continue
                        label=str(info.name or '').strip()
                        if label and label not in texts:texts.append(label[:2000])
                    except Exception:continue
                if not texts:continue
                fingerprint='\n'.join(texts)
                key=str(window['handle'])
                active[key]=fingerprint
                if seen.get(key)==fingerprint:continue
                source=next((text for text in texts if SOURCES.search(text)),None)
                if source is None and process not in MESSENGERS:continue
                source=source or '휴대폰·메신저'
                omitted={name,source,'닫기','해제','설정','Close','Dismiss','Notification','새 알림','New notification'}
                content=[text for text in texts if text not in omitted and text not in {'보내기','답장','Send','Reply'}]
                if not content:continue
                emit('notification',source=source[:160],title=content[0][:400],body='\n'.join(content[1:])[:12000])
            seen=active
            if time.monotonic()-last_heartbeat>5:
                emit('heartbeat');last_heartbeat=time.monotonic()
            time.sleep(.8)
    finally:
        kernel32.CloseHandle(owner);backend.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--parent',type=int,required=True)
    args=parser.parse_args()
    try:run(args.parent)
    except Exception:emit('unavailable')
