"""Private worker: read Hanbiro's own cache and receive selected HTTPS attachments.

Never writes the messenger profile or exposes its login key. Dependencies live
under .provider_runtime/messenger/python and are loaded only in this process.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / '.provider_runtime/messenger/python'))
PROFILE = Path.home() / 'AppData/Roaming/HanbiroTalk2'
MAX_SIZE = 2 * 1024 * 1024 * 1024


def _latest(records, key, sequence):
    result = {}
    for record in records:
        name = key(record)
        if name not in result or sequence(record) > sequence(result[name]):
            result[name] = record
    return {k: v for k, v in result.items() if v.is_live}


def _storage():
    from ccl_chromium_reader import ccl_chromium_indexeddb as idb
    from ccl_chromium_reader import ccl_chromium_localstorage as local
    cfg = json.loads((PROFILE / 'config.json').read_text('utf-8'))
    domain = cfg.get('pref_domain', '').strip().lower()
    if not re.fullmatch(r'[a-z0-9.-]+', domain) or '.' not in domain:
        raise ValueError('한비로의 현재 회사 도메인을 확인할 수 없어요.')
    with local.LocalStoreDb(PROFILE / 'Local Storage/leveldb') as db:
        records = _latest(db.iter_records_for_script_key(
            lambda origin: origin.startswith('file://'), 'pref_messengerautologin',
            raise_on_no_result=False), lambda r: r.script_key, lambda r: r.leveldb_seq_number)
        token = records.get('pref_messengerautologin')
        token = token.value if token else ''
    if not token or '\r' in token or '\n' in token:
        raise ValueError('한비로의 현재 로그인을 확인해 주세요.')
    with idb.WrappedIndexDB(PROFILE / 'IndexedDB/file__0.indexeddb.leveldb',
                          PROFILE / 'IndexedDB/file__0.indexeddb.blob') as db:
        # The reader also enumerates historical LevelDB versions. Hanbiro can
        # remove their external blobs during compaction. Select the current raw
        # version before decoding, and strip SST sequence trailers from keys.
        from ccl_chromium_reader.storage_formats import ccl_leveldb
        current = {}
        for record in db._raw_db._fetched_records:
            key = record.user_key
            if key not in current or record.seq > current[key].seq:
                current[key] = record
        db._raw_db._fetched_records = [SimpleNamespace(
            key=key, user_key=key, value=r.value, seq=r.seq, state=r.state,
            origin_file=r.origin_file) for key, r in current.items()
            if r.state == ccl_leveldb.KeyState.Live]
        db._raw_db._blob_lookup_cache.clear()
        selected = [x for x in db.database_ids if x.name == 'Talk2DB']
        if len(selected) != 1:
            raise ValueError('한비로 대화 캐시를 확인할 수 없어요.')
        rows = _latest(db[selected[0].dbid_no]['Table_talk2'].iterate_records(),
                       lambda r: str(r.key.value), lambda r: r.ldb_seq_no)
        data = {k: v.value.get('data') for k, v in rows.items() if isinstance(v.value, dict)}
    basic = cfg.get('pref_talk2_basic_info', {})
    if isinstance(basic, str):
        basic = json.loads(basic)
    own = basic.get('userKey', '') if isinstance(basic, dict) else ''
    if not own:
        raise ValueError('한비로의 현재 사용자 정보를 확인해 주세요.')
    data['_aemeath_current_user'] = own
    return domain, token, data


def _values(value):
    return value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()


def _message_rows(history):
    for message in _values(history.get('messages')):
        if not isinstance(message, dict):
            continue
        yield message
        for child in _values(message.get('groupFiles')):
            if isinstance(child, dict):
                inherited = {k: message[k] for k in ('userKey', 'createDate', 'isDeleted') if k in message}
                yield dict(inherited, **child)


def _attachments():
    domain, token, data = _storage()
    rooms = data.get('recoil-atomAllRooms', {})
    rooms = rooms.get('rooms', {}) if isinstance(rooms, dict) else {}
    found = {}
    for key, buckets in data.items():
        if not key.startswith('recoil-atomAllMessageList_'):
            continue
        for bucket in _values(buckets):
            if not isinstance(bucket, dict):
                continue
            for room, history in bucket.items():
                if not isinstance(history, dict):
                    continue
                for message in _message_rows(history):
                    if not isinstance(message, dict) or message.get('isDeleted'):
                        continue
                    detail = message.get('messageDetail') or {}
                    if not isinstance(detail, dict) or not detail.get('fileName'):
                        continue
                    if not message.get('userKey') or not detail.get('httpServerFilePath') or not detail.get('httpServerFileName'):
                        continue
                    try:
                        size = int(detail.get('fileSize', 0))
                    except (ValueError, TypeError):
                        continue
                    if not 0 < size <= MAX_SIZE:
                        continue
                    identity = [domain, data['_aemeath_current_user'], str(room), str(message.get('numberKey')),
                                message['userKey'], detail['fileName'], size,
                                detail['httpServerFilePath'], detail['httpServerFileName'],
                                detail.get('httpHostName', '')]
                    ref = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
                    room_info = rooms.get(room, {}) if isinstance(rooms, dict) else {}
                    members = room_info.get('members', {}) if isinstance(room_info, dict) else {}
                    if data['_aemeath_current_user'] not in members:
                        continue
                    sender = members.get(message['userKey'], {}) if isinstance(members, dict) else {}
                    sender_title = sender.get('name', '') if isinstance(sender, dict) else ''
                    peers = [m.get('name', '') for k, m in members.items()
                             if k != data['_aemeath_current_user'] and isinstance(m, dict)]
                    names = [room_info.get('aliasPrivate'), room_info.get('aliasPublic'), room_info.get('name'),
                             ', '.join(p for p in peers if isinstance(p, str) and p), str(room)]
                    title = next(v for v in names if isinstance(v, str) and v)
                    found[ref] = dict(ref=ref, source='hanbiro', room=str(room), room_title=title,
                                      sender=sender_title,
                                      direction='out' if message['userKey'] == data['_aemeath_current_user'] else 'in',
                                      name=detail['fileName'], size=size,
                                      posted=str(message.get('createDate', '')),
                                      _detail=detail, _sender=message['userKey'])
    return domain, token, found


def listing(query='', room='', offset=0):
    _, _, rows = _attachments()
    term = query.casefold()
    items = [r for r in rows.values() if r['direction'] == 'in' and (not room or r['room'] == room)
             and (not term or term in r['name'].casefold() or term in r['room_title'].casefold())]
    items.sort(key=lambda r: (r['posted'], r['ref']), reverse=True)
    return dict(source='hanbiro', mode='code', foreground_ui=False,
                scope='PC 앱에 캐시된 첨부만 조회하며 전체 서버 이력을 뜻하지 않아요.',
                total=len(items), offset=offset,
                items=[{k: v for k, v in r.items() if not k.startswith('_')} for r in items[offset:offset+60]])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('다운로드 서버 주소가 바뀌어 중단했어요.')


def receive(ref, destination, cancelled=''):
    domain, token, rows = _attachments()
    row = rows.get(ref)
    if not row or row['direction'] != 'in':
        raise ValueError('원본 첨부가 바뀌었거나 사라졌어요. 목록을 다시 확인해 주세요.')
    d = row['_detail']
    host = (d.get('httpHostName') or domain).strip().lower()
    # Never send an existing login key to an arbitrary host supplied by a message.
    if host != domain:
        raise ValueError('첨부 서버가 현재 회사 도메인과 달라 별도 확인이 필요해요.')
    for value in (d['httpServerFilePath'], d['httpServerFileName'], row['_sender']):
        if not isinstance(value, str) or any(c in value for c in '\r\n'):
            raise ValueError('첨부 정보 형식을 확인할 수 없어요.')
    name = row['name']
    if name != Path(name).name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith(('.', ' ')):
        raise ValueError('파일명을 안전하게 저장할 수 없어요.')
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', name.split('.')[0]):
        raise ValueError('예약된 파일명은 저장하지 않아요.')
    root = Path(destination)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('실제 수신 폴더를 확인해 주세요.')
    import file_operations
    file_operations._guard(root, True)
    target = root / name
    for n in range(1, 10000):
        if not target.exists():
            break
        target = root / (Path(name).stem + f' ({n})' + Path(name).suffix)
    else:
        raise ValueError('같은 이름의 파일이 너무 많아요.')
    partial = root / ('.aemeath-' + uuid.uuid4().hex + '.partial')
    headers = {'Content-Type': 'xml', 'HANBIRO-MODE': 'get',
               'HANBIRO-FILENAME': urllib.parse.quote(name, safe=''),
               'HANBIRO-SERVERPATH': d['httpServerFilePath'],
               'HANBIRO-SERVERFILENAME': d['httpServerFileName'],
               'HANBIRO-LOGINKEY': token, 'HANBIRO-SENDER': row['_sender']}
    request = urllib.request.Request('https://' + host + '/winapp/hcsong/messenger_file.php',
                                     data=b'', headers=headers, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    digest = hashlib.sha256()
    written = 0
    started = time.monotonic()
    try:
        if cancelled and Path(cancelled).exists():
            raise ValueError('다운로드를 중지했어요.')
        with opener.open(request, timeout=20) as response, partial.open('xb') as output:
            if response.status != 200:
                raise ValueError('한비로가 다운로드를 승인하지 않았어요.')
            while True:
                if cancelled and Path(cancelled).exists():
                    raise ValueError('다운로드를 중지했어요.')
                if time.monotonic()-started > 300:
                    raise ValueError('다운로드 대기 시간이 초과됐어요.')
                chunk = response.read(1024*1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > row['size']:
                    raise ValueError('원본 첨부 크기와 달라 중단했어요.')
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if written != row['size']:
            raise ValueError('원본 첨부 크기와 달라 다운로드를 완료하지 못했어요.')
        # Windows rename never replaces a pre-existing destination.
        partial.rename(target)
        return dict(path=str(target), size=written, sha256=digest.hexdigest(),
                    ref=ref, source='hanbiro', mode='code', model_calls=0, foreground_ui=False)
    finally:
        if partial.exists():
            partial.unlink()


def chat_snapshot(titles,room_ids):
    if not isinstance(titles,list) or len(titles)>80 or any(not isinstance(t,str) or len(t)>400 for t in titles):raise ValueError('대화 범위를 확인해 주세요.')
    if not isinstance(room_ids,list) or len(room_ids)>80 or any(not isinstance(r,str) or not re.fullmatch('[0-9a-f]{16,64}',r) for r in room_ids):raise ValueError('대화 범위를 확인해 주세요.')
    domain,_,data=_storage();own=data['_aemeath_current_user'];rooms=data.get('recoil-atomAllRooms',{}).get('rooms',{})
    selected={}
    for ident,room in rooms.items():
        if not isinstance(room,dict) or room.get('leftRoom'):continue
        members=room.get('members',{})
        if own not in members:continue
        peers=[m.get('name','') for k,m in members.items() if k!=own and isinstance(m,dict)]
        aliases=[room.get('aliasPrivate'),room.get('aliasPublic'),room.get('name'),', '.join(peers),*peers]
        if ident not in room_ids and not any(t in aliases for t in titles):continue
        title=next((v for v in aliases if isinstance(v,str) and v),ident)
        selected[ident]=dict(id=ident,title=title,messages=[],_members=members)
    for key,buckets in data.items():
        if not key.startswith('recoil-atomAllMessageList_'):continue
        for bucket in _values(buckets):
            if not isinstance(bucket,dict):continue
            for ident,row in selected.items():
                for m in _message_rows(bucket.get(ident,{})):
                    if m.get('messageType')!=1 or m.get('isDeleted') or m.get('isSending') or m.get('isPending'):continue
                    body=m.get('messageBody');number=str(m.get('numberKey') or '')
                    if not isinstance(body,str) or not body.strip() or not number:continue
                    posted=float(m.get('createDate') or 0)/1000
                    if not posted:continue
                    sender=row['_members'].get(m.get('userKey'),{})
                    row['messages'].append(dict(id=number,text=body[:4000],posted=posted,
                        direction='out' if m.get('userKey')==own else 'in',
                        sender='나' if m.get('userKey')==own else str(sender.get('name') or '참여자')))
    budget=0;items=[]
    for row in selected.values():
        row.pop('_members',None);unique={m['id']:m for m in row['messages']}
        row['messages']=sorted(unique.values(),key=lambda m:(m['posted'],m['id']))[-300:]
        row['limited']=len(unique)>300
        budget+=len(json.dumps(row,ensure_ascii=False).encode('utf-8'))
        if budget>1500000:raise ValueError('대화 기록 범위를 확인해 주세요.')
        items.append(row)
    return dict(account=hashlib.sha256((domain+'|'+own).encode()).hexdigest(),rooms=items,
                scope='현재 PC 앱에 캐시된 대화의 최근 최대300개 텍스트 메시지 기준',mode='code',model_calls=0,foreground_ui=False)


def main():
    try:
        p = json.load(sys.stdin)
        if p.get('action')=='chat_snapshot':
            value=chat_snapshot(p.get('titles',[]),p.get('room_ids',[]))
        elif p.get('action')=='pc_read':
            import messenger_read
            value=messenger_read.worker(p['account'],p['room'],p['through'],p['boundary'])
        elif p.get('action') == 'list':
            value = listing(p.get('query', ''), p.get('room', ''), int(p.get('offset', 0)))
        elif p.get('action') == 'receive':
            value = receive(p['ref'], p['destination'], p.get('cancelled', ''))
        elif p.get('action')=='history_metadata':
            refs=p.get('refs',[])
            if not isinstance(refs,list) or len(refs)>200 or any(not isinstance(r,str) or not re.fullmatch('[0-9a-f]{64}',r) for r in refs):
                raise ValueError('조회할 원본 첨부를 확인해 주세요.')
            _,_,rows=_attachments()
            value={'items':[{k:v for k,v in rows[r].items() if not k.startswith('_')} for r in refs if r in rows]}
        elif p.get('action') in {'send_prepare','send_dispatch','send_status','send_confirm'}:
            import messenger_send
            action=p['action']
            if action=='send_prepare':value=messenger_send.prepare(p['id'],p['room'],p['recipient'],p['path'])
            elif action=='send_dispatch':value=messenger_send.dispatch(p['id'])
            elif action=='send_confirm':value=messenger_send.confirm(p['id'])
            else:value=messenger_send.status(p['id'])
        else:
            raise ValueError('첨부 작업을 확인해 주세요.')
        print(json.dumps(dict(ok=True, data=value), ensure_ascii=False))
    except urllib.error.HTTPError as error:
        print(json.dumps(dict(ok=False, error=f'한비로 파일 수신 응답을 확인해 주세요. HTTP {error.code}'), ensure_ascii=False))
    except (ValueError, OSError, ImportError, KeyError, TypeError):
        # Exception reprs can contain URLs, profile values or authentication headers.
        print(json.dumps(dict(ok=False, error='첨부 수신에 필요한 원본 정보·로그인·네트워크를 확인해 주세요.'), ensure_ascii=False))


if __name__ == '__main__':
    main()
