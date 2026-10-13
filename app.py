import scanner
import updater
import project_intelligence
import workspace_insights
import content_analysis
import local_content_review
import local_image_review
import json, os, sqlite3, threading, urllib.request, urllib.error, uuid, shutil, pathlib, datetime, re, subprocess, sys
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
def call_gemma(prompt):
    s=config();base=local_content_review.validate_endpoint(s['gemma_url']); t=int(s.get('timeout',60));model=s['gemma_model']
    if s['api_type']=='openai':
        u=base+'/chat/completions';payload={'model':model,'messages':[{'role':'system','content':'당신은 한국어 업무 보조 AI입니다. 임의 실행을 약속하지 않고 확인 가능한 사실만 보고합니다.'},{'role':'user','content':prompt}],'temperature':0.2};headers={'Content-Type':'application/json'}
    else:
        u=base+'/api/chat';payload={'model':model,'messages':[{'role':'system','content':'당신은 한국어 업무 보조 AI입니다. 임의 실행을 약속하지 않고 확인 가능한 사실만 보고합니다.'},{'role':'user','content':prompt}],'stream':False,'options':{'temperature':0.2}};headers={'Content-Type':'application/json'}
    if s.get('api_key'):headers['Authorization']='Bearer '+s['api_key']
    req=urllib.request.Request(u,data=json.dumps(payload).encode(),headers=headers,method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(NoRedirect).open(req,timeout=t) as r:response=json.load(r)
    if s['api_type']=='openai':return response['choices'][0]['message']['content']
    return response['message']['content']
def call_gemma_vision(prompt,image_b64):
    s=config()
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
def safe_path(path):
    p=pathlib.Path(path).expanduser().resolve()
    if not p.is_dir():raise ValueError('존재하는 폴더를 지정하세요.')
    if str(p)==str(pathlib.Path(p.anchor)) or p==pathlib.Path.home():raise ValueError('드라이브 전체나 홈 폴더는 허용하지 않습니다.')
    return p
def plan_files(folder):
    p=safe_path(folder); mapping={'.png':'이미지','.jpg':'이미지','.jpeg':'이미지','.webp':'이미지','.gif':'이미지','.psd':'디자인원본','.ai':'디자인원본','.fig':'디자인원본','.pdf':'문서','.docx':'문서','.xlsx':'문서','.pptx':'문서','.txt':'문서','.zip':'압축파일','.7z':'압축파일','.rar':'압축파일','.mp4':'영상','.mov':'영상'}
    items=[]
    for f in sorted(p.iterdir()):
        if not f.is_file() or f.name.startswith('.'):continue
        category=mapping.get(f.suffix.lower(),'기타')
        dest=p/category/f.name
        if dest.exists():
            stem,suffix=f.stem,f.suffix
            n=1
            while (p/category/f'{stem} ({n}){suffix}').exists():n+=1
            dest=p/category/f'{stem} ({n}){suffix}'
        items.append({'source':str(f),'target':str(dest),'category':category})
    return {'folder':str(p),'items':items,'count':len(items)}
PLANS={}; LOCK=threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,data,code=200):
        out=json.dumps(data,ensure_ascii=False).encode('utf-8');self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(out)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(out)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>200000:self.reply({'error':'요청 크기 초과'},413);raise ValueError('too large')
        return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/':
            b=(ROOT/'index.html').read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
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
            s=config();s['api_key']='' if not s.get('api_key') else '(설정됨)';return self.reply({'tasks':tasks,'runs':runs,'settings':s})
        self.reply({'error':'not found'},404)
    def do_POST(self):
        try:
            p=urlparse(self.path).path;d=self.body()
            if p=='/api/analyze-workspace-content':
                paths=d.get('paths')
                if not isinstance(paths,list) or not 1<=len(paths)<=local_content_review.MAX_FILES:raise ValueError('분석할 파일을 1~8개 선택하세요.')
                with scanner.LOCK:
                    available={f['path']:dict(f) for f in scanner.FILES if not f.get('protected')}
                if any(path not in available for path in paths):raise ValueError('현재 PC 조사 결과에 포함된 비보호 파일만 분석할 수 있습니다.')
                result=local_content_review.analyze([available[path] for path in paths],config(),content_analysis.analyze_file,call_gemma)
                runlog('내용 기반 후보 분석','완료',f"Gemma 서버 · {len(paths)}개 파일 · {len(result['items'])}개 후보")
                return self.reply(result)
            if p=='/api/analyze-workspace-images':
                paths=d.get('paths')
                if scanner.status()['status']!='done':raise ValueError('먼저 PC 자료 조사를 완료하세요.')
                if not isinstance(paths,list) or not 1<=len(paths)<=local_image_review.MAX_FILES:raise ValueError('분석할 이미지를 1~3개 선택하세요.')
                with scanner.LOCK:
                    available={f['path']:dict(f) for f in scanner.FILES if not f.get('protected')}
                if any(path not in available for path in paths):raise ValueError('현재 PC 조사 결과에 포함된 비보호 파일만 분석할 수 있습니다.')
                result=local_image_review.analyze([available[path] for path in paths],config(),call_gemma_vision)
                runlog('이미지 내용 후보 분석','완료',f"Gemma 서버 · {len(paths)}개 파일 · {len(result['items'])}개 후보")
                return self.reply(result)
            if p=='/api/update-check':return self.reply(updater.fetch())
            if p=='/api/update-apply':
                status=updater.fetch()
                if status.get('phase')!='available':return self.reply(status)
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
            if p=='/api/task':
                title=str(d.get('title','')).strip()[:200];detail=str(d.get('detail',''))[:5000]
                if not title:raise ValueError('업무명을 입력해 주세요.')
                with db() as c:c.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(uuid.uuid4().hex,title,detail,'todo',now()))
                return self.reply({'ok':True})
            if p=='/api/task/status':
                status=d.get('status'); assert status in ['todo','doing','done']
                with db() as c:c.execute('UPDATE tasks SET status=? WHERE id=?',(status,d.get('id')))
                return self.reply({'ok':True})
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
            if p=='/api/plan-files':
                plan=plan_files(str(d.get('folder','')));token=uuid.uuid4().hex
                with LOCK:PLANS[token]=plan
                runlog('파일정리','미리보기',f"{plan['count']}개 파일 분류 계획",plan['folder'])
                return self.reply({'token':token,**plan})
            if p=='/api/execute-files':
                token=d.get('token');
                with LOCK:plan=PLANS.pop(token,None)
                if not plan:raise ValueError('먼저 미리보기를 생성하세요. 계획은 한 번만 실행됩니다.')
                moved=[];skipped=[]
                for entry in plan['items']:
                    src=pathlib.Path(entry['source']);dst=pathlib.Path(entry['target'])
                    if not src.is_file() or dst.exists() or src.parent.resolve()!=pathlib.Path(plan['folder']).resolve():skipped.append(str(src));continue
                    dst.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(src),str(dst));moved.append({'from':str(src),'to':str(dst)})
                runlog('파일정리','완료',f'{len(moved)}개 이동, {len(skipped)}개 건너뜀',json.dumps(moved,ensure_ascii=False))
                return self.reply({'moved':moved,'skipped':skipped})
            if p=='/api/test':
                result=call_gemma('연결 테스트입니다. 한국어로 연결 성공이라고 짧게 답하세요.');return self.reply({'answer':result[:500]})
            self.reply({'error':'not found'},404)
        except Exception as e:self.reply({'error':str(e)},400)
if __name__=='__main__':
    config();db().close()
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
