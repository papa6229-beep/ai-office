"""Send one explicitly selected file using Android Files and the official NateOn app."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

import file_operations
import nateon_guest
import task_control


FORMATS = {'.xlsx', '.xls', '.csv', '.pdf', '.docx', '.pptx', '.zip', '.txt', '.jpg', '.jpeg', '.png'}
FILE_APP = 'com.google.android.documentsui'
NATE = nateon_guest.PACKAGE
_OCR = r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-Ocr($operation,[Type]$type) {
    $task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation))
    $task.GetAwaiter().GetResult()
}
$file=Await-Ocr ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:AEMEATH_NATE_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream=Await-Ocr ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder=Await-Ocr ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Await-Ocr ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko'))
if(!$engine){throw 'Korean OCR is unavailable'}
$result=Await-Ocr ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$rows=@(foreach($line in $result.Lines){
    $word=$line.Words | Select-Object -First 1
    if(!$word){continue}
    $rect=$word.BoundingRect
    @{text=$line.Text;x=[int]($rect.X+$rect.Width/2);y=[int]($rect.Y+$rect.Height/2)}
})
ConvertTo-Json -InputObject $rows -Compress
'''


def _normalized(text):
    return re.sub(r'\s+', '', text or '').casefold()


class Guest:
    def __init__(self):
        root = nateon_guest.ROOT
        self.adb = [str(root / 'sdk' / 'platform-tools' / 'adb.exe'), '-s', nateon_guest.SERIAL]
        self.env = dict(os.environ, ANDROID_USER_HOME=str(root / 'android-home'))
        self.dump_path = '/data/local/tmp/aemeath-nateon-' + uuid4().hex + '.xml'

    def call(self, *args, data=None, timeout=12, binary=False):
        task_control.checkpoint()
        try:
            result = subprocess.run([*self.adb, *args], input=data, env=self.env, capture_output=True,
                                    timeout=timeout, creationflags=nateon_guest.NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('네이트온 전용 기기 동작을 확인하지 못했어요.') from error
        if result.returncode:
            raise ValueError('네이트온 전용 기기에서 명령이 실패했어요.')
        return result.stdout if binary else result.stdout.decode('utf-8', 'replace')

    def tree(self):
        try:
            self.call('shell', 'uiautomator', 'dump', self.dump_path)
            raw = self.call('shell', 'cat', self.dump_path)
            return ET.fromstring(raw)
        except (ValueError, ET.ParseError):
            return ET.Element('unavailable')
        finally:
            # UI snapshots stay in the guest and are removed after each read.
            self.call('shell', 'rm', '-f', self.dump_path)

    def wait(self, find, message, seconds=12):
        deadline = time.monotonic() + seconds
        while True:
            value = find(self.tree())
            if value is not None:
                return value
            if time.monotonic() >= deadline:
                raise ValueError(message)
            time.sleep(0.25)

    def tap(self, node, *, hold=False):
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
        if not match:
            raise ValueError('네이트온 화면의 조작 위치를 확인하지 못했어요.')
        x1, y1, x2, y2 = map(int, match.groups())
        if x1 >= x2 or y1 >= y2:
            raise ValueError('네이트온 화면의 조작 위치를 확인하지 못했어요.')
        point = (str((x1 + x2) // 2), str((y1 + y2) // 2))
        if hold:
            self.call('shell', 'input', 'swipe', *point, *point, '650')
        else:
            self.call('shell', 'input', 'tap', *point)

    def stage(self, path, digest):
        destination = '/sdcard/Download/' + path.name
        target = shlex.quote(destination)
        check = f'if test -f {target}; then sha256sum {target}; fi\n'
        existing = self.call('shell', '-T', 'sh', '-s', data=check.encode())
        if existing:
            if existing.split()[0] != digest:
                raise ValueError('시험 기기에 이름이 같은 다른 파일이 있어요.')
            return
        temporary = '/sdcard/Download/.aemeath-' + uuid4().hex
        self.call('push', str(path), temporary, timeout=180)
        cmd = f'set -e\nmv -n {temporary} {target}\nsha256sum {target}\n'
        saved = self.call('shell', '-T', 'sh', '-s', data=cmd.encode())
        if not saved.split() or saved.split()[0] != digest:
            raise ValueError('전송용 파일이 원본과 일치하지 않아요.')

    def room_from_pixels(self, room):
        count = re.search(r'\((\d+)명\)$', room)
        title = room[:count.start()] + ' ' + count[1] if count else room
        image = self.call('exec-out', 'screencap', '-p', binary=True)
        with tempfile.NamedTemporaryFile(dir=nateon_guest.ROOT, suffix='.png', delete=False) as stream:
            stream.write(image)
            path = Path(stream.name)
        try:
            encoded = base64.b64encode(_OCR.encode('utf-16-le')).decode('ascii')
            process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                     env=dict(os.environ, AEMEATH_NATE_OCR_IMAGE=str(path)), capture_output=True,
                                     timeout=10, creationflags=nateon_guest.NO_WINDOW)
            if process.returncode:
                raise ValueError('가상기기 대화 목록의 글자를 읽지 못했어요.')
            rows = json.loads(process.stdout.decode('utf-8-sig'))
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError) as error:
            raise ValueError('가상기기 대화 목록의 글자를 읽지 못했어요.') from error
        finally:
            path.unlink(missing_ok=True)
        matches = [row for row in rows if _normalized(row['text']) == _normalized(title)]
        if len(matches) != 1:
            raise ValueError('가상기기 화면에서 지정된 방 하나를 확인하지 못했어요.')
        row = matches[0]
        self.call('shell', 'input', 'tap', str(row['x']), str(row['y']))


def _id(tree, value):
    return next((node for node in tree.iter('node') if node.get('resource-id') == value), None)


def _text(tree, value, package):
    return next((node for node in tree.iter('node') if node.get('package') == package and
                 value in (node.get('text'), node.get('content-desc'))), None)


def _room(tree, room, *, selected=False):
    name = re.sub(r'\s*\(\d+명\)$', '', room)
    count = re.search(r'\((\d+)명\)$', room)
    matches = [node for node in tree.iter('node') if node.get('resource-id') == NATE + ':id/msgroom_title'
               and _normalized(node.get('text')) == _normalized(name)]
    if len(matches) > 1:
        raise ValueError('네이트온에서 같은 이름의 방이 여러 개 보여요.')
    if not matches:
        return None
    node = matches[0]
    parents = {child: parent for parent in tree.iter() for child in parent}
    row = parents[node]
    while row in parents and _id(row, NATE + ':id/msgroom_profile') is None:
        row = parents[row]
    if count:
        extra = _id(row, NATE + ':id/msgroom_extra')
        if extra is None or extra.get('text', '').strip() != count[1]:
            raise ValueError('네이트온 단체방의 참여 인원이 요청과 일치하지 않아요.')
    if selected:
        checks = [child for child in tree.iter('node') if child.get('resource-id') == NATE + ':id/item_selector'
                  and child.get('selected') == 'true']
        if len(checks) != 1 or checks[0] not in list(row.iter()):
            raise ValueError('선택된 네이트온 수신자가 요청과 일치하지 않아요.')
    return node


def _room_from_list(guest, room):
    for _ in range(7):
        tree = guest.tree()
        node = _room(tree, room)
        if node is not None:
            return node
        listing = _id(tree, NATE + ':id/message_room_list')
        if listing is None or listing.get('scrollable') != 'true':
            break
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', listing.get('bounds', ''))
        if not match:
            break
        left, top, right, bottom = map(int, match.groups())
        x = str((left + right) // 2)
        guest.call('shell', 'input', 'swipe', x, str(bottom - 160), x, str(top + 140), '350')
    raise ValueError('지정된 네이트온 방을 목록에서 정확히 찾지 못했어요.')


def _open_chat(guest, room):
    launcher = guest.call('shell', 'cmd', 'package', 'resolve-activity', '--brief', '-a',
                          'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', '-p', NATE)
    entries = launcher.strip().splitlines()
    if not entries or not re.fullmatch(re.escape(NATE) + r'/[A-Za-z0-9_.$]+', entries[-1]):
        raise ValueError('공식 네이트온 실행 화면을 찾지 못했어요.')
    guest.call('shell', 'am', 'start', '-n', entries[-1])
    tree = guest.wait(lambda t: t if (_id(t, NATE + ':id/tab_main') is not None or
                                    _id(t, NATE + ':id/msg_title_back_btn') is not None) else None,
                      '네이트온 대화 목록을 열 수 없어요. 로그인 상태를 확인해야 해요.')
    back = _id(tree, NATE + ':id/msg_title_back_btn')
    if back is not None:
        guest.tap(back)
        tree = guest.wait(lambda t: t if _id(t, NATE + ':id/tab_main') is not None else None,
                          '네이트온 목록으로 돌아가지 못했어요.')
    if _id(tree, NATE + ':id/message_room_list') is None:
        tab = _text(tree, '메시지', NATE)
        if tab is None:
            raise ValueError('네이트온 메시지 탭을 찾지 못했어요.')
        guest.tap(tab)
        tree = guest.tree()
    if _id(tree, NATE + ':id/message_room_list') is not None:
        guest.tap(_room_from_list(guest, room))
    elif _id(tree, NATE + ':id/tab_main') is not None:
        guest.room_from_pixels(room)
    else:
        raise ValueError('네이트온 메시지 목록이 열리지 않았어요.')
    return guest.wait(lambda t: t if _id(t, NATE + ':id/msg_title_back_btn') is not None else None,
                      '지정한 네이트온 대화가 열리지 않았어요.')


def _outgoing(tree, filename):
    return sum(1 for node in tree.iter('node') if node.get('resource-id') == NATE + ':id/send_file_name'
               and node.get('text') == filename)


def send_file(request_id, room, path, *, verify_only=False, expected_sha256='', authorize=None):
    if not re.fullmatch('[a-f0-9]{32}', request_id):
        raise ValueError('현재 전송 요청 ID를 확인해 주세요.')
    path = file_operations._guard(Path(path), source=True)
    if not path.is_file() or path.suffix.casefold() not in FORMATS or len(path.name) > 180:
        raise ValueError('실제로 존재하는 지원 업무 파일 한 개를 지정해 주세요.')
    with path.open('rb') as stream:
        hasher = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
            task_control.checkpoint()
    digest = hasher.hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError('파일 내용이 수신 또는 파일 이력의 확인값과 달라요.')
    receipts = nateon_guest.ROOT / 'outgoing'
    receipt = receipts / (request_id + '.json')
    started_at = time.monotonic()
    nateon_guest.connect()
    with nateon_guest._startup_lock(time.monotonic() + 100, 'send.lock'):
        if not verify_only and receipt.exists():
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if saved.get('sha256') == digest and saved.get('room') == room and saved.get('state') == 'sent':
                return saved
            raise ValueError('이 요청의 발신을 이미 시도했어요. 기존 발신 기록부터 확인해야 해요.')
        guest = Guest()
        before = _outgoing(_open_chat(guest, room), path.name)
        guest.stage(path, digest)
        guest.call('shell', 'am', 'start', '-a', 'android.intent.action.VIEW_DOWNLOADS')
        row = guest.wait(lambda t: _text(t, path.name, FILE_APP), 'Android 파일 목록에서 원본 파일명을 찾지 못했어요.')
        guest.tap(row, hold=True)
        share = guest.wait(lambda t: _id(t, FILE_APP + ':id/action_menu_share'), '선택한 파일의 공유 버튼을 찾지 못했어요.')
        guest.tap(share)
        app = guest.wait(lambda t: next((node for node in t.iter('node')
                                         if node.get('resource-id') == 'android:id/text1'
                                         and node.get('package') in {'android', 'com.android.intentresolver'}
                                         and node.get('text') in {'NateOn', '네이트온'}), None),
                         'Android 공유에서 네이트온을 찾지 못했어요.')
        guest.tap(app)
        guest.wait(lambda t: _id(t, NATE + ':id/message_room_list'), '네이트온 수신자 목록이 열리지 않았어요.')
        guest.tap(_room_from_list(guest, room))
        tree = guest.tree()
        _room(tree, room, selected=True)
        confirm = _id(tree, NATE + ':id/btn_confirm')
        if confirm is None:
            raise ValueError('네이트온에서 선택한 수신자를 확인할 수 없어요.')
        guest.tap(confirm)
        dialog = guest.wait(lambda t: t if _text(t, '전달하시겠습니까?', NATE) is not None else None,
                            '네이트온 파일 전달 확인창을 확인하지 못했어요.')
        if verify_only:
            cancel = _id(dialog, 'android:id/button2')
            if cancel is None or cancel.get('text') != '아니오':
                raise ValueError('시험 확인창의 취소 버튼을 찾지 못했어요.')
            guest.tap(cancel)
            close = guest.wait(lambda t: _id(t, NATE + ':id/title_navigate_close'), '공유 화면을 닫지 못했어요.')
            guest.tap(close)
            return {'state': 'ready_to_send', 'filename': path.name, 'room': room, 'sent': False,
                    'elapsed_ms': round((time.monotonic() - started_at) * 1000)}
        yes = _id(dialog, 'android:id/button1')
        if yes is None or yes.get('text') != '예':
            raise ValueError('네이트온에서 최종 발신 버튼을 확인하지 못했어요.')
        task_control.checkpoint()
        if authorize is not None:
            authorize()
        receipts.mkdir(exist_ok=True)
        result = {'state': 'attempted', 'room': room, 'filename': path.name, 'sha256': digest}
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        guest.tap(yes)
        guest.wait(lambda t: t if _id(t, NATE + ':id/buddy_select_tab_layout') is None and
                   _id(t, 'android:id/button1') is None else None, '네이트온 발신 결과를 기다리고 있어요. 다시 보내지 않았어요.')
        _open_chat(guest, room)
        guest.wait(lambda t: t if _outgoing(t, path.name) > before else None,
                   '새 첨부 발신 기록을 아직 확인하지 못했어요. 다시 보내지 않았어요.')
        result.update(state='sent', sent=True, confirmed=time.time(), path=str(path), elapsed_ms=round((time.monotonic() - started_at) * 1000))
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        temporary.replace(receipt)
        import brand_work_events
        brand_work_events.safe_capture('nateon-sent:'+request_id,'sent',result['confirmed'],data=dict(source='nateon',name=path.name,current_path=str(path),status='confirmed',room=room))
        return result
