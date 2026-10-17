"""Exact Phone Link card actions. No shell, other chats or automatic retry."""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import sys
import time
import unicodedata

import notification_capture as capture
import windows_workspace


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC',str(value or '')).split())


def target(backend,item,*,missing_ok=False):
    if item.get('origin')!='phone_link_panel':
        raise ValueError('이 알림은 휴대폰과 연결의 원본 알림이 아니에요.')
    matches=[];panes=0
    import phone_link_runtime
    for window in phone_link_runtime.windows(True):
        if window['process'].lower() not in capture.PHONES or window.get('elevated'):continue
        metadata={}
        cards=capture.phone_notification_cards(backend,window,metadata)
        if cards is None or not metadata.get('connected'):continue
        panes+=1
        for card,event in cards:
            if item.get('event_key') and event['event_key']!=item['event_key']:continue
            if all(normalized(event[key])==normalized(item.get(key)) for key in ('source','title','body')):
                matches.append(card)
    if not panes:raise ValueError('휴대폰과 연결의 알림 영역에 연결하지 못했어요. 앱 연결 상태를 확인해 주세요.')
    if not matches and missing_ok:return None
    if len(matches)!=1:
        raise ValueError('원본 알림이 바뀌거나 사라졌어요. 최신 알림 카드를 다시 선택해 주세요.' if not matches
                         else '동일한 원본 알림이 여러 개라 대상을 하나로 확인하지 못했어요.')
    return matches[0]


def control(backend,card,identifier,kind):
    matches=[node for node in capture.nodes(backend,card,100,8)
             if node.element_info.automation_id==identifier and node.element_info.control_type==kind]
    if len(matches)!=1:return None
    return matches[0]


@contextmanager
def reveal_dismiss(card):
    # Phone Link creates DeleteSingleNotification only while hovering a card.
    # Use this only for an explicit user acknowledgement; polling never moves input.
    user32=ctypes.WinDLL('user32',use_last_error=True)
    point=wintypes.POINT();user32.GetCursorPos(ctypes.byref(point))
    root=card.top_level_parent();handle=root.handle
    user32.IsIconic.argtypes=[wintypes.HWND]
    user32.ShowWindowAsync.argtypes=[wintypes.HWND,ctypes.c_int]
    minimized=bool(user32.IsIconic(handle))
    try:
        if minimized:root.restore()
        rectangle=card.rectangle()
        card.move_mouse_input(coords=(rectangle.left+min(50,rectangle.width()//2),rectangle.top+min(20,rectangle.height()//2)),absolute=True)
        time.sleep(.15)
        yield
    finally:
        user32.SetCursorPos(point.x,point.y)
        if minimized:user32.ShowWindowAsync(handle,7)


def inspect(backend,item):
    card=target(backend,item,missing_ok=True)
    if card is None:return {'matched':False,'can_reply':False,'reason':'원본 알림이 이미 정리됐거나 갱신됐어요. 이 기록에서는 바로 답장할 수 없어요.'}
    result={'matched':True,
            'reply_input':control(backend,card,'ReplyTextBox','Edit') is not None,
            'reply_send':control(backend,card,'NotificationReplySend','Button') is not None,
            'dismiss':control(backend,card,'DeleteSingleNotification','Button') is not None}
    result['can_reply']=result['reply_input'] and result['reply_send']
    result['reason']='원본 알림으로 답장할 수 있어요.' if result['can_reply'] else '이 앱의 원본 알림은 바로 답장을 제공하지 않아요. 원래 앱에서 답장해 주세요.'
    return result


def reconnect(backend):
    import phone_link_runtime
    matches=[]
    for window in phone_link_runtime.windows(True):
        root=backend._desktop.window(handle=window['handle']).wrapper_object()
        for node in capture.nodes(backend,root,160,9):
            if node.element_info.control_type=='Button' and node.element_info.automation_id=='RefreshButton' and node.parent().element_info.automation_id=='PaneRoot':
                matches.append(node)
    if len(matches)!=1 or not matches[0].is_enabled():return {'requested':False}
    matches[0].invoke()
    return {'requested':True}


def reply(backend,item,text):
    card=target(backend,item)
    box=control(backend,card,'ReplyTextBox','Edit')
    send=control(backend,card,'NotificationReplySend','Button')
    if box is None or send is None:raise ValueError('이 원본 알림에는 답장 입력란이 없어요. 원래 앱에서 답장해 주세요.')
    if not box.is_enabled():raise ValueError('휴대폰과 연결의 답장 입력란이 비활성 상태예요.')
    current=box.get_value().strip()
    if current and current!=text:raise ValueError('원본 알림에 다른 답장 초안이 있어 덮어쓰지 않았어요.')
    box.set_edit_text(text)
    deadline=time.monotonic()+2
    while not send.is_enabled() and time.monotonic()<deadline:time.sleep(.1)
    if box.get_value()!=text or not send.is_enabled():
        raise ValueError('답장 입력 또는 보내기 버튼 준비를 확인하지 못해 전송하지 않았어요.')
    # Resolve the unchanged app/sender/body again immediately before the effect.
    target(backend,item)
    send.invoke()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        try:
            if not box.get_value().strip():
                return {'submitted':True,'input_cleared':True,'message':'휴대폰과 연결에 답장을 전달했고 입력란이 비워진 것을 확인했어요. 상대방 수신 여부는 원래 앱에서 확인해 주세요.'}
        except Exception:break
        time.sleep(.15)
    return {'submitted':True,'input_cleared':False,'message':'보내기를 요청했지만 전송 결과를 확인하지 못했어요. 다시 보내기 전에 원래 앱에서 확인해 주세요.'}


def acknowledge(backend,item):
    if item.get('origin')!='phone_link_panel':return {'state':'unsupported'}
    card=target(backend,item,missing_ok=True)
    if card is None:return {'state':'not_present'}
    button=control(backend,card,'DeleteSingleNotification','Button')
    if button is None:
        with reveal_dismiss(card):
            # Never use clear-all or another card's close button.
            card=target(backend,item)
            button=control(backend,card,'DeleteSingleNotification','Button')
            if button is None:raise ValueError('선택한 알림의 개별 지우기 버튼을 찾지 못했어요.')
            button.invoke()
    else:button.invoke()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if target(backend,item,missing_ok=True) is None:return {'state':'dismissed'}
        time.sleep(.15)
    raise ValueError('원본 알림 정리 결과를 아직 확인하지 못했어요.')


def main():
    payload=json.loads(sys.stdin.read(2*1024*1024))
    backend=windows_workspace._PywinautoBackend()
    try:
        operation=payload.get('operation','inspect')
        if operation=='reconnect':return reconnect(backend)
        if operation=='inspect':return inspect(backend,payload['item'])
        if operation=='reply':return reply(backend,payload['item'],payload['text'])
        if operation=='acknowledge':
            results=[]
            for item in payload['items'][:80]:
                try:result=acknowledge(backend,item)
                except Exception as error:result={'state':'unavailable','error':str(error) if isinstance(error,ValueError) else '원본 알림 연결을 확인해 주세요.'}
                results.append(dict(result,id=item['id']))
            return {'results':results}
        raise ValueError('지원하지 않는 알림 동작이에요.')
    finally:backend.close()


if __name__=='__main__':
    try:print(json.dumps(main(),ensure_ascii=False),flush=True)
    except Exception as error:
        print(json.dumps({'error':str(error) if isinstance(error,ValueError) else '원본 알림 동작을 확인하지 못했어요.'},ensure_ascii=False),flush=True)
        sys.exit(1)
