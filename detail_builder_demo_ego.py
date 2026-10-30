"""Prepared rehearsal via official Ego API. No browser/OS focus or model calls."""
import json
from pathlib import Path
import re
import subprocess
import time

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-28-registered-ego-demo-v1'


def _current(request_id):
    import office_relay_context
    value=office_relay_context.read_current(request_id)
    if not value.get('current') or not value.get('can_execute') or value.get('stopped'):
        raise ValueError('현재 실제 요청이 아니어서 시연을 시작하지 않았어요.')
    return value


def run(request_id,phase='all',emit=print):
    if not re.fullmatch('[0-9a-f]{32}',str(request_id)) or phase not in {'prepare','build','all'}:
        raise ValueError('현재 요청과 실행 단계를 확인하세요.')
    import detail_builder_demo as demo,ego_workspace,office_fastlane,task_control
    from ego_report_session import Session
    _current(request_id)
    with office_fastlane.native_control(ROOT/'office.db',request_id),demo._lock():
        manifest=demo.readiness()
        folder=demo.PRIVATE/'runs'/request_id;folder.mkdir(parents=True,exist_ok=True)
        path=folder/'ego-result.json'
        if path.exists():
            previous=json.loads(path.read_text(encoding='utf-8'))
            if previous.get('state')=='completed':
                if previous['steps'].get('export'):demo._verify_export(Path(previous['steps']['export']['path']),manifest)
                if previous['steps'].get('thumbnails'):demo._verify_thumbnails(Path(previous['steps']['thumbnails']['path']))
                return dict(previous,replayed=True)
            raise ValueError('이전 실행의 실제 효과 확인이 필요해요. 자동으로 다시 실행하지 않았어요.')
        result={'request_id':request_id,'phase':phase,'started':time.time(),'state':'running',
                'mode':'code','model_calls':0,'browser':'ego','revision':REVISION,'steps':{}}
        demo._write(path,result)
        try:
            if phase in {'prepare','all'}:
                result['steps']['materials']=demo._prepare(manifest,request_id);demo._write(path,result)
            template=demo.PRIVATE/manifest['template']
            project=json.loads(template.read_text(encoding='utf-8'));project['name']='소프트 텅 리얼 페라 · 시연 '+request_id[:8]
            import_file=folder/'import-project.json';demo._write(import_file,project)
            destination=Path(manifest['destination'])/'상세페이지'/request_id
            cfg={'folder':folder.as_posix(),'origin':demo.ORIGIN,'space_name':'상세페이지 시연 '+request_id,
                 'template':str(template),'import_file':str(import_file),
                 'detail_zip':str(destination/'detail_split_소프트 텅 리얼 페라.zip'),
                 'thumbnails_zip':str(destination/'thumbnails_소프트 텅 리얼 페라.zip')}
            def browser(action):
                _current(request_id);task_control.checkpoint()
                cfg['action']=action;plan=folder/'ego-plan.json';demo._write(plan,cfg)
                entry=folder/'ego-run.mjs';entry.write_text('globalThis.detailDemoPlan='+json.dumps(plan.as_uri())+';\nawait import('+json.dumps((ROOT/'detail_builder_demo_ego.mjs').as_uri())+');',encoding='utf-8')
                with (folder/('ego-'+action+'.log')).open('wb') as stream:
                    proc=subprocess.Popen([ego_workspace.executable(),'nodejs','-e',"await import('"+entry.as_uri()+"')"],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    try:
                        started=time.monotonic()
                        while proc.poll() is None:
                            task_control.checkpoint()
                            if time.monotonic()-started>200:raise TimeoutError('Ego 시연 응답 확인 시간이 지났어요.')
                            time.sleep(.15)
                    finally:
                        if proc.poll() is None:
                            proc.terminate()
                            try:proc.wait(timeout=3)
                            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
                    if proc.returncode:raise ValueError('Ego 시연 단계를 마치지 못했어요. 기존 결과를 보존했어요.')
                return json.loads(path.read_text(encoding='utf-8'))
            emit('저장된 Ego 시연 실행기 · 새 상품 작업 준비 중')
            result=browser('setup')
            if phase in {'prepare','all'}:
                _current(request_id)
                with Session(ROOT/'office.db',request_id,folder/'bizhost-session',space_id=result['space_id']) as context:
                    import report_automation
                    page=report_automation._login_biz(context,ROOT/'office.db',emit,new_page=True)
                    result['steps']['bizhost']={'url':page.url,'search_submitted':False};demo._write(path,result)
            if phase in {'build','all'}:
                emit('저장된 사진·문구 가져오기 → 상세이미지·썸네일 저장 중')
                result=browser('build')
                result['steps']['export']['items']=demo._verify_export(Path(cfg['detail_zip']),manifest)
                result['steps']['thumbnails']['items']=demo._verify_thumbnails(Path(cfg['thumbnails_zip']))
                demo._write(path,result)
            result=browser('finish')
            result.update(state='completed',elapsed_seconds=round(time.time()-result['started'],2));demo._write(path,result)
            return result
        except BaseException as error:
            result=json.loads(path.read_text(encoding='utf-8'))
            result.update(state='stopped' if isinstance(error,task_control.Stopped) else 'uncertain',error_type=type(error).__name__,automatic_retry=False)
            demo._write(path,result);raise
