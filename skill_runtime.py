"""Shared local skill entry point; procedural skills stay in the current model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import skill_catalog

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-28-shared-skills-v1'
MAX_OUTPUT=2*1024*1024


def _paths():
    bundle=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    watch=ROOT/'local_backups/watch-runtime'
    ffmpeg=next(iter(sorted(watch.glob('ffmpeg/*/bin/ffmpeg.exe'),reverse=True)),None)
    return {'python':bundle/'python/python.exe','python_office':Path.home()/'AppData/Local/Programs/Python/Python310/python.exe','node':bundle/'node/bin/node.exe',
            'node_packages':bundle/'node/node_modules',
            'firecrawl':ROOT/'local_backups/firecrawl-runtime/node_modules/firecrawl-cli/dist/index.js',
            'ego':Path(shutil.which('ego-browser') or Path.home()/'.local/share/ego/active_version_dir/ego-browser.exe'),
            'yt_dlp':watch/'yt-dlp.exe','ffmpeg':ffmpeg,
            'launcher':ROOT/'tools/office-skill.cmd'}


def _helpers(row):
    root=Path(row['directory'])/'scripts';found=[]
    if root.is_dir():
        for directory,dirs,files in os.walk(root):
            dirs[:]=[d for d in dirs if d not in {'node_modules','__pycache__','.git'}]
            for name in files:
                if Path(name).suffix.lower() in {'.py','.js','.mjs','.cjs'}:
                    found.append(str((Path(directory)/name).relative_to(row['directory'])).replace('\\','/'))
                    if len(found)>=60:return sorted(found)
    return sorted(found)


def _kind(row):
    name=row['name'].casefold()
    if name in {'firecrawl','firecrawl-search','firecrawl-scrape','ego-browser','watch'}:return 'cli'
    if _helpers(row):return 'instruction_and_scripts'
    if row['provider'] in {'google-drive','notion','pages','sites','work-pets','plugin-management','imagegen'} or name in {'imagegen','computer-use','excel-live-control'}:return 'native_tools'
    return 'instruction'


def listing(query='',limit=30,workspace=None):
    result=skill_catalog.listing(query,limit,workspace)
    for item in result['items']:
        row=skill_catalog.resolve(item['id'],workspace)
        item['execution_kind']=_kind(row)
    result['runtime_revision']=REVISION
    return result


def read(ident,resource='',offset=0,workspace=None):
    result=skill_catalog.read(ident,resource,offset,workspace)
    row=skill_catalog.resolve(ident,workspace)
    result.update(runtime_revision=REVISION,execution_kind=_kind(row),helpers=_helpers(row))
    return result


def status(ident=None,workspace=None):
    paths=_paths()
    result={'runtime_revision':REVISION,'launcher':str(paths['launcher']),
            'workspace':str(Path(workspace or os.getcwd()).resolve()),
            'runtimes':{k:{'path':str(p) if p else None,'exists':bool(p and p.exists())} for k,p in paths.items()},
            'authentication':'not_checked','runner_starts_model_session':False,
            'node_esm':'ESM builders use the selected skill’s documented local node_modules link; NODE_PATH alone is not an ESM module resolver.'}
    if ident:
        row=skill_catalog.resolve(ident,workspace)
        result.update(skill={k:row[k] for k in ('id','name','path','version','legacy')},
                      execution_kind=_kind(row),helpers=_helpers(row),
                      next_action='Read the source, then execute its helper/CLI or the current conversation’s official tools. Instruction-ready is not business completion.')
    else:
        data=skill_catalog.catalog(workspace)
        result.update(catalog_total=len(data['items']),catalog_revision=data['revision'],source_files=data['source_files'],catalog_errors=data['errors'])
    return result


def guidance():
    paths=_paths();catalog=skill_catalog.catalog()
    return {'revision':REVISION,'catalog_revision':catalog['revision'][:16],
            'python':str(paths['python']),'entry':str(ROOT/'skill_runtime.py'),'launcher':str(paths['launcher']),
            'commands':['list --query 업무내용','read 제공자:스킬명','status 제공자:스킬명','run 제공자:스킬명 -- 도구인수','run 제공자:스킬명 --script scripts/도우미.py -- 인수'],
            'selection':'현재 맡은 업무에 필요한 스킬을 능동적으로 검색·읽고 적용하세요. 전체 원문을 한꺼번에 읽거나 별도 AI/팀을 기본으로 만들지 마세요.',
            'execution':'지침형은 현재 선택 모델이 수행합니다. CLI/스크립트는 공통 run, 연결 도구형은 현재 대화의 공식 도구를 사용합니다. PATH나 전용 스킬 도구가 없다는 이유로 중단하지 말고 위 Python+entry를 직접 호출하세요.',
            'boundary':'현재 사용자 요청·No Astra·선택 모델·승인/중지·브라우저 Ego 정책을 유지합니다. 스킬 읽기/실제 명령/결과 확인을 구분하고 이미 완료한 효과를 반복하지 마세요.'}


def _workspace(value):
    path=Path(value or os.getcwd()).resolve()
    if not path.is_dir():raise ValueError('현재 작업 폴더를 확인하세요.')
    return path


def _output_path(workspace,path):
    value=Path(path)
    value=(workspace/value).resolve() if not value.is_absolute() else value.resolve()
    if not value.is_relative_to(workspace):raise ValueError('실행 출력은 현재 작업 폴더 안에 지정하세요.')
    return value


def _validate_outputs(args,workspace,flags):
    for index,value in enumerate(args):
        flag=value.split('=',1)[0]
        if flag not in flags:continue
        target=value.split('=',1)[1] if '=' in value else args[index+1] if index+1<len(args) else ''
        if not target:raise ValueError('출력 경로가 필요합니다.')
        _output_path(workspace,target)


def _script(row,value,workspace):
    raw=Path(value)
    if raw.is_absolute():candidates=[raw.resolve()]
    else:candidates=[(Path(row['directory'])/raw).resolve(),(workspace/raw).resolve()]
    base=Path(row['directory']).resolve()
    for path in candidates:
        if path.is_file() and (path.is_relative_to(base) or path.is_relative_to(workspace)):
            if path.suffix.lower() not in {'.py','.js','.mjs','.cjs'}:raise ValueError('Python/JavaScript 실행 파일을 선택하세요.')
            return path
    raise ValueError('선택한 스킬 또는 현재 작업 폴더의 기존 스크립트만 실행할 수 있습니다.')


def _job(workspace):
    base=workspace/'.office-skills'
    for directory in (base,base/'runs'):
        if directory.exists():
            info=directory.lstat()
            if directory.is_symlink() or getattr(info,'st_file_attributes',0)&1024 or not directory.is_dir():raise ValueError('실행 기록 경로가 연결된 경로입니다.')
        else:directory.mkdir()
    ignore=base/'.gitignore'
    if not ignore.exists():ignore.write_text('*\n',encoding='utf-8')
    path=base/'runs'/uuid.uuid4().hex;path.mkdir()
    return path


def _redact(text):
    text=re.sub(r'(?i)((?:authorization|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)[\"\']?\s*[:=]\s*[\"\']?)(?:Bearer\s+)?[^\s,;\"\']+',r'\1[redacted]',text)
    return re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,})\b','[redacted]',text)


def run(ident,args=None,workspace=None,script='',timeout=300,python_runtime='primary'):
    if args is None:args=[]
    if not isinstance(args,list) or len(args)>100 or any(not isinstance(a,str) or '\0' in a or len(a)>16000 for a in args):raise ValueError('실행 인수는 문자열 배열로 전달하세요.')
    if type(timeout) is not int or not 1<=timeout<=1800:raise ValueError('실행 제한 시간은 1~1800초입니다.')
    if python_runtime not in {'primary','office'}:raise ValueError('기존 primary 또는 office Python을 선택하세요.')
    workspace=_workspace(workspace);row=skill_catalog.resolve(ident,workspace);paths=_paths()
    env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    env['NODE_PATH']=str(paths['node_packages'])
    command=None;watch=False;tool='script'
    if script:
        target=_script(row,script,workspace)
        runtime=paths['python_office' if python_runtime=='office' else 'python'] if target.suffix.lower()=='.py' else paths['node']
        command=[str(runtime),str(target),*args]
    elif row['name'].casefold() in {'firecrawl','firecrawl-search','firecrawl-scrape'}:
        if args and args[0] in {'login','logout','config'}:raise ValueError('계정·접근 변경은 현재 업무의 공식 사용자 인증 절차로 진행하세요.')
        _validate_outputs(args,workspace,{'-o','--output'})
        command=[str(paths['node']),str(paths['firecrawl']),*args];tool='firecrawl'
        env.update(FIRECRAWL_NO_TELEMETRY='1',FIRECRAWL_NO_ENDPOINT_FEEDBACK='1')
    elif row['name'].casefold()=='ego-browser':
        command=[str(paths['ego']),*args];tool='ego-browser'
    elif row['name'].casefold()=='watch':
        if any(a.startswith(('--cookies','--gemini','--api-key')) for a in args):raise ValueError('기존 watch는 쿠키 복사·새 API 모델 없이 local/no-whisper로 사용합니다.')
        for index,arg in enumerate(args):
            if arg.startswith('--engine'):
                value=arg.split('=',1)[1] if '=' in arg else args[index+1] if index+1<len(args) else ''
                if value!='local':raise ValueError('공통 watch는 기존 local 엔진을 유지합니다.')
        _validate_outputs(args,workspace,{'--out-dir'})
        target=Path(row['directory'])/'scripts/watch.py'
        command=[str(paths['python']),str(target),*args];tool='watch';watch=True
        env.update(WATCH_ENGINE='local',WATCH_WHISPER_BACKEND='none',WATCH_COOKIES_FILE='',WATCH_COOKIES_FROM_BROWSER='')
        additions=[str(paths['yt_dlp'].parent)]
        if paths['ffmpeg']:additions.append(str(paths['ffmpeg'].parent))
        env['PATH']=os.pathsep.join(additions+[env.get('PATH','')])
    if command is None:
        result=read(row['id'],workspace=workspace)
        result.update(runtime=status(row['id'],workspace),next_action='현재 모델이 이 지침을 적용하고 공식 연결 도구를 호출하세요. 별도 스킬 실행 바이너리는 필요하지 않습니다.')
        return result
    if not Path(command[0]).is_file() or (len(command)>1 and tool in {'firecrawl','watch','script'} and not Path(command[1]).is_file()):
        raise ValueError('기존 실행 파일을 찾지 못했습니다. skill status에서 실제 위치를 확인하세요. 자동 재설치하지 않았습니다.')
    folder=_job(workspace)
    if watch:
        if not any(a=='--out-dir' or a.startswith('--out-dir=') for a in args):command+=['--out-dir',str(folder/'outputs')]
        if not any(a=='--engine' or a.startswith('--engine=') for a in args):command+=['--engine','local']
        if '--no-whisper' not in args:command+=['--no-whisper']
    receipt={'id':folder.name,'skill_id':row['id'],'state':'running','runtime_revision':REVISION,'tool':tool,
             'workspace':str(workspace),'runtime':command[0],'started':time.time(),'receipt':str(folder/'receipt.json'),
             'stdout_file':str(folder/'stdout.txt'),'stderr_file':str(folder/'stderr.txt'),
             'completed':False,'runner_model_calls':0,'child_model_calls':'not_measured'}
    def save():(folder/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    save();output={'stdout':bytearray(),'stderr':bytearray()};overflow=threading.Event();process=None
    try:
        process=subprocess.Popen(command,cwd=workspace,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def drain(stream,key):
            while True:
                chunk=stream.read1(65536)
                if not chunk:break
                remaining=MAX_OUTPUT-len(output[key]);output[key].extend(chunk[:max(0,remaining)])
                if len(chunk)>remaining:
                    overflow.set()
                    try:process.kill()
                    except OSError:pass
                    break
        readers=[threading.Thread(target=drain,args=(process.stdout,'stdout'),daemon=True),threading.Thread(target=drain,args=(process.stderr,'stderr'),daemon=True)]
        for thread in readers:thread.start()
        try:process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill();process.wait();receipt['state']='timeout_effects_unconfirmed'
        for thread in readers:thread.join(3)
        if any(thread.is_alive() for thread in readers):receipt['state']='output_streams_unconfirmed'
        if receipt['state']=='running':receipt['state']='output_limit' if overflow.is_set() else 'completed' if process.returncode==0 else 'failed'
        receipt['exit_code']=process.returncode
    except OSError as exc:
        receipt.update(state='failed_to_start',error_type=type(exc).__name__)
    finally:
        for key,raw in output.items():
            text=_redact(raw.decode('utf-8',errors='replace'))
            (folder/(key+'.txt')).write_text(text,encoding='utf-8')
            receipt[key]=text[:12000]
        receipt.update(finished=time.time(),elapsed_ms=round((time.time()-receipt['started'])*1000),completed=receipt['state']=='completed')
        save()
        if process and not any(thread.is_alive() for thread in locals().get('readers',[])):
            if process.stdout:process.stdout.close()
            if process.stderr:process.stderr.close()
    return receipt


def main():
    arguments=sys.argv[1:];extra=[]
    if '--' in arguments:
        index=arguments.index('--');arguments,extra=arguments[:index],arguments[index+1:]
    parser=argparse.ArgumentParser(description='공통 스킬 검색·원문·환경·실행. 새 AI 모델을 호출하지 않습니다.')
    subs=parser.add_subparsers(dest='action',required=True)
    for action in ['list','read','status','run']:
        sub=subs.add_parser(action);sub.add_argument('--workspace',default=os.getcwd())
        if action=='list':sub.add_argument('--query',default='');sub.add_argument('--limit',type=int,default=30)
        else:sub.add_argument('skill',nargs='?' if action=='status' else None)
        if action=='read':sub.add_argument('--resource',default='');sub.add_argument('--offset',type=int,default=0)
        if action=='run':sub.add_argument('--script',default='');sub.add_argument('--timeout',type=int,default=300);sub.add_argument('--python-runtime',choices=['primary','office'],default='primary')
    parsed=parser.parse_args(arguments)
    try:
        if parsed.action=='list':result=listing(parsed.query,parsed.limit,parsed.workspace)
        elif parsed.action=='read':result=read(parsed.skill,parsed.resource,parsed.offset,parsed.workspace)
        elif parsed.action=='status':result=status(parsed.skill,parsed.workspace)
        else:result=run(parsed.skill,extra,parsed.workspace,parsed.script,parsed.timeout,parsed.python_runtime)
        print(json.dumps(result,ensure_ascii=True))
        if result.get('state') in {'failed','failed_to_start','timeout_effects_unconfirmed','output_limit','output_streams_unconfirmed'}:return 1
        return 0
    except (ValueError,OSError) as exc:
        print(json.dumps({'state':'unavailable','error':str(exc),'completed':False},ensure_ascii=True));return 1


if __name__=='__main__':raise SystemExit(main())
