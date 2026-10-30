"""Code-only file receipt projection shared by workspace, chat, and common tools.

Original transfer/organizer logs remain authoritative. No messenger send occurs
here. User-supplied names are annotations, never evidence of native identity.
"""
from contextlib import closing
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

import file_operations as ops
import file_transfer as transfer

ROOT=Path(__file__).resolve().parent
LOCK=threading.RLock()
_metadata_at=0.0
_metadata_refs=()
_metadata={}
_metadata_loading=False


def _db(db):
    c=sqlite3.connect(str(db),timeout=15);c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS file_history_records '
              '(id TEXT PRIMARY KEY,direction TEXT NOT NULL,data TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS file_history_people '
              '(id TEXT PRIMARY KEY,person TEXT NOT NULL,noted REAL NOT NULL)')
    c.commit();return c


def _rows(c,table):
    if not c.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone():return []
    return [dict(r) for r in c.execute('SELECT * FROM '+table)]


def _epoch(value):
    try:return float(value)/(1000 if float(value)>10**11 else 1)
    except (TypeError,ValueError):
        try:return datetime.datetime.fromisoformat(value).timestamp()
        except (TypeError,ValueError):return 0


def _key(path):return os.path.normcase(os.path.abspath(str(path))) if path else ''


def _attachment_metadata(refs,known):
    global _metadata_loading
    refs=tuple(sorted(set(refs)))
    if not refs:return {}
    if refs==_metadata_refs and time.monotonic()-_metadata_at<120:return _metadata
    # Cache enrichment must never hold the file-history/SQLite lock while the
    # messenger reader starts or waits on its app. Existing verified names stay.
    if not _metadata_loading:
        _metadata_loading=True
        def refresh():
            global _metadata_at,_metadata_refs,_metadata,_metadata_loading
            fresh=None
            try:
                import messenger_attachments
                result=messenger_attachments._call(dict(action='history_metadata',refs=list(refs)))
                fresh={r['ref']:r for r in result['items']}
            except (ValueError,OSError):fresh=None
            finally:
                with LOCK:
                    if fresh is not None:_metadata=fresh
                    _metadata_at=time.monotonic();_metadata_refs=refs;_metadata_loading=False
        threading.Thread(target=refresh,daemon=True,name='file-history-metadata').start()
    fallback={r['ref']:r for r in known.values() if r.get('ref') and r.get('person_origin')=='messenger'}
    return dict(fallback,**_metadata)


def _sync(c):
    old={r['id']:json.loads(r['data']) for r in c.execute('SELECT id,data FROM file_history_records')}
    downloads={}
    for row in _rows(c,'messenger_downloads'):
        if row['state']!='downloaded':continue
        d=json.loads(row['data']);d['ref']=row['ref']
        downloads[_key(d.get('download_path') or d.get('path'))]=d
    native=_attachment_metadata([d['ref'] for d in downloads.values()],old)
    records=[]
    for item in _rows(c,'received_items'):
        ident='in:'+item['id'];prev=old.get(ident,{})
        d=downloads.get(_key(item['path']),{});m=native.get(d.get('ref'),{})
        meta=json.loads(item['meta']);person=m.get('sender') or prev.get('person','')
        verified=bool(m or prev.get('person_origin')=='messenger')
        source='hanbiro' if d else item['source'] if item['source']=='nateon' else 'downloads'
        records.append(dict(id=ident,direction='in',intake_id=item['id'],name=Path(item['path']).name,
            size=meta['size'],source=source,person=person,person_origin='messenger' if verified else 'unknown',
            room=m.get('room') or prev.get('room',''),ref=d.get('ref',''),sha256=d.get('sha256',''),
            timestamp=_epoch(m.get('posted')) or prev.get('timestamp') or item['created'],
            received_at=item['created'],updated=item['updated'],state=item['state'],
            original_path=item['path'],base_path=item['target'] if item['state']=='organized' else item['path'],
            base_time=item['updated'] if item['state']=='organized' else item['created'],job=item['job'],
            error=item['error'],metadata=meta))
    outgoing=ROOT/'.provider_runtime/messenger/outgoing'
    if outgoing.is_dir():
        for p in outgoing.glob('*.json'):
            if not re.fullmatch('[0-9a-f]{32}\.json',p.name) or p.is_symlink() or p.stat().st_size>65536:continue
            d=json.loads(p.read_text('utf-8'))
            if d.get('state')=='prepared':continue
            records.append(dict(id='out:'+p.stem,direction='out',name=d['name'],size=d['size'],source='hanbiro',
                person=d['recipient'],person_origin='messenger',room=d['room'],sha256=d['sha256'],
                timestamp=d.get('started',0),updated=d.get('confirmed',d.get('started',0)),state=d['state'],
                original_path=d['path'],base_path=d['path'],base_time=d.get('started',0),
                server_verified=d.get('server_bytes_verified',False),message_id=d.get('message_id','')))
    for record in records:
        data=json.dumps(record,ensure_ascii=False,sort_keys=True)
        if old.get(record['id'])!=record:
            c.execute('INSERT OR REPLACE INTO file_history_records VALUES(?,?,?)',(record['id'],record['direction'],data))
    c.commit()


def _moves(c):
    result=[]
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='file_operation_steps'").fetchone():return result
    for r in c.execute('SELECT j.created,s.position,s.payload FROM file_operation_steps s '
                       'JOIN file_operation_jobs j ON j.id=s.job_id WHERE s.status=? ORDER BY j.created,s.position',('done',)):
        d=json.loads(r['payload'])
        if d.get('op') in {'move','rename','transfer','delete'} and d.get('source') and d.get('destination'):
            result.append((_epoch(d.get('completed_at')) or _epoch(r['created']),d['source'],d['destination'],d['op'],d.get('post')))
    return result


def _current(row,moves):
    path=Path(row['base_path']);events=[];identity=row.get('metadata')
    for stamp,src,dst,kind,post in moves:
        if stamp<row.get('base_time',0):continue
        try:relative=path.relative_to(Path(src))
        except ValueError:continue
        if str(relative)=='.' and isinstance(post,dict) and post.get('kind')=='file':identity=post
        path=Path(dst)/relative;events.append(dict(kind='move',at=stamp,path=str(path)))
    available=False;changed=False
    try:
        path=ops._guard(path);s=path.stat();available=path.is_file()
        changed=available and s.st_size!=row['size']
        # When no content digest exists, retain the observed file identity.
        # Cross-volume organizer transfers supply a fresh destination identity.
        meta=identity
        if available and meta and not row.get('sha256') and not row.get('job'):
            changed=changed or not transfer.same_node(path,meta,contents=True)
    except (OSError,ValueError):pass
    result=dict(row,current_path=str(path),available=available and not changed,changed=changed,path_events=events)
    if row['direction']=='in' and row['state']=='existing' and events:result['state']='organized'
    return result


def _all(db,predicate=None,*,resolve=True):
    with LOCK,closing(_db(db)) as c:
        _sync(c);moves=_moves(c)
        people={r['id']:r['person'] for r in c.execute('SELECT * FROM file_history_people')}
        result=[]
        for r in c.execute('SELECT data FROM file_history_records'):
            row=json.loads(r['data'])
            if row['id'] in people:row.update(person=people[row['id']],person_origin='user')
            if predicate and not predicate(row):continue
            result.append(row)
    # Filesystem checks can take time; they must not serialize other card reads.
    return [_current(row,moves) for row in result] if resolve else (result,moves)


def _public(row):
    return {k:v for k,v in row.items() if k not in {'metadata','base_path','base_time','path_events'}}


def listing(db,direction='in',query='',person='',offset=0,include_existing=False,period='all',kind='all'):
    if direction not in {'in','out','all'} or not isinstance(query,str) or len(query)>200 or not isinstance(person,str) or len(person)>120:
        raise ValueError('파일 이력 검색 조건을 확인해 주세요.')
    if not isinstance(offset,int) or offset<0:raise ValueError('목록 위치를 확인해 주세요.')
    from work_log import period_dates,KST
    first,last=period_dates(period)
    groups={'all':None,'excel':{'.xlsx','.xls','.xlsm','.csv'},'image':{'.png','.jpg','.jpeg','.gif','.webp','.bmp','.tif','.tiff'},
            'archive':{'.zip','.7z','.rar'},'pdf':{'.pdf'},'document':{'.doc','.docx','.hwp','.hwpx','.ppt','.pptx','.txt','.md'}}
    if kind not in groups:raise ValueError('파일 종류를 확인해 주세요.')
    def included(r):
        day=datetime.datetime.fromtimestamp(r['timestamp'],KST).date().isoformat() if r.get('timestamp') else ''
        return ((direction=='all' or r['direction']==direction) and (include_existing or r['state']!='existing')
                and (not person or person.casefold() in r['person'].casefold())
                and (not first or first<=day<=last) and (kind=='all' or Path(r['name']).suffix.lower() in groups[kind]))
    rows,moves=_all(db,included,resolve=False)
    if query:
        terms=query.casefold().split()
        def matches(row):
            # Search moved folder names too, without stat/guard on every file.
            path=Path(row['base_path'])
            for stamp,src,dst,_,_ in moves:
                if stamp<row.get('base_time',0):continue
                try:relative=path.relative_to(Path(src))
                except ValueError:continue
                path=Path(dst)/relative
            extension=Path(row['name']).suffix.lower()
            aliases=' '.join(name for name,extensions in groups.items() if extensions and extension in extensions)
            aliases+=' '+{'excel':'엑셀','image':'이미지 사진','archive':'압축파일 압축','pdf':'PDF','document':'문서'}.get(next((name for name,exts in groups.items() if exts and extension in exts),''),'')
            aliases+=' '+{'hanbiro':'한비로 한비로톡','nateon':'네이트온','downloads':'다운로드 폴더'}.get(row.get('source'),'')
            haystack='\n'.join(str(row.get(k,'')) for k in ('name','person','room','source','base_path'))+'\n'+str(path)+'\n'+aliases
            return all(term in haystack.casefold() for term in terms)
        rows=[r for r in rows if matches(r)]
    rows.sort(key=lambda r:(r['timestamp'],r['id']),reverse=True)
    return dict(items=[_public(_current(r,moves)) for r in rows[offset:offset+60]],total=len(rows),offset=offset,has_more=offset+60<len(rows),
                mode='code',model_calls=0,period=period,kind=kind,
                scope='확인한 수발신·정리 자료에서 검색해요. 단어를 띄어 쓰면 함께 찾고, 날짜는 실제 수발신 시각 기준이에요. 전체 드라이브·메신저의 모든 자료는 아니에요.')


def detail(db,ident):
    rows,moves=_all(db,resolve=False);row=next((r for r in rows if r['id']==ident),None)
    if not row:raise ValueError('현재 파일 이력에서 다시 선택해 주세요.')
    related=[_current(r,moves) for r in rows if r['id']==ident or (row.get('sha256') and r.get('sha256')==row['sha256'] and r['size']==row['size'])]
    row=next(r for r in related if r['id']==ident)
    events=[]
    for r in related:
        events.append(dict(kind=('received' if r.get('ref') else 'saved') if r['direction']=='in' else 'sent' if r['state']=='sent' else 'send_unconfirmed',
                           at=r['timestamp'],person=r['person'],source=r['source'],path=r['original_path']))
        if r.get('job') and r['state']=='organized':
            events.append(dict(kind='organized',at=r['base_time'],path=r['base_path']))
        events.extend(r['path_events'])
    unique={json.dumps(e,sort_keys=True):e for e in events}
    import material_links
    return dict(item=_public(row),events=sorted(unique.values(),key=lambda e:e['at']),
                connections=material_links.connections(db,row,rows,moves),mode='code',model_calls=0)


def annotate(db,ident,person):
    person=person.strip() if isinstance(person,str) else ''
    if not person or len(person)>120 or any(ord(c)<32 for c in person):raise ValueError('상대방 이름을 확인해 주세요.')
    row=detail(db,ident)['item']
    if row['person_origin']=='messenger':raise ValueError('원본 메신저에서 확인된 상대방은 그대로 보존해요.')
    with LOCK,closing(_db(db)) as c:
        c.execute('INSERT OR REPLACE INTO file_history_people VALUES(?,?,?)',(ident,person,time.time()));c.commit()
    return detail(db,ident)


def reveal(db,ident):
    rows=_all(db,lambda r:r['id']==ident)
    if not rows:raise ValueError('현재 파일 이력에서 다시 선택해 주세요.')
    row=rows[0]
    if not row['available']:raise ValueError('기록된 현재 위치에서 같은 파일을 확인하지 못했어요. 파일을 다시 찾아 주세요.')
    path=ops._guard(Path(row['current_path']))
    if row.get('sha256'):
        with path.open('rb') as f:
            digest=hashlib.sha256()
            for part in iter(lambda:f.read(1024*1024),b''):digest.update(part)
        if digest.hexdigest()!=row['sha256']:raise ValueError('기록 이후 파일 내용이 바뀌어 위치 열기를 멈췄어요.')
    if os.name!='nt':raise ValueError('탐색기 위치 열기는 Windows에서 지원해요.')
    _show_in_explorer(path)
    return dict(id=ident,path=str(path),dispatched=True,mode='code',model_calls=0,
                message='탐색기에 해당 파일 위치 표시를 요청했어요.')


def _show_in_explorer(path):
    """Use Shell item IDs: Explorer's command-line quoting loses spaced paths."""
    shell=ctypes.WinDLL('shell32');ole=ctypes.WinDLL('ole32')
    shell.SHParseDisplayName.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.c_ulong,ctypes.c_void_p]
    shell.SHParseDisplayName.restype=ctypes.c_long
    shell.ILFindLastID.argtypes=[ctypes.c_void_p];shell.ILFindLastID.restype=ctypes.c_void_p
    shell.SHOpenFolderAndSelectItems.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.POINTER(ctypes.c_void_p),ctypes.c_ulong]
    shell.SHOpenFolderAndSelectItems.restype=ctypes.c_long
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,ctypes.c_ulong];ole.CoInitializeEx.restype=ctypes.c_long
    ole.CoTaskMemFree.argtypes=[ctypes.c_void_p];ole.CoTaskMemFree.restype=None
    ole.CoUninitialize.argtypes=[];ole.CoUninitialize.restype=None
    initialized=ole.CoInitializeEx(None,2);folder=ctypes.c_void_p();item=ctypes.c_void_p()
    try:
        for value,target in ((str(path.parent),folder),(str(path),item)):
            if shell.SHParseDisplayName(value,None,ctypes.byref(target),0,None)<0:
                raise ValueError('현재 파일 위치를 탐색기에서 확인하지 못했어요.')
        children=(ctypes.c_void_p*1)(shell.ILFindLastID(item))
        if shell.SHOpenFolderAndSelectItems(folder,1,children,0)<0:
            raise ValueError('현재 파일 폴더를 탐색기에서 열지 못했어요.')
    finally:
        if item.value:ole.CoTaskMemFree(item)
        if folder.value:ole.CoTaskMemFree(folder)
        if initialized in (0,1):ole.CoUninitialize()


def latest(db,person,direction='out',open_location=False):
    if not isinstance(person,str) or not person.strip() or len(person)>120 or direction not in {'in','out'}:
        raise ValueError('찾을 상대방과 받은/보낸 구분을 확인해 주세요.')
    candidates,moves=_all(db,lambda r:r['direction']==direction and person.casefold() in r['person'].casefold()
                    and (direction!='out' or r['state']=='sent'),resolve=False)
    names={r['person'] for r in candidates}
    if len(names)>1:raise ValueError('같은 이름 후보가 여럿이에요: '+', '.join(sorted(names)))
    if not candidates:return dict(found=False,message='확인된 파일 이력에 해당 상대방 기록이 없어요.',mode='code',model_calls=0)
    candidates.sort(key=lambda r:(r['timestamp'],r['id']),reverse=True)
    row=_public(_current(candidates[0],moves))
    result=dict(found=True,item=row,mode='code',model_calls=0)
    result['message']=('태준님, '+row['person']+'님'+('께 가장 최근에 보낸' if direction=='out' else '에게 가장 최근에 받은')+
                       ' 파일은 **'+row['name']+'**이에요.\n위치: '+row['current_path'])
    if direction=='out' and row['state']!='sent':result['message']+='\n이 건은 전송 완료가 확인되지 않았어요.'
    if open_location:
        try:
            result['reveal']=reveal(db,row['id']);result['message']+='\n탐색기에 파일 위치 표시를 요청했어요.'
        except (ValueError,OSError) as e:result['message']+='\n'+str(e)
    return result


def match_command(text):
    # Full requests only; unrelated trailing instructions must reach the agent.
    pattern=(r'(?:(?:가장\s*)?최근(?:에)?\s*)?(?P<person>[가-힣A-Za-z]{2,20}?)(?:님)?(?:이)?(?:에게|한테|께|으로부터)\s*'
             r'(?:(?:가장\s*)?최근(?:에)?\s*)?(?P<verb>보낸|받은)\s*파일(?:이\s*뭔지|이\s*뭐(?:야|예요)|은\s*뭐(?:야|예요))?\s*'
             r'(?:(?:알려(?:주고|줘)|찾아(?:서|줘)|보여줘|확인해줘)\s*)?(?:[~,，.]\s*)?'
             r'(?P<open>(?:(?:파일\s*)?위치(?:를)?|폴더(?:를)?)\s*열어(?:줘|주세요))?')
    m=re.fullmatch(pattern,text.strip().rstrip('~.!? ').strip())
    if not m:return None
    return dict(person=m['person'],direction='out' if m['verb']=='보낸' else 'in',open_location=bool(m['open']))
