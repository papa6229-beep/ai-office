"""Read-only evidence projection for the existing private brand overview."""
from contextlib import closing
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=9))
_digests = {}


def _epoch(value):
    try:
        number = float(value)
        return number / 1000 if number > 10**11 else number
    except (TypeError, ValueError):
        try:
            return dt.datetime.fromisoformat(value).timestamp()
        except (TypeError, ValueError):
            return 0


def _key(path):
    return os.path.normcase(os.path.abspath(str(path))) if path else ''


def _normal(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def worker_metadata():
    """Runs only in the installed messenger-reader interpreter, without tokens/text."""
    import design_team
    import messenger_attachment_worker as reader
    # The running client can compact blobs while a snapshot is being opened.
    for attempt in range(3):
        try:
            domain, _, store = reader._storage()
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(.15)
    own = store['_aemeath_current_user']
    people = {name for _, name in design_team.MEMBERS}
    rooms = {}
    for key, room in store.get('recoil-atomAllRooms', {}).get('rooms', {}).items():
        members = room.get('members', {})
        peers = [member for key, member in members.items() if key != own]
        if (not room.get('leftRoom') and own in members and len(members) == 2
                and len(peers) == 1 and peers[0].get('name') in people):
            rooms[key] = (peers[0]['name'], members)
    found = {}
    for key, buckets in store.items():
        if not key.startswith('recoil-atomAllMessageList_'):
            continue
        for bucket in reader._values(buckets):
            if not isinstance(bucket, dict):
                continue
            for room, (person, members) in rooms.items():
                for message in reader._message_rows(bucket.get(room, {})):
                    detail = message.get('messageDetail') or {}
                    if not isinstance(detail, dict):
                        continue
                    if (message.get('isDeleted') or message.get('isSending')
                            or message.get('isPending') or not detail.get('fileName')
                            or not detail.get('httpServerFilePath') or not detail.get('httpServerFileName')):
                        continue
                    sender = message.get('userKey')
                    if sender not in members:
                        continue
                    try:
                        size = int(detail.get('fileSize') or 0)
                    except (ValueError, TypeError):
                        continue
                    if not 0 < size <= reader.MAX_SIZE:
                        continue
                    number = str(message.get('numberKey') or '')
                    identity = [domain, own, str(room), number, sender,
                                detail['fileName'], size, detail['httpServerFilePath'],
                                detail['httpServerFileName'], detail.get('httpHostName', '')]
                    ref = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
                    found[(room, number, ref)] = dict(
                        ref=ref, room=room, person=person, direction='out' if sender == own else 'in',
                        name=detail['fileName'], size=size, message_id=number,
                        posted=_epoch(message.get('createDate')))
    if len(found) > 5000:
        raise ValueError('브랜드 자료 대조 범위를 확인해 주세요.')
    return {'items': list(found.values()), 'mode': 'code', 'model_calls': 0}


def _resolve(path, moves, since=0):
    current = Path(path)
    for stamp, source, destination, operation, _ in moves:
        if stamp < since:
            continue
        try:
            relative = current.relative_to(Path(source))
        except ValueError:
            continue
        if operation == 'delete':
            return None
        current = Path(destination) / relative
    return current


def verify_attachment(ref, expected_size, expected_sha256):
    """Reconcile a changed cache ref with actual bytes; no saved file or send."""
    import messenger_attachment_worker as reader
    if (not re.fullmatch('[0-9a-f]{64}', str(ref))
            or not re.fullmatch('[0-9a-f]{64}', str(expected_sha256))
            or not isinstance(expected_size, int) or not 0 < expected_size <= reader.MAX_SIZE):
        raise ValueError('대조할 첨부 정보를 확인해 주세요.')
    domain, token, attachments = reader._attachments()
    attachment = attachments.get(ref)
    if not attachment or attachment['direction'] != 'in' or attachment['size'] != expected_size:
        return {'ref': ref, 'matches': False}
    detail = attachment['_detail']
    host = (detail.get('httpHostName') or domain).strip().lower()
    if host != domain:
        raise ValueError('현재 회사의 원본 첨부만 대조해요.')
    headers = {'Content-Type': 'xml', 'HANBIRO-MODE': 'get',
               'HANBIRO-FILENAME': urllib.parse.quote(attachment['name'], safe=''),
               'HANBIRO-SERVERPATH': detail['httpServerFilePath'],
               'HANBIRO-SERVERFILENAME': detail['httpServerFileName'],
               'HANBIRO-LOGINKEY': token, 'HANBIRO-SENDER': attachment['_sender']}
    request = urllib.request.Request('https://' + host + '/winapp/hcsong/messenger_file.php',
                                     data=b'', headers=headers, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), reader._NoRedirect())
    digest = hashlib.sha256()
    size = 0
    with opener.open(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError('원본 첨부를 확인하지 못했어요.')
        for chunk in iter(lambda: response.read(1024 * 1024), b''):
            size += len(chunk)
            if size > expected_size:
                return {'ref': ref, 'matches': False}
            digest.update(chunk)
    return {'ref': ref, 'matches': size == expected_size and digest.hexdigest() == expected_sha256}


def _digest(path):
    stat = path.stat()
    key = (_key(path), stat.st_size, stat.st_mtime_ns)
    if key not in _digests:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        if len(_digests) >= 4096:
            _digests.clear()
        _digests[key] = digest.hexdigest()
    return _digests[key]


def _read_json(path):
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('자료 영수증을 확인해 주세요.')
    return json.loads(path.read_text('utf-8'))


def _batch_name(path):
    match = re.match(r'^(20\d{2})[._-](\d{2})[._-](\d{2})[ _]+(.+)$', path.stem)
    if not match:
        return None
    try:
        day = dt.date(*map(int, match.group(1, 2, 3))).isoformat()
    except ValueError:
        return None
    brand = re.split(r'[_ ]샘플\s*가격', match[4], maxsplit=1)[0].strip(' _')
    return (day, brand) if brand else None


def reconcile(db, data, materials_root, metadata, warnings, discovery_since):
    """Project actual receipts; never send, move, or modify the original seed."""
    import file_history
    rows = data.setdefault('brands', [])
    events = data.setdefault('events', [])
    people = {name for _, name in __import__('design_team').MEMBERS}
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        moves = file_history._moves(connection)
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        downloads = [dict(r) for r in connection.execute(
            "SELECT * FROM messenger_downloads WHERE state='downloaded'")] if 'messenger_downloads' in tables else []
    allowed = [(materials_root / name).resolve() for name in
               ('작업용파일_전달예정', '작업용파일_전달완료')]
    metadata = metadata.get('items', [])
    sent = [r for r in metadata if r['direction'] == 'out']
    incoming = {r['ref']: r for r in metadata if r['direction'] == 'in'}
    folders = {}
    for row in [*rows, *events]:
        if not row.get('folder'):
            continue
        current = _resolve(row['folder'], moves)
        if current and any(current.is_relative_to(root) for root in allowed):
            row['folder'] = str(current)
            folders[_key(current)] = current
    durable = []
    for receipt in (ROOT / '.provider_runtime/messenger/outgoing').glob('*.json'):
        try:
            record = _read_json(receipt)
        except (OSError, ValueError):
            warnings.append('읽지 못한 발신 영수증은 현황 자동 연결에서 제외했어요.')
            continue
        if record.get('state') != 'sent' or record.get('recipient') not in people:
            continue
        current = _resolve(record['path'], moves, record.get('started', 0))
        if (not current or not current.is_file() or current.is_symlink()
                or not any(current.is_relative_to(root) for root in allowed)):
            continue
        if current.stat().st_size != record['size'] or _digest(current) != record['sha256']:
            warnings.append('발신 후 내용이 달라진 자료는 현황 자동 연결에서 제외했어요.')
            continue
        durable.append(dict(path=str(current), person=record['recipient'], name=record['name'],
                            size=record['size'], posted=record.get('started', 0),
                            message_id=record.get('message_id', ''), receipt_id=record['id']))
        if record.get('started', 0) >= discovery_since:
            parent = current.parent
            # The registered material layout is region / dated batch / files.
            if parent.parent.parent in allowed:
                folders[_key(parent)] = parent
    for root in allowed[:1]:
        if root.is_dir():
            for parent in root.glob('*/*'):
                if parent.is_dir() and not parent.is_symlink():
                    folders[_key(parent)] = parent
    folder_files = {}
    filename_folders = {}
    for key, parent in folders.items():
        if not parent.is_dir() or parent.is_symlink():
            continue
        files = [p for p in parent.iterdir() if p.is_file() and not p.is_symlink()
                 and not p.name.startswith(('~$', '.'))]
        folder_files[key] = files
        for path in files:
            filename_folders.setdefault((path.name, path.stat().st_size), set()).add(key)
    for key, parent in folders.items():
        matches = [row for row in [*rows, *events] if _key(row.get('folder')) == key]
        if matches:
            continue
        files = folder_files.get(key, [])
        if not files:
            continue
        day, separator, label = parent.name.partition(' ')
        if not separator or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', day):
            continue
        is_event = any(word in label for word in ('캠페인', '이벤트')) or any(p.suffix == '.eventbrief' for p in files)
        if is_event:
            events.append(dict(name=label, batch=day, person='미배정', folder=str(parent),
                               delivery='전달 미확인', status='착수·완료 미확인',
                               note='자료 기록으로 확인한 별도 업무'))
        else:
            rows.append(dict(brand=label, batch=day, person='미배정', folder=str(parent),
                             delivery='전달대기(확인한 기록 기준)', work_status='대기',
                             image_source='폴더 자료 확인', note='자료 기록으로 확인한 배정분'))
    for row in [*rows, *events]:
        key = _key(row.get('folder'))
        files = folder_files.get(key, [])
        evidence = [r for r in durable if _key(Path(r['path']).parent) == key]
        known = {r.get('message_id') for r in evidence}
        for path in files:
            match_key = (path.name, path.stat().st_size)
            if len(filename_folders.get(match_key, ())) != 1:
                continue
            evidence.extend(r for r in sent if (r['name'], r['size']) == match_key
                            and r.get('message_id') not in known)
        persons = {r['person'] for r in evidence}
        if not evidence:
            continue
        if len(persons) != 1 or row.get('person') not in {'미배정', '미확인', None, '', *persons}:
            row['delivery_warning'] = '자료 수신자와 기존 담당이 하나로 일치하지 않아 자동 변경하지 않았어요.'
            continue
        row.pop('delivery_warning', None)
        latest = max(evidence, key=lambda r: r['posted'])
        row.update(person=latest['person'], delivery='전달 확인 ' + dt.datetime.fromtimestamp(
            latest['posted'], TZ).strftime('%m/%d %H:%M'))
        row['delivery_evidence'] = [dict(name=r['name'], size=r['size'], message_id=r.get('message_id'),
                                         person=r['person'], posted=r['posted']) for r in evidence]
        row['sent_message_ids'] = sorted(set(row.get('sent_message_ids', [])) |
                                         {r['message_id'] for r in evidence if r.get('message_id')})
        row['sent_times'] = sorted({dt.datetime.fromtimestamp(r['posted'], TZ).strftime('%m/%d %H:%M') for r in evidence})
        if row in rows and row.get('work_status') in {'대기', '미확인', '확인 필요'}:
            row['work_status'] = '착수·완료 미확인'
    for download in downloads:
        receipt = json.loads(download['data'])
        source = incoming.get(download['ref'])
        current = _resolve(receipt.get('path', ''), moves, download['updated'])
        if (not current or current.suffix.lower() not in {'.xlsx', '.xls', '.xlsm'}
                or not current.is_file() or current.is_symlink()):
            continue
        owners = [person for person in people if current.is_relative_to(materials_root / person / '완료파일')]
        if len(owners) != 1:
            continue
        batch = _batch_name(current)
        if not batch or current.stat().st_size != receipt.get('size') or _digest(current) != receipt.get('sha256'):
            continue
        day, name = batch
        if not source:
            candidates = [r for r in incoming.values() if r['person'] == owners[0]
                          and r['name'] == current.name and r['size'] == receipt['size']
                          and r['posted'] <= download['updated'] + 10]
            if len(candidates) != 1:
                continue
            source = candidates[0]
            prior = [r.get('completion_evidence', {}) for r in rows]
            verified = any(e.get('source_ref') == source['ref']
                           and e.get('ref') == download['ref'] and e.get('sha256') == receipt['sha256']
                           for e in prior)
            if not verified:
                import messenger_attachments
                verified = messenger_attachments._call(dict(action='brand_status_verify',
                    ref=source['ref'], size=receipt['size'], sha256=receipt['sha256'])).get('matches')
            if not verified:
                warnings.append('식별자가 바뀐 완료파일의 원본 내용이 일치하지 않아 자동 연결하지 않았어요.')
                continue
        if source['person'] != owners[0]:
            continue
        matches = [r for r in rows if _normal(r.get('brand', '')) == _normal(name)
                   and r.get('batch') == day and r.get('person') == source['person']
                   and _epoch(r.get('start_date')) <= source['posted']]
        if len(matches) != 1:
            continue
        row = matches[0]
        received_day = dt.datetime.fromtimestamp(source['posted'], TZ).date().isoformat()
        row.update(completed_workbook=str(current), completion_basis='담당자 완료파일 수신·정리 확인',
                   completion_received=received_day)
        row['completion_evidence'] = dict(ref=download['ref'], source_ref=source['ref'], sender=source['person'],
                                          message_id=source['message_id'], sha256=receipt['sha256'])
        if row.get('report_day', '') and row['report_day'] >= received_day and row.get('work_status') != '완료':
            row['completion_warning'] = '완료파일 수신과 같은 날 또는 이후 보고서 상태가 달라 확인이 필요해요.'
            continue
        row.pop('completion_warning', None)
        row.update(work_status='완료', completed=received_day)
    data.setdefault('coverage', {}).update(evidence_reconciled=True, discovery_since=discovery_since,
                                          native_attachment_records=len(metadata))
