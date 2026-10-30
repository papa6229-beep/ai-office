"""Local messenger download intake. Metadata polling and explicit filing rules, no model calls."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

import file_operations as ops
import file_transfer as transfer

LOCK=threading.RLock()
WORKERS=set()
ACTIVE=set()
HEALTH={}
DATE=re.compile(r'^(20\d{2}|\d{2})[._-](\d{1,2})[._-](\d{1,2})[ _.-]+(.+)$')
JAPAN=('TIS','탑마샬','니포리','닛포리','토이즈하트','와일드원','화보집','예약상품','재팬토이즈','무료체험단')

@contextmanager
def _db(db):
    c=sqlite3.connect(str(db),timeout=15);c.row_factory=sqlite3.Row
    try:
        with c:
            c.execute("CREATE TABLE IF NOT EXISTS received_sources(id TEXT PRIMARY KEY,label TEXT NOT NULL,root TEXT NOT NULL,initialized INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',scanned REAL)")
            c.execute('CREATE TABLE IF NOT EXISTS received_items(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT NOT NULL,meta TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,target TEXT NOT NULL DEFAULT \'\',job TEXT NOT NULL DEFAULT \'\',rule TEXT NOT NULL DEFAULT \'\',error TEXT NOT NULL DEFAULT \'\')')
            c.execute('CREATE INDEX IF NOT EXISTS received_items_state ON received_items(state,created DESC)')
            c.execute('CREATE TABLE IF NOT EXISTS received_rules(id TEXT PRIMARY KEY,title TEXT NOT NULL,data TEXT NOT NULL,uses INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL)')
            yield c
    finally:c.close()

def _seed(db):
    with LOCK,_db(db) as c:
        root=Path.home()/'Documents'/'네이트온 받은 파일'
        for ident,label,path in [('nateon','네이트온',str(root) if root.is_dir() else ''),('hanbiro','한비로톡','')]:
            c.execute('INSERT OR IGNORE INTO received_sources(id,label,root) VALUES(?,?,?)',(ident,label,path))
        base=Path('D:/웹디자인팀 관리/작업용파일')
        if base.is_dir():
            for ident,title,exts,group,dest in [('product','상품정보·이미지',['.xlsx','.xls','.xlsm','.zip'],'product_region',str(base)),('eventbrief','이벤트 기획서',['.eventbrief'],'plain',str(base/'이벤트 기획서'))]:
                value=dict(source='*',extensions=exts,contains='',grouping=group,destination=dest,automatic=False)
                c.execute('INSERT OR IGNORE INTO received_rules(id,title,data,updated) VALUES(?,?,?,?)',(ident,title,json.dumps(value,ensure_ascii=False),time.time()))

def _root(value):
    if not isinstance(value,str) or not value.strip() or len(value)>1000:raise ValueError('정리할 폴더의 전체 경로를 입력해 주세요.')
    path=Path(value.strip())
    if not path.is_absolute():raise ValueError('드라이브부터 시작하는 전체 경로를 입력해 주세요.')
    # Validate every existing ancestor; missing child folders are created in the journaled plan.
    nearest=ops._nearest(path);ops._guard(nearest)
    if path.exists() and not path.is_dir():raise ValueError('파일이 아닌 폴더를 지정해 주세요.')
    if path.exists():ops._guard(path)
    return Path(os.path.abspath(str(path)))

def configure_source(db,ident,root):
    if ident not in {'nateon','hanbiro'}:raise ValueError('메신저를 다시 선택해 주세요.')
    path=ops._guard(_root(root),True)
    if not path.is_dir():raise ValueError('실제 파일이 저장되는 폴더를 선택해 주세요.')
    _seed(db)
    with LOCK,_db(db) as c:
        old=c.execute('SELECT root FROM received_sources WHERE id=?',(ident,)).fetchone()
        if old and old['root']!=str(path):c.execute("UPDATE received_items SET state='missing' WHERE source=? AND state IN ('existing','pending','ready')",(ident,))
        c.execute('UPDATE received_sources SET initialized=CASE WHEN root=? THEN initialized ELSE 0 END,root=?,error=\'\' WHERE id=?',(str(path),str(path),ident))
        if ident=='hanbiro':c.execute('UPDATE received_sources SET label=? WHERE id=?',('한비로 지정 폴더',ident))
    return {'configured':True,'baseline_pending':True}

def _group(path):
    match=DATE.fullmatch(path.stem)
    if not match:raise ValueError('날짜·브랜드를 확인할 수 없어요. 이 파일은 정리 위치를 직접 지정해 주세요.')
    import datetime
    year=int(match[1]);year+=2000 if year<100 else 0
    date=datetime.date(year,int(match[2]),int(match[3])).isoformat();tail=match[4]
    if '신상발매 기념 이벤트_' in tail:brand=tail.rsplit('_',1)[1]
    elif '무료체험단' in tail:brand=re.sub(r'[_ ](?:수정|V\d+).*$', '',tail).replace('_',' ')
    elif tail.startswith('일본_'):
        parts=tail.split('_');brand=('일본 '+parts[1]+'·예약상품') if '예약상품' in parts else parts[1]+(' 화보집' if '화보집' in parts[2:] else '')
        if brand=='화보집':brand='일본 화보집'
    elif tail.startswith('중국_'):brand=tail.split('_')[1]
    else:
        prefix=re.split(r'샘플\s*가격|샘플|이미지',tail,maxsplit=1)[0].strip(' _')
        brand=re.sub(r'\s+\d{2,4}년.*$|\s+\d+차.*$','',prefix.split('_')[0]).strip()
        if brand.startswith('TIS'):brand='TIS'
    ops._name(brand)
    region='그외' if '나래' in brand else '일본' if any(word.casefold() in brand.casefold() for word in JAPAN) else '중국'
    return region,date+' '+brand

def _matches(item,rule):
    path=Path(item['path']);d=rule['data']
    if d['source'] not in {'*',item['source']} or path.suffix.lower() not in d['extensions']:return False
    if d['contains'] and d['contains'].casefold() not in path.name.casefold():return False
    if d['grouping'] in {'date_brand','product_region'}:
        if path.suffix.lower()=='.zip' and '이미지' not in path.stem:return False
        if d['grouping']=='product_region' and path.suffix.lower()!='.zip' and not any(word in path.stem for word in ('샘플','신상발매','예약상품','화보집','체험단')):return False
        try:_group(path)
        except (ValueError,IndexError):return False
    return True

def save_rule(db,payload):
    _seed(db)
    ident=payload.get('id') or uuid.uuid4().hex
    if not isinstance(ident,str) or not re.fullmatch('[a-z0-9_-]{1,64}',ident):raise ValueError('규칙을 다시 선택해 주세요.')
    title=payload.get('title','').strip();dest=_root(payload.get('destination',''))
    extensions=payload.get('extensions',[]);source=payload.get('source','*');group=payload.get('grouping','plain');contains=payload.get('contains','')
    if not title or len(title)>120 or source not in {'*','nateon','hanbiro'} or group not in {'plain','date_brand','product_region'}:raise ValueError('규칙 이름과 분류 방식을 확인해 주세요.')
    if not isinstance(extensions,list) or not extensions or len(extensions)>20 or any(not isinstance(e,str) or not re.fullmatch(r'\.[A-Za-z0-9]{1,20}',e) for e in extensions):raise ValueError('확장자는 .xlsx처럼 입력해 주세요.')
    automatic=payload.get('automatic',False)
    if type(automatic) is not bool or not isinstance(contains,str) or len(contains)>200:raise ValueError('규칙 조건을 확인해 주세요.')
    with LOCK,_db(db) as c:
        for row in c.execute('SELECT root FROM received_sources WHERE root<>\'\''):
            if dest.is_relative_to(Path(row['root'])):raise ValueError('수신 폴더 밖의 정리 위치를 지정해 주세요. 반복 수집을 방지해요.')
        data=dict(source=source,extensions=sorted(set(e.lower() for e in extensions)),contains=contains,grouping=group,destination=str(dest),automatic=automatic)
        c.execute('INSERT INTO received_rules(id,title,data,updated) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,data=excluded.data,updated=excluded.updated',(ident,title,json.dumps(data,ensure_ascii=False),time.time()))
    return {'id':ident,'title':title,**data}

def _rules(c):return [dict(id=r['id'],title=r['title'],uses=r['uses'],data=json.loads(r['data'])) for r in c.execute('SELECT * FROM received_rules ORDER BY updated DESC')]

def scan(db):
    _seed(db)
    with LOCK,_db(db) as c:sources=[dict(r) for r in c.execute('SELECT * FROM received_sources WHERE root<>\'\'')]
    for source in sources:
        try:
            root=ops._guard(Path(source['root']),True);entries=[];now=time.time()
            for path in root.iterdir():
                if not path.is_file() or path.name.startswith('.') or path.suffix.lower() in {'.tmp','.partial','.crdownload','.download'}:continue
                try:
                    meta=transfer.metadata(path)
                    if path.stat().st_file_attributes & (2|4):continue
                except (OSError,ValueError):continue
                raw=json.dumps(meta,sort_keys=True);ident=hashlib.sha256((source['id']+'|'+str(path)+'|'+raw).encode()).hexdigest()
                entries.append((ident,path,raw,meta))
            current={e[0] for e in entries}
            with LOCK,_db(db) as c:
                for ident,path,raw,meta in entries:
                    old=c.execute('SELECT * FROM received_items WHERE id=?',(ident,)).fetchone()
                    if not old:
                        c.execute("UPDATE received_items SET state='changed' WHERE source=? AND path=? AND id<>? AND state IN ('pending','ready','existing')",(source['id'],str(path),ident))
                        c.execute('INSERT INTO received_items(id,source,path,meta,state,created,updated) VALUES(?,?,?,?,?,?,?)',(ident,source['id'],str(path),raw,'existing' if not source['initialized'] else 'pending',now,now))
                    elif old['state']=='pending' and now-old['created']>=8:
                        try:
                            with transfer.pinned(path,meta,deleting=False):pass
                        except (OSError,ValueError):continue
                        c.execute("UPDATE received_items SET state='ready',updated=? WHERE id=?",(now,ident))
                for row in c.execute("SELECT id FROM received_items WHERE source=? AND state IN ('pending','ready','existing')",(source['id'],)).fetchall():
                    if row['id'] not in current:c.execute("UPDATE received_items SET state='missing',updated=? WHERE id=?",(now,row['id']))
                c.execute('UPDATE received_sources SET initialized=1,error=\'\',scanned=? WHERE id=?',(now,source['id']))
        except (OSError,ValueError) as error:
            with _db(db) as c:c.execute('UPDATE received_sources SET error=? WHERE id=?',(str(error)[:180],source['id']))

def _item(c,ident):
    row=c.execute('SELECT * FROM received_items WHERE id=?',(ident,)).fetchone()
    if not row:raise ValueError('받은 파일을 다시 선택해 주세요.')
    return dict(row)

def organize(db,ids,destination=None,grouping='plain',rule_id=None,background=False):
    if not isinstance(ids,list) or not 1<=len(ids)<=60 or len(set(ids))!=len(ids):raise ValueError('파일을 1~60개 선택해 주세요.')
    _seed(db)
    with LOCK,_db(db) as c:
        items=[_item(c,ident) for ident in ids]
        if all(r['job'] and r['state'] in {'organizing','organized'} for r in items):return {'job_id':items[0]['job'],'state':items[0]['state']}
        if any(r['state'] not in {'ready','existing','error'} for r in items):raise ValueError('파일 수신과 확인이 끝난 항목을 선택해 주세요.')
        rule=next((r for r in _rules(c) if r['id']==rule_id),None) if rule_id else None
        if rule_id and not rule:raise ValueError('저장한 규칙을 다시 선택해 주세요.')
        if rule:
            if any(not _matches(r,rule) for r in items):raise ValueError('이 규칙에 맞지 않는 파일이 있어요.')
            destination=rule['data']['destination'];grouping=rule['data']['grouping']
        if grouping not in {'plain','date_brand','product_region'}:raise ValueError('분류 방식을 확인해 주세요.')
        dest=_root(destination)
        roots=[Path(r[0]) for r in c.execute("SELECT root FROM received_sources WHERE root<>''")]
        if any(dest.is_relative_to(root) for root in roots):raise ValueError('수신 폴더 밖으로 정리해 주세요.')
        actions=[];folders={}
        def parent(path):
            if path in folders:return {'parent_action':folders[path]}
            if path.is_dir():return {'parent':ops.observe(path)}
            base=parent(path.parent);ident='folder_'+str(len(folders))
            actions.append(dict(op='mkdir',id=ident,name=path.name,**base));folders[path]=ident
            return {'parent_action':ident}
        for item in items:
            source=Path(item['path']);meta=json.loads(item['meta'])
            if not any(source.parent==root for root in roots) or not transfer.same_node(source,meta,contents=True):raise ValueError('파일이 바뀌었어요. 새 목록에서 다시 선택해 주세요.')
            target=dest
            if grouping!='plain':
                region,label=_group(source);target=dest/region/label if grouping=='product_region' else dest/label
            actions.append(dict(op='move',source={'path':str(source),'metadata':meta},name=source.name,**parent(target)))
        job=ops.plan(db,actions,'받은 파일 정리',uuid.uuid4().hex,conflict='rename')
        actual=ops.load(db,job['id']);moves={r['source']:r['destination'] for r in actual['actions'] if r['op']=='move'}
        for item in items:c.execute("UPDATE received_items SET state='organizing',target=?,job=?,rule=?,error='',updated=? WHERE id=?",(moves[item['path']],job['id'],rule_id or '',time.time(),item['id']))
        ACTIVE.add(job['id'])
    if background:
        threading.Thread(target=_finish,args=(db,job['id'],ids),daemon=True).start()
        return {'job_id':job['id'],'state':'organizing'}
    return _finish(db,job['id'],ids)

def _finish(db,job,ids):
    try:
        result=ops.run(db,job);saved=ops.load(db,job)
        steps={r['destination']:r for r in saved['steps'] if r['op'] in {'move','transfer'}}
        with LOCK,_db(db) as c:
            for ident in ids:
                item=_item(c,ident);step=steps.get(item['target'],{})
                ok=step.get('status')=='done' and not Path(item['path']).exists() and transfer.same_node(item['target'],step.get('post',{}),contents=True)
                c.execute('UPDATE received_items SET state=?,error=?,updated=? WHERE id=?',('organized' if ok else 'error','' if ok else result.get('reason') or '실제 정리 결과를 확인해 주세요.',time.time(),ident))
                if ok and item['rule']:c.execute('UPDATE received_rules SET uses=uses+1 WHERE id=?',(item['rule'],))
        return {'job_id':job,'state':result['status'],'counts':result['counts']}
    except Exception as error:
        with _db(db) as c:
            for ident in ids:c.execute("UPDATE received_items SET state='error',error=? WHERE id=?",(str(error)[:200],ident))
        return {'job_id':job,'state':'error'}
    finally:
        with LOCK:ACTIVE.discard(job)

def resume(db,ident,background=False):
    with LOCK,_db(db) as c:
        item=_item(c,ident)
        if not item['job'] or item['state']!='error':raise ValueError('중단된 정리 항목을 다시 선택해 주세요.')
        job=item['job'];ids=[r[0] for r in c.execute("SELECT id FROM received_items WHERE job=? AND state='error'",(job,))]
        c.execute("UPDATE received_items SET state='organizing',error='' WHERE job=? AND state='error'",(job,));ACTIVE.add(job)
    if background:
        threading.Thread(target=_finish,args=(db,job,ids),daemon=True).start();return {'job_id':job,'state':'organizing'}
    return _finish(db,job,ids)

def snapshot(db,include_existing=False,query='',offset=0):
    ensure_worker(db)
    with LOCK,_db(db) as c:
        # A previous process may have stopped while moving; never retry uncertain moves automatically.
        for row in c.execute("SELECT DISTINCT job FROM received_items WHERE state='organizing'").fetchall():
            if row['job'] not in ACTIVE:c.execute("UPDATE received_items SET state='error',error='이전 정리 기록을 확인한 뒤 다시 지시해 주세요.' WHERE job=? AND state='organizing'",(row['job'],))
        rules=_rules(c);sources=[dict(r) for r in c.execute('SELECT * FROM received_sources ORDER BY id')]
        states=['ready','pending','organizing','organized','error']+(['existing'] if include_existing else [])
        sql='state IN ('+','.join('?' for _ in states)+')';args=list(states)
        if query:sql+=' AND instr(lower(path),lower(?))>0';args.append(query[:200])
        total=c.execute('SELECT count(*) FROM received_items WHERE '+sql,args).fetchone()[0]
        rows=c.execute('SELECT * FROM received_items WHERE '+sql+' ORDER BY created DESC,id LIMIT 60 OFFSET ?',(*args,max(0,int(offset)))).fetchall()
        items=[]
        for row in rows:
            item=dict(row);meta=json.loads(item.pop('meta'));item.update(name=Path(item['path']).name,size=meta['size'],suggestions=[r['id'] for r in rules if _matches(item,r)])
            items.append(item)
        count=c.execute("SELECT count(*) FROM received_items WHERE state='ready'").fetchone()[0]
    return {'items':items,'sources':sources,'rules':[dict(id=r['id'],title=r['title'],uses=r['uses'],**r['data']) for r in rules],
            'total':total,'offset':max(0,int(offset)),'has_more':offset+60<total,'ready':count,'uses_ai':False,
            'observer':HEALTH.get(str(Path(db).absolute()),{'state':'starting'}),
            'scope':'PC 수신 폴더에 저장된 파일을 관찰해요. 메신저 앱에서 아직 내려받지 않은 첨부는 이 목록에 포함되지 않아요.'}

def ensure_worker(db):
    key=str(Path(db).absolute())
    with LOCK:
        if key in WORKERS:return
        _seed(db);WORKERS.add(key)
    def worker():
        while True:
            try:
                scan(db)
                with LOCK,_db(db) as c:
                    rules=[r for r in _rules(c) if r['data']['automatic']]
                    items=[dict(r) for r in c.execute("SELECT * FROM received_items WHERE state='ready'")]
                for item in items:
                    matches=[r for r in rules if _matches(item,r)]
                    if len(matches)==1:
                        try:organize(db,[item['id']],rule_id=matches[0]['id'])
                        except Exception as error:
                            with _db(db) as c:c.execute("UPDATE received_items SET state='error',error=? WHERE id=?",(str(error)[:200],item['id']))
                HEALTH[key]={'state':'watching','last_scan':time.time(),'error':''}
            except Exception as error:
                HEALTH[key]={'state':'error','error':str(error)[:180]}
            time.sleep(8)
    threading.Thread(target=worker,daemon=True,name='received-file-intake').start()
