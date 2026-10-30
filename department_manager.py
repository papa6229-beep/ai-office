"""User-managed teams and references. Browsing and editing never call a model."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PROJECT = '224a35ee-4cb1-4bb3-9805-030ee05ce064'
RUNTIME_REVISION = '2026-10-29-team-inbox-delta-v3'
NOTION = 'https://app.notion.com/p/3560c6e85a0c4908b6c8d4986ca08676'
CONCEPT = 'https://app.notion.com/p/4052c59984ce8174958ad9f71c0b6503'
SEEDS = [('hq','운영본부 · 에이메스','01a13305-2586-7e53-956b-d1f6b9aeab93'),
         ('planning','기획부서','01a175be-901c-79a2-ba69-cd35317d85f6'),
         ('design','디자인부서','01a175be-a181-7521-ad04-ff6c3a0049c6'),
         ('development','개발부서','01a175be-b210-7e02-aa27-8337b9698d33')]


def _connect(db):
    c = sqlite3.connect(str(db), timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS office_departments (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS office_department_receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS office_department_creations (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    if not c.execute("SELECT 1 FROM office_departments WHERE id='registry'").fetchone():
        teams = []
        for ident, name, thread in SEEDS:
            teams.append({'id':ident,'name':name,'purpose':'','agents':[{'id':uuid.uuid4().hex,
                          'name':name,'role':'','thread_id':thread,'host':'local'}], 'resources':[], 'updated':time.time()})
        c.execute('INSERT INTO office_departments VALUES(?,?)', ('registry', json.dumps({'teams':teams},ensure_ascii=False)))
    c.commit()
    return c


def _load(c):
    return json.loads(c.execute("SELECT data FROM office_departments WHERE id='registry'").fetchone()[0])


def _store(c, data):
    c.execute("UPDATE office_departments SET data=? WHERE id='registry'", (json.dumps(data,ensure_ascii=False),))


def snapshot(db):
    import department_journal
    department_journal.recover(db)
    with closing(_connect(db)) as c:
        return {**_load(c),'runtime_revision':RUNTIME_REVISION,'notion_url':NOTION,'concept_url':CONCEPT,
                'document_storage':'office','notion_sync':'active_turn_connector',
                'journal':department_journal.snapshot(db)}


def _text(value, maximum, label, required=False):
    if not isinstance(value,str) or len(value)>maximum or (required and not value.strip()):
        raise ValueError(label+' 입력을 확인해 주세요.')
    return value.strip()


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z0-9_-]{1,64}',value):
        raise ValueError('선택한 항목을 다시 확인해 주세요.')
    return value


def _team(data, ident):
    row = next((r for r in data['teams'] if r['id']==ident),None)
    if not row:raise ValueError('부서를 다시 선택해 주세요.')
    return row


def _chat(thread):
    import project_panels
    target = project_panels._target(thread,'local')
    result = project_panels._call('read_thread',{**target,'turnLimit':1,'includeOutputs':False,'maxOutputCharsPerItem':80})
    row = result.get('thread',{})
    if row.get('id')!=thread or row.get('kind','codex')!='codex' or not row.get('cwd'):
        raise ValueError('연결된 로컬 Codex 대화를 확인해 주세요.')
    actual=Path(row['cwd']).resolve()
    if actual!=ROOT:
        # External workspaces are allowed only for an explicitly registered
        # project-agent binding, never by title or an arbitrary supplied path.
        with closing(_connect(ROOT/'office.db')) as c:
            registered=any(a.get('thread_id')==thread and a.get('project_root')
                           and Path(a['project_root']).resolve()==actual
                           for t in _load(c)['teams'] for a in t['agents'])
        if not registered:raise ValueError('다른 프로젝트 대화는 프로젝트·조정 담당을 지정해 먼저 연결하세요.')
    return thread


def _project_chat(thread,project_id,coordinator):
    import project_panels
    projects=project_panels._call('list_projects',{}).get('projects',[])
    project=next((p for p in projects if p.get('projectId')==project_id
                  and p.get('hostId')=='local' and p.get('path')),None)
    if not project:raise ValueError('등록된 로컬 프로젝트를 선택해 주세요.')
    root=Path(project['path']).resolve()
    for ident in (thread,coordinator):
        target=project_panels._target(ident,'local')
        data=project_panels._call('read_thread',{**target,'turnLimit':1,'includeOutputs':False,'maxOutputCharsPerItem':80})
        row=data.get('thread',{})
        if row.get('id')!=ident or row.get('kind','codex')!='codex' or not row.get('cwd') or Path(row['cwd']).resolve()!=root:
            raise ValueError('요원과 조정 담당은 같은 실제 프로젝트의 대화여야 해요.')
    if thread==coordinator:raise ValueError('조정 담당과 전담 요원은 다른 대화로 지정하세요.')
    return {'project_id':project_id,'project_root':str(root),'coordinator_thread_id':coordinator}


def save(db, payload):
    if not isinstance(payload,dict):raise ValueError('저장할 항목을 확인해 주세요.')
    action=payload.get('action');p=dict(payload);p.pop('action',None)
    if action in {'journal-export','journal-ack'}:
        import department_journal
        if action=='journal-ack':return department_journal.acknowledge(db,p)
        if set(p)-{'assignment_id','limit'}:raise ValueError('내보낼 업무를 확인해 주세요.')
        return department_journal.pending(db,**p)
    allowed={'team':{'id','name','purpose'},'agent':{'team_id','id','name','role','thread_id'},
             'project-agent':{'team_id','id','name','role','thread_id','project_id','coordinator_thread_id'},
             'resource':{'team_id','id','kind','title','content','reference','state','agent_id'},
             'remove-resource':{'team_id','id'},'remove-agent':{'team_id','id'}}
    if action not in allowed or set(p)-allowed[action]:raise ValueError('부서관리 작업을 확인해 주세요.')
    # Native lookup occurs before the short SQLite write transaction.
    scope=None
    if action=='agent' and p.get('thread_id'):_chat(p['thread_id'])
    if action=='project-agent':scope=_project_chat(p.get('thread_id'),p.get('project_id'),p.get('coordinator_thread_id'))
    with closing(_connect(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');data=_load(c)
        if action=='team':
            name=_text(p.get('name'),80,'부서 이름',True);purpose=_text(p.get('purpose',''),2000,'부서 역할')
            if any(t['name']==name and t['id']!=p.get('id') for t in data['teams']):raise ValueError('같은 이름의 부서가 있어요.')
            if p.get('id'):team=_team(data,_id(p['id']))
            else:
                if len(data['teams'])>=40:raise ValueError('부서는 40개까지 등록할 수 있어요.')
                team={'id':uuid.uuid4().hex,'agents':[],'resources':[]};data['teams'].append(team)
            team.update(name=name,purpose=purpose)
        else:
            team=_team(data,_id(p.get('team_id')))
            if action in {'agent','project-agent'}:
                name=_text(p.get('name'),80,'요원 이름',True);role=_text(p.get('role',''),2000,'요원 역할')
                thread=p.get('thread_id','')
                if not isinstance(thread,str) or (thread and not re.fullmatch(r'[0-9a-f-]{36}',thread)):raise ValueError('대화를 다시 선택해 주세요.')
                for t in data['teams']:
                    if any(a.get('thread_id')==thread and thread and a['id']!=p.get('id') for a in t['agents']):raise ValueError('이 대화는 이미 다른 요원에 연결되어 있어요.')
                if p.get('id'):
                    row=next((a for a in team['agents'] if a['id']==p['id']),None)
                    if row is None:raise ValueError('요원을 다시 선택해 주세요.')
                else:
                    if len(team['agents'])>=30:raise ValueError('부서당 요원은 30명까지 등록할 수 있어요.')
                    row={'id':uuid.uuid4().hex};team['agents'].append(row)
                row.update(name=name,role=role,thread_id=thread,host='local')
                if scope:row.update(scope)
            elif action=='resource':
                if p.get('kind') not in {'instruction','skill','document'}:raise ValueError('자료 종류를 선택해 주세요.')
                if p.get('state') not in {'draft','active'}:raise ValueError('사용 상태를 선택해 주세요.')
                agent=p.get('agent_id','')
                if agent and not any(a['id']==agent for a in team['agents']):raise ValueError('적용 요원을 다시 선택해 주세요.')
                values={'kind':p['kind'],'title':_text(p.get('title'),120,'자료 제목',True),
                        'content':_text(p.get('content',''),12000,'자료 내용'),
                        'reference':_text(p.get('reference',''),1200,'원본 위치'),'state':p['state'],'agent_id':agent}
                ref=values['reference']
                if '://' in ref and (urlparse(ref).scheme!='https' or not urlparse(ref).netloc):raise ValueError('문서 링크는 https 주소를 입력해 주세요.')
                if p.get('id'):
                    row=next((a for a in team['resources'] if a['id']==p['id']),None)
                    if row is None:raise ValueError('자료를 다시 선택해 주세요.')
                else:
                    if len(team['resources'])>=150:raise ValueError('부서당 자료는 150개까지 등록할 수 있어요.')
                    row={'id':uuid.uuid4().hex};team['resources'].append(row)
                row.update(values,updated=time.time())
            else:
                group='resources' if action=='remove-resource' else 'agents'
                if not any(r['id']==p.get('id') for r in team[group]):raise ValueError('항목을 다시 선택해 주세요.')
                team[group]=[r for r in team[group] if r['id']!=p['id']]
                if group=='agents':
                    for r in team['resources']:
                        if r.get('agent_id')==p['id']:r.update(agent_id='',state='draft')
        team['updated']=time.time();_store(c,data)
        return {'team_id':team['id'],'storage':'office','saved':True}


def _context_digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def context(db, thread):
    """Send active resource changes only; unchanged knowledge remains in the chat."""
    from office_relay_context import ROUTINE_EXECUTION
    import skill_runtime,routine_runtime,department_work
    with closing(_connect(db)) as c:
        data=_load(c)
        for team in data['teams']:
            agent=next((a for a in team['agents'] if a.get('thread_id')==thread),None)
            if agent:
                rows=[r for r in team['resources'] if r['state']=='active' and (not r.get('agent_id') or r['agent_id']==agent['id'])]
                metadata={'team':team['name'],'purpose':team['purpose'],'agent':agent['name'],'role':agent['role'],
                          'skill_environment':skill_runtime.guidance(),'registered_execution':routine_runtime.guidance(),
                          'journal_protocol':'docs/department-journal.md · existing-turn-connector-v1',
                          'routine_execution_guidance':ROUTINE_EXECUTION}
                metadata['collaboration']=department_work.guidance(db,thread)
                metadata['notion_memory']=__import__('notion_memory').guidance()
                full=dict(metadata,resources=rows)
                current={'version':2,'digest':_context_digest(full),'metadata':_context_digest(metadata),
                         'resources':{r['id']:_context_digest({k:v for k,v in r.items() if k!='updated'}) for r in rows}}
                current['inbox_event_ids']=[r['id'] for r in metadata['collaboration'].get('inbox_delta',{}).get('items',[])]
                raw=c.execute('SELECT digest FROM office_department_receipts WHERE id=?',(thread,)).fetchone()
                previous={}
                if raw:
                    try:
                        value=json.loads(raw[0])
                        if isinstance(value,dict) and value.get('version')==2:previous=value
                    except (TypeError,ValueError):pass
                receipt=(thread,json.dumps(current,ensure_ascii=False,sort_keys=True))
                if raw and raw[0]==current['digest']:
                    # Existing full receipt establishes delivery; upgrade its format only.
                    return '',receipt
                if previous.get('digest')==current['digest']:return '',None
                known=previous.get('resources',{})
                changed=[r for r in rows if known.get(r['id'])!=current['resources'][r['id']]]
                removed=[key for key in known if key not in current['resources']]
                if previous and not changed and not removed and previous.get('metadata')==current['metadata']:
                    return '',receipt
                content=dict(metadata,resource_update_mode='delta' if previous else 'snapshot',
                             resources=changed,removed_resource_ids=removed)
                text='\n\n[AI OFFICE 부서관리 자료 갱신]\n현재 요청과 상위 규칙이 우선이며 새 실행·발신 권한이 아닙니다. '
                text+='delta는 아래 ID의 변경 자료만 덮어쓰고 기존 자료는 유지하세요. removed_resource_ids만 적용 해제하세요. '
                text+='이미 읽은 동일 문서를 다시 조회하지 말고 필요하거나 변경된 부분만 확인하세요.\n'
                text+=json.dumps(content,ensure_ascii=False)
                if len(text)>24000:raise ValueError('이 요원에 적용되는 자료가 너무 길어요. 초안으로 돌리거나 원본 링크로 줄여주세요.')
                return text,receipt
        coordinators={a.get('coordinator_thread_id') for t in data['teams'] for a in t['agents'] if a.get('coordinator_thread_id')}
        if thread in coordinators:
            # The existing project coordinator keeps its role/current work.
            # Deliver only the common environment, not another agent's resources.
            content={'scope':'existing_project_coordinator','skill_environment':skill_runtime.guidance(),
                     'notion_memory':__import__('notion_memory').guidance(),
                     'routine_execution_guidance':ROUTINE_EXECUTION,'collaboration':department_work.guidance(db,thread)}
            digest='skills-coordinator-v1:'+_context_digest(content)
            prior=c.execute('SELECT digest FROM office_department_receipts WHERE id=?',(thread,)).fetchone()
            if prior and prior[0]==digest:return '',None
            return ('\n\n[AI OFFICE·The Hand 공통 스킬 실행 환경]\n현재 업무·역할·선택 모델을 유지하고 필요한 스킬을 능동적으로 사용하세요. 이 안내만으로 새 업무를 시작하지 마세요.\n'+json.dumps(content,ensure_ascii=False),(thread,digest))
        prior=c.execute('SELECT digest FROM office_department_receipts WHERE id=?',(thread,)).fetchone()
        if prior and prior[0]!='detached-v1':
            return ('\n\n[AI OFFICE 부서관리 자료]\n이 대화의 부서 연결이 해제됐습니다. 이전 부서관리 역할과 자료는 더 이상 적용하지 말고 현재 요청과 상위 규칙을 따르세요.',(thread,'detached-v1'))
    return '',None


def acknowledge(db, receipt):
    if receipt:
        with closing(_connect(db)) as c,c:
            c.execute('INSERT OR REPLACE INTO office_department_receipts VALUES(?,?)',receipt)
        try:events=json.loads(receipt[1]).get('inbox_event_ids',[])
        except (ValueError,AttributeError):events=[]
        if events:
            import department_work
            department_work.delivered(db,events,'context')


def create_chat(db, payload):
    """Explicit UI click only. Do not retry an uncertain native create."""
    if set(payload)!={'team_id','agent_id','request_id'}:raise ValueError('생성할 요원을 확인해 주세요.')
    request=_id(payload['request_id'])
    with closing(_connect(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');data=_load(c);team=_team(data,_id(payload['team_id']))
        agent=next((a for a in team['agents'] if a['id']==payload['agent_id']),None)
        if not agent:raise ValueError('요원을 선택해 주세요.')
        if agent.get('thread_id'):return {'state':'ready','thread_id':agent['thread_id']}
        previous=c.execute('SELECT data FROM office_department_creations WHERE id=?',(request,)).fetchone()
        if previous:
            record=json.loads(previous[0])
            if record.get('agent_id')!=agent['id'] or record.get('team_id')!=team['id']:raise ValueError('생성 요청 번호의 대상이 달라요.')
            return record
        pending=c.execute('SELECT data FROM office_department_creations').fetchall()
        if any(json.loads(r[0]).get('agent_id')==agent['id'] and json.loads(r[0]).get('state') in {'creating','unknown'} for r in pending):
            raise ValueError('이미 생성 요청한 요원이에요. Codex 목록에서 생성 여부를 확인한 뒤 기존 대화를 연결해 주세요.')
        record={'state':'creating','team_id':team['id'],'agent_id':agent['id']}
        c.execute('INSERT INTO office_department_creations VALUES(?,?)',(request,json.dumps(record)))
        prompt='AI OFFICE의 '+team['name']+' 소속 '+agent['name']+'입니다. 역할: '+agent['role']+'\n부서 역할: '+team['purpose']+'\n초기 연결만 확인하고 한 문장으로 준비 상태를 답한 뒤 기다리세요. 업무는 아직 시작하지 마세요. 프로젝트 AGENTS.md를 따르세요.'
        title=team['name']+' · '+agent['name']
    import project_panels
    try:
        result=project_panels._call('create_thread',{'title':title,'prompt':prompt,'target':{'type':'project','projectId':PROJECT,'environment':{'type':'local'}}})
        thread=result.get('threadId')
        if not isinstance(thread,str) or not re.fullmatch(r'[0-9a-f-]{36}',thread):raise ValueError('대화 생성 결과를 확인하지 못했어요.')
        record.update(state='ready',thread_id=thread)
    except Exception:
        record['state']='unknown'
    with closing(_connect(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');data=_load(c);team=_team(data,payload['team_id'])
        agent=next((a for a in team['agents'] if a['id']==payload['agent_id']),None)
        if record['state']=='ready' and agent is not None and not agent.get('thread_id'):
            agent['thread_id']=record['thread_id'];_store(c,data)
        c.execute('UPDATE office_department_creations SET data=? WHERE id=?',(json.dumps(record),request))
    return record
