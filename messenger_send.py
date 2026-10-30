"""Explicit file sending through the existing Hanbiro client, never desktop input.

The private journal is written before dispatch. An uncertain send is not retried.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import time
import uuid

import file_operations as ops
import messenger_attachment_worker as intake

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'.provider_runtime/messenger'
ASAR=Path.home()/'AppData/Local/Programs/hanbiro-talk/resources/app.asar'


def _version():
    with ASAR.open('rb') as f:
        header=struct.unpack('<4I',f.read(16));tree=json.loads(f.read(header[3]))
        node=tree['files']['package.json'];f.seek(8+header[1]+int(node['offset']))
        package=json.loads(f.read(node['size']))
    return str(package['version'])


def _file_digest(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _record(ident):
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident):
        raise ValueError('전송 요청 번호를 확인해 주세요.')
    return PRIVATE/'outgoing'/(ident+'.json')


def _save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def public(data):
    return {k:v for k,v in data.items() if not k.startswith('_')}


def _recipient(room,recipient):
    domain,_,data=intake._storage();own=data['_aemeath_current_user']
    current=data.get('recoil-atomAllRooms',{}).get('rooms',{}).get(room)
    if not isinstance(current,dict) or current.get('leftRoom'):
        raise ValueError('현재 참여 중인 원본 대화방을 확인해 주세요.')
    members=current.get('members',{})
    peers=[m for k,m in members.items() if k!=own]
    if own not in members or len(members)!=2 or len(peers)!=1 or peers[0].get('name')!=recipient:
        raise ValueError('지정한 팀원 한 명과의 개인 대화방으로 확인되지 않았어요.')
    return domain,own,peers[0]['userKey']


def prepare(ident,room,recipient,path):
    record=_record(ident);source=ops._guard(Path(path))
    if not source.is_file():raise ValueError('실제 보낼 파일을 확인해 주세요.')
    info=source.stat()
    if not 0<info.st_size<=2*1024*1024*1024:raise ValueError('파일 크기를 확인해 주세요.')
    domain,own,peer=_recipient(room,recipient)
    plan=dict(id=ident,state='prepared',room=room,recipient=recipient,path=str(source),
              name=source.name,size=info.st_size,sha256=_file_digest(source),
              mode='code',model_calls=0,foreground_ui=False,
              _domain=domain,_own=own,_peer=peer,_version=_version(),
              _mtime_ns=info.st_mtime_ns)
    if record.exists():
        old=json.loads(record.read_text('utf-8'))
        if any(old.get(k)!=plan[k] for k in ('room','recipient','path','sha256')):
            raise ValueError('같은 요청 번호의 파일이나 받는 사람이 달라요.')
        return public(old)
    _save(record,plan)
    return public(plan)


def status(ident):
    return public(json.loads(_record(ident).read_text('utf-8')))


def _bridge(action,payload=None):
    cfg=json.loads((PRIVATE/'hanbiro-cdp.json').read_text('utf-8-sig'))
    port=cfg.get('port')
    if not isinstance(port,int) or not 1024<=port<=65535:
        raise ValueError('한비로 전송 연결을 확인해 주세요.')
    node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    result=subprocess.run([str(node),str(ROOT/'messenger_send_bridge.cjs')],
        input=json.dumps(dict(action=action,port=port,payload=payload)),text=True,
        encoding='utf-8',capture_output=True,timeout=35,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('한비로 전송 연결을 확인해 주세요.')
    value=json.loads(result.stdout)
    if not value.get('ok'):raise ValueError(value.get('error','한비로 연결 확인이 필요해요.'))
    return value


def dispatch(ident):
    record=_record(ident);plan=json.loads(record.read_text('utf-8'))
    if plan['state']!='prepared':return public(plan)
    source=ops._guard(Path(plan['path']))
    if source.stat().st_size!=plan['size'] or _file_digest(source)!=plan['sha256']:
        raise ValueError('준비 후 파일이 바뀌어 전송하지 않았어요.')
    domain,own,peer=_recipient(plan['room'],plan['recipient'])
    if (domain,own,peer,_version())!=(plan['_domain'],plan['_own'],plan['_peer'],plan['_version']):
        raise ValueError('계정·받는 사람·앱 버전이 바뀌어 전송하지 않았어요.')
    _bridge('preflight')
    try:
        payload=dict(roomKey=plan['room'],userKeys=[peer],images=[],files=[
            dict(name=source.name,path=str(source),size=plan['size'],serverExt='ETC',duration=0,thumbBase64='')])
        # Persist before the only mutating call. Unknown completion never retries.
        plan.update(state='dispatching',started=time.time());_save(record,plan)
        _bridge('send',payload)
        plan['state']='awaiting_confirmation';_save(record,plan)
    except Exception:
        if plan['state']=='dispatching':plan['state']='unknown';_save(record,plan)
        # IPC may succeed before the transport disconnects. Reconcile the
        # original room first; never treat a client error as permission to resend.
        try:
            receipt=confirm(ident)
            if receipt['state']=='sent':return receipt
        except Exception:
            pass
        raise
    return confirm(ident)


def confirm(ident):
    record=_record(ident);plan=json.loads(record.read_text('utf-8'))
    if plan['state'] not in {'dispatching','awaiting_confirmation','unknown'}:return public(plan)
    _,_,data=intake._storage();own=data['_aemeath_current_user']
    if own!=plan['_own']:raise ValueError('현재 한비로 계정이 바뀌었어요.')
    matches=[]
    for key,buckets in data.items():
        if not key.startswith('recoil-atomAllMessageList_'):continue
        for bucket in intake._values(buckets):
            if not isinstance(bucket,dict):continue
            for message in intake._message_rows(bucket.get(plan['room'],{})):
                detail=message.get('messageDetail') or {}
                if (message.get('userKey')==own and detail.get('fileName')==plan['name']
                    and int(detail.get('fileSize',0))==plan['size']
                    and float(message.get('createDate',0))/1000>=plan['started']-2):
                    matches.append(message)
    if len(matches)==1:
        message=matches[0];detail=message['messageDetail']
        if not message.get('isSending') and not message.get('isPending') and detail.get('httpServerFileName'):
            plan.update(state='sent',message_id=str(message.get('numberKey')),
                        confirmed=time.time(),verification='원본 개인 대화방의 전송 파일 기록 확인')
            _save(record,plan)
    elif len(matches)>1:
        plan.update(state='unknown',verification='일치하는 전송 기록이 여러 개라 자동 재전송하지 않아요.')
        _save(record,plan)
    return public(plan)


def prepare_text(ident, room, recipient, text):
    """Bind one explicitly requested text to the current two-member room."""
    record=_record(ident).parent.parent/'outgoing-text'/(ident+'.json')
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise ValueError('메시지는 2000자 이내로 입력해 주세요.')
    domain,own,peer=_recipient(room,recipient)
    if record.exists():
        old=json.loads(record.read_text('utf-8'))
        if any(old.get(k)!=v for k,v in {'room':room,'recipient':recipient,'text':text}.items()):
            raise ValueError('같은 요청 번호의 메시지나 받는 사람이 달라요.')
        return public(old)
    plan=dict(id=ident,state='prepared',room=room,recipient=recipient,text=text,
              mode='code',model_calls=0,foreground_ui=False,_domain=domain,
              _own=own,_peer=peer,_version=_version(),
              _client_id=str(uuid.uuid4())+str(int(time.time()*1000)))
    _save(record,plan)
    return public(plan)


def confirm_text(ident):
    record=_record(ident).parent.parent/'outgoing-text'/(ident+'.json')
    plan=json.loads(record.read_text('utf-8'))
    if plan['state'] not in {'dispatching','awaiting_confirmation','unknown'}:return public(plan)
    _,_,data=intake._storage()
    if data['_aemeath_current_user']!=plan['_own']:raise ValueError('현재 한비로 계정이 바뀌었어요.')
    matches={}
    for key,buckets in data.items():
        if not key.startswith('recoil-atomAllMessageList_'):continue
        for bucket in intake._values(buckets):
            if not isinstance(bucket,dict):continue
            for m in intake._message_rows(bucket.get(plan['room'],{})):
                if (m.get('userKey')==plan['_own'] and m.get('messageType')==1
                    and m.get('messageBody')==plan['text'] and not m.get('isDeleted')
                    and float(m.get('createDate',0))/1000>=plan['started']-2
                    and not m.get('isSending') and not m.get('isPending') and m.get('numberKey')):
                    matches[str(m['numberKey'])]=m
    if len(matches)==1:
        plan.update(state='sent',message_id=next(iter(matches)),confirmed=time.time(),
                    verification='원본 개인 대화방의 발신 텍스트 기록 확인')
        _save(record,plan)
    elif len(matches)>1:
        plan.update(state='unknown',verification='일치하는 발신 기록이 여러 개라 재전송하지 않아요.')
        _save(record,plan)
    return public(plan)


def dispatch_text(ident):
    record=_record(ident).parent.parent/'outgoing-text'/(ident+'.json')
    plan=json.loads(record.read_text('utf-8'))
    if plan['state']!='prepared':return public(plan)
    if _recipient(plan['room'],plan['recipient'])!=(plan['_domain'],plan['_own'],plan['_peer']) or _version()!=plan['_version']:
        raise ValueError('계정·받는 사람·앱 버전이 바뀌어 전송하지 않았어요.')
    _bridge('preflight')
    plan.update(state='dispatching',started=time.time());_save(record,plan)
    try:
        _bridge('send_text',dict(roomKey=plan['room'],userKey=plan['_own'],clientId=plan['_client_id'],
                                message=plan['text'],emoticon='',richFormat=''))
        plan['state']='awaiting_confirmation';_save(record,plan)
    except Exception:
        plan['state']='unknown';_save(record,plan)
        try:
            result=confirm_text(ident)
            if result['state']=='sent':return result
        except Exception:pass
        raise
    return confirm_text(ident)
