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

MEDIA_TTL = 30 * 60
IMAGE_MAX = 16 * 1024 * 1024
VIDEO_MAX = 256 * 1024 * 1024
IMAGE_TYPES = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif'}
VIDEO_TYPES = {'.mp4':'video/mp4','.webm':'video/webm'}
STEP_LABELS={'files_find':'파일 찾기','files_preview':'자료 미리보기','files_reveal':'탐색기 열기','documents_read':'문서 읽기','documents_find':'문서 찾기','stage_publish':'초안 표시','browser_open':'웹사이트 열기','browser_read':'웹페이지 읽기','browser_scroll':'웹페이지 살펴보기','apps_launch':'프로그램 실행','work_create':'업무 등록','work_status':'업무 상태 변경','work_report':'보고서 정리'}
STEP_LABELS.update({'attachments_list':'첨부 자료 확인','attachment_read':'첨부 자료 읽기·분석',
                    'attachment_preview':'첨부 미리보기','companion_assets':'캐릭터 목록 확인','companion_apply':'캐릭터 적용'})
STEP_LABELS.update({'office_source_list':'앱 원본 찾기','office_source_read':'앱 원본 읽기',
                    'office_source_apply':'앱 변경 저장','office_source_history':'앱 변경 이력',
                    'office_source_rollback':'앱 변경 되돌리기'})
CODE_TYPES = {'.py':'python','.js':'javascript','.ts':'typescript','.html':'html','.css':'css','.json':'json','.sql':'sql','.sh':'bash','.ps1':'powershell','.jsx':'jsx','.tsx':'tsx'}

def connect(path):
    c = sqlite3.connect(str(path), timeout=5)
    c.row_factory = sqlite3.Row
    return contextlib.closing(c)

def initialize(c):
    c.executescript('''CREATE TABLE IF NOT EXISTS stage_artifacts (
        id TEXT PRIMARY KEY,request_id TEXT NOT NULL,artifact TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS stage_media (
        id TEXT PRIMARY KEY,selection TEXT NOT NULL,created REAL NOT NULL);''')

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
        if not isinstance(url,str) or not re.fullmatch(r'/api/work/(?:media|attachment)\?id=[0-9a-f]{32}',url):return None
        result['url']=url
    else:
        result.update(text=_text(value.get('text'),12000),language=_text(value.get('language'),30),
                      truncated=bool(value.get('truncated')) or len(str(value.get('text','')))>12000)
    download_url=value.get('download_url')
    if isinstance(download_url,str) and re.fullmatch(r'/api/work/attachment\?id=[0-9a-f]{32}&download=1',download_url):
        result['download_url']=download_url
    return result

def snapshot(db, request_id=None):
    if request_id is not None and not re.fullmatch('[0-9a-fA-F]{32}',request_id): raise ValueError('유효한 요청 ID가 필요합니다.')
    empty={'request':None,'steps':[],'artifacts':[],'answer':''}
    with connect(db) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_requests'").fetchone(): return empty
        row=c.execute('SELECT * FROM assistant_requests WHERE id=?',(request_id,)).fetchone() if request_id else c.execute('SELECT * FROM assistant_requests ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
        if not row: return empty
        payload=json.loads(row['payload']).get('payload',{})
        steps=[]; artifacts=[]; attention=False
        for step in c.execute('SELECT id,name,status,result,created FROM assistant_steps WHERE request_id=? ORDER BY created,rowid LIMIT 50',(row['id'],)):
            result=json.loads(step['result']) if step['result'] else {}
            status='running' if step['status']=='running' else 'pending' if result.get('pending') else 'ok' if result.get('status')=='ok' else 'error' if result.get('status')=='error' else 'unknown'
            attention=attention or status in {'error','pending','unknown'}
            steps.append({'id':step['id'],'name':step['name'],'label':STEP_LABELS.get(step['name'],'작업 처리'),'status':status,'created':step['created'],'message':_text(result.get('message'),500)})
            artifact=result.get('data',{}).get('artifact') if isinstance(result.get('data'),dict) else None
            safe_artifact=_artifact(artifact)
            if safe_artifact:artifacts.append(safe_artifact)
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='stage_artifacts'").fetchone()
        if exists:
            artifacts.extend(value for a in c.execute('SELECT artifact FROM stage_artifacts WHERE request_id=? ORDER BY created LIMIT 12',(row['id'],)) if (value:=_artifact(json.loads(a[0]))))
        response=json.loads(row['response']) if row['response'] else {}
        result=response.get('result',{})
        answer=_text(result.get('answer',''),12000)
        attention=attention or any(r.get('status')=='error' for r in result.get('tool_results',[]) if isinstance(r,dict))
        drafts = result.get('drafts', [])
        drafts = drafts[:4] if isinstance(drafts,list) else []
        drafts += [{'text':match[2],'language':match[1]} for match in
                   list(re.finditer(r'```([\w+.-]{0,30})[ \t]*\r?\n([\s\S]*?)```',answer))[:4]]
        for index,draft in enumerate(drafts):
            if not isinstance(draft,dict) or not isinstance(draft.get('text'),str):continue
            text=_text(draft['text'],6000)
            if any(a.get('kind')=='code' and a.get('text','').strip()==text.strip() for a in artifacts):continue
            artifacts.append({'id':row['id']+'-draft-'+str(index),'kind':'code','title':'코드 초안',
                              'language':_text(draft.get('language'),30),'text':text,'origin':'draft'})
        artifacts=list({a['id']:a for a in artifacts if isinstance(a,dict) and 'id' in a}.values())[:12]
        # A stale running request is uncertain, never a reason to dispatch it again.
        stale=False
        try:stale=(datetime.datetime.now().astimezone()-datetime.datetime.fromisoformat(row['created'])).total_seconds()>600
        except (ValueError,TypeError):stale=True
        status=('attention' if attention else 'done') if row['status']=='done' else 'attention' if stale else 'running'
        request={'id':row['id'],'status':status,'created':row['created'],'title':'Aemeath 작업','outcome':'확인이 필요합니다.' if status=='attention' else '요청 처리가 종료되었습니다.' if status=='done' else '작업 중입니다.'}
        if payload.get('task_id'):request['task_id']=payload['task_id']
        # The chat explanation is not a deliverable. Keep the response key for
        # older clients, but only explicitly published/previewed artifacts show.
        return {'request':request,'steps':steps,'artifacts':artifacts,'answer':''}
