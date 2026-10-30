"""Code batches over existing file operations; no inference or silent retries.

Native callers supply observed entries, not guessed filesystem capabilities.
The common tool adapter resolves its usual server references before calling us.
"""
from contextlib import closing,contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import unicodedata
import uuid

import file_operations
import task_control


def _db(db):
    c=sqlite3.connect(str(db),timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS office_fastlane_runs '
              '(id TEXT PRIMARY KEY,signature TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,updated REAL NOT NULL)')
    c.commit()
    return c


def status(db,request_id):
    with closing(_db(db)) as c:
        row=c.execute('SELECT data FROM office_fastlane_runs WHERE id=?',(request_id,)).fetchone()
    return json.loads(row[0]) if row else None


def stopped(db,request_id):
    """Also support native subprocesses, which do not share ContextVars."""
    task_control.checkpoint()
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_inputs'").fetchone():
            return bool(c.execute("SELECT 1 FROM assistant_inputs WHERE request_id=? AND kind='stop' LIMIT 1",(request_id,)).fetchone())
    return False


def require_active_request(db,request_id):
    """Mutating native report runners must belong to a live human request.

    This validates lifecycle/identity only, not an LLM interpretation of intent.
    It cannot authorize a completed, stopped, or superseded request.
    """
    if stopped(db,request_id):raise task_control.Stopped()
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        row=c.execute('SELECT status FROM assistant_requests WHERE id=?',(request_id,)).fetchone()
        if row is None:
            # A live native/project/department input need not have a synthetic
            # assistant_requests row. Require that exact actual caller instead.
            import office_current_task
            live=office_current_task.require_current(request_id)
            if live.get('source') not in {'native','project_panel','legacy_project_panel','department_assignment'}:
                raise ValueError('현재 실행 중인 사용자 요청이 아니에요.')
            return
        if row[0]!='running':raise ValueError('현재 실행 중인 사용자 요청이 아니에요. 과거 업무를 재실행하지 않았어요.')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_chat_requests'").fetchone():
            saved=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(request_id,)).fetchone()
            if saved:
                record=json.loads(saved[0])
                if record.get('state') not in {'sending','sent'}:raise ValueError('종료·중지된 전달 요청은 실행하지 않아요.')
                latest=c.execute("SELECT id FROM desktop_chat_requests WHERE json_extract(data,'$.thread_id')=? "
                    "ORDER BY CAST(json_extract(data,'$.created') AS REAL) DESC,rowid DESC LIMIT 1",(record.get('thread_id'),)).fetchone()
                if latest and latest[0]!=request_id:raise ValueError('더 최신 사용자 요청이 있어 과거 실행을 시작하지 않았어요.')
    return True


@contextmanager
def native_control(db,request_id):
    if task_control.current():
        yield;return
    # The desktop request is owned by the server. A native worker mirrors only
    # its stop flag, so existing copy/hash checkpoints remain interruptible.
    done=threading.Event()
    with task_control.capture(db,request_id) as control:
        def monitor():
            while not done.wait(.25):
                try:
                    if stopped(db,request_id):control.stopped.set();return
                except sqlite3.Error:control.stopped.set();return
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        try:yield
        finally:done.set();thread.join(timeout=1)


def run_files(db,actions,title,request_id,conflict='rename',*,root_request_id=None):
    """Plan + run once. A partial/uncertain replay returns status, not effects."""
    if not isinstance(request_id,str) or len(request_id)!=32 or any(c not in '0123456789abcdef' for c in request_id):
        raise ValueError('파일 묶음 요청 ID를 확인해 주세요.')
    if not isinstance(actions,list) or not 1<=len(actions)<=200:
        raise ValueError('파일 작업을 1~200개로 묶어 주세요.')
    if any(not isinstance(row,dict) or row.get('op') not in {'mkdir','move','rename'} for row in actions):
        raise ValueError('빠른 묶음은 폴더 생성·이동·이름 변경을 처리합니다. 삭제는 기존 명시적 삭제 계획을 사용하세요.')
    signature=hashlib.sha256(json.dumps([actions,title,conflict],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    started=time.monotonic()
    data={'id':request_id,'kind':'files','state':'planning','mode':'code','model_calls':0,'job_id':None,'timings':{}}
    previous=None
    with closing(_db(db)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        prior=c.execute('SELECT signature,data FROM office_fastlane_runs WHERE id=?',(request_id,)).fetchone()
        if prior:
            if prior['signature']!=signature:raise ValueError('같은 파일 묶음 ID로 다른 작업을 실행할 수 없어요.')
            previous=json.loads(prior['data']);previous['replayed']=True
        else:c.execute('INSERT INTO office_fastlane_runs VALUES (?,?,?,?,?)',(request_id,signature,data['state'],json.dumps(data),time.time()))
    if previous is not None:
        if previous.get('job_id'):previous['job']=file_operations.summary(file_operations.load(db,previous['job_id']))
        return previous
    def save():
        data['timings']['total_ms']=round((time.monotonic()-started)*1000)
        with closing(_db(db)) as c,c:
            c.execute('UPDATE office_fastlane_runs SET state=?,data=?,updated=? WHERE id=?',
                      (data['state'],json.dumps(data,ensure_ascii=False),time.time(),request_id))
    try:
        root=root_request_id or request_id
        if stopped(db,root):raise task_control.Stopped()
        tick=time.monotonic()
        planned=file_operations.plan(db,actions,title,request_id,conflict)
        data.update(job_id=planned['id'],state='planned')
        data['timings']['plan_ms']=round((time.monotonic()-tick)*1000);save()
        if stopped(db,root):raise task_control.Stopped()
        data['state']='running';save();tick=time.monotonic()
        with native_control(db,root):result=file_operations.run(db,planned['id'])
        data['timings']['execute_ms']=round((time.monotonic()-tick)*1000)
        data.update(state=result['status'],job=result)
    except task_control.Stopped:
        data['state']='stopped'
    except Exception as error:
        data.update(state='error',error_type=type(error).__name__)
        save();raise
    save();return data


def sort_files(db,source,rules,request_id,destination=None,title='파일 규칙 분류',conflict='rename',*,root_request_id=None):
    """Classify current direct-child files by declared rules, without per-file AI."""
    if not isinstance(rules,list) or not 1<=len(rules)<=20:raise ValueError('분류 규칙을 1~20개 지정하세요.')
    if not isinstance(request_id,str) or len(request_id)!=32 or any(c not in '0123456789abcdef' for c in request_id):raise ValueError('분류 요청 ID를 확인하세요.')
    prepared=[]
    norm=lambda text:unicodedata.normalize('NFKC',text).casefold()
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)-{'folder','extensions','contains'}:raise ValueError('폴더·확장자·이름 포함 조건만 지정하세요.')
        folder=file_operations._name(rule.get('folder',''))
        extensions=rule.get('extensions',[]);contains=rule.get('contains','')
        if not isinstance(extensions,list) or len(extensions)>30 or any(not isinstance(e,str) or not e or len(e)>20 for e in extensions) or not isinstance(contains,str) or len(contains)>200:raise ValueError('확장자/이름 분류 조건을 확인하세요.')
        if not extensions and not contains:raise ValueError('분류 조건이 필요해요. 명시한 나머지 분류는 extensions에 *를 사용하세요.')
        prepared.append((folder,{norm(e.lstrip('.')) for e in extensions},norm(contains)))
    destination=destination or source
    signature=hashlib.sha256(json.dumps([source,destination,rules,title,conflict],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    data={'id':request_id,'kind':'sort','mode':'code','model_calls':0,'state':'planning','jobs':[],'classified':0,'unmatched':0,'timings':{}}
    with closing(_db(db)) as c,c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT signature,data FROM office_fastlane_runs WHERE id=?',(request_id,)).fetchone()
        if old:
            if old['signature']!=signature:raise ValueError('같은 분류 요청 ID의 내용이 달라요.')
            return dict(json.loads(old['data']),replayed=True)
        c.execute('INSERT INTO office_fastlane_runs VALUES (?,?,?,?,?)',(request_id,signature,'planning',json.dumps(data),time.time()))
    started=time.monotonic();root=root_request_id or request_id
    def save():
        data['timings']['total_ms']=round((time.monotonic()-started)*1000)
        with closing(_db(db)) as c,c:c.execute('UPDATE office_fastlane_runs SET state=?,data=?,updated=? WHERE id=?',(data['state'],json.dumps(data,ensure_ascii=False),time.time(),request_id))
    try:
        if stopped(db,root):raise task_control.Stopped()
        dest=file_operations.live(destination)
        if not dest.is_dir():raise ValueError('도착 폴더를 확인하세요.')
        listing=file_operations.browse(source,limit=30000)
        if listing['scan_limited']:raise ValueError('폴더가 커서 하위 폴더 단위로 분류해야 해요. 파일은 이동하지 않았어요.')
        assignments=[]
        for entry in listing['items']:
            if entry['metadata']['kind']!='file':continue
            path=Path(entry['path']);suffix=norm(path.suffix.lstrip('.'));name=norm(path.name)
            match=next((folder for folder,extensions,contains in prepared if (not extensions or '*' in extensions or suffix in extensions) and (not contains or contains in name)),None)
            if match is None:data['unmatched']+=1
            else:assignments.append((entry,match))
        data.update(omitted=listing['omitted'],planned_files=len(assignments),state='running')
        data['timings']['classify_ms']=round((time.monotonic()-started)*1000);save()
        for index in range(0,len(assignments),180):
            if stopped(db,root):raise task_control.Stopped()
            actions=[];parents={}
            for entry,folder in assignments[index:index+180]:
                if folder not in parents:
                    parent_id='group_'+str(len(parents));parents[folder]=parent_id
                    actions.append({'op':'mkdir','id':parent_id,'parent':file_operations.observe(dest),'name':folder})
                actions.append({'op':'move','source':entry,'parent_action':parents[folder],'name':Path(entry['path']).name})
            child=uuid.uuid5(uuid.NAMESPACE_URL,request_id+':sort:'+str(index//180)).hex
            result=run_files(db,actions,title,child,conflict,root_request_id=root)
            data['jobs'].append({'id':result.get('job_id'),'state':result['state']});save()
            if result.get('job_id'):
                actual=file_operations.load(db,result['job_id'])
                data['classified']+=sum(row['status']=='done' and row['op'] in {'move','transfer'} for row in actual['steps'])
                data['skipped_conflicts']=data.get('skipped_conflicts',0)+sum(bool(row.get('skipped')) for row in actual['actions'] if row['op']=='move')
            if result['state']!='completed':data['state']=result['state'];break
        else:data['state']='completed'
    except task_control.Stopped:data['state']='stopped'
    except Exception as error:
        data.update(state='error',error_type=type(error).__name__);save();raise
    save();return data


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Run one currently authorized Office code batch.')
    parser.add_argument('kind',choices=['files','sort','ego','status'])
    parser.add_argument('plan',help='Private UTF-8 JSON plan path; current request and explicit actions only.')
    parser.add_argument('--db',default=str(Path(__file__).resolve().parent/'office.db'))
    args=parser.parse_args();plan=json.loads(Path(args.plan).read_text('utf-8-sig'))
    if args.kind=='files':result=run_files(args.db,**plan)
    elif args.kind=='sort':result=sort_files(args.db,**plan)
    elif args.kind=='ego':
        import ego_workspace
        result=ego_workspace.run(args.db,**plan)
    else:
        result=status(args.db,plan['request_id'])
        if result is None:
            import ego_workspace
            result=ego_workspace.status(args.db,plan['request_id'])
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
