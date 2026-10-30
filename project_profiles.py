"""Read-only evidence for human-readable project introductions. No model calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import project_catalog as catalog

DATA=catalog.DATA
SOURCE_EXT={'.py','.js','.jsx','.ts','.tsx','.vue','.svelte','.html','.css','.cs','.go','.rs','.java','.kt','.dart'}

def redact(text):
    text=re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{15,}|AIza[A-Za-z0-9_-]{20,})','[가림]',text)
    return re.sub(r'(?im)^.*(?:password|passwd|비밀번호|api[_ -]?key|access[_ -]?token)\s*[:=].*$','[민감 설정 생략]',text)

def stamp(value):
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (AttributeError,TypeError,ValueError):return 0

def token():
    exe=shutil.which('git')or r'C:\Program Files\Git\cmd\git.exe'
    result=subprocess.run([exe,'-c','credential.interactive=never','credential','fill'],input='protocol=https\nhost=github.com\n\n',
      capture_output=True,text=True,timeout=10,env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    credential=dict(line.split('=',1)for line in result.stdout.splitlines()if '='in line)
    value=credential.get('password')
    if not value:raise ValueError('GitHub 로그인 확인 필요')
    return value

def api(path,credential):
    request=urllib.request.Request('https://api.github.com'+path,headers={'Authorization':'Bearer '+credential,'Accept':'application/vnd.github+json','User-Agent':'Aemeath-Project-Profile'})
    with urllib.request.urlopen(request,timeout=12)as r:return json.load(r)

def local_evidence(row):
    root=Path(row['path']);latest=0;latest_file=None;readme='';hits=[];personal=[];visited=0;files=0;limited=False
    for name in ('README.md','readme.md','README.ko.md','README_KO.md','README.rst'):
        file=root/name
        try:
            if file.is_file()and file.stat().st_size<256000:
                readme=redact(file.read_text(encoding='utf-8',errors='replace'))[:6000];break
        except OSError:pass
    stack=[(root,0)];started=time.time()
    while stack:
        path,depth=stack.pop();visited+=1
        if visited>3000 or files>22000 or time.time()-started>12:limited=True;break
        try:
            for entry in os.scandir(path):
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower()in catalog.SKIP or entry.name in {'.agent','.agents','.claude','.openai','.worktrees','outputs','.turbo','logs'}:continue
                    if entry.is_symlink()or getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&1024:continue
                    if depth<9:stack.append((Path(entry.path),depth+1))
                    else:limited=True
                elif entry.is_file(follow_symlinks=False):
                    files+=1;file=Path(entry.path)
                    if file.name.startswith('.env')or file.suffix.lower()in {'.db','.sqlite','.sqlite3'}or file.name in {'settings.json','credentials.json'}:
                        if len(personal)<20:personal.append(str(file.relative_to(root)))
                        continue
                    if file.suffix.lower()in SOURCE_EXT or file.name in {'package.json','pyproject.toml'}:
                        modified=entry.stat().st_mtime
                        if modified>latest:latest=modified;latest_file=str(file.relative_to(root))
        except OSError:limited=True
    candidates=['package.json','app/layout.tsx','app/page.tsx','src/app/layout.tsx','src/app/page.tsx','App.tsx','src/App.tsx','src/App.jsx','src/App.vue','index.html','app.py']
    for name in candidates:
        file=root/name
        try:
            if file.is_file()and file.stat().st_size<700000:
                raw=redact(file.read_text(encoding='utf-8',errors='replace'))
                lines=[line.strip()for line in raw.splitlines()if re.search(r'[가-힣]{2}|<title>|description:|title:',line)]
                excerpt='\n'.join(lines)[:1600]
                if excerpt:hits.append({'file':name,'excerpt':excerpt})
        except OSError:pass
        if len(hits)>=3:break
    changed={'checked':False,'tracked':None,'untracked':None}
    try:
        exe=shutil.which('git')or r'C:\Program Files\Git\cmd\git.exe'
        result=subprocess.run([exe,'-c','core.fsmonitor=false','-C',str(root),'status','--porcelain=v1','--untracked-files=normal','--ignore-submodules=all'],
          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=8,env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0:
            lines=result.stdout.splitlines();changed={'checked':True,'tracked':sum(not s.startswith('??')for s in lines),'untracked':sum(s.startswith('??')for s in lines)}
    except (OSError,subprocess.TimeoutExpired):pass
    return {'path':str(root),'source_modified_at':latest,'source_modified_file':latest_file,'source_scan_limited':limited,
            'files_observed':files,'personal_data_candidates':personal,'changes':changed,'head':catalog.git(root,'rev-parse','HEAD'),
            'commit_at':row.get('commit_at',0),'readme_excerpt':readme,'ui_evidence':hits}

def collect(local=None,github=None,stop=None,progress=None):
    local=local or catalog.read(DATA/'local.json',{});github=github or catalog.read(DATA/'github.json',{})
    roots=[p for p in catalog._group(local.get('items',[]),github.get('items',[]),[])if p['github']and p['subdir']=='.']
    try:credential=token()
    except Exception:credential=None
    previous=catalog.read(DATA/'profiles.json',{'items':{}});items=dict(previous.get('items',{}));completed=0
    def one(p):
        evidence={'repo':p['github_key'],'collected_at':time.time(),'locals':[],'readme_excerpt':'','remote_commit':None,'errors':[]}
        old=items.get(p['github_key'],{})
        for key in ('supplemental_sources','remote_info','remote_files','empty_repository'):
            if key in old:evidence[key]=old[key]
        for row in p['locals']:
            if stop and stop.is_set():return None
            try:evidence['locals'].append(local_evidence(row))
            except Exception:evidence['errors'].append('로컬 자료 일부 조회 실패')
        evidence['readme_excerpt']=next((r['readme_excerpt']for r in evidence['locals']if r['readme_excerpt']),'')
        if credential:
            if not evidence['readme_excerpt']:
                try:
                    data=api('/repos/'+p['github_key']+'/readme',credential)
                    evidence['readme_excerpt']=redact(base64.b64decode(data.get('content','')).decode('utf-8',errors='replace'))[:6000]
                except Exception:evidence['errors'].append('README 조회 불가')
            try:
                commits=api('/repos/'+p['github_key']+'/commits?per_page=1',credential)
                c=commits[0];evidence['remote_commit']={'sha':c['sha'],'at':stamp(c['commit']['committer']['date']),'subject':redact(c['commit']['message'].splitlines()[0])[:180]};evidence['empty_repository']=False
            except Exception:evidence['errors'].append('GitHub 커밋 조회 불가')
        else:evidence['errors'].append('GitHub 상세 조회 인증 확인 필요')
        return evidence
    with ThreadPoolExecutor(max_workers=4)as pool:
        for evidence in pool.map(one,roots):
            if stop and stop.is_set():return {'cancelled':True}
            if evidence:items[evidence['repo']]=evidence
            completed+=1
            if progress:progress(completed,len(roots))
    catalog.write(DATA/'profiles.json',{'items':items,'collected_at':time.time(),'mode':'code','model_calls':0})
    return {'repositories':completed,'mode':'code','model_calls':0}

def decorate(project,profiles,introductions):
    evidence=profiles.get(project.get('github_key')) if project.get('github_key')else None
    draft=introductions.get(project.get('github_key'),{})if project.get('github_key')else{}
    project['introduction']=draft
    project['profile']=None
    if not evidence:return
    selected_paths={r['path']for r in project['locals']}
    local=[{k:v for k,v in r.items()if k not in {'readme_excerpt','ui_evidence','head'}}for r in evidence.get('locals',[])if r['path']in selected_paths]
    previews=catalog.read(DATA/'previews.json',{}).get('items',{})
    preview=previews.get(project['github_key'])
    project['preview']=preview if preview else None
    project['profile']={'collected_at':evidence['collected_at'],'locals':local,'remote_commit':evidence.get('remote_commit'),'errors':evidence.get('errors',[]),'empty_repository':evidence.get('empty_repository',False)}
    if draft:project['introduction']={**draft,'stale':bool(draft.get('fingerprint') and draft['fingerprint']!=fingerprint(evidence))}
    if project['subdir']!='.':return
    changes=sum((r['changes'].get('tracked')or 0)+(r['changes'].get('untracked')or 0)for r in local)
    personal=sum(len(r['personal_data_candidates'])for r in local)
    comparison=[r for r in evidence.get('locals',[])if r['path']in selected_paths and r.get('head')]
    remote=evidence.get('remote_commit')
    different=bool(remote and any(r['head']!=remote['sha']for r in comparison))
    reasons=[];level='review';label='확인 후 판단'
    if project['status']=='active':level='keep';label='유지 추천';reasons.append('진행 중으로 지정한 프로젝트예요.')
    if changes:reasons.append(f'로컬에 변경·미추적 항목 {changes}개가 있어요. 보존 여부를 먼저 확인하세요.')
    if different:reasons.append('로컬과 GitHub 최신 커밋이 달라요. 미게시·다른 브랜치 여부 확인이 필요해요.')
    if personal:reasons.append('설정·DB 등 별도 보존이 필요한 로컬 자료 후보가 있어요.')
    if len(project['locals'])>1:reasons.append('같은 저장소의 폴더가 여러 곳이라 기준 폴더를 고른 뒤 사본을 정리할 수 있어요.')
    if project['vercel']:reasons.append('연결된 배포가 있어요. 실제 사용 여부는 별도로 확인해야 해요.')
    if project.get('github',{}).get('archived') and not changes and not different:level='archive';label='보관 검토';reasons.append('GitHub에서 이미 보관된 저장소예요.')
    if evidence.get('empty_repository')and not local:
        level='delete_candidate';label='빈 저장소 · 삭제 검토';reasons.append('조회 시 원격 파일이 없는 저장소로 확인됐어요. 배포·다른 위치 연결은 삭제 전에 확인하세요.')
    elif len(local)>1 and not changes and not personal and not different:
        level='archive';label='사본 정리 검토';reasons.append('확인한 사본에 추적·미추적 변경이 없어요. 기준 폴더를 남길 수 있는지 검토하세요.')
    if not reasons:reasons.append('삭제할 근거가 아직 충분하지 않아요. 사용 계획을 확인해 주세요.')
    if not local:reasons.append('이번 목록에서 로컬 원본 위치는 찾지 못했어요.')
    if any(not r['changes']['checked']or r['source_scan_limited']for r in local):reasons.append('로컬 변경 또는 소스 날짜 조사에 미확인 범위가 있어요.')
    project['recommendation']={'level':level,'label':label,'reasons':reasons,'checked_at':evidence['collected_at']}

def fingerprint(evidence):
    source={'readme':evidence.get('readme_excerpt',''),'ui':[r.get('ui_evidence',[])for r in evidence.get('locals',[])],
            'supplemental':evidence.get('supplemental_sources',[]),'remote_sha':(evidence.get('remote_commit')or{}).get('sha')}
    return catalog.ident(json.dumps(source,ensure_ascii=False,sort_keys=True))

def preview(ref):
    if not isinstance(ref,str)or not re.fullmatch('[0-9a-f]{24}',ref):return None
    items=catalog.read(DATA/'previews.json',{}).get('items',{})
    if not any(r.get('ref')==ref and r.get('status')=='captured'for r in items.values()):return None
    path=DATA/'previews'/(ref+'.png')
    try:
        if path.is_symlink()or path.stat().st_size>3000000:return None
        raw=path.read_bytes()
        return raw if raw.startswith(b'\x89PNG\r\n\x1a\n')else None
    except OSError:return None
