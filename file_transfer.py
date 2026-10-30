"""Pinned Windows rename/copy primitives for journaled user file operations."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import os
from pathlib import Path
import stat

import file_executor as old
import task_control


def identity(info):return {'dev':int(info.st_dev),'ino':int(info.st_ino)}


def metadata(path):
    path=old._safe_path(Path(path));info=path.lstat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or not info.st_ino:raise ValueError('일반 파일·폴더의 신원을 확인할 수 없습니다.')
    if stat.S_ISREG(info.st_mode) and info.st_nlink!=1:raise ValueError('하드링크 파일은 이 이동 경로에서 제외합니다.')
    return dict(identity(info),kind='folder' if stat.S_ISDIR(info.st_mode) else 'file',size=int(info.st_size),mtime_ns=int(info.st_mtime_ns))


def same_node(path,expected,contents=False):
    try:
        current=metadata(path)
        keys=('dev','ino','kind','size','mtime_ns') if contents and current['kind']=='file' else ('dev','ino','kind')
        return all(current[k]==expected[k] for k in keys)
    except (OSError,ValueError,KeyError):return False


def _kernel():
    if os.name!='nt':raise ValueError('파일 이동 실행기는 Windows에서 사용합니다.')
    dll=ctypes.WinDLL('kernel32',use_last_error=True)
    dll.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE];dll.CreateFileW.restype=w.HANDLE
    dll.CloseHandle.argtypes=[w.HANDLE];dll.CloseHandle.restype=w.BOOL
    dll.SetFileInformationByHandle.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];dll.SetFileInformationByHandle.restype=w.BOOL
    return dll


@contextmanager
def pinned(path,expected,*,deleting=True):
    """Block other writers/deleters; rename/delete only through this handle."""
    import msvcrt
    path=Path(path);dll=_kernel()
    flags=0x00200000 | (0x02000000 if expected['kind']=='folder' else 0)
    handle=dll.CreateFileW(str(path),0x80000000 | (0x10000 if deleting else 0),3 if expected['kind']=='folder' else 1,None,3,flags,None)
    if handle==ctypes.c_void_p(-1).value:raise OSError('파일이 사용 중이거나 접근할 수 없습니다.')
    stream=None
    try:
        if not same_node(path,expected,contents=True):raise ValueError('관찰 이후 대상이 바뀌었습니다. 다시 조회해 주세요.')
        if expected['kind']=='file':
            fd=msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
            stream=os.fdopen(fd,'rb')
            if old._stamp(os.fstat(fd))!={k:expected[k] for k in ('dev','ino','size','mtime_ns')}:raise ValueError('열린 파일 신원이 다릅니다.')
        yield dll,handle,stream
    finally:
        if stream:stream.close()
        else:dll.CloseHandle(handle)


def digest(stream):
    stream.seek(0);value=hashlib.sha256()
    while True:
        task_control.checkpoint();block=stream.read(1024*1024)
        if not block:break
        value.update(block)
    return value.hexdigest()


def plain_copy_source(path):
    """Do not discard encrypted data or NTFS alternate streams during a copy."""
    if Path(path).stat().st_file_attributes & 0x4000:
        raise ValueError('암호화된 파일은 드라이브 간 이동에서 제외합니다.')
    class StreamData(ctypes.Structure):
        _fields_=[('size',ctypes.c_longlong),('name',w.WCHAR*296)]
    dll=_kernel()
    dll.FindFirstStreamW.argtypes=[w.LPCWSTR,ctypes.c_int,ctypes.c_void_p,w.DWORD];dll.FindFirstStreamW.restype=w.HANDLE
    dll.FindNextStreamW.argtypes=[w.HANDLE,ctypes.c_void_p];dll.FindNextStreamW.restype=w.BOOL
    dll.FindClose.argtypes=[w.HANDLE];dll.FindClose.restype=w.BOOL
    data=StreamData();handle=dll.FindFirstStreamW(str(path),0,ctypes.byref(data),0)
    if handle==ctypes.c_void_p(-1).value:
        if ctypes.get_last_error()==38:return
        raise ValueError('파일의 추가 데이터 스트림을 확인하지 못해 복사 이동하지 않습니다.')
    try:
        while True:
            if data.name!='::$DATA':raise ValueError('추가 데이터 스트림이 있는 파일은 원본 보존을 위해 드라이브 간 이동하지 않습니다.')
            if not dll.FindNextStreamW(handle,ctypes.byref(data)):
                if ctypes.get_last_error()!=38:raise ValueError('파일 데이터 스트림 조회를 완료하지 못했습니다.')
                break
    finally:dll.FindClose(handle)


def rename_handle(dll,handle,destination):
    class Rename(ctypes.Structure):
        _fields_=[('Flags',w.DWORD),('RootDirectory',w.HANDLE),('FileNameLength',w.DWORD),('FileName',w.WCHAR*1)]
    encoded=str(destination).encode('utf-16-le');buffer=ctypes.create_string_buffer(Rename.FileName.offset+len(encoded)+2)
    record=ctypes.cast(buffer,ctypes.POINTER(Rename)).contents;record.Flags=0;record.FileNameLength=len(encoded)
    ctypes.memmove(ctypes.addressof(buffer)+Rename.FileName.offset,encoded,len(encoded))
    if not dll.SetFileInformationByHandle(handle,3,buffer,len(buffer)):raise OSError('이름 충돌 또는 사용 중인 대상 때문에 이동하지 못했습니다.')


def delete_handle(dll,handle):
    mark=ctypes.c_ubyte(1)
    if not dll.SetFileInformationByHandle(handle,4,ctypes.byref(mark),ctypes.sizeof(mark)):raise OSError('파일·폴더 삭제를 완료하지 못했습니다. 접근 권한이나 사용 중인 상태를 확인하세요.')


def move_same(source,destination,expected):
    with old._directory_guards((Path(source).parent,Path(destination).parent)),pinned(source,expected) as (dll,handle,_):
        if Path(destination).exists():raise ValueError('도착 위치가 이미 있습니다. 덮어쓰지 않았습니다.')
        old._safe_path(Path(destination),missing=True);task_control.checkpoint()
        rename_handle(dll,handle,destination)
    return metadata(destination)


def remove_empty(path,expected):
    with old._directory_guards((Path(path).parent,)),pinned(path,expected) as (dll,handle,_):
        if any(Path(path).iterdir()):raise ValueError('폴더에 남은 항목이 있어 삭제하지 않았습니다.')
        task_control.checkpoint();delete_handle(dll,handle)


def remove_owned(path,expected):
    with old._directory_guards((Path(path).parent,)),pinned(path,expected) as (dll,handle,_):delete_handle(dll,handle)


def transfer(source,destination,expected,state,save,progress):
    """Recoverable cross-volume file move. save persists the step ownership."""
    source=Path(source);destination=Path(destination)
    with old._directory_guards((source.parent,destination.parent)):
        # Published copies are recoverable only by the recorded identity/hash.
        if state.get('published'):
            published=state['published']
            if not destination.exists() and state.get('phase')=='publishing':
                temporary=Path(state['temporary'])
                with pinned(source,expected) as (_,_,src),pinned(temporary,published) as (dll,handle,temp):
                    if digest(src)!=state['sha256'] or digest(temp)!=state['sha256']:raise ValueError('중단된 복사본 검증에 실패했습니다.')
                    task_control.checkpoint();rename_handle(dll,handle,destination)
                state['phase']='published';state['published']=metadata(destination);published=state['published'];save()
            if not same_node(destination,published,contents=True):raise ValueError('복사된 도착 파일이 바뀌어 원본을 지우지 않았습니다.')
            with pinned(destination,published,deleting=False) as (_,_,dest):
                if digest(dest)!=state['sha256']:raise ValueError('복사본 검증이 일치하지 않습니다.')
                if source.exists():
                    with pinned(source,expected) as (dll,handle,src):
                        plain_copy_source(source)
                        if digest(src)!=state['sha256']:raise ValueError('원본 내용이 바뀌었습니다.')
                        task_control.checkpoint();delete_handle(dll,handle)
            return metadata(destination)
        if destination.exists():raise ValueError('도착 파일이 이미 있습니다. 덮어쓰지 않았습니다.')
        with pinned(source,expected) as (dll,handle,src):
            plain_copy_source(source)
            temporary=Path(state['temporary'])
            if temporary.exists():
                if not state.get('temporary_identity') or not same_node(temporary,state['temporary_identity']):raise ValueError('임시 파일의 소유 기록을 확인할 수 없습니다.')
                remove_owned(temporary,metadata(temporary))
            with temporary.open('xb') as out:
                state['temporary_identity']=metadata(temporary);save()
                value=hashlib.sha256();copied=0
                while True:
                    task_control.checkpoint();block=src.read(1024*1024)
                    if not block:break
                    out.write(block);value.update(block);copied+=len(block);progress(copied,expected['size'])
                out.flush();os.fsync(out.fileno())
            if copied!=expected['size'] or old._stamp(os.fstat(src.fileno()))!={k:expected[k] for k in ('dev','ino','size','mtime_ns')}:raise ValueError('복사 중 원본이 바뀌었습니다.')
            os.utime(temporary,ns=(os.fstat(src.fileno()).st_atime_ns,expected['mtime_ns']))
            state['sha256']=value.hexdigest();state['temporary_identity']=metadata(temporary);save()
            with pinned(temporary,state['temporary_identity']) as (temp_dll,temp_handle,temp):
                if digest(temp)!=state['sha256']:raise ValueError('임시 복사본 검증에 실패했습니다.')
                task_control.checkpoint()
                # Persist the future published identity before the atomic rename.
                state['published']=dict(state['temporary_identity']);state['phase']='publishing';save()
                rename_handle(temp_dll,temp_handle,destination)
            state['phase']='published';state['published']=metadata(destination);save()
            with pinned(destination,state['published'],deleting=False) as (_,_,dest):
                if digest(dest)!=state['sha256']:raise ValueError('게시된 복사본 검증에 실패했습니다.')
                task_control.checkpoint();delete_handle(dll,handle)
    return metadata(destination)
