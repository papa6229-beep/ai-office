"""Bind an explicit Office file request to the existing messenger adapters."""

from contextlib import closing
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import time


FILE = r'[“‘"\'](?P<file>[^”’"\'\r\n]{1,600})[”’"\']|(?P<bare_file>[^\s“”‘’"\'|]{1,200}\.[A-Za-z0-9]{1,10})'
SENDER = r'(?:(?P<sender>[가-힣]{2,10}?)\s*(?:사원|님)?\s*(?:이|가)\s*보낸\s+)?'
TARGET = (r'(?P<target>네이트온|한비로톡|한비로)\s+(?:상품팀\s+)?'
          r'(?:[“‘"\'](?P<room>[^”’"\'\r\n]{1,160})[”’"\']|(?P<person>[가-힣]{2,10}?)(?:님)?)'
          r'\s*(?:단체방|개인방)?\s*(?:에게|한테|으로|에)\s*')
SEND = r'(?:보내|전송해|전달해)\s*(?:줘|주세요|주라)?'
DOWNLOAD = r'(?:다운로드해|내려받아|받아)\s*(?:줘|주세요|주라)?'


def match(text):
    file_part = r'(?:' + FILE + r')\s*(?:한\s*개|파일)?\s*(?:을|를)?\s*'
    incoming = r'한비로(?:톡)?(?:에서)?\s+' + SENDER + file_part
    request = re.fullmatch(incoming + r'(?:받아(?:서)?|내려받아(?:서)?|다운로드해(?:서)?)\s+' + TARGET + SEND, text)
    source = 'hanbiro'
    if request is None:
        request = re.fullmatch(incoming + DOWNLOAD, text)
    if request is None:
        source = 'saved'
        owner = r'(?:(?P<sender>[가-힣]{2,10}?)\s*(?:사원|님)?의\s+)?'
        request = (re.fullmatch(owner + file_part + TARGET + SEND, text)
                   or re.fullmatch(TARGET + owner + file_part + SEND, text))
    if request is None:
        return None

    fields = request.groupdict()
    name = (fields.get('file') or fields['bare_file']).strip()
    path = PureWindowsPath(name)
    if not path.suffix or (name != path.name and (source != 'saved' or not re.fullmatch(r'[A-Za-z]:[\\/].+', name))):
        return None
    target = fields.get('target') or ''
    recipient = (fields.get('room') or fields.get('person') or '').strip()
    if target == '네이트온' and re.fullmatch(r'[가-힣]+(?:\s*[,·]\s*[가-힣]+)+\s*\(\d+명\)', recipient):
        recipient = recipient.replace('·', ',')
    if target and not recipient:
        return None
    return {'source': source, 'file': name, 'sender': fields.get('sender') or '',
            'target': {'네이트온': 'nateon', '한비로': 'hanbiro', '한비로톡': 'hanbiro', '': ''}[target],
            'recipient': recipient}


def _saved_file(db, name, sender):
    import file_history
    import file_operations

    explicit_path = None
    if re.match(r'[A-Za-z]:[\\/]', name):
        explicit_path = file_operations._guard(Path(name), source=True)
        if not sender:
            return explicit_path, ''
        name = explicit_path.name
    rows = file_history._all(db, lambda row: row['name'].casefold() == name.casefold()
                             and (not sender or row.get('person') == sender))
    paths = {}
    for row in rows:
        if row['available']:
            path = file_operations._guard(Path(row['current_path']), source=True)
            if explicit_path is not None and path != explicit_path:
                continue
            paths.setdefault(path, set())
            if row.get('sha256'):
                paths[path].add(row['sha256'])
    if len(paths) != 1:
        raise ValueError('기록에서 같은 이름의 파일을 하나로 확인할 수 없어요. 실제 파일 경로를 지정해 주세요.')
    path, hashes = next(iter(paths.items()))
    if len(hashes) > 1:
        raise ValueError('같은 위치에 서로 다른 파일 기록이 있어요. 현재 보낼 파일을 확인해 주세요.')
    return path, next(iter(hashes), '')


def _receive(db, request_id, name, sender):
    import messenger_attachments as attachments
    import office_fastlane
    import task_control

    listing = attachments.listing('hanbiro', name)
    matches = [item for item in listing['items'] if item['name'].casefold() == name.casefold()
               and (not sender or item['sender'] == sender)]
    if listing['total'] > len(listing['items']) or len(matches) != 1:
        raise ValueError('한비로에서 해당 이름의 받은 첨부를 하나로 확인하지 못했어요. 보낸 사람과 파일명을 확인해 주세요.')
    office_fastlane.require_active_request(db, request_id)
    job = attachments.download(db, matches[0]['ref'])
    deadline = time.monotonic() + 350
    try:
        while time.monotonic() < deadline:
            task_control.checkpoint()
            receipt = attachments.status(db, job['id'])
            if receipt['state'] == 'downloaded':
                return receipt
            if receipt['state'] not in {'queued', 'downloading'}:
                raise ValueError(receipt.get('error') or '한비로 파일의 저장 완료를 확인하지 못했어요.')
            time.sleep(0.5)
    except task_control.Stopped:
        if not job.get('reused'):
            attachments.cancel(db, job['id'])
        raise
    raise ValueError('한비로 파일 수신이 진행 중이에요. 작업 번호 ' + job['id'] + '의 저장 결과를 확인해 주세요.')


def _authorize_send(db, request_id):
    import office_fastlane

    office_fastlane.require_active_request(db, request_id)
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        if connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', ('assistant_inputs',)).fetchone():
            newer = connection.execute(
                "SELECT 1 FROM assistant_inputs WHERE request_id=? AND kind='message' AND state='queued' LIMIT 1",
                (request_id,)).fetchone()
            if newer:
                raise ValueError('새 사용자 지시가 도착했어요. 보내기 전에 최신 지시를 확인해 주세요.')


def run(db, request_id, plan):
    import office_fastlane
    import task_control

    office_fastlane.require_active_request(db, request_id)
    download = None
    if plan['source'] == 'hanbiro':
        download = _receive(db, request_id, plan['file'], plan['sender'])
        path, digest = Path(download['path']), download['sha256']
    else:
        path, digest = _saved_file(db, plan['file'], plan['sender'])
    if not plan['target']:
        return download

    def authorize():
        _authorize_send(db, request_id)

    recipient = plan['recipient']
    try:
        authorize()
        if plan['target'] == 'nateon':
            import nateon_file_send
            result = nateon_file_send.send_file(request_id, recipient, path, expected_sha256=digest, authorize=authorize)
        else:
            import messenger_send
            room = messenger_send.private_room(recipient)
            messenger_send.prepare(request_id, room, recipient, path, expected_sha256=digest)
            result = messenger_send.dispatch(request_id, authorize=authorize)
            deadline = time.monotonic() + 12
            while result['state'] == 'awaiting_confirmation' and time.monotonic() < deadline:
                task_control.checkpoint()
                time.sleep(0.5)
                result = messenger_send.confirm(request_id)
    except (OSError, ValueError) as error:
        if download is None:
            raise
        return {'state': 'send_unconfirmed', 'download_id': download['id'], 'download_path': str(path),
                'send_error': str(error) if isinstance(error, ValueError) else '발신 연결 확인이 필요해요.'}
    if download:
        result['download_id'] = download['id']
        result['download_path'] = str(path)
    return result
