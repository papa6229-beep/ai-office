"""Common execution lifecycle. No inference or extra native polling."""
from contextlib import contextmanager
import json
import sqlite3
import time
import uuid

TOOLS={'commandExecution','fileChange','mcpToolCall','webSearch','computerUse','imageGeneration','toolCall'}
TERMINAL={'completed':'done','failed':'error','interrupted':'cancelled'}


@contextmanager
def _db(path):
    c=sqlite3.connect(str(path),timeout=5);c.row_factory=sqlite3.Row
    try:
        with c:
            c.execute('''CREATE TABLE IF NOT EXISTS work_activity (
                id TEXT PRIMARY KEY,source TEXT NOT NULL,external_id TEXT NOT NULL,
                thread_id TEXT,host TEXT,title TEXT NOT NULL,provider TEXT,
                state TEXT NOT NULL,started REAL NOT NULL,updated REAL NOT NULL,
                ended REAL,UNIQUE(source,external_id))''')
            yield c
    finally:c.close()


def _begin(c,source,external,title,provider,thread=None,host='local'):
    import document_reader
    ident=uuid.uuid5(uuid.NAMESPACE_URL,'office-work:'+source+':'+external).hex
    now=time.time()
    title=document_reader._redact(str(title or '진행 중인 업무'))[:100]
    c.execute('''INSERT OR IGNORE INTO work_activity VALUES (?,?,?,?,?,?,?,?,?,?,NULL)''',
        (ident,source,external,thread,host,title,provider,'running',now,now))
    c.execute("UPDATE work_activity SET updated=? WHERE id=? AND state='running' AND updated<?",(now,ident,now-5))
    return ident


def begin_request(path,request,title,provider):
    with _db(path) as c:return _begin(c,'office',request,title,provider)


def finish_request(path,request,failed=False):
    with _db(path) as c:
        if not c.execute("SELECT 1 FROM work_activity WHERE source='office' AND external_id=? AND state='running'",(request,)).fetchone():return
        row=c.execute('SELECT status,response FROM assistant_requests WHERE id=?',(request,)).fetchone()
        result=json.loads(row['response'] or '{}').get('result',{}) if row else {}
        state=result.get('execution_state') or ('error' if failed else 'done')
        if state not in {'done','error','cancelled','waiting'}:state='error' if failed else 'done'
        c.execute("UPDATE work_activity SET state=?,updated=?,ended=? WHERE source='office' AND external_id=? AND state='running'",
                  (state,time.time(),time.time(),request))


def observe(path,data,host='local'):
    thread=data.get('thread',{});thread_id=thread.get('id')
    if not thread_id or thread.get('kind','codex')!='codex':return
    turns=data.get('turns') or []
    latest=max(turns,key=lambda t:t.get('startedAt') or 0,default={})
    with _db(path) as c:
        for turn in turns:
            key=host+'|'+thread_id+'|'+str(turn.get('id',''))
            status=turn.get('status')
            if turn is latest and status=='inProgress' and any(i.get('type') in TOOLS for i in turn.get('items',[])):
                _begin(c,'native',key,thread.get('title') or '연결된 작업', 'codex',thread_id,host)
            if status in TERMINAL:
                c.execute("UPDATE work_activity SET state=?,updated=?,ended=? WHERE source='native' AND external_id=? AND state='running'",
                          (TERMINAL[status],time.time(),time.time(),key))


def snapshot(path):
    now=time.time()
    with _db(path) as c:
        rows=c.execute("SELECT * FROM work_activity ORDER BY CASE WHEN state='running' THEN 0 ELSE 1 END,updated DESC LIMIT 40").fetchall()
    items=[]
    for r in rows:
        status=r['state']
        # A lost observer is not proof of continued work or successful completion.
        if r['source']=='native' and status=='running' and now-r['updated']>30:status='uncertain'
        items.append({'id':r['id'],'request_id':r['external_id'] if r['source']=='office' else None,
            'channel':'native' if r['source']=='native' else 'office','provider':r['provider'],
            'title':r['title'],'status':status,'counts_as_work':status=='running',
            'control':'native' if r['source']=='native' else 'office','thread_id':r['thread_id'],
            'turn_id':r['external_id'].rsplit('|',1)[-1] if r['source']=='native' else None,
            'host':r['host'],'answer':'','execution':{'mode':'desktop' if r['source']=='native' else 'model'}})
    return items
