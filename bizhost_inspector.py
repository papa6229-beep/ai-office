"""Local, user-started Bizhost inspection dashboard. No model or messenger calls."""
from contextlib import closing
import copy
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import office_security
import report_automation as engine
import task_control

ROOT=Path(__file__).resolve().parent
DB=ROOT/'office.db'
ASSETS=ROOT/'bizhost-inspector'
PRIVATE=ROOT/'local_backups'/'bizhost-inspector'
PORT=8876
SERVICE='aemeath-bizhost-inspector-v1'


def browser_endpoint():
    """Read only the existing dedicated Chrome discovery file, never user Chrome."""
    path=ROOT/'browser_runtime'/'chrome-profile'/'DevToolsActivePort'
    if path.is_symlink():raise ValueError('작업용 Chrome 연결 파일을 확인해 주세요.')
    port=int(path.read_text(encoding='utf-8').splitlines()[0])
    if not 1<=port<=65535:raise ValueError('작업용 Chrome 연결 포트를 확인해 주세요.')
    endpoint='http://127.0.0.1:'+str(port)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint+'/json/version',timeout=3) as response:
        data=json.loads(response.read(16000))
    from urllib.parse import urlsplit
    ws=urlsplit(data.get('webSocketDebuggerUrl',''))
    if ws.hostname not in {'localhost','127.0.0.1'} or ws.port!=port:raise ValueError('작업용 Chrome 연결을 확인해 주세요.')
    return endpoint


def readiness():
    import credential_vault,product_image_review
    checks=[]
    try:browser_endpoint();checks.append({'name':'작업용 Chrome','ready':True,'detail':'기존 로그인 세션 재사용'})
    except Exception:checks.append({'name':'작업용 Chrome','ready':False,'detail':'시작 시 기존 전용 프로필로 연결'})
    try:
        ready=len([r for r in credential_vault.listing(engine.BIZ) if r['origin']==engine.BIZ])==1
        checks.append({'name':'비즈호스트 계정','ready':ready,'detail':'Windows 보관 계정' if ready else '오피스에서 계정 등록 필요'})
    except Exception:checks.append({'name':'비즈호스트 계정','ready':False,'detail':'현재 Windows 사용자 계정 확인 필요'})
    try:product_image_review.available();checks.append({'name':'이미지 검사','ready':True,'detail':'기존 로컬 검사기'})
    except Exception:checks.append({'name':'이미지 검사','ready':False,'detail':'로컬 검사기 의존성 확인 필요'})
    phone=False
    try:
        with closing(sqlite3.connect('file:'+DB.as_posix()+'?mode=ro',uri=True)) as c:
            phone=bool(c.execute("select 1 from sqlite_master where name='office_notifications'").fetchone())
    except sqlite3.Error:pass
    checks.append({'name':'문자 인증','ready':phone,'detail':'오피스 알림 연동 · 직접 입력도 가능' if phone else '인증번호 직접 입력'})
    return checks


class Inspector:
    def __init__(self):
        self.lock=threading.RLock();self.control=None;self.worker=None;self.owned_browser=None
        self.cancel=threading.Event();self.sms=None;self.last_start=None
        self.data={'state':'idle','phase':'검수를 시작할 준비가 됐어요.','current':None,'groups':{},
                   'completed':[],'issues':[],'events':[],'elapsed_seconds':0,'run_id':None,'sms_waiting':False}
        receipts=sorted((PRIVATE/'runs').glob('*/result.json'),reverse=True)
        if receipts:
            try:
                result=json.loads(receipts[0].read_text(encoding='utf-8'))
                self._result(result)
                if result['state']=='running':
                    self.data.update(state='interrupted',phase='이전 실행이 종료됐어요. 저장된 결과를 확인한 뒤 다시 시작해 주세요.')
            except (OSError,ValueError,KeyError):pass

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.data)

    @staticmethod
    def row(row):
        return {k:row[k] for k in ('id','name','group','md','reason','price','wholesale','stock') if k in row}

    def _result(self,result):
        self.data.update(state=result['state'],groups=copy.deepcopy(result['groups']),
                         completed=[self.row(r) for r in result['completed']],issues=[self.row(r) for r in result['issues']],
                         elapsed_seconds=result.get('elapsed_seconds',round(time.time()-result['started'])),
                         error=result.get('error'),run_id=Path(result['run_folder']).name)

    def emit(self,text):
        with self.lock:
            self.data.update(phase=text,sms_waiting='인증번호 수신 대기' in text)
            self.data['events'].append({'time':time.strftime('%H:%M:%S'),'text':text})
            self.data['events']=self.data['events'][-80:]

    def observe(self,event):
        with self.lock:
            kind=event['type']
            if kind=='snapshot':self._result(event['result'])
            elif kind=='product':self.data.update(current={k:event[k] for k in ('group','id','name')},sms_waiting=False,image_review=None,fields=None)
            elif kind=='image_review':self.data['image_review']={'state':event['state'],'reasons':event['reasons'],'gap':event.get('gap')}
            elif kind=='fields':self.data['fields']=event['data']
            elif kind=='group':self.data['current_group']=event['group']
            elif kind=='product_result':
                self.data['events'].append({'time':time.strftime('%H:%M:%S'),
                    'text':('보류 · ' if event['state']=='issue' else '저장 확인 · ')+event['name']})
                self.data['events']=self.data['events'][-80:]

    def take_sms(self):
        with self.lock:
            code=self.sms;self.sms=None
        return ('manual-'+uuid.uuid4().hex,code) if code else None

    def start(self,ident):
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('실행 번호를 확인해 주세요.')
        with self.lock:
            if ident==self.last_start:return
            if self.worker and self.worker.is_alive():raise ValueError('이미 검수 중이에요. 진행 화면을 확인해 주세요.')
            self.last_start=ident;self.cancel.clear();self.sms=None
            self.data.update(state='starting',phase='기존 작업용 Chrome에 연결하고 있어요.',current=None,
                             groups={},completed=[],issues=[],events=[],error=None,run_id=None,sms_waiting=False)
            self.worker=threading.Thread(target=self.run,name='bizhost-inspection',daemon=False);self.worker.start()

    def run(self):
        try:
            with task_control.capture(DB,uuid.uuid4().hex) as control:
                with self.lock:self.control=control
                if self.cancel.is_set():control.stopped.set()
                task_control.checkpoint()
                try:endpoint=browser_endpoint()
                except Exception:
                    if self.owned_browser is None:
                        from browser_workspace import BrowserWorkspace
                        self.owned_browser=BrowserWorkspace()
                    endpoint=self.owned_browser.connection()['endpoint']
                result=engine.run_inspection(DB,endpoint,emit=self.emit,observe=self.observe,sms_reader=self.take_sms)
                with self.lock:
                    self._result(result);self.data['sms_waiting']=False
                    self.data['phase']={'done':'A·B·C군 검수를 마쳤어요.','cancelled':'검수를 중지했어요. 확인된 결과는 보존했어요.',
                                        'error':'검수가 중단됐어요. 확인된 결과와 원인을 살펴봐 주세요.'}[result['state']]
                try:
                    import work_log
                    work_log.safe_event(DB,'inspection:'+Path(result['run_folder']).name,'activity','비즈호스트 상품 검수',time.time(),
                        data={'status':result['state'],'source':'bizhost_inspector','completed':len(result['completed']),'issues':len(result['issues'])})
                except Exception:pass
        except Exception as error:
            with self.lock:self.data.update(state='cancelled' if self.cancel.is_set() else 'error',phase='검수를 시작하지 못했어요.',
                                            error=str(error).split('\n')[0][:250],sms_waiting=False)
        finally:
            with self.lock:self.control=None;self.sms=None

    def stop(self):
        with self.lock:
            if not self.worker or not self.worker.is_alive():return
            self.cancel.set()
            if self.control:self.control.stopped.set()
            self.data.update(phase='중지 요청을 받았어요. 현재 사이트 응답과 저장 여부를 확인하고 멈춥니다.')


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self):
        self.token=secrets.token_hex(32);self.inspector=Inspector();self.checks=readiness()
        super().__init__(('127.0.0.1',PORT),Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,value,status=200,mime='application/json; charset=utf-8',download=None):
        body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if download:self.send_header('Content-Disposition','attachment; filename="'+download+'"')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if not office_security.local_request(self):return self.send({'error':'로컬 화면에서 열어 주세요.'},403)
        if self.path=='/health':return self.send({'service':SERVICE,'pid':os.getpid()})
        if self.path=='/api/state':return self.send(dict(self.server.inspector.snapshot(),checks=self.server.checks,token=self.server.token))
        if self.path=='/api/result':
            value=self.server.inspector.snapshot();value.pop('events',None)
            return self.send(json.dumps(value,ensure_ascii=False,indent=2).encode(),download='bizhost-inspection-result.json')
        assets={'/':('index.html','text/html; charset=utf-8'),'/inspector.css':('inspector.css','text/css; charset=utf-8'),
                '/inspector.js':('inspector.js','text/javascript; charset=utf-8')}
        if self.path not in assets:return self.send({'error':'찾을 수 없는 화면이에요.'},404)
        name,mime=assets[self.path];return self.send((ASSETS/name).read_bytes(),mime=mime)
    def do_POST(self):
        if not office_security.local_request(self):return self.send({'error':'로컬 화면에서 실행해 주세요.'},403)
        try:
            self.connection.settimeout(10)
            size=office_security.json_length(self.headers,4096);value=json.loads(self.rfile.read(size))
            if not isinstance(value,dict) or not office_security.action_authorized(value,self.server.token):return self.send({'error':'화면을 새로 열어 주세요.'},403)
            worker=self.server.inspector
            if self.path=='/api/start':
                if value.get('actual') is not True:raise ValueError('실제 반영 모드를 확인해 주세요.')
                worker.start(value.get('request_id'))
            elif self.path=='/api/stop':worker.stop()
            elif self.path=='/api/sms':
                code=value.get('code','')
                if not isinstance(code,str) or not re.fullmatch(r'[0-9]{4,8}',code):raise ValueError('인증번호 4~8자리를 입력해 주세요.')
                with worker.lock:
                    if not worker.data['sms_waiting']:raise ValueError('지금은 문자 인증 대기 상태가 아니에요.')
                    worker.sms=code
            else:return self.send({'error':'지원하지 않는 요청이에요.'},404)
            self.send({'ok':True})
        except (OSError,ValueError) as error:self.send({'error':str(error)[:250]},400)


if __name__=='__main__':
    PRIVATE.mkdir(parents=True,exist_ok=True)
    try:Server().serve_forever(poll_interval=.5)
    except OSError as error:
        (PRIVATE/'startup-error.txt').write_text(str(error),encoding='utf-8')
