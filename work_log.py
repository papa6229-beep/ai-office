"""Mechanical work journal: verified receipts, structured queries, no inference."""
from contextlib import closing
import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent
KST=dt.timezone(dt.timedelta(hours=9))
LOCK=threading.RLock()
_last_sync={}
_workers=set()
_schema_ready=set()
HEALTH={}
LABELS={'received':'받은 파일','sent':'보낸 파일','move':'파일 정리','folder':'폴더 정리',
        'report':'보고서','launch':'프로그램·사이트','project':'프로젝트','note':'직접 기록','activity':'업무 활동'}


def today():return dt.datetime.now(KST).date()


def _db(db):
    c=sqlite3.connect(str(db),timeout=20);c.row_factory=sqlite3.Row
    key=str(Path(db).resolve())
    with LOCK:
      if key not in _schema_ready:
        c.executescript('''CREATE TABLE IF NOT EXISTS work_log_events(
      id TEXT PRIMARY KEY,source_key TEXT NOT NULL UNIQUE,at REAL NOT NULL,day TEXT NOT NULL,
      kind TEXT NOT NULL,title TEXT NOT NULL,person TEXT NOT NULL DEFAULT '',
      data TEXT NOT NULL,recorded_at REAL NOT NULL);
      CREATE INDEX IF NOT EXISTS work_log_day_kind ON work_log_events(day,kind,at);
      CREATE INDEX IF NOT EXISTS work_log_person ON work_log_events(person,kind,at);
      CREATE TABLE IF NOT EXISTS work_log_projects(id TEXT PRIMARY KEY,label TEXT NOT NULL,first_seen REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS work_log_meta(id TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        c.execute("UPDATE work_log_events SET data=json_set(data,'$.time_precision','execution') WHERE kind IN ('move','folder') AND json_extract(data,'$.time_precision') IS NULL")
        c.commit();_schema_ready.add(key)
    return c


def event(db,key,kind,title,at=None,person='',data=None):
    if kind not in LABELS:raise ValueError('작업일지 분류를 확인해 주세요.')
    at=time.time() if at is None else float(at)
    if not 0<at<time.time()+86400:raise ValueError('기록 시각을 확인해 주세요.')
    title=str(title).strip()[:300];person=str(person).strip()[:120]
    payload=json.dumps(data or {},ensure_ascii=False)
    if len(payload)>20000:raise ValueError('기록 내용이 너무 길어요.')
    ident=hashlib.sha256(key.encode()).hexdigest()[:32]
    with LOCK,closing(_db(db)) as c:
        c.execute('INSERT OR IGNORE INTO work_log_events VALUES(?,?,?,?,?,?,?,?,?)',
                  (ident,key,at,dt.datetime.fromtimestamp(at,KST).date().isoformat(),kind,title,person,payload,time.time()))
        c.commit()
    if kind in {'received','sent','move'}:
        import brand_work_events
        brand_work_events.safe_capture(key,kind,at,person,data)
    return ident


def safe_event(*args,**kwargs):
    try:return event(*args,**kwargs)
    except (OSError,ValueError,sqlite3.Error):return None


def _tables(c,name):return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",(name,)).fetchone())


def sync(db,force=False):
    """Reconcile operation receipts. Reruns cannot duplicate journal rows."""
    import file_history
    key=str(Path(db).resolve())
    with LOCK:
        if not force and time.monotonic()-_last_sync.get(key,0)<15:return
        files=file_history._all(db,lambda r:r['state']!='existing')
        bypath={}
        for r in files:
            for p in {r['original_path'],r['current_path']}:bypath.setdefault(file_history._key(p),[]).append(r)
            if r['direction']=='out' and r['state']!='sent':continue
            if r['direction']=='in' and r['state'] in {'pending','error'}:continue
            kind='received' if r['direction']=='in' else 'sent'
            data={k:r.get(k) for k in ('name','source','size','current_path','original_path','person_origin','sha256')}
            data.update(file_id=r['id'],status='confirmed' if r.get('ref') or kind=='sent' else 'pc_saved',
                        saved_at=r.get('received_at'),direction=r['direction'])
            event(db,'file:'+r['id'],kind,r['name'],r['timestamp'],r['person'],data)
            # A later user annotation is evidence of a name, not a second receipt.
            if r['person']:
                with closing(_db(db)) as c:
                    c.execute("UPDATE work_log_events SET person=? WHERE (source_key=? OR json_extract(data,'$.file_id')=?) AND person<>?",(r['person'],'file:'+r['id'],r['id'],r['person']));c.commit()
        with closing(sqlite3.connect(str(db))) as c:
            c.row_factory=sqlite3.Row
            jobs=list(c.execute('SELECT id,payload,created FROM file_operation_jobs')) if _tables(c,'file_operation_jobs') else []
            steps=list(c.execute("SELECT job_id,position,payload FROM file_operation_steps WHERE status='done'")) if _tables(c,'file_operation_steps') else []
            launches=list(c.execute('SELECT id,response FROM launcher_requests WHERE response IS NOT NULL')) if _tables(c,'launcher_requests') else []
        with closing(_db(db)) as c:known={r[0] for r in c.execute('SELECT source_key FROM work_log_events')}
        for r in launches:
            value=json.loads(r['response']).get('result',{})
            if not value.get('launched_at') or not value.get('application') or 'launcher:'+r['id'] in known:continue
            event(db,'launcher:'+r['id'],'launch',value['application']+' 실행 요청',value['launched_at'],
                  data=dict(source='launcher',item_id=value.get('id'),application=value['application'],status='launch_dispatched'))
        jobmap={r['id']:json.loads(r['payload']) for r in jobs}
        folders={}
        for r in steps:
            s=json.loads(r['payload'])
            if s.get('op')=='mkdir' and s.get('destination') and not Path(s['destination']).name.startswith('.aemeath-'):
                folders.setdefault(r['job_id'],[]).append(s)
        for job_id,entries in folders.items():
            job=jobmap.get(job_id,{})
            if job.get('status')!='completed' or 'folders:'+job_id in known:continue
            stamp=max((s.get('completed_at',0) for s in entries),default=0) or file_history._epoch(job.get('created'))
            event(db,'folders:'+job_id,'folder','폴더 생성 · '+str(len(entries))+'개',stamp,
                  data=dict(source='local',job_id=job_id,count=len(entries),status='completed',
                            time_precision='exact' if any(s.get('completed_at') for s in entries) else 'execution'))
        for r in steps:
            if 'operation:'+r['job_id']+':'+str(r['position']) in known:continue
            s=json.loads(r['payload']);kind=s.get('op');job=jobmap.get(r['job_id'],{})
            source=s.get('source');dest=s.get('destination')
            if kind not in {'move','transfer'} or not source or not dest:continue
            is_file=s.get('expected',{}).get('kind')=='file'
            candidates=bypath.get(file_history._key(source),[])+bypath.get(file_history._key(dest),[])
            matches={r['id']:r for r in candidates if r.get('metadata') and all(
                r['metadata'].get(k)==s.get('expected',{}).get(k) for k in ('dev','ino','kind','size','mtime_ns'))}
            f=next(iter(matches.values())) if len(matches)==1 else {}
            data=dict(name=Path(dest).name,from_path=source,current_path=dest,job_id=r['job_id'],
                      source=f.get('source','local'),file_id=f.get('id'),status='completed',metadata=s.get('post'),
                      time_precision='exact' if s.get('completed_at') else 'execution')
            stamp=file_history._epoch(s.get('completed_at')) or file_history._epoch(job.get('created'))
            event(db,'operation:'+r['job_id']+':'+str(r['position']),'move' if is_file else 'folder',Path(dest).name,stamp,f.get('person',''),data)
        reports=ROOT/'local_backups/report-automation'
        if reports.is_dir():
            for path in reports.glob('*/result.json'):
                if path.is_symlink() or path.stat().st_size>4*1024*1024:continue
                try:
                    result=json.loads(path.read_text('utf-8'))
                    for stage,label in [('report','업무보고서 저장'),('inspection_report','보고서 최종 저장')]:
                        if not result.get(stage,{}).get('verified'):continue
                        saved=result[stage].get('saved',True)
                        if not saved:label=label.replace('저장','내용 확인')
                        report_key='report:'+path.parent.name+':'+stage
                        if report_key in known:
                            with closing(_db(db)) as c:
                                c.execute("UPDATE work_log_events SET title=?,data=json_set(data,'$.saved',?) WHERE source_key=? AND (title<>? OR json_extract(data,'$.saved') IS NULL)",(label,int(bool(saved)),report_key,label));c.commit()
                            continue
                        # Future runs write exact verification timestamps; old receipts
                        # expose only execution time, which must be labelled accordingly.
                        stamp=result[stage].get('verified_at') or result.get('started')
                        if not stamp:continue
                        event(db,report_key,'report',label,stamp,data=dict(
                            report_day=result.get('day'),status='verified',stage=stage,
                            time_precision='exact' if result[stage].get('verified_at') else 'execution',
                            source='daily_report',saved=bool(saved)))
                except (OSError,ValueError,KeyError):continue
        _last_sync[key]=time.monotonic()
        HEALTH[key]={'state':'watching','last_sync':time.time()}


def observe_projects(db,items):
    """Coarse metadata only; no code, outputs or conversation bodies are copied."""
    now=time.time();day=today().isoformat();seen=set()
    with LOCK,closing(_db(db)) as c:
        baseline=bool(c.execute("SELECT 1 FROM work_log_meta WHERE id='projects_initialized'").fetchone())
        for row in items:
            key=row.get('project_key') or row.get('id');label=row.get('project') or row.get('title')
            if not key or not label or key in seen:continue
            seen.add(key)
            old=c.execute('SELECT 1 FROM work_log_projects WHERE id=?',(key,)).fetchone()
            c.execute('INSERT OR IGNORE INTO work_log_projects VALUES(?,?,?)',(key,label,now));c.commit()
            label={'AI_OFFICE_GitHub':'AI OFFICE','godo':'고도'}.get(label,label)
            if baseline and not old:
                event(db,'new-project:'+key,'project','새 프로젝트 확인 · '+label,now,
                      data=dict(project=label,source='codex',status='first_observed'))
            active=any(r.get('project_key')==key and r.get('status')=='진행 중' for r in items)
            if active:event(db,'project-day:'+key+':'+day,'project','Codex · '+label,now,
                            data=dict(project=label,source='codex',status='activity_observed'))
        c.execute("INSERT OR IGNORE INTO work_log_meta VALUES('projects_initialized','1')");c.commit()


def start(db):
    key=str(Path(db).resolve())
    with LOCK:
        if key in _workers:return
        _workers.add(key)
    def watch():
        tick=0
        while True:
            try:
                sync(db)
                if tick%4==0:
                    import project_panels
                    observe_projects(db,project_panels.catalog().get('items',[]))
            except Exception:
                HEALTH[key]={'state':'retrying','last_sync':time.time()}
            tick+=1;time.sleep(15)
    threading.Thread(target=watch,daemon=True,name='office-work-log').start()


def period_dates(period='today',start_date=None,end_date=None):
    day=today()
    if start_date or end_date:
        first=dt.date.fromisoformat(start_date or end_date);last=dt.date.fromisoformat(end_date or start_date)
    elif period=='all':return None,None
    elif period=='yesterday':first=last=day-dt.timedelta(days=1)
    elif period=='last7':first,last=day-dt.timedelta(days=6),day
    elif period=='last30':first,last=day-dt.timedelta(days=29),day
    elif period=='last_week':last=day-dt.timedelta(days=day.weekday()+1);first=last-dt.timedelta(days=6)
    elif period=='this_month':first,last=day.replace(day=1),day
    elif period=='today':first=last=day
    else:raise ValueError('날짜 범위를 확인해 주세요.')
    if first>last:raise ValueError('시작일이 종료일보다 늦어요.')
    return first.isoformat(),last.isoformat()


def query(db,period='today',start_date=None,end_date=None,person='',kind='',source='',search='',limit=200,offset=0,order='desc',_export=False):
    if kind and kind not in LABELS:raise ValueError('기록 종류를 확인해 주세요.')
    if not isinstance(limit,int) or not 1<=limit<=(2147483647 if _export else 1000) or not isinstance(offset,int) or offset<0:raise ValueError('조회 개수를 확인해 주세요.')
    if not all(isinstance(s,str) and len(s)<=200 for s in (person,search)):raise ValueError('검색 조건을 확인해 주세요.')
    if source not in {'','hanbiro','nateon','downloads','local','codex','launcher','daily_report','user','office'} or order not in {'asc','desc'}:raise ValueError('출처와 정렬 조건을 확인해 주세요.')
    # An explicit query must see the latest completed receipts even between
    # observer ticks; it never waits for the background polling interval.
    sync(db,force=True);first,last=period_dates(period,start_date,end_date);where=[];args=[]
    if first:where.extend(['day>=?','day<=?']);args.extend([first,last])
    if person:where.append('instr(lower(person),lower(?))>0');args.append(person)
    if kind:where.append('kind=?');args.append(kind)
    if source:where.append("json_extract(data,'$.source')=?");args.append(source)
    if search:where.append('(instr(lower(title),lower(?))>0 OR instr(lower(data),lower(?))>0)');args.extend([search,search])
    sql=' WHERE '+' AND '.join(where) if where else ''
    with closing(_db(db)) as c:
        total=c.execute('SELECT count(*) FROM work_log_events'+sql,args).fetchone()[0]
        counts={r[0]:r[1] for r in c.execute('SELECT kind,count(*) FROM work_log_events'+sql+' GROUP BY kind',args)}
        rows=[dict(r) for r in c.execute('SELECT * FROM work_log_events'+sql+' ORDER BY at '+order+',id LIMIT ? OFFSET ?',(*args,limit,offset))]
    for row in rows:
        row['data']=json.loads(row['data']);row.pop('source_key',None);row['label']=LABELS[row['kind']]
        row['time']=dt.datetime.fromtimestamp(row['at'],KST).strftime('%H:%M')
        if row['data'].get('time_precision')=='date':row['time']='시간 미지정'
        elif row['data'].get('time_precision')=='execution':row['time']+=' 실행기록'
    return dict(items=rows,total=total,counts=counts,start_date=first,end_date=last,offset=offset,
                has_more=offset+len(rows)<total,mode='code',model_calls=0,
                scope='확인된 작업 기록 기준이에요. 프로그램 실행 이후 앱 안의 모든 행동을 추적하지 않아요.')


def add_note(db,text,title='직접 남긴 업무',day=None,request_id=None):
    if not isinstance(text,str) or not text.strip() or len(text)>5000:raise ValueError('기록할 내용을 1~5000자로 알려주세요.')
    at=time.time();precision='exact'
    if day and day!=today().isoformat():
        at=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(),KST).timestamp();precision='date'
    ident=event(db,'note:'+(request_id or uuid.uuid4().hex),'note',title,at,
                data=dict(text=text.strip(),source='user',time_precision=precision))
    return dict(id=ident,day=dt.datetime.fromtimestamp(at,KST).date().isoformat(),mode='code',model_calls=0,message='태준님, 작업일지에 기록했어요.')


def activity(db,title,summary,application='',project='',status='completed',request_id=None):
    if not isinstance(title,str) or not title.strip() or len(title)>150 or not isinstance(summary,str) or len(summary)>1000:
        raise ValueError('업무명과 짧은 실제 결과를 확인해 주세요.')
    if status not in {'completed','partial','cancelled'}:raise ValueError('실제 완료 상태를 확인해 주세요.')
    ident=event(db,'activity:'+(request_id or uuid.uuid4().hex),'activity',title,data=dict(
        text=summary,application=str(application)[:120],project=str(project)[:120],source='office',status=status))
    return dict(id=ident,mode='code',model_calls=0)


def reveal(db,ident):
    with closing(_db(db)) as c:r=c.execute('SELECT at,data FROM work_log_events WHERE id=?',(ident,)).fetchone()
    if not r:raise ValueError('작업일지에서 파일을 다시 선택해 주세요.')
    data=json.loads(r['data'])
    if data.get('file_id'):
        import file_history
        return file_history.reveal(db,data['file_id'])
    # Recorded file operations also retain identity at the final step. Resolve
    # later folder renames through the same code used by file history.
    import file_history,file_operations as ops,os,subprocess
    path=Path(data.get('current_path',''))
    if not path.is_absolute():raise ValueError('연결된 파일 위치가 없어요.')
    with closing(sqlite3.connect(str(db))) as c:
        c.row_factory=sqlite3.Row;moves=file_history._moves(c)
    meta=data.get('metadata')
    if not isinstance(meta,dict):raise ValueError('이 과거 기록에는 파일 신원이 없어 현재 위치를 다시 확인해야 해요.')
    current=file_history._current(dict(base_path=str(path),base_time=r['at'],metadata=meta,size=meta.get('size'),direction='in',state='organized'),moves)
    if not current['available']:raise ValueError('기록된 파일 신원과 현재 파일이 달라 위치를 다시 확인해야 해요.')
    path=ops._guard(Path(current['current_path']))
    subprocess.Popen([str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'explorer.exe'),'/select,'+str(path)],shell=False)
    return dict(path=str(path),dispatched=True,mode='code',model_calls=0)


def export_document(db,**filters):
    requested=filters.pop('limit',2147483647);filters=dict(filters,limit=requested,offset=0);result=query(db,_export=True,**filters)
    folder=ROOT/'.provider_runtime/work-log/exports';folder.mkdir(parents=True,exist_ok=True)
    ident=uuid.uuid4().hex;path=folder/(ident+'.docx')
    from xml.sax.saxutils import escape
    def p(text,bold=False):
        return '<w:p><w:r><w:rPr><w:rFonts w:ascii="Malgun Gothic" w:eastAsia="맑은 고딕"/>'+('<w:b/>' if bold else '')+'</w:rPr><w:t xml:space="preserve">'+escape(str(text))+'</w:t></w:r></w:p>'
    content=[p('작업일지',True),p((result['start_date'] or '전체')+' ~ '+(result['end_date'] or '전체')+' · '+str(len(result['items']))+'건'+(' / 전체 '+str(result['total'])+'건' if result['has_more'] else ''))]
    for r in result['items']:
        d=r['data'];content.append(p(r['day']+' '+r['time']+' · '+r['label']+' · '+r['title'],True))
        for s in [r['person'],d.get('text'),d.get('from_path'),d.get('current_path'),d.get('project')]:
            if s:content.append(p(s))
    body='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join(content)+'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1000" w:right="1000" w:bottom="1000" w:left="1000"/></w:sectPr></w:body></w:document>'
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',body)
    return dict(id=ident,path=str(path),url='/api/work/log-document?id='+ident,name='작업일지.docx',count=len(result['items']),mode='code',model_calls=0)


def document_path(ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('문서를 다시 선택해 주세요.')
    path=ROOT/'.provider_runtime/work-log/exports'/(ident+'.docx')
    if not path.is_file() or path.is_symlink():raise ValueError('문서를 찾지 못했어요.')
    return path


def match_command(text):
    text=text.strip().rstrip('~.!? ').strip()
    note=re.fullmatch(r'(?:(오늘|어제)\s*)?(?:작업일지|업무일지|업무로그|작업로그|일지|로그)에\s+(.{1,5000}?)\s*(?:추가해(?:줘)?|기록해(?:줘)?|남겨줘|적어줘)',text,re.S)
    if note:
        day=(today()-dt.timedelta(days=1)).isoformat() if note[1]=='어제' else today().isoformat()
        return dict(id='work_log_note',args=dict(text=note[2].strip(),day=day))
    # Query verbs do not authorize file modification or messages. Unknown action
    # words fall through to the normal agent and the same structured tools.
    if len(text)>300 or re.search(r'삭제|옮겨|이동해|전송해|보내줘|수정해|구조|기능|버튼|메뉴|탭|예를|예시',text):return None
    if re.search(r'만들|작성',text) and not re.search(r'문서|워드|docx',text,re.I):return None
    if not re.search(r'파일|작업일지|업무일지|업무로그|작업로그',text):return None
    if not re.search(r'(?:알려(?:줘|주세요)|보여(?:줘|주세요)|찾아(?:줘|주세요)|조회(?:해줘|해주세요)|확인해줘|작성해줘|만들어줘|열어(?:줘|주세요))$',text):return None
    if not re.search(r'받은|보낸|수신|발신|작업일지|업무일지|업무로그|작업로그',text):return None
    person=re.search(r'([가-힣A-Za-z]{2,20}?)(?:님)?(?:이)?(?:에게|한테|께|으로부터)',text)
    period='all' if '최근' in text or '마지막' in text else 'today'
    recognized_period=False
    for pattern,value in [(r'지난\s*(?:1\s*)?주일|지난\s*7\s*일|최근\s*7\s*일','last7'),
                          (r'지난주','last_week'),(r'지난\s*30\s*일|최근\s*한\s*달','last30'),
                          (r'이번\s*달','this_month'),(r'어제','yesterday'),(r'오늘','today')]:
        if re.search(pattern,text):period=value;recognized_period=True;break
    args=dict(period=period,person=person[1] if person else '',kind='received' if re.search('받은|수신',text) else 'sent' if re.search('보낸|발신',text) else '',limit=50)
    for label,source in [('네이트온','nateon'),('한비로','hanbiro'),('코덱스','codex'),('바로가기','launcher')]:
        if label in text:args['source']=source;break
    if '오래된' in text:args['order']='asc'
    dates=re.findall(r'20\d{2}-\d{2}-\d{2}',text)
    if dates:args.update(start_date=dates[0],end_date=dates[-1])
    span=re.search(r'(?:지난|최근)\s*(\d{1,3})\s*(주일|주|일)(?:간|동안)?',text)
    if span and not dates:
        days=int(span[1])*(7 if span[2] in {'주','주일'} else 1)
        if days<1:return None
        args.update(start_date=(today()-dt.timedelta(days=days-1)).isoformat(),end_date=today().isoformat())
    if not dates and not span and not recognized_period and re.search('지난|이번',text):return None
    if not dates and not span and re.search(r'최근\s*(?:한|두|세|네)\s*(?:주|일|달|개월)',text):return None
    if not dates and len(re.findall('어제|오늘|지난주|이번달',text))>1:return None
    if not dates and not span and re.search(r'그제|이번\s*주|작년|지난달|\d+\s*월|\d+\s*년',text):return None
    number=re.search(r'(\d{1,3})\s*(?:개|건)',text)
    if number:args['limit']=max(1,min(1000,int(number[1])))
    export=bool(re.search(r'문서|워드|docx',text,re.I) and re.search('작성|만들|내보내|저장',text))
    if export and not number:args.pop('limit',None)
    opened=bool(re.search(r'(?:위치|폴더)(?:도|를)?\s*열어',text))
    return dict(id='work_log_export' if export else 'work_log_query',args=dict(args,**({'open_location':opened} if not export else {})))


def answer(db,open_location=False,**filters):
    result=query(db,**filters)
    if not result['items']:return dict(result,message='태준님, 그 조건에 맞는 확인된 기록은 없어요.')
    people={r['person'] for r in result['items'] if r['person']}
    if filters.get('person') and len(people)>1 and filters['person'] not in people:
        return dict(result,message='태준님, 이름이 일치하는 분이 여럿이에요: '+', '.join(sorted(people))+'. 누구인지 알려주세요.')
    rows=result['items'];lines=['태준님, 조건에 맞는 기록 '+str(result['total'])+'건이에요.']
    for n,r in enumerate(rows,1):
        lines.append(str(n)+'. '+r['day']+' '+r['time']+' · '+(r['person']+' · ' if r['person'] else '')+r['title'])
    if len(rows)==1 and rows[0]['data'].get('file_id'):
        import file_history
        try:
            current=file_history.detail(db,rows[0]['data']['file_id'])['item']
            result['current_path']=current['current_path'];lines.append('현재 위치: '+current['current_path'])
            if not current['available']:lines.append('현재 파일은 위치 확인이 필요해요.')
        except (ValueError,OSError):lines.append('현재 파일 위치는 확인이 필요해요.')
    if result['has_more']:lines.append('일부만 표시했어요. 조회 개수나 기간을 지정하면 더 볼 수 있어요.')
    if open_location:
        if len(rows)==1:
            try:result['reveal']=reveal(db,rows[0]['id']);lines.append('탐색기에 파일 위치 표시를 요청했어요.')
            except (ValueError,OSError) as e:lines.append(str(e))
        else:lines.append('여러 파일이 있어요. 위치를 열 파일을 골라 주세요.')
    result['message']='\n'.join(lines);return result
