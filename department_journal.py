"""Department event ledger. Code queues data; the active Notion connector writes it.

No token, network client, model session or background sync lives in this module.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
BINDING = ROOT / 'local_backups' / 'department-journal' / 'notion-binding.json'
SEOUL = timezone(timedelta(hours=9))
STATES = {'queued':'배정대기', 'dispatching':'작업중', 'working':'작업중',
          'waiting':'확인필요', 'uncertain':'확인필요', 'completed':'부서응답완료',
          'failed':'확인필요', 'cancelled':'실행중단'}
PROTOCOL = ('\n\n[부서 업무일지 · 기존 업무 범위의 기록]\n'
            'docs/department-journal.md의 절차를 따르세요. 기존 업무 턴에서만 '
            'C:/Users/BNN/AI_OFFICE_GitHub/department_runtime.py export-journal의 묶음을 공식 Notion 도구로 반영합니다. '
            '같은 업무ID의 binding/검색 결과를 먼저 확인하고 기존 페이지를 재사용하세요. '
            'fetch로 업무ID·속성·event ID·revision을 확인한 뒤 acknowledge합니다. '
            '로컬 기록은 노션 반영 성공이 아닙니다. 미반영은 대기로 두고, 시간·완료를 추정하지 마세요. '
            '기록용 새 모델 호출·토큰 탐색·다른 채팅 보고를 만들지 마세요.')


def _schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS office_department_journal (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS office_department_journal_exports (id TEXT PRIMARY KEY, revision TEXT NOT NULL, data TEXT NOT NULL)')
    # One outstanding exported snapshot per assignment: stale acknowledgements
    # are accepted only while that exact exported revision still exists.


def _connect(db):
    c = sqlite3.connect(str(db), timeout=10)
    _schema(c)
    c.commit()
    return c


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_encode(value).encode('utf-8')).hexdigest()


def _stamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, SEOUL).isoformat(timespec='seconds')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('업무 ID를 확인해 주세요.')
    return value


def _page_id(value):
    try:
        import uuid
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('실제 Notion 페이지 ID를 확인해 주세요.') from None


def _url(value, page_id=None):
    p = urlparse(value or '')
    if p.scheme != 'https' or p.hostname not in {'app.notion.com', 'www.notion.so', 'notion.so'} or p.username or p.password:
        raise ValueError('실제 Notion 원문 주소를 확인해 주세요.')
    if page_id and page_id.replace('-', '') not in p.path.replace('-', ''):
        raise ValueError('Notion 페이지 ID와 주소가 달라요.')
    return value


def binding():
    """Private configuration is never copied to tracked files or public logs."""
    try:
        raw = json.loads(BINDING.read_text(encoding='utf-8-sig'))
        raw['database_url'] = _url(raw['database_url'])
        _page_id(raw['data_source_id'])
        return raw, ''
    except (OSError, ValueError, KeyError, TypeError):
        return {}, 'Notion 문서함 연결 정보를 확인해 주세요. 로컬 기록은 보존됩니다.'


def _get(c, ident):
    raw = c.execute('SELECT data FROM office_department_journal WHERE id=?', (ident,)).fetchone()
    return json.loads(raw[0]) if raw else None


def _save(c, row):
    c.execute('INSERT OR REPLACE INTO office_department_journal VALUES(?,?)', (row['id'], _encode(row)))


def capture(c, task):
    """Called inside the task write transaction; unchanged polling has no effect."""
    _schema(c)
    ident = _id(task['id'])
    row = _get(c, ident) or {'id':ident, 'events':[], 'synced_revision':'', 'notion':{}}
    old = row.get('source', {})
    source = {k:task.get(k) for k in ('title','team_id','team_name','agent_name','thread_id',
              'source_request_id','created','sent_at','state','report_state','instruction',
              'result','result_truncated','turn_id','hq_answer','error')}
    source.update({k:task[k] for k in ('origin','goal','project_id','project_root','followups','verification','activation_observation','direct_stopped','lifecycle') if k in task})
    if old == source:
        return row
    observed = _stamp()

    def event(kind, text, occurred=None, key=None):
        if key and any(e.get('key') == key for e in row['events']):
            return
        seq = len(row['events']) + 1
        row['events'].append({'id':ident+':'+str(seq), 'kind':kind, 'text':text,
                              'at':_stamp(occurred) if occurred else observed,
                              'time_basis':'발생 시각' if occurred else '관찰 시각', 'key':key or ''})

    if not old:
        event('direct' if task.get('origin')=='direct' else 'assigned', '대표 직접 업무 접수' if task.get('origin')=='direct' else '실장 배정 접수', task.get('created') if task.get('time_basis')!='observation' else None, 'assigned')
        if source.get('sent_at'):
            event('sent', '기존 부서 대화에 전달 성공', source['sent_at'], 'sent')
    elif source.get('sent_at') and not old.get('sent_at'):
        event('sent', '기존 부서 대화에 전달 성공', source['sent_at'], 'sent')
    if old.get('state') != source['state']:
        event('state', '부서 상태: '+STATES.get(source['state'], str(source['state'])))
    if source.get('result') and (old.get('result'), old.get('turn_id')) != (source['result'], source.get('turn_id')):
        event('department_result', '부서 제출 결과 수신 · 독립 검증 성공과 구별')
    if old.get('report_state') != source.get('report_state') and source.get('report_state') != 'pending':
        labels = {'sending':'실장에게 보고 전달 시작', 'awaiting':'실장 보고 응답 대기',
                  'uncertain':'실장 전달 여부 확인 필요', 'reported':'실장 최종 답변 오피스 전달 확인', 'failed':'실장 보고 미완료','notified':'실장의 정상 입력 문맥에 완료 요약 전달 확인'}
        event('report', labels.get(source['report_state'], '실장 보고 상태: '+str(source['report_state'])))
    if source.get('hq_answer') and old.get('hq_answer') != source['hq_answer']:
        event('hq_answer', '실장 최종 보고 내용 수신')
    if source.get('error') and old.get('error') != source['error']:
        event('attention', source['error'])
    for followup in task.get('followups',[]):
        event('followup','현재 업무의 질문 답변 접수' if followup.get('kind')=='question_answer' else '현재 업무에 추가 지시 연결',key='input:'+followup['request_id'])
    row.update(source=source, updated_at=observed)
    row['revision'] = _digest({'source':source, 'events':row['events']})
    _save(c, row)
    return row


def recover(db):
    """Recover saved facts only; historic intermediate transitions are unknown."""
    with closing(_connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='office_department_tasks'").fetchone():
            for raw, in c.execute('SELECT data FROM office_department_tasks').fetchall():
                capture(c, json.loads(raw))


def _notion(row, config):
    stored = row.get('notion') or config.get('pages', {}).get(row['id'], {})
    if not stored:
        return {}
    try:
        return {**stored, 'page_id':_page_id(stored['page_id']), 'url':_url(stored['url'], _page_id(stored['page_id']))}
    except (ValueError, KeyError, TypeError):
        return {}


def _status(source):
    if source.get('report_state') == 'reported':
        return '대표보고완료'
    if source.get('report_state') in {'uncertain','failed'}:
        return '확인필요'
    return STATES.get(source.get('state'), '확인필요')


def snapshot(db):
    config, error = binding()
    with closing(_connect(db)) as c:
        rows = [json.loads(r[0]) for r in c.execute('SELECT data FROM office_department_journal')]
    items = []
    for row in rows:
        s = row['source']; n = _notion(row, config)
        progress=next((e['text'] for e in reversed(row['events']) if e['kind']=='work_note'),'')
        items.append({'id':row['id'], 'title':s['title'], 'team_id':s['team_id'], 'team_name':s['team_name'],
                      'agent_name':s['agent_name'], 'thread_id':s['thread_id'], 'status':_status(s),
                      'work_at':n.get('work_at') or (_stamp(s['created']) if s.get('created') else None),
                      'updated_at':row['updated_at'], 'last_event':row['events'][-1],
                      'summary':(s.get('hq_answer') or s.get('result') or s.get('error') or progress or '지시 전달 후 부서 작업을 기다리고 있어요.')[:220],
                      'event_count':len(row['events']), 'notion_url':n.get('url',''),
                      'sync_state':'synced' if row['synced_revision'] == row['revision'] else 'pending',
                      'sync_error':row.get('sync_error',''),
                      'last_synced_at':row.get('last_synced_at') or n.get('last_synced_at','')})
    items.sort(key=lambda r:r['work_at'] or '', reverse=True)
    return {'items':items, 'database_url':config.get('database_url',''),
            'calendar_url':config.get('database_url','')+('?v='+config['calendar_view_id'] if config.get('calendar_view_id') else ''),
            'timezone':'Asia/Seoul', 'error':error,
            'pending_count':sum(r['sync_state']=='pending' for r in items),
            'transport':'existing_notion_connector_in_active_work_turn'}


def _plain(text):
    # Plain fenced text prevents task-provided markup from becoming instructions,
    # links, mentions or native Notion child-page operations.
    import document_reader
    value = document_reader._redact(str(text or '')).replace('```', '｀｀｀')
    return '```text\n'+value+'\n```'


def _payload(row, config):
    s = row['source']; n = _notion(row, config)
    # The connector returns date properties at minute precision. Original
    # event timestamps retain their exact seconds in the ledger and body.
    work_at = (_stamp(s['created']) if s.get('created') else row['events'][0]['at'])[:16]+'+09:00'
    updated_at = row['updated_at'][:16]+'+09:00'
    import notion_memory
    props = {'제목':notion_memory.display_title(row['id'],s['title']), '부서':'운영본부' if s['team_id']=='hq' else s['team_name'],
             '진행상태':_status(s), '업무ID':row['id'], '요청ID':s['source_request_id'], '요원':s['agent_name'],
             'date:업무일시:start':work_at, 'date:업무일시:is_datetime':1,
             'date:최근갱신:start':updated_at, 'date:최근갱신:is_datetime':1}
    heading = '## 코드 업무 이력 · '+row['id']
    lines = [heading, '문서 revision: '+row['revision'],
             '서울 시간입니다. 발생 시각은 저장된 원본, 관찰 시각은 코드가 처음 확인한 시각입니다. 부서 응답 완료는 제출 사실이며 실제 성공 검증과 구별합니다.',
             '### 배정 업무', _plain(s['title']),
             '### 시각별 기록']
    if s.get('origin'):
        lines[5:5]=['### 출처·프로젝트·실제 확인 수준',_plain({'origin':s['origin'],'project_id':s.get('project_id'),'project_root':s.get('project_root'),'verification':s.get('verification'),'activation':s.get('activation_observation')})]
    for e in row['events']:
        lines.append('- '+e['at']+' ('+e['time_basis']+') · '+e['text']+' · event '+e['id'])
    if s.get('result'):
        lines.extend(['### 부서 제출 결과 요약', _plain(s['result'][:1600])])
        if s.get('result_truncated'):
            lines.append('제출 결과가 잘렸습니다. 원래 부서 대화에서 전체 결과를 확인해야 합니다.')
    if s.get('hq_answer'):
        lines.extend(['### 실장 보고 요약', _plain(s['hq_answer'][:1000])])
    if s.get('lifecycle'):
        life=s['lifecycle']
        labels={'verified_complete':'실제 성공 확인','reported_unverified':'응답 수신 · 실제 성공 별도 확인','not_completed':'미완료 종료','unfinished':'미완료 유지'}
        lines.extend(['### 종료·재개 기준',
            '응답 종료: '+('예' if life['response_closed'] else '아니요')+' · '+labels[life['business_state']],
            '과거 원래 지시 자동 재실행: 하지 않음. 종료된 업무 재요청은 새 업무 ID로 기록합니다.'])
    lines.append('## 코드 업무 이력 끝 · '+row['id'])
    return {'assignment_id':row['id'], 'revision':row['revision'], 'page_id':n.get('page_id'),
            'page_url':n.get('url'), 'data_source_id':config.get('data_source_id'),
            'properties':props, 'managed_heading':heading, 'managed_content':'\n\n'.join(lines),
            'event_ids':[e['id'] for e in row['events']], 'work_at':work_at}


def pending(db, assignment_id='', limit=20):
    """Export exact revisions. Caller performs official fetch/create/update/fetch."""
    if assignment_id:
        _id(assignment_id)
    config, error = binding()
    if error:
        return {'items':[], 'error':error}
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('한 번에 반영할 업무 수를 확인해 주세요.')
    recover(db)
    with closing(_connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        rows = [json.loads(r[0]) for r in c.execute('SELECT data FROM office_department_journal')]
        items = []
        for row in rows:
            if (assignment_id and row['id'] != assignment_id) or row['revision'] == row['synced_revision']:
                continue
            item = _payload(row, config)
            c.execute('INSERT OR REPLACE INTO office_department_journal_exports(id,revision,data) VALUES(?,?,?)',
                      (row['id'], row['revision'], _encode(item)))
            items.append(item)
            if len(items) >= limit:
                break
    return {'items':items, 'error':'', 'protocol':'docs/department-journal.md'}


def acknowledge(db, payload):
    """Receipt after a successful connector readback; never claims a network call."""
    allowed = {'assignment_id','revision','page_id','url','verified_revision','verified_assignment_id',
               'verified_event_ids','verified_properties','page_last_edited_at'}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError('Notion 재조회 확인 항목을 확인해 주세요.')
    ident = _id(payload['assignment_id']); page = _page_id(payload['page_id'])
    url = _url(payload['url'], page)
    if payload['verified_assignment_id'] != ident or payload['verified_revision'] != payload['revision'] or not payload['page_last_edited_at']:
        raise ValueError('실제 Notion 업무ID·revision·최근수정 재조회가 필요해요.')
    config, _ = binding()
    with closing(_connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        row = _get(c, ident)
        raw = c.execute('SELECT data FROM office_department_journal_exports WHERE id=? AND revision=?',
                        (ident, payload['revision'])).fetchone()
        if not row or not raw:
            raise ValueError('내보낸 업무 revision을 다시 확인해 주세요.')
        exported = json.loads(raw[0])
        event_ids=payload['verified_event_ids']
        if not isinstance(event_ids,list) or len(event_ids)!=len(exported['event_ids']) or set(event_ids) != set(exported['event_ids']):
            raise ValueError('재조회한 Notion event ID가 내보낸 기록과 달라요.')
        actual = payload['verified_properties']
        if not isinstance(actual, dict):
            raise ValueError('재조회한 Notion 속성이 필요해요.')
        for key, expected in exported['properties'].items():
            observed = actual.get(key)
            if key.endswith(':start'):
                try:
                    if datetime.fromisoformat(str(observed).replace('Z','+00:00')) == datetime.fromisoformat(expected):
                        continue
                except ValueError:
                    pass
            if observed != expected:
                raise ValueError('재조회한 Notion 속성이 달라요: '+key)
        prior = _notion(row, config)
        if prior.get('page_id') and prior['page_id'] != page:
            raise ValueError('이 업무는 이미 다른 Notion 원문에 연결돼 있어요.')
        row.update(notion={'page_id':page, 'url':url, 'work_at':exported['work_at'],
                           'page_last_edited_at':payload['page_last_edited_at']},
                   synced_revision=payload['revision'], last_synced_at=_stamp(), sync_error='')
        _save(c, row)
        import notion_memory
        notion_memory.remember_receipt(c,exported,payload)
        import department_knowledge
        task=c.execute('SELECT data FROM office_department_tasks WHERE id=?',(ident,)).fetchone()
        if task:department_knowledge.index_task(c,json.loads(task[0]),row)
        return {'assignment_id':ident, 'sync_state':'synced' if row['revision']==payload['revision'] else 'pending',
                'url':url, 'verified_revision':payload['revision']}


def defer(db, assignment_id, reason):
    """Preserve a real connector failure without acknowledging any revision."""
    _id(assignment_id)
    if not isinstance(reason,str) or not 1 <= len(reason) <= 500:
        raise ValueError('실제로 확인한 미반영 사유를 기록해 주세요.')
    with closing(_connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE'); row=_get(c,assignment_id)
        if not row:raise ValueError('기존 배정 기록이 필요해요.')
        row['sync_error']=reason;_save(c,row)
        return {'assignment_id':assignment_id,'sync_state':'pending'}


def note(db, assignment_id, event_id, text, category='work_progress'):
    """Record actual work during its existing turn; caller supplies a stable ID."""
    _id(assignment_id)
    if not re.fullmatch('[a-z0-9_-]{1,80}', event_id or '') or not isinstance(text,str) or not 1 <= len(text) <= 2000:
        raise ValueError('진행 기록 ID와 실제 수행 내용을 확인해 주세요.')
    if category not in {'work_progress','work_result','confirmed_schedule','document_change','important_decision'}:
        raise ValueError('단순 질문·잡담·조회·진행 확인은 업무일지 기록 대상이 아니에요.')
    with closing(_connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE'); row = _get(c, assignment_id)
        if not row:
            raise ValueError('기존 배정 기록을 먼저 복원해 주세요.')
        key = 'note:'+event_id
        prior=next((e for e in row['events'] if e.get('key')==key),None)
        if prior:
            if prior['text']!=text:raise ValueError('같은 진행 기록 ID의 내용이 달라요.')
            return {'reused':True}
        at = _stamp()
        row['events'].append({'id':assignment_id+':'+str(len(row['events'])+1), 'kind':'work_note',
                              'text':text, 'at':at, 'time_basis':'관찰 시각', 'key':key, 'category':category})
        row['updated_at'] = at
        row['revision'] = _digest({'source':row['source'], 'events':row['events']})
        _save(c, row)
        return {'recorded':True, 'revision':row['revision']}
