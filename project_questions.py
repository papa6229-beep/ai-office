"""Read user-facing prompts from registered local chats; never edit Codex state.

The desktop read API omits async question cards. This bounded append-only reader
extracts only question fields. Explicit human answers use the existing chat send
path; it does not resolve native command/permission approvals.
"""
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-28-local-questions-v2'
UUID=r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
MAX_READ=8*1024*1024
_lock=threading.RLock()
_reply_lock=threading.Lock()
_registry_at=0
_registry={}
_paths={}
_cache={}


def registered():
    global _registry,_registry_at
    with _lock:
        if time.monotonic()-_registry_at<15:return dict(_registry)
        with closing(sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
            row=c.execute("SELECT data FROM office_departments WHERE id='registry'").fetchone()
        result={}
        for team in (json.loads(row[0]) if row else {}).get('teams',[]):
            for agent in team.get('agents',[]):
                thread=agent.get('thread_id')
                if re.fullmatch(UUID,thread or '') and agent.get('host','local')=='local':
                    result[thread]={'id':thread,'host':'local','title':agent.get('name') or team.get('name') or '요원',
                                    'department':team.get('name','')}
        _registry=result;_registry_at=time.monotonic()
        return dict(result)


def _path(thread):
    prior=_paths.get(thread)
    if prior and time.monotonic()-prior[0]<300:return prior[1]
    root=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sessions'
    files=list(root.glob('*/*/*/*'+thread+'.jsonl'))
    path=files[0] if len(files)==1 and files[0].resolve().is_relative_to(root.resolve()) else None
    _paths[thread]=(time.monotonic(),path)
    return path


def _text(p):
    content=p.get('content',[])
    return '\n'.join(x.get('text','') for x in content if isinstance(x,dict) and isinstance(x.get('text'),str))


def _human_input(state,text):
    match=re.search(r'<send_user_message_question_reply>\s*([\s\S]*?)\s*</send_user_message_question_reply>',text)
    if match:
        try:
            for answer in json.loads(match[1]):
                ident=json.loads(answer.get('questionItemId','null'))
                if isinstance(ident,list) and len(ident)==3:
                    state['prompts'].pop(ident[1],None)
        except (ValueError,TypeError,AttributeError):pass
        return
    if text.strip() and not text.lstrip().startswith(('Earlier conversation reference only','<heartbeat>')):
        state['prompts'].clear()


def _consume(state,row):
    p=row.get('payload') or {}
    if not isinstance(p,dict):return
    typ=p.get('type')
    if row.get('type')=='turn_context' or (row.get('type')=='event_msg' and typ=='task_started'):
        turn=p.get('turn_id')
        if turn and turn!=state.get('turn_id'):
            state.update(turn_id=turn,prompts={},aborted=False)
        return
    if row.get('type')=='event_msg' and typ=='turn_aborted':
        state.update(prompts={},aborted=True);return
    if row.get('type')=='event_msg' and typ=='user_message':
        _human_input(state,str(p.get('message','')));return
    if row.get('type')!='response_item':return
    if typ=='message' and p.get('role')=='user':
        _human_input(state,_text(p));return
    if typ=='function_call_output':
        output=p.get('output')
        if isinstance(output,str) and output.startswith('<codex_delegation>'):
            body=output.split('<input>',1)[-1]
            if re.match(r'\s*\[AI OFFICE (?:작업창 요청|요청) [0-9a-f]{32}\]',body) and '[자동 부서 완료보고' not in body[:500]:
                _human_input(state,body)
        return
    if typ!='function_call' or p.get('name') not in {'request_user_input_async','request_user_input'}:return
    call=p.get('call_id')
    if not isinstance(call,str) or not re.fullmatch(r'call_[A-Za-z0-9_-]{1,160}',call):return
    raw=p.get('arguments','')
    if not isinstance(raw,str) or len(raw)>65536:return
    try:args=json.loads(raw)
    except ValueError:return
    questions=[]
    for index,q in enumerate(args.get('questions',[])[:3]):
        if not isinstance(q,dict):continue
        title=q.get('title') or q.get('question')
        if not isinstance(title,str) or not title.strip():continue
        options=[]
        for option in (q.get('options') or [])[:12]:
            label=option.get('label') if isinstance(option,dict) else option
            if isinstance(label,str) and label.strip():options.append(label[:500])
        questions.append({'id':str(index),'text':title[:4000],'options':options})
    if questions:
        state['observed_calls']=state.get('observed_calls',0)+1
        state['observed_freeform']=state.get('observed_freeform',0)+sum(not q['options'] for q in questions)
        state['prompts'][call]={'id':call,'turn_id':state.get('turn_id'),'created':row.get('timestamp'),
                               'questions':questions,'replyable':p['name']=='request_user_input_async',
                               'transport':'chat' if p['name']=='request_user_input_async' else 'native'}
        while len(state['prompts'])>20:state['prompts'].pop(next(iter(state['prompts'])))


def _replies(thread):
    with closing(sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='project_question_replies'").fetchone():return {}
        return {r[0]:{'state':r[1],'request_id':r[2]} for r in c.execute(
            'SELECT question_id,state,request_id FROM project_question_replies WHERE thread_id=?',(thread,))}


def read(thread,host='local',turn=None):
    if host!='local' or thread not in registered():return {'available':False,'questions':[],'revision':REVISION}
    with _lock:
        path=_path(thread)
        if path is None:return {'available':False,'questions':[],'revision':REVISION}
        try:
            stat=path.stat();state=_cache.get(thread)
            if state is None or stat.st_size<state['offset'] or stat.st_ino!=state['inode']:
                state={'offset':max(0,stat.st_size-MAX_READ),'inode':stat.st_ino,'turn_id':None,'prompts':{},'aborted':False}
                _cache[thread]=state;initial=True
            else:initial=False
            if stat.st_size>state['offset']:
                start=state['offset']
                with path.open('rb') as stream:stream.seek(start);raw=stream.read(MAX_READ)
                if initial and start:
                    first=raw.find(b'\n')
                    if first>=0:start+=first+1;raw=raw[first+1:]
                end=raw.rfind(b'\n')
                if end>=0:
                    for line in raw[:end].split(b'\n'):
                        if len(line)>1024*1024:continue
                        try:_consume(state,json.loads(line))
                        except (ValueError,UnicodeDecodeError,TypeError,AttributeError):continue
                    state['offset']=start+end+1
                elif len(raw)>=MAX_READ:state['offset']=start+len(raw)
            if turn:
                state['live_turn']=turn.get('id');state['live_started']=turn.get('startedAt')
            prompts=[];replies=_replies(thread) if state['prompts'] else {}
            for q in state['prompts'].values():
                if q.get('turn_id') and state.get('live_turn') and q['turn_id']!=state['live_turn']:
                    try:created=datetime.datetime.fromisoformat(q['created'].replace('Z','+00:00')).timestamp()
                    except (ValueError,AttributeError):continue
                    if (state.get('live_started') or 0)>created:continue
                if not q.get('turn_id'):
                    try:created=datetime.datetime.fromisoformat(q['created'].replace('Z','+00:00')).timestamp()
                    except (ValueError,AttributeError):continue
                    if not state.get('live_started') or created<state['live_started']:continue
                receipt=replies.get(q['id'])
                if receipt and receipt['state']=='sent':continue
                prompts.append({**q,'delivery':receipt['state'] if receipt else None,
                                'replyable':q['replyable'] and not receipt and state['offset']>=stat.st_size})
            return {'available':True,'questions':prompts,'revision':REVISION,'caught_up':state['offset']>=stat.st_size,
                    'source':'local_readonly_rollout','turn_id':state.get('turn_id'),
                    'observed_calls':state.get('observed_calls',0),'observed_freeform':state.get('observed_freeform',0)}
        except OSError:return {'available':False,'questions':[],'revision':REVISION,'error':'질문 기록을 읽지 못했어요.'}


def batch():
    return {'items':[{**row,**read(thread)} for thread,row in registered().items()],'revision':REVISION}


def answer(db,payload):
    if set(payload)!={'id','host','question_id','request_id','answers'}:raise ValueError('답변할 질문과 대화를 확인하세요.')
    thread=payload['id'];call=payload['question_id'];request=payload['request_id'];answers=payload['answers']
    import project_panels
    target=project_panels._target(thread,payload['host'])
    if not isinstance(call,str) or not re.fullmatch(r'call_[A-Za-z0-9_-]{1,160}',call):raise ValueError('질문 기록 번호를 확인하세요.')
    if not isinstance(request,str) or not re.fullmatch('[0-9a-f]{32}',request):raise ValueError('답변 전송 번호를 확인하세요.')
    if not isinstance(answers,list) or not 1<=len(answers)<=3 or any(not isinstance(a,str) or not a.strip() or len(a)>3000 for a in answers):
        raise ValueError('각 질문에 답변을 입력하거나 선택해 주세요.')
    signature=hashlib.sha256(json.dumps([thread,call,answers],ensure_ascii=False).encode()).hexdigest()
    with _reply_lock:
        with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
            c.execute('CREATE TABLE IF NOT EXISTS project_question_replies (thread_id TEXT NOT NULL,question_id TEXT NOT NULL,request_id TEXT NOT NULL,signature TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(thread_id,question_id))')
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT state,signature,request_id FROM project_question_replies WHERE thread_id=? AND question_id=?',(thread,call)).fetchone()
            if old:
                if old[1]!=signature:raise ValueError('이미 답변 전달을 시도한 질문입니다. 원래 전달 결과를 먼저 확인하세요.')
                return {'state':old[0],'request_id':old[2],'reused':True}
        live=project_panels._call('read_thread',{**target,'turnLimit':1,'includeOutputs':False,'maxOutputCharsPerItem':300})
        if live.get('thread',{}).get('id')!=thread or live.get('thread',{}).get('kind','codex')!='codex':raise ValueError('질문한 원래 대화를 확인하지 못했어요.')
        turn=next(iter(live.get('turns',[])),{})
        if not turn.get('id') or turn.get('status') not in {'inProgress','completed'}:
            raise ValueError('중지되거나 상태가 확인되지 않은 작업에는 질문 답변을 전달하지 않았어요.')
        q=next((q for q in read(thread,payload['host'],turn).get('questions',[]) if q['id']==call),None)
        if not q or not q['replyable'] or len(q['questions'])!=len(answers):raise ValueError('현재 답변할 수 있는 질문이 아니에요. 질문 상태를 새로 확인하세요.')
        message='[Office에서 사용자가 제출한 질문 답변]\n질문 기록: '+call+'\n'+ '\n\n'.join('질문: '+q['text'][:700]+'\n답변: '+a.strip() for q,a in zip(q['questions'],answers))
        message+='\n\n이 답변은 위 확인 질문에 한정됩니다. 이미 중지되거나 새 지시로 대체된 작업은 이 답변만으로 재개하지 마세요. 플랫폼의 명령 실행·접근권한 승인은 별도 경계를 유지하세요.'
        with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
            c.execute('INSERT INTO project_question_replies VALUES(?,?,?,?,?)',(thread,call,request,signature,'sending'))
        try:result=project_panels.send(db,{'id':thread,'host':payload['host'],'request_id':request,'message':message})
        except Exception:
            with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
                c.execute("UPDATE project_question_replies SET state='unknown' WHERE thread_id=? AND question_id=?",(thread,call))
            raise
        with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
            c.execute('UPDATE project_question_replies SET state=? WHERE thread_id=? AND question_id=?',(result['state'],thread,call))
        return result
