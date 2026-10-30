"""Immutable served UI generations; working-tree edits are not publication."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading

ROOT=Path(__file__).resolve().parent
CACHE=ROOT/'local_backups'/'ui-assets'
REVISION='2026-10-29-published-ui-cohort-v1'
_lock=threading.RLock()
_assets={}
_generation=None
_bound_pid=None


def _valid(name):return bool(re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:html|js|css)',name))


def _install(assets):
    global _assets,_generation
    digest=hashlib.sha256()
    for name,raw in sorted(assets.items()):digest.update(name.encode());digest.update(raw)
    generation=digest.hexdigest();folder=CACHE/generation;folder.mkdir(parents=True,exist_ok=True)
    import execution_receipts,tempfile
    files={}
    for name,raw in assets.items():
        target=folder/name
        if not target.exists():
            fd,temp=tempfile.mkstemp(dir=str(folder),prefix='.asset-')
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(raw)
                os.replace(temp,target)
            finally:
                if os.path.exists(temp):os.unlink(temp)
        files[name]=hashlib.sha256(raw).hexdigest()
    execution_receipts.write_json(folder/'manifest.json',{'generation':generation,'files':files})
    execution_receipts.write_json(CACHE/'published.json',{'generation':generation})
    _assets=assets;_generation=generation


def bind(server):
    global _bound_pid
    import live_project
    with _lock:
        import office_feedback
        names={name for name in live_project.paths() if _valid(name)}|set(office_feedback.UI_FILES)
        assets={name:(ROOT/name).read_bytes() for name in sorted(names) if (ROOT/name).is_file()}
        if (CACHE/'published.json').is_file():
            prior=json.loads((CACHE/'published.json').read_text(encoding='utf-8'))['generation']
            if not re.fullmatch('[a-f0-9]{64}',prior):raise ValueError('Invalid published UI generation')
            manifest=json.loads((CACHE/prior/'manifest.json').read_text(encoding='utf-8'))
            assets={name:read(name,prior) for name in manifest['files'] if _valid(name)}
            import office_reload
            ticket=office_reload.ticket_record(os.environ.get('AI_OFFICE_RELOAD_TICKET'))
            for row in ticket.get('verified_changes',[]):
                if not _valid(row['path']):continue
                raw=(ROOT/row['path']).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Pinned UI source changed')
                assets[row['path']]=raw
        _install(assets)
        _bound_pid=os.getpid()


def generation():
    with _lock:return _generation if _bound_pid==os.getpid() else None


def status():
    with _lock:
        return {'revision':REVISION,'generation':generation(),'serving_pid':_bound_pid,'files':len(_assets),
                'script_sha256':{name:hashlib.sha256(_assets[name]).hexdigest() for name in ('workdesk.js','work-observer.js') if name in _assets},
                'actual_open_view_verified':False,'apply_policy':'explicit safe user action'}


def read(name,version=None):
    if not _valid(name):raise ValueError('Unknown UI asset')
    with _lock:
        if not version or version==_generation:
            if name not in _assets:raise ValueError('UI asset is not published')
            return _assets[name]
    if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('Invalid UI generation')
    folder=CACHE/version
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if name not in manifest.get('files',{}):raise ValueError('UI asset is not in this generation')
    raw=(folder/name).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['files'][name]:raise ValueError('UI generation asset changed')
    return raw


def publish(changes):
    if _bound_pid!=os.getpid():raise ValueError('UI publication requires the actual serving process')
    with _lock:
        updated=dict(_assets)
        for row in changes:
            if not _valid(row['path']):continue
            raw=(ROOT/row['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('UI source changed before publication')
            updated[row['path']]=raw
        _install(updated)
        return _generation


def page():
    with _lock:
        version=generation();text=read('index.html').decode('utf-8').replace('<!-- WORK_DESK -->',read('workdesk.html').decode('utf-8'))
    return (re.sub(r'((?:src|href)=["\'])(/[A-Za-z0-9_.-]+\.(?:js|css))(["\'])',
                   lambda m:m[1]+m[2]+'?office_ui='+version+m[3],text),version)
