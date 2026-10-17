"""Read exposed Phone Link notification cards and banners; never send input."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import re
import sys
import time
import hashlib
import itertools
import unicodedata
from collections import deque

SOURCES=re.compile(r'휴대폰과\s*연결|Phone\s*Link|YourPhone|카카오|Kakao|비트윈|Between|네이트온|Nate\s*On|한비로|Hanbiro|메시지|Messages|문자',re.I)
BANNERS=re.compile(r'새\s*알림|알림\s*창|new\s*notification|toast',re.I)
SHELL={'shellexperiencehost.exe','sihost.exe','explorer.exe'}
PHONES={'yourphone.exe','phoneexperiencehost.exe'}
MESSENGERS=PHONES|{'kakaotalk.exe','between.exe','nateon.exe','nateonmain.exe','hanbirotalk.exe','hanbirotalk2.exe'}


def nodes(backend,root,limit=100,depth_limit=8):
    """Bounded traversal that can stop before unrelated app panes are visited."""
    pending=deque([(root,0)]);count=0
    while pending and count<limit:
        node,depth=pending.popleft();count+=1
        yield node
        if depth>=depth_limit:continue
        try:
            remaining=limit-count-len(pending)
            pending.extend((backend._wrapper_class(child),depth+1)
                           for child in itertools.islice(node.element_info.iter_children(),max(0,remaining)))
        except Exception:continue


def label(info):
    if info.element.CurrentIsPassword:return ''
    return ''.join(c for c in str(info.name or '') if unicodedata.category(c)!='Cf').strip()


def phone_notifications(backend,window):
    root=backend._desktop.window(handle=window['handle']).wrapper_object()
    pane=next((node for node in nodes(backend,root,120,9)
               if node.element_info.automation_id=='PaneContent'
               and node.element_info.name in {'알림','Notifications'}),None)
    if pane is None:return None
    lists=[node for node in nodes(backend,pane,90,7)
           if node.element_info.automation_id in {'NotificationsList','PinnedNotificationsList'}]
    events=[]
    for group in lists:
        for element in itertools.islice(group.element_info.iter_children(),40):
            if element.control_type!='ListItem':continue
            card=backend._wrapper_class(element);fields={};messages=[]
            for node in nodes(backend,card,65,5):
                info=node.element_info
                if info.control_type!='Text':continue
                text=label(info)
                if not text:continue
                if info.automation_id in {'AppNameTextBlock','CompactModeTitleTextBlock','NotificationDescriptionText'}:
                    fields[info.automation_id]=text
            # Read only the notification's message group, never its action buttons
            # (which can contain copy-code labels), app grid, or conversation pane.
            message_group=next((node for node in nodes(backend,card,35,3)
                                if node.element_info.automation_id=='NotificationMessagesList'),None)
            if message_group:
                for node in nodes(backend,message_group,35,3):
                    info=node.element_info
                    if info.control_type=='Text':
                        text=label(info)
                        if text and text not in messages:messages.append(text)
            source=fields.get('AppNameTextBlock','')
            title=fields.get('CompactModeTitleTextBlock','')
            body=fields.get('NotificationDescriptionText','') or '\n'.join(messages)
            if not source or not (title or body):continue
            if not body and re.fullmatch(r'\d+개 알림 더보기',title):continue
            key=hashlib.sha256(json.dumps([source,label(element),title,body],ensure_ascii=False).encode()).hexdigest()
            events.append({'source':source[:160],'title':title[:400],'body':body[:12000],
                           'origin':'phone_link_panel','event_key':'phone-link:'+key})
    return events

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
    seen={};phone_seen=set();last_phone_poll=0;last_heartbeat=0;phone_state='waiting'
    try:
        emit('ready')
        while kernel32.WaitForSingleObject(owner,0)==258:
            active={}
            phone_poll=time.monotonic()-last_phone_poll>=2
            if phone_poll:last_phone_poll=time.monotonic();phone_state='not_found'
            for window in backend.windows():
                process=window['process'].lower();name=window['title']
                # Never read arbitrary apps, the notification center's past history,
                # or the Phone Link conversation pane as a new notification.
                if window.get('elevated'):continue
                if process in PHONES and phone_poll:
                    try:
                        events=phone_notifications(backend,window)
                        phone_state='reading' if events is not None else 'pane_unavailable'
                        for event in events or []:
                            if event['event_key'] in phone_seen:continue
                            emit('notification',**event);phone_seen.add(event['event_key'])
                        if len(phone_seen)>4000:phone_seen={event['event_key'] for event in events or []}
                    except Exception:phone_state='pane_unavailable'
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
                emit('heartbeat',phone_state=phone_state);last_heartbeat=time.monotonic()
            time.sleep(.8)
    finally:
        kernel32.CloseHandle(owner);backend.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--parent',type=int,required=True)
    args=parser.parse_args()
    try:run(args.parent)
    except Exception:emit('unavailable')
