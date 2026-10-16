"""Versioned companion animation choices referencing immutable uploaded originals."""
import json
import os
import pathlib
import threading
import time
import uuid

import work_attachments

STATES=('idle','answer','agree','error','typing','working')
_LOCK=threading.RLock()


def _location(db):
    # Reuse the attachment storage route checks before reaching sibling metadata.
    work_attachments._root(db)
    root=pathlib.Path(db).absolute().parent/'local_backups'/'companion-assets'
    root.mkdir(exist_ok=True)
    if root.is_symlink() or getattr(root.lstat(),'st_file_attributes',0)&1024:
        raise ValueError('캐릭터 자료 경로를 확인할 수 없습니다.')
    return root


def _read(db):
    path=_location(db)/'manifest.json'
    if not path.exists(): return {'revision':'builtin','overrides':{},'history':[]}
    if path.is_symlink() or path.stat().st_nlink!=1 or path.stat().st_size>1024*1024:
        raise ValueError('캐릭터 설정을 확인할 수 없습니다.')
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or not isinstance(data.get('overrides'),dict): raise ValueError('캐릭터 설정 형식이 올바르지 않습니다.')
    return data


def manifest(db):
    with _LOCK:
        saved=_read(db)
        states={state:[{'id':'builtin-'+state,'name':'Aemeath '+state,
                       'url':'/assets/aemeath/'+state+'.gif','still_url':'/assets/aemeath/'+state+'.png'}] for state in STATES}
        for state,override in saved['overrides'].items():
            if state not in states or not isinstance(override,dict): continue
            items=[]
            for identifier in override.get('ids',[])[:24]:
                row=work_attachments.resolve(db,identifier)
                if row['kind']!='image': continue
                item=work_attachments.public(row)
                items.append({'id':identifier,'name':item['name'],'url':item['preview_url']})
            if override.get('replace') and items: states[state]=items
            else: states[state].extend(items)
        return {'revision':saved['revision'],'states':states}


def apply(db,row,state='idle',mode='append'):
    if state not in STATES or mode not in ('append','replace'): raise ValueError('캐릭터 상태와 적용 방식을 확인해주세요.')
    if row['kind']!='image': raise ValueError('캐릭터에는 GIF·PNG·JPG·WebP·ICO 이미지를 사용할 수 있습니다.')
    # Validate immutable content immediately before committing the reference.
    row=work_attachments.resolve(db,row['id'])
    with _LOCK:
        saved=_read(db)
        before=json.loads(json.dumps(saved))
        previous=saved['overrides'].get(state,{'replace':False,'ids':[]})
        if mode=='replace': current={'replace':True,'ids':[row['id']]}
        else:
            ids=list(previous.get('ids',[]))
            if row['id'] not in ids: ids.append(row['id'])
            if len(ids)>24: raise ValueError('상태별 캐릭터 자료는 24개까지 등록할 수 있습니다.')
            current={'replace':bool(previous.get('replace')),'ids':ids}
        saved['overrides'][state]=current
        saved['revision']=uuid.uuid4().hex
        saved['history']=(saved.get('history',[])+[{'at':time.time(),'state':state,'mode':mode,'attachment_id':row['id'],'previous':previous}])[-100:]
        root=_location(db)
        # Preserve prior registry before atomically replacing the live manifest.
        backup=root/('before-'+saved['revision']+'.json')
        with backup.open('x',encoding='utf-8') as stream: json.dump(before,stream,ensure_ascii=False)
        temporary=root/('pending-'+uuid.uuid4().hex+'.json')
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(saved,stream,ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,root/'manifest.json')
    return manifest(db)


def remove(db,state,identifier):
    """Remove one uploaded variant from a state without touching its source file."""
    if state not in STATES or not isinstance(identifier,str) or not identifier:
        raise ValueError('캐릭터 상태와 자료를 확인해주세요.')
    with _LOCK:
        saved=_read(db)
        previous=saved['overrides'].get(state,{'replace':False,'ids':[]})
        ids=list(previous.get('ids',[]))
        if identifier not in ids:
            raise ValueError('선택한 상태에 등록된 추가 캐릭터가 없습니다.')
        before=json.loads(json.dumps(saved))
        ids.remove(identifier)
        if ids: saved['overrides'][state]={'replace':bool(previous.get('replace')),'ids':ids}
        else: saved['overrides'].pop(state,None)
        saved['revision']=uuid.uuid4().hex
        saved['history']=(saved.get('history',[])+[{'at':time.time(),'state':state,'mode':'remove','attachment_id':identifier,'previous':previous}])[-100:]
        root=_location(db)
        backup=root/('before-'+saved['revision']+'.json')
        with backup.open('x',encoding='utf-8') as stream: json.dump(before,stream,ensure_ascii=False)
        temporary=root/('pending-'+uuid.uuid4().hex+'.json')
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(saved,stream,ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,root/'manifest.json')
    return manifest(db)
