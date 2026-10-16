"""Explicit local shortcuts, stored independently from tasks and user files."""
import base64
import binascii
import contextlib
import io
import json
import os
import pathlib
import re
import sqlite3
import threading
import uuid
import warnings
import webbrowser
from urllib.parse import urlsplit

from desktop_workspace import _desktop_roots, _safe_route
import windows_app_catalog

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_FIELDS = {'save': {'id', 'title', 'kind', 'target', 'icon', 'icon_data', 'page'},
           'remove': {'id'}, 'reorder': {'ids'}, 'launch': {'id'},
           'add-page': set(), 'appearance': {'id', 'title', 'icon', 'icon_data'}}
_PAGE_SIZE = 12
_MAX_ICON_BYTES = 512 * 1024
_LAUNCH_FAILURE = '열기 요청을 완료하지 못했습니다. 대상을 확인한 후 다시 요청하세요.'
_LAUNCH_UNCERTAIN = '이미 처리된 열기 요청입니다. 실행 여부를 확인한 후 새로 요청하세요.'


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', value):
        raise ValueError('유효한 바로가기 ID가 필요합니다.')
    return value


def _text(value, maximum, label):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(label + ' 값을 확인하세요.')
    return value.strip()


def _target(kind, value):
    if not isinstance(kind, str) or kind not in {'web', 'app', 'folder'}:
        raise ValueError('프로그램, 웹 또는 폴더를 선택하세요.')
    value = _text(value, 4096, '대상')
    if kind == 'web':
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            # A browser may reinterpret backslashes or control characters as URL delimiters.
            if parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None or parsed.password is not None or '\\' in value or any(c.isspace() for c in value):
                raise ValueError()
            parsed.port
            if ':' in host:
                import ipaddress
                ipaddress.IPv6Address(host)
            else:
                encoded = host.encode('idna').decode('ascii')
                if len(encoded) > 253 or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in encoded.rstrip('.').split('.')):
                    raise ValueError()
        except (ValueError, UnicodeError):
            raise ValueError('HTTP 또는 HTTPS 웹 주소를 입력하세요.') from None
        return value
    if kind == 'app' and value.startswith('startapp:'):
        windows_app_catalog.resolve(value)
        return value
    if value.startswith(('\\\\', '//')) or ':' in value[2:]:
        raise ValueError('로컬 절대 경로를 입력하세요.')
    path = pathlib.Path(value)
    if not path.is_absolute():
        raise ValueError('로컬 절대 경로를 입력하세요.')
    try:
        if not _safe_route(path) or (kind == 'folder' and not path.is_dir()) or (kind == 'app' and (path.suffix.lower() not in {'.exe', '.lnk'} or not path.is_file())):
            raise ValueError('사용 가능한 로컬 프로그램 또는 폴더를 선택하세요.')
    except OSError:
        raise ValueError('대상 경로를 확인할 수 없습니다.') from None
    return str(path)


def _icon_image(value):
    if not isinstance(value, str):
        raise ValueError('아이콘 이미지를 확인하세요.')
    if not value:
        return None
    if len(value) > (_MAX_ICON_BYTES + 2) // 3 * 4 + 100:
        raise ValueError('아이콘 이미지는 512KB 이하여야 합니다.')
    match = re.fullmatch(r'data:image/(png|jpeg|webp|x-icon|vnd.microsoft.icon);base64,([A-Za-z0-9+/=]+)', value)
    if not match:
        raise ValueError('PNG, JPEG, WebP 또는 ICO 이미지를 선택하세요.')
    try:
        from PIL import Image
    except ImportError:
        raise ValueError('아이콘 이미지를 처리하려면 Pillow가 필요합니다.') from None
    try:
        raw = base64.b64decode(match[2], validate=True)
        if len(raw) > _MAX_ICON_BYTES:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                expected = {'png': 'PNG', 'jpeg': 'JPEG', 'webp': 'WEBP', 'x-icon': 'ICO', 'vnd.microsoft.icon': 'ICO'}[match[1]]
                if source.format != expected or max(source.size) > 1024 or min(source.size) < 1:
                    raise ValueError()
                source.load()
                output = io.BytesIO()
                source.convert('RGBA').save(output, format='PNG')
                normalized = output.getvalue()
                if len(normalized) > _MAX_ICON_BYTES:
                    raise ValueError()
                return normalized
    except (OSError, ValueError, binascii.Error, Image.DecompressionBombError, Warning):
        raise ValueError('유효한 1024×1024 이하 아이콘 이미지를 선택하세요.') from None


def _suggestion_roots():
    roots = list(_desktop_roots())
    for key in ('APPDATA', 'PROGRAMDATA'):
        base = os.environ.get(key)
        if base:
            roots.append(pathlib.Path(base) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs')
    return roots


def _suggestions():
    items, seen, visited = [], set(), set()
    pending = [(pathlib.Path(root), 0) for root in _suggestion_roots()]
    inspected = 0
    while pending and len(visited) < 200 and inspected < 2000 and len(items) < 50:
        root, depth = pending.pop(0)
        key = os.path.normcase(str(root))
        if key in visited:
            continue
        visited.add(key)
        try:
            if str(root).startswith(('\\\\', '//')) or not root.is_absolute() or not root.is_dir() or not _safe_route(root):
                continue
            with os.scandir(root) as entries:
                for entry in entries:
                    inspected += 1
                    if inspected > 2000 or len(items) >= 50:
                        break
                    path = pathlib.Path(entry.path)
                    try:
                        if not _safe_route(path):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth < 3 and len(pending) < 200:
                                pending.append((path, depth + 1))
                        elif path.suffix.lower() == '.lnk' and entry.is_file(follow_symlinks=False):
                            target = _target('app', str(path))
                            canonical = os.path.normcase(target)
                            if canonical not in seen:
                                seen.add(canonical)
                                items.append({'title': path.stem[:120], 'kind': 'app', 'target': target, 'icon': '🚀'})
                    except (OSError, ValueError):
                        continue
        except OSError:
            continue
    return sorted(items, key=lambda item: item['title'].casefold())


class QuickLauncher:
    def __init__(self, db_path, web_opener=None):
        self.db_path = str(db_path)
        self.web_opener = web_opener
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(os.path.normcase(os.path.abspath(self.db_path)), threading.RLock())
        with self._lock, self._connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS launcher_items (
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,
                    target TEXT NOT NULL,icon TEXT NOT NULL DEFAULT '',
                    image BLOB,position INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS launcher_requests (
                    id TEXT PRIMARY KEY,payload TEXT NOT NULL,response TEXT);
                CREATE TABLE IF NOT EXISTS launcher_pages (
                    id INTEGER PRIMARY KEY,title TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS launcher_item_pages (
                    item_id TEXT PRIMARY KEY,page_id INTEGER NOT NULL);
            ''')
            self._sync_pages(c)

    def _new_page(self, c):
        page = c.execute('SELECT COALESCE(MAX(id),0)+1 FROM launcher_pages').fetchone()[0]
        c.execute('INSERT INTO launcher_pages VALUES (?,?)', (page, f'{page}페이지'))
        return page

    def _choose_page(self, c, requested=None, item_id=None):
        if requested is not None:
            if type(requested) is not int or requested < 1 or not c.execute('SELECT 1 FROM launcher_pages WHERE id=?', (requested,)).fetchone():
                raise ValueError('등록된 바로가기 페이지를 선택하세요.')
            count = c.execute('SELECT COUNT(*) FROM launcher_item_pages WHERE page_id=? AND item_id!=?', (requested, item_id or '')).fetchone()[0]
            if count < _PAGE_SIZE:
                return requested
        for row in c.execute('SELECT id FROM launcher_pages ORDER BY id').fetchall():
            count = c.execute('SELECT COUNT(*) FROM launcher_item_pages WHERE page_id=? AND item_id!=?', (row['id'], item_id or '')).fetchone()[0]
            if count < _PAGE_SIZE:
                return row['id']
        return self._new_page(c)

    def _sync_pages(self, c):
        # Side tables keep the original item schema and saved values unchanged.
        if not c.execute('SELECT 1 FROM launcher_pages LIMIT 1').fetchone():
            self._new_page(c)
        c.execute('DELETE FROM launcher_item_pages WHERE item_id NOT IN (SELECT id FROM launcher_items)')
        missing = c.execute('SELECT i.id FROM launcher_items i LEFT JOIN launcher_item_pages m ON m.item_id=i.id LEFT JOIN launcher_pages p ON p.id=m.page_id WHERE p.id IS NULL ORDER BY i.position,i.id').fetchall()
        for row in missing:
            page = self._choose_page(c)
            c.execute('INSERT OR REPLACE INTO launcher_item_pages VALUES (?,?)', (row['id'], page))

    @contextlib.contextmanager
    def _connect(self):
        c = sqlite3.connect(self.db_path, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def _overview(self, c):
        self._sync_pages(c)
        items = []
        for row in c.execute('SELECT i.id,title,kind,target,icon,image IS NOT NULL AS icon_image,position,m.page_id AS page FROM launcher_items i JOIN launcher_item_pages m ON m.item_id=i.id ORDER BY position,i.id'):
            item = dict(row)
            item['icon_image'] = bool(item['icon_image'])
            try:
                _target(item['kind'], item['target'])
                item['available'] = True
            except ValueError:
                item['available'] = False
            items.append(item)
        catalog = windows_app_catalog.catalog()
        primary = catalog['available']
        suggestions = catalog['items'] if primary else _suggestions()
        return {'items': items, 'pages': [dict(row) for row in c.execute('SELECT id,title FROM launcher_pages ORDER BY id')],
                'page_size': _PAGE_SIZE, 'suggestions': suggestions,
                'suggestion_source': 'windows-start-menu' if primary else 'local-shortcuts',
                'suggestion_warnings': catalog.get('warnings', []),
                'suggestion_total': len(suggestions),
                'suggestion_truncated': catalog.get('truncated', False) if primary else len(suggestions) >= 50}

    def overview(self):
        with self._lock, self._connect() as c:
            return self._overview(c)

    def icon_bytes(self, item_id):
        with self._connect() as c:
            row = self._item(c, item_id)
            if row['image'] is None:
                raise ValueError('등록된 아이콘 이미지가 없습니다.')
            return bytes(row['image']), 'image/png'

    def _item(self, c, item_id):
        row = c.execute('SELECT * FROM launcher_items WHERE id=?', (_identifier(item_id),)).fetchone()
        if row is None:
            raise ValueError('등록된 바로가기를 선택하세요.')
        return row

    def _replay(self, row, signature):
        if row['payload'] != signature:
            raise ValueError('요청 ID가 다른 요청에 사용되었습니다.')
        if row['response'] is None:
            raise ValueError(_LAUNCH_UNCERTAIN)
        response = json.loads(row['response'])
        if 'error' in response:
            raise ValueError(response['error'])
        return response

    def perform(self, action, payload):
        if not isinstance(action, str) or action not in _FIELDS or not isinstance(payload, dict) or set(payload) - (_FIELDS[action] | {'request_id'}):
            raise ValueError('지원하지 않는 요청 항목입니다.')
        request_id = _identifier(payload.get('request_id'))
        try:
            signature = json.dumps({'action': action, 'payload': payload}, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raise ValueError('요청 값을 확인하세요.') from None
        with self._lock:
            with self._connect() as c:
                c.execute('BEGIN IMMEDIATE')
                prior = c.execute('SELECT * FROM launcher_requests WHERE id=?', (request_id,)).fetchone()
                if prior is not None:
                    return self._replay(prior, signature)
                if action == 'launch':
                    row = self._item(c, payload.get('id'))
                    target = _target(row['kind'], row['target'])
                    # Commit before the external side effect: crashes and duplicate callers cannot relaunch.
                    c.execute('INSERT INTO launcher_requests VALUES (?,?,NULL)', (request_id, signature))
                    launch = (row['id'], row['kind'], target)
                else:
                    result = self._apply(c, action, payload)
                    response = self._overview(c)
                    response['result'] = result
                    c.execute('INSERT INTO launcher_requests VALUES (?,?,?)', (request_id, signature, json.dumps(response, ensure_ascii=False)))
                    return response
            item_id, kind, target = launch
            browser = None
            try:
                # Validate again after reserving the request and immediately before opening.
                target = _target(kind, target)
                if kind == 'web':
                    opened = self.web_opener(target) if self.web_opener else webbrowser.open(target)
                    if not opened:
                        raise OSError()
                    if isinstance(opened, dict): browser = opened
                elif kind == 'app' and target.startswith('startapp:'):
                    windows_app_catalog.launch(target)
                else:
                    os.startfile(target)
            except (OSError, ValueError, AttributeError, webbrowser.Error):
                with self._connect() as c:
                    c.execute('UPDATE launcher_requests SET response=? WHERE id=?', (json.dumps({'error': _LAUNCH_FAILURE}, ensure_ascii=False), request_id))
                raise ValueError(_LAUNCH_FAILURE) from None
            with self._connect() as c:
                response = self._overview(c)
                response['result'] = {'id': item_id, 'item_id': item_id, 'message': '열기를 요청했습니다.'}
                if browser:
                    response['result']['browser'] = browser
                    response['result']['message'] = browser.get('message', 'AI OFFICE Chrome에서 사이트를 열었습니다.')
                c.execute('UPDATE launcher_requests SET response=? WHERE id=?', (json.dumps(response, ensure_ascii=False), request_id))
                return response

    def _apply(self, c, action, payload):
        self._sync_pages(c)
        if action == 'add-page':
            return {'page': self._new_page(c), 'message': '바로가기 페이지를 추가했습니다.'}
        if action == 'appearance':
            old = self._item(c, payload.get('id'))
            title = _text(payload.get('title'), 120, '이름')
            icon = payload.get('icon', old['icon'])
            if not isinstance(icon, str) or len(icon) > 8 or any(ord(ch) < 32 for ch in icon):
                raise ValueError('아이콘은 8자 이하의 문자나 이모지로 입력하세요.')
            image = _icon_image(payload['icon_data']) if 'icon_data' in payload else old['image']
            c.execute('UPDATE launcher_items SET title=?,icon=?,image=? WHERE id=?', (title, icon, image, old['id']))
            page = c.execute('SELECT page_id FROM launcher_item_pages WHERE item_id=?', (old['id'],)).fetchone()[0]
            return {'id': old['id'], 'item_id': old['id'], 'page': page, 'message': '바로가기 이름과 아이콘을 저장했습니다.'}
        if action == 'save':
            old = self._item(c, payload['id']) if payload.get('id') is not None else None
            title = _text(payload.get('title'), 120, '이름')
            kind = payload.get('kind')
            target = _target(kind, payload.get('target'))
            icon = payload.get('icon', old['icon'] if old else '')
            if not isinstance(icon, str) or len(icon) > 8 or any(ord(ch) < 32 for ch in icon):
                raise ValueError('아이콘은 8자 이하의 문자나 이모지로 입력하세요.')
            image = _icon_image(payload['icon_data']) if 'icon_data' in payload else old['image'] if old else None
            item_id = old['id'] if old else uuid.uuid4().hex
            position = old['position'] if old else c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM launcher_items').fetchone()[0]
            if 'page' in payload:
                if type(payload['page']) is not int:
                    raise ValueError('등록된 바로가기 페이지를 선택하세요.')
                page = self._choose_page(c, payload['page'], item_id)
            elif old:
                page = c.execute('SELECT page_id FROM launcher_item_pages WHERE item_id=?', (item_id,)).fetchone()[0]
            else:
                page = self._choose_page(c)
            c.execute('INSERT INTO launcher_items VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,kind=excluded.kind,target=excluded.target,icon=excluded.icon,image=excluded.image', (item_id, title, kind, target, icon, image, position))
            c.execute('INSERT OR REPLACE INTO launcher_item_pages VALUES (?,?)', (item_id, page))
            return {'id': item_id, 'item_id': item_id, 'page': page, 'message': '바로가기를 저장했습니다.'}
        if action == 'remove':
            item_id = self._item(c, payload.get('id'))['id']
            c.execute('DELETE FROM launcher_item_pages WHERE item_id=?', (item_id,))
            c.execute('DELETE FROM launcher_items WHERE id=?', (item_id,))
            for position, row in enumerate(c.execute('SELECT id FROM launcher_items ORDER BY position,id').fetchall()):
                c.execute('UPDATE launcher_items SET position=? WHERE id=?', (position, row['id']))
            return {'id': item_id, 'item_id': item_id, 'message': '바로가기를 삭제했습니다.'}
        ids = payload.get('ids')
        existing = [row['id'] for row in c.execute('SELECT id FROM launcher_items')]
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids) or len(ids) != len(existing) or len(set(ids)) != len(ids) or set(ids) != set(existing):
            raise ValueError('모든 바로가기를 한 번씩 포함하여 정렬하세요.')
        for position, item_id in enumerate(ids):
            c.execute('UPDATE launcher_items SET position=? WHERE id=?', (position, item_id))
        return {'message': '바로가기 순서를 저장했습니다.'}
