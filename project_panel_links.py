"""Safe chat links and local file references actually emitted in a Codex chat.

The browser receives opaque references, never an arbitrary-path download API.
HTML and executable link schemes are displayed as text, not evaluated.
"""
from collections import OrderedDict
from contextlib import contextmanager
import ctypes
import hashlib
import hmac
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import threading
from urllib.parse import quote, unquote, urlsplit

_KEY=secrets.token_bytes(32)
_FILES=OrderedDict()
_LOCK=threading.RLock()
_MAX_REFS=4096
_PRIVATE_DIRS={'.ssh','.aws','.git','.provider_runtime'}
_PRIVATE_FILES={'office.db','settings.json','credentials.json','oauth_creds.json'}
_TOKENS=re.compile(r'!?\[([^\]\n]{0,500})\]\(|https?://[^\s<>`]+|/?[A-Za-z]:[/\\][^\s<>`"\']+')
VIDEO_TYPES={'.mp4':'video/mp4','.m4v':'video/mp4','.webm':'video/webm','.ogv':'video/ogg','.mov':'video/quicktime'}


def _web(destination):
    if re.search(r'[\x00-\x20\x7f]',destination):return None
    try:
        value=urlsplit(destination)
        if value.scheme.lower() in {'http','https'} and value.hostname and not value.username and not value.password:
            return destination
        if not value.scheme and not value.netloc and value.path.startswith('/api/work/'):
            return destination
    except ValueError:
        pass
    return None


def _local(destination):
    raw=destination
    if raw.startswith('file:'):
        value=urlsplit(raw)
        if value.netloc not in {'','localhost'}:return None
        raw=value.path
    raw=unquote(raw)
    raw=re.sub(r'(?i)#L\d+(?:C\d+)?(?:-L?\d+(?:C\d+)?)?$','',raw)
    raw=re.sub(r':\d+(?::\d+)?$','',raw)
    if re.match(r'^/[A-Za-z]:[/\\]',raw):raw=raw[1:]
    if not re.match(r'^[A-Za-z]:[/\\]',raw) or re.search(r'[\x00-\x1f\x7f]',raw):return None
    parts=raw.replace('\\','/').split('/')
    if any(p in {'.','..'} for p in parts) or ':' in raw[2:]:return None
    if any(p.lower() in _PRIVATE_DIRS for p in parts):return None
    name=parts[-1].lower()
    if name in _PRIVATE_FILES or name.startswith('.env') or Path(name).suffix in {'.pem','.key','.pfx','.p12'}:return None
    return Path(raw)


def _file_ref(destination,thread_id,host,message_id):
    if host!='local':return None
    try:path=_local(destination)
    except ValueError:return None
    if path is None:return None
    payload='\0'.join((thread_id,host,message_id,str(path)))
    ident=hmac.new(_KEY,payload.encode('utf-8'),hashlib.sha256).hexdigest()[:32]
    with _LOCK:
        _FILES[ident]=str(path)
        _FILES.move_to_end(ident)
        while len(_FILES)>_MAX_REFS:_FILES.popitem(last=False)
    return '/api/work/project-panel-file?ref='+ident


def _destination(text,start):
    """Read an inline destination, including angle-wrapped paths and parentheses."""
    pos=start
    while pos<len(text) and text[pos] in ' \t':pos+=1
    if pos<len(text) and text[pos]=='<':
        end=text.find('>',pos+1)
        if end<0:return None
        close=end+1
        while close<len(text) and text[close] in ' \t':close+=1
        if close<len(text) and text[close]==')':return text[pos+1:end],close+1
        return None
    depth=1
    end=pos
    while end<len(text) and text[end] not in '\r\n':
        if text[end]=='\\' and end+1<len(text) and text[end+1] in '()':
            end+=2;continue
        if text[end]=='(':depth+=1
        if text[end]==')':
            depth-=1
            if depth==0:
                raw=text[pos:end].strip()
                return raw.replace('\\(', '(').replace('\\)',')'),end+1
        end+=1
    return None


def parts(text,thread_id,host,message_id,allow_files=False):
    """Produce DOM-safe inline parts; preserve plain text and fenced/inline code."""
    result=[]
    def plain(value,kind='text'):
        if not value:return
        if result and result[-1]['kind']==kind:result[-1]['text']+=value
        else:result.append({'kind':kind,'text':value})
    def chunk(value):
        pos=0
        while pos<len(value):
            match=_TOKENS.search(value,pos)
            if not match:plain(value[pos:]);break
            plain(value[pos:match.start()])
            image=match.group(0).startswith('![')
            if match.group(1) is not None:
                parsed=_destination(value,match.end())
                if not parsed:
                    plain(value[match.start():match.end()]);pos=match.end();continue
                destination,end=parsed
                label=re.sub(r'\\([\[\]\\])',r'\1',match.group(1)) or destination
            else:
                destination=match.group(0).rstrip('.,;!?')
                while destination.endswith(')') and destination.count(')')>destination.count('('):destination=destination[:-1]
                end=match.start()+len(destination);label=destination
            href=_web(destination)
            try:
                local=_local(destination)
                extension=(local.suffix if local else Path(urlsplit(destination).path).suffix).lower()
            except ValueError:extension=''
            video=extension in VIDEO_TYPES
            image=not video and (image or extension in {'.png','.jpg','.jpeg','.gif','.webp','.ico','.bmp'})
            if extension in {'.html','.htm','.xhtml'}:image=False
            if href:
                result.append({'kind':'link','text':label,'href':href,'image':image,'video':video})
            else:
                href=_file_ref(destination,thread_id,host,message_id) if allow_files else None
                if href:result.append({'kind':'file','text':label,'href':href,'download':href+'&download=1','path':destination,'image':image,'video':video})
                else:plain(value[match.start():end])
            pos=end
    for index,piece in enumerate(re.split(r'(```[\s\S]*?(?:```|$)|`[^`\n]*`)',str(text))):
        if index%2:plain(piece,'code')
        else:chunk(piece)
    return result


def _regular(path):
    if os.name!='nt' or ctypes.windll.kernel32.GetDriveTypeW(str(path.drive)+'\\') not in {2,3,6}:
        raise ValueError('현재 PC의 로컬 파일만 열 수 있어요.')
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('연결된 경로 대신 실제 파일을 확인해 주세요.')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('다운로드할 파일이 아니에요.')
    return info


def folder_target(identifier):
    """Resolve only an opaque local file reference already emitted in chat."""
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):
        raise ValueError('대화의 폴더 열기 버튼을 다시 선택해 주세요.')
    with _LOCK:raw=_FILES.get(identifier)
    if not raw:raise ValueError('파일 링크를 갱신하려면 대화를 다시 열어 주세요.')
    path=_local(raw)
    if path is None:raise ValueError('이 파일 위치는 열 수 없어요.')
    try:_regular(path)
    except OSError:raise ValueError('파일이 이동되었거나 저장 위치를 확인할 수 없어요.') from None
    return path


def reveal_folder(identifier):
    path=folder_target(identifier)
    # Reuse the existing Shell item-ID route; never execute the linked file.
    import file_history
    file_history._show_in_explorer(path)
    return {'dispatched':True,'mode':'code','model_calls':0,
            'message':'탐색기에 파일이 있는 폴더를 열도록 요청했어요.'}


@contextmanager
def open_file(identifier):
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('대화에서 파일 링크를 다시 선택해 주세요.')
    with _LOCK:raw=_FILES.get(identifier)
    if not raw:raise ValueError('파일 링크를 갱신하려면 대화를 다시 열어 주세요.')
    path=Path(raw);before=_regular(path)
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns):
            raise ValueError('파일이 변경됐어요. 링크를 다시 선택해 주세요.')
        _regular(path)
        extension=path.suffix.lower()
        preview=extension in VIDEO_TYPES or extension in {'.png','.jpg','.jpeg','.gif','.webp','.pdf'}
        mime=VIDEO_TYPES.get(extension) or ((mimetypes.guess_type(path.name)[0] or 'application/octet-stream') if preview else 'application/octet-stream')
        yield stream,{'name':path.name,'size':opened.st_size,'mime':mime,'preview':preview}


def serve(handler,identifier,download=False):
    started=False
    try:
        with open_file(identifier) as (stream,meta):
            disposition='inline' if meta['preview'] and not download else 'attachment'
            size=meta['size'];start=0;end=size-1;status=200
            requested=handler.headers.get('Range')
            if requested:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
                valid=bool(match and any(match.groups()))
                if valid:
                    left,right=match.groups()
                    if left:start=int(left);end=min(int(right),size-1) if right else size-1
                    else:start=max(0,size-int(right));end=size-1
                    valid=0<=start<=end<size
                if not valid:
                    handler.send_response(416);handler.send_header('Content-Range','bytes */'+str(size));handler.send_header('Content-Length','0');handler.end_headers();return
                status=206
            handler.send_response(status)
            handler.send_header('Content-Type',meta['mime'])
            handler.send_header('Content-Length',str(end-start+1))
            handler.send_header('Accept-Ranges','bytes')
            if status==206:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            handler.send_header('Content-Disposition',disposition+"; filename*=UTF-8''"+quote(meta['name']))
            handler.send_header('X-Content-Type-Options','nosniff')
            handler.send_header('X-Frame-Options','DENY')
            handler.send_header('Content-Security-Policy',"sandbox; default-src 'none'")
            handler.send_header('Cache-Control','private, no-store')
            handler.end_headers();started=True;stream.seek(start);remaining=end-start+1
            while remaining:
                data=stream.read(min(65536,remaining))
                if not data:break
                handler.wfile.write(data);remaining-=len(data)
    except (BrokenPipeError,ConnectionResetError):
        return
    except (OSError,ValueError):
        if started:handler.close_connection=True;return
        handler.reply({'error':'파일이 없거나 현재 PC에서 열 수 없어요. 원래 Codex에서 파일 위치를 확인해 주세요.'},404)
