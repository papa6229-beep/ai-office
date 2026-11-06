"""Durable sample-admin projection of confirmed brand events, without model calls.

Never moves/sends files, creates samples, copies browser login material, or changes
supplier/logistics fields. Unknown batch bindings remain visible for reconciliation.
"""
from contextlib import closing
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import uuid

import brand_status_registry as registry

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'local_backups/brand-status'
CONFIG=PRIVATE/'sample-admin.json'
LOCK=threading.Lock()
TZ=dt.timezone(dt.timedelta(hours=9))
STATES={'작업중':'진행중','진행 중':'진행중','완료':'완료','작업예정':'대기중','작업 예정':'대기중','대기·보류':'대기중','':'대기중'}


def config():
    value=registry.read(CONFIG) if CONFIG.exists() else {}
    return value if value.get('enabled') and value.get('origin')=='https://product.bbnn.kr' else {}


def _db():
    PRIVATE.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(PRIVATE/'sample-admin-outbox.sqlite3',timeout=10)
    c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS events(
      id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS observed(batch_id TEXT PRIMARY KEY,signature TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sample_state(sample_id INTEGER PRIMARY KEY,fields TEXT NOT NULL);''')
    return c


def enqueue(event):
    cfg=config()
    if not cfg or not event.get('batch_id'):return
    if event.get('at') and float(event['at'])<cfg.get('cutoff',0):return
    with closing(_db()) as c,c:
        c.execute('INSERT OR IGNORE INTO events(id,batch_id,payload,state) VALUES(?,?,?,?)',
                  (str(event['id']),event['batch_id'],json.dumps(event,ensure_ascii=False),'pending'))


def observe(snapshot):
    """One code poll covers manual board edits too; unchanged rows do no browser work."""
    if not config():return
    for row in snapshot.get('brands',[]):
        fields=row.get('table_fields',{})
        signature=hashlib.sha256(json.dumps([row.get('work_status',''),row.get('person',''),fields.get('remarks','')],ensure_ascii=False).encode()).hexdigest()
        with closing(_db()) as c,c:
            old=c.execute('SELECT signature FROM observed WHERE batch_id=?',(row['id'],)).fetchone()
            if old and old[0]!=signature:
                # Returning to a prior state is a new change, not a duplicate
                # of an old completed transition. Observation and enqueue commit together.
                event={'id':'board:'+row['id']+':'+uuid.uuid4().hex,'batch_id':row['id'],'kind':'snapshot'}
                c.execute('INSERT OR IGNORE INTO events(id,batch_id,payload,state) VALUES(?,?,?,?)',
                          (event['id'],row['id'],json.dumps(event),'pending'))
            c.execute('INSERT INTO observed VALUES(?,?) ON CONFLICT(batch_id) DO UPDATE SET signature=excluded.signature',(row['id'],signature))


def plan(row,event,binding):
    state=row.get('work_status','')
    if state not in STATES:raise ValueError('Unknown board state')
    result={'event_id':str(event['id']),'batch_id':row['id'],'status':STATES[state],
            'worker':row.get('person') or '', 'remarks':row.get('table_fields',{}).get('remarks',''),
            'waiting':state in {'','작업예정','작업 예정'},'targets':[dict(t) for t in binding['targets']]}
    with closing(_db()) as c:
        for target in result['targets']:
            previous=c.execute('SELECT fields FROM sample_state WHERE sample_id=?',(target['id'],)).fetchone()
            if previous:target['expected']=json.loads(previous[0])
    if result['waiting']:result['worker']=''
    if event.get('at') and event.get('kind') in {'assigned','completed'}:
        day=dt.datetime.fromtimestamp(float(event['at']),TZ).date().isoformat()
        if event['kind']=='assigned' and state in {'작업중','완료','대기·보류'}:result['start_date']=day
        if event['kind']=='completed' and state=='완료':result['end_date']=day
    if event.get('initial_start'):result['start_date']=event['initial_start']
    if event.get('initial'):result['clear_waiting_dates']=True
    return result


def run(plans,*,space_id=None,page='p1',finish=True,refresh=False):
    """A failed/uncertain run resumes its original Space; reconcile before any setter."""
    import ego_workspace
    folder=PRIVATE/'sample-admin-runs'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    transport=PRIVATE/'sample-admin-session.json'
    previous=registry.read(transport) if transport.exists() else {}
    resume=space_id if space_id is not None else previous.get('space_id') if not previous.get('finished',True) else None
    configuration=dict(name='AI OFFICE sample admin '+folder.name,space_id=resume,page=page if space_id is not None else previous.get('page','p1') if resume else 'p1',
                       tab='cn_in_progress',plans=plans,result=(folder/'result.json').as_posix(),finish=finish,refresh=refresh)
    settings=folder/'input.json';registry.atomic(settings,configuration)
    entry=folder/'entry.mjs'
    entry.write_text('globalThis.brandAdminConfig='+json.dumps(settings.as_uri())+';\nawait import('+json.dumps((ROOT/'brand_admin_ego.mjs').as_uri())+');',encoding='utf-8')
    try:
        with (folder/'transport.log').open('wb') as log:
            proc=subprocess.run([ego_workspace.executable(),'nodejs','-e',"await import('"+entry.as_uri()+"')"],cwd=ROOT,
                                stdout=log,stderr=log,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        result=registry.read(folder/'result.json')
        registry.atomic(transport,{k:result[k] for k in ('space_id','page','finished')})
        if (proc.returncode or result.get('error')) and not result.get('sync_complete'):raise RuntimeError(result.get('error','Ego sample update failed'))
        return result
    except BaseException:
        if (folder/'result.json').exists():
            result=registry.read(folder/'result.json')
            registry.atomic(transport,{k:result[k] for k in ('space_id','page','finished')})
        raise


def flush(snapshot,**run_options):
    cfg=config()
    if not cfg or not LOCK.acquire(blocking=False):return
    try:
        with closing(_db()) as c:
            pending=list(c.execute("SELECT * FROM events WHERE state='pending' AND next_at<=? ORDER BY rowid LIMIT 20",(time.time(),)))
        if not pending:return
        rows={r['id']:r for r in snapshot.get('brands',[])};plans=[];unbound=[]
        for item in pending:
            row=rows.get(item['batch_id']);binding=cfg.get('bindings',{}).get(item['batch_id'])
            if not row or not binding:unbound.append(item);continue
            plans.append(plan(row,json.loads(item['payload']),binding))
        for item in unbound:
            with closing(_db()) as c,c:c.execute("UPDATE events SET state='needs_binding',error='입고 차수 연결 확인 필요' WHERE id=?",(item['id'],))
        if not plans:return
        try:
            result=run(plans,**run_options)
            for saved in result['items']:
                if saved.get('values') and saved['state'] in {'verified','unchanged'}:
                    with closing(_db()) as c,c:c.execute('INSERT INTO sample_state VALUES(?,?) ON CONFLICT(sample_id) DO UPDATE SET fields=excluded.fields',
                        (saved['sample_id'],json.dumps(saved['values'],ensure_ascii=False)))
            for item in pending:
                matches=[r for r in result['items'] if r['event_id']==item['id']]
                if not matches:continue
                states={r['state'] for r in matches}
                state='synced' if states<={'verified','unchanged'} else 'needs_binding' if 'needs_binding' in states else 'conflict' if 'conflict' in states else 'pending'
                with closing(_db()) as c,c:c.execute('UPDATE events SET state=?,attempts=attempts+1,next_at=?,error=? WHERE id=?',
                    (state,time.time()+min(300,10*2**min(item['attempts'],5)),'' if state=='synced' else ','.join(sorted(states)),item['id']))
            return result
        except Exception as error:
            for item in pending:
                with closing(_db()) as c,c:c.execute('UPDATE events SET attempts=attempts+1,next_at=?,error=? WHERE id=?',
                    (time.time()+min(300,10*2**min(item['attempts'],5)),str(error)[:160],item['id']))
    finally:LOCK.release()


def status():
    cfg=config()
    if not cfg:return {'enabled':False,'mode':'code','model_calls':0}
    with closing(_db()) as c:counts={r[0]:r[1] for r in c.execute('SELECT state,count(*) FROM events GROUP BY state')}
    return {'enabled':True,'origin':cfg['origin'],'bindings':len(cfg.get('bindings',{})),'counts':counts,'mode':'code','model_calls':0}
