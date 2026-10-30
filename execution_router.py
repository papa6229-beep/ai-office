"""Per-request execution choice; no inference, settings mutation or price estimates."""
from contextlib import contextmanager
from contextvars import ContextVar
import datetime
import json
import re
import sqlite3
import threading
import time

PROFILES={'general':('gpt-6-luna','low'),'development':('gpt-6.1-sol','high'),
          'general_fallback':('gpt-6.1-sol','low')}
_current=ContextVar('office_execution',default=None)
_capacity_lock=threading.Lock()
_luna_busy_until=0.0
_CAPACITY_COOLDOWN=120

REUSE_GUIDANCE=('반복 업무는 등록된 코드 실행기를 우선 사용하세요. PROJECT_CONTEXT.md와 해당 업무 문서에 '
    '확인된 연결·실행 경로가 있으면 현재 버전과 연결 상태만 짧게 확인해 재사용하세요. '
    '메신저 경로는 docs/messenger-background.md를 참고하세요. 실제 실패·버전 변경·새 요구가 없으면 '
    '원본 구조 조사나 설치를 반복하지 마세요. 이미 성공한 효과를 다시 실행하지 말고 실패 단계만 확인하세요. '
    '단순 대답에는 불필요한 도구 호출을 하지 말고, 완료는 실제 결과로 확인하세요.')
REUSE_GUIDANCE+=' 파일 묶음은 files_batch, 확장자/이름 분류는 files_sort, 새 웹 업무는 ego_batch를 우선 사용하고 네이티브 실행 예시는 docs/office-fastlane.md를 확인하세요. 기존 Chrome 전용 업무를 자동으로 에고로 바꿨다고 가정하지 마세요.'


def _use_capacity_fallback(route,reason):
    route.update(model=PROFILES['general_fallback'][0],effort='low',reason='Luna 혼잡으로 Sol 이어받기')
    route.setdefault('recovery',[]).append({'action':'switch_model','reason':reason,
        'from_model':PROFILES['general'][0],'to_model':route['model'],'effort':route['effort']})


def note_capacity():
    global _luna_busy_until
    with _capacity_lock:_luna_busy_until=time.monotonic()+_CAPACITY_COOLDOWN


def recover_capacity(route):
    """One user-authorized model handoff; never retries an office operation."""
    if (route.get('provider')!='codex' or route.get('profile')!='general'
            or route.get('model')!=PROFILES['general'][0]):return False
    note_capacity()
    _use_capacity_fallback(route,'server_overloaded')
    route['model_errors']+=1
    save(route['_db'],route['_request_id'],route)
    return True


@contextmanager
def _db(db):
    connection=sqlite3.connect(str(db),timeout=15)
    connection.execute('CREATE TABLE IF NOT EXISTS assistant_execution_routes '
                       '(request_id TEXT PRIMARY KEY,data TEXT NOT NULL,updated TEXT NOT NULL)')
    try:
        with connection:yield connection
    finally:connection.close()


def load(db,request_id):
    with _db(db) as connection:
        row=connection.execute('SELECT data FROM assistant_execution_routes WHERE request_id=?',(request_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save(db,request_id,data):
    public={key:value for key,value in data.items() if not key.startswith('_')}
    with _db(db) as connection:
        connection.execute('INSERT OR REPLACE INTO assistant_execution_routes VALUES (?,?,?)',
            (request_id,json.dumps(public,ensure_ascii=False),datetime.datetime.now().astimezone().isoformat()))


def choose(db,action,payload,context,settings):
    import office_routines
    from model_connections import active_id
    provider=active_id(settings or {})
    inherited=None
    if action=='verify-followup':inherited=load(db,payload.get('parent_request_id'))
    elif action=='assistant-action':inherited=context.get('_pending_execution')
    routine=office_routines.match(context.get('user_goal','')) if action=='command' and not payload.get('attachment_ids') else None
    text=context.get('user_goal','').strip()
    development=bool(re.search(r'^/개발\b|코딩|디버[깅그]|버그|소스\s*코드|프로그램\s*개발|기능\s*(?:추가|개발|구현)|자가\s*수정|자체\s*수정|(?:에이메스|앱|프로그램).*(?:개조|구조.*(?:수정|변경|만들))|(?:코드|소스|UI|메뉴|팀원\s*탭|버튼|채팅창|레이아웃).*(?:수정|고쳐|바꿔|변경|추가|만들|생성|제거|없애|구현|개조)|\b(?:debug|refactor|implement|coding)\b|\.(?:py|js|css|html|tsx?)\b[\s\S]*?(?:수정|고쳐|리팩터|구현)',text,re.I))
    if not development and not routine and re.fullmatch(r'(?:응|그래|좋아|계속|이어서|그대로|다시|수정해|고쳐줘|해봐|진행해|시작해)[\s.!~]*(?:(?:해|줘|해줘|진행해|계속해|해봐)[\s.!~]*)?',text):
        with _db(db) as connection:
            import work_jobs
            row=connection.execute("SELECT e.data FROM assistant_execution_routes e LEFT JOIN assistant_work_channels w ON w.request_id=e.request_id WHERE coalesce(w.channel,'office')=? ORDER BY e.rowid DESC LIMIT 1",(work_jobs.channel(),)).fetchone()
        previous=json.loads(row[0]) if row else {}
        development=previous.get('profile')=='development'
    profile=(inherited or {}).get('profile') or ('development' if development else 'general')
    if profile not in PROFILES:profile='general'
    model,effort=PROFILES[profile] if provider=='codex' else ((settings or {}).get('gemma_model'),None)
    route={'mode':'code' if routine else 'model','provider':None if routine else provider,
            'profile':profile,'model':None if routine else model,'effort':None if routine else effort,
            'routine':routine,'reason':'등록된 반복 업무' if routine else '개발 요청' if profile=='development' else '일반 업무',
            'model_calls':0,'native_operations':0,'office_operations':0,'model_errors':0,'operation_errors':0,'elapsed_ms':0,
            'token_usage':None,'_started':time.monotonic(),'_usage':{},'_db':str(db),'_request_id':context.get('request_id')}
    with _capacity_lock:busy=time.monotonic()<_luna_busy_until
    if not routine and provider=='codex':
        import desktop_chat_bridge
        desktop=desktop_chat_bridge.configuration()
        if desktop:
            route.update(mode='desktop',transport='desktop_chat',model=None,effort=None,model_calls=None,
                         desktop_thread_id=desktop['thread_id'],reason='연결한 Codex 개발 대화에서 처리')
            return route
    if not routine and provider=='codex' and profile=='general' and busy:
        _use_capacity_fallback(route,'recent_capacity_failure')
    return route


@contextmanager
def scope(route):
    token=_current.set(route)
    try:yield route
    finally:_current.reset(token)


def profile(settings=None):
    route=_current.get()
    if route and route.get('provider')=='codex':return route['model'],route['effort']
    settings=settings or {}
    model=settings.get('codex_model') or settings.get('gemma_model') or PROFILES['general'][0]
    candidates=[value for value in PROFILES.values() if value[0]==model]
    if not candidates:raise ValueError('허용된 Codex 모델 프로필이 아닙니다. 자동 모델 대체는 하지 않습니다.')
    return candidates[0]


def promote(route):
    if route.get('provider')!='codex':return False
    route.update(profile='development',model=PROFILES['development'][0],effort='high',reason='작업 중 개발 필요 확인')
    return True


def model_call(provider,model,effort=None):
    route=_current.get()
    if route:
        route['model_calls']+=1
        route.setdefault('models_used',[])
        entry={'provider':provider,'model':model,'effort':effort}
        if entry not in route['models_used']:route['models_used'].append(entry)
        if route.get('_request_id'):save(route['_db'],route['_request_id'],route)


def operation(native=False,error=False):
    route=_current.get()
    if route:
        route['native_operations' if native else 'office_operations']+=1
        if error:route['operation_errors']+=1


def record_time(stage,elapsed_ms):
    """Aggregate only stage names and time, never arguments, text or secrets."""
    route=_current.get()
    if route is None:return
    name=re.sub('[^a-zA-Z0-9_.:-]','_',str(stage))[:80]
    row=route.setdefault('stage_timings',{}).setdefault(name,{'calls':0,'elapsed_ms':0})
    row['calls']+=1;row['elapsed_ms']+=max(0,round(elapsed_ms))


def usage(params):
    route=_current.get()
    last=(params.get('tokenUsage') or {}).get('last')
    if not route or not isinstance(last,dict):return
    key=str(params.get('turnId') or params.get('threadId'))
    route['_usage'][key]={name:last[name] for name in ('inputTokens','cachedInputTokens','outputTokens','reasoningOutputTokens') if type(last.get(name)) is int}
    route['token_usage']={name:sum(row.get(name,0) for row in route['_usage'].values()) for name in ('inputTokens','cachedInputTokens','outputTokens','reasoningOutputTokens')}


def finish(db,request_id,route):
    route['elapsed_ms']=round((time.monotonic()-route['_started'])*1000)
    save(db,request_id,route)
    return {key:value for key,value in route.items() if not key.startswith('_')}
