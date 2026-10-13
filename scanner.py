"""Read-only bounded inventory. No files are opened except optional equal-size duplicate hashing."""
import os, pathlib, collections, hashlib, threading, time, datetime, json, csv, uuid

EXCLUDE_NAMES = {'windows','program files','program files (x86)','programdata','$recycle.bin','system volume information','recovery','perflogs','.git','node_modules','.venv','venv','__pycache__','appdata','.cache','.codex','comfyui','ollama','models','checkpoints','loras','embeddings'}
EXT_GROUPS={
 '디자인 원본':{'.psd','.psb','.ai','.eps','.fig','.sketch','.indd','.xd'},
 '이미지':{'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff','.svg'},
 '문서':{'.pdf','.doc','.docx','.hwp','.hwpx','.txt','.md','.rtf'},
 '스프레드시트':{'.xls','.xlsx','.csv','.ods'},
 '프레젠테이션':{'.ppt','.pptx','.key'},
 '영상·음성':{'.mp4','.mov','.avi','.mkv','.mp3','.wav','.webm'},
 '압축 자료':{'.zip','.7z','.rar','.tar','.gz'},
 '개발 자료':{'.py','.js','.ts','.jsx','.tsx','.html','.css','.json','.toml','.yaml','.yml','.sql'},
}
PROTECTED_EXT={'.exe','.dll','.sys','.bat','.cmd','.ps1','.msi','.db','.sqlite','.sqlite3','.lock','.env','.p12','.pem','.key'}
MAX_FILES=100000
STATE={'id':None,'status':'idle','scanned':0,'skipped':0,'errors':0,'started':None,'finished':None,'roots':[],'summary':None,'message':''}
LOCK=threading.Lock()
FILES=[]

def _category(suffix):
 for k,exts in EXT_GROUPS.items():
  if suffix in exts:return k
 return '기타'

def _blocked(p):
 return any(x.lower() in EXCLUDE_NAMES for x in p.parts) or p.is_symlink()

def _root_ok(p):
 p=pathlib.Path(p).expanduser().absolute()
 if not p.is_dir():raise ValueError('존재하지 않는 폴더입니다: '+str(p))
 if p.is_symlink():raise ValueError('바로가기·링크 폴더는 지원하지 않습니다.')
 # reading drive roots is allowed; system folders skipped
 return p

def _set(**updates):
 with LOCK: STATE.update(updates)

def _worker(roots):
 global FILES
 found=[]; errors=0; skipped=0; truncated=False
 try:
  queue=[str(_root_ok(p)) for p in roots]
  for root in queue:
   for base,dirs,files in os.walk(root,topdown=True,followlinks=False,onerror=lambda e:None):
    bp=pathlib.Path(base)
    kept=[]
    for d in dirs:
     dp=bp/d
     if d.lower() in EXCLUDE_NAMES or dp.is_symlink():skipped+=1
     else: kept.append(d)
    dirs[:]=kept
    for n in files:
     if len(found)>=MAX_FILES:truncated=True;break
     p=bp/n
     try:
      if p.is_symlink():skipped+=1;continue
      st=p.stat(); ext=p.suffix.lower()
      protected=ext in PROTECTED_EXT or n.startswith('.')
      found.append({'path':str(p),'name':n,'folder':str(bp),'extension':ext,'size':st.st_size,'mtime':datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d'),'category':_category(ext),'protected':protected})
     except (PermissionError,OSError,ValueError):errors+=1
    _set(scanned=len(found),skipped=skipped,errors=errors,message=str(bp)[:180])
    if truncated:break
   if truncated:break
  groups=collections.Counter(f['category'] for f in found)
  dirs=collections.Counter(f['folder'] for f in found)
  sizegroups=collections.defaultdict(list)
  for f in found:
   if f['size']>0 and not f['protected']:sizegroups[f['size']].append(f)
  possible=sum(len(v) for v in sizegroups.values() if len(v)>1)
  summary={'total':len(found),'bytes':sum(f['size'] for f in found),'categories':dict(groups.most_common()),'folders':dirs.most_common(40),'possible_duplicate_candidates':possible,'protected':sum(f['protected'] for f in found),'errors':errors,'skipped_dirs':skipped,'truncated':truncated,'analysis_level':'메타데이터 기준 (파일 내용 미확인)'}
  with LOCK:
   FILES=found
   STATE.update(status='done',summary=summary,finished=datetime.datetime.now().isoformat(timespec='seconds'),message='조사 완료')
 except Exception as e:_set(status='error',finished=datetime.datetime.now().isoformat(timespec='seconds'),message=str(e))

def start(roots):
 if not isinstance(roots,list) or not roots or len(roots)>6:raise ValueError('조사 폴더를 1~6개 지정하세요.')
 canonical=[str(_root_ok(str(x))) for x in roots]
 with LOCK:
  if STATE['status']=='running':raise ValueError('이미 조사가 진행 중입니다.')
  STATE.update(id=uuid.uuid4().hex,status='running',scanned=0,skipped=0,errors=0,started=datetime.datetime.now().isoformat(timespec='seconds'),finished=None,roots=canonical,summary=None,message='조사 시작')
 threading.Thread(target=_worker,args=(canonical,),daemon=True).start()
 return {'ok':True,'roots':canonical}

def status():
 with LOCK:return json.loads(json.dumps(STATE,ensure_ascii=False))

def sample(page=0,category=None,term=None):
 page=max(0,min(int(page),100000)); term=(term or '').lower().strip()
 with LOCK: data=list(FILES) if STATE['status']=='done' else []
 if category:data=[f for f in data if f['category']==category]
 if term:data=[f for f in data if term in f['name'].lower() or term in f['folder'].lower()]
 count=len(data); return {'total':count,'page':page,'items':data[page*100:(page+1)*100]}

def duplicate_report():
 # Hash only identical-size files, bounded to reduce disk IO; read-only.
 with LOCK:
  if STATE['status']!='done':raise ValueError('먼저 조사를 완료하세요.')
  data=list(FILES)
 sizes=collections.defaultdict(list)
 for f in data:
  if 0<f['size']<=200*1024*1024 and not f['protected']:sizes[f['size']].append(f)
 candidates=[item for group in sizes.values() if len(group)>1 for item in group]
 if len(candidates)>2000:raise ValueError('중복 후보가 2,000개 이상입니다. 범위를 줄여 재조사하세요.')
 digests=collections.defaultdict(list); errors=0
 for f in candidates:
  try:
   h=hashlib.sha256()
   with open(f['path'],'rb') as stream:
    for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
   digests[(f['size'],h.hexdigest())].append(f['path'])
  except (PermissionError,OSError):errors+=1
 groups=[{'size':key[0],'hash':key[1],'paths':paths} for key,paths in digests.items() if len(paths)>1]
 return {'groups':groups[:200],'total_groups':len(groups),'errors':errors,'note':'내용이 완전히 같은 파일 후보입니다. 삭제는 하지 않습니다.'}
