"""Bounded read-only preview for text, source, DOCX, and optional PDF files."""
import io, pathlib, re, zipfile, xml.etree.ElementTree as ET

TEXT_EXTENSIONS={'.txt','.md','.rst','.csv','.tsv','.log','.py','.js','.ts','.jsx','.tsx','.html','.css','.json','.xml','.yaml','.yml','.toml','.ini','.cfg','.sql','.java','.kt','.cs','.go','.rs','.c','.h','.cpp','.hpp','.sh','.svg'}
SUPPORTED_EXTENSIONS=TEXT_EXTENSIONS|{'.docx','.pdf'}
MAX_BYTES=8*1024*1024
MAX_CHARS=6000
MAX_ARCHIVE_EXPANDED=12*1024*1024
SECRET_ASSIGNMENT=re.compile(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)["']?\s*[:=]\s*)(["'][^"']*["']|[^\s,;]+)''')
SECRET_XML=re.compile(r'''(?is)(<\s*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)\b[^>]*>).*?(</\s*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)\s*>)''')

def _docx_text(path):
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        if len(infos)>3000 or sum(info.file_size for info in infos)>MAX_ARCHIVE_EXPANDED:
            raise ValueError('문서 내부 데이터가 분석 제한을 넘습니다.')
        info=archive.getinfo('word/document.xml')
        if info.file_size>4*1024*1024:raise ValueError('문서 본문이 분석 제한을 넘습니다.')
        xml=archive.read(info)
    root=ET.fromstring(xml)
    words=[node.text or '' for node in root.iter() if node.tag.endswith('}t')]
    return '\n'.join(words)

def _pdf_text(data):
    try:from pypdf import PdfReader
    except ImportError as exc:raise ValueError('PDF 내용 분석에는 pypdf 설치가 필요합니다.') from exc
    try:
        reader=PdfReader(io.BytesIO(data),strict=False)
        count=len(reader.pages)
        if count>10000:raise ValueError('PDF 페이지 수가 분석 제한을 넘습니다.')
        pages=[]
        for page in reader.pages[:12]:
            try:pages.append(page.extract_text() or '')
            except Exception:pages.append('')
            if sum(map(len,pages))>=MAX_CHARS:break
        extracted='\n'.join(pages)
        return f'PDF 페이지 수: {count}\n'+extracted,count>len(pages) or len(extracted)>MAX_CHARS
    except ValueError:raise
    except Exception as exc:raise ValueError('PDF를 읽을 수 없거나 손상된 파일입니다.') from exc

def analyze_file(raw_path):
    path=pathlib.Path(raw_path).expanduser().resolve(strict=True)
    extension=path.suffix.lower()
    if not path.is_file() or extension not in SUPPORTED_EXTENSIONS:
        raise ValueError('지원하는 텍스트·코드·DOCX·PDF 파일이 아닙니다.')
    if path.name.startswith('.') or path.suffix.lower() in {'.env','.pem','.key'}:
        raise ValueError('숨김 파일이나 비밀정보 파일은 분석하지 않습니다.')
    size=path.stat().st_size
    if size>MAX_BYTES:raise ValueError(f'파일 크기가 제한({MAX_BYTES//1024}KB)을 넘습니다.')
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError(f'파일 크기가 제한({MAX_BYTES//1024}KB)을 넘습니다.')
    truncated=False
    if extension=='.docx':
        try:text=_docx_text(path)
        except (zipfile.BadZipFile,KeyError,ET.ParseError) as exc:raise ValueError('DOCX를 읽을 수 없거나 손상된 파일입니다.') from exc
    elif extension=='.pdf':text,truncated=_pdf_text(data)
    else:
        if b'\0' in data:raise ValueError('텍스트 파일로 확인되지 않습니다.')
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:text=data.decode('cp949')
            except UnicodeDecodeError as exc:raise ValueError('UTF-8 또는 한국어 텍스트로 읽을 수 없습니다.') from exc
    safe_text=SECRET_ASSIGNMENT.sub(r'\1[가림]',text)
    safe_text=SECRET_XML.sub(r'\1[가림]\2',safe_text)
    safe_text=re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [가림]', safe_text)
    preview=safe_text[:MAX_CHARS]
    lines=[line.strip() for line in preview.splitlines() if line.strip()]
    return {'name':path.name,'extension':extension,'bytes':size,'characters':len(text),'truncated':truncated or len(text)>len(preview),'preview':preview,'signals':lines[:8]}
