"""Synthetic The Hand CS workflow. Every committed stage is replay-safe."""
from contextlib import closing
import datetime as dt
import json
import re
import sqlite3
import threading
import time

REVISION = 'the-hand-cs-pilot-v1'
FIXTURES = (
    ('order', 'paid', '시험 주문의 결제 상태'),
    ('shipping', 'shipped', '시험 주문의 출고 상태'),
    ('shipping', 'destination_unknown', '구매 전 배송 가능 지역'),
    ('product', 'spec_verified', '시험 상품의 등록 규격'),
    ('return', 'intake_requested', '시험 상품의 교환 접수 안내'),
    ('complaint', 'callback_requested', '불만 접수 후 통화 요청'),
)
POLICY = {
    ('order', 'paid'): ('test-order', '시험 주문은 결제가 확인됐습니다.'),
    ('shipping', 'shipped'): ('test-shipping', '시험 주문은 출고 상태입니다.'),
    ('product', 'spec_verified'): ('test-product', '시험 상품의 등록 규격은 가로 10cm, 세로 8cm입니다.'),
    ('return', 'intake_requested'): ('test-return', '시험 교환 접수 양식을 안내합니다.'),
}
REASONS = {
    'destination_unknown': '배송 지역에 적용할 정책 자료가 없음',
    'callback_requested': '불만 상세와 통화 가능 시간을 확인해야 함',
}
FINAL = ('verified', 'needs_review')


def _connect(db):
    conn = sqlite3.connect(str(db), timeout=8)
    conn.row_factory = sqlite3.Row
    return conn


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('시험 요청 번호를 확인해 주세요.')
    return value


def enqueue(db, run_id, request_id, delay_seconds=0):
    run_id, request_id = _id(run_id), _id(request_id)
    if type(delay_seconds) is not int or not 0 <= delay_seconds <= 300:
        raise ValueError('시험 예약은 5분 이내로 지정해 주세요.')
    stamp = time.time()
    with closing(_connect(db)) as conn, conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS cs_pilot_runs (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL, revision TEXT NOT NULL, delay_seconds INTEGER NOT NULL,
                created REAL NOT NULL, due REAL NOT NULL, started REAL, finished REAL, stopped REAL);
            CREATE TABLE IF NOT EXISTS cs_pilot_cases (
                run_id TEXT NOT NULL, position INTEGER NOT NULL, intent TEXT NOT NULL, fact TEXT NOT NULL,
                summary TEXT NOT NULL, stage TEXT NOT NULL, policy TEXT, draft TEXT, reason TEXT,
                PRIMARY KEY (run_id, position));
        ''')
        conn.execute('BEGIN IMMEDIATE')
        saved = conn.execute('SELECT request_id,delay_seconds FROM cs_pilot_runs WHERE id=?', (run_id,)).fetchone()
        if saved:
            if (saved['request_id'], saved['delay_seconds']) != (request_id, delay_seconds):
                raise ValueError('같은 요청의 시험 예약 시각이 달라요.')
            return
        conn.execute('INSERT INTO cs_pilot_runs VALUES (?,?,?,?,?,?,NULL,NULL,NULL)',
                     (run_id, request_id, REVISION, delay_seconds, stamp, stamp + delay_seconds))
        conn.executemany("INSERT INTO cs_pilot_cases VALUES (?,?,?,?,?,'queued',NULL,NULL,NULL)",
                         [(run_id, n, *fixture) for n, fixture in enumerate(FIXTURES, 1)])


def _exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def advance(db, run_id=None):
    """Commit one stage; an interrupted transaction leaves its previous stage intact."""
    stamp = time.time()
    with closing(_connect(db)) as conn, conn:
        if not _exists(conn, 'cs_pilot_runs'):
            return False
        conn.execute('BEGIN IMMEDIATE')
        job = conn.execute('''SELECT * FROM cs_pilot_runs WHERE finished IS NULL AND stopped IS NULL
            AND revision=? AND due<=? AND (? IS NULL OR id=?) ORDER BY due,id LIMIT 1''',
            (REVISION, stamp, run_id, run_id)).fetchone()
        if job is None:
            return False
        ident = job['id']
        if _exists(conn, 'assistant_inputs') and conn.execute(
                "SELECT 1 FROM assistant_inputs WHERE request_id=? AND kind='stop' LIMIT 1", (job['request_id'],)).fetchone():
            conn.execute('UPDATE cs_pilot_runs SET stopped=? WHERE id=?', (stamp, ident))
            return True
        row = conn.execute('''SELECT * FROM cs_pilot_cases WHERE run_id=?
            AND stage NOT IN (?,?) ORDER BY position LIMIT 1''', (ident, *FINAL)).fetchone()
        if row is None:
            conn.execute('UPDATE cs_pilot_runs SET finished=? WHERE id=?', (stamp, ident))
            return True
        conn.execute('UPDATE cs_pilot_runs SET started=coalesce(started,?) WHERE id=?', (stamp, ident))
        rule = POLICY.get((row['intent'], row['fact']))
        stage, policy, draft, reason = row['stage'], row['policy'], row['draft'], row['reason']
        if stage == 'queued':
            if rule:
                stage, policy = 'classified', rule[0]
            else:
                stage, reason = 'needs_review', REASONS.get(row['fact'], '확인된 시험 정책이 없음')
        elif stage == 'classified' and rule and policy == rule[0]:
            stage, draft = 'drafted', rule[1]
        elif stage == 'drafted' and rule and (policy, draft) == rule:
            stage = 'verified'
        else:
            stage, draft, reason = 'needs_review', None, '정책과 초안의 일치 확인 실패'
        conn.execute('''UPDATE cs_pilot_cases SET stage=?,policy=?,draft=?,reason=?
            WHERE run_id=? AND position=?''', (stage, policy, draft, reason, ident, row['position']))
        return True


def drain(db, run_id=None):
    # This bound also keeps recovery of a backlog from occupying the server.
    for _ in range(32):
        if not advance(db, run_id):
            break


def status(db, run_id=None):
    with closing(_connect(db)) as conn:
        if not _exists(conn, 'cs_pilot_runs'):
            return None
        conn.execute('BEGIN')
        job = conn.execute('''SELECT * FROM cs_pilot_runs WHERE (? IS NULL OR id=?)
            ORDER BY created DESC,id DESC LIMIT 1''', (run_id, run_id)).fetchone()
        if job is None:
            return None
        rows = [dict(row) for row in conn.execute('''SELECT position,intent,fact,summary,stage,policy,draft,reason
            FROM cs_pilot_cases WHERE run_id=? ORDER BY position''', (job['id'],))]
    reviews = [{k: row[k] for k in ('position','intent','summary','reason')}
               for row in rows if row['stage'] == 'needs_review']
    state = ('stopped' if job['stopped'] else 'done' if job['finished']
             else 'revision_wait' if job['revision'] != REVISION
             else 'running' if job['started'] else 'scheduled')
    packet = {'policy_revision':job['revision'], 'cases':reviews} if reviews and state == 'done' else None
    return dict(id=job['id'], state=state, due=job['due'], cases=rows,
                verified=sum(row['stage'] == 'verified' for row in rows),
                review_packet=packet,
                review_packet_bytes=len(json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode()) if packet else 0,
                elapsed_ms=round((job['finished']-job['started'])*1000) if job['finished'] and job['started'] else None,
                mode='code', model_calls=0, customer_sends=0)


def summary(result):
    if not result:
        return '더핸드 CS 시험 기록이 없어요.'
    if result['state'] == 'scheduled':
        kst = dt.timezone(dt.timedelta(hours=9))
        due = dt.datetime.fromtimestamp(result['due'], kst).strftime('%H:%M:%S')
        return f'더핸드 CS 시험을 {due}에 예약했어요(한국 시간). /더핸드CS 상태로 확인할 수 있어요.'
    if result['state'] != 'done':
        labels = {'running':'처리 중', 'stopped':'중지', 'revision_wait':'시험 정책 버전 확인 대기'}
        return '더핸드 CS 시험은 '+labels[result['state']]+' 상태예요.'
    reviews = (result['review_packet'] or {}).get('cases', [])
    lines = [f"더핸드 CS 시험 {len(result['cases'])}건 중 초안 검증 {result['verified']}건, 검토 대기 {len(reviews)}건이에요.",
             f"코드 처리 {result['elapsed_ms']}ms · 모델 호출 0회 · 고객 발송 0건"]
    lines.extend(f"- {row['summary']} · {row['reason']}" for row in reviews)
    return '\n'.join(lines)


def start(db):
    import office_reload
    def worker():
        while True:
            try:
                with office_reload.action() as admitted:
                    if admitted:
                        drain(db)
            except (OSError, sqlite3.Error):
                pass
            time.sleep(15)
    threading.Thread(target=worker, name='AI OFFICE CS pilot', daemon=True).start()
