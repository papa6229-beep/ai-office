"""Explicit read-only import candidates from local Chrome and Edge bookmarks."""
import json
import itertools
import os
import pathlib
import re

from desktop_workspace import _safe_route

MAX_ITEMS = 5000
MAX_PROFILES = 40
MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 30000
MAX_DEPTH = 32
MAX_PROFILE_ENTRIES = 2000


def _browser_roots():
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return []
    base = pathlib.Path(local)
    return [('Chrome', base / 'Google' / 'Chrome' / 'User Data'),
            ('Edge', base / 'Microsoft' / 'Edge' / 'User Data')]


def catalog():
    # Reuse the exact same URL validation as manually registered website tiles.
    from quick_launcher import _target
    items, warnings, seen = [], [], set()
    result = {'source': 'browser-bookmarks', 'items': items, 'warnings': warnings, 'total': 0, 'truncated': False, 'profiles': 0}
    inspected = 0
    def warn(message):
        if message not in warnings:
            warnings.append(message)
    def walk(nodes, browser, profile, folder, depth):
        nonlocal inspected
        if depth > MAX_DEPTH:
            result['truncated'] = True
            return
        for node in nodes:
            inspected += 1
            if inspected > MAX_NODES or len(items) >= MAX_ITEMS:
                result['truncated'] = True
                return
            if not isinstance(node, dict):
                continue
            name = node.get('name', '')
            name = ''.join(c for c in name if ord(c) >= 32)[:120] if isinstance(name, str) else ''
            if node.get('type') == 'folder' and isinstance(node.get('children'), list):
                walk(node['children'], browser, profile, folder + ([name] if name else []), depth + 1)
            elif node.get('type') == 'url':
                try:
                    target = _target('web', node.get('url'))
                except ValueError:
                    continue
                key = (browser, profile, target, tuple(folder))
                if key not in seen:
                    seen.add(key)
                    items.append({'title': name or '이름 없는 북마크', 'kind': 'web', 'target': target, 'icon': '🌐',
                                  'browser': browser, 'profile': profile, 'folder': ' / '.join(folder)[:512]})
    for browser, supplied in _browser_roots():
        root = pathlib.Path(supplied)
        try:
            if not root.is_absolute() or str(root).startswith(('\\\\', '//')) or not root.exists():
                continue
            if not _safe_route(root):
                warn(browser + ': 링크로 연결된 프로필은 읽지 않았습니다.')
                continue
            entries = list(itertools.islice(root.iterdir(), MAX_PROFILE_ENTRIES + 1))
            if len(entries) > MAX_PROFILE_ENTRIES:
                result['truncated'] = True
            profiles = sorted((p for p in entries[:MAX_PROFILE_ENTRIES] if p.name == 'Default' or re.fullmatch(r'Profile [0-9]+', p.name)), key=lambda p: p.name)
            if len(profiles) > MAX_PROFILES:
                result['truncated'] = True
            for profile in profiles[:MAX_PROFILES]:
                path = profile / 'Bookmarks'
                try:
                    if not profile.is_dir() or not _safe_route(profile) or not path.exists():
                        warn(browser + ' ' + profile.name + ': 북마크 파일을 찾을 수 없습니다.')
                        continue
                    if not _safe_route(path) or not path.is_file() or path.stat().st_size > MAX_BYTES:
                        warn(browser + ' ' + profile.name + ': 안전하게 읽을 수 없는 북마크 파일입니다.')
                        continue
                    # A bounded read also handles files growing between stat and open.
                    with path.open('rb') as source:
                        raw = source.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:
                        raise ValueError()
                    document = json.loads(raw.decode('utf-8-sig'))
                    roots = document.get('roots') if isinstance(document, dict) else None
                    if not isinstance(roots, dict):
                        raise ValueError()
                    result['profiles'] += 1
                    walk(list(roots.values()), browser, profile.name, [], 0)
                except (OSError, ValueError, UnicodeError, RecursionError):
                    warn(browser + ' ' + profile.name + ': 북마크 파일을 읽지 못했습니다.')
        except OSError:
            warn(browser + ': 북마크 프로필을 읽지 못했습니다.')
    if result['truncated']:
        warn('북마크가 많아 일부만 표시합니다. 브라우저나 폴더를 나누어 등록하세요.')
    if not result['profiles'] and not warnings:
        warn('이 PC의 Chrome 또는 Edge에서 저장된 북마크를 찾지 못했습니다.')
    result['total'] = len(items)
    return result
