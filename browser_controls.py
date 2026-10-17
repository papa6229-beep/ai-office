"""Observed browser elements; generic interactions, never site-specific scripts."""
import hashlib,json,time,uuid

# Observation returns controls, not a second copy of a large product table.
MAX_CONTROLS=60
PUBLIC_BYTES=12000
SELECT=r'''({query,priority,limit}) => {
 const norm=s=>String(s||'').normalize('NFKC').toLocaleLowerCase();
 const q=norm(query);
 const rank=e=>{
  const t=e.tagName,k=(e.type||'').toLowerCase(),r=e.getAttribute('role')||'';
  if(t==='TEXTAREA'||e.isContentEditable||(t==='INPUT'&&!['hidden','file','checkbox','radio','submit','button','reset','image','range','color'].includes(k)))return 0;
  if(t==='BUTTON'||(t==='INPUT'&&['submit','button','reset','image'].includes(k))||r==='button'||(t==='A'&&(e.hasAttribute('onclick')||/^javascript:/i.test(e.getAttribute('href')||''))))return 1;
  if(t==='SELECT')return 2;
  if(['checkbox','radio','option'].includes(r)||(t==='INPUT'&&['checkbox','radio'].includes(k)))return 3;
  return 4;
 };
 const nodes=document.querySelectorAll('input:not([type=hidden]),textarea,select,button,a[href],[role=button],[role=checkbox],[role=radio],[role=option],[contenteditable=true]');
 const found=[];
 for(const e of nodes){
  if(rank(e)!==priority)continue;
  if(q){
   const text=[e.id,e.getAttribute('name'),e.getAttribute('aria-label'),e.getAttribute('placeholder'),e.getAttribute('title'),e.tagName,e.type,e.getAttribute('role'),...Array.from(e.labels||[]).map(l=>l.innerText),...(['BUTTON','A'].includes(e.tagName)||['option','button'].includes(e.getAttribute('role'))?[e.innerText]:[]),...(e.tagName==='INPUT'&&['submit','button','reset'].includes(e.type)?[e.value]:[])].join(' ');
   if(!norm(text).includes(q)&&!(e.tagName==='SELECT'&&Array.from(e.options).some(o=>norm(o.text).includes(q))))continue;
  }
  const r=e.getBoundingClientRect(),style=getComputedStyle(e);
  if(!r.width||!r.height||style.visibility==='hidden'||style.display==='none')continue;
  found.push(e);if(found.length>=limit)break;
 }
 return found;
}'''
DESCRIBE=r'''(e,query) => {
 const norm=s=>String(s||'').normalize('NFKC').toLocaleLowerCase();
 const all=e.tagName==='SELECT'?Array.from(e.options):[];
 const matches=query?all.filter(o=>norm(o.text).includes(norm(query))):[];
 const options=matches.length?matches:all;
 return {connected:e.isConnected,tag:e.tagName.toLowerCase(),type:e.type||'',role:e.getAttribute('role')||'',name:(e.getAttribute('aria-label')||Array.from(e.labels||[]).map(l=>l.innerText).join(' ')||e.getAttribute('placeholder')||(['BUTTON','A'].includes(e.tagName)||['option','button'].includes(e.getAttribute('role'))?e.innerText:e.tagName==='INPUT'&&['submit','button','reset'].includes(e.type)?e.value:'')||e.getAttribute('name')||e.id||e.tagName).slice(0,300),disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',readonly:!!e.readOnly,href:e.tagName==='A'?e.href:'',editable:e.isContentEditable,option_count:all.length,option_matches:matches.length,options:options.slice(0,80).map(o=>({label:o.text.slice(0,200),value:o.value,disabled:o.disabled}))};
}'''


def signature(data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def compact_controls(rows,query=''):
    """Keep control identity before optional select metadata within a byte budget."""
    from browser_workspace import _redact
    def size(value):return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    public=[];sources=[]
    for row in rows:
        item={k:v for k,v in row.items() if k not in {'options','name','frame_url'}}
        item.update(name=_redact(row.get('name',''))[:120],frame_url=row.get('frame_url','')[:200],options=[],options_truncated=bool(row.get('option_count',row.get('options'))))
        if size(public+[item])>PUBLIC_BYTES-128:break
        public.append(item);sources.append(row)
    # Round-robin options so a long first dropdown cannot consume all metadata.
    for index in range(80 if query else 8):
        for item,row in zip(public,sources):
            options=row.get('options',[])
            if index>=len(options):continue
            option=options[index]
            if len(str(option.get('value','')))>300:continue
            proposed=dict(option,label=_redact(option.get('label',''))[:96])
            item['options'].append(proposed)
            if size(public)>PUBLIC_BYTES-128:item['options'].pop()
    # Budget a little below the hard ceiling for these explicit scope flags.
    for item,row in zip(public,sources):
        item['options_truncated']=len(item['options'])<int(row.get('option_count',len(row.get('options',[]))))
    while public and size(public)>PUBLIC_BYTES:public.pop()
    return public,len(public)<len(rows)


def inspect(workspace,tab_id,query=''):
    from browser_workspace import validate_url,public_url,BrowserWorkspaceError,_redact
    if not isinstance(query,str) or len(query)>200:raise BrowserWorkspaceError('찾을 입력칸 이름이나 라벨을 200자 이내로 지정하세요.')
    query=query.strip();page=workspace._page(tab_id);controls={};rows=[];limited=False;unread=set()
    snapshots=getattr(workspace,'_control_snapshots',None)
    if snapshots is None:workspace._control_snapshots={};snapshots=workspace._control_snapshots
    previous=snapshots.pop(tab_id,None)
    if previous:
        for item in previous['controls'].values():
            try:item['handle'].dispose()
            except Exception:pass
    frames=page.frames[:8]
    # Search fields and actions in child frames before spending slots on menus.
    for priority in range(5):
        if limited:break
        for frame in frames:
            try:validate_url(frame.url)
            except BrowserWorkspaceError:continue
            array=None
            try:
                array=frame.evaluate_handle(SELECT,{'query':query,'priority':priority,'limit':MAX_CONTROLS-len(rows)+1})
                for handle in array.get_properties().values():
                    element=handle.as_element()
                    if not element:handle.dispose();continue
                    if len(rows)>=MAX_CONTROLS:limited=True;element.dispose();continue
                    try:data=element.evaluate(DESCRIBE,query)
                    except Exception:element.dispose();continue
                    if not data['connected']:element.dispose();continue
                    tag,kind=data['tag'],data['type'];operations=[]
                    if not data['disabled']:
                        operations=['click','press']
                        if not data['readonly'] and (tag=='textarea' or data['editable'] or (tag=='input' and kind not in {'file','checkbox','radio','submit','button','reset','image','range','color'})):operations.append('fill')
                        if tag=='select':operations.append('select')
                        if kind in {'checkbox','radio'} or data['role'] in {'checkbox','radio'}:operations+=['check','uncheck']
                    ident=uuid.uuid4().hex
                    controls[ident]={'handle':element,'signature':signature(data),'operations':operations,'frame':frame,'descriptor':data,'query':query}
                    rows.append({'id':ident,'name':_redact(data['name']),'tag':tag,'type':kind,'role':data['role'],'password':kind=='password','operations':operations,'options':data['options'],'option_count':data['option_count'],'frame_url':public_url(frame.url)})
            except Exception:unread.add(id(frame))
            finally:
                if array:
                    try:array.dispose()
                    except Exception:pass
            if limited:break
    public,compact_limited=compact_controls(rows,query)
    kept={row['id'] for row in public}
    for ident in list(controls):
        if ident not in kept:
            try:controls[ident]['handle'].dispose()
            except Exception:pass
            del controls[ident]
    for row in public:controls[row['id']]['exposed_options']={o['value'] for o in row['options'] if not o['disabled']}
    ident=uuid.uuid4().hex
    snapshots[tab_id]={'id':ident,'url':page.url,'created':time.monotonic(),'controls':controls}
    result=workspace._meta(tab_id,page)
    result.update(control_snapshot=ident,controls=public,control_limit=limited or compact_limited,control_query=query,
                  unobserved_frames=max(0,len(page.frames)-len(frames))+len(unread),
                  control_scope='실제 렌더링된 입력칸·실행 버튼 우선. 본문/상품목록과 입력값은 미수집. 누락된 요소/선택지는 query로 좁혀 다시 조회하고 본문은 browser_read로 확인하세요.')
    return result


def target(workspace,tab_id,snapshot_id,control_id):
    from browser_workspace import BrowserWorkspaceError
    page=workspace._page(tab_id);snapshot=getattr(workspace,'_control_snapshots',{}).get(tab_id)
    if not snapshot or snapshot['id']!=snapshot_id or page.url!=snapshot['url'] or time.monotonic()-snapshot['created']>120:
        raise BrowserWorkspaceError('페이지가 바뀌었거나 관찰이 오래됐습니다. browser_inspect로 다시 읽으세요.')
    target=snapshot['controls'].get(control_id)
    if not target:raise BrowserWorkspaceError('관찰된 실제 요소를 선택하세요.')
    element=target['handle']
    try:data=element.evaluate(DESCRIBE,target.get('query',''))
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

def fill_login(workspace,tab_id,snapshot_id,control_id,account_id,username_target=None):
    import credential_vault
    from browser_workspace import BrowserWorkspaceError
    page,password,data=target(workspace,tab_id,snapshot_id,control_id)
    if data['type']!='password' or 'fill' not in password['operations']:raise BrowserWorkspaceError('실제 비밀번호 입력칸을 선택해 주세요.')
    site=credential_vault.origin(page.url)
    if credential_vault.origin(password['frame'].url)!=site:raise BrowserWorkspaceError('로그인 입력칸의 사이트를 다시 확인해 주세요.')
    username=None
    if username_target:
        _,username,user_data=target(workspace,tab_id,*username_target)
        if user_data['tag']!='input' or user_data['type'] not in {'text','email','tel'} or 'fill' not in username['operations']:raise BrowserWorkspaceError('실제 아이디 입력칸을 선택해 주세요.')
        if credential_vault.origin(username['frame'].url)!=site:raise BrowserWorkspaceError('아이디 입력칸의 사이트를 다시 확인해 주세요.')
    user,secret=credential_vault.load_for_origin(account_id,site)
    try:
        if username:username['handle'].fill(user,timeout=10000)
        password['handle'].fill(secret,timeout=10000)
    except Exception:
        return {'action_dispatched':None,'execution_uncertain':True,'note':'로그인 입력 결과를 확정하지 못했어요. 재시도 전에 현재 화면을 확인해 주세요.'}
    finally:secret=None;user=None
    try:result=inspect(workspace,tab_id)
    except Exception:result=workspace._meta(tab_id,page)
    return dict(result,action_dispatched=True,operation='saved_login_fill',note='저장된 계정을 입력했어요. 로그인 버튼과 실제 인증 결과 확인을 이어가세요. 비밀번호는 반환하지 않습니다.')


def act(workspace,tab_id,snapshot_id,control_id,operation,value=None):
    from browser_workspace import BrowserWorkspaceError,validate_url
    page,item,data=target(workspace,tab_id,snapshot_id,control_id)
    if operation not in item['operations']:raise BrowserWorkspaceError('관찰된 요소가 지원하는 동작을 선택하세요.')
    element=item['handle']
    if operation in {'fill','select','press'} and (not isinstance(value,str) or len(value)>60000):raise BrowserWorkspaceError('입력값을 지정하세요.')
    if operation=='select' and value not in item.get('exposed_options',set()):raise BrowserWorkspaceError('관찰한 선택지의 실제 값을 사용하세요.')
    if operation=='press' and value not in {'Enter','Tab','Escape','ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Space','Backspace','Delete','Home','End'}:
        raise BrowserWorkspaceError('지원되는 일반 입력 키를 사용하세요. 입력란 전체 교체는 fill로 수행하고 browser_inspect로 현재 요소를 다시 확인하세요. 사용자 브라우저의 마우스·키보드 조작으로 우회하지 마세요.')
    if data['tag']=='a' and operation=='click':
        # An observed site's JS submit link is a normal DOM click, like a
        # button with onclick. Never evaluate a model-supplied script/URL.
        if data['href'] and not data['href'].lower().startswith('javascript:'):validate_url(data['href'])
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
