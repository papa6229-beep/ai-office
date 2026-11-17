"""Explicit file sending through the existing Hanbiro client, never desktop input.

The private journal is written before dispatch. An uncertain send is not retried.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid

import file_operations as ops

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'.provider_runtime/messenger'
ASAR=Path.home()/'AppData/Local/Programs/hanbiro-talk/resources/app.asar'


def _attachment_reader():
    # The Chromium cache dependencies belong to the on-demand worker process.
    import messenger_attachment_worker
    return messenger_attachment_worker


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
    domain,_,data=_attachment_reader()._storage();own=data['_aemeath_current_user']
    current=data.get('recoil-atomAllRooms',{}).get('rooms',{}).get(room)
    if not isinstance(current,dict) or current.get('leftRoom'):
        raise ValueError('현재 참여 중인 원본 대화방을 확인해 주세요.')
    members=current.get('members',{})
    peers=[m for k,m in members.items() if k!=own]
    if own not in members or len(members)!=2 or len(peers)!=1 or peers[0].get('name')!=recipient:
        raise ValueError('지정한 팀원 한 명과의 개인 대화방으로 확인되지 않았어요.')
    return domain,own,peers[0]['userKey']


def private_room(recipient):
    _, _, data = _attachment_reader()._storage()
    own = data['_aemeath_current_user']
    rooms = data.get('recoil-atomAllRooms', {}).get('rooms', {})
    matches = []
    for key, room in rooms.items():
        if not isinstance(room, dict) or room.get('leftRoom'):
            continue
        members = room.get('members', {})
        if isinstance(members, dict) and own in members and len(members) == 2:
            peer = next(value for member, value in members.items() if member != own)
            if isinstance(peer, dict) and peer.get('name') == recipient:
                matches.append(key)
    if len(matches) != 1:
        raise ValueError('한비로에서 지정한 팀원의 기존 개인방을 하나로 확인하지 못했어요.')
    return matches[0]


def prepare(ident,room,recipient,path,*,expected_sha256=''):
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
    if expected_sha256 and plan['sha256']!=expected_sha256:
        raise ValueError('파일 내용이 수신 또는 파일 이력의 확인값과 달라요.')
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


def _android_dispatch(ident, record, action, authorize):
    import hanbiro_guest_send as guest_send
    import task_control

    confirm_send = confirm_text if action == 'text' else confirm
    with guest_send.connected() as guest:
        plan = json.loads(record.read_text('utf-8'))
        if plan['state'] != 'prepared':
            return confirm_send(ident)
        if _recipient(plan['room'], plan['recipient']) != (plan['_domain'], plan['_own'], plan['_peer']):
            raise ValueError('한비로 계정이나 대상 개인방이 바뀌었어요.')
        rooms = _attachment_reader().chat_snapshot([], [plan['room']])['rooms']
        if len(rooms) != 1 or rooms[0]['id'] != plan['room']:
            raise ValueError('기존 한비로 개인방 기록을 확인하지 못했어요.')
        messages = [message['text'] for message in rooms[0]['messages']]

        def before_send():
            task_control.checkpoint()
            authorize()
            current = _recipient(plan['room'], plan['recipient'])
            if current != (plan['_domain'], plan['_own'], plan['_peer']):
                raise ValueError('한비로 계정이나 대상 개인방이 바뀌었어요.')
            plan.update(state='dispatching', started=time.time(), transport='android')
            _save(record, plan)

        try:
            if action == 'text':
                observed = guest_send.deliver_text(guest, plan['recipient'], plan['text'], messages, before_send)
                if observed:
                    plan.update(state='sent', confirmed=time.time(),
                                verification='원본 Android 한비로 개인방에서 새 발신 메시지 확인')
                    _save(record, plan)
                    return public(plan)
            else:
                path = ops._guard(Path(plan['path']))
                if path.stat().st_size != plan['size'] or _file_digest(path) != plan['sha256']:
                    raise ValueError('준비 후 파일이 바뀌어 전송하지 않았어요.')
                guest_send.deliver_file(guest, plan['recipient'], path, plan['sha256'], messages, before_send)
            plan['state'] = 'awaiting_confirmation'
            _save(record, plan)
        except Exception:
            if plan['state'] == 'dispatching':
                plan['state'] = 'unknown'
                _save(record, plan)
                try:
                    result = confirm_send(ident)
                    if result['state'] == 'sent':
                        return result
                except (OSError, ValueError):
                    pass
            raise
    return confirm_send(ident)


def dispatch(ident, *, authorize=None):
    import task_control
    record=_record(ident);plan=json.loads(record.read_text('utf-8'))
    if plan['state']!='prepared':return public(plan)
    source=ops._guard(Path(plan['path']))
    if source.stat().st_size!=plan['size'] or _file_digest(source)!=plan['sha256']:
        raise ValueError('준비 후 파일이 바뀌어 전송하지 않았어요.')
    domain,own,peer=_recipient(plan['room'],plan['recipient'])
    if (domain,own,peer,_version())!=(plan['_domain'],plan['_own'],plan['_peer'],plan['_version']):
        raise ValueError('계정·받는 사람·앱 버전이 바뀌어 전송하지 않았어요.')
    if authorize is not None and plan['_version'] != '2.1.257':
        return _android_dispatch(ident, record, 'file', authorize)
    _bridge('preflight')
    task_control.checkpoint()
    if authorize is not None:
        authorize()
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
    intake=_attachment_reader()
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
    intake=_attachment_reader()
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


def dispatch_text(ident, *, authorize=None):
    import task_control

    record=_record(ident).parent.parent/'outgoing-text'/(ident+'.json')
    plan=json.loads(record.read_text('utf-8'))
    if plan['state']!='prepared':return public(plan)
    if _recipient(plan['room'],plan['recipient'])!=(plan['_domain'],plan['_own'],plan['_peer']) or _version()!=plan['_version']:
        raise ValueError('계정·받는 사람·앱 버전이 바뀌어 전송하지 않았어요.')
    if authorize is not None and plan['_version'] != '2.1.257':
        return _android_dispatch(ident, record, 'text', authorize)
    _bridge('preflight')
    task_control.checkpoint()
    if authorize is not None:
        authorize()
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


def registered(action, recipient, *, db=None, request_id=None, **payload):
    """Use the same Chromium reader runtime as Office attachment intake."""
    from messenger_attachments import _runtime
    import task_control

    task_control.checkpoint()
    request = dict(action=action, recipient=recipient, request_id=request_id,
                   db=str(Path(db).resolve()) if db is not None else None, **payload)
    try:
        result = subprocess.run([str(_runtime()), '-X', 'utf8', str(Path(__file__).resolve())],
                                input=json.dumps(request, ensure_ascii=False), text=True,
                                encoding='utf-8', capture_output=True, timeout=350,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise ValueError('한비로 전용 실행기 응답이 지연됐어요. 발신 기록을 확인하기 전에는 같은 요청을 다시 보내지 않아요.') from None
    if result.returncode:
        raise ValueError('한비로 전용 실행기 응답을 확인하지 못했어요. 발신 기록을 확인하기 전에는 같은 요청을 다시 보내지 않아요.')
    response = json.loads(result.stdout)
    if response.get('stopped'):
        raise task_control.Stopped()
    if response.get('error'):
        raise ValueError(response['error'])
    return response['data']


def _registered_action(request):
    import office_fastlane
    from messenger_file_routines import _authorize_send

    action, recipient = request['action'], request['recipient']
    if action == 'recent':
        room = private_room(recipient)
        domain, own, _ = _recipient(room, recipient)
        snapshot = _attachment_reader().chat_snapshot([], [room])
        if snapshot['account'] != hashlib.sha256(f'{domain}|{own}'.encode()).hexdigest():
            raise ValueError('조회 중 한비로 계정이 바뀌었어요.')
        rooms = snapshot['rooms']
        messages = rooms[0]['messages'] if len(rooms) == 1 and rooms[0]['id'] == room else ()
        latest = next((message for message in reversed(messages)
                       if message['direction'] == 'in' and message['sender'] == recipient), None)
        return dict(message=latest, scope=snapshot['scope'])
    if action not in {'file', 'text'}:
        raise ValueError('지원하지 않는 한비로 요청입니다.')
    ident = request['request_id']
    if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
        raise ValueError('현재 한비로 요청 번호를 확인해 주세요.')
    db = Path(request['db'])

    def authorize():
        _authorize_send(db, ident)

    authorize()
    room = private_room(recipient)
    if action == 'file':
        prepare(ident, room, recipient, request['path'], expected_sha256=request['expected_sha256'])
        result, confirm_send = dispatch(ident, authorize=authorize), confirm
    else:
        prepare_text(ident, room, recipient, request['text'])
        result, confirm_send = dispatch_text(ident, authorize=authorize), confirm_text
    deadline = time.monotonic() + 12
    while result['state'] == 'awaiting_confirmation' and time.monotonic() < deadline:
        if office_fastlane.stopped(db, ident):
            break
        time.sleep(0.5)
        result = confirm_send(ident)
    return result


if __name__ == '__main__':
    import task_control

    try:
        response = dict(data=_registered_action(json.load(sys.stdin)))
    except task_control.Stopped:
        response = dict(stopped=True)
    except ValueError as error:
        response = dict(error=str(error))
    except OSError:
        response = dict(error='한비로 캐시 또는 작업 파일에 접근하지 못했어요. 발신 기록 확인 전에는 같은 요청을 다시 보내지 않아요.')
    print(json.dumps(response, ensure_ascii=False))
