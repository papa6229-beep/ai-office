"""Relay user requests to one explicitly connected Codex desktop conversation.

Uses the installed desktop MCP client, not a second inference agent. Local IPC
addresses and delivery state stay in Git-excluded storage. Never auto-launches
Codex, changes its model, or falls back to another inference path.
"""
from contextlib import closing, contextmanager
import atexit
import datetime
import json
import request_boundary
import os
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

BRIDGE_REVISION = '2026-10-28-connection-diagnostics-v6'
ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'local_backups'/'desktop-chat'/'connection.json'
_serial=threading.Lock()
_health_lock=threading.Lock()
_health={}
_MAX_LINE=8*1024*1024
_pool_lock=threading.Lock()
_cached_client=None
_cached_key=None
_idle_timer=None
_IDLE_SECONDS=600


def _connection_key(config):
    return tuple(config.get(key) for key in ('thread_id','workspace','node','pipe_path'))


def _close_idle(expected=None):
    global _cached_client,_cached_key,_idle_timer
    with _pool_lock:
        if expected is not None and _cached_client is not expected:return
        client=_cached_client;_cached_client=None;_cached_key=None
        if _idle_timer:_idle_timer.cancel();_idle_timer=None
    if client:client.close()


def _borrow(config):
    global _cached_client,_cached_key,_idle_timer
    with _pool_lock:
        client,key=_cached_client,_cached_key
        _cached_client=None;_cached_key=None
        if _idle_timer:_idle_timer.cancel();_idle_timer=None
    if client and (key!=_connection_key(config) or client.process.poll() is not None):
        client.close();return None
    return client


def _cache(client,config):
    global _cached_client,_cached_key,_idle_timer
    with _pool_lock:
        previous=_cached_client
        _cached_client=client;_cached_key=_connection_key(config)
        if _idle_timer:_idle_timer.cancel()
        _idle_timer=threading.Timer(_IDLE_SECONDS,_close_idle,args=(client,))
        _idle_timer.daemon=True;_idle_timer.start()
    if previous and previous is not client:previous.close()


atexit.register(_close_idle)


class BridgeError(ValueError):
    def __init__(self,message,*,code='bridge_error',stage='connection',attempts=None):
        super().__init__(message)
        self.code=code;self.stage=stage;self.attempts=attempts or []


def _failure(error):
    # Allowlisted metadata only: never retain a tool payload, path or token.
    return {'code':getattr(error,'code','access_denied' if isinstance(error,PermissionError) else 'os_error' if isinstance(error,OSError) else 'technical_error'),
            'stage':getattr(error,'stage','connection'),'error_type':type(error).__name__,
            **{key:getattr(error,key) for key in ('errno','winerror') if isinstance(getattr(error,key,None),int)},
            **({'attempts':error.attempts} if isinstance(error,BridgeError) and error.attempts else {})}


def _tool_error(result):
    raw=json.dumps(result,ensure_ascii=True)
    if re.search(r'\b(?:EACCES|EPERM)\b|Access is denied|access denied',raw,re.I):
        return BridgeError('Codex 연결 접근이 실행 환경에서 거부됐어요. 사용자 미승인이나 앱 종료로 단정하지 않습니다.',code='access_denied',stage='tool')
    if re.search(r'\b(?:ENOENT|ECONNREFUSED|ECONNRESET)\b|pipe closed',raw,re.I):
        return BridgeError('Codex 연결 경로가 없거나 연결이 끊겼어요. 전달 효과를 확인하기 전 같은 요청을 다시 보내지 않습니다.',code='endpoint_unavailable',stage='tool')
    return BridgeError('Codex 앱 도구가 요청 처리 오류를 반환했어요. 연결 실패와 사용자 승인 대기를 구분해 확인해야 합니다.',code='tool_error',stage='tool')


def configuration():
    try:
        data=json.loads(CONFIG.read_text(encoding='utf-8'))
        if data.get('enabled') is not True:return {}
        if not re.fullmatch(r'[0-9a-f-]{36}',data.get('thread_id','')):return {}
        if Path(data.get('workspace','')).resolve()!=ROOT:return {}
        return data
    except (OSError,ValueError,TypeError):return {}


def configure_current():
    """Trusted installation step: bind only the current actual desktop context."""
    ident=os.environ.get('CODEX_THREAD_ID','')
    pipe=os.environ.get('CODEX_APP_TOOLS_PIPE_PATH','')
    if not re.fullmatch(r'[0-9a-f-]{36}',ident) or not pipe.startswith('\\\\.\\pipe\\codex-app-tools-'):
        raise BridgeError('현재 Codex 앱의 대화 연결 정보를 확인하지 못했어요.')
    data={'enabled':True,'thread_id':ident,'workspace':str(ROOT),'pipe_path':pipe,
          'node':os.environ.get('CODEX_MCP_NODE_PATH') or shutil.which('node'),
          'title':'AI OFFICE 개발 대화','configured_at':time.time()}
    CONFIG.parent.mkdir(parents=True,exist_ok=True)
    temporary=CONFIG.with_suffix('.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(CONFIG)
    return public_status()


def public_status():
    data=configuration()
    with _health_lock:health=dict(_health)
    return {'enabled':bool(data),'title':data.get('title',''),'requires_desktop':True,
            'auto_start':False,'available':health.get('available'),'checked_at':health.get('checked_at'),
            'failure':health.get('failure')}


def _set_health(ok,error=None):
    with _health_lock:_health.update(available=ok,checked_at=time.time(),failure=_failure(error) if error else None)


def _client_paths(config):
    candidates=ROOT.parent/'.codex'/'plugins'/'cache'/'openai-bundled'/'codex-app-tools'
    servers=[p for p in candidates.glob('*/server.mjs') if p.is_file()]
    if not servers:raise BridgeError('설치된 Codex 앱 연결 도구를 찾지 못했어요.',code='tool_missing')
    node=config.get('node')
    if not node or not Path(node).is_file():node=shutil.which('node')
    if not node:raise BridgeError('Codex 앱 연결용 실행기를 찾지 못했어요.',code='runtime_missing')
    return node,max(servers,key=lambda p:p.stat().st_mtime)


class DesktopClient:
    def __init__(self,config,pipe):
        self.config=config;self.events=queue.Queue();self.ident=0;self.delivery_inputs={}
        node,server=_client_paths(config)
        env=dict(os.environ);env['CODEX_APP_TOOLS_PIPE_PATH']=pipe
        self.process=subprocess.Popen([node,str(server)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            cwd=ROOT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self._read,daemon=True).start()
        try:
            self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'aemeath-desktop-chat','version':'1.0'}},8)
            self.process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n');self.process.stdin.flush()
            tools=self.rpc('tools/list',{},8)
            if not {'read_thread','send_message_to_thread'}<={row['name'] for row in tools.get('tools',[])}:
                raise BridgeError('현재 Codex 앱이 대화 연결 도구를 제공하지 않아요.',code='capability_missing')
        except Exception:
            self.close();raise

    def _read(self):
        try:
            while True:
                line=self.process.stdout.readline(_MAX_LINE+1)
                if not line:break
                if len(line)>_MAX_LINE:break
                try:self.events.put(json.loads(line))
                except ValueError:continue
        finally:self.events.put(None)

    def rpc(self,method,params,timeout=20):
        self.ident+=1;ident=self.ident
        try:
            self.process.stdin.write((json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params})+'\n').encode());self.process.stdin.flush()
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                event=self.events.get(timeout=max(.1,deadline-time.monotonic()))
                if event is None:raise BridgeError('Codex 앱 도구 연결이 종료됐어요. 앱 종료·접근 제한 여부를 연결 진단에서 확인하세요.',code='connection_closed',stage=method)
                if event.get('id')==ident:
                    if event.get('error'):raise _tool_error(event['error'])
                    return event.get('result',{})
        except OSError as error:
            raise BridgeError('Codex 연결 쓰기/읽기에 실패했어요. 전달 여부가 불명확해 같은 요청을 다시 보내지 않았어요.',code='access_denied' if isinstance(error,PermissionError) else 'transport_error',stage=method) from error
        except queue.Empty:
            raise BridgeError('Codex 앱 응답을 확인하지 못했어요. 전달된 요청은 다시 보내지 않았어요.',code='response_uncertain',stage=method) from None
        raise BridgeError('Codex 앱 응답 대기시간을 넘겼어요.',code='timeout',stage=method)

    def tool(self,name,args):
        result=self.rpc('tools/call',{'name':name,'arguments':args,'_meta':{'threadId':self.config['thread_id']}})
        if result.get('isError'):raise _tool_error(result)
        texts=[row['text'] for row in result.get('content',[]) if row.get('type')=='text']
        try:return json.loads(texts[0])
        except (ValueError,IndexError):raise BridgeError('Codex 앱의 응답 형식을 확인하지 못했어요.',code='invalid_response',stage='tool') from None

    def read(self,limit=3,chars=20000):
        args={'threadId':self.config['thread_id'],'turnLimit':limit,
              'includeOutputs':False,'maxOutputCharsPerItem':chars}
        data=self.tool('read_thread',args)
        # New app versions deliver incoming messages as functionCallOutput.
        # Read bounded output only for previously unseen incoming deliveries;
        # ordinary polling must not repeatedly download every tool result.
        incoming=[(turn.get('id'),item) for turn in data.get('turns',[])
                  for item in turn.get('items',[]) if _is_delivery(item)]
        missing=[(tid,item) for tid,item in incoming
                 if (tid,item.get('id')) not in self.delivery_inputs]
        if missing:
            detail=self.tool('read_thread',dict(args,includeOutputs=True,maxOutputCharsPerItem=8000))
            for turn in detail.get('turns',[]):
                for item in turn.get('items',[]):
                    if _is_delivery(item):
                        key=(turn.get('id'),item.get('id'))
                        value=_input_text(item,self.config['thread_id'])
                        self.delivery_inputs[key]=value
            # A raced/omitted output is not an accepted delivery boundary.
            # Leave it uncached so a subsequent observation can recover it.
            while len(self.delivery_inputs)>64:
                self.delivery_inputs.pop(next(iter(self.delivery_inputs)))
        for tid,item in incoming:
            value=self.delivery_inputs.get((tid,item.get('id')))
            if value:item['_office_delivery_text']=value
        return data

    def send(self,text):
        return self.tool('send_message_to_thread',{'threadId':self.config['thread_id'],'prompt':text})

    def close(self):
        process=getattr(self,'process',None)
        if not process:return
        try:
            process.stdin.close();process.wait(timeout=2)
        except (OSError,subprocess.TimeoutExpired):
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:pass


def _pipes(config):
    seen=set()
    for pipe in (os.environ.get('CODEX_APP_TOOLS_PIPE_PATH'),config.get('pipe_path')):
        if pipe and pipe not in seen:seen.add(pipe);yield pipe
    # Desktop restarts use a new random endpoint. Discover only its named-pipe
    # family; its normal access checks and the pinned thread/workspace still apply.
    try:names=os.listdir('\\\\.\\pipe\\')
    except OSError:return
    for name in names:
        if re.fullmatch(r'codex-app-tools-[0-9a-f-]{36}',name):
            pipe='\\\\.\\pipe\\'+name
            if pipe not in seen:seen.add(pipe);yield pipe


@contextmanager
def connection(config=None,*,reuse=False):
    config=config or configuration()
    if not config:raise BridgeError('현재 Codex 대화 연결이 설정되지 않았어요.',code='configuration_missing')
    client=_borrow(config) if reuse else None
    failures=[]
    for pipe in ([] if client else list(_pipes(config))[:5]):
        try:
            client=DesktopClient(config,pipe);data=client.read(1,400)
            thread=data.get('thread',{})
            if thread.get('id')!=config['thread_id'] or Path(thread.get('cwd','')).resolve()!=ROOT:
                raise BridgeError('연결한 Codex 대화의 작업 폴더가 달라요.',code='context_mismatch')
            break
        except (BridgeError,OSError,ValueError) as error:
            failures.append(_failure(error))
            if client:client.close()
            client=None
    if client is None:
        denied=any(row['code']=='access_denied' for row in failures)
        error=BridgeError('Codex 연결 접근이 실행 환경에서 거부됐어요. 사용자 재승인이 필요한 상태와 다릅니다.' if denied else
            'Codex 연결을 확인하지 못했어요. 연결 진단 원인을 보존했고 요청을 재전송하거나 다른 모델을 실행하지 않았어요.',
            code='access_denied' if denied else (failures[-1]['code'] if failures else 'endpoint_missing'),attempts=failures)
        _set_health(False,error)
        raise error
    _set_health(True)
    try:yield client
    except BaseException as error:
        import task_control
        if not isinstance(error,task_control.Stopped):_set_health(False,error)
        client.close();raise
    else:
        if reuse and client.process.poll() is None:_cache(client,config)
        else:client.close()


def _db(db):
    c=sqlite3.connect(str(db),timeout=15);c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS desktop_chat_requests (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
    return c


def _load(db,ident):
    with closing(_db(db)) as c:
        row=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(ident,)).fetchone()
    return json.loads(row[0]) if row else None


def _save(db,ident,row):
    with closing(_db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(ident,)).fetchone()
        if existing:
            previous=json.loads(existing[0])
            if previous.get('state')=='done':return
            row=dict(previous,**row)
        request_boundary.seal_answer(row)
        c.execute('INSERT OR REPLACE INTO desktop_chat_requests VALUES(?,?)',(ident,json.dumps(row,ensure_ascii=False)))


def marker(ident):return '[AI OFFICE 요청 '+ident+']'


def _text(item):
    if item.get('type')=='userMessage':
        return '\n'.join(row.get('text','') for row in item.get('content',[]) if row.get('type')=='text')
    return item.get('text','') if item.get('type')=='agentMessage' else ''



def _is_delivery(item):
    return (item.get('type')=='functionCallOutput'
            and item.get('namespace')=='codex_app'
            and item.get('name')=='send_message_to_thread')


def _input_text(item,thread_id=None):
    """Only native user input or a same-chat app delivery can anchor a reply.

    Other tool results, quoted request tags and agent prose are never anchors.
    A bounded/truncated input is sufficient only for matching an already saved
    request ID; it supplies no new permission or instructions to the executor.
    """
    if item.get('type')=='userMessage':return _text(item)
    if not _is_delivery(item):return ''
    if item.get('_office_delivery_text'):return item['_office_delivery_text']
    output=item.get('output')
    if not isinstance(output,dict) or not isinstance(output.get('text'),str):return ''
    match=re.match(r'\A<codex_delegation>\s*<source_thread_id>([0-9a-f-]{36})</source_thread_id>\s*<input>([\s\S]*)',output['text'])
    if not match or not thread_id or match[1]!=thread_id:return ''
    text=match[2].split('</input>',1)[0]
    return text if re.match(r'\[AI OFFICE 요청 [0-9a-f]{32}\]',text) else ''


def _without_marker(text,ident):
    return str(text).replace(marker(ident),'').strip()


def response_for(data,ident,anchor=None,input_marker=None):
    """Accept only this delivery boundary, never a prior final or unrelated chat.

    A later same-request user input supersedes the earlier turn. An agent tag
    alone is not evidence of a user request; the old fallback is removed.
    """
    tag=marker(ident)
    def matches(item):
        # Pasted historical tags in native human text are not app deliveries.
        if not _is_delivery(item):return False
        lines=_input_text(item,thread_id).strip().splitlines()
        return bool(lines and lines[0]==tag and
                    (not input_marker or (len(lines)>1 and lines[1]==input_marker)))
    thread_id=data.get('thread',{}).get('id')
    for turn in data.get('turns',[]):
        items=turn.get('items',[])
        starts=[i for i,item in enumerate(items) if matches(item)]
        if not starts:continue
        start=starts[-1]
        # A direct native user message also ends this delivery's segment.
        end=next((i for i in range(start+1,len(items))
                  if items[i].get('type')=='userMessage'
                  or _input_text(items[i],thread_id)),len(items))
        segment=[r for r in items[start+1:end] if r.get('type')=='agentMessage']
        # Untagged prose can be a resurrected old topic. Never attach it to
        # this request merely because it appeared after our delivery.
        messages=[r for r in segment if r.get('text','').lstrip().startswith(tag+'\n')
                  or r.get('text','').strip()==tag]
        invalid_final=any(r.get('phase') in {'final','final_answer'} and r not in messages
                          for r in segment)
        progress='\n\n'.join(_without_marker(r.get('text',''),ident) for r in messages if r.get('phase')=='commentary')[-6000:]
        finals=[_without_marker(r.get('text',''),ident) for r in messages if r.get('phase') in {'final','final_answer'}]
        answer=finals[-1] if finals and turn.get('status')=='completed' else None
        return {'turn_id':turn['id'],'status':turn.get('status'),'progress':progress,
                'draft':finals[-1] if finals and not answer else '', 'answer':answer,
                'invalid_final':invalid_final and turn.get('status')=='completed'}
    return None


def _followup_envelope(ident,record,update):
    revision=record.get('input_revision',0)
    goal=record.get('user_goal','')
    text=marker(ident)+'\n'+record['latest_input_marker']+'\n[현재 업무 원문]\n'+goal[:2400]
    if len(goal)>2400:text+='\n(전체 원문은 read_current로 조회)'
    text+='\n[최신 추가 지시 '+str(revision)+']\n'+update['message']
    import office_relay_context
    text+=office_relay_context.human_input_notice(office_relay_context.human_input_provenance(
        ident,record['thread_id'],update['message'],source='office_relay'))
    text+='\n원래 목표에 이 추가 지시를 반영하세요. 이전 업무로 바꾸지 말고, 문맥 복원 시 office_relay_context.read_current("'+ident+'").'
    text+='\n첫 진행·최종 답변 첫 줄은 '+marker(ident)+'. 별도 게시 시 revision='+str(revision)+'을 전달하세요.'
    return text


def _envelope(context,attachments,instructions):
    tag=marker(context['request_id'])
    guidance,receipt=instructions
    text=tag+'\n[현재 사용자 요청 시작]\n'+context['user_goal']+'\n[현재 사용자 요청 끝]'
    import office_relay_context
    text+=office_relay_context.human_input_notice(office_relay_context.human_input_provenance(
        context['request_id'],receipt['target'].rsplit('|',1)[-1],context['user_goal'],source='office_relay'))
    text+='\n이 요청과 이후 사용자 추가 지시가 현재 작업입니다. 요약·과거 기록·부서 완료 보고를 새 요청으로 바꾸지 마세요. 문맥 복원 시 원문 확인: office_relay_context.read_current("'+context['request_id']+'").'
    text+='\n\n[AI OFFICE 실행 안내]\n'+receipt['reference']
    if guidance:text+='\n\n이번에 추가·갱신된 지침(현재 요청·상위 규칙 우선):\n'+guidance
    text+='\n첫 진행·최종 답변 첫 줄에 '+tag+'를 붙이세요(화면에서 제거됨). 별도 응답은 desktop_chat_bridge.publish_answer("'+context['request_id']+'", 답변), 진행은 publish_progress로 전달할 수 있습니다.'
    if attachments:text+='\n첨부 파일(현재 요청에 연결된 실제 파일):\n'+'\n'.join(attachments)
    text+=request_boundary.current_notice(context)
    text+=__import__('notion_memory').prompt(context['user_goal'])
    text+='\n\n[현재 실행 목표 재확인]\n'+context['user_goal']+'\n'+tag
    return text


def _publish(ident,text,final,revision=None):
    if not re.fullmatch('[0-9a-f]{32}',ident) or not isinstance(text,str) or not text.strip():
        raise BridgeError('응답할 요청과 내용을 확인하세요.')
    if len(text)>60000:raise BridgeError('응답이 너무 큽니다.')
    db=ROOT/'office.db'
    # An exact replay of an already saved final has no new effect.
    with closing(_db(db)) as c:
        prior=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(ident,)).fetchone()
    prior=json.loads(prior[0]) if prior else {}
    if prior.get('state')=='done' and final and prior.get('answer')==text:
        if os.environ.get('CODEX_THREAD_ID')!=prior.get('thread_id'):
            raise BridgeError('이 요청이 연결된 Codex 대화에서만 답변할 수 있어요.')
        return
    # Do the live read before opening a write transaction; don't hold SQLite
    # locks across the desktop IPC round trip.
    import office_current_task
    boundary=office_current_task.require_current(ident)
    with closing(_db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        stored=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(ident,)).fetchone()
        if not stored:raise BridgeError('전달된 에이메스 요청이 없어요.')
        row=json.loads(stored[0]);current=os.environ.get('CODEX_THREAD_ID')
        if current!=row.get('thread_id'):raise BridgeError('이 요청이 연결된 Codex 대화에서만 답변할 수 있어요.')
        if row.get('state') in {'stop_requested','cancelled'}:raise BridgeError('사용자가 중지한 요청입니다.')
        if boundary.get('input_revision',0)!=row.get('input_revision',0):
            raise BridgeError('입력 확인 뒤 추가 지시가 바뀌었습니다. 최신 요청을 다시 확인하세요.')
        if row.get('input_revision',0) and revision!=row['input_revision']:
            raise BridgeError('최신 추가 지시를 확인한 뒤 해당 revision으로 게시하세요.')
        if row.get('state')=='done':
            if final and row.get('answer')==text:return
            raise BridgeError('이미 답변한 요청입니다.')
        row.update({'state':'done','answer':text,'completed_at':time.time()} if final else {'progress':text[-6000:]})
        request_boundary.seal_answer(row)
        c.execute('UPDATE desktop_chat_requests SET data=? WHERE id=?',(json.dumps(row,ensure_ascii=False),ident))


def publish_answer(ident,text,revision=None):_publish(ident,text,True,revision)
def publish_progress(ident,text,revision=None):_publish(ident,text,False,revision)


def register_source_changes(ident,change_ids):
    """Attach actual native edits to this relay's normal idle reload broker.

    Validate saved receipts and present file hashes, not completion prose.
    No source is executed or restored by registration.
    """
    import self_edit,office_current_task
    boundary=office_current_task.require_current(ident)
    if not isinstance(change_ids,list) or not 1<=len(change_ids)<=80:raise BridgeError('실제 소스 변경 ID가 필요해요.')
    rows=[]
    for change_id in change_ids:
        _,item=self_edit._record(change_id)
        path=ROOT/item['path']
        if item.get('deleted_file') or not path.is_file() or self_edit._hash(path.read_bytes())!=item['sha256']:
            raise BridgeError('저장된 소스와 변경 기록이 달라요. 반영하지 않았어요.')
        rows.append({key:item[key] for key in ('change_id','path','kind','sha256','status','direct_project') if key in item})
    import source_activation
    with closing(_db(ROOT/'office.db')) as c,c:
        c.execute('BEGIN IMMEDIATE')
        native=boundary.get('source')=='native'
        if not native:
            department=boundary.get('source')=='department_assignment'
            project=boundary.get('source') in {'project_panel','legacy_project_panel'}
            table='project_panel_sends' if project else 'office_department_tasks' if department else 'desktop_chat_requests'
            saved=c.execute('SELECT data'+(',thread_id,state' if project else '')+' FROM '+table+' WHERE id=?',(ident,)).fetchone()
            record=json.loads(saved[0]) if saved else {}
            states={'dispatching','working'} if department else {'sent'} if project else {'sent','sending'}
            thread=saved[1] if project and saved else record.get('thread_id')
            state=saved[2] if project and saved else record.get('state')
            if thread!=os.environ.get('CODEX_THREAD_ID') or state not in states:
                raise BridgeError('현재 연결된 실행 요청에만 변경을 등록할 수 있어요.')
            changes={row['path']:row for row in record.get('source_changes',[])}
            changes.update({row['path']:row for row in rows})
            record.update(source_changes=list(changes.values()),source_activation='awaiting_idle_reload',
                          source_input_revision=boundary.get('input_revision'),source_activation_owner='native-ledger')
            c.execute('UPDATE '+table+' SET data=? WHERE id=?',(json.dumps(record,ensure_ascii=False),ident))
        else:
            if boundary['thread_id']!=os.environ.get('CODEX_THREAD_ID'):
                raise BridgeError('실제 현재 대화에서만 소스를 등록할 수 있어요.')
            changes={row['path']:row for row in rows}
        activation=source_activation.register(c,ident,boundary,list(changes.values()))
    return {'saved':True,'active':activation['state']=='active','changes':len(activation['changes']),
            'state':activation['state'],'owner':'native-ledger'}



def relay(db,context,attachments=(),timeout=3600):
    import chat_stream,task_control,execution_router
    config=configuration();ident=context['request_id']
    queue_started=time.monotonic()
    # One desktop conversation is sequential, even if office channels differ.
    while not _serial.acquire(timeout=.2):
        task_control.checkpoint();chat_stream.publish('waiting','에이메스가 앞선 작업을 마무리하고 있어요~')
    queue_ms=round((time.monotonic()-queue_started)*1000)
    execution_router.record_time('desktop_queue',queue_ms)
    try:
        record=_load(db,ident) or {'state':'new','thread_id':config.get('thread_id'),'created':time.time(),
                                  'user_message_id':context.get('user_message_id')}
        if record.get('state')=='done':return record['answer'],record
        record.setdefault('user_goal',context['user_goal'])
        record.setdefault('attachments',list(attachments))
        record.setdefault('input_revision',0)
        record['bridge_revision']=BRIDGE_REVISION
        connect_started=time.monotonic()
        with connection(config,reuse=True) as client:
            connection_ms=round((time.monotonic()-connect_started)*1000)
            execution_router.record_time('desktop_connection',connection_ms)
            if record['state']=='new':
                import office_relay_context
                context_started=time.monotonic()
                instructions=office_relay_context.prepare(db,config['thread_id'],context['user_goal'])
                prompt=_envelope(context,attachments,instructions)
                import department_manager
                department_text,department_receipt=department_manager.context(db,config['thread_id'])
                prompt+=department_text
                context_ms=round((time.monotonic()-context_started)*1000)
                execution_router.record_time('desktop_context',context_ms)
                record['prompt_context']={**instructions[1]['stats'],'prompt_chars':len(prompt),
                                          'user_chars':len(context['user_goal'])}
                record.update(state='sending',connection_ms=connection_ms,context_ms=context_ms,queue_ms=queue_ms);_save(db,ident,record)
                # Persist before delivery; an uncertain send is never automatically resent.
                send_started=time.monotonic();client.send(prompt)
                record['send_ms']=round((time.monotonic()-send_started)*1000)
                execution_router.record_time('desktop_send',record['send_ms'])
                record.update(state='sent',sent_at=time.time());_save(db,ident,record)
                try:department_manager.acknowledge(db,department_receipt)
                except (OSError,ValueError,sqlite3.Error):pass
                try:office_relay_context.acknowledge(db,instructions[1])
                except (OSError,ValueError,sqlite3.Error):
                    # Transport succeeded. A receipt failure must never resend it.
                    record['instruction_receipt_pending']=True;_save(db,ident,record)
            chat_stream.publish('working','에이메스가 작업을 준비하고 있어요~')
            response_started=time.monotonic();deadline=response_started+timeout;last=None
            while time.monotonic()<deadline:
                recorded=_load(db,ident) or {}
                if recorded.get('state')=='done':
                    recorded['response_wait_ms']=round((time.monotonic()-response_started)*1000)
                    execution_router.record_time('desktop_response_wait',recorded['response_wait_ms']);_save(db,ident,recorded)
                    return recorded['answer'],{k:v for k,v in recorded.items() if k!='answer'}
                if recorded.get('progress') and recorded['progress']!=last:
                    last=recorded['progress'];chat_stream.publish('working','에이메스가 열심히 작업 중이에요~',last)
                control=task_control.current()
                if control and control.stopped.is_set():
                    client.send(marker(ident)+'\n사용자가 에이메스에서 작업 중지를 요청했습니다. 해당 요청의 추가 작업을 멈추고 이미 실행한 내용과 중지 상태를 보고하세요.')
                    record.update(state='stop_requested');_save(db,ident,record)
                    context['desktop_stop_requested']=True;task_control.checkpoint()
                if control:
                    for update in control.inputs():
                        record['input_revision']=record.get('input_revision',0)+1
                        record['latest_input_marker']='[AI OFFICE 추가 지시 '+update['id']+']'
                        record['input_delivery_state']='sending'
                        _save(db,ident,record)
                        client.send(_followup_envelope(ident,record,update))
                        record['input_delivery_state']='sent'
                        _save(db,ident,record)
                read_started=time.monotonic()
                data=client.read(3)
                read_ms=round((time.monotonic()-read_started)*1000)
                execution_router.record_time('desktop_observation_read',read_ms)
                record['observation_reads']=record.get('observation_reads',0)+1
                record['observation_read_ms']=record.get('observation_read_ms',0)+read_ms
                import desktop_chat_mirror
                try:desktop_chat_mirror.sync(db,data)
                except (OSError,ValueError,sqlite3.Error):pass
                found=response_for(data,ident,record.get('turn_id'),record.get('latest_input_marker'))
                if found:
                    if found.get('invalid_final'):
                        raise BridgeError('현재 요청 표식이 없는 답변을 분리했습니다. '
                                          '과거 답변을 현재 완료로 표시하거나 자동 재실행하지 않았습니다.')
                    if (found['progress'] or found['draft'] or found['answer']) and not record.get('first_visible_observed_at'):
                        # Observation delay is not server TTFT or pure model generation time.
                        record['first_visible_observed_at']=time.time()
                        record['first_visible_observed_ms']=max(0,round((time.time()-record.get('sent_at',time.time()))*1000))
                        execution_router.record_time('desktop_first_visible_observed',record['first_visible_observed_ms'])
                        _save(db,ident,record)
                    if record.get('turn_id')!=found['turn_id']:
                        record['turn_id']=found['turn_id'];_save(db,ident,record)
                    if found['progress'] and found['progress']!=last:
                        last=found['progress'];chat_stream.publish('working','에이메스가 열심히 작업 중이에요~',last)
                    if found['draft'] and found['draft']!=last:
                        last=found['draft'];chat_stream.publish('draft','에이메스가 답변을 쓰고 있어요~',last)
                    if found['answer']:
                        record['response_wait_ms']=round((time.monotonic()-response_started)*1000)
                        execution_router.record_time('desktop_response_wait',record['response_wait_ms'])
                        record.update(state='done',answer=found['answer'],completed_at=time.time());_save(db,ident,record)
                        record=_load(db,ident) or record
                        return found['answer'],{k:v for k,v in record.items() if k!='answer'}
                    if found['status'] in {'failed','interrupted'}:
                        raise BridgeError('Codex 대화의 작업이 중단됐어요. 같은 요청을 다시 보내지 않았어요.')
                age=timeout-(deadline-time.monotonic())
                time.sleep(.2 if age<30 else .5 if age<120 else 1)
        raise BridgeError('Codex 답변 대기시간을 넘겼어요. 전달한 작업을 다시 실행하지 않았어요.')
    finally:_serial.release()
