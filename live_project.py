"""Before/after receipts for native Codex development in the actual project."""
import json
import os
from pathlib import Path

import self_edit

ROOT=self_edit.ROOT
EXCLUDED={'.git','.codex','.agents','.aws','.ssh','.venv','venv','node_modules','__pycache__',
          'local_backups','uploads','attachments','assets','browser_runtime','organizer_runtime',
          'desktop_recovery','dist','build'}


def valid_name(name):
    if not isinstance(name,str) or '\\' in name or ':' in name:return False
    path=Path(name)
    return (not path.is_absolute() and bool(path.parts) and path.as_posix()==name
            and all(part not in EXCLUDED and not part.startswith('.') for part in path.parts)
            and path.suffix.lower() in {'.py','.js','.mjs','.css','.html','.md','.ts','.tsx','.jsx'})


def load(name):
    if not valid_name(name):raise ValueError('개발 소스 경로가 아닙니다.')
    path=ROOT/name;info=self_edit._unlinked(path)
    if info.st_size>self_edit.MAX_BYTES:raise ValueError('개발 변경 기록의 파일 크기 한도를 넘었습니다.')
    raw=path.read_bytes();raw.decode('utf-8')
    return path,raw


def paths():
    result=[]
    for current,dirs,files in os.walk(ROOT,followlinks=False):
        allowed=[]
        for name in dirs:
            if name in EXCLUDED or name.startswith('.'):continue
            try:self_edit._unlinked(Path(current)/name,directory=True)
            except (OSError,ValueError):continue
            allowed.append(name)
        dirs[:]=allowed
        for name in files:
            relative=(Path(current)/name).relative_to(ROOT).as_posix()
            if valid_name(relative):result.append(relative)
        if len(result)>3000:raise ValueError('개발 변경 기록 범위를 나누어 주세요.')
    return sorted(result)


def begin(request_id):
    import re
    if not re.fullmatch('[0-9a-f]{32}',request_id):raise ValueError('개발 요청 식별자를 확인하세요.')
    folder=ROOT/'local_backups'/'live-development'/request_id
    folder.mkdir(parents=True,exist_ok=False)
    before={}
    for name in paths():
        _,raw=load(name);target=folder/'before'/name
        target.parent.mkdir(parents=True,exist_ok=True);self_edit._atomic(target,raw)
        before[name]=self_edit._hash(raw)
    self_edit._atomic(folder/'manifest.json',json.dumps(before,ensure_ascii=False).encode('utf-8'))
    return {'folder':str(folder),'before':before}


def changes(run):
    rows=[]
    for name in sorted(set(run['before'])|set(paths())):
        present=(ROOT/name).exists()
        raw=load(name)[1] if present else b''
        previous=run['before'].get(name)
        if (previous is None and present) or (previous is not None and (not present or self_edit._hash(raw)!=previous)):
            rows.append((name,raw,present))
    return rows


def review(run):
    errors=[]
    for name,raw,present in changes(run):
        try:
            if present:
                if name=='app.py' and not raw.strip():raise ValueError('앱 실행 진입점이 비어 있습니다.')
                self_edit._syntax(name,raw)
        except (ValueError,OSError) as error:errors.append({'path':name,'message':str(error)[:250]})
    return errors


def restore_invalid(run):
    restored=[]
    for error in review(run):
        name=error['path'];path,current=load(name)
        if path.read_bytes()!=current:raise ValueError('이후 편집이 있어 자동 복구하지 않았습니다.')
        if name in run['before']:
            before=(Path(run['folder'])/'before'/name).read_bytes()
            if self_edit._hash(before)!=run['before'][name]:raise ValueError('복구 백업이 바뀌었습니다.')
            self_edit._atomic(path,before)
        else:path.unlink()
        restored.append(name)
    return restored


def finish(run):
    receipts=[]
    for name,raw,present in changes(run):
        previous=run['before'].get(name)
        before=(Path(run['folder'])/'before'/name).read_bytes() if previous is not None else b''
        if previous is not None and self_edit._hash(before)!=previous:raise ValueError('개발 백업의 무결성을 확인하지 못했습니다.')
        result=self_edit._write_change(name,before,raw,creating=previous is None,deleting=not present,direct_project=True,already_applied=True)
        receipts.append(result)
    return receipts
