"""Independent office work channels, admission and shared-resource ownership."""
from contextlib import contextmanager
from contextvars import ContextVar
import datetime
import json
import re
import sqlite3
import sys
import threading
import time

_scope=ContextVar('office_work_channel',default=None)
_guard=threading.RLock()
_active={}
_resources={}
PROVIDERS={'codex','claude','gemini','local'}
LIMIT=2


@contextmanager
def db(path):
    connection=sqlite3.connect(str(path),timeout=15)
    connection.row_factory=sqlite3.Row
    try:
        with connection:
            connection.executescript('''CREATE TABLE IF NOT EXISTS assistant_work_channels
                (request_id TEXT PRIMARY KEY,channel TEXT NOT NULL,provider TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assistant_message_channels
                (message_id TEXT PRIMARY KEY,channel TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS work_channel_requests ON assistant_work_channels(channel);''')
            yield connection
    finally:connection.close()


def current():return _scope.get()
def channel():return (current() or {}).get('channel','office')


def resolve(path,action,payload):
    if not isinstance(payload,dict):raise ValueError('업무 요청을 확인하세요.')
    new=payload.get('new_task',False)
    if type(new) is not bool:raise ValueError('새 업무 구분을 확인하세요.')
    if 'route_mode' in payload:
        if action!='command' or payload['route_mode']!='codex' or new or any(k in payload for k in ('provider','conversation_id','parent_request_id')):
            raise ValueError('이번 메시지의 처리 경로를 확인해 주세요.')
        import desktop_chat_bridge
        if not desktop_chat_bridge.configuration():raise ValueError('연결된 코덱스 대화를 먼저 확인해 주세요.')
        return {'channel':'office','provider':'codex'}
    if new:
        if action!='command' or payload.get('conversation_id'):raise ValueError('새 업무 요청을 확인하세요.')
        ident=payload.get('request_id','')
        if not re.fullmatch('[0-9a-fA-F]{32}',ident):raise ValueError('새 업무 식별자를 확인하세요.')
        provider=payload.get('provider')
        if provider is not None and provider not in PROVIDERS:raise ValueError('지원하는 업무 연결을 선택하세요.')
        return {'channel':ident,'provider':provider}
    if 'provider' in payload:raise ValueError('공급자는 새 업무를 시작할 때 선택하세요.')
    selected=payload.get('conversation_id')
    with db(path) as connection:
        if selected:
            if action!='command' or not re.fullmatch('[0-9a-fA-F]{32}',str(selected)):raise ValueError('업무 채널을 확인하세요.')
            row=connection.execute('SELECT channel,provider FROM assistant_work_channels WHERE channel=? ORDER BY rowid DESC LIMIT 1',(selected,)).fetchone()
            if not row:raise ValueError('기록된 별도 업무를 선택하세요.')
            return dict(row)
        parent=payload.get('parent_request_id')
        if action=='assistant-action':
            row=connection.execute('SELECT payload FROM assistant_pending WHERE id=?',(payload.get('pending_id'),)).fetchone()
            origin=json.loads(row[0]).get('origin_user_message_id') if row else None
            row=connection.execute('SELECT channel FROM assistant_message_channels WHERE message_id=?',(origin,)).fetchone()
            if row:
                record=connection.execute('SELECT channel,provider FROM assistant_work_channels WHERE channel=? ORDER BY rowid DESC LIMIT 1',(row[0],)).fetchone()
                if record:return dict(record)
        if parent:
            row=connection.execute('SELECT channel,provider FROM assistant_work_channels WHERE request_id=?',(parent,)).fetchone()
            if row:return dict(row)
    return {'channel':'office','provider':None}


def settings(base,provider):
    if not provider:return dict(base)
    import model_connections
    result=dict(base)
    result.update(model_connections.profiles(base)[provider])
    result['model_provider']=provider
    return result


@contextmanager
def session(path,action,payload,provider):
    selected=resolve(path,action,payload)
    selected.update(db=str(path),request_id=payload['request_id'],provider=selected.get('provider') or provider,
                    title=payload.get('message') or '후속 업무',owned={})
    key=(str(path),payload['request_id'])
    with db(path) as connection:
        prior=connection.execute('SELECT status,response FROM assistant_requests WHERE id=?',(payload['request_id'],)).fetchone()
    replay=bool(prior and prior['response'])
    with _guard:
        if not replay and key in _active:raise ValueError('이미 처리 중인 요청입니다. 작업 상태를 확인하세요.')
        if not replay and selected['channel'] in _active.values():raise ValueError('이 업무가 진행 중입니다. 추가 지시 또는 새 업무 버튼을 사용하세요.')
        if not replay and len(_active)>=LIMIT:raise ValueError('현재 두 업무가 진행 중입니다. 완료 후 새 업무를 시작하세요.')
        if not replay:_active[key]=selected['channel']
    token=_scope.set(selected)
    try:yield selected
    finally:
        if not replay:
            try:
                import work_activity
                work_activity.finish_request(path,payload['request_id'],failed=sys.exc_info()[0] is not None)
            except (OSError,ValueError,sqlite3.Error):pass
        for lock in reversed(list(selected['owned'].values())):lock.release()
        _scope.reset(token)
        with _guard:
            if not replay:_active.pop(key,None)


def bind(path,request_id,message_id=None,connection=None):
    state=current()
    if not state:return
    def write(c):
        c.execute('INSERT OR IGNORE INTO assistant_work_channels VALUES (?,?,?,?)',
            (request_id,state['channel'],state['provider'],datetime.datetime.now().astimezone().isoformat()))
        if message_id:tag(c,message_id,state['channel'])
    if connection is not None:write(connection)
    else:
        with db(path) as c:write(c)


def tag(connection,message_id,selected=None):
    connection.execute('INSERT OR REPLACE INTO assistant_message_channels VALUES (?,?)',(message_id,selected or channel()))


def history(path,boundary,limit):
    selected=channel()
    with db(path) as c:
        return [dict(row) for row in c.execute('''SELECT id,role,content FROM
            (SELECT m.rowid seq,m.* FROM work_messages m LEFT JOIN assistant_message_channels t ON t.message_id=m.id
             WHERE m.rowid<=? AND coalesce(t.channel,'office')=? ORDER BY m.rowid DESC LIMIT ?) ORDER BY seq''',
             (boundary,selected,limit))]


def later_user(connection,origin):
    connection.execute('CREATE TABLE IF NOT EXISTS assistant_message_channels (message_id TEXT PRIMARY KEY,channel TEXT NOT NULL)')
    row=connection.execute('SELECT coalesce(t.channel,\'office\') FROM work_messages m LEFT JOIN assistant_message_channels t ON t.message_id=m.id WHERE m.id=?',(origin,)).fetchone()
    if not row:return True
    return bool(connection.execute('''SELECT 1 FROM work_messages m LEFT JOIN assistant_message_channels t ON t.message_id=m.id
        WHERE m.role='user' AND m.rowid>(SELECT rowid FROM work_messages WHERE id=?) AND coalesce(t.channel,'office')=? LIMIT 1''',(origin,row[0])).fetchone())


def release(name):
    if name in {'browser','desktop'}:name='ui'
    state=current()
    if state and name in state['owned']:state['owned'].pop(name).release()


def claim(name):
    import chat_stream,task_control
    if name in {'browser','desktop'}:name='ui'
    state=current()
    if not state:return
    if name in state['owned']:return
    # A browser reservation cannot block a developer that is holding project.
    # Re-observe the browser after publishing/promoting to development.
    if name=='project':release('browser');release('desktop')
    with _guard:lock=_resources.setdefault(name,threading.Lock())
    announced=False
    wait_started=time.monotonic()
    task_control.checkpoint()
    while not lock.acquire(timeout=.2):
        task_control.checkpoint()
        if not announced:
            chat_stream.publish('working','다른 업무가 사용하는 '+{'project':'앱 소스','ui':'화면·브라우저'}.get(name,name)+'를 기다리고 있어요')
            announced=True
    try:task_control.checkpoint()
    except BaseException:lock.release();raise
    import execution_router
    execution_router.record_time('resource_wait:'+name,(time.monotonic()-wait_started)*1000)
    state['owned'][name]=lock


@contextmanager
def tool(name):
    state=current()
    if state and name!='codex_chat_relay':
        import work_activity
        work_activity.begin_request(state['db'],state['request_id'],state['title'],state['provider'])
    project=name in {'agent_source_publish','office_source_apply','office_source_rollback'}
    held=bool(current() and 'project' in current()['owned'])
    if project:claim('project')
    elif name.startswith(('browser_','ego_')) or name in {'launcher_run','launcher_open'}:claim('browser')
    elif name.startswith(('desktop_','windows_')) and name not in {'desktop_chat','desktop_inventory','desktop_analyze_status','desktop_analyze_results'}:claim('desktop')
    try:yield
    finally:
        # A native developer already holds this resource for the entire turn.
        if project and not held:release('project')


def snapshot(path):
    import document_reader
    with _guard:live_ids=[ident for (target,ident) in _active if target==str(path)]
    with db(path) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_requests'").fetchone():return {'items':[],'limit':LIMIT}
        priority=('CASE WHEN r.id IN ('+','.join('?' for _ in live_ids)+') THEN 0 ELSE 1 END,') if live_ids else ''
        rows=c.execute("""SELECT r.id,r.status,
            json_extract(r.payload,'$.payload.message') AS title,
            json_array(json_extract(r.response,'$.result.execution_state'),
                json_extract(r.response,'$.result.answer'),
                json(coalesce(json_extract(r.response,'$.result.execution'),e.data,'null'))) AS info,
            w.channel,w.provider FROM assistant_requests r
            LEFT JOIN assistant_work_channels w ON w.request_id=r.id
            LEFT JOIN assistant_execution_routes e ON e.request_id=r.id ORDER BY """+priority+'r.rowid DESC LIMIT 12',live_ids).fetchall()
        items=[]
        for row in rows:
            info=json.loads(row['info']) if row['info'] else [None,None,None]
            status=info[0] or row['status']
            if status=='running' and row['id'] not in live_ids:status='uncertain'
            items.append({'id':row['id'],'channel':row['channel'] or 'office','provider':row['provider'],
                'title':document_reader._redact(str(row['title'] or '후속 업무'))[:100],
                'status':status,'answer':str(info[1] or '')[:6000],'execution':info[2]})
    import work_activity
    activity=work_activity.snapshot(path)
    actual={r['request_id']:r for r in activity if r['request_id']}
    native={(r['host'],r['thread_id'],r['turn_id']):r for r in activity if r['control']=='native'}
    # Only explicitly submitted Office requests belong in this list/count.
    # A desktop execution can prove that request started, but is never a second
    # job or a reason to include unrelated project/development conversations.
    with db(path) as c:
        relays={}
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_chat_requests'").fetchone():
            ids=[item['id'] for item in items]
            if ids:
                relays={r[0]:json.loads(r[1]) for r in c.execute(
                    'SELECT id,data FROM desktop_chat_requests WHERE id IN ('+','.join('?' for _ in ids)+')',ids)}
    for item in items:
        relay=relays.get(item['id'],{})
        observed=native.get(('local',relay.get('thread_id'),relay.get('turn_id')), {})
        item['counts_as_work']=item['status']=='running' and bool(
            actual.get(item['id'],{}).get('counts_as_work') or observed.get('counts_as_work'))
    items.sort(key=lambda r:not r.get('counts_as_work',False))
    return {'items':items,'limit':LIMIT,'running_count':sum(bool(r.get('counts_as_work')) for r in items)}
