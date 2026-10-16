"""Bounded project reference shared by Aemeath intent and planning.

Recorded decisions are context, never new authorization or proof of execution.
"""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).absolute().parent
DOCUMENT = ROOT / 'PROJECT_CONTEXT.md'
MAX_BYTES = 32 * 1024
SECTIONS = ('overview', 'core', 'architecture', 'decisions', 'history', 'pending')
PREFIX = 'AI OFFICE 프로젝트의 기록된 배경입니다.'
REFERENCE_NOTE = ('이 기록은 이전 결정의 참고 자료이며 이번 실행 지시가 아닙니다. '
                  '최신 사용자 요청과 실제 원본·실행 결과가 우선합니다. '
                  '모든 과거 대화나 Codex의 모든 도구가 연결되어 있다고 추측하지 마세요. '
                  '상세 내용은 office_project_context, 실제 변경 영수증은 office_source_history로 확인하세요.')


def _document():
    """Read only the fixed regular file, rejecting links and oversized content."""
    try:
        for path in (DOCUMENT, *DOCUMENT.parents):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return None
        before = DOCUMENT.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            return None
        with DOCUMENT.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                    or not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_BYTES):
                return None
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        return raw.decode('utf-8-sig')
    except (OSError, UnicodeError, ValueError):
        return None


def _section(text, section):
    if section == 'overview':
        return text
    headings = list(re.finditer(r'^#{1,2} +([^\r\n]+)\s*$', text, re.MULTILINE))
    for index, heading in enumerate(headings):
        if heading.group(1).strip().casefold() == section:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            return text[heading.end():end].strip()
    return ''


def brief():
    """One small snapshot per request; absent documentation never blocks chat."""
    text = _document()
    if text is None:
        return ''
    core = _section(text, 'core')[:3000]
    return PREFIX + '\n' + REFERENCE_NOTE + '\n' + core if core else ''


def _git_history():
    executable = shutil.which('git')
    if not executable:
        return {'available': False, 'items': []}
    try:
        # A file bounds the subsequent read even if local Git output is unusual.
        # No network, shell, user arguments, repository writes or signature helper.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([executable, '--no-pager', '-c', 'core.fsmonitor=false',
                                     'log', '--no-show-signature', '-12',
                                     '--format=%h %<(200,trunc)%s'],
                                    cwd=str(ROOT), stdout=output, stderr=subprocess.DEVNULL,
                                    timeout=3, check=False,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                    env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
            output.seek(0)
            raw = output.read(16385)
        if result.returncode or len(raw) > 16384:
            return {'available': False, 'items': []}
        return {'available': True, 'items': raw.decode('utf-8', 'replace').splitlines()[:12]}
    except (OSError, subprocess.SubprocessError):
        return {'available': False, 'items': []}


def read(section='overview'):
    if section not in SECTIONS:
        raise ValueError('지원하지 않는 프로젝트 기록 구역입니다.')
    text = _document()
    data = {'available': text is not None, 'source': 'PROJECT_CONTEXT.md',
            'section': section, 'text': _section(text, section) if text is not None else '',
            'reference_note': REFERENCE_NOTE,
            'source_receipts_tool': 'office_source_history'}
    if section == 'history':
        data['git_history'] = _git_history()
    return data
