"""Current direct work and structured HQ inbox on the existing task ledger."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-29-direct-team-work-v1'
_scan_at=0
_fingerprints={}
_observer_status={'errors':{},'observed_threads':[]}


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS office_department_native_cursors (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS office_department_inbox (id TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL)')


def registered(db):
    import desktop_chat_bridge
    hq=desktop_chat_bridge.configuration().get('thread_id');result={}
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        row=c.execute("SELECT data FROM office_departments WHERE id='registry'").fetchone()
    for team in (json.loads(row[0]).get('teams',[]) if row else []):
        for agent in team['agents']:
            thread=agent.get('thread_id')
            if not thread or thread==hq or agent.get('host','local')!='local':continue
            result[thread]={'team_id':team['id'],'team_name':team['name'],'agent_id':agent['id'],'agent_name':agent['name'],
                            'thread_id':thread,'project_id':agent.get('project_id'),'project_root':agent.get('project_root'),
                            'coordinator_thread_id':agent.get('coordinator_thread_id'),'hq_thread':hq,'registry_kind':'agent'}
            coordinator=agent.get('coordinator_thread_id')
            if coordinator and coordinator!=hq and coordinator not in result:
                result[coordinator]={'team_id':'hq','team_name':'운영본부','agent_id':None,'agent_name':'프로젝트 총괄',
                    'thread_id':coordinator,'project_id':agent.get('project_id'),'project_root':agent.get('project_root'),
                    'hq_thread':hq,'registry_kind':'existing_project_coordinator'}
    return result


def _text(item):
    import desktop_chat_bridge
    return desktop_chat_bridge._text(item)


def human_text(text):
    if '## My request:' in text:text=text.rsplit('## My request:',1)[1].strip()
    return text.strip()


def nonwork(text):
    value=human_text(text)
    if value.startswith(('# AGENTS.md','<environment_context>','<send_user_message_question_reply>','<heartbeat>','Earlier conversation reference','<codex_delegation>','[AI OFFICE')):return True
    import office_routines
    route=office_routines.match(value)
    if route and route['id'] in {'small_talk','catalog','material_search','work_log_query','file_history_latest','work_list','notifications','files_find','file_jobs','file_status'}:return True
    return bool(re.fullmatch(r'(?:안녕[~!?. ]*|고마워[~!?. ]*|감사합니다[.! ]*|응|네|알겠어|지금\s*어때\??|진행\s*(?:상황|상태)\s*(?:알려줘|보여줘)?\??)',value))


def is_work(text):
    value=human_text(text)
    if not value or len(value)>12000 or nonwork(value):return False
    return bool(re.search(r'(?:수정|구현|개발|개선|제작|작성|조사|분석|검토|설계|정리|복구|설치|연결|해결|변경|추가|삭제|이동|빌드|배포|처리|작업|완료)(?:해(?:줘|주세요)?|하자|하세요|\s*(?:부탁|요청))|(?:만들어|고쳐|내보내)(?:줘|주세요|[.!? ]*$)',value)
                or re.match(r'\s*(?:(?:can|could) you\s+|please\s+)?(?:build|create|implement|fix|update|review|audit|research|draft|export|refactor)\b',value,re.I))


def _refs(data,db):
    """Only real native human input or a matching recorded panel delivery."""
    import desktop_chat_bridge,department_tasks,office_current_task
    hq=desktop_chat_bridge.configuration().get('thread_id')
    for turn in data.get('turns',[]):
        boundaries=[i for i in turn.get('items',[]) if (i.get('type')=='userMessage' or desktop_chat_bridge._is_delivery(i))
                    and office_current_task._input(i,hq,data['thread']['id'])[1]!='reference_delivery']
        # Retained older inputs are references, not newly discovered work.
        for item in boundaries[-1:]:
            if item.get('type')=='userMessage':
                if item.get('truncated'):continue
                text=human_text(_text(item))
                yield turn,item,text,'native',hashlib.sha256((data['thread']['id']+'|'+str(item.get('id'))).encode()).hexdigest()[:32]
            elif desktop_chat_bridge._is_delivery(item):
                text=department_tasks._input(item)
                match=re.match(r'\s*\[AI OFFICE 작업창 요청 ([0-9a-f]{32})\]',text)
                if not match:continue
                with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
                    row=c.execute('SELECT data FROM project_panel_sends WHERE id=? AND thread_id=?',(match[1],data['thread']['id'])).fetchone()
                if row:yield turn,item,json.loads(row[0]).get('message',''),'project_panel',match[1]


def _active(c,thread):
    return [json.loads(r[0]) for r in c.execute("SELECT data FROM office_department_tasks WHERE json_extract(data,'$.thread_id')=? AND json_extract(data,'$.state') IN ('dispatching','working','waiting','uncertain') ORDER BY updated DESC",(thread,))]


def _upsert(c,meta,text,request,turn=None,item=None,origin='native',declared=False,title=None):
    import department_tasks,department_knowledge
    existing=c.execute('SELECT data FROM office_department_tasks WHERE id=?',(request,)).fetchone()
    if existing:
        value=json.loads(existing[0])
        if turn and item and value.get('origin')=='direct' and not any(r.get('input_id')==item['id'] for r in value.get('input_refs',[])):
            value.setdefault('input_refs',[]).append({'request_id':request,'turn_id':turn['id'],'input_id':item['id'],'channel':origin,'input_sha256':hashlib.sha256(text.encode()).hexdigest()})
            value.update(turn_id=turn['id']);department_tasks._put(c,value)
        return value
    if nonwork(text) or (not declared and not is_work(text)):return None
    clean=department_knowledge.clean(text,4000)
    row=dict(meta,id=request,signature=hashlib.sha256((meta['thread_id']+'|'+request+'|'+clean).encode()).hexdigest(),origin='direct',
             source_channel=origin,source_request_id=request,title=department_knowledge.clean(title or text.splitlines()[0],120),goal=clean,instruction=clean,
             state='working' if turn and turn.get('status')=='inProgress' else 'dispatching',report_state='pending',result='',error='',
             created=(turn.get('startedAt') or time.time()) if turn else time.time(),time_basis='native_turn' if turn and turn.get('startedAt') else 'observation',
             report_id=uuid.uuid5(uuid.NAMESPACE_URL,'office-department-report:'+request).hex,
             input_refs=[],followups=[],verification={'level':'intent_observed','business_success':'not_independently_verified'})
    if turn and item:
        row.update(turn_id=turn['id'],input_refs=[{'request_id':request,'turn_id':turn['id'],'input_id':item['id'],'channel':origin,'input_sha256':hashlib.sha256(text.encode()).hexdigest()}])
    department_tasks._put(c,row)
    return row


def panel_accepted(db,thread,request,text,before):
    meta=registered(db).get(thread)
    if not meta:return None
    import department_tasks,department_knowledge
    with closing(department_tasks._db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');schema(c)
        active=_active(c,thread)
        latest=next(iter(before.get('turns',[])),{})
        owner=next((r for r in active if r.get('turn_id')==latest.get('id')),None)
        if owner:
            follow=owner.get('followups',[])
            if not any(r.get('request_id')==request for r in follow):
                follow.append({'request_id':request,'kind':'question_answer' if text.startswith('[Office에서 사용자가 제출한 질문 답변]') else 'message','message':department_knowledge.clean(text,2000)})
                owner['followups']=follow[-30:];department_tasks._put(c,owner)
            return owner['id']
        row=_upsert(c,meta,text,request,origin='project_panel')
        return row['id'] if row else None


def observe_data(db,data):
    thread=data.get('thread',{}).get('id');meta=registered(db).get(thread)
    if not meta:return {'observed':False}
    if meta['registry_kind']=='existing_project_coordinator':meta['agent_name']=data['thread'].get('title') or meta['agent_name']
    import department_tasks,department_knowledge,desktop_chat_bridge,question_reply_context
    created=[];attached=[]
    refs=list(_refs(data,db))
    with closing(department_tasks._db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');schema(c)
        raw=c.execute('SELECT data FROM office_department_native_cursors WHERE id=?',(thread,)).fetchone()
        cursor=json.loads(raw[0]) if raw else {'seen':[],'initialized_at':time.time()}
        seen=set(cursor['seen'])
        for turn,item,text,channel,request in sorted(refs,key=lambda r:r[0].get('startedAt') or 0):
            input_id=item.get('id')
            if not input_id or input_id in seen:continue
            seen.add(input_id)
            if nonwork(text) and not question_reply_context.decode(text):continue
            # Startup completed history is never registered/reported as new work.
            started=turn.get('startedAt') or 0
            if isinstance(started,(int,float)) and started>1000000000000:started/=1000
            if turn.get('status')!='inProgress' and (not raw or not isinstance(started,(int,float)) or started<cursor['initialized_at']):continue
            active=_active(c,thread)
            markers={desktop_chat_bridge.marker(r['id']) for r in active if r.get('origin')!='direct'}
            positions=[n for n,value in enumerate(turn.get('items',[])) if department_tasks._input(value).strip().splitlines()[:1] and department_tasks._input(value).strip().splitlines()[0] in markers]
            if positions and turn['items'].index(item)<max(positions):continue
            assigned=[]
            for task in active:
                marker=desktop_chat_bridge.marker(task['id'])
                if task.get('turn_id')==turn.get('id') or any(department_tasks._input(i).strip().splitlines()[:1]==[marker] for i in turn.get('items',[]) if desktop_chat_bridge._is_delivery(i)):
                    assigned.append(task)
            owner=next((r for r in active if any(f.get('request_id')==request for f in r.get('followups',[]))),None) or (assigned[0] if assigned else None)
            reply=question_reply_context.decode(text)
            steering='\n'.join(r['answer'] for r in reply) if reply else text
            if not owner and reply:
                owner=next((r for r in active if r.get('state')=='waiting' and {q['call'] for q in reply}.issubset(set(r.get('question_ids',[])))),None)
            paused=[r for r in active if r.get('direct_stopped')]
            resume=bool(re.fullmatch(r'\s*(?:이어서\s*)?(?:계속해|재개해|다시\s*진행해)(?:줘|주세요)?[.! ]*',steering))
            if not owner and resume and len(paused)==1:owner=paused[0]
            if owner:
                links=owner.get('input_refs',[])
                if not any(r['input_id']==input_id for r in links):
                    links.append({'request_id':request,'turn_id':turn['id'],'input_id':input_id,'channel':channel,'input_sha256':hashlib.sha256(text.encode()).hexdigest()})
                    owner['input_refs']=links[-60:]
                    owner['latest_input_revision']=hashlib.sha256((input_id+'|'+text).encode()).hexdigest()
                    if request!=owner.get('source_request_id'):
                        updates=owner.get('followups',[])
                        if not any(r.get('request_id')==request for r in updates):updates.append({'request_id':request,'kind':'question_answer' if reply else 'message','message':department_knowledge.clean(text,2000)})
                        owner['followups']=updates[-30:]
                    if owner.get('origin')=='direct':owner['turn_id']=turn['id']
                    if re.fullmatch(r'\s*(?:일단\s*|이\s*(?:업무|작업)\s*)?(?:보류|중지|중단|취소|멈춰|그만)(?:해|해줘|해주세요|줘)?[.! ]*',steering):
                        owner.update(state='waiting',direct_stopped=True,error='사용자가 이 업무의 중지/보류를 지시했습니다. 자동 재개하지 않습니다.')
                    elif resume and owner.get('direct_stopped'):owner.update(state='working',direct_stopped=False,error='')
                    department_tasks._put(c,owner);attached.append(owner['id'])
                continue
            # Controls/assignment deliveries are not yielded as work origins.
            task=_upsert(c,meta,text,request,turn,item,channel)
            if task:created.append(task['id'])
        latest_seen=list(dict.fromkeys([*cursor['seen'],*(item.get('id') for _,item,_,_,_ in refs if item.get('id'))]))[-200:]
        if latest_seen!=cursor['seen'] or not raw:
            cursor.update(seen=latest_seen)
            c.execute('INSERT OR REPLACE INTO office_department_native_cursors VALUES(?,?)',(thread,json.dumps(cursor,ensure_ascii=False)))
    return {'observed':True,'created':created,'attached':attached,'completed_history_registered':False}


def discover(db):
    global _scan_at
    if time.monotonic()-_scan_at<10:return
    _scan_at=time.monotonic()
    import department_tasks
    for thread in registered(db):
        try:
            import project_questions
            path=project_questions._path(thread)
            stat=path.stat() if path else None
            fingerprint=(stat.st_size,stat.st_mtime_ns) if stat else None
            if fingerprint is not None and _fingerprints.get(thread)==fingerprint:continue
            observe_data(db,department_tasks._read(thread,2))
            if fingerprint is not None:_fingerprints[thread]=fingerprint
            _observer_status['errors'].pop(thread,None)
            if thread not in _observer_status['observed_threads']:_observer_status['observed_threads'].append(thread)
        except Exception as error:
            import department_knowledge
            _observer_status['errors'][thread]=department_knowledge.clean(str(error),180)


def response(db,data,task):
    """An unmarked direct final is accepted only after a recorded input anchor."""
    import desktop_chat_bridge,project_questions
    if task.get('direct_stopped'):return {'state':'waiting','error':task.get('error','사용자 보류 상태입니다.')}
    anchors={r['input_id'] for r in task.get('input_refs',[])}
    for turn in data.get('turns',[]):
        positions=[i for i,item in enumerate(turn.get('items',[])) if item.get('id') in anchors]
        if not positions:continue
        after=turn['items'][max(positions)+1:]
        if any(i.get('type')=='userMessage' or desktop_chat_bridge._is_delivery(i) for i in after):
            return {'state':'waiting','completion_unknown':True,'error':'새 입력 경계를 아직 연결하지 못해 완료로 단정하지 않습니다.'}
        questions=project_questions.read(task['thread_id'],'local',turn).get('questions',[])
        if questions:return {'state':'waiting','question_ids':[q['id'] for q in questions],'turn_id':turn['id'],'error':''}
        status=turn.get('status')
        finals=[i for i in after if i.get('type')=='agentMessage' and i.get('phase') in {'final','final_answer'} and i.get('text','').strip()]
        if status in {'interrupted','cancelled','failed'}:
            return {'state':'failed' if status=='failed' else 'cancelled','turn_id':turn['id'],'result':'실제 native 턴이 '+status+'로 종료됐습니다.','verification':{'level':'native_terminal_observed','business_success':'not_claimed'}}
        if status=='completed' and finals:
            import department_knowledge
            text=re.sub(r'```[\s\S]*?```','[인용/코드 본문은 원대화 참조]',finals[-1]['text'])
            text='\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('>'))
            return {'state':'completed','turn_id':turn['id'],'final_item_id':finals[-1].get('id'),
                    'result':department_knowledge.clean(text,1600),'result_truncated':bool(finals[-1].get('truncated')) or len(text)>1600,
                    'verification':{'level':'native_final_received','business_success':'not_independently_verified'}}
        if status=='completed':return {'state':'waiting','completion_unknown':True,'error':'턴은 종료됐지만 실제 최종 결과를 확인하지 못했습니다.'}
        return {'state':'working','turn_id':turn['id'],'error':''}
    return None


def declare_current(db,request_id,title,kind):
    import office_relay_context,department_tasks
    current=office_relay_context.read_current(request_id)
    if not current.get('current') or not current.get('can_execute') or current.get('stopped'):raise ValueError('실제 현재 입력이 아닙니다.')
    if current.get('source')=='department_assignment':return {'tracked_as':'assigned','task_id':request_id}
    if kind not in {'development','design','research','document','file_operation'}:raise ValueError('실제 산출물 업무의 종류를 지정하세요.')
    meta=registered(db).get(current['thread_id'])
    if not meta:raise ValueError('등록된 기존 부서 대화에서만 업무를 연결합니다.')
    with closing(department_tasks._db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');schema(c)
        owner=owner_for_input(c,current['thread_id'],request_id)
        if owner:return {'tracked_as':owner.get('origin','assigned'),'task_id':owner['id'],'reused':True}
        task=_upsert(c,meta,current['user_message'],request_id,{'id':current['turn_id'],'status':'inProgress'},
                     {'id':current['input_id']},current['source'],declared=True,title=title)
    if not task:raise ValueError('질문·잡담·단순 조회는 새 업무로 만들지 않습니다.')
    return {'task_id':task['id'],'tracked_as':'direct','automatic_report':True}


def owner_for_input(c,thread,request_id):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_department_tasks'").fetchone():return None
    for raw, in c.execute("SELECT data FROM office_department_tasks WHERE json_extract(data,'$.thread_id')=? ORDER BY updated DESC",(thread,)):
        row=json.loads(raw)
        if row['id']==request_id or any(r.get('request_id')==request_id for r in [*row.get('input_refs',[]),*row.get('followups',[])]):return row
    return None


def capture_inbox(c,task):
    schema(c)
    if task['state'] not in {'completed','failed','cancelled'}:return
    if task.get('source_changes') and (task.get('activation_observation') or {}).get('confirmation_state') not in {'confirmed','timed_out'}:return
    import department_knowledge
    ident=uuid.uuid5(uuid.NAMESPACE_URL,'office-department-completion:'+task['id']).hex
    previous=c.execute('SELECT data FROM office_department_inbox WHERE id=?',(ident,)).fetchone()
    prior=json.loads(previous[0]) if previous else {}
    value={'id':ident,'task_id':task['id'],'team':task['team_name'],'agent':task['agent_name'],'origin':task.get('origin','assigned'),
           'title':task['title'],'state':task['state'],'summary':department_knowledge.clean(task.get('result'),1200),
           'verification':task.get('verification'),'source_activation':task.get('source_activation'),
           'activation_observation':task.get('activation_observation'),'thread_id':task['thread_id'],
           'delivery':prior.get('delivery','historical_reported' if task.get('report_state') in {'reported','failed','notified'} else 'pending'),'observed_at':prior.get('observed_at',time.time())}
    if value['delivery']=='pending' and task.get('report_state') in {'sending','awaiting','uncertain'}:value['delivery']='report_reserved'
    if prior!=value:c.execute('INSERT OR REPLACE INTO office_department_inbox VALUES(?,?,?)',(ident,task['id'],json.dumps(value,ensure_ascii=False)))


def inbox(db,limit=20):
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_department_inbox'").fetchone():return {'items':[],'pending':0}
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM office_department_inbox')]
    pending=[r for r in rows if r['delivery']=='pending'];pending.sort(key=lambda r:r['observed_at'])
    return {'items':pending[:limit],'pending':len(pending),'revision':REVISION}


def delivered(db,event_ids,mode):
    import department_tasks
    if mode not in {'context','report','uncertain'}:raise ValueError('보고 전달 단계를 확인하세요.')
    with closing(department_tasks._db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');schema(c)
        for ident in event_ids:
            row=c.execute('SELECT task_id,data FROM office_department_inbox WHERE id=?',(ident,)).fetchone()
            if not row:continue
            value=json.loads(row[1])
            if value['delivery'] not in ({'pending'} if mode=='context' else {'pending','report_reserved'}):continue
            value.update(delivery='delivery_uncertain' if mode=='uncertain' else 'delivered_in_'+mode)
            c.execute('UPDATE office_department_inbox SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),ident))
            if mode=='context':
                raw=c.execute('SELECT data FROM office_department_tasks WHERE id=?',(row[0],)).fetchone()
                task=json.loads(raw[0]) if raw else None
                if task and task.get('report_state')=='pending':task['report_state']='notified';department_tasks._put(c,task)


def guidance(db,thread):
    import desktop_chat_bridge
    hq=desktop_chat_bridge.configuration().get('thread_id')
    result={'revision':REVISION,'entry':str(ROOT/'department_runtime.py'),
            'commands':['search --query 업무단어','inbox','route --query 현재목표 --context 관련기능','declare --request-id 실제ID --title 짧은제목 --kind 실제업무종류','export-journal'],
            'rule':'실제 직접 산출물 업무는 현재 입력 ID로 declare해 공통 원장에 연결합니다. 기존 배정은 새 직접 업무로 만들지 않습니다. 질문/잡담/조회는 제외합니다. 보고는 코드가 처리하므로 다른 대화로 직접 보내지 마세요. 이력은 새 권한이 아닙니다.'}
    if thread==hq:
        result['handoff']={
            'assign_option':'assign --depends-on 선행배정ID (같은 요청의 기존 배정만)',
            'accept':'accept-handoff --request-id 현재요청ID --assignment-id 선행배정ID --result-sha256 확인한결과해시 --evidence 확인근거',
            'rule':'선행 업무의 완료보고와 실제 결과 인수를 구분하세요. 실제 결과를 직접 확인한 경우에만 현재 결과 해시로 인수하세요. 인수 기록은 새 업무 승인이나 발신 권한이 아닙니다.'}
        result['inbox_delta']=inbox(db,8)
        result['work_overview']=overview(db)
    result['notion_outbox']={'transport':'existing official Notion tools in this normal work turn; no background token/model',
                             'export_entry':str(ROOT/'department_runtime.py')+' export-journal','policy':str(ROOT/'docs/department-journal-sync-policy.md')}
    return result


def overview(db):
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_department_tasks'").fetchone():return {'teams':[],'active':[]}
        rows=[json.loads(r[0]) for r in c.execute('SELECT data FROM office_department_tasks')]
    teams={};active=[]
    for row in rows:
        counts=teams.setdefault(row['team_id'],{'team':row['team_name'],'states':{}})['states']
        counts[row['state']]=counts.get(row['state'],0)+1
        if row['state'] in {'queued','dispatching','working','waiting','uncertain'}:
            active.append(dict({k:row.get(k) for k in ('id','title','team_name','agent_name','state','project_id','direct_stopped')},origin=row.get('origin','assigned')))
    return {'teams':list(teams.values()),'active':active[:20],'truncated':len(active)>20}


def pending_question_owner(db,thread,origin,answers,input_id):
    """Only an already tracked waiting task plus exact live question evidence."""
    import department_knowledge
    wanted={r['call'] for r in answers}
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_department_tasks'").fetchone():return None
        for task in _active(c,thread):
            if task.get('origin')!='direct' or task.get('direct_stopped') or not wanted<=set(task.get('question_ids',[])):continue
            if task.get('state')!='waiting' and not any(r['input_id']==input_id for r in task.get('input_refs',[])):continue
            if not any(r['turn_id']==origin['turn_id'] for r in task.get('input_refs',[])):continue
            if task.get('goal')==department_knowledge.clean(human_text(origin['text']),4000):return task['id']
    return None
