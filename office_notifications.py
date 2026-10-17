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
        c.execute('INSERT INTO office_notifications VALUES (?,?,?,?,?,NULL,?,?)',
                  (uuid.uuid4().hex,source,title,body,now,digest,origin))

def confirm(db,identifier=None,through=None):
    with _db(db) as c:
        if identifier is not None:
            if not isinstance(identifier,str) or len(identifier)!=32:raise ValueError('알림을 다시 선택해 주세요.')
            c.execute('UPDATE office_notifications SET confirmed=? WHERE id=? AND confirmed IS NULL',(time.time(),identifier))
        elif type(through) in (int,float) and 0<through<=time.time()+5:
            # A new event arriving after the user's displayed snapshot stays unread.
            c.execute('UPDATE office_notifications SET confirmed=? WHERE confirmed IS NULL AND received<=?',(time.time(),through))
        else:raise ValueError('확인할 알림 범위를 다시 불러와 주세요.')
    return snapshot(db)

def get(db,identifier):
    if not isinstance(identifier,str) or len(identifier)!=32:raise ValueError('알림을 다시 선택해 주세요.')
    with _db(db) as c:
        row=c.execute('SELECT id,source,title,body,received,confirmed FROM office_notifications WHERE id=?',(identifier,)).fetchone()
    if row is None:raise ValueError('알림을 찾지 못했어요. 목록을 새로 불러와 주세요.')
    return dict(row)

def snapshot(db):
    with _db(db) as c:
        rows=c.execute('SELECT id,source,title,body,received,confirmed,origin FROM office_notifications ORDER BY received DESC,rowid DESC LIMIT 80').fetchall()
        unread=c.execute('SELECT count(*) FROM office_notifications WHERE confirmed IS NULL').fetchone()[0]
    service=_service
    status=service.status() if service else {'state':'stopped','message':'알림 수신기를 준비하고 있어요.'}
    return {'items':[dict(row) for row in rows],'unread':unread,'through':time.time(),'receiver':status,
            'scope':'휴대폰과 연결 앱의 노출된 알림 목록과 PC 알림 배너. 원본 대화·숨겨진 내용은 수집하지 않습니다.'}

class Receiver:
    def __init__(self,db):
        self.db=db;self.stop=threading.Event();self.lock=threading.Lock();self.process=None
        self.state='starting';self.message='PC 알림 수신기를 준비하고 있어요.';self.last_heartbeat=0
        self.thread=threading.Thread(target=self._run,name='office-notification-receiver',daemon=True)
        self.thread.start()
        threading.Thread(target=self._watchdog,name='office-notification-watchdog',daemon=True).start()

    def status(self):
        with self.lock:
            state=self.state;message=self.message
            if state=='watching' and time.monotonic()-self.last_heartbeat>30:
                state='unavailable';message='알림 수신 상태를 확인하고 있어요.'
            return {'state':state,'message':message,'mode':'phone_link_panel_and_banners','uses_ai':False}

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
