"""Shallow, read-only Windows desktop inventory and explicit click actions."""
import datetime
import hashlib
import io
import os
import pathlib
import stat
import subprocess
import sys
import warnings

from scanner import PROTECTED_EXT, _category

MAX_ITEMS = 2000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}
BLOCKED_EXTENSIONS = PROTECTED_EXT | {
    '.com', '.scr', '.pif', '.cpl', '.hta', '.vbs', '.vbe', '.js', '.jse',
    '.wsf', '.wsh', '.psm1', '.psd1', '.reg', '.py', '.pyw', '.rb', '.sh',
    '.jar', '.application', '.appref-ms', '.scf', '.chm', '.html', '.htm',
}


def _desktop_roots():
    user = pathlib.Path.home() / 'Desktop'
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                value, _ = winreg.QueryValueEx(key, 'Desktop')
                user = pathlib.Path(os.path.expandvars(value))
        except (OSError, ImportError, TypeError):
            pass
    roots = [user]
    public = pathlib.Path(os.environ.get('PUBLIC', r'C:\Users\Public')) / 'Desktop'
    if sys.platform == 'win32' and public.is_dir():
        roots.append(public)
    return roots


def _canonical(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _identifier(path):
    return hashlib.sha256(_canonical(path).encode('utf-8')).hexdigest()


def _linked(path, info=None):
    info = info or path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))


def _safe_route(path):
    # Revalidate all parents so a replaced directory cannot redirect a click.
    for part in (path, *path.parents):
        if _linked(part):
            return False
    return True


def inventory():
    items, roots, messages, seen = [], [], [], set()
    for supplied in _desktop_roots():
        root = pathlib.Path(supplied).absolute()
        key = _canonical(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not root.is_dir():
                messages.append('바탕화면 폴더를 찾을 수 없습니다: ' + str(root))
                continue
            if not _safe_route(root):
                messages.append('링크 폴더는 지원하지 않습니다: ' + str(root))
                continue
            roots.append(str(root))
            with os.scandir(root) as entries:
                for entry in entries:
                    if len(items) >= MAX_ITEMS:
                        messages.append('바탕화면 항목은 최대 2,000개까지 표시합니다.')
                        return {'roots': roots, 'items': items, 'warnings': messages, 'total': len(items)}
                    path = pathlib.Path(entry.path)
                    try:
                        info = path.lstat()
                        attributes = getattr(info, 'st_file_attributes', 0)
                        hidden_or_system = attributes & (
                            getattr(stat, 'FILE_ATTRIBUTE_HIDDEN', 2) |
                            getattr(stat, 'FILE_ATTRIBUTE_SYSTEM', 4))
                        if hidden_or_system or path.name.startswith('.') or path.name.casefold() == 'desktop.ini':
                            continue
                        linked = _linked(path, info)
                        extension = path.suffix.lower()
                        folder = stat.S_ISDIR(info.st_mode)
                        kind = 'folder' if folder else 'app' if extension in {'.lnk', '.url'} else 'file'
                        openable = not linked and (folder or stat.S_ISREG(info.st_mode)) and (
                            folder or (extension not in BLOCKED_EXTENSIONS and not path.name.startswith('.')))
                        items.append({'id': _identifier(path), 'name': path.name, 'path': str(path),
                            'kind': kind, 'category': '폴더' if folder else '앱' if kind == 'app' else _category(extension),
                            'extension': extension, 'size': 0 if folder else info.st_size,
                            'modified': datetime.datetime.fromtimestamp(info.st_mtime).isoformat(timespec='seconds'),
                            'openable': openable})
                    except (OSError, ValueError):
                        messages.append('항목을 읽을 수 없습니다: ' + path.name)
        except OSError:
            messages.append('바탕화면 폴더를 읽을 수 없습니다: ' + str(root))
    items.sort(key=lambda item: (item['kind'] != 'folder', item['name'].casefold(), item['path']))
    return {'roots': roots, 'items': items, 'warnings': messages, 'total': len(items)}


def _selected(item_id):
    if not isinstance(item_id, str) or len(item_id) != 64:
        raise ValueError('바탕화면 항목을 다시 선택하세요.')
    item = next((item for item in inventory()['items'] if item['id'] == item_id), None)
    if item is None:
        raise ValueError('바탕화면 항목이 변경되었습니다. 목록을 새로고침하세요.')
    path = pathlib.Path(item['path'])
    try:
        if not path.exists() or not _safe_route(path):
            raise ValueError('링크 또는 변경된 경로는 열 수 없습니다.')
        path.stat()
    except OSError as error:
        raise ValueError('파일에 접근할 수 없습니다. 목록을 새로고침하세요.') from error
    return item, path


def open_item(item_id):
    if sys.platform != 'win32':
        raise ValueError('파일 열기는 Windows에서 지원합니다.')
    item, path = _selected(item_id)
    if not item['openable']:
        raise ValueError('실행 파일·스크립트·보호 파일은 이 화면에서 열 수 없습니다.')
    try:
        os.startfile(str(path))
    except OSError as error:
        raise ValueError('연결된 앱으로 파일을 열 수 없습니다.') from error
    return {'ok': True, 'id': item_id}


def reveal_item(item_id):
    if sys.platform != 'win32':
        raise ValueError('탐색기 보기는 Windows에서 지원합니다.')
    _, path = _selected(item_id)
    explorer = pathlib.Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'explorer.exe'
    try:
        subprocess.Popen([str(explorer), '/select,' + str(path)], shell=False)
    except OSError as error:
        raise ValueError('탐색기를 열 수 없습니다.') from error
    return {'ok': True, 'id': item_id}


def thumbnail(item_id):
    item, path = _selected(item_id)
    if item['kind'] != 'file' or item['extension'] not in IMAGE_FORMATS or not item['openable']:
        raise ValueError('PNG·JPEG·WebP 이미지만 미리보기할 수 있습니다.')
    if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError('미리보기 이미지는 8MB 이하만 지원합니다.')
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ValueError('이미지 미리보기에 Pillow가 필요합니다.') from error
    try:
        # A capped read also protects against a file growing after the size check.
        with path.open('rb') as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError('미리보기 이미지는 8MB 이하만 지원합니다.')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != IMAGE_FORMATS[item['extension']]:
                    raise ValueError('이미지 형식과 확장자가 일치하지 않습니다.')
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError('이미지는 2,000만 픽셀 이하만 지원합니다.')
                image = ImageOps.exif_transpose(image)
                image.thumbnail((320, 220))
                rgba = image.convert('RGBA')
                clean = Image.new('RGB', rgba.size, 'white')
                clean.paste(rgba, mask=rgba.getchannel('A'))
                output = io.BytesIO()
                clean.save(output, format='JPEG', quality=75)
                return output.getvalue()
    except ValueError:
        raise
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('이미지 미리보기를 만들 수 없습니다.') from error
