"""Bounded, audited source replacements for the running AI OFFICE installation.

Only registry-issued source references reach this module. No shell or generated
code is executed; syntax checking parses Python or uses Node's check-only mode.
"""
import ast
import difflib
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid

ROOT = pathlib.Path(__file__).absolute().parent
BACKUPS = ROOT / 'local_backups' / 'self-edit'
LOCK = threading.RLock()
MAX_BYTES = 512 * 1024
MAX_TEXT = 12000
# An explicit production catalog excludes user assets, credentials, generated
# files, tests, diagnostics, and this module/restart boundary itself.
ALLOWED = frozenset('''adobe_preview.py app.py assistant_extensions.py assistant_model.py
assistant_runtime.py assistant_tools.py assistant_voice.py browser_bookmarks.py
browser_workspace.py capabilities.py chat_launcher.py chat_stream.py codex_bridge.py
content_analysis.py desktop_collections.py desktop_judgment.py desktop_preferences.py
desktop_recovery.py desktop_workspace.py document_reader.py file_executor.py
file_workspace.py lmstudio_runtime.py local_content_review.py local_image_review.py
model_requests.py office_weather.py organizer_lab.py organizer_policy.py
project_intelligence.py quick_launcher.py scanner.py streamdock_import.py
system_settings.py updater.py windows_actions.py windows_app_catalog.py
windows_workspace.py work_assistant.py work_stage.py workspace_insights.py
attachment_tools.py work_attachments.py companion_assets.py office_context.py office_journal.py
desk-collections.js desk-judgment.js desk-organizer.js desk-policy.js desk.js
workambient.js workdesk.js workstage.js work-observer.js desk.css retro.css workambient.css
workdesk.css workstage.css first-release.html index.html workdesk.html'''.split())
MANAGED = ROOT / 'local_backups' / 'codex-agent-state' / 'managed-sources.json'
PROTECTED = frozenset({'self_edit.py','office_reload.py','agent_workspace.py','agent_state.py','office_feedback.py'})


def _valid_new_name(name):
    return (isinstance(name,str) and bool(re.fullmatch(r'[a-z][a-z0-9_-]{0,79}\.(?:py|js|css|html|md)',name))
            and name not in PROTECTED and not re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\..+',name))


def source_names():
    names = set(ALLOWED)
    if MANAGED.exists():
        info = _unlinked(MANAGED)
        if info.st_size > 32768: raise ValueError('관리 원본 목록이 올바르지 않습니다.')
        rows = json.loads(MANAGED.read_text(encoding='utf-8'))
        if not isinstance(rows,list) or any(not _valid_new_name(n) for n in rows):
            raise ValueError('관리 원본 목록이 올바르지 않습니다.')
        names.update(rows)
    return names - PROTECTED


def _register_name(name):
    names = source_names() - set(ALLOWED)
    names.add(name)
    MANAGED.parent.mkdir(parents=True,exist_ok=True)
    _unlinked(MANAGED.parent,directory=True)
    _atomic(MANAGED,json.dumps(sorted(names)).encode('utf-8'))


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _unlinked(path, directory=False):
    """Check every existing ancestor, including Windows junction/reparse points."""
    path = pathlib.Path(path).absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('연결된 경로는 앱 수정에 사용할 수 없습니다.')
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode): raise ValueError('앱 폴더가 올바르지 않습니다.')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('일반 단일 원본 파일만 수정할 수 있습니다.')
    return info


def _path(name):
    if not isinstance(name, str) or name not in source_names():
        raise ValueError('허용된 AI OFFICE 원본 파일을 먼저 조회하세요.')
    path = ROOT / name
    _unlinked(path)
    return path


def _load(name):
    path = _path(name)
    info = path.stat()
    if not 0 <= info.st_size <= MAX_BYTES: raise ValueError('원본 파일 크기 제한은 512KB입니다.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_nlink != 1:
            raise ValueError('원본 파일이 바뀌었습니다. 다시 읽으세요.')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES: raise ValueError('원본 파일 크기 제한은 512KB입니다.')
    _unlinked(path)
    # Keep BOM and newline bytes intact across replacements.
    try: text = raw.decode('utf-8')
    except UnicodeDecodeError: raise ValueError('UTF-8 원본만 수정할 수 있습니다.') from None
    return path, raw, text


def _query_terms(query):
    # Queries describe UI features in Korean as well as literal source symbols.
    # Match independent terms; an entire sentence is rarely a source filename.
    return list(dict.fromkeys(re.findall(r'[\w.-]{2,}', query.casefold())))[:24]


def _source_summary(text):
    lines = [line.strip() for line in text.lstrip('\ufeff').splitlines()[:8] if line.strip()]
    return ' '.join(lines)[:120]


def catalog(query=''):
    with LOCK:
        rows = []
        terms = _query_terms(query)
        for name in sorted(source_names()):
            if not (ROOT / name).exists(): continue
            _, raw, text = _load(name)
            folded = text.casefold()
            matches = [term for term in terms if term in name.casefold() or term in folded]
            score = sum(12 if term in name.casefold() else 1 + min(3, folded.count(term)) for term in matches)
            rows.append({'path':name, 'size':len(raw), 'sha256':_hash(raw),
                         'kind':'backend' if name.endswith('.py') else 'frontend',
                         'summary':_source_summary(text), 'matched_terms':matches,
                         'match_score':score})
        matched = [row for row in rows if row['match_score']]
        # No lexical match means the query vocabulary differs, not that the
        # installation has no editable sources. Keep real references available.
        chosen = matched if matched else rows
        return sorted(chosen, key=lambda row:(-row['match_score'], row['path']))


def read(selection, query='', offset=0):
    with LOCK:
        _, raw, text = _load(selection['path'])
        start = offset
        matched_term = ''
        if query:
            match = text.casefold().find(query.casefold(), offset)
            if match >= 0:
                matched_term = query
            else:
                # Preserve exact-query behavior first, then locate a real term
                # from a natural-language query instead of silently reading 0.
                candidates = [(text.casefold().find(term, offset), term) for term in _query_terms(query)]
                candidates = [(pos, term) for pos, term in candidates if pos >= 0]
                if candidates:
                    match, matched_term = min(candidates)
            start = max(0, match - 1500) if match >= 0 else offset
        end = min(len(text), start + MAX_TEXT)
        return {'path':selection['path'], 'sha256':_hash(raw), 'size':len(raw),
                'offset':start, 'next_offset':end, 'total_chars':len(text),
                'text':text[start:end], 'truncated':end < len(text),
                'query_found':not query or bool(matched_term), 'matched_term':matched_term,
                'hint':'검색어가 없으면 offset/next_offset으로 계속 읽거나 실제 소스의 식별자로 검색하세요.' if query and not matched_term else ''}


def _backup_root():
    _unlinked(ROOT, directory=True)
    for folder in (ROOT / 'local_backups', BACKUPS):
        if not folder.exists(): folder.mkdir()
        _unlinked(folder, directory=True)


def _atomic(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _syntax(name, raw):
    text = raw.decode('utf-8-sig')
    if name.endswith('.py'):
        try: ast.parse(text, filename=name)
        except (SyntaxError, ValueError): raise ValueError('Python 구문 오류가 있어 적용하지 않았습니다.') from None
        return 'python_ast'
    if name.endswith('.js'):
        node = shutil.which('node')
        # The bundled runtime is a fixed executable location, never model input.
        bundled = pathlib.Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        if not node and bundled.is_file(): node = str(bundled)
        if node:
            _backup_root()
            fd, temporary = tempfile.mkstemp(prefix='syntax-', suffix='.js', dir=str(BACKUPS))
            try:
                with os.fdopen(fd, 'wb') as stream: stream.write(raw)
                result = subprocess.run([node, '--check', temporary], capture_output=True, timeout=10,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode: raise ValueError('JavaScript 구문 오류가 있어 적용하지 않았습니다.')
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
            return 'node_check'
        return 'not_available'
    return 'not_applicable'


def _record(change_id):
    if not isinstance(change_id, str) or not re.fullmatch('[0-9a-f]{32}', change_id):
        raise ValueError('조회한 변경 이력을 선택하세요.')
    _backup_root()
    folder = BACKUPS / change_id
    _unlinked(folder, directory=True)
    path = folder / 'change.json'
    info = _unlinked(path)
    if info.st_size > 8192: raise ValueError('변경 이력이 올바르지 않습니다.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('change_id') != change_id or data.get('path') not in source_names():
        raise ValueError('변경 이력이 올바르지 않습니다.')
    return folder, data


def history():
    with LOCK:
        _backup_root()
        rows = []
        for folder in sorted(BACKUPS.iterdir(), key=lambda p:p.name):
            if not re.fullmatch('[0-9a-f]{32}', folder.name): continue
            try:
                _, data = _record(folder.name)
                rows.append({k:data[k] for k in ('change_id','path','created','before_sha256','sha256','status','kind')})
            except (OSError, ValueError, KeyError): continue
        return sorted(rows, key=lambda r:r['created'], reverse=True)[:50]


def _write_change(name, before, after, *, rollback_of=None, creating=False, deleting=False):
    validation = _syntax(name, after)
    _backup_root()
    change_id = uuid.uuid4().hex
    folder = BACKUPS / change_id
    folder.mkdir()
    _unlinked(folder, directory=True)
    metadata = {'change_id':change_id, 'path':name, 'created':time.time(),
                'before_sha256':_hash(before), 'sha256':_hash(after), 'status':'prepared',
                'kind':'backend' if name.endswith('.py') else 'frontend', 'validation':validation,
                'created_file':creating,'deleted_file':deleting}
    if rollback_of: metadata['rollback_of'] = rollback_of
    _atomic(folder / 'before.bin', before)
    _atomic(folder / 'change.json', json.dumps(metadata, ensure_ascii=False).encode('utf-8'))
    path = ROOT / name
    if creating:
        _unlinked(ROOT,directory=True)
        if path.exists() or path.is_symlink(): raise ValueError('동일한 원본 이름이 이미 있습니다.')
        _register_name(name)
    else:
        path, current, _ = _load(name)
        if current != before: raise ValueError('다른 편집으로 원본이 바뀌었습니다. 다시 읽고 수정하세요.')
    if deleting:
        path.unlink()
    elif creating:
        # Publish complete bytes without clobbering a concurrently created file.
        fd,temporary=tempfile.mkstemp(prefix='.new-source-',suffix='.tmp',dir=str(ROOT))
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(after);stream.flush();os.fsync(stream.fileno())
            os.link(temporary,path)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
    else:
        _atomic(path, after)
    metadata['status'] = 'saved'
    _atomic(folder / 'change.json', json.dumps(metadata, ensure_ascii=False).encode('utf-8'))
    diff = ''.join(difflib.unified_diff(before.decode('utf-8').splitlines(keepends=True),
                   after.decode('utf-8').splitlines(keepends=True), fromfile=name+' (before)', tofile=name+' (saved)'))
    return {'source_change':{k:metadata[k] for k in ('change_id','path','kind','sha256','status')},
            'validation':validation,
            'artifact':{'id':change_id, 'kind':'code', 'language':'diff', 'origin':'applied',
                        'title':name+' · 원본 저장됨', 'text':diff[:12000], 'truncated':len(diff)>12000}}


def publish_file(name, expected_sha256, after):
    """Server-owned baseline permits whole-file native patches without model hashes."""
    if not isinstance(after,bytes) or not after or len(after)>MAX_BYTES or b'\0' in after:
        raise ValueError('UTF-8 원본 크기와 내용을 확인하세요.')
    after.decode('utf-8')
    with LOCK:
        if expected_sha256 is None:
            if not _valid_new_name(name) or name in ALLOWED or (ROOT/name).exists():
                raise ValueError('새 원본 이름은 보호되지 않은 새 평면 파일이어야 합니다.')
            return _write_change(name,b'',after,creating=True)
        _,before,_=_load(name)
        if _hash(before)!=expected_sha256: raise ValueError('실행 원본이 바뀌었습니다. 작업 사본과 변경 내용을 비교하세요.')
        if before==after:return {'unchanged':True,'path':name}
        return _write_change(name,before,after)


def apply(selection, expected_sha256, old_text, new_text):
    if not all(isinstance(v,str) for v in (expected_sha256,old_text,new_text)):
        raise ValueError('수정 내용이 올바르지 않습니다.')
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256) or not old_text or old_text == new_text:
        raise ValueError('읽은 SHA256과 비어 있지 않은 정확한 수정 구간이 필요합니다.')
    if len(old_text) + len(new_text) > 23000 or '\x00' in old_text + new_text:
        raise ValueError('한 번의 수정 구간은 합계 24000자 미만이어야 합니다.')
    with LOCK:
        _, before, text = _load(selection['path'])
        if _hash(before) != expected_sha256:
            raise ValueError('원본이 바뀌었습니다. 다시 읽은 SHA256으로 수정하세요.')
        if text.count(old_text) != 1: raise ValueError('기존 문자열이 정확히 한 번 일치해야 합니다. 고유한 주변 문맥을 포함하세요.')
        after = text.replace(old_text, new_text, 1).encode('utf-8')
        if len(after) > MAX_BYTES: raise ValueError('수정 후 원본 크기 제한은 512KB입니다.')
        return _write_change(selection['path'], before, after)


def rollback(selection):
    with LOCK:
        folder, data = _record(selection['change_id'])
        if data['status'] != 'saved': raise ValueError('저장이 확인된 변경만 되돌릴 수 있습니다.')
        if data.get('deleted_file'):
            if (ROOT/data['path']).exists(): raise ValueError('삭제 이후 생성된 원본은 덮어쓰지 않습니다.')
            before=(folder/'before.bin').read_bytes()
            if _hash(before)!=data['before_sha256']:raise ValueError('백업 무결성을 확인할 수 없습니다.')
            return _write_change(data['path'],b'',before,rollback_of=data['change_id'],creating=True)
        _, current, _ = _load(data['path'])
        if _hash(current) != data['sha256']:
            raise ValueError('이후 변경이 있어 자동으로 덮어쓰지 않습니다. 현재 원본을 다시 확인하세요.')
        before_path = folder / 'before.bin'
        info = _unlinked(before_path)
        if info.st_size > MAX_BYTES: raise ValueError('백업 크기가 올바르지 않습니다.')
        before = before_path.read_bytes()
        if _hash(before) != data['before_sha256']: raise ValueError('백업 무결성을 확인할 수 없습니다.')
        return _write_change(data['path'], current, before, rollback_of=data['change_id'],deleting=bool(data.get('created_file')))


def restore_changes(change_ids):
    """Protected restart broker entry point; IDs come from its saved batch only.

    Reverse order lets multiple changes to the same file unwind. Every restore
    independently verifies the live hash; conflicts are reported, never forced.
    """
    restored, conflicts = [], []
    if not isinstance(change_ids, list) or len(change_ids) > 80:
        raise ValueError('변경 묶음이 올바르지 않습니다.')
    with LOCK:
        ordered=[]
        for change_id in set(change_ids):
            try:
                _,record=_record(change_id)
                ordered.append((record['created'],change_id))
            except (ValueError,OSError,KeyError) as error:
                conflicts.append({'change_id':str(change_id)[:32],'message':str(error)[:300]})
        # Concurrent requests may finish in a different order to their writes.
        for _,change_id in sorted(ordered,reverse=True):
            try:
                result = rollback({'change_id':change_id})
                restored.append({'original_change_id':change_id, **result['source_change']})
            except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
                conflicts.append({'change_id':str(change_id)[:32], 'message':str(error)[:300]})
    return {'restored':restored, 'conflicts':conflicts}
