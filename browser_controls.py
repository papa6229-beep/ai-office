"""Observed browser elements; generic interactions, never site-specific scripts."""
import hashlib,json,time,uuid

SELECT=r'''() => Array.from(document.querySelectorAll('input:not([type=hidden]),textarea,select,button,a[href],[role=button],[role=checkbox],[role=radio],[role=option],[contenteditable=true]')).filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width&&r.height&&s.visibility!=='hidden'&&s.display!=='none'}).slice(0,120)'''
DESCRIBE=r'''e => ({connected:e.isConnected,tag:e.tagName.toLowerCase(),type:e.type||'',role:e.getAttribute('role')||'',name:(e.getAttribute('aria-label')||Array.from(e.labels||[]).map(l=>l.innerText).join(' ')||e.getAttribute('placeholder')||(['BUTTON','A'].includes(e.tagName)||e.getAttribute('role')==='option'?e.innerText:'')||e.getAttribute('name')||e.id||e.tagName).slice(0,300),disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',readonly:!!e.readOnly,href:e.tagName==='A'?e.href:'',editable:e.isContentEditable,options:e.tagName==='SELECT'?Array.from(e.options).slice(0,80).map(o=>({label:o.text.slice(0,200),value:o.value.slice(0,300),disabled:o.disabled})):[]})'''

def signature(data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def inspect(workspace,tab_id):
    from browser_workspace import validate_url,public_url,BrowserWorkspaceError,_redact
    page=workspace._page(tab_id);controls={};public=[]
    snapshots=getattr(workspace,'_control_snapshots',None)
    if snapshots is None:workspace._control_snapshots={};snapshots=workspace._control_snapshots
    previous=snapshots.pop(tab_id,None)
    if previous:
        for item in previous['controls'].values():
            try:item['handle'].dispose()
            except Exception:pass
    for frame in page.frames[:8]:
        if len(public)>=120:break
        try:validate_url(frame.url)
        except BrowserWorkspaceError:continue
        array=None
        try:
            array=frame.evaluate_handle(SELECT)
            for key,handle in (array.get_properties()).items():
                element=handle.as_element()
                if not element:continue
                data=element.evaluate(DESCRIBE)
                if not data['connected'] or len(public)>=120:element.dispose();continue
                tag,kind=data['tag'],data['type'];operations=[]
                if not data['disabled']:
                    operations=['click','press']
                    if not data['readonly'] and (tag=='textarea' or data['editable'] or (tag=='input' and kind not in {'file','checkbox','radio','submit','button','reset','image','range','color'})):operations.append('fill')
                    if tag=='select':operations.append('select')
                    if kind in {'checkbox','radio'} or data['role'] in {'checkbox','radio'}:operations+=['check','uncheck']
                identifier=uuid.uuid4().hex
                controls[identifier]={'handle':element,'signature':signature(data),'operations':operations,'frame':frame,'descriptor':data}
                public.append({'id':identifier,'name':_redact(data['name']),'tag':tag,'type':kind,'role':data['role'],
                               'password':kind=='password','operations':operations,'options':data['options'],'frame_url':public_url(frame.url)})
        except Exception:
            continue
        finally:
            if array:
                try:array.dispose()
                except Exception:pass
    identifier=uuid.uuid4().hex
    snapshots[tab_id]={'id':identifier,'url':page.url,'created':time.monotonic(),'controls':controls}
    result=workspace._do_read(tab_id)
    result.update(control_snapshot=identifier,controls=public,control_limit=len(public)>=120,
                  control_scope='현재 보이는 입력·버튼·선택 요소와 실제 참조. 입력값·비밀번호는 반환하지 않음')
    return result

def target(workspace,tab_id,snapshot_id,control_id):
    from browser_workspace import BrowserWorkspaceError
    page=workspace._page(tab_id);snapshot=getattr(workspace,'_control_snapshots',{}).get(tab_id)
    if not snapshot or snapshot['id']!=snapshot_id or page.url!=snapshot['url'] or time.monotonic()-snapshot['created']>120:
        raise BrowserWorkspaceError('페이지가 바뀌었거나 관찰이 오래됐습니다. browser_inspect로 다시 읽으세요.')
    target=snapshot['controls'].get(control_id)
    if not target:raise BrowserWorkspaceError('관찰된 실제 요소를 선택하세요.')
    element=target['handle']
    try:data=element.evaluate(DESCRIBE)
    except Exception:raise BrowserWorkspaceError('요소가 바뀌었습니다. 페이지를 다시 관찰하세요.') from None
    if not data['connected'] or signature(data)!=target['signature']:raise BrowserWorkspaceError('요소의 현재 상태가 바뀌었습니다. 다시 관찰하세요.')
    return page,target,data

def read_value(workspace,tab_id,snapshot_id,control_id):
    from browser_workspace import BrowserWorkspaceError,_redact
    import re
    page,item,data=target(workspace,tab_id,snapshot_id,control_id)
    if data['type']=='password' or re.search(r'(?i)password|비밀번호|암호|api.?key|token|secret',data['name']):raise BrowserWorkspaceError('비밀번호·비밀값은 읽어 반환하지 않습니다.')
    element=item['handle']
    text=element.input_value() if data['tag'] in {'input','textarea','select'} else element.inner_text()
    return dict(workspace._meta(tab_id,page),name=data['name'],text=_redact(text[:60000]),total_characters=len(text),truncated=len(text)>60000)

def act(workspace,tab_id,snapshot_id,control_id,operation,value=None):
    from browser_workspace import BrowserWorkspaceError,validate_url
    page,item,data=target(workspace,tab_id,snapshot_id,control_id)
    if operation not in item['operations']:raise BrowserWorkspaceError('관찰된 요소가 지원하는 동작을 선택하세요.')
    element=item['handle']
    if operation in {'fill','select','press'} and (not isinstance(value,str) or len(value)>60000):raise BrowserWorkspaceError('입력값을 지정하세요.')
    if operation=='select' and value not in {x['value'] for x in data['options'] if not x['disabled']}:raise BrowserWorkspaceError('관찰한 선택지의 실제 값을 사용하세요.')
    if operation=='press' and value not in {'Enter','Tab','Escape','ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Space','Backspace','Delete','Home','End'}:
        raise BrowserWorkspaceError('일반 입력 키를 사용하세요. 복합 컴퓨터 조작은 desktop_action을 사용하세요.')
    if data['tag']=='a' and operation=='click':validate_url(data['href'])
    try:
        if operation=='fill':element.fill(value,timeout=10000)
        elif operation=='click':element.click(timeout=10000)
        elif operation=='select':element.select_option(value,timeout=10000)
        elif operation in {'check','uncheck'}:getattr(element,operation)(timeout=10000)
        else:element.press(value,timeout=10000)
        page.wait_for_timeout(300)
    except Exception:
        return {'action_dispatched':None,'execution_uncertain':True,'note':'동작 결과를 확정하지 못했습니다. 반복하지 말고 browser_inspect로 현재 상태를 읽으세요.'}
    try:result=inspect(workspace,tab_id)
    except Exception:result=workspace._meta(tab_id,page)
    return dict(result,action_dispatched=True,operation=operation,note='현재 페이지와 입력창을 다시 관찰했습니다. 전체 업무 완료 여부는 이 결과로 판단하세요.')
