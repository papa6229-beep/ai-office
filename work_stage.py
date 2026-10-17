"""Bounded read-only workspace views. Artifacts are drafts or validated sources, never programs."""
import contextlib
import datetime
import io
import json
import os
import pathlib
import re
import sqlite3
import time
import uuid

import document_reader
import file_workspace
import office_feedback

MEDIA_TTL = 30 * 60
IMAGE_MAX = 16 * 1024 * 1024
VIDEO_MAX = 256 * 1024 * 1024
IMAGE_TYPES = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif'}
VIDEO_TYPES = {'.mp4':'video/mp4','.webm':'video/webm'}
STEP_LABELS={'files_find':'파일 찾기','files_preview':'자료 미리보기','files_reveal':'탐색기 열기','documents_read':'문서 읽기','documents_find':'문서 찾기','stage_publish':'초안 표시','browser_open':'웹사이트 열기','browser_read':'웹페이지 읽기','browser_scroll':'웹페이지 살펴보기','apps_launch':'프로그램 실행','work_create':'업무 등록','work_status':'업무 상태 변경','work_report':'보고서 정리'}
STEP_LABELS.update({'attachments_list':'첨부 자료 확인','attachment_read':'첨부 자료 읽기·분석',
                    'attachment_preview':'첨부 미리보기','companion_assets':'캐릭터 목록 확인','companion_apply':'캐릭터 적용'})
STEP_LABELS['work_edit']='업무 내용·상태 수정'
STEP_LABELS.update({'notifications_list':'수신 알림 목록','notification_read':'알림 내용 확인','notification_confirm':'알림 확인 완료'})
STEP_LABELS.update({'desktop_observe':'데스크톱 화면 관찰','desktop_action':'마우스·키보드·창 조작','browser_inspect':'웹 입력란·버튼 관찰','browser_act':'웹 입력·클릭','browser_export':'웹 내용 복사','text_document':'텍스트 문서·메모장 연결'})
STEP_LABELS.update({'office_source_list':'앱 원본 찾기','office_source_read':'앱 원본 읽기',
                    'office_source_apply':'앱 변경 저장','office_source_history':'앱 변경 이력',
                    'office_source_rollback':'앱 변경 되돌리기'})
STEP_LABELS.update({'agent_workspace_status':'작업 사본 확인','agent_source_publish':'앱 변경 게시','agent_output_publish':'결과물 게시'})
STEP_LABELS.update({'office_ui_observe':'실제 화면 관찰','office_task_goal':'사용자 결과·확인 조건 기록',
                    'office_ui_check':'현재 화면 결과 확인','office_ui_review':'실제 창 이미지 검토'})
STEP_LABELS.update({'codex_command':'코드·작업 실행','codex_file_change':'작업 파일 수정',
                    'codex_image_view':'이미지 확인','codex_web_search':'자료 검색','codex_context':'작업 문맥 정리'})
CODE_TYPES = {'.py':'python','.js':'javascript','.ts':'typescript','.html':'html','.css':'css','.json':'json','.sql':'sql','.sh':'bash','.ps1':'powershell','.jsx':'jsx','.tsx':'tsx'}

def connect(path):
    c = sqlite3.connect(str(path), timeout=5)
    c.row_factory = sqlite3.Row
    return contextlib.closing(c)

def initialize(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS stage_artifacts (
        id TEXT PRIMARY KEY,request_id TEXT NOT NULL,artifact TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS stage_media (
        id TEXT PRIMARY KEY,selection TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS stage_task_notes (
        request_id TEXT PRIMARY KEY,title TEXT NOT NULL,summary TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS stage_reviews (
        request_id TEXT PRIMARY KEY,decision TEXT NOT NULL,note TEXT NOT NULL,updated REAL NOT NULL);''')

def set_review(db, request_id, decision):
    if not isinstance(request_id,str) or not re.fullmatch('[0-9a-fA-F]{32}',request_id):
        raise ValueError('작업 요청을 확인할 수 없습니다.')
    if decision not in {'confirmed','needs_changes'}: raise ValueError('확인 상태를 선택하세요.')
    with connect(db) as c,c:
        initialize(c)
        if not c.execute('SELECT 1 FROM assistant_requests WHERE id=?',(request_id,)).fetchone():
            raise ValueError('작업 요청을 확인할 수 없습니다.')
        c.execute('INSERT INTO stage_reviews VALUES (?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET decision=excluded.decision,updated=excluded.updated',
                  (request_id,decision,'',time.time()))
    return {'request_id':request_id,'decision':decision}


def task_note(db, request_id, title, summary=''):
    """Model-authored intent, bound to the host request; never completion evidence."""
    if not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id):
        raise ValueError('현재 작업 요청이 필요합니다.')
    if not isinstance(title,str) or not title.strip() or len(title)>60 or not isinstance(summary,str) or len(summary)>240:
        raise ValueError('작업 제목은 60자, 요약은 240자 이내로 작성하세요.')
    title=' '.join(_text(title,60).split());summary=' '.join(_text(summary,240).split())
    with connect(db) as c,c:
        initialize(c)
        if not c.execute('SELECT 1 FROM assistant_requests WHERE id=?',(request_id,)).fetchone():
            raise ValueError('현재 작업 요청을 확인할 수 없습니다.')
        c.execute('INSERT INTO stage_task_notes VALUES (?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET title=excluded.title,summary=excluded.summary,updated=excluded.updated',
                  (request_id,title,summary,time.time()))
    return {'title':title,'summary':summary,'intent_only':True}


def _task_identity(note, operations, artifacts):
    # Old read/list receipts alone do not turn an ordinary conversation into work.
    actual=[op for op in operations if op['name'] not in {'workspace_task_note','office_task_goal'} and
            (op['effect'] in {'write','external','approval'} and op['name']!='codex_command')]
    analysis={'documents_read','attachment_read','browser_read','browser_view','codex_command','codex_image_view','codex_web_search'}
    eligible=bool(actual or artifacts or (note and any(op['name'] in analysis for op in operations)))
    if note:return eligible,note['title'],note['summary']
    names={op['name'] for op in actual}
    if names & {'office_source_apply','agent_source_publish','office_source_rollback','codex_file_change'}:
        title='AI OFFICE 화면·기능 수정'
        paths=[]
        for op in actual:
            path=op['arguments'].get('path','')
            if isinstance(path,str) and path:
                label=re.split(r'[/\\]',path)[-1]
                if label not in paths:paths.append(label)
        summary='수정 대상: '+', '.join(paths[:3]) if paths else '앱 변경 작업과 실제 실행 기록'
    elif artifacts:
        title=_text(artifacts[0].get('title'),60) or '결과물 작성'
        summary='게시한 결과물과 작업 기록'
    elif actual:
        title=STEP_LABELS.get(actual[-1]['name'],'업무 작업 처리')
        summary='실제 수행 단계와 처리 결과'
    else:title='작업 요청';summary=''
    return eligible,title,summary

def _text(value, maximum):
    text=document_reader._redact(str(value or ''))
    text=re.sub(r'((?:비밀번호|암호)\s*(?:(?:은|는|이|가)\s*|[:=]\s*))[\w@!#$%^&*.-]+',r'\1[가림]',text)
    return text[:maximum]

def publish(db, request_id, kind, title, text, language=''):
    if kind not in {'text','code'} or not isinstance(text,str) or len(text)>3000:
        raise ValueError('초안은 코드·텍스트 3000자 이하로 작성하세요.')
    if not isinstance(request_id,str) or not re.fullmatch('[0-9a-fA-F]{32}',request_id):
        raise ValueError('현재 작업 요청이 필요합니다.')
    artifact={'id':uuid.uuid4().hex,'kind':kind,'title':_text(title,120),'text':_text(text,3000),'language':_text(language,30),'origin':'draft'}
    with connect(db) as c, c:
        initialize(c)
        if c.execute('SELECT 1 FROM assistant_requests WHERE id=?',(request_id,)).fetchone() is None:
            raise ValueError('현재 작업 요청을 확인할 수 없습니다.')
        if c.execute('SELECT count(*) FROM stage_artifacts WHERE request_id=?',(request_id,)).fetchone()[0]>=12:
            raise ValueError('한 요청에는 초안을 12개까지 표시합니다.')
        c.execute('INSERT INTO stage_artifacts VALUES (?,?,?,?)',(artifact['id'],request_id,json.dumps(artifact,ensure_ascii=False),time.time()))
    return artifact

def _resource_path(db, resource_id):
    from work_assistant import _resource
    with connect(db) as c:
        row=c.execute('SELECT path FROM work_resources WHERE id=?',(resource_id,)).fetchone()
    if not row:raise ValueError('등록된 업무 자료를 선택하세요.')
    return pathlib.Path(_resource(row['path'])[0])

def _checked(files, expected):
    if expected.get('resource_id'):
        livepath=_resource_path(files.db_path,expected['resource_id'])
        if str(livepath)!=expected['path']: raise ValueError('업무 자료가 변경되었습니다.')
        path,info=file_workspace._safe(livepath)
    else:
        live=files.resolve(expected['id'])
        if any(live.get(k)!=expected.get(k) for k in ('id','path','kind','identity')):
            raise ValueError('파일이 변경되었습니다. 다시 선택하세요.')
        path,info=file_workspace._safe(live['path'])
    if file_workspace._identity(info)!=expected['identity'] or not path.is_file():
        raise ValueError('파일이 변경되었습니다. 다시 선택하세요.')
    return path,info

@contextlib.contextmanager
def _opened(files, expected):
    path,info=_checked(files,expected)
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor,'rb') as stream:
        if file_workspace._identity(os.fstat(stream.fileno()))!=expected['identity']:
            raise ValueError('파일이 변경되었습니다.')
        yield path,info,stream
        if file_workspace._identity(os.fstat(stream.fileno()))!=expected['identity']:
            raise ValueError('읽는 동안 파일이 변경되었습니다.')
    _checked(files,expected)

def preview(db, files, expected):
    with _opened(files,expected) as (path,info,stream):
        ext=path.suffix.lower()
        artifact={'id':uuid.uuid4().hex,'title':path.name,'origin':'source'}
        if ext in IMAGE_TYPES:
            if not 0<info.st_size<=IMAGE_MAX: raise ValueError('이미지는 16MB 이하만 미리 봅니다.')
            raw=stream.read(IMAGE_MAX+1)
            try:
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as image:
                    expected_format={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.webp':'WEBP','.gif':'GIF'}[ext]
                    frames=getattr(image,'n_frames',1)
                    if image.format!=expected_format or image.width*image.height>32000000 or frames>300 or image.width*image.height*frames>64000000:
                        raise ValueError('이미지 픽셀·프레임 제한을 넘습니다.')
                    for frame in range(frames):
                        image.seek(frame)
                        image.load()
            except ValueError: raise
            except Exception: raise ValueError('지원하는 이미지 형식을 확인할 수 없습니다.') from None
            artifact['kind']='image'
        elif ext in VIDEO_TYPES:
            if not 0<info.st_size<=VIDEO_MAX: raise ValueError('영상은 256MB 이하만 미리 봅니다.')
            head=stream.read(4096)
            if (ext=='.mp4' and not (len(head)>=12 and head[4:8]==b'ftyp')) or (ext=='.webm' and not(head.startswith(b'\x1aE\xdf\xa3') and b'webm' in head)):
                raise ValueError('영상 형식을 확인할 수 없습니다.')
            artifact['kind']='video'
        else:
            data=document_reader.read(path)
            artifact.update(kind='code' if ext in CODE_TYPES else 'text',text=data['text'][:12000],language=CODE_TYPES.get(ext,''),truncated=data.get('truncated',False))
    if artifact['kind'] in {'image','video'}:
        with connect(db) as c, c:
            initialize(c)
            c.execute('DELETE FROM stage_media WHERE created<?',(time.time()-MEDIA_TTL,))
            c.execute('INSERT INTO stage_media VALUES (?,?,?)',(artifact['id'],json.dumps(expected,ensure_ascii=False),time.time()))
            c.execute('DELETE FROM stage_media WHERE id NOT IN (SELECT id FROM stage_media ORDER BY created DESC LIMIT 200)')
        artifact['url']='/api/work/media?id='+artifact['id']
    return {'artifact':artifact}

def resource_preview(db, files, resource_id):
    if not isinstance(resource_id,str) or not re.fullmatch('[0-9a-fA-F]{32}',resource_id):
        raise ValueError('등록된 업무 자료를 선택하세요.')
    path=_resource_path(db,resource_id)
    path,info=file_workspace._safe(path)
    expected={'resource_id':resource_id,'path':str(path),'identity':file_workspace._identity(info)}
    return preview(db,files,expected)

def serve_media(handler, db, files, media_id):
    if not isinstance(media_id,str) or not re.fullmatch('[0-9a-f]{32}',media_id): raise ValueError('미리보기가 만료되었습니다.')
    with connect(db) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='stage_media'").fetchone():raise ValueError('미리보기가 만료되었습니다.')
        row=c.execute('SELECT selection,created FROM stage_media WHERE id=?',(media_id,)).fetchone()
    if not row or row['created']<time.time()-MEDIA_TTL: raise ValueError('미리보기가 만료되었습니다. 다시 선택하세요.')
    expected=json.loads(row['selection'])
    with _opened(files,expected) as (path,info,stream):
        mime=(IMAGE_TYPES|VIDEO_TYPES).get(path.suffix.lower())
        if not mime: raise ValueError('미리보기 형식을 확인할 수 없습니다.')
        size=info.st_size; start=0; end=size-1; code=200
        requested=handler.headers.get('Range')
        if requested:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
            valid=bool(match and any(match.groups()))
            if valid:
                left,right=match.groups()
                if left: start=int(left); end=min(int(right),size-1) if right else size-1
                else: start=max(0,size-int(right)); end=size-1
                valid=0<=start<=end<size
            if not valid:
                handler.send_response(416);handler.send_header('Content-Range','bytes */'+str(size));handler.send_header('Content-Length','0');handler.end_headers();return
            code=206
        handler._stage_media_started=True
        handler.send_response(code)
        for name,value in [('Content-Type',mime),('Content-Length',str(end-start+1)),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Cross-Origin-Resource-Policy','same-origin'),('Accept-Ranges','bytes')]: handler.send_header(name,value)
        if code==206:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        handler.end_headers();stream.seek(start);remaining=end-start+1
        while remaining:
            chunk=stream.read(min(65536,remaining))
            if not chunk: break
            handler.wfile.write(chunk);remaining-=len(chunk)

def _artifact(value):
    if not isinstance(value,dict) or value.get('kind') not in {'code','text','image','video'}:return None
    result={'id':_text(value.get('id'),80),'kind':value['kind'],'title':_text(value.get('title'),120),
            'origin':value.get('origin') if value.get('origin') in {'source','applied','attachment'} else 'draft'}
    if not result['id']:return None
    if result['kind'] in {'image','video'}:
        url=value.get('url','')
        if not isinstance(url,str) or not re.fullmatch(r'/api/work/(?:media|attachment|agent-artifact)\?id=[0-9a-f]{32}',url):return None
        result['url']=url
    else:
        result.update(text=_text(value.get('text'),12000),language=_text(value.get('language'),30),
                      truncated=bool(value.get('truncated')) or len(str(value.get('text','')))>12000)
    download_url=value.get('download_url')
    if isinstance(download_url,str) and re.fullmatch(r'/api/work/(?:attachment|agent-artifact)\?id=[0-9a-f]{32}&download=1',download_url):
        result['download_url']=download_url
    return result

def request_list(db):
    with connect(db) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_requests'").fetchone(): return []
        rows=c.execute('SELECT id,payload,created FROM assistant_requests r WHERE EXISTS '
                       '(SELECT 1 FROM assistant_steps s WHERE s.request_id=r.id) '
                       'ORDER BY created DESC,rowid DESC LIMIT 300').fetchall()
    items=[]
    for row in rows:
        try:
            envelope=json.loads(row['payload'])
            if envelope.get('action')=='verify-followup':continue
            payload=envelope.get('payload',{})
        except (ValueError,TypeError): payload={}
        message=_text(payload.get('message',''),500)
        if not message: message='작업 요청'
        detail=snapshot(db,row['id'])
        if detail['request'] and detail.get('workspace_eligible'):
            items.append({'request':detail['request'],'summary':detail['request']['title'],'data':detail})
            if len(items)>=30:break
    return items

def snapshot(db, request_id=None):
    if request_id is not None and not re.fullmatch('[0-9a-fA-F]{32}',request_id): raise ValueError('유효한 요청 ID가 필요합니다.')
    if request_id is None:
        return {'request':None,'steps':[],'artifacts':[],'answer':'',
                'requests':request_list(db)}
    empty={'request':None,'steps':[],'artifacts':[],'answer':''}
    related,verification=office_feedback.family(db,request_id)
    placeholders=','.join('?' for _ in related)
    with connect(db) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_requests'").fetchone(): return empty
        row=c.execute('SELECT * FROM assistant_requests WHERE id=?',(request_id,)).fetchone() if request_id else c.execute('SELECT * FROM assistant_requests ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
        if not row: return empty
        payload=json.loads(row['payload']).get('payload',{})
        steps=[]; artifacts=[]; attention=False;operations=[];unresolved={};source_refs={}
        for step in c.execute('SELECT id,name,effect,arguments,status,result,created FROM assistant_steps WHERE request_id IN ('+placeholders+') ORDER BY created,rowid',related):
            result=json.loads(step['result']) if step['result'] else {}
            try:arguments=json.loads(step['arguments'])
            except (ValueError,TypeError):arguments={}
            if not isinstance(arguments,dict):arguments={}
            operations.append({'name':step['name'],'effect':step['effect'],'arguments':arguments})
            if step['name']=='workspace_task_note':continue
            data=result.get('data') if isinstance(result.get('data'),dict) else {}
            if step['name']=='office_source_read' and data.get('path') and arguments.get('ref'):
                source_refs[arguments['ref']]=data['path']
            status='running' if step['status']=='running' else 'pending' if result.get('pending') else 'ok' if result.get('status')=='ok' else 'error' if result.get('status')=='error' else 'unknown'
            source_path=source_refs.get(arguments.get('ref')) if isinstance(arguments,dict) else None
            if step['name']=='agent_source_publish':source_path=arguments.get('path')
            if source_path:source_path=str(source_path).replace('\\','/').removeprefix('source/')
            key=('source',source_path) if source_path and step['name'] in {'office_source_apply','agent_source_publish'} else (step['name'],json.dumps(arguments,sort_keys=True))
            if status in {'error','pending','unknown'}:unresolved[key]=status
            elif status=='ok':unresolved.pop(key,None)
            steps.append({'id':step['id'],'name':step['name'],'label':STEP_LABELS.get(step['name'],'작업 처리'),'status':status,'created':step['created'],'message':_text(result.get('message'),500)})
            artifact=result.get('data',{}).get('artifact') if isinstance(result.get('data'),dict) else None
            safe_artifact=_artifact(artifact)
            if safe_artifact:artifacts.append(safe_artifact)
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='stage_artifacts'").fetchone()
        if exists:
            artifacts.extend(value for a in c.execute('SELECT artifact FROM stage_artifacts WHERE request_id IN ('+placeholders+') ORDER BY created LIMIT 12',related) if (value:=_artifact(json.loads(a[0]))))
        response=json.loads(row['response']) if row['response'] else {}
        initialize(c)
        review=c.execute('SELECT decision FROM stage_reviews WHERE request_id=?',(row['id'],)).fetchone()
        result=response.get('result',{})
        attention=bool(unresolved) or (not operations and any(r.get('status')=='error' for r in result.get('tool_results',[]) if isinstance(r,dict)))
        # Fenced code in a chat answer is not a published deliverable. Only
        # explicit stage publications and actual tool artifacts belong here.
        artifacts=list({a['id']:a for a in artifacts if isinstance(a,dict) and 'id' in a}.values())[:12]
        # A stale running request is uncertain, never a reason to dispatch it again.
        stale=False
        try:stale=(datetime.datetime.now().astimezone()-datetime.datetime.fromisoformat(row['created'])).total_seconds()>600
        except (ValueError,TypeError):stale=True
        status=('attention' if attention else 'done') if row['status']=='done' else 'running'
        if row['status']=='done' and verification:
            if verification['state'] in {'failed','unverified'}:status='attention'
            elif verification['state']=='verified':status='done'
            else:status='verifying'
        if review and row['status']=='done':status='done' if review['decision']=='confirmed' else 'attention'
        note=None
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='stage_task_notes'").fetchone():
            note=c.execute('SELECT title,summary FROM stage_task_notes WHERE request_id=?',(row['id'],)).fetchone()
        eligible,title,summary=_task_identity(note,operations,artifacts)
        request={'id':row['id'],'status':status,'created':row['created'],'title':title,
                 'summary':summary,'workspace_eligible':eligible,'uncertain':stale and status=='running',
                 'outcome':'사용자가 확인 완료했습니다.' if review and review['decision']=='confirmed' else '사용자가 수정·재확인을 요청했습니다.' if review else '확인이 필요합니다.' if status=='attention' else '요청 처리가 종료되었습니다.' if status=='done' else '작업 중입니다.',
                 'user_review':review['decision'] if review else None}
        if payload.get('task_id'):request['task_id']=payload['task_id']
        # The chat explanation is not a deliverable. Keep the response key for
        # older clients, but only explicitly published/previewed artifacts show.
        diagnostics=[]
        for receipt in result.get('tool_results',[]):
            if isinstance(receipt,dict) and receipt.get('status') in {'error','uncertain'}:
                message=_text(receipt.get('message'),1000)
                if message and message not in {entry['message'] for entry in diagnostics}:
                    diagnostics.append({'status':receipt['status'],'message':message})
                if len(diagnostics)>=20:break
        return {'request':request,'workspace_eligible':eligible,'steps':steps[-100:],'artifacts':artifacts,
                'diagnostics':diagnostics,'verification':verification,'answer':''}
