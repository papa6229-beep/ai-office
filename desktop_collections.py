"""Virtual desktop collections and explicit, bounded duplicate inspection.

Collections change app preferences only. Suggestions use names and metadata;
only duplicate_report reads source bytes and never writes them.
"""
import hashlib
import json
import os
import pathlib
import re
import stat
import uuid

import desktop_preferences as preferences
import desktop_workspace as workspace

MAX_COLLECTIONS = 60
MAX_MEMBERS = 100
MAX_DISMISSED = 500
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
NOTE = '파일명에 따른 관련 자료 후보입니다. 실제 업무 관계·최신 버전은 확인하지 않았습니다. 묶음은 앱 안에서만 저장됩니다.'


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120 or any(ord(c) < 32 for c in value):
        raise ValueError('묶음 이름은 1~120자로 입력하세요.')
    return value.strip()


def _ids(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_MEMBERS or any(
            not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in values) or len(set(values)) != len(values):
        raise ValueError('묶음 항목을 1~100개 선택하세요.')
    return list(values)


def _state(data):
    collections = data.get('collections', [])
    dismissed = data.get('dismissed_suggestions', [])
    if not isinstance(collections, list) or len(collections) > MAX_COLLECTIONS or not isinstance(dismissed, list) or len(dismissed) > MAX_DISMISSED:
        raise ValueError('묶음 설정을 읽을 수 없습니다. 기존 설정을 보존했습니다.')
    seen, members = set(), set()
    for group in collections:
        if not isinstance(group, dict) or not isinstance(group.get('id'), str) or not re.fullmatch('[0-9a-f]{32}', group['id']) or group['id'] in seen:
            raise ValueError('묶음 설정을 확인하세요.')
        _label(group.get('label'))
        ids = _ids(group.get('item_ids'))
        if members.intersection(ids):
            raise ValueError('중복된 묶음 설정을 확인하세요.')
        seen.add(group['id'])
        members.update(ids)
    if any(not isinstance(s, str) or not re.fullmatch('[0-9a-f]{64}', s) for s in dismissed):
        raise ValueError('보류한 제안 설정을 확인하세요.')
    if not isinstance(data.get('auto_organize_enabled', True), bool):
        raise ValueError('자동 묶음 설정을 확인하세요.')
    history = data.get('last_auto_group_ids', [])
    if not isinstance(history, list) or len(history) > MAX_COLLECTIONS or any(
            not isinstance(i, str) or not re.fullmatch('[0-9a-f]{32}', i) for i in history) or len(history) != len(set(history)):
        raise ValueError('자동 묶음 기록을 확인하세요.')
    return collections, dismissed


def _family(name):
    stem = pathlib.Path(name).stem.casefold().strip()
    plain = re.sub(r'\s*\(\d+\)$', '', stem).strip()
    if re.fullmatch('[0-9a-f]{12,}', plain) or re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27,}', plain) or re.fullmatch(r'[a-z]{1,8}\d{6,}', plain):
        return None
    # Strip suffix tokens only, never substrings inside meaningful words.
    suffix = r'(?:\s*\(\d+\)|[ _-]+(?:copy|복사본|복사)(?:[ _-]*\d+)?|[ _-]+(?:v|ver|version)\s*\d+(?:\.\d+)*|[ _-]+\d+|(?<=[a-z가-힣])\d+)$'
    previous = None
    while stem != previous:
        previous = stem
        stem = re.sub(suffix, '', stem).strip(' _-')
    compact = re.sub(r'[\s_-]+', '', stem)
    if len(compact) < 3 or not re.search('[a-z가-힣]', compact):
        return None
    if compact in {'새텍스트문서', '새문서', '제목없음', 'untitled', 'newtextdocument', 'copy', '복사본', 'image', 'img', 'photo', '사진', 'kakao', 'kakaotalk', 'screenshot', '스크린샷'}:
        return None
    if re.fullmatch('[0-9a-f]{12,}', compact) or re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27,}', stem):
        return None
    if re.match(r'^(?:kakao(?:talk)?|img|image|photo|dsc|pxl|screenshot)[ _-]?\d', pathlib.Path(name).stem.casefold()):
        return None
    return stem


def _signature(items):
    metadata = []
    for item in items:
        try:
            info = pathlib.Path(item['path']).lstat()
            precise = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        except OSError:
            precise = None
        metadata.append((item['id'], item['name'], item['size'], item['modified'], precise))
    metadata.sort()
    return hashlib.sha256(json.dumps(metadata, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _overview(snapshot, data):
    collections, dismissed = _state(data)
    live = {item['id']: item for item in snapshot['items']}
    occupied = {i for c in collections for i in c['item_ids']}
    result = []
    for group in collections:
        items = [live[i] for i in group['item_ids'] if i in live]
        result.append({**group, 'items': items, 'missing_count': len(group['item_ids']) - len(items)})
    families = {}
    for item in snapshot['items']:
        if item['id'] in occupied or item['kind'] != 'file' or not item['openable']:
            continue
        family = _family(item['name'])
        if family:
            key = (workspace._canonical(pathlib.Path(item['path']).parent), family)
            families.setdefault(key, []).append(item)
    suggestions = []
    for (_, family), items in sorted(families.items()):
        if not 2 <= len(items) <= MAX_MEMBERS:
            continue
        signature = _signature(items)
        if signature in dismissed:
            continue
        suggestions.append({'id': signature, 'label': family, 'item_ids': [i['id'] for i in items],
            'items': items, 'reason': '같은 위치에서 파일명과 복사본·버전 표기가 유사합니다.', 'kind': 'related'})
    return {'collections': result, 'suggestions': suggestions, 'dismissed_count': len(dismissed), 'note': NOTE,
        'auto_organize_enabled': data.get('auto_organize_enabled', True),
        'last_auto_group_ids': data.get('last_auto_group_ids', []), 'automatic_created_count': 0}


def overview(snapshot=None):
    return _overview(snapshot if snapshot is not None else workspace.inventory(), preferences.load())


def _mutate(action):
    snapshot = workspace.inventory()
    def edit(data):
        _state(data)
        data.setdefault('collections', [])
        data.setdefault('dismissed_suggestions', [])
        action(data, snapshot)
    data = preferences.update(edit)
    return _overview(snapshot, data)


def _group(data, group_id):
    group = next((g for g in data['collections'] if g['id'] == group_id), None)
    if group is None:
        raise ValueError('묶음이 변경되었습니다. 목록을 새로고침하세요.')
    return group


def create(label, item_ids):
    label, item_ids = _label(label), _ids(item_ids)
    def action(data, snapshot):
        live = {i['id'] for i in snapshot['items']}
        if not set(item_ids).issubset(live):
            raise ValueError('파일 목록이 변경되었습니다. 새로고침 후 다시 선택하세요.')
        occupied = {i for g in data['collections'] for i in g['item_ids']}
        if occupied.intersection(item_ids):
            raise ValueError('이미 다른 묶음에 있는 항목입니다. 목록을 새로고침하세요.')
        if len(data['collections']) >= MAX_COLLECTIONS:
            raise ValueError('묶음은 최대 60개까지 저장할 수 있습니다.')
        data['collections'].append({'id': uuid.uuid4().hex, 'label': label, 'item_ids': item_ids})
    return _mutate(action)


def rename(collection_id, label):
    label = _label(label)
    def action(data, snapshot):
        _group(data, collection_id)['label'] = label
    return _mutate(action)


def remove_item(collection_id, item_id):
    def action(data, snapshot):
        group = _group(data, collection_id)
        if item_id not in group['item_ids']:
            raise ValueError('묶음 항목이 변경되었습니다. 목록을 새로고침하세요.')
        group['item_ids'].remove(item_id)
        if not group['item_ids']:
            data['collections'].remove(group)
    return _mutate(action)


def dissolve(collection_id):
    def action(data, snapshot):
        data['collections'].remove(_group(data, collection_id))
    return _mutate(action)


def dismiss(suggestion_id):
    def action(data, snapshot):
        suggestions = _overview(snapshot, data)['suggestions']
        if suggestion_id not in {g['id'] for g in suggestions}:
            raise ValueError('제안이 변경되었습니다. 새로고침 후 다시 확인하세요.')
        if len(data['dismissed_suggestions']) >= MAX_DISMISSED:
            raise ValueError('보류한 제안이 500개에 도달했습니다.')
        data['dismissed_suggestions'].append(suggestion_id)
    return _mutate(action)


def restore_dismissed():
    def action(data, snapshot):
        data['dismissed_suggestions'] = []
    return _mutate(action)


def auto_organize(enabled=True):
    """Organize metadata families virtually; never move or delete source files."""
    if not isinstance(enabled, bool):
        raise ValueError('자동 묶음 설정을 확인하세요.')
    created = []
    def action(data, snapshot):
        data['auto_organize_enabled'] = enabled
        if not enabled:
            return
        suggestions = _overview(snapshot, data)['suggestions']
        for suggestion in suggestions:
            if len(data['collections']) >= MAX_COLLECTIONS:
                break
            group_id = uuid.uuid4().hex
            data['collections'].append({'id': group_id, 'label': suggestion['label'][:120],
                'item_ids': suggestion['item_ids'], 'source': 'automatic'})
            created.append(group_id)
        # A harmless refresh must not lose the user's ability to undo the last
        # actual batch. A later nonempty batch replaces that one-step history.
        if created:
            data['last_auto_group_ids'] = created
    result = _mutate(action)
    result['automatic_created_count'] = len(created)
    return result


def undo_auto():
    def action(data, snapshot):
        history = set(data.get('last_auto_group_ids', []))
        data['collections'] = [group for group in data['collections'] if group['id'] not in history]
        data['last_auto_group_ids'] = []
        data['auto_organize_enabled'] = False
    return _mutate(action)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity(info):
    # Windows path and descriptor stats can expose different ctime semantics.
    # Compare identity/size/mtime across interfaces; still compare full ctime
    # stamps within each interface before/after reading.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def duplicate_report():
    snapshot = workspace.inventory()
    candidates, warnings = {}, list(snapshot.get('warnings', []))
    for item in snapshot['items']:
        if item['kind'] != 'file' or not item['openable'] or item['extension'] in workspace.BLOCKED_EXTENSIONS:
            continue
        if item['size'] > MAX_FILE_BYTES:
            warnings.append('크기 제한으로 비교하지 않았습니다: ' + item['name'])
            continue
        candidates.setdefault(item['size'], []).append(item)
    total, checked, hashed = 0, 0, {}
    for size, items in sorted(candidates.items()):
        if len(items) < 2:
            continue
        for item in items:
            path = pathlib.Path(item['path'])
            try:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode) or not workspace._safe_route(path) or before.st_size != size:
                    raise ValueError()
                if total + size > MAX_TOTAL_BYTES:
                    warnings.append('전체 읽기 제한으로 비교하지 않았습니다: ' + item['name'])
                    continue
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if _identity(opened) != _identity(before) or not workspace._safe_route(path):
                        raise ValueError()
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(CHUNK_BYTES, remaining))
                        total += len(chunk)
                        remaining -= len(chunk)
                        if not chunk:
                            raise ValueError()
                        digest.update(chunk)
                    after = os.fstat(stream.fileno())
                if _stamp(opened) != _stamp(after) or _stamp(before) != _stamp(path.lstat()) or not workspace._safe_route(path):
                    raise ValueError()
                checked += 1
                hashed.setdefault((size, digest.hexdigest()), []).append((item, _stamp(before)))
            except (OSError, ValueError):
                warnings.append('변경되었거나 읽을 수 없어 비교에서 제외했습니다: ' + item['name'])
    groups = []
    # Revalidate every member after the whole pass; earlier files could change
    # while later files are hashed. Changed members never enter an exact group.
    for (size, digest), members in hashed.items():
        items = []
        for item, stamp in members:
            try:
                path = pathlib.Path(item['path'])
                if _stamp(path.lstat()) != stamp or not workspace._safe_route(path):
                    raise ValueError()
                items.append(item)
            except (OSError, ValueError):
                warnings.append('비교 도중 변경된 파일을 제외했습니다: ' + item['name'])
        if len(items) >= 2:
            groups.append({'id': _signature(items), 'item_ids': [i['id'] for i in items], 'items': items,
                'bytes_per_copy': size, 'duplicate_bytes': size * (len(items) - 1),
                'reason': '읽기 전후 파일 상태를 확인한 SHA-256 내용 일치입니다. 삭제 필요 여부는 판단하지 않았습니다.'})
    return {'groups': groups, 'warnings': warnings, 'checked': checked, 'total_bytes_read': total}
