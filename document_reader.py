"""Read-only document discovery and bounded extraction from approved local sources."""
import contextlib
import hashlib
import io
import os
import pathlib
import re
import sqlite3
import stat
import zipfile
import xml.etree.ElementTree as ET

import content_analysis
import desktop_workspace
import scanner

MAX_FILES = 500
MAX_DIRS = 100
MAX_ENTRIES = 2000
MAX_DEPTH = 3
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 120000
MAX_EXCERPT = 12000
MAX_PAGES = 60
SUPPORTED_EXTENSIONS = content_analysis.SUPPORTED_EXTENSIONS | {'.hwpx'}
_PROTECTED = re.compile(r'(?i)(?:^|[._\- ])(?:settings|config|configuration|credentials?|passwords?|secrets?|tokens?|keys?|auth|api[_-]?keys?|private[_-]?keys?)(?:[._\- ]|$)')
_PRIVATE_KEY = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)', re.S)


class DocumentError(ValueError):
    """A public, sanitized read failure."""


def _safe(path):
    path = pathlib.Path(path).absolute()
    if any(part in {'.', '..'} for part in path.parts):
        raise DocumentError('상대 경로나 변경된 경로는 읽을 수 없습니다.')
    for part in (path, *path.parents):
        if part.name.startswith('.') or part.name.casefold() in scanner.EXCLUDE_NAMES:
            raise DocumentError('숨김·시스템·보호 경로는 읽지 않습니다.')
        if _PROTECTED.search(part.name):
            raise DocumentError('설정·인증정보 파일은 읽지 않습니다.')
        info = part.lstat()
        if part != pathlib.Path(part.anchor) and getattr(info, 'st_file_attributes', 0) & 6:
            raise DocumentError('숨김·시스템 파일은 읽지 않습니다.')
    if not desktop_workspace._safe_route(path):
        raise DocumentError('링크·재분석 경로는 읽지 않습니다.')
    info = path.lstat()
    if stat.S_ISREG(info.st_mode) and (info.st_nlink != 1 or info.st_size > MAX_BYTES):
        raise DocumentError('하드링크 또는 크기 제한을 넘는 파일은 읽지 않습니다.')
    return path, info


def catalog(db_path, workspace=None):
    """Private paths are for the server registry only; callers expose opaque IDs."""
    roots = [(path, '바탕화면') for path in desktop_workspace._desktop_roots()]
    if workspace:
        roots.extend((path, '작업 폴더') for path in (workspace if isinstance(workspace, (tuple, list)) else [workspace]))
    warnings = []
    if db_path and pathlib.Path(db_path).is_file():
        try:
            uri = pathlib.Path(db_path).absolute().as_uri() + '?mode=ro'
            with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
                rows = connection.execute('SELECT path FROM work_resources LIMIT 600').fetchall()
            roots.extend((row[0], '업무 연결 자료') for row in rows if row[0])
        except (sqlite3.Error, OSError, ValueError):
            warnings.append('업무 연결 자료 목록 일부를 확인하지 못했습니다.')
    with scanner.LOCK:
        approved = [(item.get('path'), 'PC 조사 자료') for item in scanner.FILES[:MAX_ENTRIES] if item.get('path') and not item.get('protected')]
        scanner_truncated = len(scanner.FILES) > MAX_ENTRIES
    # Scanner file entries are individual approvals, not permission to scan siblings.
    queue = [(path, source, 0) for path, source in roots[:MAX_ENTRIES]]
    queue.extend((path, source, MAX_DEPTH) for path, source in approved)
    items, seen, visited = [], set(), set()
    entries = directories = 0
    truncated = scanner_truncated or len(roots) > MAX_ENTRIES
    while queue:
        if len(items) >= MAX_FILES or entries >= MAX_ENTRIES or directories >= MAX_DIRS:
            truncated = True
            break
        supplied, source, depth = queue.pop(0)
        entries += 1
        try:
            path, info = _safe(supplied)
            canonical = os.path.normcase(str(path))
            if canonical in visited:
                continue
            visited.add(canonical)
            if stat.S_ISDIR(info.st_mode):
                directories += 1
                if depth > MAX_DEPTH:
                    truncated = True
                    continue
                with os.scandir(path) as children:
                    for child in children:
                        if entries + len(queue) >= MAX_ENTRIES:
                            truncated = True
                            break
                        queue.append((child.path, source, depth + 1))
            elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in SUPPORTED_EXTENSIONS and canonical not in seen:
                seen.add(canonical)
                items.append({'id': hashlib.sha256(canonical.encode('utf-8')).hexdigest(), 'title': path.name,
                              'name': path.name, 'path': str(path), 'extension': path.suffix.lower(), 'source': source})
        except (OSError, ValueError, TypeError):
            continue
    return {'items': items, 'total': len(items), 'truncated': truncated, 'warnings': warnings}


def _xml_text(data, hwpx=False):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise DocumentError('문서 XML 형식을 안전하게 읽을 수 없습니다.')
    root = ET.fromstring(data)
    namespace = 'http://www.hancom.co.kr/hwpml/2011/paragraph' if hwpx else 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    paragraphs = []
    for paragraph in root.iter('{' + namespace + '}p'):
        paragraphs.append(''.join(node.text or '' for node in paragraph.iter('{' + namespace + '}t')))
    return '\n'.join(paragraphs)


def _archive_text(data, hwpx=False):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > 3000 or sum(item.file_size for item in infos) > content_analysis.MAX_ARCHIVE_EXPANDED:
            raise DocumentError('문서 내부 데이터가 분석 제한을 넘습니다.')
        names = sorted(name for name in archive.namelist() if re.fullmatch(r'Contents/section\d+\.xml', name)) if hwpx else ['word/document.xml']
        if not names:
            raise DocumentError('문서 본문을 찾을 수 없습니다.')
        parts = []
        for name in names:
            info = archive.getinfo(name)
            if info.file_size > 4 * 1024 * 1024 or info.flag_bits & 1:
                raise DocumentError('문서 본문이 제한을 넘거나 암호화되었습니다.')
            parts.append(_xml_text(archive.read(info), hwpx))
        return '\n'.join(parts)


def _redact(text):
    text = _PRIVATE_KEY.sub('[비밀키 가림]', text)
    text = content_analysis.SECRET_ASSIGNMENT.sub(r'\1[가림]', text)
    text = content_analysis.SECRET_XML.sub(r'\1[가림]\2', text)
    return re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [가림]', text)


def read(path, query='', offset=0):
    """Read a freshly validated path previously selected by the server catalog."""
    try:
        path, info = _safe(path)
        extension = path.suffix.lower()
        if not stat.S_ISREG(info.st_mode) or extension not in SUPPORTED_EXTENSIONS:
            raise DocumentError('지원하는 텍스트·코드·DOCX·PDF·HWPX 파일이 아닙니다.')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_nlink != 1:
                raise DocumentError('파일이 변경되었습니다. 다시 선택하세요.')
            data = stream.read(MAX_BYTES + 1)
        _safe(path)
        if len(data) > MAX_BYTES:
            raise DocumentError('파일 크기가 8MB 분석 제한을 넘습니다.')
        pages_read = page_count = None
        extraction_truncated = False
        if extension in {'.docx', '.hwpx'}:
            text = _archive_text(data, extension == '.hwpx')
        elif extension == '.pdf':
            if not data.startswith(b'%PDF-'):
                raise DocumentError('PDF 형식을 확인할 수 없습니다.')
            try:
                from pypdf import PdfReader
            except ImportError as error:
                raise DocumentError('PDF 분석 모듈을 설치해야 합니다.') from error
            document = PdfReader(io.BytesIO(data), strict=False)
            if document.is_encrypted:
                raise DocumentError('암호화된 PDF는 읽지 않습니다.')
            page_count = len(document.pages)
            if page_count > 10000:
                raise DocumentError('PDF 페이지 수가 제한을 넘습니다.')
            parts, count, pages_read = [], 0, 0
            for page in document.pages[:MAX_PAGES]:
                part = page.extract_text() or ''
                parts.append(part)
                pages_read += 1
                count += len(part) + 1
                if count > MAX_TEXT:
                    break
            text = '\n'.join(parts)
            extraction_truncated = pages_read < page_count
        else:
            if b'\0' in data:
                raise DocumentError('텍스트 파일로 확인되지 않습니다.')
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = data.decode('cp949')
    except DocumentError:
        raise
    except Exception as error:
        raise DocumentError('문서에 접근할 수 없거나 지원하지 않는 문서 형식입니다.') from error
    # Redact the entire bounded extraction before clipping, so partial secrets do not escape.
    safe = _redact(text)
    extraction_truncated = extraction_truncated or len(safe) > MAX_TEXT
    safe = safe[:MAX_TEXT]
    offset = max(0, min(int(offset), len(safe)))
    query = str(query or '').strip()[:300]
    matches = []
    match_count = 0
    if query:
        # Keep original offsets while full case folding may expand Unicode characters.
        folded_parts, positions = [], []
        for position, character in enumerate(safe):
            folded = character.casefold()
            folded_parts.append(folded)
            positions.extend([position] * len(folded))
        folded_text, folded_query, cursor = ''.join(folded_parts), query.casefold(), 0
        while folded_query:
            found = folded_text.find(folded_query, cursor)
            if found < 0:
                break
            match_start = positions[found]
            match_end = positions[found + len(folded_query) - 1] + 1
            cursor = found + len(folded_query)
            match_count += 1
            if len(matches) < 20:
                start, end = max(0, match_start - 220), min(len(safe), match_end + 400)
                matches.append({'text': safe[start:end], 'position': match_start, 'line': safe.count('\n', 0, match_start) + 1})
    excerpt = safe[offset:offset + MAX_EXCERPT]
    next_offset = offset + len(excerpt) if offset + len(excerpt) < len(safe) else None
    empty_pdf = extension == '.pdf' and not safe.strip()
    complete = not extraction_truncated and not empty_pdf
    note = '읽은 범위에서만 검색한 결과입니다.' if query else '읽은 본문 범위입니다.'
    if extraction_truncated:
        note += ' 분석 한도로 읽지 않은 내용이 있습니다.'
    if empty_pdf:
        note += ' 추출된 본문이 없습니다. 스캔 이미지 또는 빈 PDF일 수 있으며 OCR은 수행하지 않았습니다.'
    return {'name': path.name, 'extension': extension, 'text': excerpt, 'total_characters': len(safe),
            'query': query, 'matches': matches, 'match_count': match_count, 'matches_truncated': match_count > len(matches),
            'offset': offset, 'next_offset': next_offset, 'truncated': extraction_truncated or next_offset is not None,
            'search_complete': complete, 'pages_read': pages_read, 'page_count': page_count,
            'scanned_or_empty': empty_pdf, 'note': note}
