"""Deterministic, explicit chat registration using trusted Windows app identities."""
import json
import datetime
import ntpath
import re
import uuid

import windows_app_catalog

_ALIASES = {'코덱스': 'codex', '포토샵': 'photoshop', '일러스트': 'illustrator',
            '일러스트레이터': 'illustrator', '크롬': 'chrome', '구글크롬': 'chrome',
            '챗지피티': 'chatgpt', '엣지': 'edge', '메모장': 'notepad'}


def names(message):
    """Only a complete affirmative imperative grants registration authority."""
    if re.search(r'[?？\n\r"\'“”‘’`]|하지|말아|말고|않|취소|금지|나중|만약|예를|가령|아니|말하면|라는|라고|해도|할까|(?:^|\s)안\s', message):
        return None
    verb = r'(?:추가|등록)(?:해\s*줘|해\s*주세요|해주세요|해|해라|해줘요|해줄래|해주시겠어요)[.!。]?\s*'
    match = re.fullmatch(r'\s*(?:바로\s*가기|런처)(?:에|로)?\s+(.+?)\s*(?:을|를)?\s*' + verb, message)
    if not match:
        match = re.fullmatch(r'\s*(.+?)\s*(?:을|를)?\s+(?:바로\s*가기|런처)(?:에|로)\s*' + verb, message)
    if not match:
        return None
    parts = re.split(r'\s*(?:[,，、]|그리고|하고|이랑|랑|(?:와|과)(?=\s))\s*', match[1])
    parts = [re.sub(r'(?:을|를)$', '', part.strip()).strip() for part in parts]
    if not 1 <= len(parts) <= 10 or any(not p or len(p) > 120 or re.search(r'추가|등록|업무|삭제|실행|전송', p) for p in parts):
        return []
    return list(dict.fromkeys(parts))


def _normal(value):
    return re.sub(r'[\s._-]+', '', value).casefold()


def matches(name, items):
    original = _normal(name)
    queries = {original, _ALIASES.get(original, original)}
    exact, related = [], []
    for item in items:
        labels = [item['title'], *item.get('aliases', [])]
        normalized = [_normal(label) for label in labels]
        if any(query in normalized for query in queries):
            exact.append(item)
        elif any(query in label for query in queries for label in normalized):
            related.append(item)
    # A shorter unversioned product label must not hide another installed edition.
    result = exact + related
    return list({item['target']: item for item in result}.values())


def pending(c):
    return [{'id': row['id'], 'message_id': row['message_id'], 'name': row['name'],
             'options': json.loads(row['options'])}
            for row in c.execute('SELECT * FROM work_launcher_choices WHERE resolved=0 ORDER BY rowid')]


def _add(c, launcher, item):
    # Titles are editable; identity comes exclusively from the trusted target.
    identity = _identity(item['target'])
    for existing in c.execute('SELECT target FROM launcher_items WHERE kind=?', ('app',)):
        if _identity(existing['target']) == identity:
            return False
    launcher._apply(c, 'save', {'title': item['title'], 'kind': 'app', 'target': item['target'], 'icon': item.get('icon', '🚀')})
    return True


def _identity(target):
    if target.startswith('startapp:'):
        try:
            registered = windows_app_catalog.resolve(target)
            if re.match(r'^[A-Za-z]:\\', registered):
                return ntpath.normcase(ntpath.normpath(registered))
        except ValueError:
            pass
        return target
    return ntpath.normcase(ntpath.normpath(target))


def register(c, launcher, requested, catalog, message_id):
    added, skipped, missing, ambiguous = [], [], [], []
    if not requested:
        return {'launcher_changed': False, 'message': '추가할 프로그램 이름을 1~10개로 나누어 알려주세요.'}
    if not catalog['available']:
        return {'launcher_changed': False, 'message': 'Windows 시작 메뉴 앱 목록을 읽지 못해 바로가기를 추가하지 않았습니다. 잠시 후 다시 요청하세요.'}
    for name in requested:
        options = matches(name, catalog['items'])
        if not options:
            missing.append(name)
        elif len(options) == 1:
            (added if _add(c, launcher, options[0]) else skipped).append(options[0]['title'])
        else:
            choice_id = uuid.uuid4().hex
            # Only these server-recorded targets can be chosen later.
            safe_options = [{key: item[key] for key in ('title', 'target', 'detail') if key in item} for item in options]
            for option in safe_options:
                if sum(other['title'] == option['title'] for other in safe_options) > 1:
                    option['detail'] = 'Windows 등록 이름: ' + windows_app_catalog.resolve(option['target'])
            c.execute('INSERT INTO work_launcher_choices VALUES (?,?,?,?,?,0)',
                      (choice_id, message_id, name, json.dumps(safe_options, ensure_ascii=False), datetime.datetime.now().astimezone().isoformat()))
            ambiguous.append(name)
    lines = []
    if added: lines.append('추가했습니다: ' + ', '.join(added))
    if skipped: lines.append('이미 등록되어 있습니다: ' + ', '.join(skipped))
    if missing: lines.append('시작 메뉴에서 찾지 못했습니다: ' + ', '.join(missing))
    if ambiguous: lines.append('여러 프로그램이 있어 선택이 필요합니다: ' + ', '.join(ambiguous))
    return {'launcher_changed': bool(added), 'message': '\n'.join(lines)}


def choose(c, launcher, payload, catalog):
    if not isinstance(payload.get('choice_id'), str) or not re.fullmatch(r'[0-9a-f]{32}', payload['choice_id']):
        raise ValueError('선택할 바로가기 요청을 확인하세요.')
    row = c.execute('SELECT * FROM work_launcher_choices WHERE id=?', (payload.get('choice_id'),)).fetchone()
    if row is None:
        raise ValueError('선택할 바로가기 요청을 확인하세요.')
    if row['resolved']:
        return {'launcher_changed': False, 'message': '이미 처리된 프로그램 선택입니다.'}
    allowed = {item['target']: item for item in json.loads(row['options'])}
    target = payload.get('target')
    if not isinstance(target, str) or target not in allowed:
        raise ValueError('표시된 프로그램 중 하나를 선택하세요.')
    if not catalog['available']:
        return {'launcher_changed': False, 'message': '현재 시작 메뉴 앱 목록을 확인할 수 없습니다. 잠시 후 다시 선택하세요.'}
    item = next((item for item in catalog['items'] if item['target'] == target), None)
    c.execute('UPDATE work_launcher_choices SET resolved=1 WHERE id=?', (row['id'],))
    if item is None:
        return {'launcher_changed': False, 'message': '선택한 프로그램이 현재 시작 메뉴에 없어 추가하지 않았습니다. 다시 요청하세요.'}
    added = _add(c, launcher, item)
    return {'launcher_changed': added, 'message': ('추가했습니다: ' if added else '이미 등록되어 있습니다: ') + item['title']}
