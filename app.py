import scanner
import updater
import project_intelligence
import workspace_insights
import content_analysis
import local_content_review
import local_image_review
import desktop_workspace
import desktop_preferences
import desktop_collections
import organizer_lab
import organizer_policy
import desktop_judgment
import desktop_recovery
import work_assistant
import quick_launcher
import browser_bookmarks
import assistant_model
import lmstudio_runtime
import model_requests
import model_connections
import chat_stream
import office_reload
import office_feedback
import agent_workspace
import work_attachments
import companion_assets
import browser_workspace
import windows_workspace
import capabilities
import work_stage
import office_weather
import office_notifications
import file_workspace
import base64, json, os, sqlite3, threading, urllib.request, urllib.error, uuid, pathlib, datetime, re, subprocess, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
ROOT=pathlib.Path(__file__).resolve().parent
_AEMEATH_ASSETS = frozenset(f'/assets/aemeath/{state}.{extension}'
                          for state in ('answer', 'idle', 'agree', 'error', 'typing', 'working')
                          for extension in ('gif', 'png'))
DB=ROOT/'office.db'
CFG=ROOT/'settings.json'
HOST='127.0.0.1'; PORT=8765
DEFAULT={'gemma_url':'http://127.0.0.1:1234/v1','gemma_model':'gpt-6-luna','vision_model':'gpt-6-luna','api_type':'codex','lmstudio_auto_start':False,'ai_on_demand_only':True,'api_key':'','workspace':str(pathlib.Path.home()/'Documents'/'AI-Office'),'timeout':60}
def config():
    if not CFG.exists(): CFG.write_text(json.dumps(DEFAULT,ensure_ascii=False,indent=2),encoding='utf-8')
    return {**DEFAULT,**json.loads(CFG.read_text(encoding='utf-8'))}
def db():
    c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,title TEXT NOT NULL,detail TEXT DEFAULT "",status TEXT DEFAULT "todo",created TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,kind TEXT,status TEXT,summary TEXT,detail TEXT,created TEXT)')
    c.commit();return c
def now():return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def call_gemma(prompt,settings=None):
    s=settings if settings is not None else config()
    model_connections.require_runnable(s)
    if s.get('api_type')=='codex':
        import codex_bridge
        return codex_bridge.ask(prompt,s,background=s.get('_judgment_background') is True)
    base=local_content_review.validate_endpoint(s['gemma_url']); t=int(s.get('timeout',60));model=s['gemma_model']
    lmstudio_runtime.ensure_ready(s)
    managed=lmstudio_runtime.is_managed(s)
    if s['api_type']=='openai':
        u=base+'/chat/completions';payload={'model':model,'messages':[{'role':'system','content':'당신은 한국어 업무 보조 AI입니다. 임의 실행을 약속하지 않고 확인 가능한 사실만 보고합니다.'},{'role':'user','content':prompt}],'temperature':0.2};headers={'Content-Type':'application/json'}
    else:
        u=base+'/api/chat';payload={'model':model,'messages':[{'role':'system','content':'당신은 한국어 업무 보조 AI입니다. 임의 실행을 약속하지 않고 확인 가능한 사실만 보고합니다.'},{'role':'user','content':prompt}],'stream':False,'options':{'temperature':0.2}};headers={'Content-Type':'application/json'}
    # Per-call judgment limits stay in the captured snapshot, never in user settings.
    if s.get('_judgment_json') is True:
        if s['api_type']=='openai':
            payload['response_format']=({'type':'json_schema','json_schema':{'name':'office_judgment','schema':{'type':'object'}}}
                                        if managed else {'type':'json_object'})
        else:payload['format']='json'
    output_limit=s.get('_judgment_max_tokens')
    if type(output_limit) is int and 128<=output_limit<=1536:
        if s['api_type']=='openai':payload['max_tokens']=output_limit
        else:payload['options']['num_predict']=output_limit
    if s['api_type']!='openai':payload['think']=s.get('_judgment_think') is True
    elif managed:payload['reasoning_effort']='none'
    if s.get('api_key'):headers['Authorization']='Bearer '+s['api_key']
    req=urllib.request.Request(u,data=json.dumps(payload).encode(),headers=headers,method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with model_requests.request_slot(background=s.get('_judgment_background') is True):
        with urllib.request.build_opener(NoRedirect).open(req,timeout=t) as r:response=json.load(r)
    if s['api_type']=='openai':return response['choices'][0]['message']['content']
    return response['message']['content']
def call_gemma_vision(prompt,image_b64,settings=None):
    s=settings if settings is not None else config()
    model_connections.require_runnable(s)
    if s.get('api_type')=='codex':
        import codex_bridge
        header=base64.b64decode(image_b64[:32],validate=True)
        mime='image/png' if header.startswith(b'\x89PNG\r\n\x1a\n') else (
            'image/webp' if header.startswith(b'RIFF') and header[8:12]==b'WEBP' else 'image/jpeg')
        return codex_bridge.ask(prompt,s,image_data_url='data:'+mime+';base64,'+image_b64,
                                background=s.get('_judgment_background') is True)
    managed=lmstudio_runtime.is_managed(s)
    if s.get('api_type')!='ollama' and not managed:raise ValueError('이미지 내용 분석은 Ollama Gemma 또는 관리되는 로컬 E4B 연결에서 지원합니다.')
    lmstudio_runtime.ensure_ready(s)
    base=local_content_review.validate_endpoint(s['gemma_url']);model=str(s.get('vision_model') or '').strip() or s['gemma_model'];t=int(s.get('timeout',60))
    if managed:
        if model!='google/gemma-4-e4b':raise ValueError('로컬 이미지 분석 모델은 google/gemma-4-e4b로 설정하세요.')
        # Scanner composites are JPEG; browser captures and the no-Pillow
        # fallback retain PNG/WebP bytes. Declare the actual bounded input.
        header=base64.b64decode(image_b64[:32],validate=True)
        mime='image/png' if header.startswith(b'\x89PNG\r\n\x1a\n') else (
            'image/webp' if header.startswith(b'RIFF') and header[8:12]==b'WEBP' else 'image/jpeg')
        payload={'model':model,'messages':[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+image_b64}}]}],
                 'stream':False,'temperature':0.2,'max_tokens':1536,'reasoning_effort':'none'}
        route='/chat/completions'
    else:
        payload={'model':model,'messages':[{'role':'user','content':prompt,'images':[image_b64]}],'stream':False,'options':{'temperature':0.2}}
        route='/api/chat'
    headers={'Content-Type':'application/json'}
    if s.get('api_key'):headers['Authorization']='Bearer '+s['api_key']
    req=urllib.request.Request(base+route,data=json.dumps(payload).encode(),headers=headers,method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with model_requests.request_slot(background=s.get('_judgment_background') is True):
        with urllib.request.build_opener(NoRedirect).open(req,timeout=t) as r:response=json.load(r)
    return response['choices'][0]['message']['content'] if managed else response['message']['content']
def runlog(kind,status,summary,detail=''):
    with db() as c:c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',(uuid.uuid4().hex,kind,status,summary,detail,now()))
DESK_TOKEN=uuid.uuid4().hex
DESK_ACTIONS={'/api/desk/'+name for name in ('open','reveal','pin','create-collection','rename-collection','remove-member','dissolve-collection','dismiss-suggestion','restore-dismissed','duplicates','auto-organize','undo-auto','organizer-create','organizer-execute','organizer-undo','policy-save','policy-preview','policy-run','policy-enabled','policy-undo','policy-resume','judgment-start','judgment-stop','judgment-retain','judgment-hold','judgment-restore')}
_AI_READY=False
_AI_THREAD=None
_AI_STOP=threading.Event()
_AI_LOCK=threading.Lock()

def configure_ai():
    global _AI_READY
    with _AI_LOCK:
        if not _AI_READY:
            desktop_judgment.configure(config,call_gemma,call_gemma_vision)
            desktop_recovery.reconcile()
            _AI_READY=True

def start_ai_supervisor():
    global _AI_THREAD
    configure_ai()
    settings=config()
    if settings.get('api_type')=='codex' or settings.get('ai_on_demand_only') is True:return
    with _AI_LOCK:
        if _AI_THREAD and _AI_THREAD.is_alive():return
        _AI_STOP.clear()
        def supervise():
            while not _AI_STOP.is_set():
                try:
                    settings=config()
                    if settings.get('api_type')=='codex' or settings.get('ai_on_demand_only') is True:break
                    state=desktop_judgment.status()
                    if state.get('enabled') and not state.get('running'):desktop_judgment.start()
                except (ValueError,OSError):pass
                if _AI_STOP.wait(60):break
        _AI_THREAD=threading.Thread(target=supervise,name='AI OFFICE desktop understanding',daemon=True)
        _AI_THREAD.start()

def stop_ai_supervisor():
    _AI_STOP.set()
    desktop_judgment.stop()

def judgment_view():
    configure_ai()
    data=desktop_judgment.status()
    eligibility=desktop_recovery.holdability([item['id'] for item in data.get('candidates',[])])
    for item in data.get('candidates',[]):
        item['hold_allowed']=eligibility.get(item['id'],{}).get('allowed',False)
        item['hold_note']=eligibility.get(item['id'],{}).get('reason','')
    try:
        synced=desktop_collections.sync_content_groups(data.get('groups',[]))
        data['applied_group_count']=sum(group.get('source')=='content_ai' for group in synced['collections'])
    except ValueError:
        data['group_warning']='작업함 설정을 보존했습니다. 내용 관계는 분석 결과에서 확인할 수 있습니다.'
    data['recovery']=desktop_recovery.status()
    return data

def work_service():
    from assistant_tools import ToolRegistry
    settings=dict(config())
    model_connections.require_runnable(settings)
    return work_assistant.WorkAssistant(DB,config,call_gemma,
        native_model=lambda messages,tools:call_assistant_model(messages,tools,settings),
        intent_model=lambda messages,tools:call_assistant_intent(messages,tools,settings),
        agent_model=(lambda messages,tools,dispatch,**hooks:assistant_model.run_agent(messages,tools,settings,dispatch,**hooks))
                     if settings.get('api_type')=='codex' else None,
        registry_factory=lambda:ToolRegistry(DB,
            work_factory=lambda:work_assistant.WorkAssistant(DB,config,call_gemma),
            configure_ai=configure_ai,browser_service=browser_workspace.manager(),
            vision_model=lambda prompt,image:call_gemma_vision(prompt,image,settings),
            workspace=settings.get('workspace'),web_opener=open_managed_web,windows_service=windows_workspace.manager(),
            model_settings=settings))

def capability_service():
    return capabilities.CapabilityService(DB,workspace=config().get('workspace'))

def resume_screen_verification(server,parent_id):
    # Continue the same user-authorized task only after its actual UI report.
    payload=None;ticket=None
    try:
        with office_reload.action() as admitted:
            if not admitted or config().get('api_type')!='codex':return
            payload=office_feedback.claim_followup(DB,parent_id)
            if not payload:return
            response=work_service().perform('verify-followup',payload)
            changes=response.get('result',{}).get('source_changes',[])
            if changes:ticket=office_reload.prepare(changes,payload['request_id'])
    except Exception:
        if payload:office_feedback.mark_unverified(DB,payload['request_id'],'실제 화면 확인을 이어가는 연결이 중단됐습니다. 완료로 확정하지 않습니다.')
    finally:office_reload.schedule(server,ticket)

def open_managed_web(url):
    # Reuse the AI OFFICE Chrome workspace so launcher sites become tabs in
    # the same managed window instead of spawning the system browser.
    return browser_workspace.manager().open(url)

def launcher_service():
    return quick_launcher.QuickLauncher(DB,web_opener=open_managed_web)

def call_assistant_model(messages,tools,settings=None):
    return assistant_model.call(messages,tools,settings if settings is not None else config())

def call_assistant_intent(messages,tools,settings=None):
    return assistant_model.execution_decision(messages,tools,settings if settings is not None else config())

def work_view():
    data=work_service().overview()
    data['action_token']=DESK_TOKEN
    data['companion_assets']=companion_assets.manifest(DB)
    return data

def launcher_view():
    data=quick_launcher.QuickLauncher(DB).overview()
    data['action_token']=DESK_TOKEN
    return data

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,data,code=200):
        out=json.dumps(data,ensure_ascii=False).encode('utf-8');self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(out)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(out)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        # A maximum cover plus maximum small icon occupies 6MiB in base64;
        # leave a bounded allowance for JSON and escaped metadata.
        path=urlparse(self.path).path
        limit=14*1024*1024+65536 if path=='/api/work/upload' else 6*1024*1024+65536 if path in {'/api/launcher/save','/api/launcher/appearance','/api/launcher/card-style'} else 200000
        if n<0 or n>limit:raise ValueError('요청 크기 초과')
        return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        path=urlparse(self.path).path
        if path in {'/api/work/model-connections','/api/work/notifications','/api/work/agent-artifact','/api/work/attachment','/api/work/companion-assets','/api/work/reload-status','/api/work/weather','/api/work/stage','/api/work/media','/api/launcher','/api/launcher/icon','/api/launcher/cover','/api/work','/api/work/task-history','/api/state','/api/desk','/api/desk/thumbnail','/api/desk/organizer','/api/desk/policy','/api/desk/judgment'} and self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            return self.reply({'error':'이 PC의 AI OFFICE 주소로 연결하세요.'},403)
        if path=='/api/work/model-connections':
            return self.reply(model_connections.snapshot(config()))
        if path=='/api/work/notifications':
            try:return self.reply(office_notifications.snapshot(DB))
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'알림 목록을 불러오지 못했어요.'},400)
        if path=='/api/work/companion-assets':
            try:return self.reply(companion_assets.manifest(DB))
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'캐릭터 자료를 읽지 못했습니다.'},400)
        if path=='/api/work/agent-artifact':
            from urllib.parse import parse_qs,quote
            query=parse_qs(urlparse(self.path).query)
            started=False
            try:
                with agent_workspace.open_artifact(query.get('id',[''])[0]) as (stream,meta):
                    download=query.get('download',['0'])[0]=='1'
                    disposition='inline' if meta['kind'] in {'image','video'} and not download else 'attachment'
                    self.send_response(200);self.send_header('Content-Type',meta['mime'])
                    self.send_header('Content-Length',str(meta['size']))
                    self.send_header('Content-Disposition',disposition+"; filename*=UTF-8''"+quote(meta['name']))
                    self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','private, no-store')
                    self.send_header('Content-Security-Policy',"sandbox; default-src 'none'");self.end_headers();started=True
                    while True:
                        chunk=stream.read(65536)
                        if not chunk:break
                        self.wfile.write(chunk)
                return
            except (BrokenPipeError,ConnectionResetError):return
            except (ValueError,OSError):
                if started:self.close_connection=True;return
                return self.reply({'error':'결과 파일을 찾을 수 없거나 파일이 변경되었습니다.'},404)
        if path=='/api/work/attachment':
            from urllib.parse import parse_qs
            query=parse_qs(urlparse(self.path).query)
            try:return work_attachments.serve(self,DB,query.get('id',[''])[0],download=query.get('download',['0'])[0]=='1')
            except (BrokenPipeError,ConnectionResetError):return
            except (ValueError,OSError,sqlite3.Error):
                if getattr(self,'_attachment_started',False):self.close_connection=True;return
                return self.reply({'error':'첨부 자료를 찾을 수 없거나 자료가 변경되었습니다.'},404)
        if path=='/api/work/reload-status':return self.reply(office_reload.snapshot())
        if path in _AEMEATH_ASSETS:
            b=(ROOT/path.lstrip('/')).read_bytes()
            self.send_response(200);self.send_header('Content-Type','image/gif' if path.endswith('.gif') else 'image/png');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(b);return
        if path in {'/model-connections.js','/model-connections.css','/work-notifications.js','/work-notifications.css','/work-observer.js','/workambient.css','/workambient.js','/workstage.css','/workstage.js','/workdesk.css','/workdesk.js','/desk.css','/desk.js','/desk-collections.js','/desk-organizer.js','/desk-policy.js','/desk-judgment.js','/retro.css','/assets/fonts/PretendardVariable.woff2','/assets/fonts/BarlowCondensed-Bold.ttf','/assets/fonts/BarlowCondensed-ExtraBoldItalic.ttf'}:
            b=(ROOT/path.lstrip('/')).read_bytes()
            mime='font/woff2' if path.endswith('.woff2') else 'font/ttf' if path.endswith('.ttf') else 'text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-cache');self.end_headers();self.wfile.write(b);return
        if path=='/first-release.html':
            b=(ROOT/'first-release.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if path=='/api/work/weather':
            return self.reply(office_weather.snapshot())
        if path=='/api/work/stage':
            from urllib.parse import parse_qs
            try:return self.reply(work_stage.snapshot(DB,parse_qs(urlparse(self.path).query).get('request_id',[None])[0]))
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'작업 진행 기록을 읽지 못했습니다.'},400)
        if path=='/api/work/media':
            from urllib.parse import parse_qs
            try:
                files=file_workspace.FileWorkspace(db_path=DB,workspace=config().get('workspace'))
                return work_stage.serve_media(self,DB,files,parse_qs(urlparse(self.path).query).get('id',[''])[0])
            except (BrokenPipeError,ConnectionResetError):return
            except (ValueError,OSError,sqlite3.Error):
                if getattr(self,'_stage_media_started',False):self.close_connection=True;return
                return self.reply({'error':'미리보기가 만료되었거나 자료가 변경되었습니다.'},404)
        if path=='/api/work':
            try:return self.reply(work_view())
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'업무 기록을 읽지 못했습니다. 기존 자료를 보존했습니다.'},400)
        if path=='/api/work/task-history':
            from urllib.parse import parse_qs
            try:return self.reply(work_service().task_history(parse_qs(urlparse(self.path).query).get('id',[''])[0]))
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'선택한 업무의 기록을 읽지 못했습니다.'},400)
        if path=='/api/launcher':
            try:return self.reply(launcher_view())
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'빠른 실행 목록을 읽지 못했습니다.'},400)
        if path in {'/api/launcher/icon','/api/launcher/cover'}:
            from urllib.parse import parse_qs
            try:
                item_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
                service=quick_launcher.QuickLauncher(DB)
                data,mime=service.cover_bytes(item_id) if path.endswith('/cover') else service.icon_bytes(item_id)
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data);return
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'등록한 아이콘을 찾을 수 없습니다.'},404)
        if path=='/api/desk/judgment':
            try:return self.reply(judgment_view())
            except (ValueError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if path=='/api/desk/policy':
            try:
                organizer_policy.ensure_worker()
                return self.reply(capability_service().policy_snapshot())
            except (ValueError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if path=='/api/desk/organizer':
            try:return self.reply({'job':organizer_lab.latest()})
            except (ValueError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if path=='/api/desk':
            try:
                data=desktop_workspace.inventory();data.update(desktop_collections.overview(data));data['pinned_ids']=desktop_preferences.load()['pinned_ids'];data['action_token']=DESK_TOKEN
                return self.reply(data)
            except (ValueError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if path=='/api/desk/thumbnail':
            from urllib.parse import parse_qs
            try:
                item_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
                b=desktop_workspace.thumbnail(item_id)
                self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(b);return
            except (ValueError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if path=='/':
            organizer_policy.ensure_worker()
            start_ai_supervisor()
            page=(ROOT/'index.html').read_text(encoding='utf-8').replace('<!-- WORK_DESK -->',(ROOT/'workdesk.html').read_text(encoding='utf-8'))
            page=page.replace('</head>',"<meta name=\"office-build\" content='"+json.dumps(office_feedback.version())+"'></head>",1)
            b=page.encode('utf-8');self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if path=='/api/desktop-path':
            home=pathlib.Path.home()
            options=[home/'OneDrive - BNN'/'Desktop', home/'OneDrive'/'Desktop', home/'Desktop']
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                    val,_=winreg.QueryValueEx(key,'Desktop')
                    options.insert(0,pathlib.Path(os.path.expandvars(val)))
            except (OSError, ImportError):pass
            found=next((str(p) for p in options if p.is_dir()),'')
            return self.reply({'path':found})
        if path=='/api/pick-folder':
            if os.name != 'nt':return self.reply({'error':'폴더 선택창은 Windows 로컬 환경에서 지원합니다.'},400)
            script=("import tkinter as tk; from tkinter import filedialog; "
                    "root=tk.Tk(); root.withdraw(); root.attributes('-topmost',True); "
                    "path=filedialog.askdirectory(parent=root,title='AI OFFICE · 조사할 폴더 선택',mustexist=True); "
                    "print(path); root.destroy()")
            try:
                result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise RuntimeError(result.stderr[-500:] or '선택창 실행 실패')
                return self.reply({'path':result.stdout.strip(),'cancelled':not bool(result.stdout.strip())})
            except Exception as ex:return self.reply({'error':'Windows 폴더 선택창 실행 실패: '+str(ex)},500)
        if path=='/api/update-status':
            status=updater.check()
            f=ROOT/'update_status.json'
            if f.exists():
                try:status['last_run']=json.loads(f.read_text(encoding='utf-8'))
                except (OSError,ValueError):pass
            return self.reply(status)
        if path=='/api/scan-status':return self.reply(scanner.status())
        if path=='/api/workspace-insights':
            if scanner.status()['status']!='done':return self.reply({'error':'먼저 PC 자료 조사를 완료하세요.'},409)
            with scanner.LOCK: data=list(scanner.FILES)
            return self.reply(workspace_insights.review(data))
        if path=='/api/scan-files':
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            return self.reply(scanner.sample(q.get('page',['0'])[0],q.get('category',[''])[0],q.get('term',[''])[0]))
        if path=='/api/analyze-file':
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            try:return self.reply(content_analysis.analyze_file(q.get('path',[''])[0]))
            except ValueError as exc:return self.reply({'error':str(exc)},400)
            except OSError:return self.reply({'error':'파일을 읽을 수 없습니다.'},400)
        if path=='/api/state':
            with db() as c:tasks=[dict(r) for r in c.execute('SELECT * FROM tasks ORDER BY created DESC')];runs=[dict(r) for r in c.execute('SELECT * FROM runs ORDER BY created DESC LIMIT 30')]
            s=model_connections.public_settings(config());return self.reply({'tasks':tasks,'runs':runs,'settings':s,'action_token':DESK_TOKEN})
        self.reply({'error':'not found'},404)
    def do_POST(self):
        with office_reload.action(readonly=urlparse(self.path).path in {'/api/work/stream','/api/work/feedback'}) as admitted:
            if not admitted:return self.reply({'error':'수정한 기능을 반영하고 있어요. 잠시 뒤 다시 요청해 주세요.'},503)
            self._do_POST()
    def _do_POST(self):
        try:
            p=urlparse(self.path).path;d=self.body()
            if p.startswith('/api/files/'):
                allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if not isinstance(d,dict) or d.get('token')!=DESK_TOKEN or (self.headers.get('Origin') and self.headers['Origin'] not in allowed) or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                    return self.reply({'error':'오피스를 새로고침한 뒤 다시 시도하세요.'},403)
                service=capability_service()
                if p=='/api/files/search':
                    if set(d)-{'token','query','kind','limit'}:raise ValueError('파일 검색 요청을 확인하세요.')
                    return self.reply(service.search(d.get('query',''),d.get('kind','all'),d.get('limit',50)))
                if p=='/api/files/preview':
                    if set(d)-{'token','id','selection_token'}:raise ValueError('검색한 파일만 선택할 수 있습니다.')
                    return self.reply(service.preview(d.get('id'),d.get('selection_token')))
                if p=='/api/files/reveal':
                    if set(d)-{'token','id','selection_token','request_id'}:raise ValueError('검색한 파일만 선택할 수 있습니다.')
                    return self.reply(service.reveal(d.get('id'),d.get('selection_token'),d.get('request_id')))
                return self.reply({'error':'not found'},404)
            if p.startswith('/api/launcher/'):
                allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if not isinstance(d,dict) or d.get('token')!=DESK_TOKEN or (self.headers.get('Origin') and self.headers['Origin'] not in allowed) or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                    return self.reply({'error':'오피스를 새로고침한 뒤 다시 시도하세요.'},403)
                if p=='/api/launcher/bookmarks':
                    if set(d)-{'token'}:raise ValueError('북마크 선택 요청을 확인하세요.')
                    return self.reply(browser_bookmarks.catalog())
                if p=='/api/launcher/refresh':
                    if set(d)-{'token'}:raise ValueError('목록 새로고침 요청을 확인하세요.')
                    quick_launcher.windows_app_catalog.catalog(refresh=True)
                    return self.reply(launcher_view())
                if p=='/api/launcher/pick-program':
                    if set(d)-{'token','request_id'}:raise ValueError('프로그램 선택 요청을 확인하세요.')
                    if os.name!='nt':raise ValueError('프로그램 선택은 Windows에서 지원합니다.')
                    script=("import tkinter as tk;from tkinter import filedialog;root=tk.Tk();root.withdraw();root.attributes('-topmost',True);"
                            "path=filedialog.askopenfilename(parent=root,title='AI OFFICE · 실행할 프로그램 선택',filetypes=[('프로그램·바로가기','*.exe *.lnk')]);print(path);root.destroy()")
                    try:
                        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,encoding='utf-8',timeout=180,env=dict(os.environ,PYTHONIOENCODING='utf-8'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        if result.returncode:raise ValueError('프로그램 선택창을 열지 못했습니다.')
                        selected=result.stdout.strip()
                        return self.reply({'path':selected,'cancelled':not bool(selected)})
                    except (OSError,subprocess.TimeoutExpired):raise ValueError('프로그램 선택창을 열지 못했습니다. 경로를 직접 입력할 수 있습니다.') from None
                payload={k:v for k,v in d.items() if k!='token'}
                result=launcher_service().perform(p.rsplit('/',1)[-1],payload)
                result['action_token']=DESK_TOKEN
                return self.reply(result)
            if p.startswith('/api/work/') or p in {'/api/task','/api/task/status','/api/settings'}:
                allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if not isinstance(d,dict) or d.get('token')!=DESK_TOKEN or (self.headers.get('Origin') and self.headers['Origin'] not in allowed) or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                    return self.reply({'error':'오피스를 새로고침한 뒤 다시 시도하세요.'},403)
                payload={k:v for k,v in d.items() if k!='token'}
                if p=='/api/work/model-connections-refresh':
                    if payload:raise ValueError('연결 확인 요청을 확인해줘.')
                    return self.reply(model_connections.inspect_readiness(config()))
                if p=='/api/work/model-select':
                    if set(payload)!={'provider'}:raise ValueError('사용할 연결을 선택해줘.')
                    with model_connections.LOCK:
                        settings=model_connections.save_settings(CFG,DB,config(),provider=payload['provider'])
                    return self.reply(model_connections.snapshot(settings))
                if p=='/api/settings':
                    fields={'gemma_url','gemma_model','vision_model','api_type','workspace','timeout','lmstudio_auto_start','api_key'}
                    if set(payload)-fields:raise ValueError('연결 설정 요청을 확인해줘.')
                    if payload.get('api_key')=='(설정됨)':payload.pop('api_key')
                    with model_connections.LOCK:
                        model_connections.save_settings(CFG,DB,config(),payload)
                    return self.reply({'ok':True})
                if p=='/api/work/notifications-confirm':
                    if set(payload) not in ({'id'},{'through'}):raise ValueError('확인할 알림을 다시 선택해 주세요.')
                    return self.reply(office_notifications.confirm(DB,payload.get('id'),payload.get('through')))
                if p=='/api/work/notifications-prepare':
                    if set(payload)!={'id'}:raise ValueError('알림을 다시 선택해 주세요.')
                    return self.reply(office_notifications.prepare_reply(DB,payload.get('id')))
                if p=='/api/work/notifications-reconnect':
                    if payload:raise ValueError('연결 요청을 확인해 주세요.')
                    return self.reply(office_notifications.start(DB).recover())
                if p=='/api/work/notifications-reply':
                    if set(payload)!={'id','text','request_id'}:raise ValueError('답장 요청 내용을 확인해 주세요.')
                    return self.reply(office_notifications.reply(DB,payload.get('id'),payload.get('text'),payload.get('request_id')))
                if p=='/api/work/feedback':
                    if set(payload)-{'client','report'}:raise ValueError('화면 확인 요청을 확인하세요.')
                    feedback=office_feedback.update_client(DB,payload.get('client'),payload.get('report'))
                    self.reply(feedback)
                    accepted=feedback.get('accepted') or {}
                    if accepted.get('followup'):
                        threading.Thread(target=resume_screen_verification,args=(self.server,accepted['actual_request_id']),daemon=True).start()
                    return
                if p=='/api/work/stage-review':
                    if set(payload)!={'request_id','decision'}:raise ValueError('작업 확인 상태를 선택하세요.')
                    work_stage.set_review(DB,payload.get('request_id'),payload.get('decision'))
                    return self.reply(work_stage.snapshot(DB,payload.get('request_id')))
                if p=='/api/work/aemeath-guidelines':
                    import aemeath_guidelines
                    action=payload.get('action')
                    if action=='list' and set(payload)=={'action'}:return self.reply(aemeath_guidelines.list_cards(DB))
                    if action=='save' and set(payload)=={'action','category','title','content','card_id'}:
                        return self.reply({'card':aemeath_guidelines.save_card(DB,payload.get('category'),payload.get('title'),payload.get('content'),payload.get('card_id'))})
                    raise ValueError('행동지침 요청을 확인해줘.')
                if p=='/api/work/companion-remove':
                    if set(payload)!={'state','id'}:raise ValueError('캐릭터 제거 요청을 확인하세요.')
                    return self.reply({'companion_assets':companion_assets.remove(DB,payload['state'],payload['id'])})
                if p=='/api/work/upload':
                    if set(payload)-{'request_id','upload_id','name','data_url'}:raise ValueError('첨부 요청을 확인하세요.')
                    result=work_attachments.upload(DB,payload.get('name'),payload.get('data_url'),payload.get('upload_id'),request_id=payload.get('request_id'))
                    return self.reply({'attachment':result})
                if p=='/api/work/upload-remove':
                    if set(payload)-{'request_id','attachment_id'}:raise ValueError('첨부 취소 요청을 확인하세요.')
                    return self.reply(work_attachments.discard(DB,payload.get('attachment_id'),payload.get('request_id')))
                if p=='/api/work/stream':
                    if set(payload)-{'request_id'}:raise ValueError('대화 진행 요청을 확인하세요.')
                    return self.reply(chat_stream.snapshot(payload.get('request_id')))
                if p=='/api/work/preview-resource':
                    if set(payload)-{'resource_id'}:raise ValueError('등록한 업무 자료만 미리 볼 수 있습니다.')
                    files=file_workspace.FileWorkspace(db_path=DB,workspace=config().get('workspace'))
                    return self.reply(work_stage.resource_preview(DB,files,payload.get('resource_id')))
                service=work_service()
                if p=='/api/work/open-resource':
                    if set(payload)-{'resource_id','request_id'}:raise ValueError('등록한 업무 자료만 열 수 있습니다.')
                    resource=service.resource_path(payload.get('resource_id'))
                    try:os.startfile(str(resource))
                    except OSError:raise ValueError('자료를 열지 못했습니다. 파일 위치와 기본 앱을 확인하세요.') from None
                    result=service.overview();result['result']={'action':'open-resource','message':'등록한 자료 열기를 요청했습니다.'}
                else:
                    action='task' if p=='/api/task' else 'status' if p=='/api/task/status' else p.rsplit('/',1)[-1]
                    if action=='verify-followup':raise ValueError('화면 확인은 원래 요청에서 내부적으로 이어집니다.')
                    if p=='/api/task/status' and 'id' in payload:payload['task_id']=payload.pop('id')
                    if action in {'command','assistant-action'} and config().get('api_type')=='codex':
                        with chat_stream.capture(payload.get('request_id')):
                            result=service.perform(action,payload)
                    else:result=service.perform(action,payload)
                result['action_token']=DESK_TOKEN
                if p in {'/api/task','/api/task/status'}:result['ok']=True
                changes=result.get('result',{}).get('source_changes',[])
                ticket=office_reload.prepare(changes,payload.get('request_id')) if changes else None
                if ticket:result['result']['source_reload']=ticket
                try:return self.reply(result)
                finally:office_reload.schedule(self.server,ticket)
            if p in DESK_ACTIONS:
                origin=self.headers.get('Origin')
                allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if d.get('token')!=DESK_TOKEN or (origin and origin not in allowed) or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                    return self.reply({'error':'작업 데스크를 새로고침한 뒤 다시 선택하세요.'},403)
                item_id=d.get('id')
                if p.startswith('/api/desk/judgment-'):
                    configure_ai()
                    action=p.rsplit('-',1)[-1]
                    allowed_keys={'token','force'} if action=='start' else {'token','item_ids'} if action=='retain' else {'token','revision','candidate_ids','request_id'} if action=='hold' else {'token','batch_id'} if action=='restore' else {'token'}
                    if set(d)-allowed_keys:raise ValueError('표시된 바탕화면 분석 결과만 처리할 수 있습니다.')
                    if action=='start':
                        force=d.get('force',False)
                        if type(force) is not bool:raise ValueError('다시 분석 설정을 확인하세요.')
                        desktop_judgment.start(force=force);start_ai_supervisor()
                    elif action=='stop':desktop_judgment.stop()
                    elif action=='retain':desktop_judgment.retain(d.get('item_ids'))
                    elif action=='hold':desktop_recovery.hold(d.get('revision'),d.get('candidate_ids'),d.get('request_id'))
                    else:desktop_recovery.restore(d.get('batch_id'))
                    return self.reply(judgment_view())
                if p.startswith('/api/desk/policy-'):
                    action=p.rsplit('-',1)[-1]
                    allowed_keys={'token','source_dir','destination_dir','exclusions','auto_enabled','authorized'} if action=='save' else {'token','enabled','request_id','expected_scope'} if action=='enabled' else {'token','id','request_id','expected_job'} if action in ('undo','resume') else {'token','request_id','expected_scope'}
                    if set(d)-allowed_keys:raise ValueError('정리 범위 설정과 기록 번호만 사용할 수 있습니다.')
                    service=capability_service()
                    if action=='save':
                        with organizer_policy.LOCK:
                            organizer_policy.save({k:v for k,v in d.items() if k!='token'})
                            result=service.policy_snapshot()
                    else:
                        proof='expected_job' if action in ('undo','resume') else 'expected_scope'
                        if d.get('request_id') is not None and not isinstance(d.get(proof),dict):
                            raise ValueError('정리 상태를 새로고침한 뒤 표시된 범위를 선택하세요.')
                        payload={'enabled':d.get('enabled')} if action=='enabled' else {'id':item_id} if action in ('undo','resume') else {}
                        result=service.policy(action,payload,d.get('request_id'),
                                              expected_scope=d.get('expected_scope'),expected_job=d.get('expected_job'))
                    organizer_policy.ensure_worker()
                    return self.reply(result)
                if p.endswith(('/organizer-create','/organizer-execute','/organizer-undo')):
                    if set(d)-{'id','token'}:raise ValueError('시험 정리는 앱이 만든 샘플 폴더에서만 실행할 수 있습니다.')
                    if p.endswith('/organizer-create'):job=organizer_lab.create()
                    elif p.endswith('/organizer-execute'):job=organizer_lab.execute(item_id)
                    else:job=organizer_lab.undo(item_id)
                    return self.reply({'job':job})
                if p.endswith('/create-collection'):return self.reply(desktop_collections.create(d.get('label'),d.get('item_ids')))
                if p.endswith('/rename-collection'):return self.reply(desktop_collections.rename(item_id,d.get('label')))
                if p.endswith('/remove-member'):return self.reply(desktop_collections.remove_item(d.get('collection_id'),d.get('item_id')))
                if p.endswith('/dissolve-collection'):return self.reply(desktop_collections.dissolve(item_id))
                if p.endswith('/dismiss-suggestion'):return self.reply(desktop_collections.dismiss(item_id))
                if p.endswith('/restore-dismissed'):return self.reply(desktop_collections.restore_dismissed())
                if p.endswith('/duplicates'):return self.reply(desktop_collections.duplicate_report())
                if p.endswith('/auto-organize'):return self.reply(desktop_collections.auto_organize(d.get('enabled',True)))
                if p.endswith('/undo-auto'):return self.reply(desktop_collections.undo_auto())
                if p.endswith('/open'):
                    result=desktop_workspace.open_item(item_id)
                    if _AI_READY:
                        try:desktop_judgment.record_open(item_id)
                        except (OSError,ValueError):result['usage_warning']='열기는 요청했으나 사용 기록을 저장하지 못했습니다.'
                    return self.reply(result)
                if p.endswith('/reveal'):return self.reply(desktop_workspace.reveal_item(item_id))
                enabled=d.get('enabled')
                if not isinstance(enabled,bool):raise ValueError('즐겨찾기 선택을 확인하세요.')
                if enabled and not any(x['id']==item_id for x in desktop_workspace.inventory()['items']):raise ValueError('바탕화면 목록을 새로고침하세요.')
                return self.reply(desktop_preferences.pin(item_id,enabled))
            if p=='/api/analyze-workspace-content':
                paths=d.get('paths')
                if not isinstance(paths,list) or not 1<=len(paths)<=local_content_review.MAX_FILES:raise ValueError('분석할 파일을 1~8개 선택하세요.')
                with scanner.LOCK:
                    available={f['path']:dict(f) for f in scanner.FILES if not f.get('protected')}
                if any(path not in available for path in paths):raise ValueError('현재 PC 조사 결과에 포함된 비보호 파일만 분석할 수 있습니다.')
                settings=config()
                result=local_content_review.analyze([available[path] for path in paths],settings,content_analysis.analyze_file,lambda prompt:call_gemma(prompt,settings))
                runlog('내용 기반 후보 분석','완료',f"Gemma 서버 · {len(paths)}개 파일 · {len(result['items'])}개 후보")
                return self.reply(result)
            if p=='/api/analyze-workspace-images':
                paths=d.get('paths')
                if scanner.status()['status']!='done':raise ValueError('먼저 PC 자료 조사를 완료하세요.')
                if not isinstance(paths,list) or not 1<=len(paths)<=local_image_review.MAX_FILES:raise ValueError('분석할 이미지를 1~3개 선택하세요.')
                with scanner.LOCK:
                    available={f['path']:dict(f) for f in scanner.FILES if not f.get('protected')}
                if any(path not in available for path in paths):raise ValueError('현재 PC 조사 결과에 포함된 비보호 파일만 분석할 수 있습니다.')
                settings=config()
                result=local_image_review.analyze([available[path] for path in paths],settings,lambda prompt,image:call_gemma_vision(prompt,image,settings))
                runlog('이미지 내용 후보 분석','완료',f"Gemma 서버 · {len(paths)}개 파일 · {len(result['items'])}개 후보")
                return self.reply(result)
            if p=='/api/update-check':return self.reply(updater.fetch())
            if p=='/api/update-apply':
                status=updater.fetch()
                if status.get('phase')!='available':return self.reply(status)
                organizer_policy.stop_worker()
                stop_ai_supervisor()
                subprocess.Popen([sys.executable,str(ROOT/'updater.py'),'apply'],cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)|getattr(subprocess,'CREATE_NO_WINDOW',0))
                threading.Thread(target=lambda:(__import__('time').sleep(1.2),__import__('urllib.request').request.urlopen('http://127.0.0.1:8765/api/none',timeout=2) if False else os._exit(0)),daemon=True).start()
                return self.reply({'phase':'starting','message':'업데이트를 시작합니다. 잠시 후 화면을 새로고침해 주세요.'})
            if p=='/api/project-archives':
                result=project_intelligence.analyze(str(d.get('folder','')))
                runlog('프로젝트 비교','완료',f"ZIP {result['archive_count']}개 비교 · 관련 그룹 {len(result['groups'])}개",result['folder'])
                return self.reply(result)
            if p=='/api/scan-start':
                result=scanner.start(d.get('roots',[]));runlog('PC 조사','시작','읽기 전용 파일 목록 조사',json.dumps(result,ensure_ascii=False));return self.reply(result)
            if p=='/api/scan-duplicates':
                result=scanner.duplicate_report();runlog('중복 조사','완료',str(result['total_groups'])+'개 동일 내용 그룹 확인');return self.reply(result)
            if p=='/api/ask':
                text=str(d.get('prompt','')).strip()[:12000]
                if not text:raise ValueError('업무 지시를 입력해 주세요.')
                try:
                    answer=call_gemma(text);runlog('모델','완료',text[:100],answer[:12000]);return self.reply({'answer':answer})
                except Exception as e:runlog('모델','실패',text[:100],str(e));raise
            if p in ('/api/plan-files','/api/execute-files'):
                return self.reply({'error':'파일 정리는 저장한 정리 범위에서 실행하도록 변경되었습니다. 자동 폴더 정리 화면을 이용하세요.','page':'files'},409)
            if p=='/api/test':
                result=call_gemma('연결 테스트입니다. 한국어로 연결 성공이라고 짧게 답하세요.');return self.reply({'answer':result[:500]})
            self.reply({'error':'not found'},404)
        except Exception as e:self.reply({'error':str(e)},400)
if __name__=='__main__':
    config();db().close()
    try:
        lmstudio_runtime.ensure_ready(config())
    except (RuntimeError, ValueError, OSError) as exc:
        # Keep settings and the desk reachable; model callbacks retry readiness
        # and report this failure without switching to another AI server.
        print(f'[WARNING] 로컬 AI 준비 실패: {exc}', flush=True)
    organizer_policy.ensure_worker()
    office_notifications.start(DB)
    start_ai_supervisor()
    import webbrowser, time
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"[ERROR] 서버 시작 실패: {exc}", flush=True)
        raise
    url = f"http://{HOST}:{PORT}"
    print(f"AI OFFICE 준비 완료: {url}", flush=True)
    def launch_browser():
        time.sleep(0.7)
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"[WARNING] 브라우저 자동 실행 실패: {exc}", flush=True)
        print(f"브라우저에서 직접 접속: {url}", flush=True)
    if os.environ.get('AI_OFFICE_NO_BROWSER')!='1':threading.Thread(target=launch_browser, daemon=True).start()
    server.serve_forever()
