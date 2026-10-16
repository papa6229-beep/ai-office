"""Observed client context and evidence-bound, post-activation UI verification.

No model script is evaluated in a user tab. Checks are read-only DOM predicates;
pixels are captured only from a selected, non-protected Windows window.
"""
import base64
import contextlib
import hashlib
import json
import pathlib
import re
import sqlite3
import time
import uuid

ROOT=pathlib.Path(__file__).resolve().parent
BOOT=uuid.uuid4().hex
UI_FILES=('index.html','workdesk.html','workdesk.js','workdesk.css','workstage.js',
          'workstage.css','workambient.js','workambient.css','work-observer.js')
ATTRIBUTES={'href','target','rel','class','aria-label','aria-expanded','aria-selected',
            'aria-disabled','data-state','type','role','title'}


def _json(value):return json.dumps(value,ensure_ascii=False,allow_nan=False)


@contextlib.contextmanager
def _db(db):
    c=sqlite3.connect(str(db),timeout=15);c.row_factory=sqlite3.Row
    try:
        with c:
            c.executescript('''CREATE TABLE IF NOT EXISTS office_clients
                (id TEXT PRIMARY KEY,data TEXT NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS office_goals
                (request_id TEXT PRIMARY KEY,data TEXT NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS office_observations
                (id TEXT PRIMARY KEY,request_id TEXT NOT NULL,data TEXT NOT NULL,created REAL NOT NULL);''')
            yield c
    finally:c.close()


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('화면 연결 식별자를 확인하세요.')
    return value


def version():
    digest=hashlib.sha256()
    for name in UI_FILES:
        path=ROOT/name
        if path.is_file():digest.update(name.encode());digest.update(path.read_bytes())
    return {'boot':BOOT,'ui':digest.hexdigest()}


def _text(value,maximum):
    if not isinstance(value,str) or len(value)>maximum:raise ValueError('화면 정보의 길이를 확인하세요.')
    from browser_workspace import _redact
    return _redact(value)


def client_snapshot(value):
    if not isinstance(value,dict):raise ValueError('화면 정보가 필요합니다.')
    result={'id':_id(value.get('id')),'version':value.get('version'),
            'url':_text(value.get('url',''),500),'title':_text(value.get('title',''),160),
            'view':_text(value.get('view',''),100),'visible':value.get('visible') is True,
            'focused':value.get('focused') is True}
    if not re.fullmatch(r'https?://(?:127\.0\.0\.1|localhost):8765/[^?#]*',result['url']):raise ValueError('현재 오피스 탭만 연결할 수 있습니다.')
    if not isinstance(result['version'],dict) or set(result['version'])!={'boot','ui'} or any(not isinstance(x,str) or len(x)>80 for x in result['version'].values()):raise ValueError('화면 버전을 확인하세요.')
    viewport=value.get('viewport',{})
    result['viewport']={key:viewport.get(key) for key in ('width','height','scale','x','y','outer_width','outer_height')
                        if isinstance(viewport.get(key),(int,float)) and -30000<=viewport[key]<=30000}
    result['elements']=[]
    if not isinstance(value.get('elements',[]),list) or not isinstance(value.get('errors',[]),list):raise ValueError('화면 요소 목록을 확인하세요.')
    for row in value.get('elements',[])[:100]:
        if not isinstance(row,dict):continue
        result['elements'].append({key:_text(row.get(key,''),300) for key in ('selector','tag','role','label','href')})
    result['errors']=[_text(x,300) for x in value.get('errors',[])[:8] if isinstance(x,str)]
    if len(_json(result))>45000:raise ValueError('화면 정보가 너무 큽니다.')
    return result


def latest_client(db,client_id=None):
    with _db(db) as c:
        row=c.execute('SELECT data,updated FROM office_clients WHERE id=?',(client_id,)).fetchone() if client_id else None
        if not row:return {'available':False,'reason':'이 요청을 보낸 실제 탭의 화면 정보가 없습니다. 현재 창을 안다고 추측하지 마세요.'}
        result=json.loads(row['data']);age=time.time()-row['updated']
        return dict(result,available=age<15,age_seconds=round(age,1),scope='실제 요청 탭의 화면 요소·배치; 입력값·대화 본문을 수집하지 않음')


def validate_checks(checks):
    if not isinstance(checks,list) or not 1<=len(checks)<=12:raise ValueError('확인 조건은 1~12개가 필요합니다.')
    clean=[]
    for check in checks:
        if not isinstance(check,dict) or set(check)-{'kind','selector','expected','attribute','label'}:raise ValueError('지원하지 않는 확인 조건입니다.')
        kind=check.get('kind');selector=_text(check.get('selector',''),300)
        if kind not in {'visible','absent','text','attribute','count'} or not selector or any(x in selector for x in ('\x00','\n','\r')):raise ValueError('읽기 전용 화면 확인 조건을 지정하세요.')
        item={'kind':kind,'selector':selector,'label':_text(check.get('label',selector),160)}
        if kind in {'text','attribute'}:item['expected']=_text(check.get('expected',''),500)
        if kind=='text' and not item['expected']:raise ValueError('찾을 텍스트를 지정하세요.')
        if kind=='attribute':
            if check.get('attribute') not in ATTRIBUTES:raise ValueError('읽을 수 있는 화면 속성을 지정하세요.')
            item['attribute']=check['attribute']
        if kind=='count':
            expected=check.get('expected')
            if type(expected) is not int or not 0<=expected<=10000:raise ValueError('확인할 개수를 지정하세요.')
            item['expected']=expected
        clean.append(item)
    return clean


def plan(db,context,goal,checks):
    goal=_text(goal,1000)
    if not goal.strip():raise ValueError('사용자가 원하는 관찰 가능한 결과를 기록하세요.')
    checks=validate_checks(checks)
    client=latest_client(db,context.get('client_id'))
    data={'request_id':context['request_id'],'client_id':context.get('client_id'),
          'root_request_id':context.get('root_request_id',context['request_id']),
          'goal':goal,'checks':checks,'state':'planned','results':[],
          'origin_user_message_id':context.get('user_message_id'),'original_goal':context.get('user_goal',''),
          'created_boot':BOOT,'baseline':client.get('version'),'sources':[],'nonce':uuid.uuid4().hex}
    with _db(db) as c:c.execute('INSERT INTO office_goals VALUES (?,?,?) ON CONFLICT(request_id) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                              (context['request_id'],_json(data),time.time()))
    return {'goal':goal,'checks':checks,'state':'planned','note':'확인 조건을 저장했습니다. 아직 실제 결과가 확인된 것이 아닙니다.'}


def goal_status(db,request_id):
    with contextlib.closing(sqlite3.connect(str(db),timeout=5)) as c:
        c.row_factory=sqlite3.Row
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_goals'").fetchone():return None
        row=c.execute('SELECT data FROM office_goals WHERE request_id=?',(request_id,)).fetchone()
    return json.loads(row['data']) if row else None


def _save_goal(c,data):
    c.execute('UPDATE office_goals SET data=?,updated=? WHERE request_id=?',(_json(data),time.time(),data['request_id']))


def queue_activation(db,request_id,changes):
    data=goal_status(db,request_id)
    if not data:
        data={'request_id':request_id,'goal':'요청한 앱 변경','checks':[],'results':[],
              'state':'unverified','reason':'작업 전에 사용자 결과 확인 조건을 기록하지 않았습니다.','sources':changes}
        with _db(db) as c:c.execute('INSERT OR REPLACE INTO office_goals VALUES (?,?,?)',(request_id,_json(data),time.time()))
        return data
    latest={change['path']:change for change in changes}
    data.update(state='awaiting_activation',created_boot=BOOT,sources=list(latest.values()),results=[],nonce=uuid.uuid4().hex)
    if not data.get('client_id'):data.update(state='unverified',reason='이 요청을 보낸 실제 탭이 연결되지 않아 반영 결과를 자동 확인할 수 없습니다.')
    with _db(db) as c:_save_goal(c,data)
    return data


def _sources_match(goal):
    for change in goal.get('sources',[]):
        name=change.get('path','')
        if not isinstance(name,str) or pathlib.Path(name).name!=name:return False
        path=ROOT/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=change.get('sha256'):return False
    return True


def _ready(goal,client,current):
    if not goal.get('checks') or client['version']!=current:return False
    if goal['state']=='awaiting_activation':
        if any(x.get('kind')=='backend' for x in goal.get('sources',[])) and goal.get('created_boot')==BOOT:return False
        if not _sources_match(goal):return False
    return True


def _judge(check,actual):
    if not isinstance(actual,dict) or actual.get('error'):return False
    kind=check['kind'];count=actual.get('count')
    if type(count) is not int:return False
    if kind=='absent':return count==0
    if kind=='count':return count==check['expected']
    if kind=='visible':return count>0 and actual.get('visible') is True
    if kind=='text':return check['expected'] in str(actual.get('text',''))[:2500]
    return count>0 and actual.get('attribute')==check['expected']


def update_client(db,snapshot,report=None):
    client=client_snapshot(snapshot);current=version();now=time.time();jobs=[];accepted=None;needs_reload=False
    with _db(db) as c:
        c.execute('DELETE FROM office_clients WHERE updated<?',(now-86400,))
        c.execute('INSERT OR REPLACE INTO office_clients VALUES (?,?,?)',(client['id'],_json(client),now))
        if report is not None:
            if not isinstance(report,dict):raise ValueError('화면 확인 결과를 확인하세요.')
            row=c.execute('SELECT data FROM office_goals WHERE request_id=?',(_id(report.get('request_id')),)).fetchone()
            data=json.loads(row['data']) if row else None
            if data and data.get('client_id')==client['id'] and data.get('nonce')==report.get('nonce') and data['state'] in {'checking_client','awaiting_activation'} and _ready(data,client,current):
                actuals=report.get('actuals',[])
                if not isinstance(actuals,list) or len(actuals)!=len(data['checks']):raise ValueError('화면 확인 항목 수가 달라졌습니다.')
                results=[]
                for check,actual in zip(data['checks'],actuals):
                    if not isinstance(actual,dict) or len(_json(actual))>8000:raise ValueError('화면 확인 결과가 너무 큽니다.')
                    safe={key:actual.get(key) for key in ('count','visible','text','attribute','error') if key in actual}
                    if isinstance(safe.get('text'),str):safe['text']=_text(safe['text'],2500)
                    results.append({'check':check,'actual':safe,'passed':_judge(check,safe)})
                passed=all(x['passed'] for x in results)
                data.update(state=('awaiting_review' if data.get('sources') else 'verified') if passed else 'failed',
                            results=results,observed_version=current,observed_at=now,
                            scope='현재 요청 탭의 실제 DOM 조건 확인; 외부 업무 완료와 픽셀 품질 검토는 별도')
                _save_goal(c,data)
                request=c.execute('SELECT status FROM assistant_requests WHERE id=?',(data['request_id'],)).fetchone()
                accepted={'request_id':data.get('root_request_id',data['request_id']),'actual_request_id':data['request_id'],'state':data['state'],
                          'followup':bool(data.get('sources') and request and request['status']=='done')}
        for row in c.execute('SELECT data FROM office_goals ORDER BY updated DESC LIMIT 40'):
            goal=json.loads(row['data'])
            if goal.get('client_id')!=client['id'] or goal['state'] not in {'checking_client','awaiting_activation'}:continue
            if goal['state']=='awaiting_activation':
                request=c.execute('SELECT status FROM assistant_requests WHERE id=?',(goal['request_id'],)).fetchone()
                if not request or request['status']!='done':continue
                if client['version']!=current and _sources_match(goal) and (not any(x.get('kind')=='backend' for x in goal.get('sources',[])) or goal.get('created_boot')!=BOOT):needs_reload=True
            if _ready(goal,client,current):jobs.append({key:goal[key] for key in ('request_id','nonce','checks')})
        states=[]
        seen=set()
        for row in c.execute('SELECT data,updated FROM office_goals ORDER BY updated DESC LIMIT 30'):
            goal=json.loads(row['data']);root=goal.get('root_request_id',goal['request_id'])
            if goal.get('client_id')==client['id'] and root not in seen:
                states.append({'request_id':root,'state':goal['state'],'updated':row['updated']});seen.add(root)
    return {'ok':True,'version':current,'jobs':jobs[:3],'accepted':accepted,'states':states,'needs_reload':needs_reload}


def check_live(db,context):
    data=goal_status(db,context['request_id'])
    if not data:raise ValueError('먼저 office_task_goal로 사용자 결과와 확인 조건을 기록하세요.')
    data.update(state='checking_client',nonce=uuid.uuid4().hex,results=[])
    with _db(db) as c:_save_goal(c,data)
    until=time.monotonic()+8
    while time.monotonic()<until:
        current=goal_status(db,context['request_id'])
        if current['state'] in {'verified','failed','awaiting_review'}:return current
        time.sleep(.15)
    return dict(data,note='현재 탭 응답 또는 새 화면 반영을 기다립니다. 확인 완료로 말하지 마세요.')


def observe(registry,context,screenshot=True,window_ref=None):
    client=latest_client(registry.db_path,context.get('client_id'))
    result={'client':client,'verification':goal_status(registry.db_path,context['request_id'])}
    if not screenshot:return result
    try:service=registry.windows._service()
    except Exception:return dict(result,screenshot_unavailable='실제 창 연결을 사용할 수 없습니다. DOM 관찰과 픽셀 확인을 구분하세요.')
    if window_ref:
        window=registry._get(context,window_ref,'windows_window')
        window_id=window['window_id']
        selected=next((row for row in service.windows('') if row['window_id']==window_id),None)
        if not selected or not client.get('title') or client['title'].casefold() not in selected.get('title','').casefold():
            return dict(result,screenshot_unavailable='선택한 창에서 요청한 오피스 탭이 현재 열려 있지 않습니다. 실제 탭을 선택한 뒤 다시 관찰하세요.')
    else:
        title=client.get('title','')
        if not client.get('available') or not title:
            return dict(result,screenshot_unavailable='현재 요청 탭이 연결되지 않았습니다. windows_list로 실제 대상 창을 골라 주세요.')
        candidates=[row for row in service.windows(title) if row['process'].lower() in {'chrome.exe','msedge.exe','firefox.exe','brave.exe'}]
        if len(candidates)!=1:
            return dict(result,screenshot_unavailable='현재 탭과 실제 창을 유일하게 연결하지 못했습니다. windows_list에서 실제 창을 골라 주세요.')
        window_id=candidates[0]['window_id']
    try:captured=service.capture(window_id)
    except Exception:return dict(result,screenshot_unavailable='이 창의 픽셀을 읽지 못했습니다. 최소화·보호 상태나 창 변경을 확인하세요.')
    if not client.get('available') or not client.get('title') or client['title'].casefold() not in captured.get('title','').casefold():
        return dict(result,screenshot_unavailable='선택한 창의 현재 탭 제목이 이 요청 탭과 다릅니다. windows_inspect로 브라우저 탭을 확인하고 요청한 오피스 탭을 선택한 뒤 다시 관찰하세요.')
    raw=base64.b64decode(captured.pop('image'),validate=True)
    import agent_workspace
    locations=agent_workspace.prepare(context['agent_session_id']);identifier=uuid.uuid4().hex
    path=pathlib.Path(locations['inputs_dir'])/('office-'+identifier+'.jpg')
    import self_edit
    self_edit._atomic(path,raw)
    data={'id':identifier,'client_id':context.get('client_id'),'window':captured,
          'path':str(path),'version':client.get('version'),'viewed':False}
    with _db(registry.db_path) as c:c.execute('INSERT INTO office_observations VALUES (?,?,?,?)',(identifier,context['request_id'],_json(data),time.time()))
    return dict(result,screenshot=data,note='실제 창 이미지입니다. 네이티브 view_image로 path를 열어 픽셀을 확인하세요. DOM만 읽고 이미지를 봤다고 말하지 마세요.')


def image_viewed(db,context,event):
    if event.get('kind')!='imageView' or event.get('phase')!='completed' or not event.get('path'):return
    if event.get('status') in {'failed','declined','interrupted'}:return
    viewed_path=pathlib.Path(event['path'])
    if not viewed_path.is_absolute() and context.get('agent_session_id'):
        import agent_workspace
        viewed_path=agent_workspace._paths(context['agent_session_id'])[0]/viewed_path
    with _db(db) as c:
        for row in c.execute('SELECT id,data FROM office_observations WHERE request_id=?',(context['request_id'],)):
            data=json.loads(row['data'])
            if viewed_path.resolve()==pathlib.Path(data['path']).resolve():
                data['viewed']=True;c.execute('UPDATE office_observations SET data=? WHERE id=?',(_json(data),row['id']))


def visual_review(db,context,observation_id,matched,notes):
    with _db(db) as c:
        row=c.execute('SELECT data,created FROM office_observations WHERE id=? AND request_id=?',(_id(observation_id),context['request_id'])).fetchone()
        if not row or time.time()-row['created']>120:raise ValueError('새 화면을 다시 관찰한 후 판단하세요.')
        observation=json.loads(row['data'])
        if not observation.get('viewed'):raise ValueError('해당 실제 창 이미지를 view_image로 먼저 확인하세요.')
        goal=c.execute('SELECT data FROM office_goals WHERE request_id=?',(context['request_id'],)).fetchone()
        if not goal:raise ValueError('확인할 사용자 결과를 먼저 기록하세요.')
        data=json.loads(goal['data'])
        if observation.get('version')!=version() or not _sources_match(data):raise ValueError('실행 반영 후의 최신 창을 다시 관찰하세요.')
        if not data.get('results') or not all(x['passed'] for x in data['results']):raise ValueError('먼저 실제 탭의 사용자 결과 조건을 확인하세요.')
        data['visual_review']={'observation_id':observation_id,'matched':matched is True,'notes':_text(notes,1200),'at':time.time()}
        data['state']='verified' if matched is True else 'failed'
        _save_goal(c,data)
        return data['visual_review']


def claim_followup(db,parent_id):
    """Resume only an activated, reported user task, once per concrete report."""
    with _db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM office_goals WHERE request_id=?',(_id(parent_id),)).fetchone()
        if not row:return None
        data=json.loads(row['data'])
        request=c.execute('SELECT status,response FROM assistant_requests WHERE id=?',(parent_id,)).fetchone()
        if (not request or request['status']!='done' or data['state'] not in {'awaiting_review','failed'}
                or data.get('child_request_id') or not data.get('sources') or not data.get('origin_user_message_id')):return None
        response=json.loads(request['response']) if request['response'] else {}
        message_id=response.get('result',{}).get('message_id')
        if message_id and c.execute("SELECT 1 FROM assistant_pending WHERE message_id=? AND status IN ('pending','running')",(message_id,)).fetchone():
            data.update(state='unverified',reason='사용자 선택·승인 대기 중이어서 자동 확인을 재개하지 않았습니다.');_save_goal(c,data);return None
        if not _sources_match(data):
            data.update(state='unverified',reason='이후 소스 변경이 있어 이전 작업을 자동 재개하지 않았습니다.');_save_goal(c,data);return None
        origin=c.execute('SELECT rowid FROM work_messages WHERE id=?',(data['origin_user_message_id'],)).fetchone()
        if not origin or c.execute("SELECT 1 FROM work_messages WHERE role='user' AND rowid>? LIMIT 1",(origin[0],)).fetchone():
            data.update(state='unverified',reason='새 사용자 요청이 들어와 이전 요청의 자동 수정을 재개하지 않았습니다.');_save_goal(c,data);return None
        child=hashlib.sha256((parent_id+data['nonce']).encode()).hexdigest()[:32]
        cloned=dict(data,request_id=child,state='awaiting_review',root_request_id=data.get('root_request_id',parent_id))
        cloned.pop('child_request_id',None)
        data.update(child_request_id=child,state='following_up');_save_goal(c,data)
        c.execute('INSERT INTO office_goals VALUES (?,?,?)',(child,_json(cloned),time.time()))
        return {'request_id':child,'parent_request_id':parent_id}


def resume_context(db,payload):
    parent=goal_status(db,_id(payload.get('parent_request_id')))
    if not parent or parent.get('child_request_id')!=payload['request_id']:raise ValueError('실제 반영 확인에서 이어진 요청만 재개할 수 있습니다.')
    child=goal_status(db,payload['request_id'])
    if not child:raise ValueError('확인할 원래 작업이 없습니다.')
    with _db(db) as c:
        origin=c.execute('SELECT rowid FROM work_messages WHERE id=?',(child.get('origin_user_message_id'),)).fetchone()
        if not origin or c.execute("SELECT 1 FROM work_messages WHERE role='user' AND rowid>? LIMIT 1",(origin[0],)).fetchone():raise ValueError('새 사용자 요청이 들어와 이전 작업을 재개하지 않습니다.')
    if not _sources_match(child):raise ValueError('소스가 이후 변경되어 이전 작업을 재개하지 않습니다.')
    return child


def mark_unverified(db,request_id,reason):
    data=goal_status(db,request_id)
    if data:
        data.update(state='unverified',reason=reason)
        with _db(db) as c:_save_goal(c,data)


def family(db,request_id):
    with contextlib.closing(sqlite3.connect(str(db),timeout=5)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='office_goals'").fetchone():return [request_id],None
        rows=c.execute("SELECT request_id,data FROM office_goals WHERE request_id=? OR json_extract(data,'$.root_request_id')=? ORDER BY updated DESC LIMIT 100",(request_id,request_id)).fetchall()
    return list(dict.fromkeys([request_id]+[row[0] for row in rows])),json.loads(rows[0][1]) if rows else None
