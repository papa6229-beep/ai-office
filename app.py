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
import json, os, sqlite3, threading, urllib.request, urllib.error, uuid, pathlib, datetime, re, subprocess, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'office.db'
CFG=ROOT/'settings.json'
HOST='127.0.0.1'; PORT=8765
DEFAULT={'gemma_url':'http://127.0.0.1:11434','gemma_model':'gemma4:26b','api_type':'ollama','api_key':'','workspace':str(pathlib.Path.home()/'Documents'/'AI-Office'),'timeout':60}
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
    s=settings if settings is not None else config();base=local_content_review.validate_endpoint(s['gemma_url']); t=int(s.get('timeout',60));model=s['gemma_model']
    if s['api_type']=='openai':
        u=base+'/chat/completions';payload={'model':model,'messages':[{'role':'system','content':'당신은 한국어 업무 보조 AI입니다. 임의 실행을 약속하지 않고 확인 가능한 사실만 보고합니다.'},{'role':'user','content':prompt}],'temperature':0.2};headers={'Content-Type':'application/json'}
    else:
        u=base+'/api/chat';payload={'model':model,'messages':[{'role':'system','content':'당신은 한국어 업무 보조 AI입니다. 임의 실행을 약속하지 않고 확인 가능한 사실만 보고합니다.'},{'role':'user','content':prompt}],'stream':False,'options':{'temperature':0.2}};headers={'Content-Type':'application/json'}
    # Per-call judgment limits stay in the captured snapshot, never in user settings.
    if s.get('_judgment_json') is True:
        if s['api_type']=='openai':payload['response_format']={'type':'json_object'}
        else:payload['format']='json'
    output_limit=s.get('_judgment_max_tokens')
    if type(output_limit) is int and 128<=output_limit<=1536:
        if s['api_type']=='openai':payload['max_tokens']=output_limit
        else:payload['options']['num_predict']=output_limit
    if s['api_type']!='openai' and s.get('_judgment_think') is False:payload['think']=False
    if s.get('api_key'):headers['Authorization']='Bearer '+s['api_key']
    req=urllib.request.Request(u,data=json.dumps(payload).encode(),headers=headers,method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(NoRedirect).open(req,timeout=t) as r:response=json.load(r)
    if s['api_type']=='openai':return response['choices'][0]['message']['content']
    return response['message']['content']
def call_gemma_vision(prompt,image_b64,settings=None):
    s=settings if settings is not None else config()
    if s.get('api_type')!='ollama':raise ValueError('이미지 내용 분석은 설정된 Ollama Gemma 서버에서만 지원합니다.')
    base=local_content_review.validate_endpoint(s['gemma_url']);model=s['gemma_model'];t=int(s.get('timeout',60))
    payload={'model':model,'messages':[{'role':'user','content':prompt,'images':[image_b64]}],'stream':False,'options':{'temperature':0.2}}
    headers={'Content-Type':'application/json'}
    if s.get('api_key'):headers['Authorization']='Bearer '+s['api_key']
    req=urllib.request.Request(base+'/api/chat',data=json.dumps(payload).encode(),headers=headers,method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(NoRedirect).open(req,timeout=t) as r:response=json.load(r)
    return response['message']['content']
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
    with _AI_LOCK:
        if _AI_THREAD and _AI_THREAD.is_alive():return
        _AI_STOP.clear()
        def supervise():
            while not _AI_STOP.is_set():
                try:
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
    return work_assistant.WorkAssistant(DB,config,call_gemma)

def work_view():
    data=work_service().overview()
    data['action_token']=DESK_TOKEN
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
        limit=800000 if urlparse(self.path).path=='/api/launcher/save' else 200000
        if n<0 or n>limit:raise ValueError('요청 크기 초과')
        return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        path=urlparse(self.path).path
        if path in {'/api/launcher','/api/launcher/icon','/api/work','/api/work/task-history','/api/state','/api/desk','/api/desk/thumbnail','/api/desk/organizer','/api/desk/policy','/api/desk/judgment'} and self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
            return self.reply({'error':'이 PC의 AI OFFICE 주소로 연결하세요.'},403)
        if path in {'/workdesk.css','/workdesk.js','/desk.css','/desk.js','/desk-collections.js','/desk-organizer.js','/desk-policy.js','/desk-judgment.js'}:
            b=(ROOT/path.lstrip('/')).read_bytes()
            self.send_response(200);self.send_header('Content-Type','text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-cache');self.end_headers();self.wfile.write(b);return
        if path=='/first-release.html':
            b=(ROOT/'first-release.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
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
        if path=='/api/launcher/icon':
            from urllib.parse import parse_qs
            try:
                item_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
                data,mime=quick_launcher.QuickLauncher(DB).icon_bytes(item_id)
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data);return
            except (ValueError,OSError,sqlite3.Error):return self.reply({'error':'등록한 아이콘을 찾을 수 없습니다.'},404)
        if path=='/api/desk/judgment':
            try:return self.reply(judgment_view())
            except (ValueError,OSError) as exc:return self.reply({'error':str(exc)},400)
        if path=='/api/desk/policy':
            try:
                organizer_policy.ensure_worker()
                return self.reply(organizer_policy.status())
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
            s=config();s['api_key']='' if not s.get('api_key') else '(설정됨)';return self.reply({'tasks':tasks,'runs':runs,'settings':s,'action_token':DESK_TOKEN})
        self.reply({'error':'not found'},404)
    def do_POST(self):
        try:
            p=urlparse(self.path).path;d=self.body()
            if p.startswith('/api/launcher/'):
                allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if not isinstance(d,dict) or d.get('token')!=DESK_TOKEN or (self.headers.get('Origin') and self.headers['Origin'] not in allowed) or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                    return self.reply({'error':'오피스를 새로고침한 뒤 다시 시도하세요.'},403)
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
                result=quick_launcher.QuickLauncher(DB).perform(p.rsplit('/',1)[-1],payload)
                result['action_token']=DESK_TOKEN
                return self.reply(result)
            if p.startswith('/api/work/') or p in {'/api/task','/api/task/status'}:
                allowed={f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}
                if not isinstance(d,dict) or d.get('token')!=DESK_TOKEN or (self.headers.get('Origin') and self.headers['Origin'] not in allowed) or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}:
                    return self.reply({'error':'오피스를 새로고침한 뒤 다시 시도하세요.'},403)
                payload={k:v for k,v in d.items() if k!='token'}
                service=work_service()
                if p=='/api/work/open-resource':
                    if set(payload)-{'resource_id','request_id'}:raise ValueError('등록한 업무 자료만 열 수 있습니다.')
                    resource=service.resource_path(payload.get('resource_id'))
                    try:os.startfile(str(resource))
                    except OSError:raise ValueError('자료를 열지 못했습니다. 파일 위치와 기본 앱을 확인하세요.') from None
                    result=service.overview();result['result']={'action':'open-resource','message':'등록한 자료 열기를 요청했습니다.'}
                else:
                    action='task' if p=='/api/task' else 'status' if p=='/api/task/status' else p.rsplit('/',1)[-1]
                    if p=='/api/task/status' and 'id' in payload:payload['task_id']=payload.pop('id')
                    result=service.perform(action,payload)
                result['action_token']=DESK_TOKEN
                if p in {'/api/task','/api/task/status'}:result['ok']=True
                return self.reply(result)
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
                    allowed_keys={'token','source_dir','destination_dir','exclusions','auto_enabled','authorized'} if action=='save' else {'token','enabled'} if action=='enabled' else {'token','id'} if action in ('undo','resume') else {'token'}
                    if set(d)-allowed_keys:raise ValueError('정리 범위 설정과 기록 번호만 사용할 수 있습니다.')
                    if action=='save':result=organizer_policy.save({k:v for k,v in d.items() if k!='token'})
                    elif action=='preview':result=organizer_policy.preview()
                    elif action=='run':result=organizer_policy.run()
                    elif action=='enabled':result=organizer_policy.set_enabled(d.get('enabled'))
                    elif action=='undo':result=organizer_policy.undo(item_id)
                    else:result=organizer_policy.resume(item_id)
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
            if p=='/api/settings':
                new={k:d[k] for k in ('gemma_url','gemma_model','api_type','workspace','timeout') if k in d}
                if 'api_key' in d and d['api_key']!='(설정됨)':new['api_key']=d['api_key']
                s=config();s.update(new)
                if s['api_type'] not in ['ollama','openai']:raise ValueError('지원하지 않는 API 형식')
                local_content_review.validate_endpoint(s['gemma_url'])
                CFG.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8');return self.reply({'ok':True})
            if p=='/api/ask':
                text=str(d.get('prompt','')).strip()[:12000]
                if not text:raise ValueError('업무 지시를 입력해 주세요.')
                try:
                    answer=call_gemma(text);runlog('Gemma','완료',text[:100],answer[:12000]);return self.reply({'answer':answer})
                except Exception as e:runlog('Gemma','실패',text[:100],str(e));raise
            if p in ('/api/plan-files','/api/execute-files'):
                return self.reply({'error':'파일 정리는 저장한 정리 범위에서 실행하도록 변경되었습니다. 자동 폴더 정리 화면을 이용하세요.','page':'files'},409)
            if p=='/api/test':
                result=call_gemma('연결 테스트입니다. 한국어로 연결 성공이라고 짧게 답하세요.');return self.reply({'answer':result[:500]})
            self.reply({'error':'not found'},404)
        except Exception as e:self.reply({'error':str(e)},400)
if __name__=='__main__':
    config();db().close()
    organizer_policy.ensure_worker()
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
    threading.Thread(target=launch_browser, daemon=True).start()
    server.serve_forever()
