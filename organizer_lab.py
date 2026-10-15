"""Generated file sandbox for exercising the organizer without touching user data."""
import base64
import os
import pathlib
import re
import threading
import uuid

import desktop_workspace
import file_executor

ROOT = pathlib.Path(__file__).resolve().parent / 'organizer_lab'
LOCK = threading.Lock()
MAX_SESSIONS = 30
ID = re.compile(r'^[0-9a-f]{32}$')
SAMPLES = {
    '행사_메모.txt': '행사 준비를 위한 시험 메모입니다.\n'.encode('utf-8'),
    '참가자_목록.csv': '이름,역할\n샘플,기획\n'.encode('utf-8'),
    '작업_설명.md': '# 정리 시험\n앱이 만든 파일입니다.\n'.encode('utf-8'),
    '홍보_이미지.png': base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XcAAAAASUVORK5CYII='),
    '연결자료_확인.ai': b'AI OFFICE generated placeholder; linked assets need inspection.',
}


def _safe_directory(path):
    if not path.is_dir() or not desktop_workspace._safe_route(path):
        raise ValueError('시험 정리 폴더가 없거나 링크로 바뀌었습니다.')


def _journal(job_id):
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise ValueError('정리 기록 번호를 확인하세요.')
    _safe_directory(ROOT)
    folder = ROOT / job_id
    _safe_directory(folder)
    _safe_directory(folder / 'inbox')
    _safe_directory(folder / 'organized')
    journal = folder / 'journal.json'
    if not journal.is_file() or not desktop_workspace._safe_route(journal):
        raise ValueError('정리 기록을 읽을 수 없습니다.')
    return journal


def _checked(job_id):
    journal = _journal(job_id)
    job = file_executor.load(journal)
    expected = journal.parent
    if (os.path.normcase(job['source_dir']) != os.path.normcase(str(expected / 'inbox'))
            or os.path.normcase(job['destination_dir']) != os.path.normcase(str(expected / 'organized'))):
        raise ValueError('기록의 정리 범위가 시험 폴더와 다릅니다.')
    # Session and executor IDs may differ. Public IDs always name trusted lab folders.
    return journal, job


def _public(job_id, job):
    result = dict(job)
    result['executor_id'] = result['id']
    result['id'] = job_id
    result['scope'] = 'generated_demo'
    return result


def latest():
    with LOCK:
        if not ROOT.exists():
            return None
        _safe_directory(ROOT)
        candidates = []
        for folder in ROOT.iterdir():
            if ID.fullmatch(folder.name):
                _safe_directory(folder)
                journal = folder / 'journal.json'
                if journal.exists():
                    candidates.append((journal.lstat().st_mtime_ns, folder.name))
        if not candidates:
            return None
        job_id = max(candidates)[1]
        _, job = _checked(job_id)
        return _public(job_id, job)


def create():
    with LOCK:
        if not ROOT.exists():
            _safe_directory(ROOT.parent)
            ROOT.mkdir(exist_ok=True)
        _safe_directory(ROOT)
        if len(list(ROOT.iterdir())) >= MAX_SESSIONS:
            raise ValueError('시험 기록 30개가 보관돼 있습니다. 새 시험은 여기서 멈춥니다.')
        job_id = uuid.uuid4().hex
        folder = ROOT / job_id
        folder.mkdir()
        source, destination = folder / 'inbox', folder / 'organized'
        source.mkdir()
        destination.mkdir()
        for name, content in SAMPLES.items():
            with (source / name).open('xb') as stream:
                stream.write(content)
        job = file_executor.plan(source, destination, folder / 'journal.json')
        return _public(job_id, job)


def execute(job_id):
    with LOCK:
        journal, _ = _checked(job_id)
        return _public(job_id, file_executor.execute(journal))


def undo(job_id):
    with LOCK:
        journal, _ = _checked(job_id)
        return _public(job_id, file_executor.undo(journal))
