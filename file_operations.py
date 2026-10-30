"""Shared, explicitly planned file/folder operations; private resumable receipts."""
from contextlib import contextmanager
import ctypes
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import uuid

import chat_stream
import desktop_workspace
import file_executor as old
import file_transfer as transfer
import file_workspace
import task_control

ROOT=Path(__file__).absolute().parent
LOCK=threading.Lock()
MAX_STEPS=30000


def _within(path,root):return old._inside(Path(path),Path(root))


def _protected(path,source=False):
    path=Path(path)
    protected=[ROOT,Path.home()/'.codex',Path.home()/'.ssh',Path.home()/'.aws',Path.home()/'AppData']
    protected += [Path(os.environ[key]) for key in ('WINDIR','ProgramFiles','ProgramFiles(x86)','ProgramData') if os.environ.get(key)]
    return any(_within(path,p) or (source and _within(p,path)) for p in protected)


def _guard(path,source=False):
    path=old._safe_path(Path(path))
    if _protected(path,source) or (source and path==Path(path.anchor)):raise ValueError('앱·운영체제·계정 저장소 또는 드라이브 전체는 이동·삭제하지 않습니다.')
    if source and any(part.casefold().startswith('.aemeath-') for part in path.parts):raise ValueError('실행기 보관·임시 자료는 원래 작업의 되돌리기로 관리하세요.')
    return path


def _name(value):
    if (not isinstance(value,str) or not value or value in {'.','..'} or len(value.encode('utf-16-le'))>480
        or value!=value.rstrip(' .') or any(ord(c)<32 or c in '<>:"/\\|?*' for c in value)
        or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',value)):
        raise ValueError('이름은 경로가 아닌 유효한 파일·폴더 이름 하나로 지정하세요.')
    return value


def observe(path):
    path=old._safe_path(Path(path));meta=transfer.metadata(path)
    return {'path':str(path),'metadata':meta}


def live(entry,source=False):
    path=_guard(entry['path'],source)
    if not transfer.same_node(path,entry['metadata'],contents=True):raise ValueError('조회 뒤 파일·폴더가 바뀌었습니다. 다시 목록을 읽어 주세요.')
    return path


def locations():
    rows=[]
    for path in desktop_workspace._desktop_roots():
        try:rows.append(dict(observe(path),label='바탕화면'))
        except (OSError,ValueError):pass
    for path,label in file_workspace._standard_roots():
        try:rows.append(dict(observe(path),label=label))
        except (OSError,ValueError):pass
    if os.name=='nt':
        dll=ctypes.WinDLL('kernel32',use_last_error=True)
        dll.GetDriveTypeW.argtypes=[ctypes.c_wchar_p];dll.GetDriveTypeW.restype=ctypes.c_uint
        for index in range(26):
            path=Path(chr(65+index)+':\\')
            if dll.GetDriveTypeW(str(path)) in {2,3}:
                try:rows.append(dict(observe(path),label=str(path)+' 드라이브'))
                except (OSError,ValueError):pass
    return list({os.path.normcase(row['path']):row for row in rows}.values())


def browse(entry,query='',offset=0,limit=40):
    path=live(entry)
    if not path.is_dir():raise ValueError('폴더를 선택해 주세요.')
    query=unicodedata.normalize('NFKC',query).casefold();rows=[];omitted=0;limited=False
    with os.scandir(path) as entries:
        for index,item in enumerate(entries):
            if index>=30000:limited=True;break
            if query and query not in unicodedata.normalize('NFKC',item.name).casefold():continue
            if item.name.startswith('.aemeath-'):omitted+=1;continue
            try:
                node=observe(item.path)
                if _protected(item.path) or item.stat(follow_symlinks=False).st_file_attributes & (2|4):omitted+=1;continue
                rows.append(node)
            except (OSError,ValueError,AttributeError):omitted+=1
    rows.sort(key=lambda r:(r['metadata']['kind']!='folder',Path(r['path']).name.casefold()))
    return {'directory':entry,'items':rows[offset:offset+limit],'total':len(rows),'offset':offset,
            'next_offset':offset+limit if offset+limit<len(rows) else None,'scan_limited':limited,'omitted':omitted}


@contextmanager
def _db(db):
    c=sqlite3.connect(str(db),timeout=10);c.row_factory=sqlite3.Row
    try:
        with c:
            c.execute('CREATE TABLE IF NOT EXISTS file_operation_jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS file_operation_steps(job_id TEXT NOT NULL,position INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(job_id,position))')
            yield c
    finally:c.close()


def _save(db,job,index=None):
    stamp=datetime.datetime.now().astimezone().isoformat()
    with _db(db) as c:
        if index is not None:
            row=job['steps'][index]
            c.execute('UPDATE file_operation_steps SET status=?,payload=? WHERE job_id=? AND position=?',(row['status'],json.dumps(row,ensure_ascii=False),job['id'],index))
            return
        exists=c.execute('SELECT 1 FROM file_operation_jobs WHERE id=?',(job['id'],)).fetchone()
        header={k:v for k,v in job.items() if k!='steps'}
        c.execute('INSERT INTO file_operation_jobs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',
            (job['id'],json.dumps(header,ensure_ascii=False),job['created'],stamp))
        if not exists:c.executemany('INSERT INTO file_operation_steps VALUES(?,?,?,?)',[(job['id'],i,row['status'],json.dumps(row,ensure_ascii=False)) for i,row in enumerate(job['steps'])])


def load(db,ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('정리 작업을 다시 선택하세요.')
    with _db(db) as c:
        row=c.execute('SELECT payload FROM file_operation_jobs WHERE id=?',(ident,)).fetchone()
        if row is None:raise ValueError('정리 작업을 찾지 못했습니다.')
        result=json.loads(row['payload']);result['steps']=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM file_operation_steps WHERE job_id=? ORDER BY position',(ident,))]
    return result


def summary(job):
    counts={state:sum(row['status']==state for row in job['steps']) for state in ('planned','running','done','error','undone')}
    actions=[{k:(v if not isinstance(v,str) or len(v)<=300 else v[:100]+'…'+v[-190:]) for k,v in row.items()} for row in job['actions'][:10]]
    deleted=[row for row in job['actions'] if row['op']=='delete']
    permanent=sum(bool(row.get('permanent')) for row in deleted)
    return {'id':job['id'],'title':job['title'],'status':job['status'],'direction':job.get('direction','forward'),
            'created':job['created'],'request_id':job['request_id'],'steps':len(job['steps']),'counts':counts,
            'reason':job.get('reason',''),'actions':actions,'actions_truncated':len(job['actions'])>10,
            'path_display':'장문 경로는 화면 표시만 줄임. 실제 실행 경로는 비공개 작업 기록에 보존.',
            'deletion':{'recoverable_actions':len(deleted)-permanent,'permanent_actions':permanent,
                        'undo_supported':not bool(permanent),
                        'notice':'영구 삭제는 복구할 수 없습니다.' if permanent else '일반 삭제는 Windows 휴지통이 아닌 원본 드라이브의 앱 복구 보관함으로 이동합니다. files_job_undo로 복원할 수 있습니다.' if deleted else ''}}


def jobs(db,offset=0,limit=15):
    items=[]
    with _db(db) as c:
        rows=c.execute('SELECT payload FROM file_operation_jobs ORDER BY created DESC LIMIT ? OFFSET ?',(limit+1,offset)).fetchall()
        for r in rows[:limit]:
            header=json.loads(r['payload']);item=summary(dict(header,steps=[]))
            # Lists stay small even when many large plans are retained.
            item.pop('actions',None);item.pop('actions_truncated',None);item.pop('path_display',None)
            item['actions_count']=len(header['actions'])
            counts=dict(c.execute('SELECT status,count(*) FROM file_operation_steps WHERE job_id=? GROUP BY status',(header['id'],)).fetchall())
            item.update(counts=counts,steps=sum(counts.values()));items.append(item)
    return {'items':items,'next_offset':offset+limit if len(rows)>limit else None}


def status(db,ident,offset=0,limit=20):
    job=load(db,ident)
    result=summary(job);result['items']=[{k:(v if not isinstance(v,str) or len(v)<=300 else v[:100]+'…'+v[-190:]) for k,v in row.items() if k in {'op','source','destination','status','error','permanent','delete_intent'}} for row in job['steps'][offset:offset+limit]]
    result['next_offset']=offset+limit if offset+limit<len(job['steps']) else None
    return result


def _nearest(path):
    path=Path(path)
    while not path.exists():
        if path.parent==path:raise ValueError('도착 드라이브가 연결되어 있지 않습니다.')
        path=path.parent
    return path


def _walk_error(error):raise error


def _delete_tree(source):
    """Bounded metadata only; do not traverse links or silently skip children."""
    source=_guard(source,True);directories=[];files=[]
    if source.is_file():return [],[(source,transfer.metadata(source))]
    for current,dirs,names in os.walk(source,followlinks=False,onerror=_walk_error):
        task_control.checkpoint();current=_guard(current,True)
        directories.append((current,transfer.metadata(current)))
        for child in dirs:
            task_control.checkpoint();transfer.metadata(_guard(current/child,True))
        for child in names:
            task_control.checkpoint();path=_guard(current/child,True)
            files.append((path,transfer.metadata(path)))
            if len(files)+len(directories)>MAX_STEPS:raise ValueError('삭제 대상이 커서 하위 폴더 단위로 나누어 주세요. 아직 삭제하지 않았습니다.')
        if len(files)+len(directories)+len(dirs)>MAX_STEPS:raise ValueError('삭제 대상이 커서 하위 폴더 단위로 나누어 주세요. 아직 삭제하지 않았습니다.')
    return directories,files


def plan(db,actions,title,request_id,conflict='rename'):
    if not actions or len(actions)>200:raise ValueError('한 계획에 1~200개의 명확한 작업을 지정해 주세요.')
    if any(a.get('permanent') for a in actions) and not all(a.get('op')=='delete' and a.get('permanent') is True for a in actions):
        raise ValueError('영구 삭제는 복구 가능한 다른 작업과 별도 계획으로 기록하세요.')
    job={'id':uuid.uuid4().hex,'created':datetime.datetime.now().astimezone().isoformat(),'request_id':request_id,
         'title':title[:100] or '파일·폴더 정리','status':'planned','direction':'forward','steps':[],'actions':[]}
    folders={};reserved=set();sources=[];anchor_map={};cross_bytes={};holds=set()
    def anchor(path):
        current=_nearest(path);_guard(current)
        anchor_map[str(current)]=transfer.metadata(current)
    def add(op,src=None,dst=None,expected=None):
        if len(job['steps'])>=MAX_STEPS:raise ValueError('폴더가 커서 계획 범위를 나누어야 합니다. 아직 파일을 변경하지 않았습니다.')
        row={'op':op,'source':str(src) if src else None,'destination':str(dst) if dst else None,'expected':expected,'status':'planned'}
        job['steps'].append(row)
    for index,action in enumerate(actions):
        op=action['op'];ident=action.get('id') or 'action_'+str(index+1)
        if ident in folders:raise ValueError('계획의 폴더 ID가 중복됩니다.')
        if op not in {'mkdir','move','rename','delete'}:raise ValueError('지원하는 파일 작업이 아닙니다.')
        if 'permanent' in action and (op!='delete' or not isinstance(action['permanent'],bool)):raise ValueError('permanent는 삭제 작업의 참/거짓 값으로만 지정하세요.')
        source=live(action['source'],True) if op!='mkdir' else None
        expected=transfer.metadata(source) if source else None
        if op=='delete':
            if any(action.get(key) for key in ('parent','parent_action','name','id')):raise ValueError('삭제에는 source_ref와 선택적인 permanent만 지정하세요.')
            if any(_within(source,p) or _within(p,source) for p in sources):raise ValueError('상위·하위 또는 같은 대상을 중복 이동·삭제하지 마세요.')
            directories,files=_delete_tree(source);sources.append(source);anchor(source.parent)
            permanent=action.get('permanent',False)
            if permanent:
                job['actions'].append({'op':'delete','source':str(source),'destination':None,'permanent':True})
                for path,meta in files:
                    add('unlink',path,expected=meta);job['steps'][-1]['permanent']=True
                for path,meta in reversed(directories):
                    add('rmdir',path,expected=meta);job['steps'][-1]['permanent']=True
            else:
                hold=source.parent/('.aemeath-deleted-'+job['id']);destination=hold/source.name
                if hold not in holds:
                    if hold.exists():raise ValueError('복구 보관 위치가 이미 있습니다. 새 계획을 만들어 주세요.')
                    add('mkdir',dst=hold);holds.add(hold)
                job['actions'].append({'op':'delete','source':str(source),'destination':str(destination),'permanent':False})
                add('delete',source,destination,expected)
            continue
        if op=='rename':
            if action.get('parent') or action.get('parent_action'):raise ValueError('이름 변경에는 도착 폴더를 지정하지 마세요.')
            parent=source.parent
        else:
            if bool(action.get('parent'))==bool(action.get('parent_action')):raise ValueError('도착 폴더는 실제 ref 또는 앞서 만드는 폴더 ID 하나로 지정하세요.')
            if action.get('parent_action'):
                if action['parent_action'] not in folders:raise ValueError('앞서 계획한 폴더 ID를 사용하세요.')
                parent=folders[action['parent_action']]
            else:parent=live(action['parent'])
            if parent.exists() and not parent.is_dir():raise ValueError('도착 위치는 폴더여야 합니다.')
        anchor(parent)
        name=_name(action.get('name') or (source.name if source else ''))
        destination=parent/name;canonical=os.path.normcase(str(destination))
        if _protected(destination):raise ValueError('도착 위치가 앱·운영체제·계정 저장소에 해당합니다.')
        if source and os.path.normcase(str(source))==canonical:raise ValueError('출발과 도착이 같습니다. 다른 이름이나 폴더를 지정하세요.')
        reuse=op=='mkdir' and destination.is_dir() and canonical not in reserved
        if not reuse and (destination.exists() or canonical in reserved):
            if conflict=='skip':
                job['actions'].append({'op':op,'source':str(source) if source else None,'destination':str(destination),'skipped':'이름 충돌'});continue
            if conflict=='error':raise ValueError('같은 이름이 있습니다. 덮어쓰지 않았습니다.')
            stem,suffix=(Path(name).stem,Path(name).suffix) if source and source.is_file() else (name,'')
            for number in range(2,10002):
                candidate=parent/_name(stem+' ('+str(number)+')'+suffix)
                if not candidate.exists() and os.path.normcase(str(candidate)) not in reserved:destination=candidate;break
            else:raise ValueError('빈 도착 이름을 찾지 못했습니다.')
        reserved.add(os.path.normcase(str(destination)))
        job['actions'].append({'op':op,'source':str(source) if source else None,'destination':str(destination),'reuse':reuse})
        if op=='mkdir':
            if reuse:add('keepdir',destination,expected=transfer.metadata(destination))
            else:add('mkdir',dst=destination)
            folders[ident]=destination;continue
        _guard(source,True)
        if source.is_dir() and _within(destination,source):raise ValueError('폴더를 자기 안으로 옮길 수 없습니다.')
        if any(_within(source,old_source) or _within(old_source,source) for old_source in sources):raise ValueError('상위·하위 폴더를 한꺼번에 중복 이동·삭제하지 마세요.')
        sources.append(source);anchor(source.parent)
        if expected['dev']==transfer.metadata(_nearest(parent))['dev']:
            add('move',source,destination,expected);continue
        if expected['kind']=='file':
            transfer.plain_copy_source(source)
            add('transfer',source,destination,expected);cross_bytes[str(_nearest(parent))]=cross_bytes.get(str(_nearest(parent)),0)+expected['size'];continue
        entries=[];directories=[];count=0
        for current,dirs,files in os.walk(source,followlinks=False,onerror=_walk_error):
            task_control.checkpoint();current=Path(current);meta=transfer.metadata(current);directories.append((current,meta))
            for child in dirs:transfer.metadata(current/child)
            for child in files:
                task_control.checkpoint()
                item=current/child;meta=transfer.metadata(item);transfer.plain_copy_source(item);entries.append((item,meta));count+=1
                if count+len(directories)>MAX_STEPS//2:raise ValueError('이 폴더는 하위 폴더 단위로 나누어 정리해 주세요. 아직 이동하지 않았습니다.')
        for directory,_ in directories:add('mkdir',dst=destination/directory.relative_to(source))
        for file,meta in entries:add('transfer',file,destination/file.relative_to(source),meta)
        for directory,meta in reversed(directories):add('rmdir',directory,expected=meta)
        cross_bytes[str(_nearest(parent))]=cross_bytes.get(str(_nearest(parent)),0)+sum(meta['size'] for _,meta in entries)
    for action in job['actions']:
        if action.get('skipped'):continue
        if action.get('destination') and any(_within(action['destination'],source) for source in sources):raise ValueError('도착 폴더가 다른 이동·삭제 대상 안에 있습니다. 작업을 나누어 주세요.')
    for path,size in cross_bytes.items():
        if shutil.disk_usage(path).free<size:raise ValueError('도착 드라이브의 여유 공간이 부족합니다.')
    job['anchors']=anchor_map
    for index,row in enumerate(job['steps']):
        if row['op']=='transfer':row['state']={'temporary':str(Path(row['destination']).parent/('.aemeath-'+job['id']+'-'+str(index)+'.partial'))}
    _save(db,job);return summary(job)


@contextmanager
def _lock():
    waiting=False
    while not LOCK.acquire(timeout=.2):
        task_control.checkpoint()
        if not waiting:chat_stream.publish('working','다른 파일 실행이 끝날 때까지 기다리고 있어요');waiting=True
    stream=None
    try:
        import msvcrt
        folder=ROOT/'.provider_runtime'/'file-operations';folder.mkdir(parents=True,exist_ok=True);old._safe_path(folder)
        stream=(folder/'execution.lock').open('a+b');stream.seek(0)
        if not stream.read(1):stream.write(b'0');stream.flush()
        stream.seek(0)
        try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise ValueError('다른 프로세스에서 파일 정리 중입니다.') from None
        yield
    finally:
        if stream:stream.close()
        LOCK.release()


def _check_anchors(job):
    for path,expected in job['anchors'].items():
        _guard(path)
        if not transfer.same_node(path,expected):raise ValueError('계획 이후 기준 폴더가 바뀌었습니다. 다시 계획해 주세요.')


def _forward(row,save,progress):
    source=Path(row['source']) if row.get('source') else None;destination=Path(row['destination']) if row.get('destination') else None
    if row['op']=='keepdir':
        if not transfer.same_node(source,row['expected']):raise ValueError('재사용할 폴더가 바뀌었습니다.')
    elif row['op']=='mkdir':
        _guard(destination.parent)
        if destination.exists():
            if not row.get('post') or not transfer.same_node(destination,row['post']):raise ValueError('도착 폴더가 먼저 생겨서 덮어쓰지 않았습니다.')
        else:
            with old._directory_guards((destination.parent,)):destination.mkdir();row['post']=transfer.metadata(destination);save()
    elif row['op']=='rmdir':
        if source.exists():
            if row.get('permanent'):_guard(source,True)
            if not transfer.same_node(source,row['expected']):raise ValueError('원본 폴더 신원이 바뀌었습니다.')
            row['delete_intent']=True;save();transfer.remove_empty(source,row['expected'])
        elif not row.get('delete_intent'):raise ValueError('원본 폴더가 없어졌습니다.')
        if source.exists():raise ValueError('폴더 삭제가 아직 완료되지 않았습니다. 사용 중인 항목을 확인하세요.')
    elif row['op']=='unlink':
        if source.exists():
            _guard(source,True)
            with old._directory_guards((source.parent,)),transfer.pinned(source,row['expected']) as (dll,handle,_):
                row['delete_intent']=True;save();task_control.checkpoint();transfer.delete_handle(dll,handle)
        elif not row.get('delete_intent'):raise ValueError('삭제할 파일이 없어졌습니다. 임의로 완료 처리하지 않았습니다.')
        if source.exists():raise ValueError('파일 삭제가 아직 완료되지 않았습니다. 사용 중인 항목을 확인하세요.')
    elif row['op'] in {'move','delete'}:
        if transfer.same_node(destination,row['expected']) and not transfer.same_node(source,row['expected']):row['post']=transfer.metadata(destination)
        else:
            if row['op']=='delete':_delete_tree(source)
            row['post']=transfer.move_same(source,destination,row['expected'])
    elif row['op']=='transfer':
        if not row['state'].get('published') and not transfer.same_node(source,row['expected'],True):raise ValueError('원본이 바뀌었습니다.')
        row['post']=transfer.transfer(source,destination,row['expected'],row['state'],save,progress)
    else:raise ValueError('알 수 없는 파일 작업은 실행하지 않습니다.')


def _undo(row,save,progress):
    source=Path(row['source']) if row.get('source') else None;destination=Path(row['destination']) if row.get('destination') else None
    if row.get('permanent'):raise ValueError('영구 삭제는 되돌릴 수 없습니다.')
    if row['op']=='keepdir':return
    if row['op']=='mkdir':
        if row.get('post') and destination.exists():transfer.remove_empty(destination,row['post'])
        return
    if row['op']=='rmdir':
        if transfer.same_node(source,row['expected']):return
        if not source.exists():
            with old._directory_guards((source.parent,)):source.mkdir();row['undo_post']=transfer.metadata(source);save()
        elif not row.get('undo_post') or not transfer.same_node(source,row['undo_post']):raise ValueError('원래 폴더 위치에 다른 항목이 생겼습니다.')
        return
    if row['op'] in {'move','delete'}:
        if transfer.same_node(source,row['expected']) and not transfer.same_node(destination,row['expected']):return
        expected=row.get('post') or row['expected']
        if not transfer.same_node(destination,expected):raise ValueError('이동했던 대상의 신원이 바뀌었습니다.')
        transfer.move_same(destination,source,transfer.metadata(destination));return
    if row['op']!='transfer':raise ValueError('알 수 없는 파일 작업은 되돌리지 않습니다.')
    state=row['state']
    if row.get('undo_state'):
        row['undo_post']=transfer.transfer(destination,source,row['undo_expected'],row['undo_state'],save,progress);return
    if not state.get('published'):
        temporary=Path(state['temporary'])
        if temporary.exists():
            if not state.get('temporary_identity') or not transfer.same_node(temporary,state['temporary_identity']):raise ValueError('임시 파일 소유를 확인하지 못했습니다.')
            transfer.remove_owned(temporary,transfer.metadata(temporary))
        return
    if source.exists():
        if not transfer.same_node(source,row['expected'],True):raise ValueError('원래 위치에 다른 파일이 있습니다.')
        if not destination.exists() and state.get('phase')=='publishing':
            temporary=Path(state['temporary'])
            if temporary.exists():
                with transfer.pinned(source,row['expected'],deleting=False) as (_,_,src),transfer.pinned(temporary,state['published']) as (dll,handle,temp):
                    if transfer.digest(src)!=state['sha256'] or transfer.digest(temp)!=state['sha256']:raise ValueError('중단된 임시 복사본이 바뀌었습니다.')
                    transfer.delete_handle(dll,handle)
            return
        # The source survived a stopped copy; remove only the unchanged owned duplicate.
        with transfer.pinned(source,row['expected'],deleting=False) as (_,_,src),transfer.pinned(destination,state['published']) as (dll,handle,dest):
            transfer.plain_copy_source(destination)
            if transfer.digest(src)!=state['sha256'] or transfer.digest(dest)!=state['sha256']:raise ValueError('중단된 복사본이 바뀌었습니다.')
            transfer.delete_handle(dll,handle)
        return
    expected=row.get('post') or state['published']
    if not transfer.same_node(destination,expected,True):raise ValueError('복사 이동한 파일이 바뀌어 자동 복구하지 않았습니다.')
    undo_state=row.setdefault('undo_state',{'temporary':str(source.parent/('.aemeath-undo-'+uuid.uuid4().hex+'.partial'))})
    row['undo_expected']=transfer.metadata(destination);save()
    row['undo_post']=transfer.transfer(destination,source,row['undo_expected'],undo_state,save,progress)


def run(db,ident,undo=False):
    with _lock():
        job=load(db,ident)
        if job['status']=='undone':return summary(job)
        if undo and any(row.get('permanent') and row.get('delete_intent') for row in job['steps']):
            raise ValueError('영구 삭제가 시작된 작업은 되돌릴 수 없습니다. 항목별 기록에서 실제 삭제·실패 상태를 확인하세요.')
        if not undo and job.get('direction')=='undo':raise ValueError('복구 중인 작업은 되돌리기를 이어서 실행하세요.')
        if not undo and job['status']=='completed':return summary(job)
        job.update(status='running',direction='undo' if undo else 'forward',reason='');_save(db,job)
        last=[0.0];active=[None];parents=dict(job['anchors'])
        for row in job['steps']:
            if row['op']=='rmdir':parents[row['source']]=row.get('undo_post') or row['expected']
            if row['op']=='mkdir' and row.get('post'):parents[row['destination']]=row['post']
        def save():
            if active[0] is not None:
                current=job['steps'][active[0]]
                if current['op']=='mkdir' and current.get('post'):parents[current['destination']]=current['post']
                if current['op']=='rmdir' and current.get('undo_post'):parents[current['source']]=current['undo_post']
            _save(db,job,active[0])
        def progress(done,total):
            if time.monotonic()-last[0]>1:
                last[0]=time.monotonic();chat_stream.publish('working','파일 복사·검증 중 '+str(done//(1024*1024))+' / '+str(total//(1024*1024))+' MB')
        indices=range(len(job['steps'])-1,-1,-1) if undo else range(len(job['steps']))
        try:
            _check_anchors(job)
            for index in indices:
                row=job['steps'][index]
                if (undo and row['status'] in {'planned','undone'}) or (not undo and row['status']=='done'):continue
                active[0]=index
                task_control.checkpoint();_check_anchors(job)
                for path in (row.get('source'),row.get('destination')):
                    if not path:continue
                    parent=str(Path(path).parent)
                    if parent not in parents or not transfer.same_node(parent,parents[parent]):raise ValueError('작업 사이에 상위 폴더가 바뀌었습니다. 현재 위치를 다시 확인하세요.')
                row.update(status='running',started=True);row.pop('error',None);save()
                chat_stream.publish('working',('되돌리는 중 ' if undo else '파일 정리 중 ')+str(index+1)+' / '+str(len(job['steps'])))
                try:
                    if undo and row.get('permanent'):pass  # No delete intent: nothing to restore.
                    elif undo:_undo(row,save,progress)
                    else:_forward(row,save,progress)
                    row['status']='undone' if undo else 'done'
                    if not undo:row['completed_at']=time.time()
                    save()
                except (OSError,ValueError) as error:
                    row.update(status='error',error=str(error)[:300]);job.update(status='undo_partial' if undo else 'partial',reason=row['error']);save();break
            else:job['status']='undone' if undo else 'completed'
        except task_control.Stopped:
            job.update(status='undo_stopped' if undo else 'stopped',reason='사용자가 중지했습니다. 이미 실행된 항목은 기록에 남아 있습니다.')
        except (OSError,ValueError) as error:
            job.update(status='undo_partial' if undo else 'partial',reason=str(error)[:300])
            if active[0] is not None:
                job['steps'][active[0]].update(status='error',error=job['reason']);save()
        except Exception:
            job.update(status='undo_partial' if undo else 'partial',reason='실행 결과를 확정하지 못했습니다. 기록을 확인한 뒤 재개해 주세요.')
        _save(db,job);return summary(job)
