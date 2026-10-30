"""Direct entry to existing registered code routines; never starts a model."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-29-correction-reuse-v3'


def guidance():
    import sys,office_routines,artifact_review
    return {'revision':REVISION,'python':sys.executable,'entry':str(ROOT/'routine_runtime.py'),
        'commands':['list','run --request-id 현재요청ID'],
        'local_artifact_review':artifact_review.guidance(),
        'catalog':[{'id':r['id'],'example':r['example']} for r in office_routines.CATALOG],
        'rule':'등록된 업무는 이 실행기 또는 같은 이름의 공통 도구를 바로 호출합니다. 소스·설치·연결 조사는 실제 실패·변경 때만 하며 성공한 효과는 반복하지 않습니다.'}


def run(request_id=None):
    import office_relay_context,office_routines,office_fastlane,assistant_tools
    current=office_relay_context.read_current(request_id)
    if not current.get('current') or not current.get('can_execute') or current.get('stopped'):
        raise ValueError('실제 현재 입력이 아니라 실행하지 않았어요.')
    request_id=current['request_id']
    if any(r.get('kind') in {'message','question_answer'} for r in current.get('followups',[])):
        return {'state':'needs_interpretation','reason':'원래 목표와 추가 지시·질문 답변을 함께 해석해 실패한 단계만 이어가세요. 전체 등록 업무를 자동 재실행하지 않았습니다.',
                'correction_reuse':current.get('correction_reuse',{})}
    route=office_routines.match(current['user_message'])
    if not route:return {'state':'unregistered','reason':'현재 요청 전체와 일치하는 등록 실행기가 없습니다.','model_calls':0}
    folder=ROOT/'local_backups'/'registered-routines';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(request_id+'.json')
    signature=hashlib.sha256(json.dumps(route,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    # Exclusive reservation prevents concurrent code execution and blind retries.
    import execution_corrections
    initial={'request_id':request_id,'routine':route['id'],'signature':signature,'state':'running','mode':'code','model_calls':0,
             'runtime_revision':REVISION,'correction_reuse':execution_corrections.references(ROOT/'office.db',[route['id']])}
    try:
        with path.open('x',encoding='utf-8') as stream:json.dump(initial,stream,ensure_ascii=False)
    except FileExistsError:
        old=json.loads(path.read_text(encoding='utf-8'))
        if old.get('signature')!=signature:raise ValueError('같은 요청의 등록 업무가 변경됐어요.')
        return dict(old,replayed=True,automatic_retry=False)
    result=initial
    try:
        client_id=None
        with closing(sqlite3.connect(ROOT/'office.db')) as c:
            row=c.execute('SELECT payload FROM assistant_requests WHERE id=?',(request_id,)).fetchone()
            if row:
                client=json.loads(row[0]).get('payload',{}).get('client')
                client_id=client.get('id') if isinstance(client,dict) else client
        registry=assistant_tools.ToolRegistry(ROOT/'office.db');calls=[]
        def call(name,args):
            latest=office_relay_context.read_current(request_id)
            if (not latest.get('current') or not latest.get('can_execute') or latest.get('stopped')
                    or latest.get('input_revision')!=current.get('input_revision')):
                raise ValueError('현재 지시가 바뀌어 다음 실행을 중단했습니다. 성공한 단계를 반복하지 마세요.')
            context={'request_id':request_id,'root_request_id':request_id,'client_id':client_id,
                'step_id':uuid.uuid5(uuid.NAMESPACE_URL,request_id+':registered:'+str(len(calls))+':'+name).hex}
            value=registry.execute(name,args,context);calls.append({'tool':name,'status':value.get('status')});return value
        with office_fastlane.native_control(ROOT/'office.db',request_id):
            message=office_routines.execute(route,call)
        result.update(state='completed' if all(r['status']=='ok' for r in calls) else 'partial',message=message,calls=calls)
    except BaseException as error:
        result.update(state='uncertain',error_type=type(error).__name__,automatic_retry=False);raise
    finally:
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['list','run']);parser.add_argument('--request-id')
    args=parser.parse_args()
    if args.action=='list':
        import office_routines
        result={'revision':REVISION,'items':office_routines.CATALOG}
    else:result=run(args.request_id)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
