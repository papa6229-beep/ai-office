"""Conservative review of selected scanned files using the configured Gemma endpoint."""
import ipaddress, json, re
from urllib.parse import urlparse

MAX_FILES=8
MIN_CONFIDENCE=0.65
ALLOWED_EXTENSIONS={'.txt','.md','.rst','.csv','.tsv','.log','.py','.js','.ts','.jsx','.tsx','.html','.css','.json','.xml','.yaml','.yml','.toml','.ini','.cfg','.sql','.java','.kt','.cs','.go','.rs','.c','.h','.cpp','.hpp','.sh','.svg','.docx','.pdf'}

def validate_endpoint(url):
    """Validate the explicitly configured HTTP(S) base without exposing its value."""
    error='Gemma 서버 주소는 인증 정보 없이 올바른 HTTP(S) 주소로 설정하세요.'
    if not isinstance(url,str) or not url or any(char.isspace() or ord(char)<32 for char in url) or '\\' in url or '?' in url or '#' in url:
        raise ValueError(error)
    try:
        parsed=urlparse(url)
        host=parsed.hostname
        if parsed.scheme not in {'http','https'} or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError(error)
        if parsed.port is not None and not 1<=parsed.port<=65535:
            raise ValueError(error)
        if not re.fullmatch(r'(?:\[[0-9a-fA-F:.]+\]|[^:]+)(?::[0-9]+)?',parsed.netloc):
            raise ValueError(error)
        try:ipaddress.ip_address(host)
        except ValueError:
            labels=host.encode('idna').decode('ascii').rstrip('.').split('.')
            if len(host)>253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in labels):
                raise ValueError(error)
    except (ValueError,UnicodeError):
        raise ValueError(error) from None
    return url.rstrip('/')

def analyze(files, settings, reader, model):
    validate_endpoint(settings.get('gemma_url',''))
    if not isinstance(files,list) or not 1<=len(files)<=MAX_FILES:
        raise ValueError(f'분석할 파일을 1~{MAX_FILES}개 선택하세요.')
    documents=[]
    for index,item in enumerate(files,1):
        if item.get('protected') or item.get('extension','').lower() not in ALLOWED_EXTENSIONS:continue
        extracted=reader(item['path'])
        documents.append({'id':str(index),'name':str(item['name'])[:200],'extension':item['extension'],'content':extracted['preview'][:6000]})
    if not documents:raise ValueError('선택한 파일 중 분석 가능한 텍스트·코드·DOCX·PDF가 없습니다.')
    prompt=(
        '다음 파일 내용만 근거로 업무 프로젝트 연관성을 보수적으로 분류하세요. '
        '파일 이동, 삭제, 전송을 제안하거나 실행하지 말고 확신이 낮으면 confidence를 0.65 미만으로 두세요. '
        '입력 파일의 id만 사용하고, JSON 객체만 반환하세요. 형식: '
        '{"items":[{"id":"1","project":"프로젝트 후보 또는 빈 문자열","task":"업무 종류","confidence":0.0,"reason":"짧은 근거"}]}\n'
        +json.dumps(documents,ensure_ascii=False)
    )
    raw=model(prompt)
    try:
        start=raw.find('{');end=raw.rfind('}')
        parsed=json.loads(raw[start:end+1]) if start>=0 and end>start else None
        predictions=parsed.get('items',[]) if isinstance(parsed,dict) else []
    except (TypeError,ValueError):predictions=[]
    by_id={doc['id']:doc for doc in documents};results=[]
    for item in predictions[:MAX_FILES]:
        if not isinstance(item,dict) or str(item.get('id','')) not in by_id:continue
        confidence=item.get('confidence',0)
        if isinstance(confidence,bool) or not isinstance(confidence,(int,float)):confidence=0
        confidence=max(0,min(1,float(confidence)))
        source=by_id[str(item['id'])]
        results.append({'name':source['name'],'project':str(item.get('project',''))[:120],'task':str(item.get('task',''))[:120],'confidence':round(confidence,2),'reason':str(item.get('reason',''))[:300],'state':'검토 후보' if confidence>=MIN_CONFIDENCE else '보류'})
    return {'items':results,'note':'선택한 파일 내용으로 설정된 Gemma 서버가 만든 검토 후보입니다. 자동 분류 확정이나 파일 변경은 하지 않습니다.'}
