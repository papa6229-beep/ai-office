"""Additional fixed chat adapters for existing launcher and scan services.

The caller validates schemas and journals write effects before dispatch here.
All mutable destinations come from server-owned references, never model paths.
"""
import base64
import copy
import json
import os
import time

import capabilities
import desktop_workspace
import file_workspace
import quick_launcher
import scanner
import companion_assets


def _string(description='', maximum=4000):
    return {'type': 'string', 'description': description, 'maxLength': maximum}


SPECS = {
    'launcher_pages': ('read', '과거 설정 호환용 페이지 정보를 조회합니다. 현재 화면은 페이지 전환 없는 스크롤 카드 보드이므로 보드 순서는 launcher_list로 확인하세요.', {}, []),
    'launcher_page_create': ('write', '과거 설정 호환용 빈 페이지를 추가합니다. 현재 스크롤 보드 화면에 페이지 전환이 생기지 않습니다.', {}, []),
    'launcher_move_page': ('write', '과거 호환용 페이지 소속만 옮깁니다. 현재 스크롤 보드 순서는 바뀌지 않으며 보드 순서는 launcher_reorder로 변경하세요.', {'ref': _string(), 'page_ref': _string()}, ['ref', 'page_ref']),
    'launcher_change_target': ('write', '조회한 바로가기의 실행 대상을 조회한 앱·북마크·폴더로 변경합니다. 이름·아이콘·페이지는 유지합니다. 파일 실행이나 추측 경로는 지원하지 않습니다.', {'ref': _string(), 'target_ref': _string()}, ['ref', 'target_ref']),
    'launcher_set_image': ('write', 'files_find로 선택한 실제 PNG·JPEG·WebP·GIF·ICO(512KB 이하)를 기존 바로가기 아이콘으로 적용합니다. 애니메이션 GIF는 원본을 유지하며 경로·신원을 다시 확인합니다.', {'ref': _string(), 'image_ref': _string()}, ['ref', 'image_ref']),
    'launcher_card_edit': ('write', '바로가기 카드 설명(160자 이하)과 커버를 편집합니다. cover_ref는 files_find가 조회한 4MB 이하 PNG·JPEG·WebP·GIF입니다. clear_cover=true는 커버만 지우며 기존 작은 아이콘은 유지합니다.', {'ref': _string(), 'description': _string(maximum=160), 'cover_ref': _string(), 'clear_cover': {'type':'boolean'}}, ['ref']),
    'scan_status': ('read', '기존 PC 자료 조사의 실제 진행 상태를 조회합니다. 새 조사를 시작하거나 파일 본문을 읽지 않습니다.', {}, []),
    'scan_results': ('read', '기존 PC 조사 결과에서 보호 경로를 제외한 메타데이터를 100개씩 조회합니다. 전체 디스크 조사나 파일 본문 읽기가 아닙니다. 위치 표시는 files_find로 다시 선택하세요.', {'query': _string(maximum=500), 'page': {'type': 'integer', 'minimum': 0, 'maximum': 1000}}, []),
    'companion_assets': ('read', 'Aemeath 상태별 캐릭터 목록을 조회합니다. 기본 이미지는 제거 대상이 아니며 추가 이미지의 참조를 반환합니다.', {'state': {'type':'string','enum':['idle','answer','agree','error','typing','working']}}, []),
    'companion_remove': ('write', '조회한 캐릭터 참조를 지정 상태의 표시 목록에서 제거합니다. 원본 첨부 파일은 삭제하지 않습니다.', {'state': {'type':'string','enum':['idle','answer','agree','error','typing','working']}, 'ref': _string()}, ['state','ref']),
}


def _ok(message, data=None, refresh=False):
    result = {'status': 'ok', 'message': message}
    if data is not None:
        result['data'] = data
    if refresh:
        result['refresh'] = ['launcher']
    return result


def _current(registry, context, ref):
    item_id = registry._get(context, ref, 'launcher')
    item = next((row for row in registry.launcher.overview()['items'] if row['id'] == item_id), None)
    if item is None:
        raise ValueError('바로가기를 다시 조회하세요.')
    return item


def _selection(registry, context, ref):
    selected = registry._get(context, ref, 'file_selection')
    with registry.capabilities._db() as connection:
        row = connection.execute('SELECT item_id,snapshot,created FROM capability_selections WHERE token=?',
                                 (selected['selection_token'],)).fetchone()
    if not row or row[0] != selected['id'] or row[2] < time.time() - capabilities.SELECTION_TTL:
        raise ValueError('파일 선택이 만료되었거나 변경되었습니다. 다시 검색하세요.')
    expected = json.loads(row[1])
    live = registry.capabilities.files.resolve(selected['id'])
    if any(expected.get(key) != live.get(key) for key in ('id', 'path', 'kind', 'identity')):
        raise ValueError('검색 이후 항목이 변경되었습니다. 다시 검색하세요.')
    return live


def _image_data(item, *, cover=False):
    maximum = quick_launcher._MAX_COVER_BYTES if cover else quick_launcher._MAX_ICON_BYTES
    path, info = file_workspace._safe(item['path'])
    mimes = {'.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.webp': 'webp', '.gif': 'gif', '.ico': 'x-icon'}
    mime = mimes.get(path.suffix.casefold())
    if item['kind'] != 'file' or not mime or (cover and mime == 'x-icon') or not 0 < info.st_size <= maximum:
        raise ValueError('커버는 4MB 이하 PNG·JPEG·WebP·GIF 파일을 선택하세요.' if cover else '아이콘은 512KB 이하의 PNG·JPEG·WebP·GIF·ICO 파일을 선택하세요.')
    if file_workspace._identity(info) != item['identity']:
        raise ValueError('검색 이후 이미지가 변경되었습니다. 다시 검색하세요.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if file_workspace._identity(os.fstat(stream.fileno())) != item['identity']:
            raise ValueError('이미지 경로가 변경되었습니다. 다시 검색하세요.')
        raw = stream.read(maximum + 1)
        if file_workspace._identity(os.fstat(stream.fileno())) != item['identity']:
            raise ValueError('이미지가 읽는 동안 변경되었습니다. 다시 검색하세요.')
    _, after = file_workspace._safe(path)
    if file_workspace._identity(after) != item['identity'] or len(raw) > maximum:
        raise ValueError('이미지 경로나 크기가 변경되었습니다. 다시 검색하세요.')
    value = 'data:image/' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')
    quick_launcher._cover_image(value) if cover else quick_launcher._icon_image(value)
    return value


def execute(registry, name, args, context):
    """Dispatch one named adapter; ToolRegistry owns schema and effect checks."""
    if name not in SPECS:
        raise ValueError('등록된 확장 기능을 선택하세요.')
    if name == 'companion_assets':
        manifest=companion_assets.manifest(registry.db_path)
        items=[]
        for state,rows in manifest['states'].items():
            if args.get('state') and state!=args['state']: continue
            for item in rows:
                if item['id'].startswith('builtin-'): continue
                ref=registry._ref(context,'companion_asset',{'id':item['id'],'state':state})
                items.append({'ref':ref,'state':state,'name':item['name']})
        return _ok('추가된 캐릭터 목록입니다.',{'items':items})
    if name == 'companion_remove':
        selected=registry._get(context,args['ref'],'companion_asset')
        if selected['state']!=args['state']: raise ValueError('조회한 상태와 제거 요청이 달라요. 다시 확인해줘.')
        result=companion_assets.remove(registry.db_path,selected['state'],selected['id'])
        return _ok('선택한 추가 캐릭터를 표시 목록에서 제거했어요. 원본 파일은 보관돼요.',{'revision':result['revision']})
    if name == 'scan_status':
        status = scanner.status()
        safe = {key: status.get(key) for key in ('id', 'status', 'scanned', 'skipped', 'errors', 'started', 'finished', 'summary')}
        # Raw scanner messages can contain private paths. Counters are sufficient.
        summary = safe.get('summary')
        if isinstance(summary, dict):
            safe['summary'] = {key: summary.get(key) for key in ('total', 'bytes', 'categories', 'protected', 'errors', 'skipped_dirs', 'truncated', 'analysis_level')}
        return _ok('PC 자료 조사 상태를 확인했습니다.', safe)
    if name == 'scan_results':
        result = scanner.sample(page=args.get('page', 0), term=args.get('query', ''))
        items = []
        for row in result['items']:
            if row.get('protected'):
                continue
            try:
                path, info = file_workspace._safe(row['path'])
                items.append(file_workspace._item(path, info, 'PC 조사 자료'))
            except (OSError, ValueError, TypeError):
                continue
        return _ok('기존 PC 조사 메타데이터를 확인했습니다.', {'items': items, 'page': result['page'],
                   'scanned_match_count': result['total'], 'visible_count': len(items),
                   'note': '기존 조사 결과에서 해당 페이지의 현재 접근 가능한 비보호 항목만 표시했습니다. 전체 PC 결과가 아닙니다.'})
    if name == 'launcher_pages':
        state = registry.launcher.overview()
        pages = []
        for row in state['pages']:
            page = copy.deepcopy(row)
            page['count'] = sum(item['page'] == page['id'] for item in state['items'])
            page['ref'] = registry._ref(context, 'launcher_page', page['id'])
            page.pop('id')
            pages.append(page)
        return _ok('바로가기 페이지를 확인했습니다.', {'items': pages, 'page_size': state['page_size']})
    if name == 'launcher_page_create':
        result = registry.launcher.perform('add-page', {'request_id': context['step_id']})
    elif name == 'launcher_move_page':
        item = _current(registry, context, args['ref'])
        page = registry._get(context, args['page_ref'], 'launcher_page')
        result = registry.launcher.perform('move-page', {'request_id': context['step_id'], 'id': item['id'], 'page': page})
    elif name == 'launcher_change_target':
        target = registry._get(context, args['target_ref'], {'app', 'bookmark', 'desktop', 'file_selection'})
        kind = context['refs'][args['target_ref']]['kind']
        if kind == 'desktop':
            target, _ = desktop_workspace._selected(target)
            if target['kind'] != 'folder':
                raise ValueError('폴더 바로가기는 조회한 폴더를 선택하세요.')
            kind, value = 'folder', target['path']
        elif kind == 'file_selection':
            target = _selection(registry, context, args['target_ref'])
            if target['kind'] != 'folder':
                raise ValueError('파일 실행 대신 폴더·앱·북마크를 선택하세요.')
            kind, value = 'folder', target['path']
        else:
            kind, value = ('app' if kind == 'app' else 'web'), target['target']
        with registry.launcher._lock:
            item = _current(registry, context, args['ref'])
            payload = {key: item[key] for key in ('title', 'icon')}
            payload.update(id=item['id'], request_id=context['step_id'], kind=kind, target=value)
            # Omitting icon_data preserves the original BLOB through QuickLauncher.
            result = registry.launcher.perform('save', payload)
    elif name == 'launcher_card_edit':
        if args.get('clear_cover') and 'cover_ref' in args:
            raise ValueError('커버 등록과 제거 중 하나를 선택하세요.')
        item = _current(registry, context, args['ref'])
        payload = {'request_id':context['step_id'], 'id':item['id']}
        if 'description' in args: payload['description'] = args['description']
        if 'cover_ref' in args: payload['cover_data'] = _image_data(_selection(registry, context, args['cover_ref']),cover=True)
        elif args.get('clear_cover'): payload['cover_data'] = None
        if len(payload) == 2:
            raise ValueError('변경할 설명이나 커버를 지정하세요.')
        result = registry.launcher.perform('card-style',payload)
    elif name == 'launcher_set_image':
        image = _selection(registry, context, args['image_ref'])
        value = _image_data(image)
        with registry.launcher._lock:
            item = _current(registry, context, args['ref'])
            result = registry.launcher.perform('appearance', {'request_id': context['step_id'], 'id': item['id'],
                      'title': item['title'], 'icon_data': value})
    else:
        raise ValueError('등록된 확장 기능을 선택하세요.')
    return _ok(result['result']['message'], result['result'], refresh=True)
