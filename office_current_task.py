"""Thread-bound current input recovery. Reads never enqueue or resume work.

Native tools remain outside this module's enforcement boundary. A correct
snapshot alone cannot prove that an arbitrary subsequent command is in scope.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-30-live-task-read-ack-v9'
UUID=r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
ID=r'[0-9a-f]{32}'


def _ro():
    return sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=5)


def _denied(thread, reason, **extra):
    return dict(current=False,thread_id=thread,can_execute=False,
                reason=reason,recovery_revision=REVISION,**extra)


def _input(item,hq,thread):
    import desktop_chat_bridge as bridge
    if item.get('type')=='userMessage':
        text=bridge._text(item)
        if '## My request:' in text:text=text.rsplit('## My request:',1)[1].strip()
        return text,'native',bool(item.get('truncated'))
    if not bridge._is_delivery(item):return None
    output=item.get('output')
    if not isinstance(output,dict):return '','unresolved_delivery',True
    match=re.match(r'\A<codex_delegation>\s*<source_thread_id>('+UUID+r')</source_thread_id>\s*<input>([\s\S]*)',output.get('text',''))
    if not match:return '','untrusted_delivery',True
    text=match[2].split('</input>',1)[0]
    truncated=bool(output.get('truncated'))
    # A department's returned evidence is not a new human instruction. Keep
    # the actual input boundary in this same live turn, including at HQ.
    if match[1]!=hq:return text,'reference_delivery',truncated
    marked=re.match(r'\A\[AI OFFICE 요청 ('+ID+r')\]',text.strip())
    if marked:
        row=_record('desktop_chat_requests',marked[1],thread)
        if row and row.get('origin')=='department_report' and not row.get('user_goal'):
            return text,'reference_delivery',truncated
    if reference_delivery(text,thread):return text,'reference_delivery',truncated
    return text,'office_delivery',truncated


def reference_delivery(text,thread):
    """Data-only HQ follow-ups; the caller still needs a real same-turn input."""
    if text.startswith('[기존 조사 결과 공유 · 자료이며 새 사용자 지시/새 업무 배정 아님]'):return True
    if text.startswith(('동일 ','현재 진행 중인 ')) and '새 업무 지시가 아닙니다.' in text:return True
    # A same-task HQ observation is data. Skip it only in this live turn;
    # read_current still requires the original real input in that same turn.
    reference=re.match(r'\A(?:현재 진행 중인 |동일 )[^\n]{0,100}?업무\s*([0-9a-f]{32})(?:에 대한|의) ',text.strip())
    if reference:
        task=_record('office_department_tasks',reference[1],thread)
        if task:return True
    return False


def _record(table, ident, thread):
    # Names are code constants only; never supplied by the caller.
    with closing(_ro()) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():return None
        if table=='project_panel_sends':
            row=c.execute('SELECT data,state FROM project_panel_sends WHERE id=? AND thread_id=? AND host=?',(ident,thread,'local')).fetchone()
            if row:return dict(json.loads(row[0]),state=row[1])
            return None
        row=c.execute('SELECT data FROM '+table+' WHERE id=?',(ident,)).fetchone()
    data=json.loads(row[0]) if row else None
    return data if data and data.get('thread_id')==thread else None


def _resolve(text,source,item,turn,thread,hq):
    ident=None
    match=re.match(r'\A\[AI OFFICE (작업창 요청|요청) ('+ID+r')\]',text.strip())
    if match and source=='office_delivery':
        category,ident=match.groups()
        if category=='작업창 요청':
            row=_record('project_panel_sends',ident,thread)
            if not row:return _denied(thread,'대상 대화와 일치하는 작업창 전달 기록이 없습니다.')
            goal=row.get('message','');state=row['state'];active=state=='sent'
            source='project_panel'
        else:
            row=_record('office_department_tasks',ident,thread)
            if row:
                goal=row.get('instruction','');state=row.get('state');active=state in {'dispatching','working'}
                source='department_assignment'
            elif thread==hq:
                import office_relay_context
                value=office_relay_context._read_office_record(ident,thread)
                if not value.get('current'):
                    return dict(value,recovery_revision=REVISION)
                if not value.get('user_message'):
                    return _denied(thread,'완료보고 또는 원문 없는 Office 기록은 새 실행 지시가 아닙니다.',request_id=ident)
                return value
            else:return _denied(thread,'이 부서의 현재 배정 기록과 일치하지 않습니다.',request_id=ident)
    elif source=='office_delivery':
        # Compatibility for already delivered plain panel inputs, never fuzzy
        # title/keyword matching. A newer boundary cannot revive an older row.
        with closing(_ro()) as c:
            exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='project_panel_sends'").fetchone()
            rows=c.execute('SELECT id,data,state FROM project_panel_sends WHERE thread_id=? AND host=? ORDER BY created DESC LIMIT 20',(thread,'local')).fetchall() if exists else []
        matched=[]
        for rid,raw,state in rows:
            row=json.loads(raw);goal=row.get('message','')
            if goal and (text==goal or text.startswith(goal+'\n\n')):
                matched.append((rid,goal,state))
        if len(matched)!=1:return _denied(thread,'표식 없는 전달의 원문이 하나로 확인되지 않습니다.')
        ident,goal,state=matched[0];active=state=='sent';source='legacy_project_panel'
    else:
        goal=text;state=turn.get('status');active=state=='inProgress'
        ident=hashlib.sha256((thread+'|'+str(item.get('id'))).encode()).hexdigest()[:32]
        if re.match(r'\s*(?:Earlier conversation reference only|<heartbeat>|<send_user_message_question_reply>)',text):
            return _denied(thread,'이 입력은 과거 참고 또는 제어 자료입니다. 단독으로 업무를 재개하지 않습니다.')
    stopped=turn.get('status') in {'interrupted','cancelled','failed'}
    in_progress=turn.get('status')=='inProgress'
    digest=hashlib.sha256(json.dumps([thread,turn.get('id'),item.get('id'),ident,goal,state],ensure_ascii=False).encode()).hexdigest()
    return {'current':True,'request_id':ident,'thread_id':thread,'turn_id':turn.get('id'),
            'input_id':item.get('id'),'input_revision':digest,'source':source,
            'state':state,'can_execute':bool(active and in_progress and not stopped),
            'stopped':stopped,'user_message':goal,'recovery_revision':REVISION,
            'reference':'실제 최신 입력 경계입니다. 이후 직접 사용자 지시가 우선합니다. 완료·중지·확인불가 기록은 재실행 권한이 아닙니다.'}


def read_current(request_id=None, *, thread_id=None):
    read_started=time.time()
    if request_id is not None and not re.fullmatch(ID,str(request_id)):raise ValueError('요청 ID 형식을 확인하세요.')
    thread=thread_id or os.environ.get('CODEX_THREAD_ID')
    if not isinstance(thread,str) or not re.fullmatch(UUID,thread):
        return _denied(thread,'실행 대화 ID를 확인하지 못했습니다. 본부 대화로 대체하지 않습니다.')
    # A compact checkpoint is recovery context, never a freshness proof.
    # Even a previously emitted checkpoint can precede a new native input.
    # Read the actual latest turn on every explicit current-input lookup;
    # unavailable transport must fail closed instead of reviving a snapshot.
    import desktop_chat_bridge as bridge,project_panels
    hq=bridge.configuration().get('thread_id')
    try:
        # One live turn with bounded outputs is enough to identify exact
        # delivery prefixes. Original Office goals still come from their DB
        # records; there is no scan back into completed historical turns.
        args={'threadId':thread,'hostId':'local','turnLimit':1,'includeOutputs':True,'maxOutputCharsPerItem':6000}
        data=project_panels._call('read_thread',args)
        if data.get('thread',{}).get('id')!=thread:return _denied(thread,'조회 대상 대화가 다릅니다.')
        turns=sorted(data.get('turns',[]),key=lambda t:t.get('startedAt') or 0,reverse=True)
        if not turns:return _denied(thread,'실제 입력 턴을 확인하지 못했습니다.')
        turn=turns[0]
        candidates=[i for i in turn.get('items',[]) if i.get('type')=='userMessage' or bridge._is_delivery(i)]
        if not candidates:return _denied(thread,'현재 턴에 사용자 입력 경계가 없습니다. 과거 턴을 대신 선택하지 않습니다.')
        for item in reversed(candidates):
            value=_input(item,hq,thread)
            if value[1]!='reference_delivery':break
        else:
            return _denied(thread,'현재 턴에는 보고 자료만 있습니다. 이전 턴의 업무를 재개하지 않습니다.')
        text,source,truncated=value
        # A marked delivery recovers its complete original from the exact DB
        # record, not this bounded transport preview. Native input has no such
        # canonical source and must be complete.
        marked=re.match(r'\A\[AI OFFICE (?:작업창 요청|요청) ('+ID+r')\]',text.strip())
        if not text or (truncated and not (source=='office_delivery' and marked)):
            return _denied(thread,'현재 입력이 없거나 잘렸습니다. 과거 기록으로 대체하지 않습니다.')
        if source=='native' and text.lstrip().startswith('<send_user_message_question_reply>'):
            import question_reply_context
            link=question_reply_context.origin(thread,text,turn.get('id'),hq)
            if not link.get('available'):
                return _denied(thread,link['reason'],must_pause=True,question_reply=True)
            origin=link['origin']
            # A native goal from a completed earlier turn is never revived by
            # a late answer. Office goals also pass their exact live DB fence.
            if origin['source']=='native' and (origin['turn_id']!=turn.get('id') or not any(
                    (v:=_input(candidate,hq,thread)) and v[1]=='native' and v[0]==origin['text'] for candidate in candidates)):
                import department_work
                tracked=department_work.pending_question_owner(ROOT/'office.db',thread,origin,link['answers'],item.get('id'))
                if not tracked:return _denied(thread,'완료·중지·대상 불명인 이전 네이티브 업무는 질문 답변만으로 재개하지 않습니다.',must_pause=True)
            result=_resolve(origin['text'],origin['source'],item,turn,thread,hq)
            if result.get('source')=='department_assignment' and result.get('state')=='waiting' and turn.get('status')=='inProgress':
                task=_record('office_department_tasks',result['request_id'],thread) or {}
                if not task.get('direct_stopped') and {r['call'] for r in link['answers']}<=set(task.get('question_ids',[])):
                    result['can_execute']=True
            if (not result.get('current') or result.get('stopped')
                    or (turn.get('status')=='inProgress' and not result.get('can_execute'))):
                return _denied(thread,'원래 질문의 업무가 현재 활성 상태가 아닙니다. 재개하지 않았습니다.',must_pause=True)
            replies=[{'id':'question:'+r['call']+':'+str(r['index']),'kind':'question_answer',
                      'question_id':r['call'],'question_index':r['index'],'question':r['question'],
                      'message':r['answer'],'origin_request_id':result['request_id']} for r in link['answers']]
            result['followups']=[*result.get('followups',[]),*replies]
            result['question_reply']={'revision':link['revision'],'evidence_sha256':link['evidence_sha256'],
                                     'origin_turn_id':origin['turn_id'],'native_card_resolved':False}
            result['input_revision']=hashlib.sha256(json.dumps([result['request_id'],turn.get('id'),item.get('id'),result['user_message'],result['followups']],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            import execution_corrections
            result['correction_reuse']=execution_corrections.references(ROOT/'office.db',['current_input'])
        else:result=_resolve(text,source,item,turn,thread,hq)
        # Apply the live turn boundary also to an Office DB snapshot.
        result.update(turn_id=turn.get('id'),input_id=item.get('id'),turn_status=turn.get('status'),recovery_revision=REVISION)
        if turn.get('status')!='inProgress':result['can_execute']=False
        if request_id and result.get('request_id')!=request_id:
            return _denied(thread,'최신 입력이 요청한 업무와 다릅니다. 이전 업무를 재개하지 마세요.',
                           requested_id=request_id,latest_request_id=result.get('request_id'),current_input=result)
        if result.get('current'):
            import department_work
            if thread in department_work.registered(ROOT/'office.db'):
                result['work_tracking']={'entry':str(ROOT/'department_runtime.py'),
                    'rule':'실제 직접 산출물 업무는 declare --request-id 현재ID --title 짧은업무명 --kind 업무종류로 연결합니다. 기존 배정/추가 입력은 재사용하고 질문·잡담·조회는 새 업무가 아닙니다. 완료보고는 코드가 처리합니다.'}
            elif thread==hq:
                result['work_tracking']={'entry':str(ROOT/'department_runtime.py'),'rule':'search/inbox/route로 기존 상태·담당을 조회하고 현재 의도/예외를 한 번 판단합니다. assign은 현재 본부ID·유지범위·완료기준으로 비동기 접수합니다. 이력/보고는 새 권한이 아닙니다.'}
        if result.get('current') and result.get('can_execute') and not result.get('stopped'):
            import office_context_hook
            result['recovery_live_read_ack']=office_context_hook.acknowledge_live_input(result,read_started=read_started)
            result['context_order_rule']='현재 목표는 이 실제 입력입니다. 축약된 user 역할 기록의 마지막 문장을 새 지시로 취급하지 마세요. 새 입력은 실제 input_id/turn_id로만 구별합니다.'
        return result
    except Exception as exc:
        details=bridge._failure(exc)
        return _denied(thread,'현재 입력 조회를 완료하지 못했습니다. 과거 요약으로 목표를 대신 선택하지 말고 실행을 보류하세요.',
                       error_type=type(exc).__name__,connection_failure=details,must_pause=True)


def require_current(request_id, *, thread_id=None):
    """Gate a managed write/publication on a live, exact input boundary.

    This validates identity/lifecycle, not the semantic scope of arbitrary
    native commands. A caller must still perform only the requested effects.
    """
    value = read_current(request_id, thread_id=thread_id)
    if (not value.get('current') or value.get('request_id') != request_id
            or not value.get('can_execute') or value.get('stopped')):
        error=ValueError('현재 입력과 실행 요청이 일치하는지 확인하지 못해 반영하지 않았습니다. '
                         + value.get('reason', '완료·중지·다른 요청의 결과를 사용하지 마세요.'))
        error.code=(value.get('connection_failure') or {}).get('code','current_input_unavailable')
        error.stage='current_input'
        raise error
    return value


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Read the exact live input; never enqueue or resume work.')
    parser.add_argument('--read-current',nargs='?',const='',required=True)
    parser.add_argument('--thread-id',required=True)
    args=parser.parse_args()
    print(json.dumps(read_current(args.read_current or None,thread_id=args.thread_id),ensure_ascii=True))
