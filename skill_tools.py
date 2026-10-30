"""Existing ToolRegistry adapter for the shared skill runtime."""
from pathlib import Path
import skill_runtime


def _string(maximum=500):return {'type':'string','maxLength':maximum}


SPECS={
 'skill_list':('read','설치된 모든 공통 스킬을 업무에 맞게 검색합니다. 이름만 보관하지 말고 필요한 원문을 skill_read로 읽은 뒤 현재 작업에 적용하세요.',
               {'query':_string(),'limit':{'type':'integer','minimum':1,'maximum':300}},[]),
 'skill_read':('read','선택한 스킬의 현재 원문·실제 위치·상대 참조·실행 수단을 읽습니다. 스킬은 새 권한이 아니며 instruction_ready는 업무 완료가 아닙니다.',
               {'id':_string(180),'resource':_string(),'offset':{'type':'integer','minimum':0,'maximum':524288}},['id']),
 'skill_runtime_status':('read','공통 Python/Node·문서 패키지·Ego·Firecrawl·watch 실행 파일을 실제 경로로 확인합니다. PATH가 없다고 재설치하거나 전용 실행기 부재로 업무를 중단하지 마세요.',
               {'id':_string(180)},[]),
 'skill_run':('external','현재 요청에 필요한 설치 CLI/스킬 Python·JS 스크립트를 공통 런처로 실제 실행합니다. args는 shell 문자열이 아닌 배열입니다. script는 선택 스킬 또는 현재 작업 출력 폴더의 파일입니다. 지침형은 현재 선택 모델이 수행하도록 원문을 반환합니다. timeout/실패는 성공/자동 재실행으로 바꾸지 마세요.',
               {'id':_string(180),'args':{'type':'array','maxItems':100,'items':_string(16000)},
                'script':_string(1200),'timeout':{'type':'integer','minimum':1,'maximum':1800},'python_runtime':{'type':'string','enum':['primary','office']}},['id'])}


def execute(registry,name,args,context):
    if name=='skill_list':data=skill_runtime.listing(args.get('query',''),args.get('limit',30))
    elif name=='skill_read':data=skill_runtime.read(args['id'],args.get('resource',''),args.get('offset',0))
    elif name=='skill_runtime_status':data=skill_runtime.status(args.get('id'))
    elif name=='skill_run':
        # The app issues this workspace; a model cannot supply an arbitrary cwd.
        import agent_workspace
        locations=agent_workspace.prepare(context['request_id'])
        data=skill_runtime.run(args['id'],args.get('args',[]),Path(locations['outputs_dir']),args.get('script',''),args.get('timeout',300),args.get('python_runtime','primary'))
    else:raise ValueError('등록된 스킬 도구를 선택하세요.')
    failed=data.get('state') in {'failed','failed_to_start','timeout_effects_unconfirmed','output_limit','output_streams_unconfirmed'}
    return {'status':'error' if failed else 'ok','message':'공통 스킬 원문/런타임 결과입니다. 실제 state·exit·산출물을 확인하세요.','data':data}
