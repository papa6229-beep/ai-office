"""Evidence-based file/room/work links. No inferred identities or file effects."""
from contextlib import closing
import re
import sqlite3
import time
import uuid


def _table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS material_task_links(
        file_id TEXT NOT NULL,task_id TEXT NOT NULL,resource_id TEXT NOT NULL,
        created REAL NOT NULL,PRIMARY KEY(file_id,task_id))''')


def connections(db, row, records, moves):
    import file_history as history
    result = {'conversation': None, 'tasks': [], 'task_options': [], 'related_files': []}
    paths = {history._key(row.get(k)) for k in ('original_path', 'current_path', 'base_path') if row.get(k)}
    paths.update(history._key(e['path']) for e in row.get('path_events', []) if e.get('path'))
    with closing(sqlite3.connect(str(db), timeout=10)) as c:
        c.row_factory = sqlite3.Row
        if row.get('source') == 'hanbiro' and row.get('room') and row.get('person_origin') == 'messenger':
            if _table(c, 'notification_native_links') and _table(c, 'notification_rooms'):
                rooms = list(c.execute('''SELECT r.id,r.title,r.source FROM notification_native_links l
                    JOIN notification_rooms r ON r.id=l.room_id WHERE l.native_room=?''', (row['room'],)))
                import notification_conversations
                rooms = [r for r in rooms if notification_conversations.messenger(r['source']) == 'hanbiro']
                if len(rooms) == 1:
                    result['conversation'] = dict(rooms[0], evidence='native_room')
        linked = {}
        if _table(c, 'tasks'):
            if _table(c, 'work_resources'):
                for r in c.execute('''SELECT t.id,t.title,t.status,w.path FROM work_resources w
                        JOIN tasks t ON t.id=w.task_id'''):
                    if history._key(r['path']) in paths:
                        linked[r['id']] = {k:r[k] for k in ('id','title','status')}
                        linked[r['id']]['evidence'] = 'registered_resource'
            if _table(c, 'material_task_links'):
                for r in c.execute('''SELECT t.id,t.title,t.status FROM material_task_links m
                        JOIN tasks t ON t.id=m.task_id WHERE m.file_id=?''', (row['id'],)):
                    linked[r['id']] = dict(r, evidence='user_link')
            result['tasks'] = list(linked.values())
            result['task_options'] = [dict(r) for r in c.execute(
                'SELECT id,title,status FROM tasks ORDER BY created DESC,rowid DESC LIMIT 100')]
    for other in sorted(records, key=lambda r:r.get('timestamp',0), reverse=True):
        if other['id'] == row['id']:
            continue
        same_content = bool(row.get('sha256') and other.get('sha256') == row['sha256'] and other['size'] == row['size'])
        same_job = bool(row.get('job') and other.get('job') == row['job'])
        if not same_content and not same_job:
            continue
        public = history._public(history._current(other, moves))
        result['related_files'].append(dict(public, evidence='same_content' if same_content else 'same_organization_job'))
        if len(result['related_files']) == 12:
            break
    result['conversation_note'] = '' if result['conversation'] else '원본 대화를 하나로 확인할 연결 근거가 아직 없어요.'
    return result


def link_task(db, file_id, task_id):
    import file_history as history
    if not isinstance(task_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',task_id):
        raise ValueError('목록에 있는 업무를 선택해 주세요.')
    rows=history._all(db,lambda r:r['id']==file_id)
    if len(rows)!=1 or not rows[0]['available']:
        raise ValueError('기록한 현재 위치에서 파일을 확인하지 못했어요.')
    row=rows[0]
    with closing(sqlite3.connect(str(db),timeout=10)) as c, c:
        if not _table(c,'tasks') or not c.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone():
            raise ValueError('실제 등록된 업무를 선택해 주세요.')
        _schema(c)
        c.execute('CREATE TABLE IF NOT EXISTS work_resources (id TEXT PRIMARY KEY,task_id TEXT,name TEXT,path TEXT,kind TEXT)')
        existing=c.execute('SELECT resource_id FROM material_task_links WHERE file_id=? AND task_id=?',(file_id,task_id)).fetchone()
        resource=existing[0] if existing else None
        if not resource:
            resource=next((r[0] for r in c.execute('SELECT id,path FROM work_resources WHERE task_id=?',(task_id,))
                           if history._key(r[1])==history._key(row['current_path'])),None) or uuid.uuid4().hex
        c.execute('INSERT OR REPLACE INTO work_resources VALUES(?,?,?,?,?)',
                  (resource,task_id,row['name'],row['current_path'],'file'))
        c.execute('INSERT OR IGNORE INTO material_task_links VALUES(?,?,?,?)',(file_id,task_id,resource,time.time()))
    return dict(file_id=file_id,task_id=task_id,resource_id=resource,mode='code',model_calls=0,
                message='선택한 파일을 해당 업무에 연결했어요. 파일은 이동하지 않았어요.')
