"""Read-only team reports, local snapshots, and one daily code-only refresh."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / 'local_backups/design-team/sources.json'
ORIGIN = 'https://webdesign-daily-report.vercel.app'
SEOUL = dt.timezone(dt.timedelta(hours=9))
MEMBERS = [('lee-seokhwan', '이석환'), ('ahn-yonghoon', '안용훈'),
           ('jo-hyeonjin', '조현진'), ('jo-hyeyeon', '조혜연')]
LOCK = threading.Lock()
START_LOCK = threading.Lock()
WORKER = None


def now(): return dt.datetime.now(SEOUL)


def reference_day():
    day = now().date() - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def attendance(db):
    """Observe Codex's existing local IPC and the paired phone heartbeat only."""
    import desktop_chat_bridge
    configured=bool(desktop_chat_bridge.configuration())
    try:
        codex=configured and any(re.fullmatch(r'codex-app-tools-[0-9a-f-]{36}',name)
                                  for name in os.listdir('\\\\.\\pipe\\'))
    except OSError:codex=False
    with closing(db_open(db)) as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='direct_phone_devices'").fetchone()
        phone=bool(exists and c.execute('SELECT 1 FROM direct_phone_devices WHERE seen>? LIMIT 1',(time.time()-40,)).fetchone())
    return {'codex_running':bool(codex),'phone_connected':phone}


def dates(day=None, month=None):
    day = day or reference_day()
    parsed = dt.date.fromisoformat(day)
    if parsed.isoformat() != day or parsed > now().date() or parsed.year < 2020:
        raise ValueError('조회할 보고서 날짜를 확인해 주세요.')
    month = month or day[:7]
    if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month) or int(month[:4]) < 2020 or month > now().strftime('%Y-%m'):
        raise ValueError('조회할 월을 확인해 주세요.')
    return day, month


def db_open(db):
    c = sqlite3.connect(str(db), timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE IF NOT EXISTS design_team_snapshots(
      member TEXT NOT NULL,kind TEXT NOT NULL,period TEXT NOT NULL,data TEXT NOT NULL,
      fetched REAL NOT NULL,error TEXT NOT NULL DEFAULT '',attempt REAL NOT NULL,
      PRIMARY KEY(member,kind,period));
      CREATE TABLE IF NOT EXISTS design_team_schedule(id TEXT PRIMARY KEY,data TEXT NOT NULL);''')
    return c


def sources():
    raw = json.loads(SOURCES.read_text(encoding='utf-8'))
    result = {}
    for member, name in MEMBERS:
        url = raw.get(name, '')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'webdesign-daily-report.vercel.app'
                or not re.fullmatch(r'/member/[A-Za-z0-9_-]{16,100}', parsed.path)
                or parsed.query or parsed.fragment):
            raise ValueError('팀원 보고서 연결 주소를 확인해 주세요.')
        result[member] = url
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def fetch(url):
    request = urllib.request.Request(url, headers={'Accept':'application/json', 'User-Agent':'AI-Office-Team/1.0'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024: raise ValueError('응답 크기 초과')
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get('error'): raise ValueError('보고서 조회 실패')
    return result


def validate(data, member, name, kind, period):
    if kind == 'report':
        if data.get('member') != {'id':member, 'name':name}: raise ValueError('팀원 불일치')
        report = data.get('report')
        if not isinstance(report, dict): raise ValueError('보고서 형식 변경')
        for key in ('workEntries', 'brandUpdates'):
            rows = report.get(key)
            if not isinstance(rows, list) or len(rows) > 1500 or any(not isinstance(r, dict) for r in rows): raise ValueError('보고서 항목 형식 변경')
        if report.get('status') not in ('draft', 'submitted'): raise ValueError('제출 상태 형식 변경')
    else:
        if data.get('month') != period or not isinstance(data.get('days'), list): raise ValueError('월별 이력 형식 변경')
        for row in data['days']:
            if not isinstance(row, dict) or not str(row.get('date','')).startswith(period+'-') or not isinstance(row.get('brands'),list): raise ValueError('날짜별 이력 형식 변경')
    return data


def store(db, member, kind, period, data=None, error=''):
    stamp = time.time()
    with closing(db_open(db)) as c, c:
        if data is not None:
            c.execute('''INSERT INTO design_team_snapshots VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(member,kind,period) DO UPDATE SET data=excluded.data,
                fetched=excluded.fetched,error='',attempt=excluded.attempt''',
                (member,kind,period,json.dumps(data,ensure_ascii=False),stamp,'',stamp))
        else:
            c.execute('''INSERT INTO design_team_snapshots VALUES(?,?,?,'{}',0,?,?)
                ON CONFLICT(member,kind,period) DO UPDATE SET error=excluded.error,attempt=excluded.attempt''',
                (member,kind,period,error,stamp))


def schedule(db):
    with closing(db_open(db)) as c:
        row = c.execute("SELECT data FROM design_team_schedule WHERE id='daily'").fetchone()
    return json.loads(row[0]) if row else {}


def save_schedule(db, data):
    with closing(db_open(db)) as c, c:
        c.execute("INSERT OR REPLACE INTO design_team_schedule VALUES('daily',?)",(json.dumps(data),))


def refresh(db, day=None, month=None, *, force=False, scheduled=False):
    day, month = dates(day, month)
    if not LOCK.acquire(blocking=False): return snapshot(db,day,month)
    try:
        urls = sources()
        tasks = []
        with closing(db_open(db)) as c:
            for member,name in MEMBERS:
                for kind,period in [('report',day),('monthly',month)]:
                    row = c.execute('SELECT fetched,error FROM design_team_snapshots WHERE member=? AND kind=? AND period=?',(member,kind,period)).fetchone()
                    if not force and row and row['fetched'] and not row['error']: continue
                    token = urls[member].rsplit('/',1)[-1]
                    query = '?date='+period if kind=='report' else '?month='+period
                    tasks.append((member,name,kind,period,ORIGIN+'/api/member/'+token+'/'+kind+query))
        def retrieve(task):
            member,name,kind,period,url = task
            try:
                data = validate(fetch(url),member,name,kind,period)
                store(db,member,kind,period,data)
                return True
            except (OSError,ValueError,KeyError,TypeError,OverflowError):
                # Never include URLs/tokens, response bodies, or work text in errors.
                store(db,member,kind,period,error='원본을 가져오지 못했어요. 마지막 성공 자료를 표시합니다.')
                return False
        if scheduled:
            import desktop_chat_mirror
            desktop_chat_mirror.announce(db,'태준님, 디자인팀 관리 업데이트를 시작할게요. 네 분의 보고서를 확인해서 최신화하겠습니다.','design-team-start:'+now().date().isoformat())
        with ThreadPoolExecutor(max_workers=4) as pool:
            success = all(list(pool.map(retrieve,tasks)))
        if scheduled:
            save_schedule(db,{'attempt_at':time.time(),'run_day':now().date().isoformat(),
                              'reference_day':day,'success':success,'mode':'code','model_calls':0})
            if success:
                import work_log
                work_log.safe_event(db,'design-team:'+now().date().isoformat(),'activity','디자인팀 보고서 갱신',
                                    data={'application':'AI OFFICE','status':'completed','source':'design_team','reference_day':day,'member_count':4,'mode':'code','model_calls':0})
    finally: LOCK.release()
    return snapshot(db,day,month)


def snapshot(db, day=None, month=None):
    day,month = dates(day,month)
    try: urls=sources()
    except (OSError,ValueError): urls={}
    with closing(db_open(db)) as c:
        rows=c.execute('SELECT * FROM design_team_snapshots WHERE (kind=\'report\' AND period=?) OR (kind=\'monthly\' AND period=?)',(day,month)).fetchall()
    indexed={(r['member'],r['kind']):r for r in rows}
    members=[]
    for member,name in MEMBERS:
        result={'id':member,'name':name,'source_url':urls.get(member,''),'report':None,'monthly':None,'errors':{},'fetched':{}}
        for kind in ('report','monthly'):
            row=indexed.get((member,kind))
            if row and row['fetched']:
                payload=json.loads(row['data'])
                result[kind]=payload['report'] if kind=='report' else payload
                result['fetched'][kind]=row['fetched']
            if row and row['error']: result['errors'][kind]=row['error']
        members.append(result)
    upcoming=now().replace(hour=9,minute=30,second=0,microsecond=0)
    if upcoming<=now():upcoming+=dt.timedelta(days=1)
    while upcoming.weekday()>=5:upcoming+=dt.timedelta(days=1)
    return {'day':day,'month':month,'members':members,'busy':LOCK.locked(),'schedule':schedule(db),
            'next_sync':upcoming.isoformat(),'scheduled_time':'09:30','timezone':'Asia/Seoul',
            'schedule_days':'월~금','schedule_conditions':['Codex 실행 중','휴대폰 연결 중'],
            'reference_policy':'previous_weekday',
            'mode':'code','model_calls':0,'configured':len(urls)==4}


def start(db):
    global WORKER
    with START_LOCK:
        if WORKER and WORKER.is_alive():return
        def loop():
            while True:
                try:
                    instant=now();state=schedule(db)
                    due=instant.weekday()<5 and instant.time()>=dt.time(9,30)
                    done=state.get('run_day')==instant.date().isoformat() and state.get('success')
                    retry=time.time()-state.get('attempt_at',0)>=600
                    if SOURCES.is_file() and due and not done and retry:
                        present=attendance(db)
                        if not all(present.values()):
                            time.sleep(30)
                            continue
                        refresh(db,force=True,scheduled=True)
                except (OSError,ValueError,sqlite3.Error):
                    pass
                time.sleep(30)
        WORKER=threading.Thread(target=loop,name='AI OFFICE team reports',daemon=True)
        WORKER.start()
