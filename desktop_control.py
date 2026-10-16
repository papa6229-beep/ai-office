"""Screenshot-bound mouse/keyboard control on the existing Windows worker."""
import base64,ctypes,io,re,time,uuid

OPERATIONS=('click','double_click','right_click','move','drag','type','key','scroll','focus','move_window','resize','minimize','maximize','restore','close')

def _wrapper(service,info):return service._backend._desktop.window(handle=info['handle']).wrapper_object()

def _guard(info):
    from windows_workspace import WindowsWorkspaceError
    if info['process'].lower() in {'chatgpt.exe','codex.exe','lockapp.exe'}:
        raise WindowsWorkspaceError('denied')

def _activate(info):
    from pywinauto.controls.hwndwrapper import HwndWrapper
    from windows_workspace import WindowsWorkspaceError
    try:HwndWrapper(info['handle']).set_focus()
    except Exception:raise WindowsWorkspaceError('focus') from None

def _focus(service,window):
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        element=IUIA().iuia.GetFocusedElement()
        raw=service._backend._wrapper_class(UIAElementInfo(element))
        data=service._backend.describe_control(raw)
        return {key:data[key] for key in ('name','control_type','password','process_id') if key in data}
    except Exception:return {}

def observe(service,window_id):
    from windows_workspace import WindowsWorkspaceError,_sensitive,_redact
    from PIL import ImageGrab,ImageDraw
    window,info=service._resolve_window(window_id);_guard(info);wrapper=_wrapper(service,info)
    try:
        if wrapper.is_minimized():wrapper.restore()
        _activate(info);time.sleep(.18)
        rect=wrapper.rectangle();bounds=(rect.left,rect.top,rect.right,rect.bottom)
        if not 0<(rect.right-rect.left)*(rect.bottom-rect.top)<=32000000:raise WindowsWorkspaceError('unsupported')
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.GetForegroundWindow.restype=ctypes.c_void_p
        if user32.GetForegroundWindow()!=info['handle']:raise WindowsWorkspaceError('focus')
        image=ImageGrab.grab(bbox=bounds,all_screens=True).convert('RGB');draw=ImageDraw.Draw(image)
        for raw,data in service._collect(window):
            if _sensitive(data):
                box=raw.rectangle();box=(max(0,box.left-rect.left-3),max(0,box.top-rect.top-3),min(image.width,box.right-rect.left+3),min(image.height,box.bottom-rect.top+3))
                if box[2]>box[0] and box[3]>box[1]:draw.rectangle(box,fill='black')
        if all(b-a<3 for a,b in image.getextrema()):raise WindowsWorkspaceError('unsupported')
        original=image.size;image.thumbnail((1600,1200));scale=image.width/original[0]
        stream=io.BytesIO();image.save(stream,'JPEG',quality=80)
    except WindowsWorkspaceError:raise
    except Exception:raise WindowsWorkspaceError('unavailable') from None
    identifier=uuid.uuid4().hex
    if not hasattr(service,'_remote_states'):service._remote_states={}
    service._remote_states[window_id]={'id':identifier,'created':time.monotonic(),'bounds':bounds,'scale':scale,'size':image.size,'title':info['title']}
    try:accessibility=service._do_inspect(window_id)
    except Exception:accessibility={'text':'이 창의 접근성 텍스트를 읽지 못했습니다. 화면 이미지로 판단하세요.','controls':[]}
    return {'observation_id':identifier,'window_id':window_id,'title':_redact(info['title']), 'process':info['process'],
            'width':image.width,'height':image.height,'image':base64.b64encode(stream.getvalue()).decode(),
            'text':accessibility.get('text',''),'focused':_focus(service,window),
            'scope':'선택한 실제 전경 창의 화면·접근성·초점. 화면 좌표는 이 이미지 기준이며 다음 동작 뒤 만료됩니다.'}

def key_sequence(value):
    if not isinstance(value,str) or len(value)>80:raise ValueError('키 조합을 확인하세요.')
    parts=[x.strip().upper() for x in value.split('+')];mods='';key=parts[-1]
    for part in parts[:-1]:
        if part not in {'CTRL','CONTROL','ALT','SHIFT'}:raise ValueError('Ctrl·Alt·Shift 키 조합을 사용하세요.')
        mods+= {'CTRL':'^','CONTROL':'^','ALT':'%','SHIFT':'+'}[part]
    named={'ENTER':'ENTER','RETURN':'ENTER','TAB':'TAB','ESC':'ESC','ESCAPE':'ESC','SPACE':'SPACE','BACKSPACE':'BACKSPACE','DELETE':'DELETE','HOME':'HOME','END':'END','PAGEUP':'PGUP','PAGEDOWN':'PGDN','UP':'UP','DOWN':'DOWN','LEFT':'LEFT','RIGHT':'RIGHT','INSERT':'INSERT'}
    if key in named:return mods+'{'+named[key]+'}'
    if re.fullmatch(r'F(?:[1-9]|1[0-2])',key):return mods+'{'+key+'}'
    if re.fullmatch('[A-Z0-9]',key):return mods+key.lower()
    raise ValueError('일반 문자·방향·기능 키를 지정하세요. Windows 키·실행창·터미널 조작은 지원하지 않습니다.')

def act(service,window_id,observation_id,operation,args):
    from windows_workspace import WindowsWorkspaceError,_sensitive
    from pywinauto import mouse,keyboard
    from pywinauto.controls.hwndwrapper import HwndWrapper
    from ctypes import wintypes
    if operation not in OPERATIONS:raise WindowsWorkspaceError('invalid')
    window,info=service._resolve_window(window_id);_guard(info);wrapper=_wrapper(service,info)
    state=getattr(service,'_remote_states',{}).get(window_id)
    if not state or state['id']!=observation_id or time.monotonic()-state['created']>90:raise WindowsWorkspaceError('stale')
    rect=wrapper.rectangle()
    if tuple((rect.left,rect.top,rect.right,rect.bottom))!=state['bounds'] or info['title']!=state['title']:raise WindowsWorkspaceError('stale')
    user32=ctypes.WinDLL('user32',use_last_error=True);user32.GetForegroundWindow.restype=ctypes.c_void_p
    _activate(info)
    if user32.GetForegroundWindow()!=info['handle']:raise WindowsWorkspaceError('focus')
    focus=_focus(service,window)
    if operation in {'type','key'} and _sensitive(focus):raise WindowsWorkspaceError('denied')
    if info['process'].lower()=='explorer.exe' and operation in {'type','key'} and re.search(r'(?i)address|주소|location',focus.get('name','')):
        raise WindowsWorkspaceError('denied')
    def point(x,y):
        if type(x) not in (int,float) or type(y) not in (int,float) or not 0<=x<state['size'][0] or not 0<=y<state['size'][1]:raise WindowsWorkspaceError('invalid')
        target=(int(rect.left+x/state['scale']),int(rect.top+y/state['scale']))
        user32.WindowFromPoint.argtypes=[wintypes.POINT];user32.WindowFromPoint.restype=wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user32.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user32.GetAncestor.restype=wintypes.HWND
        handle=user32.WindowFromPoint(wintypes.POINT(*target));pid=wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle,ctypes.byref(pid))
        if pid.value!=info['pid'] and user32.GetAncestor(handle,2)!=info['handle']:raise WindowsWorkspaceError('stale')
        return target
    p=point(args.get('x'),args.get('y')) if operation in {'click','double_click','right_click','move','drag','scroll'} else None
    destination=point(args.get('to_x'),args.get('to_y')) if operation=='drag' else None
    text=args.get('text','');keys=args.get('keys','')
    if operation=='type' and (not isinstance(text,str) or not 0<len(text)<=60000 or '\x00' in text):raise WindowsWorkspaceError('invalid')
    sequence=key_sequence(keys) if operation=='key' else None
    size=(args.get('width'),args.get('height'))
    if operation=='resize' and any(type(x) is not int or not 200<=x<=12000 for x in size):raise WindowsWorkspaceError('invalid')
    if operation=='move_window' and any(type(args.get(x)) is not int or not -20000<=args[x]<=20000 for x in ('x','y')):raise WindowsWorkspaceError('invalid')
    service._remote_states.pop(window_id,None)
    sequence_before=user32.GetClipboardSequenceNumber()
    try:
        if operation in {'click','right_click'}:mouse.click(button='right' if operation=='right_click' else 'left',coords=p)
        elif operation=='double_click':mouse.double_click(coords=p)
        elif operation=='move':mouse.move(coords=p)
        elif operation=='drag':
            mouse.press(coords=p)
            try:
                for step in range(1,11):
                    mouse.move(coords=tuple(int(p[i]+(destination[i]-p[i])*step/10) for i in (0,1)));time.sleep(.025)
            finally:mouse.release(coords=destination)
        elif operation=='scroll':mouse.scroll(coords=p,wheel_dist=max(-20,min(20,args.get('delta',-4))))
        elif operation=='type':keyboard.send_keys(''.join('{'+c+'}' if c in '+^%{}()' else c for c in text),with_spaces=True,with_tabs=True,with_newlines=True,pause=.001)
        elif operation=='key':keyboard.send_keys(sequence,pause=.03)
        elif operation=='resize':HwndWrapper(info['handle']).move_window(width=size[0],height=size[1])
        elif operation=='move_window':HwndWrapper(info['handle']).move_window(x=args['x'],y=args['y'])
        elif operation in {'minimize','maximize','restore','close'}:getattr(HwndWrapper(info['handle']),operation)()
        else:wrapper.set_focus()
        time.sleep(.2)
    except Exception:return {'action_dispatched':None,'execution_uncertain':True,'note':'동작 결과가 불확실합니다. 다시 관찰한 뒤 판단하고 같은 효과를 맹목적으로 반복하지 마세요.'}
    result={'action_dispatched':True,'operation':operation}
    if operation=='key' and keys.upper() in {'CTRL+C','CONTROL+C','CTRL+X','CONTROL+X'} and user32.GetClipboardSequenceNumber()!=sequence_before:
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            try:
                from browser_workspace import _redact
                result['copied_text']=_redact(str(win32clipboard.GetClipboardData(13))[:60000])
            finally:win32clipboard.CloseClipboard()
        except Exception:result['clipboard_note']='클립보드 내용은 읽지 못했습니다.'
    if operation not in {'close','minimize'}:
        try:result['observation']=observe(service,window_id)
        except Exception:result['note']='동작 후 창이나 대화상자가 바뀌었습니다. windows_list에서 현재 창을 다시 선택하세요.'
    return result
