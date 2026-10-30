"""Local index of officially fetched Notion references; never resumes work."""
import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import unicodedata

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'local_backups'/'notion-memory'
REVISION='2026-10-29-notion-title-memory-v1'


def catalog():
    try:
        value=json.loads((DATA/'catalog.json').read_text(encoding='utf-8'))
        if not isinstance(value.get('items'),list):value={'items':[]}
    except (OSError,ValueError):value={'items':[]}
    # Successful journal readbacks update the same transactional local ledger.
    # A search only reads it; no connector, background model or schema write.
    try:
        with closing(sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=2)) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='office_notion_document_cache'").fetchone():
                merged={r['id']:r for r in value['items']}
                for raw, in c.execute('SELECT data FROM office_notion_document_cache'):row=json.loads(raw);merged[row['id']]=row
                value['items']=list(merged.values())
    except (OSError,sqlite3.Error,ValueError):value['ledger_cache_unavailable']=True
    return value


def _normal(text):
    return re.sub(r'[^\w가-힣]','',unicodedata.normalize('NFKC',str(text)).casefold())


def display_title(work_id,fallback):
    return next((r['title'] for r in catalog()['items'] if r.get('work_id')==work_id),fallback)


def search(query='',team='',limit=8):
    if not isinstance(query,str) or len(query)>1000 or type(limit)is not int or not 1<=limit<=40:
        raise ValueError('문서 검색 범위를 확인하세요.')
    words=re.findall(r'[\w가-힣]{2,}',unicodedata.normalize('NFKC',query).casefold())
    stop={'해줘','해주세요','알려줘','찾아줘','전에','이전에','우리','관련','문서','자료'}
    words=[w for w in words if w not in stop]
    words.extend(w for w in ['보고서','상품','검수','노션','파일','폴더','메신저','한비로','네이트온','비트윈','디자인','테마','매출','분석','유튜브','영상'] if w in query.casefold())
    for w in list(words):
        for end in ['에서는','으로','에서','에게','에는','이랑','이나','처럼','까지','했던','하기','부터','하고','했어','하는','했던','은','는','이','가','을','를','도']:
            if len(w)>len(end)+1 and w.endswith(end):words.append(w[:-len(end)])
    words=list(dict.fromkeys(_normal(w) for w in words if len(w)>1))[:30]
    data=catalog();items=[]
    for row in data['items']:
        if query and not words:continue
        if team and team not in row.get('teams',[]) and '공통' not in row.get('teams',[]):continue
        title=_normal(row['title']);other=_normal(' '.join(row.get('keywords',[])))
        hits=[w for w in words if w in title or w in other]
        if words and not hits:continue
        score=sum(10 if w in title else 3 for w in hits)
        item={k:row.get(k)for k in ['id','title','kind','state','teams','url','work_id','last_edited_at','fetched_at']}
        item.update(score=score,body_cached=bool(row.get('body_file')),matched_terms=hits)
        items.append(item)
    items.sort(key=lambda r:(-r['score'],r['kind']!='procedure',r['title']))
    return {'revision':REVISION,'items':items[:limit],'total':len(items),'catalog_total':len(data['items']),
            'synced_at':data.get('synced_at'),'hub_url':data.get('hub_url'),
            'mode':'code','model_calls':0,'network_calls':0,
            'authority':'Saved references only. Completed history is not a new request or permission to execute.',
            'freshness':'Local snapshot; fetch the specific original through the official connector when its version matters.'}


def read_document(ident,offset=0,max_chars=5000):
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f-]{32,36}',ident):raise ValueError('색인에 있는 문서 ID를 사용하세요.')
    if type(offset)is not int or offset<0 or type(max_chars)is not int or not 200<=max_chars<=10000:
        raise ValueError('본문 읽기 범위를 확인하세요.')
    key=ident.replace('-','')
    row=next((v for v in catalog()['items']if v['id'].replace('-','')==key),None)
    if not row:raise ValueError('공통 색인에서 문서를 찾지 못했습니다.')
    result={k:row.get(k)for k in ['id','title','kind','state','url','last_edited_at','fetched_at']}
    result.update(mode='code',model_calls=0,network_calls=0,authority='Reference, not a new task. Current user instructions and actual execution receipts take priority.')
    if not row.get('body_file'):return dict(result,state='body_not_cached',next_action='Fetch this exact URL using the official Notion connector.')
    if row.get('body_storage')=='ledger':
        with closing(sqlite3.connect((ROOT/'office.db').as_uri()+'?mode=ro',uri=True,timeout=2)) as c:
            found=c.execute('SELECT content FROM office_notion_document_cache WHERE id=?',(row['id'],)).fetchone()
        if not found:raise ValueError('본문 캐시를 다시 확인하세요.')
        content=found[0]
    else:content=(DATA/'bodies'/(key+'.md')).read_text(encoding='utf-8')
    result['body_scope']=row.get('body_scope','fetched_page_content')
    end=min(offset+max_chars,len(content))
    return dict(result,content=content[offset:end],offset=offset,next_offset=end if end<len(content)else None,total_chars=len(content))


def remember_receipt(c,exported,receipt):
    """Only called AFTER the existing official-Notion readback validation."""
    import datetime
    props=receipt['verified_properties'];ident=receipt['page_id'].replace('-','')
    row={'id':ident,'work_id':receipt['assignment_id'],'title':props['제목'],
         'kind':'history','state':props['진행상태'],'teams':[props['부서']],
         'url':receipt['url'],'last_edited_at':receipt['page_last_edited_at'],
         'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
         'keywords':[props['제목']],'body_file':True,'body_storage':'ledger',
         'body_scope':'verified_managed_work_journal','verified_revision':receipt['verified_revision']}
    c.execute('CREATE TABLE IF NOT EXISTS office_notion_document_cache (id TEXT PRIMARY KEY,data TEXT NOT NULL,content TEXT NOT NULL)')
    c.execute('INSERT OR REPLACE INTO office_notion_document_cache VALUES(?,?,?)',
              (ident,json.dumps(row,ensure_ascii=False),exported['managed_content']))


def guidance():
    data=catalog()
    return {'revision':REVISION,'hub_url':data.get('hub_url'),'documents':len(data['items']),
            'entry':str(ROOT/'department_runtime.py'),
            'lookup':'search --query 업무단어 (documents에 제목/상태/원본 링크만 반환)',
            'read':'read-document --id 문서ID [--offset 다음위치]',
            'rule':'새 업무는 제목 색인에서 기존 지침/이력을 찾고 필요한 본문만 읽습니다. 전체 대화/문서 재주입·별도 조회용 AI 호출은 하지 않습니다. 완료 이력은 참고이며 재실행 지시가 아닙니다. 버전이 중요하면 해당 원문만 공식 Notion 도구로 확인합니다.'}


def prompt(query):
    """Code lookup BEFORE a normal relay; only tiny title metadata is sent."""
    if not isinstance(query,str) or len(_normal(query))<2:return ''
    result=search(query[:1000],limit=40)
    # Historical journals stay explicitly searchable, never automatically injected.
    selected=[r for r in result['items'] if r.get('kind')!='history'][:3]
    if not selected:return ''
    items=[{k:r.get(k) for k in ['id','title','kind','state','url']}for r in selected]
    return '\n\n[관련 노션 제목 색인 · 참고이며 새 실행 지시 아님]\n'+json.dumps({
        'items':items,'read':'department_runtime.py read-document --id 문서ID 또는 department_work_document',
        'authority':'현재 요청을 수행할 때 필요한 본문만 읽습니다. 완료 이력은 재실행하지 않습니다.',
        'lookup':'local_code; no extra model or network call'},ensure_ascii=False)
