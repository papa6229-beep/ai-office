"""Common-model adapters for the official Ego batch service."""
import sqlite3

import ego_workspace

def _s(**values):return dict(type='string',maxLength=8000,**values)
_step={'type':'object','additionalProperties':False,'required':['op'],'properties':{
    'op':_s(enum=sorted(ego_workspace.OPS)), 'page':_s(), 'url':_s(), 'selector':_s(),
    'when_selector':_s(), 'unless_selector':_s(), 'selectors':{'type':'array','minItems':1,'maxItems':10,'items':_s()},
    'value':_s(), 'label':_s(), 'state':_s(enum=['attached','detached','visible','hidden']),
    'timeout_ms':{'type':'integer','minimum':100,'maximum':180000},
    'equals':_s(), 'contains':_s(), 'account_ref':_s(), 'username_selector':_s(),
    'password_selector':_s(), 'source':_s(), 'match':_s(), 'since':{'type':'number'},
    'after_mark':_s(), 'digits':{'type':'integer','minimum':4,'maximum':8},
    'name':_s(), 'query':_s(), 'limit':{'type':'integer','minimum':1,'maximum':100}}}
SPECS={
    'files_sort':('external','선택한 폴더의 바로 아래 파일을 확장자/이름 조건으로 코드 분류하고 폴더 생성·이동까지 묶음 실행합니다. 폴더 ref는 files_browse/locations 결과를 사용하세요. rules의 순서가 우선순위이며 조건에 맞지 않는 파일과 하위 폴더는 그대로 둡니다. 나머지 파일도 분류하라는 명확한 요청이 있으면 마지막 규칙 extensions에 *를 사용하세요. 각 파일마다 모델로 판단하지 않습니다.',
        {'source_ref':_s(),'destination_ref':_s(),'title':_s(),'conflict':_s(enum=['rename','skip','error']),
         'rules':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','additionalProperties':False,'required':['folder'],'properties':{'folder':_s(),'extensions':{'type':'array','maxItems':30,'items':_s()},'contains':_s()}}}},['source_ref','rules']),
    'ego_status':('read','에고 공식 실행기와 이 어댑터의 작업 공간을 조회합니다. request_id가 있으면 해당 묶음 영수증도 읽습니다. 새 작업 공간을 만들거나 사용자 공간을 가져오지 않습니다.',{'request_id':_s()},[]),
    'ego_batch':('external','에고에서 현재 맡긴 웹 동작을 코드로 묶어 실행합니다. 최초 open/inspect로 작은 입력 목록을 읽고 실제 CSS로 다음 단계들을 한 번에 묶으세요. login은 login_accounts의 account_ref와 관찰한 최상위 password/username CSS를 사용합니다. mark(name)→인증 요청 click→otp_fill(after_mark,match,selector)→확인 click→expect/wait_url로 AI 재호출 없이 인증 대기·입력·검증합니다. 파일/상품 변경·전송은 현재 요청 범위만 실행하고 결과를 검증하세요. 모호한 결과는 재시도하지 않습니다. space_ref로 이 어댑터 소유 공간을 재사용하며 사용자 제어는 가져오지 않습니다.',
        {'space_ref':_s(),'steps':{'type':'array','minItems':1,'maxItems':40,'items':_step},
         'finish':{'type':'boolean'},'keep':{'type':'array','maxItems':10,'items':_s()}},['steps'])
}

def execute(registry,name,args,context):
    if name=='files_sort':
        import office_fastlane
        source=registry._get(context,args['source_ref'],'filesystem_entry')
        destination=registry._get(context,args['destination_ref'],'filesystem_entry') if args.get('destination_ref') else None
        data=office_fastlane.sort_files(registry.db_path,source,args['rules'],context['step_id'],destination,args.get('title','파일 규칙 분류'),args.get('conflict','rename'),root_request_id=context.get('root_request_id') or context['request_id'])
        for job in data.get('jobs',[]):
            if job.get('id'):job['ref']=registry._ref(context,'file_operation_job',job['id'])
        return {'status':'ok' if data['state']=='completed' else 'error','message':'코드 분류 결과입니다. 실제 이동 수·미분류·예외를 확인하세요.','data':data}
    if name=='ego_status':
        data=ego_workspace.availability();data['spaces']=[]
        if args.get('request_id'):data['batch']=ego_workspace.status(registry.db_path,args['request_id'])
        with sqlite3.connect(registry.db_path,timeout=5) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='ego_batch_spaces'").fetchone():
                for ident,state in c.execute("SELECT id,state FROM ego_batch_spaces WHERE state='active' ORDER BY updated DESC LIMIT 20"):
                    data['spaces'].append({'ref':registry._ref(context,'ego_space',ident),'space_id':ident,'state':state})
        return {'status':'ok','message':'에고 실행기와 어댑터 소유 공간입니다. 실제 연결은 실행 결과로 확인하세요.','data':data}
    steps=[]
    for supplied in args['steps']:
        step=dict(supplied)
        if step.get('account_ref'):
            step['account_id']=registry._get(context,step.pop('account_ref'),'login_account')['id']
        steps.append(step)
    ident=registry._get(context,args['space_ref'],'ego_space') if args.get('space_ref') else None
    data=ego_workspace.run(registry.db_path,context['step_id'],steps,space_id=ident,
        finish=args.get('finish',False),keep=args.get('keep'),root_request_id=context.get('root_request_id') or context['request_id'])
    if data.get('space_id') is not None and not data.get('finished'):data['space_ref']=registry._ref(context,'ego_space',data['space_id'])
    success=data['state']=='completed'
    message=('에고 코드 단계 실행을 마쳤어요. '+('지정한 결과 조건도 확인했어요.' if data.get('verified') else '업무 완료 판단에는 실제 결과 조건 확인이 더 필요해요.')) if success else '에고 단계를 마치지 못했어요. 실행 기록과 현재 화면을 확인하고 성공한 동작을 반복하지 마세요.'
    return {'status':'ok' if success else 'error','message':message,'data':data}
