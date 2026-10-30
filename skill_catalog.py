"""Discover installed skill sources without executing their content."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

ROOT=Path(__file__).resolve().parent
MAX_SOURCE=512*1024
_lock=threading.RLock()
_cache={}


def _header(path):
    if path.stat().st_size>MAX_SOURCE:raise ValueError('스킬 원문이 너무 큽니다.')
    text=path.read_text(encoding='utf-8-sig')
    front=text.split('---',2)[1] if text.startswith('---') else ''
    name=re.search(r'(?m)^name:\s*(.+)$',front)
    desc=re.search(r'(?ms)^description:\s*(.*?)(?=\n\S|\Z)',front)
    return (name[1].strip().strip('\"\'') if name else path.parent.name,
            re.sub(r'\s+',' ',desc[1]).strip(' \"\'|>') if desc else '')


def _roots(workspace):
    home=Path.home()
    roots=[(home/'.agents/skills','local',5),(home/'.codex/skills','local',4),
           (home/'.claude/skills','local',2),(home/'.codex/plugins/cache','plugin',1)]
    if workspace:
        roots.extend([(Path(workspace)/'.agents/skills','project',3),(Path(workspace)/'.codex/skills','project',3)])
    return roots


def _source(base,path,scope):
    relative=path.relative_to(base)
    if scope!='plugin':
        return ('codex' if '.system' in relative.parts else scope),'',False
    parts=relative.parts
    package=parts[1] if len(parts)>1 else 'plugin'
    legacy=package.startswith('plugin-backup-')
    return ('legacy' if legacy else package),(parts[2] if len(parts)>2 else ''),legacy


def catalog(workspace=None,refresh=False):
    workspace=str(Path(workspace).resolve()) if workspace else ''
    with _lock:
        cached=_cache.get(workspace)
        if cached and not refresh and time.monotonic()-cached[0]<15:return cached[1]
        candidates={};seen=set();errors=[]
        for base,scope,priority in _roots(workspace):
            if not base.is_dir():continue
            for directory,dirs,files in os.walk(base,onerror=lambda e:errors.append(type(e).__name__)):
                dirs[:]=[d for d in dirs if d not in {'node_modules','.git','__pycache__','local_backups'}]
                if 'SKILL.md' not in files:continue
                path=Path(directory)/'SKILL.md';key=str(path).casefold()
                if key in seen:continue
                seen.add(key)
                try:
                    name,description=_header(path)
                    provider,version,legacy=_source(base,path,scope)
                    slug=re.sub(r'[^a-z0-9._-]+','-',name.casefold()).strip('-')
                    if not slug:continue
                    ident=provider+':'+slug
                    versions=tuple(int(n) for n in re.findall(r'\d+',version))
                    info=path.stat()
                    row={'id':ident,'name':name,'description':description,'provider':provider,
                         'version':version,'path':str(path),'directory':str(path.parent),
                         'legacy':legacy,'source_available':True,
                         '_rank':(legacy,-priority,tuple(-n for n in (versions+(0,)*8)[:8]),len(path.parts),key),
                         '_stamp':[info.st_mtime_ns,info.st_size]}
                    candidates.setdefault(ident,[]).append(row)
                except (OSError,UnicodeError,ValueError) as exc:errors.append(type(exc).__name__)
        rows=[]
        for ident,choices in candidates.items():
            choices.sort(key=lambda r:r['_rank']);chosen=dict(choices[0])
            chosen['alternatives']=[{'path':c['path'],'version':c['version']} for c in choices[1:]]
            chosen.pop('_rank',None);rows.append(chosen)
        rows.sort(key=lambda r:r['id'])
        digest=hashlib.sha256(json.dumps([(r['id'],r['path'],r['_stamp']) for r in rows],sort_keys=True).encode()).hexdigest()
        for row in rows:row.pop('_stamp',None)
        result={'revision':digest,'items':rows,'source_files':len(seen),'errors':errors}
        _cache[workspace]=(time.monotonic(),result)
        return result


def resolve(ident,workspace=None):
    if not isinstance(ident,str) or not 1<=len(ident)<=180:raise ValueError('스킬 이름 또는 ID를 지정하세요.')
    rows=catalog(workspace)['items'];key=ident.casefold()
    exact=[r for r in rows if r['id'].casefold()==key]
    choices=exact or [r for r in rows if r['name'].casefold()==key or r['id'].split(':',1)[1]==key]
    if len(choices)!=1:
        if choices:raise ValueError('같은 이름의 스킬이 있습니다. 고유 ID를 선택하세요: '+', '.join(r['id'] for r in choices))
        raise ValueError('설치된 스킬을 찾지 못했습니다. skill list --query 로 확인하세요.')
    return choices[0]


_TAGS={'웹 조사 검색 수집 크롤링':['firecrawl','ego-browser'],
       '영상 동영상 유튜브 자막':['watch'],'기획 계획 요구 설계':['brainstorming','writing-plans'],
       '오류 버그 수정 진단':['systematic-debugging','find-bugs'],
       '디자인 화면 인터페이스':['frontend-design','playground'],
       '엑셀 계산 표 스프레드시트':['spreadsheets','google-sheets'],
       '문서 워드':['documents','google-docs'],'발표 슬라이드':['presentations','google-slides'],
       '이미지 그림 생성':['imagegen'],'검토 리뷰':['code-review','requesting-code-review'],
       '노션 기록':['notion'],'배포 서버 웹개발':['vercel','supabase'],
       '스킬 실행 환경 도구':['office-skills']}


def listing(query='',limit=30,workspace=None):
    if not isinstance(query,str) or len(query)>500 or type(limit) is not int or not 1<=limit<=300:
        raise ValueError('검색어와 조회 개수를 확인하세요.')
    data=catalog(workspace);terms=re.findall(r'[\w-]+',query.casefold())
    related={tag for words,tags in _TAGS.items() if any(t in words for t in terms) for tag in tags}
    scored=[]
    for row in data['items']:
        body=(row['id']+' '+row['name']+' '+row['description']).casefold()
        score=sum(12 if t in row['id'] else 1 for t in terms if t in body)
        score+=sum(5 for t in related if t in row['id'])
        if terms and not score:continue
        public={k:v for k,v in row.items() if k not in {'directory','alternatives'}}
        public['description']=public['description'][:360]
        public['alternative_count']=len(row['alternatives'])
        scored.append((score,public))
    scored.sort(key=lambda x:(-x[0],x[1]['id']))
    return {'items':[r for _,r in scored[:limit]],'total':len(scored),'catalog_total':len(data['items']),
            'source_files':data['source_files'],'revision':data['revision'],'errors':data['errors'],
            'meaning':'원문 발견과 실제 도구 인증/실행 성공은 다릅니다.'}


def resource_path(row,relative=''):
    base=Path(row['directory']).resolve()
    if not relative:return Path(row['path'])
    if not isinstance(relative,str) or len(relative)>500 or Path(relative).is_absolute() or ':' in relative:
        raise ValueError('선택한 스킬 안의 상대 경로를 지정하세요.')
    path=(base/relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():raise ValueError('스킬 폴더 안의 기존 파일만 읽을 수 있습니다.')
    return path


def read(ident,resource='',offset=0,workspace=None):
    if type(offset) is not int or not 0<=offset<=MAX_SOURCE:raise ValueError('원문 위치를 확인하세요.')
    row=resolve(ident,workspace);path=resource_path(row,resource)
    if path.stat().st_size>MAX_SOURCE:raise ValueError('원문은 512KiB 이하만 읽을 수 있습니다.')
    try:text=path.read_text(encoding='utf-8-sig')
    except UnicodeError:raise ValueError('바이너리 자료는 반환된 원본 위치에서 해당 이미지/문서 도구로 확인하세요.') from None
    chunk=text[offset:offset+24000]
    return {'state':'instruction_ready','skill':{k:v for k,v in row.items() if k!='description'},
            'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'text':chunk,
            'next_offset':offset+len(chunk) if offset+len(chunk)<len(text) else None,
            'relative_base':row['directory'],'completed':False,
            'execution':'이 원문을 현재 선택 모델이 업무에 적용합니다. scripts/ 도구는 공통 run으로 실제 실행하며 공식 연결 도구는 현재 대화의 도구를 사용하세요.'}
