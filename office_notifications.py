"""Local, durable notification inbox. Receiving never calls a model or sends replies."""
import atexit
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parent
_guard=threading.Lock()
_SCHEMA_LOCK=threading.Lock()
_ACTION_LOCK=threading.Lock()
_service=None

@contextlib.contextmanager
def _db(path):
    connection=sqlite3.connect(str(path),timeout=10)
    connection.row_factory=sqlite3.Row
    try:
        with connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS office_notifications (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, received REAL NOT NULL, confirmed REAL,
                fingerprint TEXT NOT NULL, origin TEXT NOT NULL)''')
            with _SCHEMA_LOCK:
                columns={row[1] for row in connection.execute('PRAGMA table_info(office_notifications)')}
                if 'phone_sync' not in columns:
                    connection.execute("ALTER TABLE office_notifications ADD COLUMN phone_sync TEXT NOT NULL DEFAULT 'local_only'")
                if 'event_key' not in columns:
                    connection.execute('ALTER TABLE office_notifications ADD COLUMN event_key TEXT')
            connection.execute('''CREATE TABLE IF NOT EXISTS office_notification_actions (
                request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response TEXT, created REAL NOT NULL)''')
            connection.execute('CREATE INDEX IF NOT EXISTS office_notification_time ON office_notifications(received DESC)')
            connection.execute('CREATE TABLE IF NOT EXISTS office_notification_receipts (event_key TEXT PRIMARY KEY, recorded REAL NOT NULL)')
            yield connection
    finally:connection.close()

def _text(value,limit):
    if not isinstance(value,str):raise ValueError('알림 내용을 확인할 수 없습니다.')
    return ''.join(c for c in value if ord(c)>=32 or c in '\n\t').strip()[:limit]

def ingest(db,event):
    """Only the trusted local capture worker calls this; no public ingest endpoint."""
    source=_text(event.get('source',''),160)
    title=_text(event.get('title',''),400)
    body=_text(event.get('body',''),12000)
    if not source or not (title or body):return
    digest=hashlib.sha256(json.dumps([source,title,body],ensure_ascii=False).encode()).hexdigest()
    now=time.time()
    event_key=event.get('event_key')
    if event_key is not None and (not isinstance(event_key,str) or not 1<=len(event_key)<=200):return
    origin=event.get('origin') if event.get('origin') in {'phone_link_panel','native_between_banner'} else 'windows_banner'
    with _db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        if event_key:
            inserted=c.execute('INSERT OR IGNORE INTO office_notification_receipts VALUES (?,?)',(event_key,now))
            if not inserted.rowcount:return
        if not event_key and c.execute('SELECT 1 FROM office_notifications WHERE fingerprint=? AND received>?',(digest,now-90)).fetchone():return
        c.execute('INSERT INTO office_notifications (id,source,title,body,received,confirmed,fingerprint,origin,event_key) VALUES (?,?,?,?,?,NULL,?,?,?)',
                  (uuid.uuid4().hex,source,title,body,now,digest,origin,event_key))

def _phone_action(payload,timeout=25):
    try:
        result=subprocess.run([sys.executable,str(ROOT/'notification_reply.py')],
            input=json.dumps(payload,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',
            timeout=timeout,env=dict(os.environ,PYTHONIOENCODING='utf-8'),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=json.loads(result.stdout[:1024*1024])
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise ValueError('원본 알림 동작 결과를 확인하지 못했어요. 다시 보내기 전에 원래 앱을 확인해 주세요.') from None
    if not isinstance(data,dict) or result.returncode or data.get('error'):
        raise ValueError(data.get('error','원본 알림 동작을 완료하지 못했어요.') if isinstance(data,dict) else '원본 알림 응답을 확인하지 못했어요.')
    return data


def confirm(db,identifier=None,through=None):
    with _ACTION_LOCK:
        with _db(db) as c:
            if identifier is not None:
                if not isinstance(identifier,str) or len(identifier)!=32:raise ValueError('알림을 다시 선택해 주세요.')
                rows=c.execute('SELECT * FROM office_notifications WHERE id=?',(identifier,)).fetchall()
            elif type(through) in (int,float) and 0<through<=time.time()+5:
                rows=c.execute("SELECT * FROM office_notifications WHERE received<=? AND (confirmed IS NULL OR phone_sync IN ('local_only','unavailable','pending')) ORDER BY received DESC LIMIT 80",(through,)).fetchall()
            else:raise ValueError('확인할 알림 범위를 다시 불러와 주세요.')
            if identifier and not rows:raise ValueError('알림을 찾지 못했어요.')
            items=[dict(row) for row in rows]
            for item in items:
                c.execute("UPDATE office_notifications SET confirmed=COALESCE(confirmed,?),phone_sync='pending' WHERE id=?",(time.time(),item['id']))
        results=[]
        if items:
            try:results=_phone_action({'operation':'acknowledge','items':items},timeout=35)['results']
            except (ValueError,KeyError):results=[{'id':item['id'],'state':'unavailable'} for item in items]
            by_id={item['id']:item for item in results}
            with _db(db) as c:
                for item in items:
                    state=by_id.get(item['id'],{}).get('state','unavailable')
                    if state not in {'dismissed','not_present','unsupported'}:state='unavailable'
                    c.execute('UPDATE office_notifications SET phone_sync=? WHERE id=?',(state,item['id']))
        data=snapshot(db)
        counts={key:sum(row.get('state')==key for row in results) for key in ('dismissed','not_present','unsupported','unavailable')}
        data['sync_summary']=counts
        data['confirmation_message']=('오피스에서 확인했어요. 일부 휴대폰 알림 정리는 아직 연결되지 않았어요. 해당 카드에서 다시 확인할 수 있어요.' if counts['unavailable'] else
            '오피스와 Phone Link 알림을 정리했어요. 휴대폰 알림에도 동기화되며, 메신저 대화의 읽음 상태와는 별개예요.' if counts['dismissed'] else
            '확인했어요. 해당 원본 알림은 Phone Link에 이미 없거나 휴대폰 동기화 대상이 아니에요.')
        return data


def get(db,identifier):
    if not isinstance(identifier,str) or len(identifier)!=32:raise ValueError('알림을 다시 선택해 주세요.')
    with _db(db) as c:
        row=c.execute('SELECT id,source,title,body,received,confirmed,origin,phone_sync,event_key FROM office_notifications WHERE id=?',(identifier,)).fetchone()
    if row is None:raise ValueError('알림을 찾지 못했어요. 목록을 새로 불러와 주세요.')
    return dict(row)

def reply(db,identifier,text,request_id):
    item=get(db,identifier)
    if item['origin']!='phone_link_panel':raise ValueError('이 알림은 휴대폰 답장 동기화 대상이 아니에요.')
    if not isinstance(text,str):raise ValueError('답장 내용을 확인해 주세요.')
    text=''.join(c for c in text if ord(c)>=32 or c in '\n\t').strip()
    if not text or len(text)>3000:raise ValueError('답장은 1~3000자까지 입력해 주세요.')
    if not isinstance(request_id,str) or len(request_id)!=32 or any(c not in '0123456789abcdef' for c in request_id):
        raise ValueError('오피스를 새로고침한 뒤 답장해 주세요.')
    signature=hashlib.sha256(json.dumps([identifier,text],ensure_ascii=False).encode()).hexdigest()
    with _ACTION_LOCK:
        with _db(db) as c:
            prior=c.execute('SELECT signature,response FROM office_notification_actions WHERE request_id=?',(request_id,)).fetchone()
            if prior:
                if prior['signature']!=signature:raise ValueError('답장 요청이 달라졌어요. 알림 카드를 다시 열어 주세요.')
                if prior['response']:
                    result=json.loads(prior['response'])
                    if result.get('error'):raise ValueError(result['error'])
                    return result
                raise ValueError('이 답장의 전송 결과가 불명확해 다시 보내지 않았어요. 원래 앱을 확인해 주세요.')
            c.execute('INSERT INTO office_notification_actions VALUES (?,?,NULL,?)',(request_id,signature,time.time()))
        try:result=_phone_action({'operation':'reply','item':item,'text':text})
        except ValueError as error:
            with _db(db) as c:c.execute('UPDATE office_notification_actions SET response=? WHERE request_id=?',(json.dumps({'error':str(error)},ensure_ascii=False),request_id))
            raise
        with _db(db) as c:c.execute('UPDATE office_notification_actions SET response=? WHERE request_id=?',(json.dumps(result,ensure_ascii=False),request_id))
        return result


def snapshot(db):
    with _db(db) as c:
        rows=c.execute('SELECT id,source,title,body,received,confirmed,origin,phone_sync FROM office_notifications ORDER BY received DESC,rowid DESC LIMIT 80').fetchall()
        unread=c.execute('SELECT count(*) FROM office_notifications WHERE confirmed IS NULL').fetchone()[0]
    service=_service
    status=service.status() if service else {'state':'stopped','message':'알림 수신기를 준비하고 있어요.'}
    return {'items':[dict(row) for row in rows],'unread':unread,'through':time.time(),'receiver':status,
            'scope':'휴대폰과 연결 앱의 노출된 알림 목록과 PC 알림 배너. 원본 대화·숨겨진 내용은 수집하지 않습니다.'}

class Receiver:
    def __init__(self,db):
        self.db=db;self.stop=threading.Event();self.lock=threading.Lock();self.process=None
        self.state='starting';self.message='PC 알림 수신기를 준비하고 있어요.';self.last_heartbeat=0
        self.phone_startup={'state':'preparing','message':'휴대폰과 연결 자동 준비 중'}
        self.thread=threading.Thread(target=self._run,name='office-notification-receiver',daemon=True)
        self.thread.start()
        threading.Thread(target=self._watchdog,name='office-notification-watchdog',daemon=True).start()

    def status(self):
        with self.lock:
            state=self.state;message=self.message
            if state=='watching' and time.monotonic()-self.last_heartbeat>30:
                state='unavailable';message='알림 수신 상태를 확인하고 있어요.'
            return {'state':state,'message':message,'mode':'phone_link_panel_and_banners','uses_ai':False,
                    'phone_startup':dict(self.phone_startup)}

    def close(self):
        self.stop.set()
        with self.lock:process=self.process
        if process and process.poll() is None:process.terminate()

    def _watchdog(self):
        while not self.stop.wait(5):
            with self.lock:
                process=self.process;stale=time.monotonic()-self.last_heartbeat>45
            # A hung third-party accessibility provider must not stop receiving
            # forever. Only terminate this application's own capture child.
            if process and process.poll() is None and stale:
                try:process.terminate()
                except OSError:pass

    def _run(self):
        if os.name!='nt':
            with self.lock:self.state='unavailable';self.message='Windows에서 알림 수신을 사용할 수 있어요.'
            return
        # This runs once per AI OFFICE start, not on capture-worker retries.
        try:
            import phone_link_runtime
            startup=phone_link_runtime.ensure_ready()
        except Exception:
            startup={'state':'launch_failed','message':'휴대폰과 연결 자동 준비를 완료하지 못했어요.'}
        with self.lock:self.phone_startup=startup
        while not self.stop.is_set():
            process=None
            try:
                environment=dict(os.environ,PYTHONIOENCODING='utf-8')
                process=subprocess.Popen([sys.executable,'-u',str(ROOT/'notification_capture.py'),'--parent',str(os.getpid())],
                    cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                with self.lock:self.process=process;self.last_heartbeat=time.monotonic()
                for raw in iter(lambda:process.stdout.readline(64001),b''):
                    if self.stop.is_set():break
                    if len(raw)>64000:raise ValueError('oversized worker event')
                    event=json.loads(raw)
                    if event.get('kind')=='notification':ingest(self.db,event)
                    elif event.get('kind')=='ready':
                        with self.lock:
                            self.state='watching';self.message='휴대폰 알림 연결 확인 중';self.last_heartbeat=time.monotonic()
                    elif event.get('kind')=='heartbeat':
                        with self.lock:
                            self.last_heartbeat=time.monotonic()
                            self.message={'reading':'휴대폰 알림 목록 연결됨','not_found':'휴대폰과 연결 앱을 실행해 주세요','pane_unavailable':'휴대폰과 연결의 알림 영역을 확인해 주세요'}.get(event.get('phone_state'),'휴대폰 알림 연결 확인 중')
                    elif event.get('kind')=='unavailable':raise RuntimeError('capture unavailable')
            except (OSError,ValueError,RuntimeError,sqlite3.Error):pass
            finally:
                if process:
                    if process.poll() is None:process.terminate()
                    try:process.wait(timeout=3)
                    except subprocess.TimeoutExpired:process.kill()
                    if process.stdout:process.stdout.close()
                with self.lock:
                    self.process=None;self.state='unavailable';self.message='알림 수신기를 다시 연결하고 있어요.'
            self.stop.wait(15)

def start(db):
    global _service
    with _guard:
        if _service is None:
            with _db(db):pass
            _service=Receiver(db)
            atexit.register(_service.close)
    return _service
