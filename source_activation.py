"""Finish registered source publication without replaying an agent's work.

The ledger contains only explicit, current-input registrations. It never
imports historical pending tasks or starts a model, browser, or business job.
"""
import atexit
from contextlib import closing
import hashlib
import json
import sqlite3
import threading
import time

REVISION='2026-10-29-publisher-scoped-idle-v6'
TABLE='office_source_activations'
PENDING={'awaiting_completion','boundary_unavailable','awaiting_reload_slot','queued'}
OBSERVE_ONLY={'activation_unconfirmed','activation_failed'}
_worker=None
_guard=threading.RLock()
_stop=threading.Event()
_status={'last_check_at':None,'last_error':None}
_idle_cache={'at':0,'idle':False}


def _ensure(c):
    c.execute('CREATE TABLE IF NOT EXISTS '+TABLE+' (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)')


def _goal(value):
    followups=[{k:r.get(k) for k in ('id','kind','message')} for r in value.get('followups',[])]
    return hashlib.sha256(json.dumps([value.get('user_message'),followups],
        ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def register(c,ident,boundary,changes):
    if not boundary.get('current') or not boundary.get('can_execute') or boundary.get('stopped'):
        raise ValueError('Source registration requires the actual active input')
    if not boundary.get('turn_id') or not boundary.get('input_id'):
        raise ValueError('Source registration requires an observed turn and input')
    _ensure(c)
    old=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(ident,)).fetchone()
    old=json.loads(old[0]) if old else {}
    rows={r['path']:r for r in old.get('changes',[])}
    rows.update({r['path']:r for r in changes})
    same=(old.get('changes')==list(rows.values()) and old.get('goal_digest')==_goal(boundary)
          and old.get('input_id')==boundary['input_id'])
    if same and old.get('state') in {'queued','active','refresh_required'}:return old
    value={'id':ident,'thread_id':boundary['thread_id'],'turn_id':boundary['turn_id'],
        'input_id':boundary['input_id'],'goal_digest':_goal(boundary),'changes':list(rows.values()),
        'state':'awaiting_completion','reason':'실제 작업 종료를 확인한 뒤 서버에 반영합니다.',
        'revision':REVISION,'registered_at':time.time()}
    value['corrections']=old.get('corrections',[])
    value['retains']=old.get('retains',[])
    value['retained_sources']=old.get('retained_sources',[])
    if boundary.get('source')=='department_assignment':
        value['report_gate']=old.get('report_gate') or {'state':'waiting','deadline':time.time()+1800}
    source=boundary.get('source')
    value['record_table']=None if source=='native' else 'project_panel_sends' if source in {'project_panel','legacy_project_panel'} else 'office_department_tasks' if source=='department_assignment' else 'desktop_chat_requests'
    import department_work,department_tasks
    owner=department_work.owner_for_input(c,boundary['thread_id'],ident)
    if not owner and source in {'native','project_panel'}:
        db=c.execute('PRAGMA database_list').fetchone()[2]
        meta=department_work.registered(db).get(boundary['thread_id'])
        if meta:owner=department_work._upsert(c,meta,boundary['user_message'],ident,
            {'id':boundary['turn_id'],'status':'inProgress'},{'id':boundary['input_id']},source,declared=True)
    if owner:
        owner.setdefault('origin','assigned' if source=='department_assignment' else 'direct')
        owner.setdefault('goal',boundary['user_message'][:4000])
        value.update(record_table='office_department_tasks',record_id=owner['id'])
        value['report_gate']=old.get('report_gate') or {'state':'waiting','deadline':time.time()+1800}
        owned={r['path']:r for r in owner.get('source_changes',[])};owned.update(rows)
        owner.update(source_changes=list(owned.values()),source_activation='awaiting_completion',source_activation_owner='native-ledger',
                     source_activation_request_id=ident,verification={'level':'source_saved','business_success':'not_independently_verified'})
        department_tasks._put(c,owner)
    c.execute('INSERT OR REPLACE INTO '+TABLE+' VALUES(?,?,?)',
        (ident,json.dumps(value,ensure_ascii=False),time.time()))
    return value


def retain_corrections(db,request_id,origin_id,correction_ids,review_ref):
    """Explicit maintenance review, never infer semantic retention from a hash."""
    import office_current_task
    office_current_task.require_current(request_id)
    with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        target=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(request_id,)).fetchone()
        original=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(origin_id,)).fetchone()
        if not target or not original:raise ValueError('실제 등록 원장이 필요합니다.')
        value=json.loads(target[0]);origin=json.loads(original[0])
        known={r['id'] for r in origin.get('corrections',[])}
        if not correction_ids or not set(correction_ids)<=known:raise ValueError('원래 교정 식별자가 다릅니다.')
        current={r['path']:r for r in value['changes']}
        if any(r['path'] not in current for r in origin['changes']):raise ValueError('원래 수정 범위의 현재 소스 영수증이 모두 필요합니다.')
        retained={'origin_request_id':origin_id,'correction_ids':list(correction_ids),
                  'original_changes':origin['changes'],'current_changes':[current[r['path']] for r in origin['changes']],
                  'review_ref':review_ref,'business_validation':'unchanged; not re-executed'}
        prior=[r for r in value.get('retains',[]) if r['origin_request_id']!=origin_id]
        value['retains']=[*prior,retained]
        c.execute('UPDATE '+TABLE+' SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),request_id))
    return {'retained_corrections':len(correction_ids),'requires_verified_load':True}


def continuity_evidence(db,origin,correction_id=None):
    import office_reload
    with closing(sqlite3.connect(str(db),timeout=3)) as c:
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM '+TABLE+' ORDER BY updated DESC LIMIT 200')]
    for candidate in rows:
        if candidate.get('state')!='active':continue
        if correction_id is None and not origin.get('corrections'):
            for retained in candidate.get('retained_sources',[]):
                if retained['origin_request_id']!=origin['id']:continue
                proof=office_reload.activation_evidence(retained['current_changes'],candidate.get('reload_id'))
                if proof:return {'proof':proof,'changes':retained['current_changes'],'request_id':candidate['id'],'retention':retained}
        for retained in candidate.get('retains',[]):
            if retained['origin_request_id']!=origin['id']:continue
            required={correction_id} if correction_id else {r['id'] for r in origin.get('corrections',[])}
            if not required.issubset(set(retained['correction_ids'])):continue
            proof=office_reload.activation_evidence(retained['current_changes'],candidate.get('reload_id'))
            if proof:return {'proof':proof,'changes':retained['current_changes'],'request_id':candidate['id'],'retention':retained}
    return None


def retain_sources(db,request_id,origin_id,review_ref):
    """Reviewed module maintenance, independent of the correction canon."""
    import office_current_task
    office_current_task.require_current(request_id)
    with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(request_id,)).fetchone()
        old=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(origin_id,)).fetchone()
        if not row or not old:raise ValueError('Actual source registrations are required')
        value=json.loads(row[0]);origin=json.loads(old[0]);current={r['path']:r for r in value['changes']}
        if any(r['path'] not in current for r in origin['changes']):raise ValueError('Maintenance coverage is incomplete')
        reviewed={'origin_request_id':origin_id,'current_changes':[current[r['path']] for r in origin['changes']],
                  'original_changes':origin['changes'],'review_ref':review_ref,'business_validation':'not rerun'}
        value['retained_sources']=[*[r for r in value.get('retained_sources',[]) if r['origin_request_id']!=origin_id],reviewed]
        c.execute('UPDATE '+TABLE+' SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),request_id))
    return {'reviewed_source_request':origin_id,'requires_serving_proof':True}


def _update(db,row,**values):
    if all(row.get(k)==v for k,v in values.items()):
        if values.get('state') in {'active','activation_failed','stopped','superseded','input_changed','source_conflict'}:finish_confirmation(db,row['id'])
        return
    with closing(sqlite3.connect(str(db),timeout=5)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        saved=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(row['id'],)).fetchone()
        if not saved:return
        current=json.loads(saved[0])
        # Do not overwrite a newer registration or follow-up from this chat.
        if any(current.get(k)!=row.get(k) for k in ('registered_at','goal_digest','changes')):return
        current.update(values,updated_at=time.time())
        c.execute('UPDATE '+TABLE+' SET data=?,updated=? WHERE id=?',
            (json.dumps(current,ensure_ascii=False),time.time(),row['id']))
        table=current.get('record_table')
        if table in {'desktop_chat_requests','project_panel_sends','office_department_tasks'}:
            record_id=current.get('record_id') or row['id']
            owner=c.execute('SELECT data FROM '+table+' WHERE id=?',(record_id,)).fetchone()
            if owner:
                data=json.loads(owner[0])
                data.update(source_activation=current['state'],source_reload_id=current.get('reload_id'),source_activation_reason=current.get('reason'))
                if table=='office_department_tasks':
                    import department_tasks
                    if data.get('source_activation_request_id',row['id'])==row['id']:department_tasks._put(c,data)
                else:c.execute('UPDATE '+table+' SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),record_id))
    if values.get('state') in {'active','activation_failed','stopped','superseded','input_changed','source_conflict'}:
        finish_confirmation(db,row['id'])


def snapshot(db):
    with closing(sqlite3.connect(str(db),timeout=3)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone():
            return {'revision':REVISION,'pending':0,'items':[],'worker_running':bool(_worker and _worker.is_alive()),**_status}
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM '+TABLE+' ORDER BY updated DESC LIMIT 30')]
    return {'revision':REVISION,'pending':sum(r['state'] in PENDING for r in rows),'worker_running':bool(_worker and _worker.is_alive()),**_status,
        'items':[dict({k:r.get(k) for k in ('id','state','reason','reload_id','updated_at','loaded_pid','activation_basis','retained_by','confirmation_worker')},
                      report_confirmation=(r.get('report_gate') or {}).get('state'),
                      correction_ids=[link['id'] for link in r.get('corrections',[])]) for r in rows[:10]]}


def dispatch_barrier(db,thread):
    """Preserve FIFO entries while this owner's actual source result is pending."""
    with closing(sqlite3.connect(str(db),timeout=3)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone():return None
        rows=c.execute('SELECT data FROM '+TABLE+' ORDER BY updated').fetchall()
    for raw, in rows:
        row=json.loads(raw)
        if row.get('thread_id')==thread and row.get('state') in PENDING:
            return {'id':row['id'],'state':row['state'],'reason':row.get('reason')}
    return None


def observe_evidence(db,row):
    """Observe already loaded effects only; safe for historical registrations."""
    import office_reload
    proof=office_reload.activation_evidence(row['changes'],row.get('reload_id'))
    # A loaded ticket is activation evidence, even if another user input arrived.
    # Observing it performs no publication and cannot revive an old business task.
    if proof:
        _update(db,row,state='active',reason='수정본이 실행 서버에 반영됐습니다.',
                reload_id=proof['ticket_id'],loaded_pid=proof['pid'],loaded_proof=proof,activation_basis='serving_process_proof',
                ui_refresh_required=proof.get('ui_refresh_required',[]));return True
    retained=continuity_evidence(db,row)
    if retained:
        proof=retained['proof']
        _update(db,row,state='active',reason='검토된 교정 유지 범위가 후속 서버에서 실제 로드됐습니다.',
                original_reload_id=row.get('original_reload_id') or row.get('reload_id'),reload_id=proof['ticket_id'],
                loaded_pid=proof['pid'],loaded_proof=proof,current_changes=retained['changes'],
                retained_by=retained['request_id'],activation_basis='verified_continuity');return True
    ticket=office_reload.ticket_record(row.get('reload_id'))
    if ticket.get('state') in {'failed','rolled_back','load_mismatch'}:
        _update(db,row,state='activation_failed',reason='해당 티켓의 실제 실패를 보존합니다. 자동 재실행하지 않습니다.',failure_type=ticket.get('error_type'));return True
    return False


def _advance(db,server,row):
    import office_reload,office_current_task
    if observe_evidence(db,row):return
    ticket=office_reload.ticket_record(row.get('reload_id'))
    if row['state'] in OBSERVE_ONLY|{'active','stopped','superseded','input_changed','source_conflict','refresh_required'}:
        finish_confirmation(db,row['id']);return
    if row.get('reload_id') and ticket.get('state')!='deferred':
        if not ticket:
            _update(db,row,state='activation_unconfirmed',reason='이 티켓의 과거 근거가 없습니다. 새 티켓을 보거나 자동 재실행하지 않고 실제 로드 증거만 대조합니다.')
        return
    boundary=office_current_task.read_current(row['id'],thread_id=row['thread_id'])
    if not boundary.get('current'):
        if boundary.get('latest_request_id'):
            _update(db,row,state='superseded',reason='새 지시가 있어 이전 수정본의 자동 반영을 중단했습니다.')
        else:
            failure=(boundary.get('connection_failure') or {}).get('code','current_input_unavailable')
            _update(db,row,state='boundary_unavailable',reason='실제 작업 종료 상태를 읽지 못해 반영을 보류합니다.',failure_code=failure)
        return
    if boundary.get('stopped') or boundary.get('turn_status') in {'interrupted','cancelled','failed'}:
        _update(db,row,state='stopped',reason='작업이 중지되어 자동 반영하지 않았습니다.');return
    if (boundary.get('turn_id')!=row['turn_id'] or boundary.get('input_id')!=row['input_id']
            or _goal(boundary)!=row['goal_digest']):
        _update(db,row,state='input_changed',reason='추가 지시가 바뀌어 이전 등록을 자동 반영하지 않았습니다.');return
    if boundary.get('turn_status')!='completed':
        if row['state']!='awaiting_completion':
            _update(db,row,state='awaiting_completion',reason='실제 작업 종료를 확인한 뒤 서버에 반영합니다.')
        return
    try:changes=office_reload.verify_changes(row['changes'])
    except (ValueError,OSError):
        _update(db,row,state='source_conflict',reason='등록 후 원본이 바뀌어 최신 소스를 보존하고 반영을 중단했습니다.');return
    # Recheck the same input directly before publication, not a cached checkpoint.
    latest=office_current_task.read_current(row['id'],thread_id=row['thread_id'])
    if (not latest.get('current') or latest.get('stopped') or latest.get('turn_status')!='completed'
            or latest.get('turn_id')!=row['turn_id'] or latest.get('input_id')!=row['input_id'] or _goal(latest)!=row['goal_digest']):return
    ticket=office_reload.prepare(changes,row['id'],existing_ticket=row.get('reload_id'))
    state='awaiting_reload_slot' if ticket['state']=='deferred' else 'queued' if ticket['mode']=='restart' else 'refresh_required'
    _update(db,row,state=state,reason='진행 중인 서버 작업이 끝나면 반영합니다.' if state=='queued' else '화면 소스가 저장됐습니다. 화면 새로고침이 필요합니다.',reload_id=ticket['id'])
    office_reload.schedule(server,ticket)


def finish_confirmation(db,ident,*,timed_out=False):
    """Release the existing report only with actual activation/reference facts."""
    with closing(sqlite3.connect(str(db),timeout=5)) as c:
        found=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(ident,)).fetchone()
    if not found:return False
    row=json.loads(found[0]);gate=row.get('report_gate') or {}
    if gate.get('state')!='waiting':return bool(gate.get('state'))
    if row['state'] in PENDING|OBSERVE_ONLY and not timed_out:return False
    import execution_corrections
    reuse=execution_corrections.references(db,['reports_all','current_input'])
    required={i for r in row.get('retains',[]) for i in r['correction_ids']}
    applied={r['id'] for r in reuse['applied']}
    if row['state']=='active' and (not row.get('loaded_proof') or not required<=applied) and not timed_out:return False
    proof=row.get('loaded_proof') or {}
    observation={'activation_state':row['state'],'confirmation_state':'timed_out' if timed_out else 'confirmed',
                 'reload_id':row.get('reload_id'),'loaded_pid':row.get('loaded_pid'),
                 'proof_revision':proof.get('proof_revision'),'loaded_revisions':proof.get('runtime'),
                 'change_coverage':[r['change_id'] for r in proof.get('changes',[])],
                 'applied_correction_ids':sorted(applied),'required_correction_ids':sorted(required),
                 'remaining_business_validation':'다음 실제 본업의 correction_reuse; 본업 재실행 없음'}
    import department_runtime
    observation['work_loop']=department_runtime.observe_actual(db,row.get('record_id') or ident)
    import office_ui_assets
    observation['ui_publication']=office_ui_assets.status()
    with closing(sqlite3.connect(str(db),timeout=5)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        latest=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(ident,)).fetchone()
        current=json.loads(latest[0]) if latest else {}
        if current.get('changes')!=row['changes'] or (current.get('report_gate') or {}).get('state')!='waiting':return False
        owner=c.execute('SELECT data FROM office_department_tasks WHERE id=?',(row.get('record_id') or ident,)).fetchone()
        data=json.loads(owner[0]) if owner else None
        if data and data.get('source_activation_request_id',ident)!=ident:
            current['report_gate']=dict(gate,state='superseded_in_same_work',newer_registration=data['source_activation_request_id'])
            c.execute('UPDATE '+TABLE+' SET data=? WHERE id=?',(json.dumps(current,ensure_ascii=False),ident))
            return True
        if not data or data.get('state') not in {'completed','failed','cancelled','uncertain'}:return False
        # Old-runtime bootstrap can use the same report path: append an explicitly
        # code-observed result, preserving the agent's earlier provisional text.
        original=data.get('result_before_activation',data.get('result',''))
        data.update(result_before_activation=original,activation_observation=observation,
                    verification={'level':'source_loaded' if row['state']=='active' else 'activation_not_confirmed','business_success':'not_independently_verified'},
                    result=original+'\n\n[코드가 확인한 실제 활성화·재사용 결과]\n'+json.dumps(observation,ensure_ascii=False),next_poll=0)
        current['report_gate']=dict(gate,state='timed_out' if timed_out else 'confirmed',observation=observation,confirmed_at=time.time())
        if current.get('confirmation_worker'):
            current['confirmation_worker']=dict(current['confirmation_worker'],state='timed_out' if timed_out else 'confirmed',ended_at=time.time())
        c.execute('UPDATE '+TABLE+' SET data=?,updated=? WHERE id=?',(json.dumps(current,ensure_ascii=False),time.time(),ident))
        import department_tasks
        department_tasks._put(c,data)
    return True


def confirm_bootstrap(ident):
    """One broker confirmation phase for the currently installed older worker.

    Never prepares/restarts a ticket, sends a message, or replays business work.
    The normal department worker sends its one report after this gate releases.
    """
    import office_current_task,office_reload
    office_current_task.require_current(ident)
    db=office_reload.ROOT/'office.db';deadline=time.monotonic()+1800
    import os,department_tasks
    # Lease only this existing row's poll/report; use its existing read-only
    # watcher below. This also works before the new server worker is installed.
    with closing(sqlite3.connect(str(db),timeout=5)) as c,c:
        raw=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(ident,)).fetchone()
        owner=c.execute('SELECT data FROM office_department_tasks WHERE id=?',(ident,)).fetchone()
        if not raw or not owner:return
        row=json.loads(raw[0]);data=json.loads(owner[0])
        if (row.get('confirmation_worker') or {}).get('state')=='watching_existing_registration':
            raise ValueError('이미 등록된 확인 프로세스의 실제 결과부터 대조하세요. 중복 실행하지 않았습니다.')
        if data.get('report_state') in {'sending','awaiting','uncertain','reported','failed'}:raise ValueError('기존 보고 전송 결과부터 대조해야 합니다.')
        row['report_gate']=dict(row.get('report_gate') or {},state='waiting',deadline=time.time()+1800)
        row['confirmation_worker']={'pid':os.getpid(),'state':'watching_existing_registration','started_at':time.time(),'deadline':time.time()+1800}
        data['next_poll']=time.time()+1830
        c.execute('UPDATE '+TABLE+' SET data=? WHERE id=?',(json.dumps(row,ensure_ascii=False),ident))
        c.execute('UPDATE office_department_tasks SET data=? WHERE id=?',(json.dumps(data,ensure_ascii=False),ident))
    while time.monotonic()<deadline:
        phase='read_registration'
        try:
            with closing(sqlite3.connect(str(db),timeout=5)) as c,c:
                raw=c.execute('SELECT data FROM '+TABLE+' WHERE id=?',(ident,)).fetchone()
                if not raw:return
                row=json.loads(raw[0])
                if (row.get('report_gate') or {}).get('state')!='waiting':return
                owner=c.execute('SELECT data FROM office_department_tasks WHERE id=?',(ident,)).fetchone()
                if owner:
                    data=json.loads(owner[0])
            if owner and data.get('state') in {'dispatching','working','waiting'}:
                phase='existing_native_watch'
                department_tasks._watch(db,data)
            phase='archive_legacy_ticket'
            legacy=office_reload._read(office_reload.STATE)
            if (ident in legacy.get('request_ids',[]) and {r['change_id'] for r in row['changes']}.issubset(set(legacy.get('changes',[])))
                    and not office_reload.ticket_record(legacy.get('id')).get('launch_nonce')):
                archived=dict(legacy,legacy_observed_at=time.time())
                archived['verified_changes']=office_reload.verify_changes([{'change_id':i} for i in legacy['changes']])
                office_reload._save(archived,archive_only=True)
            phase='observe_loaded_proof';observe_evidence(db,row)
            phase='verify_references_and_release_report'
            if finish_confirmation(db,ident):return
        except (OSError,ValueError,sqlite3.Error) as error:
            _status['confirmation_error']=type(error).__name__
            print(json.dumps({'phase':phase,'error_type':type(error).__name__,'at':time.time()}),flush=True)
        time.sleep(2)
    finish_confirmation(db,ident,timed_out=True)


def validate_ticket(ticket):
    """Final stop/input fence at the actual idle restart, not just scheduling."""
    import office_current_task,office_reload
    db=office_reload.ROOT/'office.db'
    with closing(sqlite3.connect(str(db),timeout=5)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(TABLE,)).fetchone():return
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM '+TABLE+" WHERE json_extract(data,'$.state')='queued'")]
    ids=set(ticket.get('changes',[]))
    for row in rows:
        if not {r['change_id'] for r in row['changes']}.issubset(ids):continue
        current=office_current_task.read_current(row['id'],thread_id=row['thread_id'])
        if (not current.get('current') or current.get('stopped') or current.get('turn_status')!='completed'
                or current.get('turn_id')!=row['turn_id'] or current.get('input_id')!=row['input_id'] or _goal(current)!=row['goal_digest']):
            raise ValueError('Source activation input changed before idle restart')
    if not native_idle(ticket,force=True):
        raise ValueError('Native work changed before idle restart')


def native_idle(ticket=None,*,force=False):
    """Only the publishing owners must finish; unrelated native work continues."""
    cache_key=(ticket or {}).get('id')
    if not force and _idle_cache.get('key')==cache_key and time.monotonic()-_idle_cache['at']<2:return _idle_cache['idle']
    import office_reload,project_panels,desktop_chat_bridge
    targets=set()
    try:
        with closing(sqlite3.connect(str(office_reload.ROOT/'office.db'),timeout=5)) as c:
            names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if TABLE in names:
                ids=set((ticket or {}).get('changes',[]));requests=set((ticket or {}).get('request_ids',[]))
                for raw, in c.execute('SELECT data FROM '+TABLE):
                    row=json.loads(raw)
                    if row.get('state') not in PENDING:continue
                    if row['id'] in requests or (ids and {r['change_id'] for r in row['changes']}.issubset(ids)):
                        targets.add(row['thread_id'])
        busy=0
        for thread in sorted(targets):
            data=project_panels._call('read_thread',{'threadId':thread,'hostId':'local','turnLimit':1,'includeOutputs':False,'maxOutputCharsPerItem':100})
            if data.get('thread',{}).get('id')!=thread:raise ValueError('Native thread status unavailable')
            status=data['thread'].get('status',{});kind=status.get('type') if isinstance(status,dict) else status
            if kind in {'active','running'} or any(t.get('status')=='inProgress' for t in data.get('turns',[])):busy+=1
        idle=busy==0
        _status.update(native_busy=busy,native_check_error=None,native_check_scope='publishing_owners',native_checked_threads=len(targets))
    except Exception as error:
        idle=False;_status.update(native_busy=None,native_check_error=type(error).__name__)
    _idle_cache.update(at=time.monotonic(),idle=idle,key=cache_key)
    return idle


def _run(db,server):
    while not _stop.is_set():
        _status.update(last_check_at=time.time(),last_error=None)
        try:
            with closing(sqlite3.connect(str(db),timeout=3)) as c:
                rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM '+TABLE+" WHERE json_extract(data,'$.state') IN ('awaiting_completion','boundary_unavailable','awaiting_reload_slot','queued','activation_unconfirmed','activation_failed') OR json_extract(data,'$.report_gate.state')='waiting' ORDER BY updated LIMIT 100")]
            for row in rows:
                if _stop.is_set():break
                try:_advance(db,server,row)
                except sqlite3.Error as error:_status['last_error']=type(error).__name__
                except Exception as error:
                    # Keep technical failures separate from consent and never
                    # retry mutations or relaunch the original business command.
                    _status['last_error']=type(error).__name__
                    if not row.get('reload_id'):_update(db,row,state='boundary_unavailable',reason='반영 연결 오류로 확인을 기다립니다.',failure_code=type(error).__name__)
                if (row.get('report_gate') or {}).get('state')=='waiting' and time.time()>(row['report_gate'].get('deadline') or float('inf')):
                    finish_confirmation(db,row['id'],timed_out=True)
        except (OSError,ValueError,sqlite3.Error) as error:_status['last_error']=type(error).__name__
        _stop.wait(5)


def start(db,server):
    global _worker
    with _guard:
        import office_reload
        office_reload.bind_server(server)
        if _worker and _worker.is_alive():return
        with closing(sqlite3.connect(str(db),timeout=5)) as c,c:_ensure(c)
        _stop.clear();_worker=threading.Thread(target=_run,args=(db,server),name='office-source-activation',daemon=True);_worker.start()


atexit.register(_stop.set)
