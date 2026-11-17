"""Send through the installed Hanbiro app inside the Office Android guest."""
from contextlib import contextmanager
import re
import socket
import struct
import subprocess
import time
from uuid import uuid4

import nateon_guest
from nateon_file_send import Guest
import task_control


PACKAGE = 'com.hanbiro.globalmessenger_v2'
DOCUMENTS = 'com.google.android.documentsui'
SCRCPY_VERSION = '5.0.1'
EMPTY_EDITOR = {'', 'Aa , @', 'Enter message', '메시지 입력'}


def _nodes(tree, control, package=PACKAGE):
    return [node for node in tree.iter('node')
            if node.get('package') == package and node.get('resource-id') == f'{package}:id/{control}']


def _one(nodes, message):
    if len(nodes) != 1:
        raise ValueError(message)
    return nodes[0]


def _text(node):
    return node.get('text', '').strip()


def _document_titles(tree):
    return [n for n in tree.iter('node') if n.get('package') == DOCUMENTS
            and n.get('resource-id', '').endswith(':id/title')]


def _room(tree, recipient):
    return ([_text(node) for node in _nodes(tree, 'tv_chat_title')] == [recipient]
            and len(_nodes(tree, 'list_view_chat')) == 1)


@contextmanager
def connected():
    nateon_guest.connect()
    # Both messengers share the guest and must not interleave screen actions.
    with nateon_guest._startup_lock(time.monotonic() + 100, 'send.lock'):
        guest = Guest()
        if guest.call('shell', 'getprop', 'ro.boot.qemu.avd_name').strip() != nateon_guest.AVD:
            raise ValueError('오피스 전용 Android 기기가 바뀌었어요.')
        yield guest


def open_room(guest, recipient):
    tree = guest.tree()
    if _room(tree, recipient):
        return tree
    if _nodes(tree, 'tv_chat_title'):
        guest.call('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        tree = guest.tree()
    if not _nodes(tree, 'list_view_chat_list'):
        if not _nodes(tree, 'tab_chat'):
            activity = guest.call('shell', 'cmd', 'package', 'resolve-activity', '--brief',
                                  '-a', 'android.intent.action.MAIN', '-c',
                                  'android.intent.category.LAUNCHER', PACKAGE).strip().splitlines()
            if not activity or not activity[-1].startswith(PACKAGE + '/'):
                raise ValueError('Android 한비로 공식 앱을 확인하지 못했어요.')
            guest.call('shell', 'am', 'start', '-n', activity[-1])
            tree = guest.wait(lambda t: t if _nodes(t, 'tab_chat') or _room(t, recipient) else None,
                              '가상기기 한비로의 로그인이나 대화 목록을 확인해 주세요.')
        if _room(tree, recipient):
            return tree
        if _nodes(tree, 'tv_chat_title'):
            guest.call('shell', 'input', 'keyevent', 'KEYCODE_BACK')
            tree = guest.wait(lambda t: t if _nodes(t, 'tab_chat') else None,
                              '가상기기 한비로 대화 목록으로 돌아가지 못했어요.')
        if not _nodes(tree, 'list_view_chat_list'):
            guest.tap(_one(_nodes(tree, 'tab_chat'), '한비로 채팅 탭을 하나로 확인하지 못했어요.'))
            tree = guest.wait(lambda t: t if _nodes(t, 'list_view_chat_list') else None,
                              '한비로 채팅 목록이 열리지 않았어요.')
    listing = _one(_nodes(tree, 'list_view_chat_list'), '한비로 채팅 목록을 하나로 확인하지 못했어요.')
    rows = [row for row in listing if row.get('resource-id') == PACKAGE + ':id/vg_container'
            and [_text(n) for n in _nodes(row, 'tv_name')] == [recipient]]
    guest.tap(_one(rows, '현재 한비로 채팅 목록에서 지정한 개인방을 하나로 확인하지 못했어요.'))
    return guest.wait(lambda t: t if _room(t, recipient) else None, '지정한 한비로 개인방이 열리지 않았어요.')


def _read_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        block = connection.recv(size - len(data))
        if not block:
            raise OSError('device disconnected')
        data.extend(block)
    return bytes(data)


def _paste(guest, text, check):
    """Use the bundled scrcpy control socket, without the Windows clipboard."""
    scid = uuid4().hex[:8]
    scid = f'{int(scid, 16) & 0x7fffffff:08x}'
    path = f'/data/local/tmp/aemeath-hanbiro-{scid}.jar'
    server = nateon_guest.ROOT / f'scrcpy-win64-v{SCRCPY_VERSION}' / 'scrcpy-server'
    port = ''
    process = None
    try:
        guest.call('push', str(server), path)
        port = guest.call('forward', 'tcp:0', 'localabstract:scrcpy_' + scid).strip()
        if not port.isdecimal():
            raise ValueError('Android 한비로 입력 연결을 열지 못했어요.')
        process = subprocess.Popen(
            [*guest.adb, 'shell', f'CLASSPATH={path}', 'app_process', '/', 'com.genymobile.scrcpy.Server',
             SCRCPY_VERSION, f'scid={scid}', 'tunnel_forward=true', 'video=false', 'audio=false',
             'control=true', 'clipboard_autosync=false', 'send_device_meta=false', 'cleanup=false'],
            env=guest.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=nateon_guest.NO_WINDOW)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline and process.poll() is None:
            task_control.checkpoint()
            try:
                connection = socket.create_connection(('127.0.0.1', int(port)), timeout=0.6)
            except OSError:
                time.sleep(0.15)
                continue
            with connection:
                try:
                    ready = _read_exact(connection, 1) == b'\0'
                except OSError:
                    ready = False
                if ready:
                    check()
                    payload = text.encode('utf-8')
                    connection.settimeout(4)
                    # v5.0.1 SET_CLIPBOARD: type, sequence, paste flag, size, UTF-8.
                    connection.sendall(struct.pack('>BQBI', 9, 1, 1, len(payload)) + payload)
                    if _read_exact(connection, 9) != struct.pack('>BQ', 1, 1):
                        raise ValueError('Android 한비로의 붙여넣기 응답을 확인하지 못했어요.')
                    return
            time.sleep(0.15)
        raise ValueError('Android 한비로의 문자 입력 연결이 응답하지 않았어요.')
    except OSError as error:
        raise ValueError('Android 한비로의 문자 입력 연결이 끊겼어요.') from error
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
        if port.isdecimal():
            guest.call('forward', '--remove', 'tcp:' + port)
        guest.call('shell', 'rm', '-f', path)


def _editor(tree, recipient):
    if not _room(tree, recipient):
        raise ValueError('한비로 개인방이 바뀌었어요.')
    return _one(_nodes(tree, 'edtMessage'), '한비로 메시지 입력칸이 하나로 확인되지 않았어요.')


def draft_text(guest, recipient, text):
    tree = open_room(guest, recipient)
    editor = _editor(tree, recipient)
    if _text(editor) not in EMPTY_EDITOR:
        raise ValueError('한비로 입력칸에 작성 중인 내용이 있어요.')
    guest.tap(editor)

    def check_empty():
        focused = _editor(guest.tree(), recipient)
        if focused.get('focused') != 'true' or _text(focused) not in EMPTY_EDITOR:
            raise ValueError('한비로의 비어 있는 메시지 입력칸이 선택되지 않았어요.')

    _paste(guest, text, check_empty)
    return guest.wait(lambda t: t if _editor(t, recipient).get('text') == text else None,
                      '한비로 입력란의 메시지 내용이 원문과 일치하지 않아요.')


def prepare_file(guest, recipient, path, digest):
    tree = open_room(guest, recipient)
    guest.stage(path, digest)
    more = _one(_nodes(tree, 'kb_imb_more_menu'), '한비로 첨부 메뉴를 확인하지 못했어요.')
    guest.tap(more)
    tree = guest.tree()
    if not _room(tree, recipient):
        raise ValueError('한비로 개인방이 바뀌었어요.')
    guest.tap(_one([n for n in _nodes(tree, 'kb_titleMoreitem') if _text(n) in {'Attach file', '파일 첨부'}],
                   '한비로의 파일 첨부 메뉴를 확인하지 못했어요.'))
    tree = guest.wait(lambda t: t if any(n.get('package') == DOCUMENTS for n in t.iter('node')) else None,
                      'Android 파일 선택기가 열리지 않았어요.')
    if not _download_folder(tree):
        roots = [n for n in tree.iter('node') if n.get('package') == DOCUMENTS
                 and n.get('content-desc') in {'Show roots', '루트 표시'} and n.get('clickable') == 'true']
        guest.tap(_one(roots, 'Android 파일 위치를 선택할 수 없어요.'))
        tree = guest.wait(lambda t: t if _nodes(t, 'roots_list', DOCUMENTS) else None,
                          'Android 파일 위치 목록이 열리지 않았어요.')
        locations = _one(_nodes(tree, 'roots_list', DOCUMENTS), 'Android 파일 위치를 확인하지 못했어요.')
        folders = [n for n in _document_titles(locations) if _text(n) in {'Downloads', '다운로드'}]
        guest.tap(_one(folders, 'Android Downloads 폴더를 하나로 확인하지 못했어요.'))
        tree = guest.wait(lambda t: t if _download_folder(t) else None, 'Android Downloads 폴더가 열리지 않았어요.')
    return tree


def _download_folder(tree):
    return any(_text(n) in {'Downloads', '다운로드'} for control in ('toolbar', 'action_bar')
               for root in _nodes(tree, control, DOCUMENTS) for n in root.iter('node'))


def _file_choice(tree, filename):
    if not _download_folder(tree):
        raise ValueError('확인한 Android Downloads 폴더가 바뀌었어요.')
    return _one([n for n in _document_titles(tree) if _text(n) == filename
                 and n.get('enabled') == 'true'], 'Android 파일 선택기에서 원본 파일명을 하나로 확인하지 못했어요.')


def _match_original_room(tree, original_messages):
    actual = {_text(n) for n in _nodes(tree, 'tc_txt_msg')}
    original = {line.strip() for line in original_messages if len(line.strip()) >= 8}
    if not actual.intersection(original):
        raise ValueError('PC와 Android 한비로의 동일 개인방이라는 기록을 확인하지 못했어요.')


def deliver_text(guest, recipient, text, original_messages, before_send):
    _match_original_room(open_room(guest, recipient), original_messages)
    try:
        tree = draft_text(guest, recipient, text)
        send = _one([n for n in _nodes(tree, 'kb_imb_send') if n.get('enabled') == 'true'
                     and n.get('clickable') == 'true'], '한비로 전송 버튼을 확인하지 못했어요.')
        before_send()
    except Exception:
        editor = _editor(guest.tree(), recipient)
        if editor.get('text') == text and editor.get('focused') == 'true':
            guest.call('shell', 'input', 'keycombination', '113', '29')
            guest.call('shell', 'input', 'keyevent', 'KEYCODE_DEL')
        raise
    guest.tap(send)


def deliver_file(guest, recipient, path, digest, original_messages, before_send):
    _match_original_room(open_room(guest, recipient), original_messages)
    try:
        tree = prepare_file(guest, recipient, path, digest)
        row = _file_choice(tree, path.name)
        # Android may attach and send immediately when a document is selected.
        before_send()
    except Exception:
        for _ in range(2):
            if not any(n.get('package') == DOCUMENTS for n in guest.tree().iter('node')):
                break
            guest.call('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        raise
    guest.tap(row)
