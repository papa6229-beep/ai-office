"""Batched official Ego API execution. No browser/profile/Chrome fallback.

One CLI invocation performs one declared batch. Effects are never resent after
timeout or an uncertain response. Vault/OTP values travel only through a
short-lived authenticated loopback broker into the observed matching form.
"""
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

import chat_stream
import credential_vault
import office_fastlane
import task_control

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'local_backups'/'ego-batches'
OPS={'open','new_page','inspect','fill','select','click','press','wait_url','wait_selector','wait_any','read','expect','login','otp_fill','mark'}


def executable():
    found=shutil.which('ego-browser')
    if found:return str(Path(found).resolve())
    root=Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Citro Labs'/'ego lite'/'Application'
    versions=[]
    if root.is_dir():
        for folder in root.iterdir():
            if re.fullmatch(r'\d+(?:\.\d+){1,3}',folder.name) and (folder/'ego-browser.exe').is_file():
                versions.append((tuple(map(int,folder.name.split('.'))),folder/'ego-browser.exe'))
    if not versions:raise ValueError('설치된 에고 공식 실행기를 찾지 못했어요. 다른 브라우저로 전환하지 않았어요.')
    return str(max(versions,key=lambda row:row[0])[1])


def availability():
    try:return {'available':True,'executable':executable(),'browser':'ego','connected_verified':False}
    except ValueError:return {'available':False,'browser':'ego','connected_verified':False}


def _connect(db):
    c=sqlite3.connect(str(db),timeout=20);c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS ego_batch_runs
        (id TEXT PRIMARY KEY,signature TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ego_batch_spaces
        (id INTEGER PRIMARY KEY,owner TEXT NOT NULL,state TEXT NOT NULL,updated REAL NOT NULL);''')
    return c


def status(db,request_id):
    with closing(_connect(db)) as c:
        row=c.execute('SELECT data FROM ego_batch_runs WHERE id=?',(request_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _validate(steps):
    if not isinstance(steps,list) or not 1<=len(steps)<=40:raise ValueError('에고 동작을 1~40개로 묶어 주세요.')
    allowed={'op','page','url','selector','value','label','timeout_ms','state','equals','contains','account_id','username_selector','password_selector','source','match','since','after_mark','digits','name','query','limit','when_selector','unless_selector','selectors'}
    for row in steps:
        if not isinstance(row,dict) or set(row)-allowed or row.get('op') not in OPS:raise ValueError('등록된 에고 동작과 인수를 사용하세요.')
        for key,value in row.items():
            if isinstance(value,str) and len(value)>8000:raise ValueError('에고 입력이 너무 커요. 필요한 항목만 묶어 주세요.')
        if row['op']=='open':
            parsed=urlsplit(row.get('url',''))
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                raise ValueError('정상 HTTP(S) 사이트 주소를 지정하세요.')
        if row.get('page') is not None and not re.fullmatch(r'(?:p[1-9][0-9]*|\$last)',str(row['page'])):raise ValueError('실제 페이지 라벨을 지정하세요.')
        if row['op']=='wait_url' and not isinstance(row.get('url'),str):raise ValueError('대기할 주소 조건을 지정하세요.')
        if row['op']=='wait_any' and (not isinstance(row.get('selectors'),list) or not 1<=len(row['selectors'])<=10 or any(not isinstance(s,str) or not s or len(s)>2000 for s in row['selectors'])):raise ValueError('대기할 실제 CSS 조건을 1~10개 지정하세요.')
        if row['op']=='mark' and (not isinstance(row.get('name','challenge'),str) or not row.get('name','challenge')):raise ValueError('인증 요청 시각 이름을 확인하세요.')
        if row['op'] in {'fill','select','click','press','wait_selector','read','expect','otp_fill'} and not row.get('selector'):raise ValueError('관찰한 입력/클릭 대상이 필요해요.')
        if row['op'] in {'fill','press'} and not isinstance(row.get('value'),str):raise ValueError('입력 값을 확인해 주세요.')
        if row['op']=='select' and not any(isinstance(row.get(k),str) for k in ('value','label')):raise ValueError('선택 값이나 라벨을 확인해 주세요.')
        if 'timeout_ms' in row and (type(row['timeout_ms']) is not int or not 100<=row['timeout_ms']<=180000):raise ValueError('단계 대기는 100~180000ms로 지정하세요.')
        if row['op']=='login' and (not re.fullmatch('[0-9a-f]{32}',str(row.get('account_id',''))) or not row.get('password_selector')):raise ValueError('보관 계정과 실제 password 입력칸을 지정하세요.')
        if row['op']=='otp_fill' and (not row.get('match') or not (row.get('after_mark') or type(row.get('since')) in (int,float))):raise ValueError('인증 요청 시각과 사이트/발신자 구분 문구가 필요해요.')
        if row['op']=='expect' and not any(k in row for k in ('equals','contains','state')):raise ValueError('실제 결과 확인 조건을 지정하세요.')
        if row['op']=='expect' and row.get('state') in {'attached','visible'} and not any(k in row for k in ('equals','contains')):raise ValueError('결과 문구/값 조건도 지정하세요. 보이는 것만으로 저장 성공으로 판단하지 않아요.')


class _Broker:
    def __init__(self,db,root_request,steps,started):
        self.db=db;self.root_request=root_request;self.steps=steps;self.started=started
        self.token=secrets.token_urlsafe(32);self.used=set();self.secret_values=set();self.attempts=0
        self.cancelled=threading.Event();self.secret_lock=threading.Lock()
    def check(self):
        if self.cancelled.is_set():raise task_control.Stopped()
        if office_fastlane.stopped(self.db,self.root_request):raise task_control.Stopped()
    def dispatch(self,body):
        self.check();index=body.get('index')
        if type(index) is not int or not 0<=index<len(self.steps):raise ValueError('Unknown step')
        row=self.steps[index];self.attempts+=1
        if self.attempts>160:raise ValueError('Broker request limit')
        if body.get('action')=='checkpoint':return {'ok':True}
        if body.get('action')=='login' and row['op']=='login':
            site=credential_vault.origin(body.get('url',''))
            username,password=credential_vault.load_for_origin(row['account_id'],site)
            with self.secret_lock:self.secret_values.update(value for value in (username,password) if value)
            return {'origin':site,'username':username,'password':password}
        if body.get('action')=='otp' and row['op']=='otp_fill':
            site=credential_vault.origin(body.get('url',''))
            # Origin is pinned for filling by the runner; never expose a code
            # to a different origin between polling and input.
            since=body.get('since')
            if type(since) not in (int,float) or not self.started-900<=since<=time.time()+5:raise ValueError('Invalid challenge time')
            token=str(row['match']).casefold();source=str(row.get('source','')).casefold()
            digits=row.get('digits',6)
            if type(digits) is not int or not 4<=digits<=8:raise ValueError('Invalid code length')
            deadline=time.monotonic()+min(row.get('timeout_ms',120000)/1000,180)
            last=0
            while True:
                self.check()
                with closing(sqlite3.connect(Path(self.db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
                    try:
                        rows=c.execute('SELECT id,source,title,body,received,coalesce(ingested,received) FROM office_notifications '
                            'WHERE superseded_by IS NULL AND coalesce(ingested,received)>=? ORDER BY coalesce(ingested,received) DESC,received DESC,rowid DESC LIMIT 100',(since,)).fetchall()
                    except sqlite3.OperationalError:rows=[]
                import office_notifications
                for ident,app,title,body,received,ingested in rows:
                    if ident in self.used or received<since-15 or (source and source not in app.casefold()) or not office_notifications.source_allowed(app):continue
                    if token not in (title+' '+body).casefold():continue
                    codes=re.findall(r'(?:인증\s*번호|인증\s*코드|verification\s*code|code)[^\d]{0,20}(\d{'+str(digits)+r'})(?!\d)',body,re.I)
                    if not codes:
                        candidates=re.findall(r'(?<!\d)(\d{'+str(digits)+r'})(?!\d)',body)
                        codes=candidates if len(set(candidates))==1 else []
                    if codes:
                        self.used.add(ident)
                        with self.secret_lock:self.secret_values.add(codes[-1])
                        return {'origin':site,'code':codes[-1]}
                if time.monotonic()>=deadline:return {'error':'otp_timeout'}
                if time.monotonic()-last>10:
                    chat_stream.publish('working','인증문자를 기다리고 있어요 · 코드 실행 중');last=time.monotonic()
                time.sleep(.4)
        raise ValueError('Unknown broker action')


def run(db,request_id,steps,space_id=None,finish=False,keep=None,root_request_id=None):
    """One batch with optional owned-space reuse; no automatic effect retries."""
    _validate(steps)
    if not re.fullmatch('[0-9a-f]{32}',str(request_id)):raise ValueError('에고 묶음 요청 ID를 확인해 주세요.')
    if type(finish) is not bool or (space_id is not None and type(space_id) is not int):raise ValueError('에고 작업 공간을 확인해 주세요.')
    keep=[] if keep is None else keep
    if not isinstance(keep,list) or len(keep)>10 or any(not re.fullmatch('p[1-9][0-9]*',str(p)) for p in keep):raise ValueError('유지할 실제 페이지 라벨을 지정하세요.')
    exe=executable();root_request=root_request_id or request_id
    signature=hashlib.sha256(json.dumps([steps,space_id,finish,keep],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    started=time.monotonic();started_at=time.time()
    receipt={'id':request_id,'state':'running','browser':'ego','mode':'code','model_calls':0,'space_id':space_id,'steps':[],'verified':False}
    with closing(_connect(db)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT signature,data FROM ego_batch_runs WHERE id=?',(request_id,)).fetchone()
        if old:
            if old['signature']!=signature:raise ValueError('같은 에고 묶음 ID의 내용이 달라요.')
            result=json.loads(old['data']);result['replayed']=True;return result
        if space_id is not None:
            saved=c.execute('SELECT state FROM ego_batch_spaces WHERE id=?',(space_id,)).fetchone()
            if not saved or saved[0]!='active':raise ValueError('현재 어댑터가 소유한 활성 공간만 재사용할 수 있어요. 사용자 공간을 임의로 가져오지 않았어요.')
        c.execute('INSERT INTO ego_batch_runs VALUES (?,?,?,?,?)',(request_id,signature,'running',json.dumps(receipt),time.time()))
    def save():
        receipt['elapsed_ms']=round((time.monotonic()-started)*1000)
        receipt['transport_ms']=max(0,receipt['elapsed_ms']-sum(step.get('elapsed_ms',0) for step in receipt.get('steps',[])))
        with closing(_connect(db)) as c,c:
            c.execute('UPDATE ego_batch_runs SET state=?,data=?,updated=? WHERE id=?',(receipt['state'],json.dumps(receipt,ensure_ascii=False),time.time(),request_id))
            if receipt.get('space_id') is not None:
                c.execute('INSERT OR REPLACE INTO ego_batch_spaces VALUES (?,?,?,?)',(receipt['space_id'],root_request,'finished' if receipt.get('finished') else 'active',time.time()))
    broker=_Broker(db,root_request,steps,started_at)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*unused):pass
        def do_POST(self):
            if self.path!='/secret' or not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+broker.token):self.send_error(403);return
            try:
                n=int(self.headers.get('Content-Length','0'))
                if not 0<n<=8192:raise ValueError()
                value=broker.dispatch(json.loads(self.rfile.read(n)));raw=json.dumps(value).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            except Exception:
                raw=b'{"error":"broker_unavailable"}';self.send_response(400);self.send_header('Content-Length',str(len(raw)));self.end_headers()
                try:self.wfile.write(raw)
                except OSError:pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    PRIVATE.mkdir(parents=True,exist_ok=True)
    folder=PRIVATE/(request_id+'-'+uuid.uuid4().hex);folder.mkdir()
    proc=None
    try:
        broker.check()
        config={'steps':steps,'space':space_id if space_id is not None else 'AI OFFICE '+root_request,
                'finish':finish,'keep':keep,'broker':'http://127.0.0.1:'+str(server.server_port)+'/secret','token':broker.token}
        (folder/'plan.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
        entry=folder/'run.mjs'
        entry.write_text('globalThis.officeEgoPlan='+json.dumps((folder/'plan.json').as_uri())+';\nawait import('+json.dumps((ROOT/'ego_runner.mjs').as_uri())+');\n',encoding='utf-8')
        output=folder/'output.txt'
        # Secrets are not in args/env/scripts: the runner retrieves them only
        # after observing the live HTTPS form through its memory-only broker.
        with output.open('wb') as stream:
            proc=subprocess.Popen([exe,'nodejs','-e',"await import('"+entry.as_uri()+"')"],stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,
                cwd=str(ROOT),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+min(600,45+sum(min(step.get('timeout_ms',15000),180000)/1000 for step in steps))
            while proc.poll() is None:
                broker.check()
                if time.monotonic()>=deadline:raise TimeoutError()
                time.sleep(.15)
        with output.open('rb') as stream:
            stream.seek(max(0,output.stat().st_size-1024*1024));raw=stream.read().decode('utf-8','replace')
        lines=[line[len('OFFICE_EGO_RESULT:'):] for line in raw.splitlines() if line.startswith('OFFICE_EGO_RESULT:')]
        if not lines:receipt.update(state='uncertain',error_type='missing_receipt',execution_uncertain=True)
        else:
            received=json.loads(lines[-1])
            receipt.update({key:received[key] for key in ('state','space_id','steps','verified','finished','pages','failed_step','error_type','error_code') if key in received})
            if receipt['state']=='error':receipt['execution_uncertain']=True
        # Explicit redaction also protects a password echoed by a site's DOM.
    except task_control.Stopped:receipt.update(state='stopped',execution_uncertain=proc is not None)
    except Exception as error:receipt.update(state='uncertain',error_type=type(error).__name__,execution_uncertain=proc is not None)
    finally:
        broker.cancelled.set()
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
        # Even if stopped after a click, keep the completed step receipts and
        # the created space. Never replay a batch whose final response is lost.
        if 'output' in locals() and output.is_file():
            with output.open('rb') as stream:
                stream.seek(max(0,output.stat().st_size-1024*1024));tail=stream.read().decode('utf-8','replace')
            if not receipt.get('steps'):
                completed=[]
                for line in tail.splitlines():
                    try:
                        if line.startswith('OFFICE_EGO_SPACE:'):receipt['space_id']=json.loads(line.split(':',1)[1])['space_id']
                        elif line.startswith('OFFICE_EGO_STEP:'):completed.append(json.loads(line.split(':',1)[1]))
                    except (ValueError,KeyError,TypeError):pass
                receipt['steps']=completed
        with broker.secret_lock:secret_values=sorted(broker.secret_values,key=len,reverse=True)
        def redact(value):
            if isinstance(value,str):
                for secret in secret_values:value=value.replace(secret,'[가림]')
                return value
            if isinstance(value,list):return [redact(v) for v in value]
            if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
            return value
        receipt=redact(receipt)
        server.shutdown();server.server_close()
        # Only this request's validated private scratch files, never profiles.
        for path in folder.iterdir():
            if path.is_file():
                try:path.unlink()
                except OSError:pass
        try:folder.rmdir()
        except OSError:pass
        save()
    return receipt
