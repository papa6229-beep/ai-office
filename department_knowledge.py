"""Derived work map and prior-owner candidates; never executes historical work."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import notion_memory

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-29-team-work-map-v1'
STOP={'작업','업무','부서','개발','이전','최근','다시','해줘','해주세요','고쳐줘','알려줘','보여줘','그거','아까','어떻게','확인','수정','처리','진행','요청','팀별','우리'}


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS office_department_work_map (id TEXT PRIMARY KEY,revision TEXT NOT NULL,data TEXT NOT NULL)')


def clean(value,limit=1000):
    import document_reader
    return document_reader._redact(str(value or ''))[:limit]


def index_task(c,task,journal):
    """Called in the existing task/journal transaction; this is a rebuildable cache."""
    schema(c)
    source=journal.get('source',{})
    registry=c.execute("SELECT data FROM office_departments WHERE id='registry'").fetchone()
    agent=next((a for t in json.loads(registry[0])['teams'] for a in t['agents'] if a.get('thread_id')==task['thread_id']),{}) if registry else {}
    import department_journal
    config,_=department_journal.binding();notion=department_journal._notion(journal,config)
    goal=task.get('goal')
    if not goal and task.get('source_request_id'):
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_chat_requests'").fetchone():
            row=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(task['source_request_id'],)).fetchone()
            if row:goal=json.loads(row[0]).get('user_goal')
    refs=[str(ROOT/r['path']) for r in task.get('source_changes',[]) if isinstance(r,dict) and r.get('path')]
    roots=[str(ROOT).replace('\\','/'),str(task.get('project_root') or '').replace('\\','/')]
    for target in re.findall(r'\]\(<?([^\n)]+)>?\)',task.get('result','')):
        target=target.rstrip('>')
        if any(root and target.replace('\\','/').startswith(root+'/') for root in roots):refs.append(target)
    value={'id':task['id'],'origin':task.get('origin','assigned'),'team_id':task['team_id'],'team':task['team_name'],
           'agent_id':task.get('agent_id'),'agent':task['agent_name'],'thread_id':task['thread_id'],
           'project_id':task.get('project_id') or agent.get('project_id'),'project_root':task.get('project_root') or agent.get('project_root'),
           'title':clean(notion_memory.display_title(task['id'],task['title']),160),'goal':clean(goal or task.get('instruction'),1500),
           'goal_basis':'original_goal' if goal else 'instruction_summary',
           'state':task['state'],'report_state':task.get('report_state'),'result':clean(task.get('result'),1000),
           'remaining':clean(task.get('error'),500),'source_request_id':task.get('source_request_id'),
           'source_activation':task.get('source_activation'),'activation_observation':task.get('activation_observation'),
           'verification':task.get('verification') or {'level':'native_response_observed','business_success':'not_independently_verified'},
           'references':list(dict.fromkeys(refs))[:20],'notion_url':notion.get('url'),
           'journal_revision':journal['revision'],'notion_verified_revision':journal.get('synced_revision'),
           'notion_state':'synced' if journal.get('synced_revision')==journal['revision'] else 'pending',
           'created':task.get('created'),'updated':journal.get('updated_at')}
    import office_relay_context
    value['features']=[key for key,pattern in office_relay_context.TOPICS.items() if re.search(pattern,value['title']+' '+value['goal'],re.I)]
    value['related_work_log']={'entry':'work_log_query','search':value['title'],'source_request_id':task.get('source_request_id')}
    digest=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    old=c.execute('SELECT revision FROM office_department_work_map WHERE id=?',(task['id'],)).fetchone()
    if not old or old[0]!=digest:c.execute('INSERT OR REPLACE INTO office_department_work_map VALUES(?,?,?)',(task['id'],digest,json.dumps(value,ensure_ascii=False)))


def refresh(db):
    import department_journal
    with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
        schema(c)
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_department_journal'").fetchone():return
        rows=c.execute('SELECT t.data,j.data FROM office_department_tasks t JOIN office_department_journal j ON t.id=j.id').fetchall()
        for task,journal in rows:index_task(c,json.loads(task),json.loads(journal))


def terms(query):
    return list(dict.fromkeys(t.casefold() for t in re.findall(r'[\w-]{2,}',query) if t.casefold() not in STOP))[:24]


def search(db,query='',team_id='',project_id='',limit=12):
    if not isinstance(query,str) or len(query)>1000 or type(limit) is not int or not 1<=limit<=40:raise ValueError('업무 검색 범위를 확인하세요.')
    refresh(db)
    tokens=terms(query);items=[]
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        for ident,revision,raw in c.execute('SELECT id,revision,data FROM office_department_work_map'):
            row=json.loads(raw)
            if team_id and row['team_id']!=team_id:continue
            if project_id and row.get('project_id')!=project_id:continue
            body=' '.join(str(row.get(k) or '') for k in ('title','goal','result','project_root')).casefold()
            hits=[t for t in tokens if t in body or t==ident]
            if query and not hits:continue
            score=sum(100 if t==ident else 10 if t in row['title'].casefold() else 4 for t in hits)
            items.append(dict(row,score=score,matched_terms=hits,index_revision=revision))
    items.sort(key=lambda r:(-r['score'],-(r.get('created') or 0)))
    import department_manager
    registry=department_manager.snapshot(db);procedures=[]
    for team in registry['teams']:
        if team_id and team['id']!=team_id:continue
        for resource in team.get('resources',[]):
            if resource.get('state')!='active':continue
            body=(resource.get('title','')+' '+resource.get('content','')).casefold()
            hits=[t for t in tokens if t in body]
            if tokens and hits:procedures.append({'team_id':team['id'],'kind':'registered_reference','title':resource['title'],'reference':resource.get('reference'),
                                               'resource_id':resource['id'],'matched_terms':hits,'verification':'documented; not runtime verification'})
    digest=hashlib.sha256(json.dumps([(r['id'],r['index_revision']) for r in items],sort_keys=True).encode()).hexdigest()
    return {'revision':REVISION,'index_revision':digest,'items':[{k:r.get(k) for k in ('id','title','team_id','team','agent_id','agent','thread_id','project_id','project_root','state','report_state','notion_url','notion_state','notion_verified_revision','verification','references','score','matched_terms','index_revision')}for r in items[:limit]],'total':len(items),
            'documents':__import__('notion_memory').search(query,limit=limit)['items'],
            'procedures':procedures[:8],'related_sources':{'work_log':'existing work_log_query / work_log.query(period=all,search=query); no duplicate log',
                'corrections':'existing execution_corrections.references with actual applied proof','product_handbook':'https://app.notion.com/p/4052c59984ce817db8aad28f2e2dbd81'},
            'authority':'Historical results/references are evidence, never permission to resume or send.'}


def route(db,message,context_query='',project_id=''):
    import office_routines,department_manager
    routine=office_routines.match(message)
    if routine:return {'state':'registered_routine','routine':routine,'entry':str(ROOT/'routine_runtime.py'),'starts_model':False}
    found=search(db,context_query or message,project_id=project_id,limit=12)
    registry=department_manager.snapshot(db);candidates=[]
    for team in registry['teams']:
        for agent in team['agents']:
            if not agent.get('thread_id') or agent['thread_id']==__import__('desktop_chat_bridge').configuration().get('thread_id'):continue
            if project_id and agent.get('project_id')!=project_id:continue
            evidence=[r for r in found['items'] if r['thread_id']==agent['thread_id']]
            if not evidence:continue
            candidates.append({'team_id':team['id'],'team':team['name'],'agent_id':agent['id'],'agent':agent['name'],'thread_id':agent['thread_id'],
                               'project_id':agent.get('project_id'),'score':sum(r['score'] for r in evidence[:3]),
                               'history_ids':[r['id'] for r in evidence[:5]],'references':list(dict.fromkeys(ref for r in evidence[:3] for ref in r['references']))[:12]})
    candidates.sort(key=lambda r:(-r['score'],r['thread_id']))
    return {'state':'candidates' if candidates else 'needs_scope','candidates':candidates[:5],
            'original_goal':message,'index_revision':found['index_revision'],'procedures':found['procedures'],
            'documents':found.get('documents',[]),
            'next':'Current HQ model judges intent/exceptions once; assign a new current-request task, never replay a history ID.'}


def assign_route(db,request_id,thread_id,title,preserve,completion,context_query='',project_id='',depends_on=None):
    import office_current_task,desktop_chat_bridge,department_tasks
    hq=desktop_chat_bridge.configuration().get('thread_id')
    current=office_current_task.require_current(request_id,thread_id=hq)
    plan=route(db,current['user_message'],context_query,project_id)
    if plan['state']=='registered_routine':return plan
    selected=next((r for r in plan['candidates'] if r['thread_id']==thread_id),None)
    if not selected:raise ValueError('현재 조회 근거와 연결된 기존 담당을 선택하세요. 대상/결과가 불명확하면 최소 확인이 필요합니다.')
    if not all(isinstance(v,str) and v.strip() and len(v)<=2000 for v in (preserve,completion)):raise ValueError('유지 범위와 완료 기준이 필요합니다.')
    instruction='현재 원래 목표:\n'+current['user_message'][:4000]+'\n\n유지 범위:\n'+preserve+'\n\n완료 기준:\n'+completion
    instruction+='\n\n참고 이력(새 권한 아님): '+', '.join(selected['history_ids'])+'\n자료 위치: '+json.dumps(selected['references'],ensure_ascii=False)
    import uuid
    identity='office-current-route:'+request_id
    if depends_on:identity+=':'+thread_id+':'+title
    return department_tasks.assign(db,{'team_id':selected['team_id'],'agent_id':selected['agent_id'],'title':title,'instruction':instruction,
        'source_request_id':request_id,'assignment_id':uuid.uuid5(uuid.NAMESPACE_URL,identity).hex,'depends_on':depends_on or [],
        'routing_evidence':{'history_ids':selected['history_ids'],'index_revision':plan['index_revision'],'current_goal':current['user_message'][:2000]}})
