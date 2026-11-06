"""Private, arrival-batch identities over the existing read-only evidence projection.

File dates and report start dates are links, never replacement arrival dates.
Chat text is evidence for review, never an instruction or an automatic status edit.
"""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
import threading

TZ = dt.timezone(dt.timedelta(hours=9))
SUFFIXES = {'.xlsx', '.xls', '.xlsm', '.csv', '.zip', '.rar', '.7z'}
_table_lock = threading.Lock()
PRODUCT_FIELDS = {'brand', 'total_count', 'kind_count', 'arrival_date', 'list_work_date',
                  'web_delivery', 'priority', 'discontinuation'}
WEB_FIELDS = {'work_status', 'person'}


class TableConflict(ValueError):
    pass


def row_revision(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def table_snapshot(data, private):
    settings = registry(private)
    if not settings or not settings.get('table_version'):
        return data
    cached = {r.get('id'): r for r in data.get('brands', [])}
    rows = []
    for index, source in enumerate(settings['batches']):
        row = dict(cached.get(source['id'], {}))
        row.update(id=source['id'], brand=source['brand'], arrival_date=source['arrival_date'],
                   batch=source['arrival_date'], person=source.get('person') or '미확인',
                   work_status=source['baseline_status'], confirmed_at=source['confirmed_at'])
        fields = {key: source.get(key, '') for key in PRODUCT_FIELDS}
        fields.update(work_status=source['baseline_status'], person=source.get('person') or '미확인')
        row.update(table_fields=fields, row_revision=row_revision(source), sequence=index + 1)
        if row['work_status'] != '완료':row.pop('completed', None)
        rows.append(row)
    return dict(data, brands=rows, table_version=settings['table_version'],
                manual_status=True, table_updated_at=settings.get('table_updated_at', settings['confirmed_at']))


def save_table(private, payload):
    """Same-origin Office editor. Sections identify field ownership, not login roles."""
    if (not isinstance(payload, dict) or set(payload) != {'id', 'expected_revision', 'section', 'fields'}
            or not isinstance(payload['section'], str) or payload['section'] not in {'product', 'web'}
            or not isinstance(payload['fields'], dict)):
        raise ValueError('수정할 행과 입력 항목을 확인해 주세요.')
    allowed = PRODUCT_FIELDS if payload['section'] == 'product' else WEB_FIELDS
    if not payload['fields'] or set(payload['fields']) - allowed:
        raise ValueError('선택한 담당 영역의 항목만 저장할 수 있어요.')
    with _table_lock:
        settings = registry(private)
        if not settings or not settings.get('table_version'):
            raise ValueError('기반표를 먼저 불러와 주세요.')
        matches = [r for r in settings['batches'] if r['id'] == payload['id']]
        if len(matches) != 1:raise ValueError('입고 차수를 다시 선택해 주세요.')
        source = matches[0]
        if payload['expected_revision'] != row_revision(source):
            raise TableConflict('다른 곳에서 이 행을 수정했어요. 입력 내용을 보관하고 최신 표를 다시 불러와 주세요.')
        fields = dict(payload['fields'])
        enums = {'web_delivery': {'수령 확인', '미수령', '전달 완료', '미전달', '확인 필요'},
                 'priority': {'', '최우선', '1순위', '2순위', '3순위', '미지정'},
                 'discontinuation': {'미확인', '확인 필요', '확인 완료'},
                 'work_status': {'미수령', '작업 예정', '대기', '진행 중', '완료', '보류'},
                 'person': {'미확인', '미배정', '이석환', '안용훈', '조현진', '조혜연'}}
        for key, value in fields.items():
            if key in {'total_count', 'kind_count'}:
                if value is not None and (type(value) is not int or not 0 <= value <= 1000000):
                    raise ValueError('가짓수와 종류는 빈칸 또는 0 이상의 정수로 입력해 주세요.')
            elif key in {'arrival_date', 'list_work_date'}:
                if not isinstance(value, str) or (key == 'arrival_date' and not value):
                    raise ValueError('물류입고일을 확인해 주세요.')
                if value:
                    try:dt.date.fromisoformat(value)
                    except ValueError:raise ValueError('날짜는 연-월-일로 입력해 주세요.') from None
            elif key == 'brand':
                if not isinstance(value, str) or not value.strip() or len(value) > 80 or '\n' in value:
                    raise ValueError('브랜드명을 확인해 주세요.')
                fields[key] = value.strip()
            elif not isinstance(value, str) or value not in enums[key]:
                raise ValueError('항목의 선택 목록에서 값을 골라 주세요.')
        before = {key: source.get('baseline_status') if key == 'work_status' else source.get(key, '') for key in fields}
        if before == fields:return {'unchanged': True, 'row_revision': row_revision(source)}
        original = copy.deepcopy(settings)
        if fields.get('brand') and fields['brand'] != source['brand']:
            source['aliases'] = sorted(set(source.get('aliases', []) + [source['brand']]))
        now = dt.datetime.now(TZ).isoformat(timespec='seconds')
        for key, value in fields.items():source['baseline_status' if key == 'work_status' else key] = value
        if payload['section'] == 'web':
            source['confirmed_at'] = now
            if source.get('report', {}).get('person') != source.get('person'):
                source.pop('report', None)
        source['last_edit'] = dict(at=now, section=payload['section'], before=before, after=fields)
        settings.update(manual_status=True, table_updated_at=now, version=settings['version'] + 1)
        backup = private / 'table-history' / (row_revision(original) + '.json')
        if not backup.exists():atomic(backup, original)
        atomic(private / 'registry.json', settings)
        return {'unchanged': False, 'row_revision': row_revision(source), 'saved_at': now}


def normal(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def read(path):
    if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('통합 현황 자료의 경로와 크기를 확인해 주세요.')
    return json.loads(path.read_text('utf-8'))


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    temporary.replace(path)


def registry(private):
    path = private / 'registry.json'
    if not path.exists():
        return None
    value = read(path)
    rows = value.get('batches', [])
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 500
            or len({r['id'] for r in rows}) != len(rows)):
        raise ValueError('입고 차수의 고유 번호를 확인해 주세요.')
    for row in rows:
        dt.date.fromisoformat(row['arrival_date'])
        dt.datetime.fromisoformat(row['confirmed_at'])
        if not row.get('brand') or row['baseline_status'] not in {
                '진행 중', '완료', '작업 예정', '미수령', '보류'}:
            raise ValueError('확정 현황의 브랜드와 상태를 확인해 주세요.')
    return value


def aliases(row):
    return {normal(v) for v in [row['brand'], *row.get('aliases', [])] if v}


def file_date(name):
    match = re.match(r'(20\d{2})[._-]?(\d{2})[._-]?(\d{2})(?:[_ .-]|$)', name)
    if not match:
        return ''
    try:
        return dt.date(*map(int, match.groups())).isoformat()
    except ValueError:
        return ''


def is_file_match(row, path):
    explicit = {os.path.normcase(os.path.abspath(v['path'])) for v in row.get('files', [])}
    if os.path.normcase(os.path.abspath(path)) in explicit:
        return True
    return (file_date(path.name) in row.get('list_dates', [])
            and any(a in normal(path.stem) for a in aliases(row)))


def inventory(root):
    """Only the registered material tree; skip junctions, temporary files and links."""
    result = []
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not
                   getattr((Path(parent) / d).lstat(), 'st_file_attributes', 0) & 0x400]
        for name in names:
            path = Path(parent) / name
            if (path.suffix.lower() not in SUFFIXES or name.startswith(('~$', '.'))
                    or path.is_symlink()):
                continue
            info = path.stat()
            if getattr(info, 'st_file_attributes', 0) & 0x400:
                continue
            result.append(dict(path=str(path), name=name, size=info.st_size,
                               modified_ns=info.st_mtime_ns, list_date=file_date(name)))
            if len(result) > 20000:
                raise ValueError('자료 폴더의 대조 범위가 너무 커 마지막 현황을 유지했어요.')
    return result


def _event(source, ident, **fields):
    key = hashlib.sha256((source + ':' + str(ident)).encode()).hexdigest()
    return dict(id=key, source=source, **fields)


def export_messages(spec):
    """Read an explicitly registered official local export, not a private app DB."""
    path = Path(spec['path'])
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('등록된 대화 내보내기 파일을 확인해 주세요.')
    text = path.read_text('utf-8-sig')
    if spec['header'] not in text.splitlines()[0]:
        raise ValueError('대화 내보내기의 단체방이 등록된 방과 다릅니다.')
    pattern = re.compile(r'^(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}), (.+?)\s+:\s+(.*)$', re.M)
    matches = list(pattern.finditer(text))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start(3):end].strip()
        posted = dt.datetime.strptime(match[1], '%Y.%m.%d %H:%M:%S').replace(tzinfo=TZ).isoformat()
        sender = match[2].strip()
        result.append(_event('nateon_export', spec['header'] + posted + sender + body,
            posted=posted, sender=sender, body=body[:3000], room=spec['header'],
            direction='out' if sender in spec.get('own_names', []) else 'in',
            group_verified=True, origin=str(path)))
    return result


def collect_messages(db, private, config, warnings):
    events = []
    coverage = {}
    # Notification capture is continuous but may omit messages with no OS notification.
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {'notification_room_messages', 'notification_rooms'} <= tables:
            titles = config.get('notification_titles', [])
            if titles:
                query = ('SELECT m.id,r.source,r.title,m.direction,m.sender,m.body,m.posted '
                         'FROM notification_room_messages m JOIN notification_rooms r ON r.id=m.room_id '
                         'WHERE r.title IN (' + ','.join('?' for _ in titles) + ') '
                         'ORDER BY m.posted DESC LIMIT 3000')
                for ident, source, room, direction, sender, body, posted in c.execute(query, titles):
                    events.append(_event('notification', ident, room=room, sender=sender,
                        body=body[:3000], posted=dt.datetime.fromtimestamp(posted, TZ).isoformat(),
                        direction=direction, app=source, group_verified=False))
            coverage['notifications'] = '수집된 알림만 · 단체방 소속과 누락 여부는 별도 확인'
    try:
        import messenger_attachments
        titles = config.get('hanbiro_titles', [])
        if titles:
            value = messenger_attachments._call({'action': 'chat_snapshot', 'titles': titles,
                                                  'room_ids': []}, timeout=25)
            allowed = set(titles)
            for room in value.get('rooms', []):
                if room.get('title') not in allowed:
                    continue
                for message in room.get('messages', []):
                    events.append(_event('hanbiro_chat', room['id'] + message['id'],
                        room=room['title'], sender=message.get('sender', ''),
                        body=message.get('text', '')[:3000], direction=message.get('direction'),
                        posted=dt.datetime.fromtimestamp(message['posted'], TZ).isoformat(),
                        group_verified=True))
            coverage['hanbiro'] = value.get('scope', '현재 PC 캐시')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        warnings.append('한비로 대화 갱신 실패 · 이전 근거를 유지해요.')
        coverage['hanbiro'] = '이번 조회 실패'
    ranges = []
    for spec in config.get('nateon_exports', []):
        try:
            messages = export_messages(spec)
            events.extend(messages)
            if messages:
                ranges.append(dict(first=messages[0]['posted'], last=messages[-1]['posted']))
        except (OSError, ValueError, KeyError, IndexError):
            warnings.append('등록한 네이트온 대화 내보내기를 읽지 못했어요.')
    coverage['nateon'] = dict(native_exports=ranges,
        scope='등록된 공식 내보내기 + 수집 알림 · PC 전체 과거 대화의 실시간 조회는 아님')
    return events, coverage


def project(db, data, root, private, config, reports):
    """Idempotent projection; user-confirmed identities survive every refresh/restart."""
    settings = registry(private)
    if not settings:
        return data
    warnings = data.setdefault('warnings', [])
    definitions = settings['batches']
    files = inventory(root)
    messages, coverage = collect_messages(db, private, config, warnings)
    old_ledger = read(private / 'evidence.json') if (private / 'evidence.json').exists() else {}
    ledger = old_ledger.get('events', {})
    if not isinstance(ledger, dict):
        raise ValueError('대조 근거 기록 형식을 확인해 주세요.')
    now = dt.datetime.now(TZ).isoformat(timespec='seconds')
    rows, linked_files, linked_legacy = [], set(), set()
    legacy = data.get('brands', [])
    previous = {r['id']: r for r in data.pop('_canonical_previous', []) if r.get('id')}
    for definition in definitions:
        ident = definition['id']
        row = dict(id=ident, brand=definition['brand'], batch=definition['arrival_date'],
            arrival_date=definition['arrival_date'], list_dates=definition.get('list_dates', []),
            person=definition.get('person') or '미확인', work_status=definition['baseline_status'],
            confirmed_at=definition['confirmed_at'], state_basis='사용자 확인',
            delivery='자료 미수령' if definition['baseline_status'] == '미수령' else '사용자 대조 확인',
            image_source=definition.get('image_source', '자료별 확인'),
            folder='', note=definition.get('note', ''), evidence=[], files=[])
        old = previous.get(ident, {})
        if old.get('confirmed_at') != definition['confirmed_at']:
            old = {}
        # Preserve newer validated report state when a source is temporarily unavailable.
        for key in ('report_day', 'total', 'remaining',
                    'delivery_evidence', 'delivery', 'completion_evidence', 'completed_workbook',
                    'completion_basis', 'completion_received', 'received_at', 'forwarded_at', 'forward_basis'):
            if key in old:
                row[key] = old[key]
        if (old.get('state_basis') == '연결된 제출 보고서'
                and old.get('report_day', '') > definition['confirmed_at'][:10]):
            for key in ('work_status', 'state_basis', 'completed'):
                if key in old:
                    row[key] = old[key]
        baseline = _event('user_confirmation', ident + definition['confirmed_at'],
            batch_id=ident, posted=definition['confirmed_at'], status=definition['baseline_status'],
            source_request=settings.get('request_id', ''), label='사용자 확인 기준')
        ledger[baseline['id']] = baseline
        binding = definition.get('report', {})
        if binding:
            linked_legacy.update(index for index, item in enumerate(legacy)
                if item.get('person') == binding.get('person')
                and item.get('start_date') == binding.get('start_date')
                and normal(item.get('brand')) in aliases(definition))
        available = reports.get(binding.get('person'), ('', {}))
        day, report = available
        matches = [r for r in report.get('brandUpdates', [])
                   if normal(r.get('brand')) in aliases(definition)
                   and r.get('startDate') == binding.get('start_date')]
        if len(matches) == 1 and day >= old.get('report_day', ''):
            item = matches[0]
            status = item.get('status')
            row.update(report_day=day, total=item.get('totalCount'), remaining=item.get('remainingCount'),
                       start_date=item.get('startDate'))
            report_event = _event('report', ident + day + json.dumps(item, sort_keys=True, ensure_ascii=False),
                batch_id=ident, posted=day, label='제출 보고서', status=status,
                total=row['total'], remaining=row['remaining'])
            ledger[report_event['id']] = report_event
            contradictory = (status == '완료' and (item.get('remainingCount') != 0
                or not item.get('completedDate') or item['completedDate'] < binding['start_date']))
            if contradictory:
                row['completion_warning'] = '보고서 완료 상태와 잔여·완료일이 달라 자동 완료하지 않았어요.'
            elif status in {'진행 중', '완료', '보류', '대기'} and day > definition['confirmed_at'][:10]:
                row.update(work_status='작업 예정' if status == '대기' else status, state_basis='연결된 제출 보고서')
                if status == '완료':
                    row['completed'] = item['completedDate']
                else:
                    row.pop('completed', None)
            if status != '완료' and item.get('completedDate'):
                row['completion_warning'] = '진행 중 보고서에 남은 과거 완료일은 현재 완료로 적용하지 않아요.'
        for item in files:
            path = Path(item['path'])
            if not is_file_match(definition, path):
                continue
            owners = [d['id'] for d in definitions if is_file_match(d, path)]
            if len(owners) != 1:
                row['material_warning'] = '같은 파일이 여러 입고 차수와 일치해 자동 연결에서 제외했어요.'
                continue
            linked_files.add(item['path'])
            role = '완료파일' if '완료파일' in path.parts else '작업자료'
            event = _event('file', item['path'] + ':' + str(item['modified_ns']) + ':' + str(item['size']),
                batch_id=ident, label=role, **item)
            if role == '완료파일':
                import brand_status_evidence
                event['sha256'] = brand_status_evidence._digest(path)
            ledger[event['id']] = event
            row['files'].append(dict(item, role=role))
            if role != '완료파일' and not row['folder']:
                row['folder'] = str(path.parent)
            for index, existing in enumerate(legacy):
                if (os.path.normcase(existing.get('completed_workbook', '')) == os.path.normcase(item['path'])
                        or existing.get('folder') == str(path.parent)):
                    linked_legacy.add(index)
                    if existing.get('delivery_evidence'):
                        row['delivery_evidence'] = existing['delivery_evidence']
                        row['delivery'] = existing.get('delivery', row['delivery'])
                    if role == '완료파일' and existing.get('completion_evidence'):
                        row.update(completed_workbook=item['path'], completion_basis=existing.get('completion_basis'),
                                   completion_evidence=existing['completion_evidence'])
        if row['files'] and definition['baseline_status'] == '미수령':
            row['material_warning'] = '미수령 기준과 연결된 파일이 함께 있어 확인이 필요해요.'
        selected = sorted((m for m in messages if any(a in normal(m.get('body'))
            for a in aliases(definition))), key=lambda m: m.get('posted', ''))
        for message in selected:
            # A brand-only message can concern multiple batches. Keep it as unassigned reference.
            message = dict(message)
            matched_file = next((f for f in row['files'] if f['name'] in message['body']), None)
            strong = bool(matched_file)
            target = ident if strong else ''
            message.update(batch_id=target, label='대화 · 파일 일치' if strong else '대화 · 차수 미확정')
            if strong or not ledger.get(message['id'], {}).get('batch_id'):
                ledger[message['id']] = message
            if strong and message['source'] == 'nateon_export' and message.get('direction') == 'in':
                row['received_at'] = max(row.get('received_at', ''), message['posted'])
            if strong and message['source'] == 'nateon_export' and message.get('direction') == 'out':
                if matched_file['role'] == '완료파일':
                    row['forwarded_at'] = max(row.get('forwarded_at', ''), message['posted'])
                    row['forward_basis'] = '상품팀 단체방 원문에 동일 완료파일명 발신 확인'
        evidence = [e for e in ledger.values() if e.get('batch_id') == ident]
        row['evidence'] = sorted(evidence, key=lambda e: e.get('posted', ''), reverse=True)[:80]
        row['reference_messages'] = [dict(source=m['source'], posted=m['posted'], room=m['room'],
            sender=m['sender'], text=m['body'][:500]) for m in selected[-12:]]
        row['unverified'] = definition.get('unverified', '')
        if row.get('work_status') != '완료':
            row.pop('completed', None)
        rows.append(row)
    known_aliases = {a for d in definitions for a in aliases(d)}
    candidates = [f for f in files if f['path'] not in linked_files and
                  any(a in normal(f['name']) for a in known_aliases)]
    data['other_batches'] = [r for i, r in enumerate(legacy) if i not in linked_legacy]
    data.update(brands=rows, batch_registry=True, registry_version=settings['version'],
                registry_confirmed_at=settings['confirmed_at'], link_candidates=candidates,
                auto_sync=dict(enabled=True, interval_seconds=300, last_success=now))
    data.setdefault('coverage', {}).update(coverage, files_scanned=len(files),
        linked_files=len(linked_files), evidence_events=len(ledger),
        matching='입고 고유번호 + 등록한 리스트 날짜·파일·보고서 배정 연결')
    # Source facts are deduplicated by stable IDs; failed reads do not erase history.
    atomic(private / 'evidence.json', dict(version=1, events=ledger, as_of=now))
    return table_snapshot(data, private)
