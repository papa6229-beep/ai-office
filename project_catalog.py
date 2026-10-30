"""Local project inventory and private catalog. Never runs discovered code."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'local_backups/project-catalog'
LOCK=threading.RLock()
WORKER=None
STOP=threading.Event()
STATE={}
SKIP={'.git','.vercel','node_modules','bower_components','.next','.nuxt','.output',
      '.venv','venv','env','__pycache__','.mypy_cache','.pytest_cache','.ruff_cache',
      '.cache','.uv-cache','.npm','.pnpm-store','.yarn','vendor','site-packages','dist','build',
      'target','coverage','library','temp','tmp','$recycle.bin','system volume information',
      'models','checkpoints','loras','vae','embeddings','clip_vision','controlnet',
      '.provider_runtime','browser_runtime','local_backups','organizer_runtime',
      'appdata','application data','local settings','cookies','nethood','printhood',
      'sendto','recent','my documents','시작 메뉴','.codex','.config','.local',
      'windows','program files','program files (x86)','programdata','recovery',
      'documents and settings','config.msi','esd','perflogs','platform-tools','android'}
MARKERS={'package.json','pyproject.toml','requirements.txt','Cargo.toml','go.mod',
         'pubspec.yaml','pom.xml','build.gradle','build.gradle.kts'}

def read(path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def write(path,value):
    DATA.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,ensure_ascii=False,indent=2)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(raw,encoding='utf-8');os.replace(tmp,path)

def ident(value):return hashlib.sha256(value.encode()).hexdigest()[:24]

def github_key(url):
    if not isinstance(url,str):return None
    # Credentials, query parameters and arbitrary hosts never reach the catalog.
    match=re.fullmatch(r'git@github\.com:([\w.-]+)/([\w.-]+?)(?:\.git)?',url.strip())
    if not match:
        try:
            p=urlsplit(url.strip())
            if p.hostname!='github.com' or p.scheme not in {'https','ssh'}:return None
            match=re.fullmatch(r'/([\w.-]+)/([\w.-]+?)(?:\.git)?/?',p.path)
        except ValueError:return None
    return (match[1]+'/'+match[2]).lower() if match else None

def git(path,*args):
    exe=shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never',GIT_OPTIONAL_LOCKS='0')
    try:
        result=subprocess.run([exe,'-c','core.fsmonitor=false','-C',str(path),*args],
          capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=5,env=env,
          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return result.stdout.strip() if result.returncode==0 else ''
    except (OSError,subprocess.TimeoutExpired):return ''

def roots():
    home=Path.home();items=[Path('D:/')]
    for base in (home,Path('C:/')):
        try:
            for entry in base.iterdir():
                if not entry.is_dir() or entry.name.lower() in SKIP or entry.name.startswith('$'):continue
                if base==home and (entry.name.startswith('.') or entry.name in {'Music','Videos','Pictures','Contacts','Favorites','Links','Saved Games','Searches','3D Objects','ansel'}):continue
                if base==Path('C:/') and entry.name.lower() in {'users','adobetemp','chrometemp','images','imagipark_free','hnc','quarantine_mz'}:continue
                items.append(entry)
        except OSError:pass
    items.append(home/'.codex/worktrees')
    unique=[]
    for path in sorted(items,key=lambda p:len(p.parts)):
        if path.is_dir() and not any(path.is_relative_to(p) for p in unique):unique.append(path)
    return unique

def inspect(item):
    path,names,parent_repo=item
    result={'id':ident(str(path).lower()),'path':str(path),'name':path.name,
            'markers':sorted(MARKERS.intersection(names)),'repo_root':str(parent_repo) if parent_repo else None,
            'github':None,'branch':'','commit_at':0,'description':'','frameworks':[],
            'vercel_id':None,'subdir':str(path.relative_to(parent_repo)).replace('\\','/') if parent_repo else '.'}
    if parent_repo==path:
        result['github']=github_key(git(path,'config','--get','remote.origin.url'))
        result['branch']=git(path,'symbolic-ref','--short','HEAD')[:100]
        stamp=git(path,'log','-1','--format=%ct')
        if stamp.isdigit():result['commit_at']=int(stamp)
    manifest=path/'package.json'
    if manifest.is_file() and manifest.stat().st_size<512000:
        package=read(manifest,{})
        if isinstance(package,dict):
            result['description']=str(package.get('description')or'')[:500]
            result['package_name']=str(package.get('name')or'')[:160]
            deps={key:value for field in ('dependencies','devDependencies')for key,value in (package.get(field)if isinstance(package.get(field),dict)else{}).items()}
            result['frameworks']=[key for key in ('next','react','vue','svelte','vite','electron','express') if key in deps]
            result['script_names']=list(package.get('scripts')if isinstance(package.get('scripts'),dict)else{})[:15]
    if any(n in names for n in ('pyproject.toml','requirements.txt')):result['frameworks'].append('Python')
    link=read(path/'.vercel/project.json',{})
    if isinstance(link,dict) and re.fullmatch(r'prj_[\w-]+',str(link.get('projectId',''))):result['vercel_id']=link['projectId']
    tracked=[path/n for n in names if n in MARKERS or n.lower() in {'readme.md','readme.rst'}]
    try:result['modified_at']=max([p.stat().st_mtime for p in tracked]+[result['commit_at'],path.stat().st_mtime])
    except OSError:result['modified_at']=result['commit_at']
    hashes=[]
    for file in tracked:
        try:
            if file.name in MARKERS and file.stat().st_size<512000:hashes.append(file.name+':'+hashlib.sha256(file.read_bytes()).hexdigest())
        except OSError:pass
    result['manifest_fingerprint']=ident('|'.join(sorted(hashes))) if hashes else None
    return result

def local_scan():
    selected=roots();stack=[(p,0,None)for p in reversed(selected)]
    candidates=[];visited=0;denied=[];depth_skipped=0;started=time.time();limited=False
    while stack and not STOP.is_set():
        if visited>=180000 or time.time()-started>240:limited=True;break
        path,depth,parent_repo=stack.pop();visited+=1
        try:
            entries=list(os.scandir(path));names={e.name for e in entries}
            repo=path if '.git' in names else parent_repo
            if '.git'in names or '.vercel'in names or MARKERS.intersection(names):candidates.append((path,names,repo))
            for entry in entries:
                if entry.name.lower() in SKIP or entry.name.startswith('$'):continue
                if not entry.is_dir(follow_symlinks=False):continue
                info=entry.stat(follow_symlinks=False)
                if getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):continue
                if depth>=14:depth_skipped+=1;continue
                stack.append((Path(entry.path),depth+1,repo))
        except OSError:
            if len(denied)<30:denied.append(str(path))
        if visited%200==0:
            with LOCK:
                STATE.update(phase='폴더 조사',visited=visited,candidates=len(candidates),current=str(path),elapsed=round(time.time()-started,1))
                write(DATA/'scan-state.json',STATE)
    if STOP.is_set():return None
    with LOCK:STATE.update(phase='저장소 연결 대조',visited=visited,candidates=len(candidates))
    results=[]
    def safe_inspect(item):
        try:return inspect(item)
        except (OSError,ValueError,TypeError):return None
    with ThreadPoolExecutor(max_workers=4)as pool:
        for row in pool.map(safe_inspect,candidates):
            if STOP.is_set():return None
            if row is not None:results.append(row)
    origins={r['path'].lower():r for r in results if r['repo_root']==r['path']}
    for row in results:
        parent=origins.get((row['repo_root']or'').lower())
        if parent and parent is not row:
            for field in ('github','branch','commit_at'):row[field]=parent[field]
    return {'collected_at':time.time(),'items':results,'coverage':{'roots':[str(p)for p in selected],
       'visited':visited,'denied_samples':denied,'depth_skipped':depth_skipped,'limit_reached':limited,
       'remaining_directories':len(stack),'max_depth':14,'metadata_skipped':len(candidates)-len(results),'dependency_and_system_folders_excluded':True}}

def fetch_github():
    exe=shutil.which('git') or r'C:\Program Files\Git\cmd\git.exe'
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never')
    proc=subprocess.run([exe,'-c','credential.interactive=never','credential','fill'],
      input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,timeout=10,env=env,
      creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    credential=dict(line.split('=',1)for line in proc.stdout.splitlines()if '='in line)
    token=credential.get('password')
    if not token:raise ValueError('GitHub 로그인 확인이 필요해요.')
    rows=[]
    for page in range(1,11):
        if STOP.is_set():raise ValueError('조회 중지')
        request=urllib.request.Request(f'https://api.github.com/user/repos?per_page=100&page={page}&affiliation=owner,collaborator,organization_member',
          headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'Aemeath-Project-Catalog'})
        with urllib.request.urlopen(request,timeout=15)as response:items=json.load(response)
        rows.extend({k:x.get(k)for k in ('id','full_name','name','description','private','archived','default_branch','updated_at','pushed_at')}|{'url':x['html_url']}for x in items)
        if len(items)<100:break
    return {'collected_at':time.time(),'items':rows,'error':None,'complete':len(items)<100}

def _group(local,github,vercel):
    projects={};vercel_map={}
    for row in local:
        key=('git:'+row['github']+':'+row['subdir'])if row['github']else('vercel:'+row['vercel_id'] if row['vercel_id']else'path:'+row['path'].lower())
        pid=ident(key)
        p=projects.setdefault(pid,{'id':pid,'name':row['name'],'github_key':row['github'],'subdir':row['subdir'],
                                  'description':row['description'],'locals':[],'github':None,'vercel':[]})
        p['locals'].append(row)
        if row['vercel_id']:vercel_map.setdefault(row['vercel_id'],set()).add(pid)
    for repo in github:
        key=repo['full_name'].lower();pid=ident('git:'+key+':.')
        p=projects.setdefault(pid,{'id':pid,'name':repo['name'],'github_key':key,'subdir':'.','description':repo.get('description')or'',
                                  'locals':[],'github':None,'vercel':[]})
        p['github']=repo
        for sub in projects.values():
            if sub['github_key']==key:sub['github']=repo
    for deploy in vercel:
        matches=vercel_map.get(deploy['id'],set())
        confirmed=read(DATA/'cloud-links.json',{}).get('items',{}).get(deploy['id'])
        if not matches and confirmed and urlsplit(confirmed['homepage']).hostname in deploy.get('domains',[]):
            matches={p['id']for p in projects.values()if p['github_key']==confirmed['repo']and p['subdir']=='.'}
        if not matches:
            pid=ident('vercel:'+deploy['id'])
            projects.setdefault(pid,{'id':pid,'name':deploy['name'],'github_key':None,'subdir':'.','description':'',
                                     'locals':[],'github':None,'vercel':[]})
            matches={pid}
        for pid in matches:projects[pid]['vercel'].append(deploy)
    return list(projects.values())

def snapshot():
    local=read(DATA/'local.json',{});gh=read(DATA/'github.json',{});vc=read(DATA/'vercel.json',{});notes=read(DATA/'notes.json',{})
    projects=_group(local.get('items',[]),gh.get('items',[]),vc.get('items',[]))
    import project_profiles
    profiles=read(DATA/'profiles.json',{}).get('items',{})
    introductions=read(DATA/'introductions.json',{}).get('items',{})
    names={}
    for p in projects:names.setdefault(p['name'].lower(),[]).append(p['id'])
    for p in projects:
        p['user']=notes.get(p['id'],{})
        if not p['user']:
            p['user']=next((notes[ident('vercel:'+v['id'])]for v in p['vercel']if ident('vercel:'+v['id'])in notes),{})
        p['status']=p['user'].get('status','unclassified')
        p['updated']=max([r['modified_at'] for r in p['locals']]+[0])
        p['locals'].sort(key=lambda r:r['modified_at'],reverse=True)
        p['similar_names']=[key for key in names[p['name'].lower()]if key!=p['id']]
        p['external']=bool(p['github_key'] and not p['github'])
        project_profiles.decorate(p,profiles,introductions)
        if p.get('profile'):
            p['updated']=max([p['updated'],(p['profile'].get('remote_commit')or{}).get('at',0)]+[r['source_modified_at']for r in p['profile']['locals']])
    projects.sort(key=lambda p:(p['status']!='active',-p['updated'],p['name'].lower()))
    with LOCK:state=dict(STATE)if STATE else read(DATA/'scan-state.json',{'status':'idle'})
    if state.get('status')=='running' and state.get('pid')!=os.getpid():state.update(status='interrupted',phase='지난 조사가 중단됐어요. 다시 조사해 주세요.')
    return {'projects':projects,'scan':state,'coverage':local.get('coverage',{}),'collected_at':local.get('collected_at'),
       'cloud':{'github':{k:gh.get(k)for k in ('collected_at','error','complete')},
                'vercel':{'collected_at':vc.get('collected_at'),'error':vc.get('error'),'refresh':'연결된 Vercel 도구로 대조한 목록'}},
       'counts':{'projects':len(projects),'folders':len(local.get('items',[])),'github':len(gh.get('items',[])),'vercel':len(vc.get('items',[]))},
       'mode':'code','model_calls':0}

def scan(db=None):
    global WORKER
    with LOCK:
        if WORKER and WORKER.is_alive():return {'accepted':False,'scan':dict(STATE)}
        STOP.clear();STATE.clear();STATE.update(status='running',phase='폴더 조사 준비',pid=os.getpid(),started_at=time.time(),visited=0,candidates=0)
        write(DATA/'scan-state.json',STATE)
        def run():
            try:
                local=local_scan()
                if local is None:raise InterruptedError()
                with LOCK:STATE.update(phase='GitHub 저장소 대조')
                try:
                    github=fetch_github()
                    if not STOP.is_set():write(DATA/'github.json',github)
                except Exception:
                    github=read(DATA/'github.json',{});github['error']='GitHub 갱신 실패 · 마지막 성공 목록을 유지해요.';write(DATA/'github.json',github)
                if STOP.is_set():raise InterruptedError()
                write(DATA/'local.json',local)
                import project_profiles
                def profile_progress(done,total):
                    with LOCK:STATE.update(phase=f'소개 근거·변경 기록 확인 {done}/{total}')
                project_profiles.collect(local=local,github=github,stop=STOP,progress=profile_progress)
                if STOP.is_set():raise InterruptedError()
                with LOCK:STATE.update(status='done',phase='조사 완료',finished_at=time.time(),candidates=len(local['items']))
                if db:
                    import work_log
                    work_log.activity(db,'AI 개발 프로젝트 목록 갱신',f"로컬 개발 후보 {len(local['items'])}곳을 조사해 저장소·배포 목록과 대조했어요.",application='AI OFFICE',status='completed')
            except InterruptedError:
                with LOCK:STATE.update(status='cancelled',phase='조사를 중지했어요. 이전 목록은 유지돼요.')
            except Exception:
                with LOCK:STATE.update(status='failed',phase='조사를 마치지 못했어요. 이전 목록은 유지돼요.')
            finally:
                with LOCK:write(DATA/'scan-state.json',STATE)
        WORKER=threading.Thread(target=run,name='project-catalog',daemon=True);WORKER.start()
        return {'accepted':True,'scan':dict(STATE)}

def cancel():
    STOP.set();return {'requested':True}

def save(payload):
    if set(payload)-{'id','status','purpose','next_step','primary_path','title'}:raise ValueError('프로젝트 저장 항목을 확인해 주세요.')
    p=next((p for p in snapshot()['projects']if p['id']==payload.get('id')),None)
    if not p:raise ValueError('목록에서 프로젝트를 다시 선택해 주세요.')
    status=payload.get('status','unclassified')
    if status not in {'unclassified','active','paused','archived'}:raise ValueError('프로젝트 상태를 확인해 주세요.')
    fields={}
    for key,limit in (('purpose',2000),('next_step',2000),('title',160),('primary_path',2000)):
        value=payload.get(key,'')
        if not isinstance(value,str)or len(value)>limit:raise ValueError('입력 내용을 확인해 주세요.')
        fields[key]=value.strip()
    if fields['primary_path'] and fields['primary_path']not in {r['path']for r in p['locals']}:raise ValueError('조사된 폴더 중에서 기준 폴더를 골라 주세요.')
    with LOCK:
        notes=read(DATA/'notes.json',{});notes[p['id']]={**fields,'status':status,'updated_at':time.time()};write(DATA/'notes.json',notes)
    return {'saved':True,'id':p['id']}

def reveal(payload):
    if set(payload)-{'id','path'}:raise ValueError('열 폴더를 확인해 주세요.')
    project=next((p for p in snapshot()['projects']if p['id']==payload.get('id')),None)
    if not project or payload.get('path')not in {r['path']for r in project['locals']}:raise ValueError('조사된 폴더를 선택해 주세요.')
    path=Path(payload['path'])
    if not path.is_dir():raise ValueError('폴더가 이동되었거나 없어졌어요. 다시 조사해 주세요.')
    os.startfile(str(path))
    return {'opened':True}
