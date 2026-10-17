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
import uuid
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


def phone_notification_cards(backend,window,metadata=None):
    """Return exact notification ListItems with their public message fields."""
    root=backend._desktop.window(handle=window['handle']).wrapper_object()
    roots=list(nodes(backend,root,160,9))
    if metadata is not None:
        metadata.update(connected=any(node.element_info.control_type in {'Text','Button'} and label(node.element_info) in {'연결됨','Connected'} for node in roots),complete=True)
    pane=next((node for node in roots
               if node.element_info.automation_id=='PaneContent'
               and node.element_info.name in {'알림','Notifications'}),None)
    if pane is None:return None
    lists=[node for node in nodes(backend,pane,90,7)
           if node.element_info.automation_id in {'NotificationsList','PinnedNotificationsList'}]
    if not lists:return None
    events=[]
    for group in lists:
        children=list(itertools.islice(group.element_info.iter_children(),41))
        items=[element for element in children if element.control_type=='ListItem']
        if metadata is not None:
            # SizeOfSet includes filtered system cards. A partial/virtualized
            # list must never be mistaken for messages cleared on the phone.
            from pywinauto.uia_defines import IUIA
            sizes=[]
            for element in items:
                try:sizes.append(int(element.element.GetCurrentPropertyValue(IUIA().UIA_dll.UIA_SizeOfSetPropertyId)))
                except Exception:pass
            complete=bool(sizes and min(sizes)>0 and len(set(sizes))==1 and len(items)==sizes[0] and len(children)<41)
            if not items:
                try:complete=not bool(group.parent().iface_scroll.CurrentVerticallyScrollable)
                except Exception:complete=False
            metadata.setdefault('coverage',[]).append({'items':len(items),'expected':sorted(set(sizes)),'complete':complete})
            metadata['complete'] &= complete
        for element in items[:40]:
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
            actions={node.element_info.automation_id for node in nodes(backend,card,75,7)
                     if node.element_info.control_type in {'Edit','Button'}} if metadata is not None else set()
            events.append((card,{'source':source[:160],'title':title[:400],'body':body[:12000],
                                 'can_reply':{'ReplyTextBox','NotificationReplySend'}<=actions,
                                 'origin':'phone_link_panel','event_key':'phone-link:'+key}))
    if metadata is not None:
        coverage=metadata.get('coverage',[])
        # An empty pinned collection has no SizeOfSet/Scroll pattern. It must
        # not invalidate a fully enumerated regular collection.
        metadata['complete']=any(row['complete'] for row in coverage) and all(row['complete'] for row in coverage if row['items'])
        refreshing=any(node.element_info.automation_id=='RefreshButton' and node.element_info.control_type=='Button' and not node.is_enabled() for node in roots)
        if refreshing:metadata['complete']=False
        if not any(row['items'] for row in coverage):
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.IsIconic.argtypes=[wintypes.HWND];user32.IsWindowVisible.argtypes=[wintypes.HWND]
            if user32.IsIconic(window['handle']) or not user32.IsWindowVisible(window['handle']):metadata['complete']=False
    return events


def phone_notifications(backend,window):
    cards=phone_notification_cards(backend,window)
    return None if cards is None else [event for _,event in cards]


def between_banner(window):
    """The observed Between banner is a 310x80 window, separate from chat."""
    if window['process'].lower()!='between.exe' or window['title']!='Between':return False
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user32.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    user32.IsIconic.argtypes=[wintypes.HWND]
    if user32.IsIconic(window['handle']):return False
    cls=ctypes.create_unicode_buffer(160);rect=wintypes.RECT()
    if not user32.GetClassNameW(window['handle'],cls,len(cls)):return False
    if not user32.GetWindowRect(window['handle'],ctypes.byref(rect)):return False
    return cls.value=='Chrome_WidgetWin_1' and 220<=rect.right-rect.left<=500 and 45<=rect.bottom-rect.top<=180

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
            inventories=[];inventory_flags=[];inventory_sources={}
            if phone_poll:last_phone_poll=time.monotonic();phone_state='not_found'
            windows=backend.windows()
            import phone_link_runtime
            known={window['handle'] for window in windows}
            windows.extend(dict(window,elevated=False,title='Phone Link') for window in phone_link_runtime.windows(True) if window['handle'] not in known)
            for window in windows:
                process=window['process'].lower();name=window['title']
                # Never read arbitrary apps, the notification center's past history,
                # or the Phone Link conversation pane as a new notification.
                if window.get('elevated'):continue
                if process in PHONES and phone_poll:
                    try:
                        metadata={}
                        cards=phone_notification_cards(backend,window,metadata)
                        events=None if cards is None else [event for _,event in cards]
                        phone_state='reading' if events is not None else 'pane_unavailable'
                        if events is not None:
                            inventory_flags.append(metadata)
                            for event in events:
                                fingerprint=hashlib.sha256(json.dumps([event['source'],event['title'],event['body']],ensure_ascii=False).encode()).hexdigest()
                                inventories.append({'key':event['event_key'],'fingerprint':fingerprint,'can_reply':event['can_reply']})
                                inventory_sources[event['source']]=inventory_sources.get(event['source'],0)+1
                            if not metadata.get('connected'):phone_state='disconnected'
                            elif not metadata.get('complete'):phone_state='partial'
                        for event in events or []:
                            if event['event_key'] in phone_seen:continue
                            emit('notification',**event);phone_seen.add(event['event_key'])
                        if len(phone_seen)>4000:phone_seen={event['event_key'] for event in events or []}
                    except Exception:phone_state='pane_unavailable'
                if between_banner(window):
                    texts=[]
                    try:
                        for control in backend.controls(window,35,5):
                            info=control.element_info
                            if info.control_type not in {'Text','Document'}:continue
                            text=label(info)
                            if text and text not in {'Between','닫기','Close'} and text not in texts:texts.append(text)
                    except Exception:pass
                    key='between:'+str(window['handle']);fingerprint='\n'.join(texts)
                    active[key]=fingerprint
                    if key not in seen or seen[key]!=fingerprint:
                        emit('notification',source='Between',title=texts[0][:400] if texts else '비트윈에 새 알림이 왔어요',
                             body='\n'.join(texts[1:])[:12000] if texts else 'PC 비트윈 알림을 감지했어요. 메시지 내용은 비트윈 앱에서 확인해 주세요.',
                             origin='native_between_banner',event_key='between:'+uuid.uuid4().hex)
                    continue
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
            if phone_poll:
                emit('phone_inventory',entries=inventories,
                     connected=bool(inventory_flags) and all(row['connected'] for row in inventory_flags),
                     complete=bool(inventory_flags) and all(row['complete'] for row in inventory_flags),sources=inventory_sources)
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
