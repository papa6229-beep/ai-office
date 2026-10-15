"""Bounded, read-only discovery of live G-Clicker StreamDock shortcuts.

Targets and icon_data are private server data, not model-facing descriptions.
Only standalone website/openApps actions are supported; macros are never walked.
"""
import base64
import hashlib
import itertools
import io
import json
import ntpath
import os
import pathlib
import warnings
from urllib.parse import urlsplit

from desktop_workspace import _safe_route
import windows_app_catalog

WEBSITE = 'com.hotspot.streamdock.system.website'
OPEN_APP = 'com.hotspot.streamdock.system.openApps'
MAX_BYTES = 8 * 1024 * 1024
MAX_PROFILES = 100
MAX_ENTRIES = 2000
MAX_NODES = 20000
MAX_DEPTH = 16
MAX_ITEMS = 2000
MAX_FILES = 100
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ICON_BYTES = 512 * 1024


def _profile_root():
    roaming = os.environ.get('APPDATA')
    return pathlib.Path(roaming) / 'HotSpot' / 'StreamDock' / 'profiles' if roaming else None


def _title(value, fallback):
    if not isinstance(value, str):
        return fallback
    # JSON permits escaped lone surrogates, which cannot be emitted as UTF-8.
    return ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127
                   and not 0xD800 <= ord(c) <= 0xDFFF).strip()[:120] or fallback


def _icon(profile, action, application=None):
    from quick_launcher import _icon_image
    states = action.get('States')
    choices = []
    if isinstance(states, list):
        state = action.get('State', 0)
        index = state if type(state) is int and 0 <= state < len(states) else 0
        if states and isinstance(states[index], dict):
            choices.append(states[index].get('Image'))
    if application:
        choices.append(application.get('image'))
    for value in choices:
        if not isinstance(value, str) or not value or len(value) > MAX_ICON_BYTES * 2:
            continue
        try:
            if value.startswith('data:image/'):
                raw = _icon_image(value)
            else:
                # Do not accept absolute paths, traversal, URLs, or files outside
                # this profile. The selected app's shared cache icon is optional.
                winpath = pathlib.PureWindowsPath(value)
                if winpath.is_absolute() or winpath.drive or ':' in value or '..' in winpath.parts:
                    continue
                relative = pathlib.Path(*winpath.parts)
                if relative.is_absolute() or '..' in relative.parts:
                    continue
                path = profile / relative
                if not path.exists() and len(relative.parts) == 1:
                    path = profile / 'Images' / relative
                path.relative_to(profile)
                if not _safe_route(path) or not path.is_file() or path.stat().st_size > MAX_ICON_BYTES:
                    continue
                with path.open('rb') as source:
                    data = source.read(MAX_ICON_BYTES + 1)
                if len(data) > MAX_ICON_BYTES:
                    continue
                # StreamDock also uses GIF icons. Keep its first frame only and
                # pass normalized pixels through the launcher's existing guard.
                from PIL import Image
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('error', Image.DecompressionBombWarning)
                        with Image.open(io.BytesIO(data)) as image:
                            if image.format not in {'PNG', 'JPEG', 'GIF', 'WEBP', 'ICO'} or min(image.size) < 1 or max(image.size) > 1024:
                                continue
                            image.load()
                            output = io.BytesIO()
                            image.convert('RGBA').save(output, format='PNG')
                except Image.DecompressionBombError:
                    continue
                raw = _icon_image('data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii'))
            if raw:
                return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
        except (OSError, ValueError, ImportError, Warning):
            continue
    return None


def catalog():
    from quick_launcher import _target
    result = {'source': 'g-clicker', 'items': [], 'total': 0, 'profiles': 0,
              'skipped': {'unsupported': 0, 'invalid': 0, 'duplicate': 0},
              'warnings': [], 'truncated': False}
    seen, registered, digest = set(), {}, hashlib.sha256()
    inspected = 0

    def warn(message):
        if message not in result['warnings']:
            result['warnings'].append(message)

    # Exact Windows activation identity, never names, chooses the installed app.
    for item in windows_app_catalog.catalog().get('items', []):
        try:
            registered[windows_app_catalog.resolve(item['target'])] = item
        except (ValueError, KeyError, OSError):
            continue

    def actions(container, profile, depth=0):
        nonlocal inspected
        if depth > MAX_DEPTH:
            result['truncated'] = True
            return
        if not isinstance(container, dict):
            return
        for action in container.values():
            inspected += 1
            if inspected > MAX_NODES or len(result['items']) >= MAX_ITEMS:
                result['truncated'] = True
                return
            if not isinstance(action, dict):
                continue
            uuid = action.get('UUID')
            if not isinstance(uuid, str) or uuid not in {WEBSITE, OPEN_APP}:
                # Only explicit structural Actions containers without an action
                # UUID may descend. Unsupported action Settings stay untouched.
                if uuid is None and isinstance(action.get('Actions'), dict):
                    actions(action['Actions'], profile, depth + 1)
                else:
                    result['skipped']['unsupported'] += 1
                continue
            settings = action.get('Settings')
            application = None
            try:
                if not isinstance(settings, dict):
                    raise ValueError()
                if uuid == WEBSITE:
                    kind, target = 'web', _target('web', settings.get('path'))
                    title = _title(action.get('Name'), '')
                    if not title or title.casefold() in {'website', '웹사이트', 'web site', 'open website'}:
                        title = urlsplit(target).hostname[:120]
                else:
                    index, applications = settings.get('appId'), settings.get('applications')
                    if type(index) is not int or not isinstance(applications, list) or not 0 <= index < len(applications):
                        raise ValueError()
                    application = applications[index]
                    if not isinstance(application, dict):
                        raise ValueError()
                    raw = application.get('path')
                    if not isinstance(raw, str):
                        raise ValueError()
                    entry = registered.get(raw)
                    kind = 'app'
                    target = entry['target'] if entry else _target('app', raw)
                    title = _title(application.get('name'), entry['title'] if entry else '프로그램')
                canonical = ntpath.normcase(target) if kind == 'app' else target
                key = kind + '\0' + canonical
                if key in seen:
                    result['skipped']['duplicate'] += 1
                    continue
                seen.add(key)
                item = {'id': hashlib.sha256(key.encode('utf-8')).hexdigest(),
                        'title': title, 'kind': kind, 'target': target,
                        'icon': '🌐' if kind == 'web' else '🚀'}
                icon = _icon(profile, action, application)
                if icon:
                    item['icon_data'] = icon
                result['items'].append(item)
            except (ValueError, OSError, TypeError):
                result['skipped']['invalid'] += 1

    root = _profile_root()
    if root is not None:
        root = pathlib.Path(root)
        pending, directories, files, total_bytes = [(root, 0)], 0, 0, 0
        while pending:
            directory, depth = pending.pop(0)
            if depth > MAX_DEPTH or directories >= MAX_PROFILES * 2:
                result['truncated'] = True
                break
            directories += 1
            try:
                if not directory.is_absolute() or str(directory).startswith(('\\\\', '//')) or not directory.is_dir() or not _safe_route(directory):
                    continue
                with os.scandir(directory) as scan:
                    entries = list(itertools.islice(scan, MAX_ENTRIES + 1))
                if len(entries) > MAX_ENTRIES:
                    result['truncated'] = True
                for entry in sorted(entries[:MAX_ENTRIES], key=lambda e: e.name):
                    profile = pathlib.Path(entry.path)
                    if not entry.name.endswith('.sdProfile') or not _safe_route(profile) or not profile.is_dir():
                        continue
                    if result['profiles'] >= MAX_PROFILES or files >= MAX_FILES or total_bytes >= MAX_TOTAL_BYTES:
                        result['truncated'] = True
                        break
                    path = profile / 'manifest.json'
                    try:
                        if not path.is_file() or not _safe_route(path):
                            continue
                        files += 1
                        if path.stat().st_size > MAX_BYTES:
                            raise ValueError()
                        with path.open('rb') as source:
                            raw = source.read(min(MAX_BYTES, MAX_TOTAL_BYTES - total_bytes) + 1)
                        total_bytes += len(raw)
                        if total_bytes > MAX_TOTAL_BYTES:
                            result['truncated'] = True
                            raise ValueError()
                        if len(raw) > MAX_BYTES:
                            raise ValueError()
                        document = json.loads(raw.decode('utf-8-sig'))
                        if not isinstance(document, dict) or not isinstance(document.get('Actions'), dict):
                            raise ValueError()
                        result['profiles'] += 1
                        digest.update(hashlib.sha256(raw).digest())
                        actions(document['Actions'], profile)
                        # The documented live subprofile directory only. No zip,
                        # backup directory, or arbitrary recursive file search.
                        pending.append((profile / 'profiles', depth + 1))
                    except (OSError, ValueError, UnicodeError, RecursionError):
                        warn('일부 G-Clicker 프로필을 안전하게 읽지 못했습니다.')
            except OSError:
                warn('G-Clicker 프로필 폴더를 읽지 못했습니다.')
    result['total'] = len(result['items'])
    # Include normalized candidates so icon changes also invalidate snapshots.
    digest.update(json.dumps(result['items'], ensure_ascii=False, sort_keys=True).encode('utf-8'))
    result['revision'] = digest.hexdigest()
    if result['truncated']:
        warn('G-Clicker 자료가 많아 안전한 범위 내에서 일부만 확인했습니다.')
    if not result['profiles'] and not result['warnings']:
        warn('이 PC에서 지원하는 G-Clicker 프로필을 찾지 못했습니다.')
    return result
