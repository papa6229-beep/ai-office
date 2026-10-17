"""Native Codex work copies and audited publication; private app data stays outside.

Only the work-copy directory is writable by native agent tools. Baselines and
published immutable artifacts are siblings, managed exclusively by this module.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

import self_edit

ROOT = self_edit.ROOT
WORK = ROOT / 'local_backups' / 'codex-agent-workspaces'
STATE = ROOT / 'local_backups' / 'codex-agent-state'
MAX_OUTPUT = 30 * 1024 * 1024
LOCK = threading.RLock()
REFERENCES = ('README.md','PROJECT_CONTEXT.md')


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):
        raise ValueError('서버 작업 세션을 확인할 수 없습니다.')
    return value


def _directory(path):
    path=Path(path)
    missing=[]
    current=path
    while not current.exists():
        if current.is_symlink():raise ValueError('연결된 작업 경로를 사용할 수 없습니다.')
        missing.append(current);current=current.parent
    self_edit._unlinked(current,directory=True)
    for item in reversed(missing):
        item.mkdir(exist_ok=True)
        self_edit._unlinked(item,directory=True)
    self_edit._unlinked(path,directory=True)
    return path


def _read(path,limit):
    info=self_edit._unlinked(path)
    if info.st_size>limit:raise ValueError('파일 크기 제한을 넘었습니다.')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino) or opened.st_nlink!=1:
            raise ValueError('읽는 중 파일이 변경되었습니다.')
        data=stream.read(limit+1)
    self_edit._unlinked(path)
    if len(data)>limit:raise ValueError('파일 크기 제한을 넘었습니다.')
    return data


def _paths(session_id):
    identifier=_id(session_id)
    return WORK/identifier,STATE/(identifier+'.json')


def _manifest(session_id):
    _,path=_paths(session_id)
    if not path.exists():raise ValueError('작업 사본을 먼저 준비하세요.')
    data=json.loads(_read(path,131072).decode('utf-8'))
    if data.get('session_id')!=session_id or not isinstance(data.get('sources'),dict):
        raise ValueError('작업 기준 원본을 확인할 수 없습니다.')
    return data


def _save(session_id,data):
    _,path=_paths(session_id)
    _directory(STATE)
    self_edit._atomic(path,json.dumps(data,ensure_ascii=False).encode('utf-8'))


def prepare(session_id):
    """Create or synchronize clean work copies; never overwrite pending edits."""
    with LOCK,self_edit.LOCK:
        workspace,state_path=_paths(session_id)
        for folder in (workspace,workspace/'source',workspace/'outputs',workspace/'inputs',workspace/'reference'):
            _directory(folder)
        data=_manifest(session_id) if state_path.exists() else {'session_id':session_id,'sources':{}}
        for name in sorted(self_edit.source_names()):
            if not (ROOT/name).is_file():continue
            _,raw,_=self_edit._load(name)
            actual=self_edit._hash(raw)
            staged=workspace/'source'/name
            baseline=data['sources'].get(name)
            if not baseline:
                if staged.exists():
                    # An untracked staged file is never mistaken for a trusted baseline.
                    continue
                self_edit._atomic(staged,raw)
                data['sources'][name]={'sha256':actual}
            elif staged.exists() and self_edit._hash(_read(staged,self_edit.MAX_BYTES))==baseline['sha256']:
                if actual!=baseline['sha256']:
                    self_edit._atomic(staged,raw);baseline['sha256']=actual
        for name in REFERENCES:
            if (ROOT/name).is_file():
                self_edit._atomic(workspace/'reference'/name,_read(ROOT/name,self_edit.MAX_BYTES))
        _save(session_id,data)
        return {'cwd':str(workspace),'source_dir':str(workspace/'source'),
                'outputs_dir':str(workspace/'outputs'),'inputs_dir':str(workspace/'inputs'),
                'reference_dir':str(workspace/'reference')}


def _relative(path,section):
    if not isinstance(path,str) or '\\' in path or ':' in path or '\0' in path:
        raise ValueError('작업 폴더의 상대 경로를 사용하세요.')
    value=Path(path)
    parts=value.parts
    if parts and parts[0]==section:parts=parts[1:]
    if not parts or value.is_absolute() or any(p in {'..','.'} or p.startswith('.') for p in parts):
        raise ValueError('작업 폴더의 상대 경로를 사용하세요.')
    return Path(*parts)


def status(session_id):
    with LOCK,self_edit.LOCK:
        locations=prepare(session_id)
        workspace,_=_paths(session_id);data=_manifest(session_id)
        rows=[]
        for path in sorted((workspace/'source').iterdir()):
            if not path.is_file():continue
            raw=_read(path,self_edit.MAX_BYTES)
            baseline=data['sources'].get(path.name)
            digest=self_edit._hash(raw)
            if not baseline or digest!=baseline['sha256']:
                live_hash=None
                if path.name in self_edit.source_names() and (ROOT/path.name).exists():
                    live_hash=self_edit._hash(self_edit._load(path.name)[1])
                rows.append({'path':'source/'+path.name,'state':'new' if not baseline else 'modified',
                             'conflict':bool(baseline and live_hash!=baseline['sha256'])})
        return {**locations,'changes':rows,
                'instructions':'모든 모델은 workspace_list/read/write/run/image로 작업합니다. Codex는 네이티브 도구도 사용합니다. source 작업본은 agent_source_publish, outputs 결과물은 agent_output_publish로 게시합니다. reference와 inputs는 자료입니다. 실제 앱 반영·화면 확인은 별도입니다.'}


def publish_source(session_id,path):
    with LOCK,self_edit.LOCK:
        workspace,_=_paths(session_id);data=_manifest(session_id)
        relative=_relative(path,'source')
        if len(relative.parts)!=1:raise ValueError('앱 원본은 source 바로 아래 파일이어야 합니다.')
        name=relative.name
        raw=_read(workspace/'source'/name,self_edit.MAX_BYTES)
        baseline=data['sources'].get(name)
        if not baseline and (name in self_edit.ALLOWED or (ROOT/name).exists()):
            raise ValueError('현재 원본의 작업 기준이 없습니다. 작업 사본을 다시 준비하세요.')
        result=self_edit.publish_file(name,baseline['sha256'] if baseline else None,raw)
        data['sources'][name]={'sha256':self_edit._hash(raw)}
        _save(session_id,data)
        return result


def stage_inputs(session_id,db_path,current_user_message_id):
    """Copy only attachments bound to this server-selected current user message."""
    import work_attachments
    with LOCK:
        locations=prepare(session_id)
        if not current_user_message_id:return []
        rows=work_attachments.for_message(db_path,current_user_message_id)
        items=[]
        for public in rows:
            row=work_attachments.resolve(db_path,public['id'],{'attachment_scope':[public['id']]})
            raw=work_attachments.read_bytes(db_path,row)
            name=row['id']+row['extension']
            target=Path(locations['inputs_dir'])/name
            if target.exists():
                if self_edit._hash(_read(target,work_attachments.MAX_BYTES))!=row['sha256']:
                    raise ValueError('작업 첨부 사본이 변경되었습니다.')
            else:self_edit._atomic(target,raw)
            items.append({'id':row['id'],'name':row['name'],'path':'inputs/'+name,'kind':row['kind']})
        return items


def publish_output(session_id,path,title=''):
    import work_attachments
    with LOCK:
        workspace,_=_paths(session_id);_manifest(session_id)
        relative=_relative(path,'outputs')
        target=workspace/'outputs'/relative
        raw=_read(target,MAX_OUTPUT)
        extension=target.suffix.lower()
        mime='application/octet-stream';kind='text';text='완성된 파일을 다운로드할 수 있습니다.';language=''
        if extension in work_attachments.IMAGE_TYPES:
            mime,kind,_=work_attachments._inspect(raw,extension)
            text=''
        elif extension in {'.mp4','.webm'}:
            if not ((extension=='.mp4' and len(raw)>12 and raw[4:8]==b'ftyp') or
                    (extension=='.webm' and raw.startswith(b'\x1aE\xdf\xa3'))):
                raise ValueError('영상의 실제 파일 형식을 확인하세요.')
            mime='video/mp4' if extension=='.mp4' else 'video/webm';kind='video';text=''
        elif extension in work_attachments.TEXT_TYPES:
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError:raise ValueError('텍스트 결과물은 UTF-8로 저장하세요.') from None
            code={'.py':'python','.js':'javascript','.html':'html','.css':'css','.json':'json','.sql':'sql'}
            language=code.get(extension,'');kind='code' if language else 'text'
            mime='text/plain; charset=utf-8'
        identifier=uuid.uuid4().hex
        folder=_directory(STATE/'artifacts'/identifier)
        self_edit._atomic(folder/'data.bin',raw)
        meta={'id':identifier,'name':relative.name,'mime':mime,'kind':kind,'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        self_edit._atomic(folder/'metadata.json',json.dumps(meta,ensure_ascii=False).encode('utf-8'))
        artifact={'id':identifier,'kind':kind,'title':str(title or relative.name)[:120],
                  'origin':'source','download_url':'/api/work/agent-artifact?id='+identifier+'&download=1'}
        if kind in {'image','video'}:artifact['url']='/api/work/agent-artifact?id='+identifier
        else:artifact.update(text=text[:12000],language=language,truncated=len(text)>12000)
        return {'artifact':artifact}


@contextmanager
def open_artifact(identifier):
    folder=STATE/'artifacts'/_id(identifier)
    meta=json.loads(_read(folder/'metadata.json',8192).decode('utf-8'))
    if meta.get('id')!=identifier or meta.get('kind') not in {'image','video','text','code'}:
        raise ValueError('게시된 결과물을 확인할 수 없습니다.')
    path=folder/'data.bin'
    raw=_read(path,MAX_OUTPUT)
    if len(raw)!=meta.get('size') or self_edit._hash(raw)!=meta.get('sha256'):
        raise ValueError('게시된 결과물의 무결성을 확인할 수 없습니다.')
    import io
    with io.BytesIO(raw) as stream:yield stream,meta
