"""Read-only project/archive intelligence. No extract, copy, rename or delete."""
import pathlib, zipfile, hashlib, re, collections, datetime
MAX_ARCHIVES=150
MAX_MEMBERS=4000
MAX_UNCOMPRESSED=350*1024*1024

def _normalized(name):
    name=pathlib.PurePosixPath(name.replace('\\','/')).name.lower()
    name=re.sub(r'\(\d+\)(?=\.)','',name)
    name=re.sub(r'(?i)(?:v(?:ersion)?[\s._-]*\d+(?:[._-]\d+)*|최종|수정본|업데이트|update|backup|백업|복사본|copy|latest|visual)',' ',name)
    return re.sub(r'[^\w가-힣]+',' ',pathlib.Path(name).stem).strip() or pathlib.Path(name).stem

def _digest(fp):
    h=hashlib.sha256()
    with open(fp,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def _zip_inventory(p):
    with zipfile.ZipFile(p) as z:
        infos=[x for x in z.infolist() if not x.is_dir()]
        if len(infos)>MAX_MEMBERS:return None,'압축 내부 파일 수 제한 초과'
        if sum(x.file_size for x in infos)>MAX_UNCOMPRESSED:return None,'압축 해제 예상 크기 제한 초과'
        names=[]; records={}
        for i in infos:
            n=i.filename.replace('\\','/').lstrip('./')
            if '..' in pathlib.PurePosixPath(n).parts:continue
            names.append(n)
            records[n]=f'{i.file_size}:{i.CRC:08x}'
        # Prefix is sometimes a wrapper folder, remove only if all files share it
        segs=[n.split('/') for n in names]
        if segs and all(len(s)>1 and s[0]==segs[0][0] for s in segs):
            records={'/'.join(k.split('/')[1:]):v for k,v in records.items()}
        return records,None

def analyze(folder):
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError('분석할 폴더를 선택하거나 경로를 입력해 주세요. 회색 경로 문구는 예시입니다.')
    root=pathlib.Path(folder).expanduser().resolve()
    if not root.is_dir():raise ValueError('존재하는 폴더를 지정하세요.')
    if root==pathlib.Path(root.anchor) or root==pathlib.Path.home():raise ValueError('전체 드라이브/홈 대신 작은 폴더부터 분석하세요.')
    archives=sorted([p for p in root.iterdir() if p.is_file() and p.suffix.lower()=='.zip' and not p.is_symlink()],key=lambda p:p.name.lower())
    truncated=len(archives)>MAX_ARCHIVES
    archives=archives[:MAX_ARCHIVES]
    items=[]
    for p in archives:
        st=p.stat(); entry={'name':p.name,'path':str(p),'bytes':st.st_size,'modified':datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M'),'group':_normalized(p.name)}
        try:
            entry['sha256']=_digest(p) if st.st_size<=500*1024*1024 else None
            entry['members'],entry['warning']=_zip_inventory(p)
            entry['files_inside']=len(entry['members']) if entry['members'] is not None else None
        except (OSError,zipfile.BadZipFile,RuntimeError,ValueError) as e:
            entry['members']=None;entry['warning']='읽기 실패 또는 손상: '+str(e)[:90];entry['files_inside']=None
        items.append(entry)
    groups=[]
    # shared internal file paths allow similarity even when archive names differ
    parent=list(range(len(items)))
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    def union(a,b):parent[find(a)]=find(b)
    links=[]
    for i in range(len(items)):
        for j in range(i+1,len(items)):
            a,b=items[i],items[j]
            akeys=set(a['members'] or {}); bkeys=set(b['members'] or {})
            common=akeys & bkeys
            similarity=len(common)/len(akeys|bkeys) if (akeys|bkeys) else 0
            equal=bool(a['sha256'] and a['sha256']==b['sha256'])
            same_content=bool(akeys and akeys==bkeys and a['members']==b['members'])
            same_name=a['group']==b['group'] and len(a['group'])>=3
            if equal or same_content or similarity>=.65 and len(common)>=2 or (same_name and len(common)>=1):
                union(i,j)
                relation='완전히 동일한 ZIP' if equal else '내부 파일 구성과 CRC 동일' if same_content else '내부 파일 경로 유사 (내용 차이는 별도 확인 필요)'
                if not equal and not same_content and a['members'] and b['members'] and common:
                    unchanged=sum(a['members'][k]==b['members'][k] for k in common)
                    relation+=f' · 공통 {len(common)}개 중 {unchanged}개 동일 CRC'
                links.append({'a':i,'b':j,'relation':relation,'shared_paths':len(common),'path_similarity':round(similarity,2)})
    components=collections.defaultdict(list)
    for i in range(len(items)):components[find(i)].append(i)
    for c in components.values():
        if len(c)<2:continue
        pairs=[q for q in links if q['a'] in c and q['b'] in c]
        same=all(q['relation']=='완전히 동일한 ZIP' for q in pairs) and len(pairs)==len(c)*(len(c)-1)//2
        groups.append({'files':[items[i]['name'] for i in c], 'relations':pairs,'assessment':'완전 동일 복제본 그룹' if same else '동일 프로젝트 후보 · 최신본 미확정','recommendation':'보관 위치를 함께 검토하세요. 자동 이동/삭제 없음. 파일 수정일만으로 최신본을 확정하지 않습니다.'})
    public=[{k:v for k,v in f.items() if k!='members'} for f in items]
    return {'folder':str(root),'archives':public,'groups':groups,'archive_count':len(items),'truncated':truncated,'note':('이 폴더의 바로 아래에서 ZIP 파일을 찾지 못했습니다. 실제 바탕화면 위치(OneDrive\\Desktop 포함)를 확인하거나 ZIP이 있는 폴더를 선택하세요. ' if not items else '')+'읽기 전용. ZIP 내부 목록/CRC는 압축 파일의 메타데이터이며, 프로젝트 동일성의 확정 근거가 아닙니다. 파일 이동·삭제 없음.'}
