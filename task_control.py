"""Authenticated per-request human steering; no independent work is launched."""
from contextlib import contextmanager
from contextvars import ContextVar
import datetime
import json
import re
import sqlite3
import threading

_guard=threading.RLock()
_active={}
_current=ContextVar('office_task_control',default=None)


class Stopped(Exception):pass


def current():return _current.get()


def checkpoint():
    control=current()
    if control and control.stopped.is_set():raise Stopped('사용자가 작업 중지를 요청했어요.')


class Control:
    def __init__(self,db,request_id):
        self.db=str(db);self.request_id=request_id;self.stopped=threading.Event();self.lock=threading.RLock();self.callbacks=set();self.accepting=True
    def inputs(self,final=False):
        with self.lock,sqlite3.connect(self.db,timeout=5) as c:
            rows=c.execute("SELECT id,message FROM assistant_inputs WHERE request_id=? AND kind='message' AND state='queued' ORDER BY rowid",(self.request_id,)).fetchall()
            for ident,_ in rows:c.execute("UPDATE assistant_inputs SET state='applied' WHERE id=?",(ident,))
            if final and not rows:self.accepting=False
            return [{'id':ident,'message':text} for ident,text in rows]
    def subscribe(self,callback):
        with self.lock:
            self.callbacks.add(callback);already=self.stopped.is_set()
        if already:
            try:callback()
            except OSError:pass
        def remove():
            with self.lock:self.callbacks.discard(callback)
        return remove


@contextmanager
def capture(db,request_id):
    with sqlite3.connect(str(db),timeout=5) as c:
        c.execute('CREATE TABLE IF NOT EXISTS assistant_inputs (id TEXT PRIMARY KEY,request_id TEXT NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL,state TEXT NOT NULL,created TEXT NOT NULL)')
    control=Control(db,request_id)
    with _guard:_active[request_id]=control
    token=_current.set(control)
    try:yield control
    finally:
        _current.reset(token)
        with control.lock:control.accepting=False
        with _guard:
            if _active.get(request_id) is control:del _active[request_id]
        with control.lock:control.callbacks.clear()


def submit(db,payload):
    if not isinstance(payload,dict) or set(payload)-{'request_id','input_id','kind','message'}:raise ValueError('작업 중 입력 요청을 확인해 주세요.')
    request_id=payload.get('request_id');ident=payload.get('input_id');kind=payload.get('kind');text=payload.get('message','')
    if any(not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value) for value in (request_id,ident)) or kind not in {'message','stop'}:raise ValueError('현재 작업을 다시 확인해 주세요.')
    if not isinstance(text,str) or (kind=='message' and not 1<=len(text.strip())<=4000) or (kind=='stop' and text):raise ValueError('추가 메시지는 4000자 이내로 입력해 주세요.')
    with _guard:control=_active.get(request_id)
    with sqlite3.connect(str(db),timeout=5) as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_inputs'").fetchone()
        old=c.execute('SELECT request_id,kind,message,state FROM assistant_inputs WHERE id=?',(ident,)).fetchone() if exists else None
    if old:
        if old[:3]!=(request_id,kind,text):raise ValueError('이미 사용한 입력 번호의 내용이 달라요.')
        return {'accepted':True,'input_id':ident,'kind':kind,'state':old[3]}
    if not control:return {'accepted':False,'code':'task_not_running','message':'해당 작업이 끝났어요. 새 요청으로 보내 주세요.'}
    with control.lock,sqlite3.connect(str(db),timeout=5) as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT request_id,kind,message,state FROM assistant_inputs WHERE id=?',(ident,)).fetchone()
        if old:
            if old[:3]!=(request_id,kind,text):raise ValueError('이미 사용한 입력 번호의 내용이 달라요.')
            return {'accepted':True,'input_id':ident,'kind':kind,'state':old[3]}
        if not control.accepting:return {'accepted':False,'code':'task_not_running','message':'해당 작업이 끝났어요. 새 요청으로 보내 주세요.'}
        if control.stopped.is_set() and kind=='message':return {'accepted':False,'code':'task_stopping','message':'작업을 중지하고 있어요. 메시지는 잠시 뒤 새 요청으로 보내 주세요.'}
        row=c.execute('SELECT status FROM assistant_requests WHERE id=?',(request_id,)).fetchone()
        if not row or row[0]!='running':return {'accepted':False,'code':'task_not_running','message':'해당 작업이 끝났어요. 새 요청으로 보내 주세요.'}
        if kind=='message' and c.execute("SELECT count(*) FROM assistant_inputs WHERE request_id=? AND state='queued' AND kind='message'",(request_id,)).fetchone()[0]>=20:raise ValueError('앞서 보낸 추가 메시지를 반영할 때까지 잠시 기다려 주세요.')
        stamp=datetime.datetime.now().astimezone().isoformat()
        c.execute('INSERT INTO assistant_inputs VALUES (?,?,?,?,?,?)',(ident,request_id,kind,text,'queued' if kind=='message' else 'stopping',stamp))
        c.execute('INSERT INTO work_messages VALUES (?,?,?,?)',(ident,'user',text if kind=='message' else '작업 중지 요청',stamp))
        if kind=='stop':control.stopped.set()
        callbacks=list(control.callbacks) if kind=='stop' else []
    for callback in callbacks:
        try:callback()
        except Exception:pass
    return {'accepted':True,'input_id':ident,'kind':kind,'state':'queued' if kind=='message' else 'stopping'}


@contextmanager
def interruptible(process):
    control=current()
    def stop():
        if process.poll() is None:process.terminate()
    remove=control.subscribe(stop) if control else lambda:None
    try:checkpoint();yield
    finally:remove()
