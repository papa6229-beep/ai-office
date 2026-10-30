"""Mirror future visible replies from the connected desktop chat; no inference."""
import atexit
from contextlib import closing
import datetime
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid

import desktop_chat_bridge as bridge

_lock=threading.RLock()
_worker=None
_stop=threading.Event()
_status={'revision':0,'enabled':False,'available':None}
_TAG=re.compile(r'\[AI OFFICE 요청 [0-9a-f]{32}\]')


def _db(path):
    c=sqlite3.connect(str(path),timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS desktop_chat_mirror_state (thread_id TEXT PRIMARY KEY,data TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS desktop_chat_mirror_items (source_id TEXT PRIMARY KEY,message_id TEXT NOT NULL,digest TEXT NOT NULL)')
    return c


def enable_current(db):
    """One explicitly authorized boundary; do not copy older conversations."""
    config=bridge.configuration()
    if not config or config['thread_id']!=__import__('os').environ.get('CODEX_THREAD_ID'):
        raise ValueError('현재 연결한 Codex 대화에서만 표시 연결을 설정할 수 있어요.')
    with bridge.connection(config) as client:data=client.read(3)
    turns=data.get('turns',[])
    if not turns:raise ValueError('현재 대화의 표시 시작점을 확인하지 못했어요.')
    # read_thread returns the most recent turn first.
    start=turns[0]['id']
    state={'start_turn':start,'ignored':[t['id'] for t in turns[1:]],'armed':False,'revision':0}
    with closing(_db(db)) as c,c:
        previous=c.execute('SELECT data FROM desktop_chat_mirror_state WHERE thread_id=?',(config['thread_id'],)).fetchone()
        if previous:raise ValueError('이미 설정된 표시 시작점을 임의로 다시 설정하지 않습니다.')
        c.execute('INSERT INTO desktop_chat_mirror_state VALUES (?,?)',(config['thread_id'],json.dumps(state)))
    config['mirror_enabled']=True
    temporary=bridge.CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(bridge.CONFIG)
    sync(db,data)
    return status()


def status():
    with _lock:return dict(_status)


def _activity(data):
    """Expose lifecycle/type information only, never private reasoning text."""
    native=data.get('thread',{}).get('status',{})
    kind=native.get('type') if isinstance(native,dict) else native
    turns=data.get('turns') or []
    latest=max(turns,key=lambda t:t.get('startedAt') or 0,default={})
    flags=native.get('activeFlags',[]) if isinstance(native,dict) else []
    waiting=any(str(flag).lower().replace('_','') in {'waitingonapproval','waitingonuserinput'} for flag in flags)
    active=kind=='active' or (kind not in {'idle','systemError','notLoaded'} and latest.get('status')=='inProgress')
    phase='waiting' if waiting else 'working' if active else 'idle' if kind=='idle' else 'unknown'
    if active and not waiting:
        items=latest.get('items') or []
        last=items[-1] if items else {}
        if last.get('type')=='reasoning':phase='thinking'
        elif last.get('type')=='agentMessage' and last.get('phase') in {'final','final_answer'}:phase='draft'
    labels={'working':'에이메스가 작업 중이에요','thinking':'요청을 살펴보고 있어요','draft':'답변을 작성하고 있어요',
            'waiting':'태준님의 확인을 기다리고 있어요','idle':'이야기를 기다리고 있어요','unknown':'작업 상태를 확인하고 있어요'}
    return dict(phase=phase,label=labels[phase],observed_at=time.time(),thread_id=data.get('thread',{}).get('id'))


def sync(db,data):
    config=bridge.configuration()
    if not config.get('mirror_enabled'):return
    if data.get('thread',{}).get('id')!=config['thread_id']:return
    import work_activity
    work_activity.observe(db,data)
    with _lock:_status.update(activity=_activity(data),enabled=True,available=True)
    import work_jobs
    with _lock,closing(_db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        raw=c.execute('SELECT data FROM desktop_chat_mirror_state WHERE thread_id=?',(config['thread_id'],)).fetchone()
        if not raw:return
        state=json.loads(raw[0]);ignored=set(state['ignored']);changed=False
        turns=list(reversed(data.get('turns',[])))
        for turn in turns:
            tid=turn['id']
            if tid in ignored:continue
            if not state['armed']:
                if tid!=state['start_turn']:continue
                state['armed']=True
            items=turn.get('items',[])
            relay_segment=False
            for item in items:
                # A direct desktop follow-up can share a turn with a relayed
                # request. Keep its replies while excluding the relay segment.
                incoming=bridge._input_text(item,config['thread_id'])
                if item.get('type')=='userMessage' or incoming:
                    relay_segment=bool(_TAG.match(incoming.lstrip()))
                    continue
                if item.get('type')=='agentMessage' and _TAG.match(item.get('text','').lstrip()):
                    relay_segment=True;continue
                if relay_segment:continue
                if item.get('type')!='agentMessage' or item.get('phase') not in {'commentary','final','final_answer'}:continue
                text=item.get('text','').strip()[:6000]
                if not text or not item.get('id'):continue
                source=config['thread_id']+':'+tid+':'+item['id']
                digest=hashlib.sha256(text.encode()).hexdigest()
                old=c.execute('SELECT message_id,digest FROM desktop_chat_mirror_items WHERE source_id=?',(source,)).fetchone()
                if old and old[1]==digest:continue
                mid=old[0] if old else uuid.uuid5(uuid.NAMESPACE_URL,source).hex
                if old:c.execute("UPDATE work_messages SET content=? WHERE id=? AND role='assistant'",(text,mid))
                else:
                    stamp=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
                    c.execute('INSERT INTO work_messages VALUES (?,?,?,?)',(mid,'assistant',text,stamp))
                    work_jobs.tag(c,mid,'office')
                c.execute('INSERT OR REPLACE INTO desktop_chat_mirror_items VALUES (?,?,?)',(source,mid,digest))
                state['revision']+=1;changed=True
        c.execute('UPDATE desktop_chat_mirror_state SET data=? WHERE thread_id=?',(json.dumps(state),config['thread_id']))
        _status.update(revision=state['revision'],enabled=True,available=True)
        if changed:_status['updated_at']=time.time()


def touch(db,user_message_id=None):
    """Invalidate chat views after a locally recorded user message, no inference."""
    config=bridge.configuration()
    with _lock,closing(_db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM desktop_chat_mirror_state WHERE thread_id=?',(config.get('thread_id',''),)).fetchone()
        if row:
            state=json.loads(row[0]);state['revision']+=1
            c.execute('UPDATE desktop_chat_mirror_state SET data=? WHERE thread_id=?',(json.dumps(state),config['thread_id']))
            _status.update(revision=state['revision'],updated_at=time.time())
        if user_message_id:
            _status['user_message_ids']=list(dict.fromkeys([*_status.get('user_message_ids',[]),user_message_id]))[-40:]


def announce(db, text, key):
    """Publish an authorized local code event, without invoking an AI model."""
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise ValueError('안내 문구를 확인해 주세요.')
    import work_jobs
    mid=uuid.uuid5(uuid.NAMESPACE_URL,'office-code-notice:'+key).hex
    config=bridge.configuration()
    with _lock,closing(_db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM work_messages WHERE id=?',(mid,)).fetchone():return False
        stamp=datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        c.execute('INSERT INTO work_messages VALUES (?,?,?,?)',(mid,'assistant',text,stamp))
        work_jobs.tag(c,mid,'office')
        row=c.execute('SELECT data FROM desktop_chat_mirror_state WHERE thread_id=?',(config.get('thread_id',''),)).fetchone()
        if row:
            state=json.loads(row[0]);state['revision']+=1
            c.execute('UPDATE desktop_chat_mirror_state SET data=? WHERE thread_id=?',(json.dumps(state),config['thread_id']))
            revision=state['revision']
        else:revision=_status.get('revision',0)+1
        _status.update(revision=revision,enabled=True,updated_at=time.time())
    return True


def _run(db):
    while not _stop.is_set():
        if not bridge.configuration().get('mirror_enabled'):
            _stop.wait(2);continue
        if bridge._serial.locked():
            _stop.wait(.2);continue
        try:
            with bridge.connection(reuse=True) as client:
                while not _stop.is_set() and not bridge._serial.locked():
                    sync(db,client.read(3))
                    _stop.wait(.5)
        except Exception:
            with _lock:_status.update(available=False)
            _stop.wait(3)


def start(db):
    global _worker
    with _lock:
        if _worker and _worker.is_alive():return
        _stop.clear()
        _status['enabled']=bool(bridge.configuration().get('mirror_enabled'))
        _worker=threading.Thread(target=_run,args=(db,),name='office-chat-mirror',daemon=True)
        _worker.start()


atexit.register(_stop.set)
