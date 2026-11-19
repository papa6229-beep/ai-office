"""Correlate a live structured answer with its exact local question call.

Reads a bounded rollout; never edits Codex state, resolves UI, or resumes work.
The caller must still validate the originating request against current DB state.
"""
import hashlib
import json
import re

REVISION='2026-11-19-live-question-origin-v3'
MAX_READ=8*1024*1024
TOOLS={'request_user_input_async','request_user_input'}


def decode(text):
    match=re.fullmatch(r'\s*<send_user_message_question_reply>\s*([\s\S]*?)\s*</send_user_message_question_reply>\s*',text)
    if not match:return None
    try:
        rows=json.loads(match[1]);result=[];seen=set()
        if not isinstance(rows,list) or not 1<=len(rows)<=3:return None
        for row in rows:
            tool,call,index=json.loads(row['questionItemId'])
            if (tool not in TOOLS or not re.fullmatch(r'call_[A-Za-z0-9_-]{1,160}',call)
                    or type(index) is not int or not 0<=index<3 or (call,index) in seen
                    or not isinstance(row.get('question'),str) or len(row['question'])>4000
                    or not isinstance(row.get('answer'),str) or not 1<=len(row['answer'].strip())<=3000):return None
            seen.add((call,index));result.append({'tool':tool,'call':call,'index':index,'question':row['question'],'answer':row['answer']})
        return result
    except (ValueError,KeyError,TypeError):return None


def _live_origin(thread,text,turn_id,hq,turn,answers):
    """Use ordered official items when compaction omitted a local delivery.

    A question's actual call ID must sit between the same input and its reply.
    Assistant prose, request markers in summaries and old DB rows cannot anchor it.
    """
    import desktop_chat_bridge as bridge
    from office_current_task import _input
    wanted={a['call'] for a in answers}
    items=turn.get('items',[])
    if not wanted <= {i.get('id') for i in items}:return None
    if turn.get('id')!=turn_id:return {'available':False,'reason':'질문을 조회한 실제 턴이 다릅니다.'}
    last=None;matched=None;calls={};serial=0
    for item in items:
        if item.get('type')=='userMessage' or bridge._is_delivery(item):
            incoming,source,truncated=_input(item,hq,thread)
            if source=='reference_delivery':continue
            if decode(incoming) is not None:
                if incoming.strip()==text.strip():matched=last
                continue
            if source not in {'native','office_delivery'}:continue
            if incoming.lstrip().startswith(('Earlier conversation reference only','<heartbeat>')):continue
            # Marked deliveries recover their complete original from their DB
            # fence in read_current; an incomplete native input cannot do so.
            marked=re.match(r'\A\[AI OFFICE (?:작업창 요청|요청) [0-9a-f]{32}\]',incoming.strip())
            if not incoming or (source=='office_delivery' and not marked) or (truncated and not marked):
                return {'available':False,'reason':'질문의 실제 원래 입력이 없거나 잘렸습니다.'}
            serial+=1
            last={'serial':serial,'text':incoming,'source':source,'turn_id':turn_id}
        if item.get('id') in wanted:
            if item['id'] in calls:return {'available':False,'reason':'실제 질문 호출이 중복되었습니다.'}
            calls[item['id']]=last
    if not matched or last!=matched or any(calls.get(a['call'])!=matched for a in answers):
        return {'available':False,'reason':'질문과 답변 사이에 입력이 바뀌었거나 실제 질문 연결이 없습니다.'}
    return {'available':True,'origin':matched}


def origin(thread,text,turn_id,hq,*,live_turn=None):
    answers=decode(text)
    if answers is None:return {'available':False,'reason':'구조화된 질문 답변 형식을 확인하지 못했습니다.'}
    live=_live_origin(thread,text,turn_id,hq,live_turn,answers) if live_turn is not None else None
    if live is not None and not live.get('available'):return live
    import project_questions
    path=project_questions._path(thread)
    if not path:return {'available':False,'reason':'이 대화의 질문 원문을 읽을 수 없습니다.'}
    try:
        with path.open('rb') as stream:
            size=path.stat().st_size;start=max(0,size-MAX_READ);stream.seek(start);raw=stream.read(MAX_READ)
        if start:raw=raw.partition(b'\n')[2]
        # Ignore an incomplete final record. Missing evidence is a technical wait.
        raw=raw[:raw.rfind(b'\n')] if b'\n' in raw else b''
        current_turn=None;last=None;serial=0;calls={};matched=None;aborted=set()
        wanted={r['call'] for r in answers}
        for line in raw.splitlines():
            if len(line)>1024*1024:continue
            try:row=json.loads(line)
            except (ValueError,UnicodeError):continue
            if not isinstance(row,dict):continue
            p=row.get('payload') or {}
            if not isinstance(p,dict):continue
            if row.get('type')=='event_msg' and p.get('type')=='turn_aborted':
                aborted.add(p.get('turn_id') or current_turn);continue
            if row.get('type')=='turn_context' or (row.get('type')=='event_msg' and p.get('type')=='task_started'):
                current_turn=p.get('turn_id') or current_turn;continue
            if row.get('type')!='response_item':continue
            kind=p.get('type');incoming=None
            if kind=='message' and p.get('role')=='user':
                incoming='\n'.join(x.get('text','') for x in p.get('content',[]) if isinstance(x,dict) and isinstance(x.get('text'),str))
                reply=decode(incoming)
                if reply is not None:
                    if current_turn==turn_id:
                        matched=last if incoming.strip()==text.strip() else None
                    continue
                if incoming.lstrip().startswith(('Earlier conversation reference only','<heartbeat>')):continue
                source='native'
            elif kind=='function_call_output':
                output=p.get('output')
                if not isinstance(output,str):continue
                delivery=re.match(r'\A<codex_delegation>\s*<source_thread_id>([0-9a-f-]{36})</source_thread_id>\s*<input>([\s\S]*?)</input>',output)
                if not delivery or delivery[1]!=hq:continue
                incoming=delivery[2]
                # Only a real marked Office request can establish this origin.
                if not re.match(r'\A\[AI OFFICE (?:작업창 요청|요청) [0-9a-f]{32}\]',incoming.strip()):continue
                source='office_delivery'
            elif kind=='function_call' and p.get('call_id') in wanted:
                tool=p.get('name','').rsplit('.',1)[-1]
                if tool not in TOOLS or (not last and live is None):continue
                try:questions=json.loads(p.get('arguments','')).get('questions',[])
                except (ValueError,TypeError,AttributeError):continue
                calls[p['call_id']]={'origin':last,'turn_id':current_turn,'tool':tool,'questions':questions}
            if incoming is not None:
                serial+=1;last={'serial':serial,'text':incoming,'source':source,'turn_id':current_turn}
        if live is not None:
            matched=live['origin']
        if current_turn!=turn_id or (live is None and (matched is None or last is None or last['serial']!=matched['serial'])):
            return {'available':False,'reason':'실제 최신 답변과 원래 질문의 연속된 입력 근거가 없습니다. 새 입력 이후 과거 답변은 재개하지 않습니다.'}
        for answer in answers:
            call=calls.get(answer['call'])
            if (not call or call['tool']!=answer['tool'] or call['turn_id'] in aborted
                    or (live is not None and call['turn_id']!=turn_id)
                    or (live is None and (not call['origin'] or call['origin']['serial']!=matched['serial']))):
                return {'available':False,'reason':'질문 뒤 새 입력이 있거나 원래 요청이 다른 답변입니다.'}
            questions=call['questions']
            if not isinstance(questions,list) or answer['index']>=len(questions):return {'available':False,'reason':'원래 질문 번호가 다릅니다.'}
            question=questions[answer['index']]
            if not isinstance(question,dict) or (question.get('title') or question.get('question'))!=answer['question']:
                return {'available':False,'reason':'원래 질문 원문과 답변 대상이 다릅니다.'}
        return {'available':True,'origin':matched,'answers':answers,'revision':REVISION,
                'evidence_sha256':hashlib.sha256(text.encode()).hexdigest()}
    except OSError as error:return {'available':False,'reason':'원래 질문 연결을 읽지 못했습니다.','error_type':type(error).__name__}
