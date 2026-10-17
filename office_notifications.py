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
                for name,declaration in {'source_state':"TEXT NOT NULL DEFAULT 'unknown'",'source_missing':'REAL','can_reply':'INTEGER NOT NULL DEFAULT 0','superseded_by':'TEXT'}.items():
                    if name not in columns:connection.execute('ALTER TABLE office_notifications ADD COLUMN '+name+' '+declaration)
            connection.execute('''CREATE TABLE IF NOT EXISTS office_notification_actions (
                request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, response TEXT, created REAL NOT NULL)''')
            connection.execute('CREATE INDEX IF NOT EXISTS office_notification_time ON office_notifications(received DESC)')
            connection.execute('CREATE TABLE IF NOT EXISTS office_notification_receipts (event_key TEXT PRIMARY KEY, recorded REAL NOT NULL)')
            yield connection
    finally:connection.close()

def _text(value,limit):
    if not isinstance(value,str):raise ValueError('알림 내용을 확인할 수 없습니다.')
    return ''.join(c for c in value if ord(c)>=32 or c in '\n\t').strip()[:limit]


def _collapse_between(connection):
    """A bodyless desktop signal is a fallback, not a second message card."""
    rows=connection.execute("SELECT id,received,title,body FROM office_notifications WHERE origin='native_between_banner' AND superseded_by IS NULL").fetchall()
    for row in rows:
        generic=row['body'].startswith('PC 비트윈 알림을 감지했어요.')
        rich=connection.execute("SELECT id FROM office_notifications WHERE origin='phone_link_panel' AND lower(source) IN ('between','비트윈') AND ABS(received-?)<=30 AND (? OR (title=? AND body=?)) ORDER BY ABS(received-?) LIMIT 1",(row['received'],generic,row['title'],row['body'],row['received'])).fetchone()
        if rich:connection.execute("UPDATE office_notifications SET superseded_by=?,confirmed=COALESCE(confirmed,?),phone_sync='superseded' WHERE id=?",(rich['id'],time.time(),row['id']))


def reconcile_source(db,event):
    """Only a connected, complete repeated inventory proves source removal."""
    entries=event.get('entries',[])
    if not isinstance(entries,list) or len(entries)>160:return
    keys={row['key'] for row in entries if isinstance(row,dict) and isinstance(row.get('key'),str) and row['key'].startswith('phone-link:') and len(row['key'])<=200}
    fingerprints={}
    for entry in entries:
        if isinstance(entry,dict) and entry.get('key') in keys and isinstance(entry.get('fingerprint'),str):fingerprints.setdefault(entry['fingerprint'],[]).append(entry['key'])
    now=time.time()
    complete=event.get('connected') is True and event.get('complete') is True
    with _db(db) as c:
        rows=c.execute("SELECT id,event_key,fingerprint,source_missing,source_state FROM office_notifications WHERE origin='phone_link_panel'").fetchall()
        for row in rows:
            legacy=fingerprints.get(row['fingerprint'],[]) if not row['event_key'] else []
            key=row['event_key'] if row['event_key'] in keys else legacy[0] if len(legacy)==1 else None
            if key:
                c.execute("UPDATE office_notifications SET source_state='present',source_missing=NULL,can_reply=0,event_key=COALESCE(event_key,?),phone_sync=CASE WHEN phone_sync='source_removed' THEN 'local_only' ELSE phone_sync END WHERE id=?",(key,row['id']))
            elif legacy:
                c.execute("UPDATE office_notifications SET source_state='present',source_missing=NULL,can_reply=0 WHERE id=?",(row['id'],))
            elif not complete:
                c.execute('UPDATE office_notifications SET source_missing=NULL WHERE id=?',(row['id'],))
            elif row['source_state']!='removed':
                if row['source_missing'] is None:c.execute('UPDATE office_notifications SET source_missing=? WHERE id=?',(now,row['id']))
                elif now-row['source_missing']>=4:
                    c.execute("UPDATE office_notifications SET source_state='removed',can_reply=0,confirmed=COALESCE(confirmed,?),phone_sync='source_removed' WHERE id=?",(now,row['id']))

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
        if origin in {'phone_link_panel','native_between_banner'}:_collapse_between(c)

def _phone_action(payload,timeout=25):
    try:
        result=subprocess.run([sys.executable,str(ROOT/'notification_reply.py')],
            input=json.dumps(payload,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',
            timeout=timeout,env=dict(os.environ,PYTHONIOENCODING='utf-8'),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=json.loads(result.stdout[:1024*1024])
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise ValueError('원본 알림 동작 결과를 확인하지 못했어요. 휴대폰 연결 상태를 확인해 주세요.') from None
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
                rows=c.execute("SELECT * FROM office_notifications WHERE superseded_by IS NULL AND received<=? AND (confirmed IS NULL OR phone_sync IN ('local_only','unavailable','pending')) ORDER BY received DESC LIMIT 80",(through,)).fetchall()
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
        row=c.execute('SELECT id,source,title,body,received,confirmed,origin,phone_sync,event_key,source_state FROM office_notifications WHERE id=?',(identifier,)).fetchone()
    if row is None:raise ValueError('알림을 찾지 못했어요. 목록을 새로 불러와 주세요.')
    return dict(row)


def snapshot(db):
    with _db(db) as c:
        rows=c.execute('SELECT id,source,title,body,received,confirmed,origin,phone_sync,source_state FROM office_notifications WHERE superseded_by IS NULL ORDER BY received DESC,rowid DESC LIMIT 80').fetchall()
        unread=c.execute('SELECT count(*) FROM office_notifications WHERE confirmed IS NULL AND superseded_by IS NULL').fetchone()[0]
    service=_service
    status=service.status() if service else {'state':'stopped','message':'알림 수신기를 준비하고 있어요.'}
    return {'items':[dict(row) for row in rows],'unread':unread,'through':time.time(),'receiver':status,
            'scope':'Phone Link에 전달된 알림과 지원 PC 배너를 수집해요. 메신저 앱 안의 전체 미확인 대화 개수와는 달라요.'}

class Receiver:
    def __init__(self,db):
        self.db=db;self.stop=threading.Event();self.lock=threading.Lock();self.process=None
        self.state='starting';self.message='PC 알림 수신기를 준비하고 있어요.';self.last_heartbeat=0
        self.phone_startup={'state':'preparing','message':'휴대폰과 연결 자동 준비 중'}
        self.source_info={'connected':False,'complete':False,'sources':{}}
        self.inventory_at=0;self.last_recovery=time.monotonic();self.recovering=False
        self.thread=threading.Thread(target=self._run,name='office-notification-receiver',daemon=True)
        self.thread.start()
        threading.Thread(target=self._watchdog,name='office-notification-watchdog',daemon=True).start()
        threading.Thread(target=self._maintain_link,name='office-phone-link-keepalive',daemon=True).start()

    def status(self):
        with self.lock:
            state=self.state;message=self.message
            if state=='watching' and time.monotonic()-self.last_heartbeat>30:
                state='unavailable';message='알림 수신 상태를 확인하고 있어요.'
            if self.recovering:message='휴대폰 알림 연결을 다시 확인하고 있어요.'
            elif self.inventory_at and not self.source_info['connected']:message='휴대폰 연결이 끊겼어요. 저장된 알림을 표시하며 자동 복구를 기다리고 있어요.'
            elif self.source_info['connected'] and not self.source_info['complete']:message='휴대폰 연결됨 · 목록 일부만 확인 중, 역방향 확인 동기화 대기'
            return {'state':state,'message':message,'mode':'phone_link_panel_and_banners','uses_ai':False,
                    'phone_startup':dict(self.phone_startup),'phone_connection':dict(self.source_info),
                    'automatic_recovery':True}

    def recover(self):
        if not _ACTION_LOCK.acquire(False):raise ValueError('다른 알림 처리가 끝난 뒤 다시 연결해 주세요.')
        try:
            with self.lock:self.recovering=True;self.last_recovery=time.monotonic()
            import phone_link_runtime
            startup=phone_link_runtime.ensure_ready(timeout=15)
            try:result=_phone_action({'operation':'reconnect'},timeout=8)
            except ValueError:result={'requested':False}
            with self.lock:self.phone_startup=startup
            return {'requested':bool(result.get('requested')),'message':'기존 휴대폰 연결을 새로고침했어요. 실제 연결 상태는 수신기가 다시 확인해요.' if result.get('requested') else '연결 준비를 요청했어요. 휴대폰이나 PC에 권한 확인이 뜨면 직접 확인해 주세요.'}
        finally:
            with self.lock:self.recovering=False
            _ACTION_LOCK.release()

    def _maintain_link(self):
        while not self.stop.wait(15):
            with self.lock:
                connected=self.source_info['connected'];elapsed=time.monotonic()-self.last_recovery
            if elapsed<(180 if connected else 60):continue
            try:self.recover()
            except (ValueError,OSError):pass

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
                with _db(self.db) as c:c.execute('UPDATE office_notifications SET source_missing=NULL')
                for raw in iter(lambda:process.stdout.readline(64001),b''):
                    if self.stop.is_set():break
                    if len(raw)>64000:raise ValueError('oversized worker event')
                    event=json.loads(raw)
                    if event.get('kind')=='notification':ingest(self.db,event)
                    elif event.get('kind')=='phone_inventory':
                        reconcile_source(self.db,event)
                        with self.lock:
                            self.inventory_at=time.monotonic()
                            self.source_info={'connected':event.get('connected') is True,'complete':event.get('complete') is True,'sources':event.get('sources',{})}
                    elif event.get('kind')=='ready':
                        with self.lock:
                            self.state='watching';self.message='휴대폰 알림 연결 확인 중';self.last_heartbeat=time.monotonic()
                    elif event.get('kind')=='heartbeat':
                        with self.lock:
                            self.last_heartbeat=time.monotonic()
                            self.message={'reading':'휴대폰 알림 목록 연결됨','partial':'휴대폰 알림 목록 일부 확인 중','disconnected':'휴대폰 연결이 끊겼어요','not_found':'휴대폰과 연결 앱을 다시 준비하고 있어요','pane_unavailable':'휴대폰과 연결의 알림 영역을 확인해 주세요'}.get(event.get('phone_state'),'휴대폰 알림 연결 확인 중')
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
            with _db(db) as c:_collapse_between(c)
            _service=Receiver(db)
            atexit.register(_service.close)
    return _service
