"""Bounded metadata discovery and Explorer reveal for approved local sources.

This service never opens file contents and never executes a discovered item.
Scanner approvals grant access to the named item only, not its neighbours.
"""
import contextlib
import datetime
import hashlib
import os
import pathlib
import re
import sqlite3
import stat
import subprocess
import sys
import time
from collections import deque

import desktop_workspace
import scanner

MAX_ITEMS = 2000
MAX_ENTRIES = 5000
MAX_DIRECTORIES = 300
MAX_DEPTH = 4
MAX_SECONDS = 3.0
_PROTECTED = re.compile(r'(?i)(?:^|[._\- ])(?:settings|config|configuration|credentials?|passwords?|secrets?|tokens?|keys?|auth|api[_-]?keys?|private[_-]?keys?)(?:[._\- ]|$)')
_APP_ROOT = pathlib.Path(__file__).absolute().parent


class FileWorkspaceError(ValueError):
    """Sanitized discovery or reveal failure."""


def _canonical(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _within(path, root):
    try:
        return os.path.commonpath([_canonical(path), _canonical(root)]) == _canonical(root)
    except ValueError:
        return False


def _standard_roots():
    """Read Windows known-folder locations, without scanning the user profile."""
    roots = [('Documents', '문서'), ('Downloads', '다운로드')]
    result = []
    for name, label in roots:
        path = pathlib.Path.home() / name
        if sys.platform == 'win32':
            try:
                import winreg
                value_name = 'Personal' if name == 'Documents' else '{374DE290-123F-4565-9164-39C4925E467B}'
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
                    path = pathlib.Path(os.path.expandvars(value))
            except (ImportError, OSError, TypeError, ValueError):
                pass
        result.append((path, label))
    return result


def _safe(path):
    raw = pathlib.Path(path)
    if not raw.is_absolute() or '..' in raw.parts:
        raise FileWorkspaceError('절대 경로의 승인된 자료만 확인합니다.')
    path = raw.absolute()
    if _within(path, _APP_ROOT):
        raise FileWorkspaceError('앱 내부 경로는 검색하지 않습니다.')
    for part in (path, *path.parents):
        if part.name.startswith('.') or part.name.casefold() in scanner.EXCLUDE_NAMES or _PROTECTED.search(part.name):
            raise FileWorkspaceError('숨김·시스템·설정·인증 경로는 검색하지 않습니다.')
        info = part.lstat()
        if desktop_workspace._linked(part, info) or (part != pathlib.Path(part.anchor) and getattr(info, 'st_file_attributes', 0) & 6):
            raise FileWorkspaceError('링크·재분석·숨김 경로는 검색하지 않습니다.')
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        if path.suffix.casefold() in scanner.PROTECTED_EXT or info.st_nlink != 1:
            raise FileWorkspaceError('보호 파일 또는 하드링크는 검색하지 않습니다.')
    elif not stat.S_ISDIR(info.st_mode):
        raise FileWorkspaceError('일반 파일과 폴더만 검색합니다.')
    return path, info


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _item(path, info, source):
    folder = stat.S_ISDIR(info.st_mode)
    return {'id': hashlib.sha256(_canonical(path).encode('utf-8')).hexdigest(),
            'name': path.name, 'path': str(path), 'source': source,
            'kind': 'folder' if folder else 'file', 'size': 0 if folder else info.st_size,
            'extension': '' if folder else path.suffix.casefold(),
            'modified': datetime.datetime.fromtimestamp(info.st_mtime).isoformat(timespec='seconds'),
            'identity': _identity(info)}


class FileWorkspace:
    def __init__(self, db_path=None, workspace=None, *, opener=None):
        self.db_path = db_path
        self.workspace = workspace
        self.opener = opener

    def _sources(self):
        roots = [(path, '바탕화면', True) for path in desktop_workspace._desktop_roots()]
        roots.extend((path, source, True) for path, source in _standard_roots())
        if self.workspace:
            supplied = self.workspace if isinstance(self.workspace, (list, tuple)) else [self.workspace]
            roots.extend((path, '작업 폴더', True) for path in supplied)
        warnings = []
        truncated = False
        if self.db_path and pathlib.Path(self.db_path).is_file():
            try:
                uri = pathlib.Path(self.db_path).absolute().as_uri() + '?mode=ro'
                with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
                    rows = connection.execute('SELECT path FROM work_resources LIMIT ?', (MAX_ITEMS + 1,)).fetchall()
                truncated = len(rows) > MAX_ITEMS
                roots.extend((row[0], '업무 연결 자료', True) for row in rows[:MAX_ITEMS] if row[0])
            except (sqlite3.Error, OSError, ValueError):
                warnings.append('업무 연결 자료 목록 일부를 확인하지 못했습니다.')
        with scanner.LOCK:
            approved = [(row.get('path'), 'PC 조사 자료', False) for row in scanner.FILES[:MAX_ITEMS]
                        if row.get('path') and not row.get('protected')]
            truncated = truncated or len(scanner.FILES) > MAX_ITEMS
        roots.extend(approved)
        if len(roots) > MAX_ENTRIES:
            truncated = True
            roots = roots[:MAX_ENTRIES]
        return roots, warnings, truncated

    def _catalog(self):
        started = time.monotonic()
        roots, warnings, truncated = self._sources()
        scopes = []
        queue = deque()
        for supplied, source, recursive in roots:
            if time.monotonic() - started >= MAX_SECONDS:
                truncated = True
                break
            try:
                path, _ = _safe(supplied)
                scopes.append({'path': str(path), 'source': source, 'recursive': recursive})
                queue.append((path, source, 0, recursive))
            except (OSError, ValueError, TypeError):
                warnings.append(source + ': 사용할 수 없거나 보호된 경로가 있습니다.')
        items, visited, seen_ids = [], set(), set()
        entries = directories = 0
        while queue:
            if (entries >= MAX_ENTRIES or len(items) >= MAX_ITEMS or
                    time.monotonic() - started >= MAX_SECONDS):
                truncated = True
                break
            supplied, source, depth, recursive = queue.popleft()
            entries += 1
            try:
                path, info = _safe(supplied)
                canonical = _canonical(path)
                # An individual scanner approval must not suppress an independently
                # registered recursive root at the same path.
                visit_key = (canonical, recursive)
                if visit_key in visited:
                    continue
                visited.add(visit_key)
                identifier = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
                if identifier not in seen_ids:
                    seen_ids.add(identifier)
                    items.append(_item(path, info, source))
                if stat.S_ISDIR(info.st_mode) and recursive:
                    if depth >= MAX_DEPTH or directories >= MAX_DIRECTORIES:
                        truncated = True
                        continue
                    directories += 1
                    children = []
                    with os.scandir(path) as iterator:
                        for child in iterator:
                            if entries + len(queue) + len(children) >= MAX_ENTRIES or time.monotonic() - started >= MAX_SECONDS:
                                truncated = True
                                break
                            children.append(child.path)
                    queue.extend((child, source, depth + 1, True) for child in sorted(children, key=str.casefold))
            except (OSError, ValueError, TypeError):
                warnings.append('일부 항목은 접근할 수 없거나 보호되어 제외했습니다.')
        if truncated:
            warnings.append('검색 범위·깊이·개수·시간 제한으로 전체 PC 검색 결과가 아닙니다.')
        items.sort(key=lambda row: (row['kind'] != 'folder', row['name'].casefold(), row['path']))
        return {'items': items, 'truncated': truncated, 'scopes': scopes,
                'warnings': list(dict.fromkeys(warnings))}

    def search(self, query='', kind='all', limit=50):
        if kind not in {'all', 'file', 'folder'}:
            raise FileWorkspaceError('검색 종류는 all, file, folder 중 하나여야 합니다.')
        if not isinstance(query, str) or len(query) > 500:
            raise FileWorkspaceError('검색어는 500자 이하로 입력하세요.')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise FileWorkspaceError('검색 결과 개수는 1~200이어야 합니다.')
        result = self._catalog()
        tokens = query.casefold().split()
        matches = [row for row in result['items'] if (kind == 'all' or row['kind'] == kind)
                   and all(token in row['name'].casefold() for token in tokens)]
        result['items'] = matches[:limit]
        result['total'] = len(matches)
        if len(matches) > limit:
            result['warnings'].append('일치한 항목 중 요청한 개수만 표시했습니다.')
        result['truncated'] = result['truncated'] or len(matches) > limit
        return result

    def resolve(self, item_id):
        if not isinstance(item_id, str) or not re.fullmatch(r'[0-9a-f]{64}', item_id):
            raise FileWorkspaceError('검색 결과에서 파일 또는 폴더를 선택하세요.')
        item = next((row for row in self._catalog()['items'] if row['id'] == item_id), None)
        if item is None:
            raise FileWorkspaceError('승인된 검색 범위에서 항목을 다시 찾을 수 없습니다. 다시 검색하세요.')
        return item

    def reveal(self, item_id, expected=None):
        item = self.resolve(item_id)
        if expected is not None and (not isinstance(expected, dict) or
                any(expected.get(key) != item[key] for key in ('id', 'path', 'kind', 'identity'))):
            raise FileWorkspaceError('검색 이후 항목이 변경되었습니다. 다시 검색하세요.')
        path, info = _safe(item['path'])
        if _identity(info) != item['identity']:
            raise FileWorkspaceError('검색 이후 항목이 변경되었습니다. 다시 검색하세요.')
        if sys.platform != 'win32' and self.opener is None:
            raise FileWorkspaceError('탐색기 보기는 Windows에서 지원합니다.')
        explorer = pathlib.Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'explorer.exe'
        if not explorer.is_absolute():
            raise FileWorkspaceError('Windows 탐색기 위치를 확인할 수 없습니다.')
        args = [str(explorer), str(path) if item['kind'] == 'folder' else '/select,' + str(path)]
        try:
            (self.opener or subprocess.Popen)(args, shell=False)
        except OSError as error:
            raise FileWorkspaceError('탐색기 열기 요청을 전달하지 못했습니다.') from error
        return {'ok': True, 'dispatched': True, 'id': item['id'], 'path': str(path),
                'message': '탐색기에 위치 표시를 요청했습니다. 실제 화면 표시는 확인하지 않았습니다.'}
