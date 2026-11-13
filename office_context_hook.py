"""Scoped Codex lifecycle recovery; never starts a task or calls a model.

Only registered Office/linked-project chats participate. Native input capture
starts after installation; older unobserved input is not execution authority.
Hooks must be reviewed/trusted in Codex. This is a context/failure fence, not a
semantic permission engine for arbitrary native shell or browser operations.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parent
LEGACY_DATA=ROOT/'local_backups'/'current-task-hooks'
# Hooks inherit the tool sandbox. Project writes may be denied even for a
# read-only tool; runtime bookkeeping belongs in the permitted user temp area.
# Keep the trusted installation scope and existing fences readable in place.
DATA=Path(tempfile.gettempdir())/('ai-office-current-task-hooks-'+hashlib.sha256(str(ROOT).casefold().encode('utf-8')).hexdigest()[:16])
SETTINGS=LEGACY_DATA/'installation.json'
REVISION='2026-11-13-compact-recovery-command-first-v7'
UUID=r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'


def _load(path, default=None):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        # Lazy read-through preserves captured inputs, installation cutover
        # and interrupt/compact fences; never treat missing state as approval.
        if path.parent==DATA:
            try:return json.loads((LEGACY_DATA/path.name).read_text(encoding='utf-8'))
            except FileNotFoundError:pass
        return default


def _save(path,value):
    DATA.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()


def _db():
    return sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=1)


def _scope(c,thread):
    found=c.execute("SELECT data FROM office_departments WHERE id='registry'").fetchone()
    data=json.loads(found[0]) if found else {}
    return any(thread in {a.get('thread_id'),a.get('coordinator_thread_id')}
               for team in data.get('teams',[]) for a in team.get('agents',[]))


def managed(thread):
    if not isinstance(thread,str) or not re.fullmatch(UUID,thread):return False
    try:
        with closing(_db()) as c:return _scope(c,thread)
    except (sqlite3.Error,OSError,ValueError):
        # A database failure must not stop unrelated projects using a global
        # user hook. Fail closed only for the explicitly installed scope.
        return thread in (_load(SETTINGS,{}) or {}).get('managed_thread_ids',[])


def read_checkpoint(thread):
    """Local exact input records only; no named-pipe or network fallback.

    Lack of native capture before the installation cutover cannot be papered
    over by selecting an old Office row. Output is reference, not a tool grant.
    """
    settings=_load(SETTINGS,{})
    baseline=(_load(DATA/'capture-start.json',{}) or {}).get('started_at')
    if not isinstance(baseline,(int,float)):
        return {'current':False,'can_execute':False,'reason':'신뢰된 훅의 입력 관측이 아직 시작되지 않았습니다.'}
    with closing(_db()) as c:
        if not _scope(c,thread):return {'managed':False,'current':False,'can_execute':False}
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        candidates=[]
        if 'project_panel_sends' in tables:
            row=c.execute('SELECT id,data,state,created FROM project_panel_sends WHERE thread_id=? AND host=? ORDER BY created DESC,rowid DESC LIMIT 1',(thread,'local')).fetchone()
            if row:
                value=json.loads(row[1]);candidates.append({'request_id':row[0],'user_message':value.get('message',''),
                    'state':row[2],'created':row[3],'source':'project_panel','eligible':row[2]=='sent'})
        if 'desktop_chat_requests' in tables:
            row=c.execute("SELECT id,data FROM desktop_chat_requests WHERE json_extract(data,'$.thread_id')=? ORDER BY CAST(json_extract(data,'$.created') AS REAL) DESC,rowid DESC LIMIT 1",(thread,)).fetchone()
            if row:
                value=json.loads(row[1]);origin=c.execute('SELECT status FROM assistant_requests WHERE id=?',(row[0],)).fetchone()
                stopped=bool(c.execute("SELECT 1 FROM assistant_inputs WHERE request_id=? AND kind='stop' LIMIT 1",(row[0],)).fetchone()) if 'assistant_inputs' in tables else False
                candidates.append({'request_id':row[0],'user_message':value.get('user_goal') or '',
                    'state':value.get('state'),'created':value.get('created',0),'source':'office_relay',
                    'input_revision':value.get('input_revision',0),
                    'followups': [{'kind':r[0],'message':r[1]} for r in c.execute('SELECT kind,message FROM assistant_inputs WHERE request_id=? ORDER BY created,rowid',(row[0],)).fetchall()][-20:] if 'assistant_inputs' in tables else [],
                    'eligible':bool(value.get('user_goal') and value.get('state') in {'sending','sent'} and origin and origin[0]=='running' and not stopped)})
        if 'office_department_tasks' in tables:
            row=c.execute("SELECT id,data FROM office_department_tasks WHERE json_extract(data,'$.thread_id')=? AND json_extract(data,'$.sent_at') IS NOT NULL ORDER BY CAST(json_extract(data,'$.sent_at') AS REAL) DESC,rowid DESC LIMIT 1",(thread,)).fetchone()
            if row:
                value=json.loads(row[1]);parent=c.execute('SELECT data FROM desktop_chat_requests WHERE id=?',(value.get('source_request_id'),)).fetchone()
                parent=json.loads(parent[0]) if parent else {}
                candidates.append({'request_id':row[0],'user_message':value.get('instruction',''),'state':value.get('state'),
                    'created':value.get('sent_at',0),'source':'department_assignment',
                    'eligible':value.get('state') in {'working','dispatching'} and parent.get('state') not in {'stop_requested','cancelled'}})
    native=_load(DATA/(thread+'.native.json'))
    if native:candidates.append(native)
    if not candidates:return {'managed':True,'current':False,'can_execute':False,'reason':'현재 입력 기록이 없습니다.'}
    latest=max(candidates,key=lambda r:float(r.get('created') or 0))
    eligible=bool(latest.get('eligible') and latest.get('user_message') and float(latest.get('created') or 0)>=baseline)
    fingerprint=hashlib.sha256(json.dumps(latest,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return dict(latest,managed=True,current=eligible,can_execute=False,context_valid=eligible,
                thread_id=thread,checkpoint_revision=fingerprint,recovery_revision=REVISION,
                reason='' if eligible else '현재 입력의 활성 상태 또는 설치 이후 관측을 확인하지 못했습니다. 과거 업무를 대신 선택하지 마세요.')


def _stop(event,reason):
    if event=='PreToolUse':return {'hookSpecificOutput':{'hookEventName':event,'permissionDecision':'deny','permissionDecisionReason':reason}}
    return {'continue':False,'stopReason':reason,'systemMessage':reason}


def recovery_command(thread,request_id=None):
    """One narrowly allowed read while a compact/interrupt fence is pending."""
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    command='& '+quote(sys.executable)+' -X utf8 '+quote(ROOT/'office_current_task.py')+' --read-current'
    if request_id:command+=' '+quote(request_id)
    return command+' --thread-id '+quote(thread)


def _recovery_read(data,thread,fence):
    commands={recovery_command(thread,fence.get('request_id')),recovery_command(thread)}
    args=data.get('tool_input')
    name=str(data.get('tool_name','')).rsplit('.',1)[-1]
    if name in {'Bash','exec_command'} and isinstance(args,dict):
        return (args.get('cmd') or args.get('command') or '').strip() in commands
    # Code-mode is just transport for the same single read. No arbitrary JS,
    # parallel calls, file edits or appended commands pass this narrow shape.
    if name=='exec':
        code=args if isinstance(args,str) else (args or {}).get('code','')
        match=re.fullmatch(r'\s*text\(await tools\.exec_command\((\{[\s\S]*\})\)\);?\s*',code)
        if match:
            try:
                call=json.loads(match[1])
                return (call.get('cmd','').strip() in commands and
                        set(call)<={'cmd','sandbox_permissions','justification','max_output_tokens','workdir','yield_time_ms','login','shell','tty'})
            except (ValueError,TypeError,AttributeError):pass
    return False


def acknowledge_live_input(value, *, read_started=None):
    """A real successful lookup, never context emission, releases recovery."""
    thread=value.get('thread_id')
    if (not isinstance(thread,str) or not re.fullmatch(UUID,thread) or
            not value.get('current') or not value.get('can_execute') or value.get('stopped') or
            not value.get('input_id') or not value.get('turn_id')):return False
    path=DATA/(thread+'.fence.json');fence=_load(path,{})
    if not fence.get('pending') or fence.get('reason') not in {'live_read_required','PreCompact','recovery_failed','context_emitted'}:return False
    # A lookup begun before this recovery epoch cannot acknowledge it.
    if read_started is not None and (not isinstance(read_started,(int,float))
            or not fence.get('created',0)<=read_started<=time.time()):return False
    previous=fence.get('request_id')
    if previous and previous!=value.get('request_id'):
        # Only a fresh authoritative lookup, corroborated by the latest local
        # input, can supersede a stale request binding. Cached/delayed ACKs
        # without a lookup start time remain unable to release this fence.
        if read_started is None:return False
        checkpoint=read_checkpoint(thread)
        if (not checkpoint.get('context_valid')
                or checkpoint.get('request_id')!=value.get('request_id')):return False
    # Never clear an Interrupt fence with the old input's live read.
    _save(path,{'pending':False,'reason':'live_input_confirmed','created':time.time(),
                'request_id':value['request_id'],'input_id':value['input_id'],
                'turn_id':value['turn_id'],'recovery_epoch':fence.get('recovery_epoch'),
                'read_started':read_started,
                'superseded_request_id':previous if previous!=value['request_id'] else None})
    return True


def recovery_context(checkpoint,thread):
    """Explicit chronology: retained user-role messages can be historical."""
    request=checkpoint.get('request_id') if checkpoint.get('source')!='native_prompt' else None
    return ('[현재 입력 경계 · 축약 후 원문 재조회 필수]\n'
            '다음 동작은 아래 명령 한 번입니다. 원문을 확인하기 전에는 진행 메시지·답변·질문·메모리 조회·검색을 시작하지 마세요.\n'
            +recovery_command(thread,request)+'\n'
            '조회 결과가 current=true, can_execute=true, stopped=false인 경우에만 반환된 현재 원문과 후속 지시를 사용하세요. '
            '조회가 실패하면 현재 원문을 확인하지 못했다고 알리세요. '
            'Office 전달은 도구 입력으로 올 수 있습니다. 축약본의 user 역할과 메시지 위치로 현재 업무를 고르지 마세요. '
            '아래 기록은 원문 조회를 위한 참고입니다.\n'
            +json.dumps({k:checkpoint.get(k) for k in ('thread_id','request_id','checkpoint_revision','source','user_message','followups')},ensure_ascii=False))


def handle(data):
    thread=data.get('session_id');event=data.get('hook_event_name')
    if not managed(thread):return {}
    # Metadata only, written by a real callback before event-specific work.
    # Installing/trusting a definition is not evidence that it ran.
    _save(DATA/(thread+'.last-event.json'),{
        'thread_id':thread,'event':event,'turn_id':data.get('turn_id'),
        'source':data.get('source'),'tool_name':data.get('tool_name'),
        'observed_at':time.time(),'recovery_revision':REVISION})
    capture=DATA/'capture-start.json'
    if not _load(capture):_save(capture,{'started_at':time.time(),'first_event':event})
    fence_path=DATA/(thread+'.fence.json')
    if event=='UserPromptSubmit':
        prompt=data.get('prompt')
        if not isinstance(prompt,str):return _stop(event,'현재 사용자 입력이 없어 과거 업무로 대체하지 않았습니다.')
        if '## My request:' in prompt:prompt=prompt.rsplit('## My request:',1)[1].strip()
        # Store only this private current prompt, never the full transcript.
        if len(prompt)>16000:return _stop(event,'현재 입력이 복구 한도를 넘어 자동 실행을 보류했습니다.')
        reference=bool(re.match(r'\s*(?:Earlier conversation reference only|<heartbeat>|<send_user_message_question_reply>|\[자동 부서 완료보고)',prompt))
        _save(DATA/(thread+'.native.json'),{'request_id':uuid.uuid4().hex,'user_message':prompt,
              'created':time.time(),'source':'native_prompt','turn_id':data.get('turn_id'),
              'state':'reference' if reference else 'submitted','eligible':not reference and bool(prompt.strip())})
        # A fresh human input is the only reset of an old native stop fence.
        if not reference:
            _save(fence_path,{'pending':False,'reason':'fresh_prompt','created':time.time()})
        return {}
    if event in {'PreCompact','Interrupt'}:
        _save(fence_path,{'pending':True,'reason':event,'turn_id':data.get('turn_id'),'created':time.time()})
        return {}
    if event=='SessionStart' and data.get('source')=='compact':
        checkpoint=read_checkpoint(thread)
        if not checkpoint.get('context_valid'):
            _save(fence_path,{'pending':True,'reason':'recovery_failed','created':time.time()})
            return _stop(event,'현재 지시를 복구하지 못해 자동 실행을 멈췄습니다. 완료된 과거 업무로 대체하지 않습니다.')
        text=recovery_context(checkpoint,thread)
        if len(text)>18000:return _stop(event,'현재 지시가 길어 정확한 복구를 보류했습니다. 과거 업무를 실행하지 않습니다.')
        _save(fence_path,{'pending':True,'reason':'live_read_required','revision':checkpoint['checkpoint_revision'],
                         'request_id':checkpoint.get('request_id') if checkpoint.get('source')!='native_prompt' else None,
                         'recovery_epoch':uuid.uuid4().hex,'created':time.time()})
        return {'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':text}}
    if event=='PreToolUse':
        fence=_load(fence_path,{})
        if fence.get('pending'):
            if _recovery_read(data,thread,fence):return {}
            # Tool-delivered Office inputs do not trigger UserPromptSubmit.
            # Inspect a newer local input before rejecting against the old ID.
            checkpoint=read_checkpoint(thread)
            if checkpoint.get('context_valid') and checkpoint.get('created',0)>fence.get('created',0):
                # A fresh Office delivery may arrive as a tool input instead
                # of UserPromptSubmit. Reject the old pending call once and
                # show its exact replacement goal; don't strand a fresh input
                # behind a previous interruption fence.
                notice=recovery_context(checkpoint,thread)
                if len(notice)<=18000:
                    _save(fence_path,{'pending':True,'reason':'live_read_required','revision':checkpoint['checkpoint_revision'],
                                     'request_id':checkpoint.get('request_id') if checkpoint.get('source')!='native_prompt' else None,
                                     'recovery_epoch':uuid.uuid4().hex,'created':time.time()})
                    return _stop(event,notice)
            if fence.get('reason')=='live_read_required':
                return _stop(event,recovery_command(thread,fence.get('request_id'))+'\n'
                             '이 명령으로 현재 원문부터 읽으세요. 조회 전에는 과거 메시지에 답하거나 검색하지 마세요.')
            return _stop(event,'축약/중지 뒤 현재 지시 복구가 확인되지 않았습니다. 과거 업무의 도구 실행을 보류합니다.')
    return {}


def main():
    # Hook JSON is UTF-8 even when Windows defaults to cp949.
    sys.stdin.reconfigure(encoding='utf-8-sig',errors='strict')
    raw=sys.stdin.read(131073)
    event='SessionStart'
    try:
        if len(raw)>131072:raise ValueError('Hook input too long')
        data=json.loads(raw);event=data.get('hook_event_name',event)
        result=handle(data)
    except Exception as error:
        # Explicit documented output; a malformed/failed hook is not a denial.
        result=_stop(event,'현재 지시 복구 장치가 정상 확인되지 않아 실행을 보류합니다.'+' ['+type(error).__name__+'; chars='+str(len(raw))+']')
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
