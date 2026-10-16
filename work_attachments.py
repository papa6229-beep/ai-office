"""Immutable, bounded chat uploads. Filenames/content are untrusted source material."""
import base64
import contextlib
import hashlib
import io
import json
import os
import pathlib
import re
import sqlite3
import stat
import time
import urllib.parse
import uuid
import zipfile

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 6
MAX_TOTAL = 30 * 1024 * 1024
IMAGE_TYPES = {'.gif':'image/gif','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.ico':'image/x-icon'}
TEXT_TYPES = {'.txt','.md','.csv','.tsv','.json','.py','.js','.ts','.jsx','.tsx','.html','.css','.xml','.yaml','.yml','.sql','.log','.rst','.toml'}
OTHER_TYPES = {'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.zip':'application/zip'}


def initialize(c):
    c.execute('''CREATE TABLE IF NOT EXISTS chat_attachments (
        id TEXT PRIMARY KEY,request_id TEXT NOT NULL,message_id TEXT,
        name TEXT NOT NULL,extension TEXT NOT NULL,size INTEGER NOT NULL,
        mime TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT NOT NULL,
        details TEXT NOT NULL,created REAL NOT NULL)''')


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):
        raise ValueError('첨부 자료 ID가 올바르지 않습니다.')
    return value


def _root(db):
    root=pathlib.Path(db).absolute().parent/'local_backups'/'chat-files'
    root.parent.mkdir(exist_ok=True)
    parent_info=root.parent.lstat()
    if root.parent.is_symlink() or getattr(parent_info,'st_file_attributes',0)&1024:
        raise ValueError('첨부 보관 경로를 확인할 수 없습니다.')
    root.mkdir(exist_ok=True)
    for node in (root,root.parent):
        info=node.lstat()
        if node.is_symlink() or getattr(info,'st_file_attributes',0)&1024:
            raise ValueError('첨부 보관 경로를 확인할 수 없습니다.')
    return root


def _path(db,row):
    path=_root(db)/(_id(row['id'])+row['extension'])
    info=path.lstat()
    if path.is_symlink() or getattr(info,'st_file_attributes',0)&1024 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('첨부 원본의 신원을 확인할 수 없습니다.')
    if info.st_size!=row['size']: raise ValueError('첨부 원본이 변경되었습니다.')
    return path


def read_bytes(db,row):
    path=_path(db,row)
    before=path.stat()
    with path.open('rb') as f:
        actual=os.fstat(f.fileno())
        if (before.st_dev,before.st_ino)!=(actual.st_dev,actual.st_ino) or actual.st_nlink!=1:
            raise ValueError('첨부 원본이 변경되었습니다.')
        data=f.read(MAX_BYTES+1)
    _path(db,row)
    if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
        raise ValueError('첨부 원본이 변경되었습니다.')
    return data


def _inspect(data,extension):
    if extension in IMAGE_TYPES:
        from PIL import Image
        formats={'.gif':'GIF','.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG','.webp':'WEBP','.ico':'ICO'}
        with Image.open(io.BytesIO(data)) as image:
            if image.format!=formats[extension]: raise ValueError('이미지 확장자와 실제 형식이 다릅니다.')
            count=getattr(image,'n_frames',1)
            if count>400 or image.width*image.height*count>64000000 or image.width*image.height>16000000:
                raise ValueError('이미지는 400프레임·총 6400만 화소 이하로 올려주세요.')
            width,height=image.size
            for frame in range(count):
                image.seek(frame)
                image.load()
        return IMAGE_TYPES[extension],'image',{'width':width,'height':height,'frames':count,'animated':count>1}
    if extension=='.pdf':
        if not data.startswith(b'%PDF-'): raise ValueError('PDF 형식이 아닙니다.')
    elif extension in {'.docx','.zip'}:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos=archive.infolist()
            if len(infos)>3000 or sum(item.file_size for item in infos)>64*1024*1024:
                raise ValueError('압축 자료 내부 데이터가 제한을 넘습니다.')
            if extension=='.docx' and 'word/document.xml' not in archive.namelist(): raise ValueError('DOCX 문서 형식이 아닙니다.')
            for item in infos:
                if item.flag_bits&1: raise ValueError('암호화된 압축 자료는 지원하지 않습니다.')
                name=item.filename.replace('\\','/')
                if name.startswith('/') or '..' in pathlib.PurePosixPath(name).parts or ':' in name:
                    raise ValueError('안전하지 않은 압축 자료 경로입니다.')
        return OTHER_TYPES[extension], 'archive' if extension=='.zip' else 'document', {'entries':len(infos)}
    elif extension in TEXT_TYPES:
        if b'\0' in data: raise ValueError('텍스트 형식으로 확인되지 않습니다.')
        try: data.decode('utf-8-sig')
        except UnicodeDecodeError: data.decode('cp949')
        return 'text/plain','text',{}
    else: raise ValueError('지원하지 않는 첨부 형식입니다.')
    return OTHER_TYPES[extension],'document',{}


def public(row):
    result={key:row[key] for key in ('id','name','size','mime','kind','sha256')}
    result.update(json.loads(row['details']))
    result['download_url']='/api/work/attachment?id='+row['id']+'&download=1'
    result['preview_url']='/api/work/attachment?id='+row['id'] if row['kind']=='image' else None
    return result


def upload(db,name,data_url,upload_id=None,request_id=None):
    identifier=_id(upload_id or uuid.uuid4().hex)
    request_id=_id(request_id)
    if not isinstance(name,str) or not name.strip() or len(name)>240 or any(ord(c)<32 for c in name):
        raise ValueError('파일 이름을 확인해주세요.')
    name=pathlib.PurePosixPath(name.replace('\\','/')).name
    extension=pathlib.Path(name).suffix.lower()
    if extension not in IMAGE_TYPES and extension not in TEXT_TYPES and extension not in OTHER_TYPES:
        raise ValueError('지원하지 않는 첨부 형식입니다.')
    if not isinstance(data_url,str) or len(data_url)>((MAX_BYTES+2)//3)*4+256:
        raise ValueError('첨부 파일은 하나당 10MB 이하입니다.')
    match=re.fullmatch(r'data:[^;,]{0,150};base64,([A-Za-z0-9+/=]*)',data_url)
    if not match: raise ValueError('첨부 파일 데이터가 올바르지 않습니다.')
    try: data=base64.b64decode(match.group(1),validate=True)
    except Exception as error: raise ValueError('첨부 파일 데이터를 읽을 수 없습니다.') from error
    if not 0<len(data)<=MAX_BYTES: raise ValueError('첨부 파일은 하나당 10MB 이하입니다.')
    digest=hashlib.sha256(data).hexdigest()
    mime,kind,details=_inspect(data,extension)
    if kind in {'text','document'} and len(data)>8*1024*1024:
        raise ValueError('문서·코드 첨부는 분석 가능한 8MB 이하로 올려주세요. 이미지·ZIP은 10MB까지 가능합니다.')
    with contextlib.closing(sqlite3.connect(str(db),timeout=15)) as c,c:
        c.row_factory=sqlite3.Row
        initialize(c)
        c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT * FROM chat_attachments WHERE id=?',(identifier,)).fetchone()
        if existing:
            if existing['request_id']!=request_id or existing['sha256']!=digest or existing['name']!=name:
                raise ValueError('이 첨부 ID는 다른 자료에 사용되었습니다.')
            read_bytes(db,existing)
            return public(existing)
        count,total=c.execute('SELECT count(*),coalesce(sum(size),0) FROM chat_attachments WHERE request_id=?',(request_id,)).fetchone()
        if count>=MAX_FILES or total+len(data)>MAX_TOTAL: raise ValueError('한 요청에는 최대 6개·합계 30MB까지 첨부할 수 있습니다.')
        path=_root(db)/(identifier+extension)
        # Exclusive creation avoids replacing any existing file or following links.
        if path.exists() or path.is_symlink():
            # A crash after exclusive byte storage but before DB commit can leave
            # this exact upload orphaned. Recover only identical immutable bytes.
            orphan={'id':identifier,'extension':extension,'size':len(data),'sha256':digest}
            read_bytes(db,orphan)
        else:
            with path.open('xb') as stream:
                stream.write(data)
                stream.flush(); os.fsync(stream.fileno())
        c.execute('INSERT INTO chat_attachments VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                  (identifier,request_id,None,name,extension,len(data),mime,kind,digest,json.dumps(details),time.time()))
        row=c.execute('SELECT * FROM chat_attachments WHERE id=?',(identifier,)).fetchone()
    return public(row)


def discard(db,identifier,request_id):
    """Cancel only an unbound upload owned by this command draft."""
    identifier,request_id=_id(identifier),_id(request_id)
    with contextlib.closing(sqlite3.connect(str(db),timeout=15)) as c,c:
        c.row_factory=sqlite3.Row
        initialize(c)
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM chat_attachments WHERE id=?',(identifier,)).fetchone()
        if row is None:return {'ok':True}
        if row['request_id']!=request_id or row['message_id'] is not None:
            raise ValueError('현재 입력 중인 요청의 미전송 첨부만 취소할 수 있습니다.')
        try:
            path=_path(db,row)
            path.unlink()
        except FileNotFoundError:
            pass
        c.execute('DELETE FROM chat_attachments WHERE id=?',(identifier,))
    return {'ok':True}


def bind(c,ids,message_id,request_id):
    """Caller initializes table before BEGIN and owns atomic request transaction."""
    if not isinstance(ids,list) or len(ids)>MAX_FILES or len(set(ids))!=len(ids): raise ValueError('첨부 자료 목록을 확인해주세요.')
    request_id=_id(request_id)
    rows=[]
    for identifier in ids:
        _id(identifier)
        row=c.execute('SELECT request_id,message_id,size FROM chat_attachments WHERE id=?',(identifier,)).fetchone()
        if not row or row[0]!=request_id or row[1] not in (None,message_id): raise ValueError('현재 요청에 올린 첨부 자료만 사용할 수 있습니다.')
        rows.append(row)
    if sum(row[2] for row in rows)>MAX_TOTAL: raise ValueError('첨부 합계 크기가 제한을 넘습니다.')
    for identifier in ids: c.execute('UPDATE chat_attachments SET message_id=? WHERE id=?',(message_id,identifier))


def _rows(db,column,value):
    if isinstance(db,sqlite3.Connection):
        cursor=db.execute('SELECT * FROM chat_attachments WHERE '+column+'=? AND message_id IS NOT NULL ORDER BY created,id',(value,))
        names=[item[0] for item in cursor.description]
        return [public(dict(zip(names,row))) for row in cursor]
    with contextlib.closing(sqlite3.connect(str(db))) as c:
        c.row_factory=sqlite3.Row
        initialize(c)
        return [public(row) for row in c.execute('SELECT * FROM chat_attachments WHERE '+column+'=? AND message_id IS NOT NULL ORDER BY created,id',(value,))]


def for_message(db,message_id): return _rows(db,'message_id',message_id)
def for_request(db,request_id): return _rows(db,'request_id',request_id)


def resolve(db,identifier,context=None):
    _id(identifier)
    with contextlib.closing(sqlite3.connect(str(db))) as c:
        c.row_factory=sqlite3.Row
        initialize(c)
        found=c.execute('SELECT * FROM chat_attachments WHERE id=?',(identifier,)).fetchone()
    if not found: raise ValueError('첨부 자료를 찾을 수 없습니다.')
    row=dict(found)
    if context is not None:
        scope=context.get('attachment_scope',[])
        if identifier not in scope or row['message_id'] is None: raise ValueError('이 대화에 연결된 첨부 자료만 사용할 수 있습니다.')
    read_bytes(db,row)
    return row


def serve(handler,db,identifier,download=False):
    row=resolve(db,identifier)
    data=read_bytes(db,row)
    inline=row['kind']=='image' and not download
    handler._attachment_started=True
    handler.send_response(200)
    handler.send_header('Content-Type',row['mime'] if inline else 'application/octet-stream')
    handler.send_header('Content-Length',str(len(data)))
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Cross-Origin-Resource-Policy','same-origin')
    handler.send_header('Cache-Control','private, max-age=3600, immutable')
    handler.send_header('Content-Disposition',('inline' if inline else 'attachment')+"; filename*=UTF-8''"+urllib.parse.quote(row['name'],safe=''))
    handler.end_headers()
    handler.wfile.write(data)


def image_input(db,row):
    """Sample an animation for visual analysis; never re-encode its stored original."""
    from PIL import Image,ImageOps,ImageDraw
    data=read_bytes(db,row)
    with Image.open(io.BytesIO(data)) as source:
        count=getattr(source,'n_frames',1)
        positions=sorted(set([0,count//3,2*count//3,count-1])) if count>1 else [0]
        canvas=Image.new('RGB',(384*len(positions),408),(28,30,31))
        draw=ImageDraw.Draw(canvas)
        for index,position in enumerate(positions):
            source.seek(position)
            frame=ImageOps.contain(source.convert('RGBA'),(384,384))
            canvas.paste(frame,(384*index+(384-frame.width)//2,(384-frame.height)//2),frame)
            draw.text((384*index+8,388),'Frame '+str(position+1)+' / '+str(count),fill='white')
        output=io.BytesIO(); canvas.save(output,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
