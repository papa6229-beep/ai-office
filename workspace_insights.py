"""Conservative metadata-only review. Never moves or deletes user files."""
import collections,re,pathlib
STOP={'final','final2','copy','new','old','backup','ver','version','최종','최종본','수정','사본','복사본','완성','test','draft','temp','파일','자료','image','img','data','document','untitled','제목없음'}
def _stem(name):
 s=pathlib.Path(name).stem.lower();s=re.sub(r'\(\d+\)$','',s);s=re.sub(r'(?:20\d\d[-_.]?)?\d{6,8}$','',s);s=re.sub(r'(?i)(?:final|copy|backup|ver(?:sion)?|최종|수정|사본|복사본|완성|v\d+(?:\.\d+)*)',' ',s);return re.sub(r'[\s_\-().]+',' ',s).strip()
def review(files):
 if not files: return {'total':0,'groups':[],'versions':[],'attention':[],'note':'먼저 PC 자료 조사를 완료하세요.'}
 folder_groups=collections.defaultdict(list);stem_groups=collections.defaultdict(list)
 for f in files:
  folder_groups[f['folder']].append(f)
  stem=_stem(f['name'])
  if len(stem)>=2 and stem not in STOP:stem_groups[(f['folder'],stem)].append(f)
 groups=[]
 for path,items in sorted(folder_groups.items(),key=lambda kv:-len(kv[1]))[:20]:
  if len(items)<2:continue
  groups.append({'title':pathlib.Path(path).name or path,'folder':path,'count':len(items),'categories':dict(collections.Counter(x['category'] for x in items).most_common(3)),'examples':[x['name'] for x in items[:5]],'confidence':'폴더 기준','suggestion':'업무별 묶음 후보입니다. 파일 내용 확인 전 자동 이동하지 마세요.'})
 versions=[]
 for (folder,stem),items in sorted(stem_groups.items(),key=lambda kv:-len(kv[1])):
  if len(items)<2:continue
  # False positives possible: metadata is not evidence of a newer revision.
  versions.append({'label':stem,'folder':folder,'files':[{'name':x['name'],'mtime':x['mtime'],'size':x['size']} for x in sorted(items,key=lambda x:x['mtime'],reverse=True)[:12]],'assessment':'파일명 유사 후보 · 최신본 미확정'})
  if len(versions)>=30:break
 attention=[]
 for f in files:
  if f['protected']:continue
  if re.search(r'(?i)(?:최종|final|수정|backup|백업|copy|사본|복사본|\(\d+\))',f['name']):
   attention.append({'name':f['name'],'folder':f['folder'],'reason':'수정본·백업본으로 보이는 이름 (확인 필요)'})
  if len(attention)>=30:break
 return {'total':len(files),'groups':groups,'versions':versions,'attention':attention,'note':'현재는 파일명·폴더·수정일만으로 생성한 검토 후보입니다. AI 내용 분석이나 최신본 확정이 아닙니다. 읽기 전용이며 이동·삭제는 하지 않습니다.'}
