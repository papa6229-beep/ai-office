"""AI OFFICE's own Windows credentials; no public password-read interface."""
import ctypes
from ctypes import wintypes as w
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

PREFIX='AI_OFFICE/login/'


class _Credential(ctypes.Structure):
    _fields_=[('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),
              ('Comment',w.LPWSTR),('LastWritten',w.FILETIME),
              ('CredentialBlobSize',w.DWORD),('CredentialBlob',ctypes.POINTER(w.BYTE)),
              ('Persist',w.DWORD),('AttributeCount',w.DWORD),('Attributes',ctypes.c_void_p),
              ('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]


_Pointer=ctypes.POINTER(_Credential)


def _api():
    if os.name!='nt':raise ValueError('이 로그인 저장소는 Windows에서 사용할 수 있어요.')
    api=ctypes.WinDLL('Advapi32.dll',use_last_error=True)
    api.CredWriteW.argtypes=[_Pointer,w.DWORD];api.CredWriteW.restype=w.BOOL
    api.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.POINTER(_Pointer)];api.CredReadW.restype=w.BOOL
    api.CredEnumerateW.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.POINTER(ctypes.POINTER(_Pointer))];api.CredEnumerateW.restype=w.BOOL
    api.CredFree.argtypes=[ctypes.c_void_p];api.CredFree.restype=None
    return api


def origin(url):
    try:
        parts=urlsplit(url)
        if parts.scheme.lower()!='https' or not parts.hostname or parts.username is not None or parts.password is not None:raise ValueError()
        host=parts.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:host='['+host+']'
        value='https://'+host+(':'+str(parts.port) if parts.port not in (None,443) else '')
        if len(value)>160:raise ValueError()
        return value
    except (ValueError,UnicodeError,TypeError):raise ValueError('로그인 계정은 정확한 HTTPS 사이트 주소에 연결해 주세요.') from None


def _name(identifier):
    if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('등록된 로그인 계정을 선택해 주세요.')
    return PREFIX+identifier


def _metadata(record):
    name=record.TargetName or ''
    if record.Type!=1 or not name.startswith(PREFIX):raise ValueError('오피스에 등록한 계정이 아니에요.')
    ident=name[len(PREFIX):];_name(ident)
    try:
        data=json.loads(record.Comment or '{}')
        if data.get('v')!=1 or origin(data['origin'])!=data['origin'] or not isinstance(data['label'],str):raise ValueError()
    except (ValueError,KeyError,TypeError):raise ValueError('등록된 계정 정보를 확인하지 못했어요.') from None
    return {'id':ident,'label':data['label'],'origin':data['origin'],'storage':'windows_credential_manager'}


def save(url,label,username,password):
    site=origin(url)
    if not isinstance(label,str) or not 1<=len(label.strip())<=60:raise ValueError('계정 이름은 60자 이내로 지정해 주세요.')
    if not isinstance(username,str) or len(username)>256 or '\x00' in username:raise ValueError('아이디를 확인해 주세요.')
    if not isinstance(password,str) or not password or '\x00' in password:raise ValueError('저장할 비밀번호를 확인해 주세요.')
    secret=password.encode('utf-16-le')
    if len(secret)>2560:raise ValueError('저장할 비밀번호가 너무 길어요.')
    ident=hashlib.sha256((site+'\x00'+username).encode('utf-8')).hexdigest()[:32]
    comment=json.dumps({'v':1,'origin':site,'label':label.strip()},ensure_ascii=False,separators=(',',':'))
    if len(comment.encode('utf-16-le'))>512:raise ValueError('계정 이름이나 사이트 주소가 너무 길어요.')
    buffer=ctypes.create_string_buffer(secret)
    record=_Credential();record.Type=1;record.TargetName=_name(ident);record.Comment=comment
    record.CredentialBlobSize=len(secret);record.CredentialBlob=ctypes.cast(buffer,ctypes.POINTER(w.BYTE))
    record.Persist=2;record.UserName=username
    try:
        if not _api().CredWriteW(ctypes.byref(record),0):raise ValueError('이 Windows 사용자의 로그인 저장소에 등록하지 못했어요.')
    finally:ctypes.memset(buffer,0,len(buffer))
    return _metadata(record)


def listing(query=''):
    if not isinstance(query,str) or len(query)>200:raise ValueError('계정 검색어를 확인해 주세요.')
    api=_api();count=w.DWORD();rows=ctypes.POINTER(_Pointer)()
    if not api.CredEnumerateW(PREFIX+'*',0,ctypes.byref(count),ctypes.byref(rows)):
        if ctypes.get_last_error()==1168:return []
        raise ValueError('Windows 로그인 저장소를 읽지 못했어요.')
    result=[]
    try:
        for index in range(count.value):
            try:metadata=_metadata(rows[index].contents)
            except ValueError:continue
            if not query or query.casefold() in (metadata['label']+' '+metadata['origin']).casefold():result.append(metadata)
    finally:api.CredFree(rows)
    return result


def load_for_origin(identifier,url):
    """Private browser-worker use only, after observed-page origin validation."""
    site=origin(url);api=_api();pointer=_Pointer()
    if not api.CredReadW(_name(identifier),1,0,ctypes.byref(pointer)):raise ValueError('등록한 로그인 계정을 찾지 못했어요.')
    try:
        record=pointer.contents;metadata=_metadata(record)
        if metadata['origin']!=site:raise ValueError('저장된 계정의 사이트와 현재 로그인 화면이 달라요.')
        if not 0<record.CredentialBlobSize<=2560:raise ValueError('저장된 로그인 정보를 확인하지 못했어요.')
        secret=ctypes.string_at(record.CredentialBlob,record.CredentialBlobSize).decode('utf-16-le')
        return record.UserName or '',secret
    finally:api.CredFree(pointer)
