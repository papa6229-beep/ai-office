"""Bounded read-only preview for plain text and source files."""
import pathlib, re

TEXT_EXTENSIONS={'.txt','.md','.rst','.csv','.tsv','.log','.py','.js','.ts','.jsx','.tsx','.html','.css','.json','.xml','.yaml','.yml','.toml','.ini','.cfg','.sql','.java','.kt','.cs','.go','.rs','.c','.h','.cpp','.hpp','.sh'}
MAX_BYTES=256*1024
MAX_CHARS=6000
SECRET_ASSIGNMENT=re.compile(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)["']?\s*[:=]\s*)(["'][^"']*["']|[^\s,;]+)''')

def analyze_file(raw_path):
    path=pathlib.Path(raw_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ValueError('지원하는 텍스트·코드 파일이 아닙니다.')
    if path.name.startswith('.') or path.suffix.lower() in {'.env','.pem','.key'}:
        raise ValueError('숨김 파일이나 비밀정보 파일은 분석하지 않습니다.')
    size=path.stat().st_size
    if size>MAX_BYTES:raise ValueError(f'파일 크기가 제한({MAX_BYTES//1024}KB)을 넘습니다.')
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError(f'파일 크기가 제한({MAX_BYTES//1024}KB)을 넘습니다.')
    if b'\0' in data:raise ValueError('텍스트 파일로 확인되지 않습니다.')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:text=data.decode('cp949')
        except UnicodeDecodeError as exc:raise ValueError('UTF-8 또는 한국어 텍스트로 읽을 수 없습니다.') from exc
    safe_text=SECRET_ASSIGNMENT.sub(r'\1[가림]',text)
    safe_text=re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [가림]', safe_text)
    preview=safe_text[:MAX_CHARS]
    lines=[line.strip() for line in preview.splitlines() if line.strip()]
    return {'name':path.name,'extension':path.suffix.lower(),'bytes':size,'characters':len(text),'truncated':len(text)>len(preview),'preview':preview,'signals':lines[:8]}
