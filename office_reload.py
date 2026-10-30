"""Apply requested source updates at a quiet point; never restore private data."""
from contextlib import contextmanager
import json
import ast
import hashlib
import inspect
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'local_backups' / 'self-edit' / 'reload-status.json'
HISTORY=STATE.parent/'reload-history'
REVISION='2026-10-29-scoped-drain-load-proof-v2'
_lock = threading.RLock()
_depth=threading.local()
_server=None
_loaded=None
_active = 0
_draining = False
_closing = False
_thread = None


@contextmanager
def _file_guard(name='state',timeout=10):
    """Cross-process metadata/restart ownership; never locks business execution."""
    HISTORY.mkdir(parents=True,exist_ok=True)
    with (HISTORY/(name+'.lock')).open('a+b') as stream:
        stream.seek(0,2)
        if not stream.tell():stream.write(b'0');stream.flush()
        deadline=time.monotonic()+timeout
        while True:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:raise TimeoutError('Reload metadata owner busy')
                time.sleep(.05)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


@contextmanager
def _state_guard():
    with _lock:
        if getattr(_depth,'held',False):yield;return
        with _file_guard():
            _depth.held=True
            try:yield
            finally:_depth.held=False


def _read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}


def ticket_record(ident):
    if not re.fullmatch('[0-9a-f]{32}',str(ident)):return {}
    saved=_read(HISTORY/(ident+'.json'))
    if saved:return saved
    legacy=_read(STATE)
    return legacy if legacy.get('id')==ident else {}


def _save(data,*,select=False,archive_only=False):
    import execution_receipts
    with _state_guard():
        ident=data.get('id')
        if not re.fullmatch('[0-9a-f]{32}',str(ident)):raise ValueError('Invalid reload ticket')
        prior=ticket_record(ident);current=_read(STATE)
        # Successful historical evidence is immutable to stale drain failures.
        if prior.get('state')=='active' and data.get('state')!='active':return prior
        value={k:v for k,v in data.items() if k not in {'pid','runtime','loaded_boot'}}
        events=list(prior.get('events',[]))
        if not events or any(prior.get(k)!=value.get(k) for k in ('state','changes','launch_nonce')):
            events.append({'state':value.get('state'),'at':time.time(),'pid':os.getpid(),'error_type':value.get('error_type')})
        value.update(events=events[-128:],updated_at=time.time())
        execution_receipts.write_json(HISTORY/(ident+'.json'),value)
        if not archive_only and (select or not current or current.get('id')==ident):
            if current.get('id') and current.get('id')!=ident and not (HISTORY/(current['id']+'.json')).exists():
                execution_receipts.write_json(HISTORY/(current['id']+'.json'),dict(current,legacy_imported_at=time.time()))
            execution_receipts.write_json(STATE,value)
        return value


def _code_value(value):
    if isinstance(value,types.CodeType):
        return [value.co_code.hex(),[_code_value(v) for v in value.co_consts],value.co_names,value.co_varnames,
                value.co_argcount,value.co_kwonlyargcount,getattr(value,'co_posonlyargcount',0),
                value.co_flags,value.co_freevars,value.co_cellvars,getattr(value,'co_exceptiontable',b'').hex()]
    if isinstance(value,(tuple,list)):return [_code_value(v) for v in value]
    if isinstance(value,frozenset):return sorted((_code_value(v) for v in value),key=repr)
    if isinstance(value,bytes):return ['bytes',value.hex()]
    return [type(value).__name__,repr(value)]


def _loaded_python(path,raw):
    """Compare executing definitions/literal constants with the pinned source."""
    module=next((m for m in list(sys.modules.values()) if m and getattr(m,'__file__',None)
                 and Path(m.__file__).resolve()==path.resolve()),None)
    if module is None:return {'matched':False,'reason':'module_not_loaded'}
    compiled=compile(raw,str(path),'exec',dont_inherit=True);proof=[]
    def check(namespace,code):
        for child in code.co_consts:
            if not isinstance(child,types.CodeType) or child.co_name.startswith('<'):continue
            actual=vars(namespace).get(child.co_name)
            if isinstance(actual,(staticmethod,classmethod)):actual=actual.__func__
            if isinstance(actual,property):actual=actual.fget
            if isinstance(actual,type):
                if not check(actual,child):return False
            else:
                actual=inspect.unwrap(actual) if callable(actual) else actual
                if not hasattr(actual,'__code__') or _code_value(actual.__code__)!=_code_value(child):return False
                proof.append([child.co_name,_code_value(actual.__code__)])
        return True
    if not check(module,compiled):return {'matched':False,'reason':'loaded_definition_mismatch'}
    tree=ast.parse(raw);runtime_writes=set()
    for scope in ast.walk(tree):
        if not isinstance(scope,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        global_names={name for statement in ast.walk(scope) if isinstance(statement,ast.Global) for name in statement.names}
        for statement in ast.walk(scope):
            targets=statement.targets if isinstance(statement,ast.Assign) else [statement.target] if isinstance(statement,(ast.AugAssign,ast.AnnAssign)) else []
            runtime_writes.update(target.id for target in targets if isinstance(target,ast.Name) and target.id in global_names and target.id.startswith('_'))
    constants=[];runtime_states=[]
    for node in tree.body:
        if not isinstance(node,ast.Assign):continue
        try:value=ast.literal_eval(node.value)
        except (ValueError,TypeError,SyntaxError):continue
        for target in node.targets:
            if isinstance(target,ast.Name) and target.id.isupper():
                if target.id in runtime_writes:
                    runtime_states.append({'name':target.id,'basis':'private global assigned by executing function','type':type(getattr(module,target.id,None)).__name__})
                    continue
                if getattr(module,target.id,object())!=value:return {'matched':False,'reason':'loaded_constant_mismatch'}
                constants.append([target.id,_code_value(value)])
    return {'matched':bool(proof),'module':module.__name__,'definitions':len(proof),
            'code_sha256':hashlib.sha256(json.dumps([proof,constants],sort_keys=True).encode()).hexdigest(),
            'runtime_states':runtime_states}


def bind_server(server):
    global _server
    _server=server
    import office_ui_assets
    office_ui_assets.bind(server)
    HISTORY.mkdir(parents=True,exist_ok=True)
    import execution_receipts
    execution_receipts.write_json(HISTORY/'serving-boot.json',{'pid':os.getpid(),'port':server.server_port,
        'state':'awaiting_serving_proof','nonce':os.environ.get('AI_OFFICE_RELOAD_NONCE'),'observed_at':time.time()})


def _inherited_changes(required):
    """Unchanged active receipts are rechecked in this real serving process."""
    import sqlite3
    wanted={r['path'] for r in required};inherited={}
    try:
        with sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=3) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_source_activations'").fetchone():return []
            rows=c.execute("SELECT data FROM office_source_activations WHERE json_extract(data,'$.state')='active' ORDER BY updated DESC").fetchall()
        for raw, in rows:
            row=json.loads(raw)
            for receipt in row.get('current_changes') or row.get('changes',[]):
                if receipt['path'] in wanted or receipt['path'] in inherited:continue
                path=ROOT/receipt['path']
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==receipt['sha256']:
                    inherited[receipt['path']]=dict(receipt,inherited_from_request=row['id'])
    except (OSError,ValueError,sqlite3.Error):return []
    return list(inherited.values())


def _capture_load(runtime):
    global _loaded
    if _loaded is not None:return _loaded
    ident=os.environ.get('AI_OFFICE_RELOAD_TICKET');nonce=os.environ.get('AI_OFFICE_RELOAD_NONCE')
    if _server is None or not ident or not re.fullmatch('[0-9a-f]{32}',nonce or ''):return None
    ticket=ticket_record(ident)
    if ticket.get('launch_nonce')!=nonce:return None
    verified=[];failures=[];inherited_failures=[]
    required=ticket.get('verified_changes',[]);required_ids={r['change_id'] for r in required}
    for change in [*required,*_inherited_changes(required)]:
        path=ROOT/change['path']
        try:
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=change['sha256']:raise ValueError('source_changed')
            if path.suffix=='.py':code=_loaded_python(path,raw)
            else:
                import office_ui_assets
                if office_ui_assets._valid(change['path']):
                    served=office_ui_assets.read(change['path'])
                    code={'matched':hashlib.sha256(served).hexdigest()==change['sha256'],'mode':'served_ui_source',
                          'reason':'ui_source_not_published','generation':office_ui_assets.generation(),'ui_verified':False}
                else:code={'matched':True,'mode':'saved_source_only','ui_verified':False}
            if not code['matched']:raise ValueError(code['reason'])
            verified.append(dict(change,loaded_code=code))
        except (OSError,ValueError,TypeError) as error:
            (failures if change['change_id'] in required_ids else inherited_failures).append({'change_id':change['change_id'],'reason':str(error)[:120]})
    state='loaded' if verified and not failures else 'load_mismatch'
    if os.environ.get('AI_OFFICE_RELOAD_ROLLBACK')=='1':state='rolled_back';verified=[]
    proof={'ticket_id':ident,'nonce':nonce,'pid':os.getpid(),'port':_server.server_port,'state':state,
           'observed_at':time.time(),'runtime':runtime,'changes':verified,'failures':failures,'proof_revision':REVISION,
           'required_change_ids':sorted(required_ids),'inherited_failures':inherited_failures,
           'ui_refresh_required':[r['change_id'] for r in verified if r.get('kind')!='backend']}
    import execution_receipts
    with _state_guard():
        folder=HISTORY/'boots';folder.mkdir(exist_ok=True)
        target=folder/(nonce+'.json')
        if target.exists():proof=_read(target)
        else:execution_receipts.write_json(target,proof)
        execution_receipts.write_json(HISTORY/'serving-boot.json',proof)
    _loaded=proof
    return proof


def activation_evidence(changes,ticket_id=None):
    """Observe durable serving-process receipts; never enqueue a restart."""
    wanted={r['change_id']:r for r in changes}
    candidates=[]
    serving=_read(HISTORY/'serving-boot.json')
    if serving:
        # A newer actual server supersedes old boot proofs, including when its
        # own proof is not ready. A previous PID is never current evidence.
        archived=_read(HISTORY/'boots'/((serving.get('nonce') or '')+'.json'))
        if archived.get('pid')==serving.get('pid') and archived.get('nonce')==serving.get('nonce'):candidates=[archived]
    elif ticket_id:
        ticket=ticket_record(ticket_id)
        if ticket.get('loaded_proof'):candidates.append(ticket['loaded_proof'])
    folder=HISTORY/'boots'
    if not serving and folder.exists():candidates.extend(_read(p) for p in sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100])
    for proof in candidates:
        actual={r['change_id']:r for r in proof.get('changes',[])}
        if proof.get('state')!='loaded' or not wanted or not wanted.keys()<=actual.keys():continue
        if all(actual[k].get('sha256')==v.get('sha256') and actual[k].get('path')==v.get('path') and actual[k].get('loaded_code',{}).get('matched') for k,v in wanted.items()):
            return proof
    return None


def snapshot():
    with _lock:
        try:
            data = json.loads(STATE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {'id': '', 'state': 'idle', 'message': ''}
        import desktop_chat_bridge as bridge,assistant_tools as tools,report_automation as report
        import office_current_task,department_tasks,office_context_hook
        import office_relay_context,department_manager,ego_report_session,execution_receipts,project_questions,skill_runtime
        import execution_corrections,routine_runtime,office_routines,office_feedback,question_reply_context
        import department_work,department_knowledge,department_runtime
        import office_ui_assets
        value={**data, 'pid':os.getpid(), 'runtime':{
            'reload_revision':REVISION,
            'bridge_revision':getattr(bridge,'BRIDGE_REVISION',None),
            'current_task_revision':office_current_task.REVISION,
            'department_revision':department_tasks.RUNTIME_REVISION,
            'direct_work_revision':department_work.REVISION,
            'work_map_revision':department_knowledge.REVISION,
            'work_runtime_revision':department_runtime.REVISION,
            'ui_assets_revision':office_ui_assets.REVISION,
            'served_ui_generation':office_ui_assets.generation(),
            'relay_context_revision':office_relay_context.RUNTIME_REVISION,
            'department_manager_revision':department_manager.RUNTIME_REVISION,
            'ego_adapter_revision':ego_report_session.RUNTIME_REVISION,
            'receipt_revision':execution_receipts.REVISION,
            'question_revision':project_questions.REVISION,
            'skill_runtime_revision':skill_runtime.REVISION,
            'correction_revision':execution_corrections.REVISION,
            'registered_runtime_revision':routine_runtime.REVISION,
            'routine_revision':office_routines.RUNTIME_REVISION,
            'request_client_revision':office_feedback.REQUEST_CLIENT_REVISION,
            'question_reply_revision':question_reply_context.REVISION,
            'hook_callback_records':len(list(office_context_hook.DATA.glob('*.last-event.json'))),
            'tools_revision':getattr(tools,'RUNTIME_REVISION',None),
            'report_engine':getattr(report,'REPORT_ENGINE',None)}}
        proof=_capture_load(value['runtime'])
        if proof:value['loaded_boot']=proof
        return value


@contextmanager
def action(readonly=False):
    global _active
    with _lock:
        # Waiting publication is not a service outage. readonly is retained
        # for old callers; auth/effect permissions remain in the actual route.
        admitted = not _closing
        if admitted:_active += 1
    try:
        yield admitted
    finally:
        if admitted:
            with _lock:_active -= 1


def draining():
    with _lock:
        return _draining


def prepare(changes, request_id, existing_ticket=None):
    global _draining
    changes = [dict(row) for row in changes if isinstance(row, dict)
               and re.fullmatch('[0-9a-f]{32}', str(row.get('change_id', '')))]
    if not changes:
        return None
    changes=verify_changes(changes)
    backend = any(row.get('kind') == 'backend' for row in changes)
    data = {'id': uuid.uuid4().hex, 'state': 'queued' if backend else 'saved',
            'mode': 'restart' if backend else 'refresh', 'request_id': request_id,
            'previous_pid': os.getpid(), 'request_ids':[request_id], 'changes': [row['change_id'] for row in changes],
            'verified_changes':changes,
            'message': '소스 저장됨 · 진행 중 작업 종료 후 자동 반영' if backend else '소스 저장됨 · 화면을 새로 반영합니다.'}
    with _state_guard():
        prior=_read(STATE)
        deferred=ticket_record(existing_ticket)
        if (deferred.get('state')=='deferred' and deferred.get('request_id')==request_id
                and set(deferred.get('changes',[]))=={r['change_id'] for r in changes}):
            data.update(id=deferred['id'])
        if prior.get('state') in {'queued','restarting','active'} and request_id in prior.get('request_ids',[]) and all(row['change_id'] in prior.get('changes',[]) for row in changes):
            return prior
        # Pre-admitted requests can finish while another request drains the app.
        # Keep one ticket and one ordered rollback batch for all their changes.
        if prior.get('state')=='queued' and prior.get('previous_pid')==os.getpid():
            prior['changes']=list(dict.fromkeys(prior['changes']+data['changes']))
            prior['request_ids']=list(dict.fromkeys(prior.get('request_ids',[])+[request_id]))
            prior['verified_changes']=verify_changes([{'change_id':i} for i in prior['changes']])
            data=prior
        elif _closing or (prior.get('state')=='restarting' and prior.get('previous_pid')==os.getpid()) or (_thread and _thread.is_alive()):
            data.update(state='deferred',message='진행 중인 재시작 결과를 기다립니다. 새 티켓이 기존 티켓을 덮지 않습니다.')
            return _save(data)
        _save(data,select=True)
        if data.get('mode')=='refresh':
            import office_ui_assets
            data['ui_generation']=office_ui_assets.publish(changes)
            data['message']='새 화면 버전이 준비됐습니다. 현재 화면은 유지하고 사용자가 적용합니다.'
            _save(data)
    return data


def schedule(server, ticket):
    global _thread
    if not ticket or ticket.get('mode') != 'restart' or ticket.get('state')!='queued' or ticket.get('previous_pid')!=os.getpid():
        return
    with _lock:
        if _thread and _thread.is_alive():
            return
        _thread = threading.Thread(target=_wait_and_restart, args=(server, ticket), daemon=True)
        _thread.start()


def verify_changes(changes):
    """Only existing saved receipts whose bytes still match can enter reload."""
    import self_edit
    verified=[]
    for row in changes:
        _,saved=self_edit._record(row['change_id'])
        path=ROOT/saved['path']
        if not path.is_file() or self_edit._hash(path.read_bytes())!=saved['sha256']:
            raise ValueError('등록 뒤 변경된 소스가 있어 반영을 보류합니다.')
        verified.append({key:saved[key] for key in ('change_id','path','kind','sha256','status','direct_project') if key in saved})
    return verified


def _wait_and_restart(server, ticket):
    global _draining, _closing
    import model_requests
    import organizer_policy
    import scanner
    import desktop_judgment
    import source_activation
    try:
        # Prevent a new automatic organizer pass; the in-flight pass may finish.
        while True:
            with _lock:
                idle = _active == 0
            if idle and model_requests.is_idle() and not organizer_policy.RUNNING and scanner.status().get('status') != 'running' and not desktop_judgment.status().get('running') and source_activation.native_idle(ticket):
                with _lock:
                    if _active==0:
                        _closing=True;_draining=True
                        organizer_policy.STOP.set()
                        latest=ticket_record(ticket['id'])
                        if latest.get('state')!='queued' or _read(STATE).get('id')!=ticket['id']:
                            _closing=False;_draining=False;return
                        ticket=latest
                        break
            time.sleep(0.25)
        # Sources and user intent may change while other server work drains.
        # Revalidate immediately before the actual restart, not only at enqueue.
        verify_changes([{'change_id':ident} for ident in ticket['changes']])
        import source_activation
        source_activation.validate_ticket(ticket)
        with organizer_policy.LOCK:
            if organizer_policy.RUNNING:
                raise RuntimeError('폴더 정리 작업이 아직 실행 중입니다.')
            with _lock:
                ticket = dict(ticket, state='restarting', message='변경한 기능을 반영하기 위해 재시작합니다.')
                _save(ticket)
            environment = dict(os.environ, AI_OFFICE_NO_BROWSER='1', PYTHONIOENCODING='utf-8')
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--restart', ticket['id']],
                             cwd=str(ROOT), env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            server.shutdown()
            server.server_close()
    except Exception as error:
        with _lock:
            _draining = False
            _closing = False
            _save(dict(ticket, state='failed',error_type=type(error).__name__,message='소스는 저장됐지만 자동 재시작에 실패했습니다. 실제 실패 근거를 보존했습니다.'))
        organizer_policy.ensure_worker()


def _health():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8765/api/work/reload-status', timeout=5) as response:
            return json.load(response)
    except Exception:
        return None


def _launch(ticket,rollback=False):
    nonce=uuid.uuid4().hex
    ticket.update(launch_nonce=nonce,verified_changes=verify_changes([{'change_id':i} for i in ticket['changes']]) if not rollback else ticket['verified_changes'])
    _save(ticket)
    environment = dict(os.environ, AI_OFFICE_NO_BROWSER='1', PYTHONIOENCODING='utf-8',
                       AI_OFFICE_RELOAD_TICKET=ticket['id'],AI_OFFICE_RELOAD_NONCE=nonce,
                       AI_OFFICE_RELOAD_ROLLBACK='1' if rollback else '0')
    log = STATE.parent / 'reload.log'
    with log.open('ab') as stream:
        process=subprocess.Popen([sys.executable, '-u', str(ROOT / 'app.py')], cwd=str(ROOT),
                                env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    ticket.update(launch_pid=process.pid);_save(ticket)
    return process


def _restart(ticket_id):
    if not re.fullmatch('[0-9a-f]{32}', ticket_id):
        return
    try:
        with _file_guard('restart-owner',timeout=3):_restart_owned(ticket_id)
    except TimeoutError:
        data=ticket_record(ticket_id)
        if data:_save(dict(data,state='activation_unconfirmed',error_type='restart_owner_busy',message='다른 재시작 소유자가 있어 실행하지 않았습니다. 관찰로만 대조합니다.'))
    except Exception as error:
        data=ticket_record(ticket_id)
        if data:_save(dict(data,state='failed',error_type=type(error).__name__,message='재시작 확인 단계에서 실패했습니다. 원래 티켓을 보존하고 자동 재실행하지 않습니다.'))


def _restart_owned(ticket_id):
    # Legacy bootstrap may have only the exact current ticket. Import that
    # record, never infer an older overwritten ticket or retry a failed one.
    data=ticket_record(ticket_id)
    if data.get('state')=='queued':
        legacy=_read(STATE)
        if legacy.get('id')==ticket_id and legacy.get('state')=='restarting' and legacy.get('changes')==data.get('changes'):
            data=dict(data,**{k:v for k,v in legacy.items() if k not in {'pid','runtime'}})
    if data.get('state')!='restarting':return
    data['verified_changes']=verify_changes([{'change_id':i} for i in data['changes']])
    _save(data)
    old_pid = data.get('previous_pid')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        health = _health()
        if health is None:
            try:
                with socket.create_connection(('127.0.0.1',8765),timeout=1):
                    pass
            except OSError:
                break
        if health and health.get('pid') != old_pid:
            proof=activation_evidence(data['verified_changes'])
            _save(dict(data,state='active' if proof else 'activation_unconfirmed',loaded_proof=proof,
                       loaded_pid=proof.get('pid') if proof else None,message='실제 로드 근거를 대조했습니다.' if proof else '다른 서버가 실행 중입니다. 종료하거나 재실행하지 않았습니다.'))
            return
        time.sleep(0.25)
    else:
        _save(dict(data, state='failed', message='기존 앱 종료를 확인하지 못했습니다. 소스는 저장됐습니다.'))
        return
    process = _launch(data)
    for attempt in range(2):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            health = _health()
            if health and health.get('pid') == process.pid:
                proof=health.get('loaded_boot') or {}
                if proof.get('ticket_id')!=ticket_id or proof.get('nonce')!=data['launch_nonce']:
                    time.sleep(.3);continue
                if attempt==0 and proof.get('state')=='loaded' and {r['change_id'] for r in data['verified_changes']}.issubset({r['change_id'] for r in proof['changes']}):
                    _save(dict(data,state='active',loaded_proof=proof,loaded_pid=process.pid,message='실제 서버 PID·로드 코드·리비전·변경 범위를 확인했습니다.'))
                else:
                    _save(dict(data,state='rolled_back' if attempt else 'load_mismatch',loaded_proof=proof,loaded_pid=process.pid,message='서버는 실행 중이나 요청 수정본의 로드 성공으로 확인하지 않았습니다.'))
                return
            if process.poll() is not None:
                break
            time.sleep(0.3)
        if process.poll() is None:
            # Never terminate an instance that might already be doing real work.
            _save(dict(data, state='failed', message='실행 상태를 확인하지 못했습니다. 소스와 작업 기록을 유지했습니다.'))
            return
        if attempt == 0:
            import self_edit
            restoration = self_edit.restore_changes(data['changes'])
            if restoration.get('conflicts'):
                _save(dict(data, state='failed', message='다른 소스 편집이 있어 자동 복원을 멈췄습니다. 백업과 작업 기록을 유지했습니다.'))
                return
            process = _launch(data,rollback=True)
    _save(dict(data, state='failed', message='앱을 자동 복구하지 못했습니다. 소스 백업과 개인 자료를 유지했습니다.'))


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--restart':
    _restart(sys.argv[2])
elif __name__=='__main__' and len(sys.argv)==3 and sys.argv[1]=='--confirm':
    import source_activation
    source_activation.confirm_bootstrap(sys.argv[2])
