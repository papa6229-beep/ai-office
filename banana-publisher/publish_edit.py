"""Publish event changes through the existing Ego/BizHost editor."""

import json
import re
import sys
import tempfile
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ego_report_session import EgoCommandError, Session
from report_automation import _login_biz


SKINS = {'web': '20', 'mobile': '21'}
BODY_START = '<!-- 메인 본문 이벤트 내용 start -->'
BODY_END = '<!-- 메인 본문 이벤트 내용 end-->'
IMAGE_ROOTS = ('{global.imgdomain}', 'https://cdn-banana.bizhost.kr',
               'http://cdn-banana.bizhost.kr', '//cdn-banana.bizhost.kr')
ATTRIBUTE = re.compile(r'''([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')
READ_EDITOR = '''() => {
    const form = document.querySelector('#regiform');
    const button = document.querySelectorAll('.btn_save[data-form="#regiform"][data-savetype="save"] > a');
    if (!form || form.getAttribute('action') !== '/adminx/newdesign.php?code=editor' || button.length !== 1
        || button[0].parentElement.dataset.answer !== '저장하시겠습니까? (※연결사이트로 즉시반영됩니다.)') return null;
    const keys = ['htm', 'js', 'css'];
    if (keys.some(key => !document.querySelector('#editor_' + key)?.env?.editor
        || !form.elements['load_hash_' + key]?.value)) return null;
    return {name: form.elements.basename.value, skin: form.elements.skin_idx.value,
        values: keys.map(key => document.querySelector('#editor_' + key).env.editor.getValue())};
}'''


class UnsafeEdit(ValueError):
    pass


class ImageEdit(HTMLParser):
    def __init__(self, source, target):
        super().__init__()
        self.old_path = urlsplit(target['preview']).path
        self.new_path = urlsplit(target.get('replacement', '')).path
        self.lines = [0, *(match.end() for match in re.finditer('\n', source))]
        self.picture_depth = 0
        self.edits = []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'picture':
            self.picture_depth += 1
        if tag != 'img':
            return
        raw = self.get_starttag_text()
        for attr in ATTRIBUTE.finditer(raw, 4):
            if attr[1].lower() != 'src':
                continue
            value_group = next((group for group in (2, 3, 4) if attr[group] is not None), None)
            if value_group is None:
                continue
            value = unescape(attr[value_group])
            root = next((prefix for prefix in IMAGE_ROOTS if value.startswith(prefix + '/')), None)
            if root is None or urlsplit(value[len(root):]).path != self.old_path:
                continue
            if self.picture_depth or any(key in {'srcset', 'data-src', 'data-srcset', 'data-lazy-src'} for key, _ in attrs):
                raise UnsafeEdit('이 이미지는 반응형 또는 지연 로딩 주소를 함께 사용합니다. 저장하지 않았습니다.')
            if sum(key == 'src' for key, _ in attrs) != 1:
                raise UnsafeEdit('이미지 태그의 주소가 중복되어 저장하지 않았습니다.')
            line, column = self.getpos()
            offset = self.lines[line - 1] + column
            if self.new_path:
                start, end = attr.span(value_group)
                self.edits.append((offset + start, offset + end, root + self.new_path))
            else:
                self.edits.append((offset, offset + len(raw), ''))

    def handle_endtag(self, tag):
        if tag == 'picture' and self.picture_depth:
            self.picture_depth -= 1

    def update(self, source):
        if len(self.edits) != 1:
            raise UnsafeEdit('현재 편집기에서 선택한 이미지 태그를 한 곳으로 확인하지 못했습니다. 저장하지 않았습니다.')
        start, end, value = self.edits[0]
        return source[:start] + value + source[end:]


def editor_source(page, job, skin):
    url = job['editors'][skin]
    page.goto(url)
    if page.url != url:
        raise UnsafeEdit('비즈호스트가 지정한 이벤트 편집 화면을 열지 않았습니다.')
    page.locator('#editor_htm').wait_for()
    value = page.evaluate(READ_EDITOR)
    if not value or value['name'] != '/etc/' + job['event'] or value['skin'] != SKINS[skin]:
        raise UnsafeEdit('편집 화면의 이벤트 파일이나 스킨이 선택과 일치하지 않습니다.')
    return value['values']


def replace_field(source, pattern, value):
    updated, matches = re.subn(pattern, lambda match: match[1] + value + (match[2] or ''), source, flags=re.MULTILINE)
    if matches != 1:
        raise UnsafeEdit('기준 템플릿에서 행사 제목 또는 검색 설명 위치를 확인하지 못했습니다.')
    return updated


def registration_html(source, registration, skin):
    if source.count(BODY_START) != 1 or source.count(BODY_END) != 1:
        raise UnsafeEdit('기준 템플릿의 행사 본문 경계를 확인하지 못했습니다.')
    header, _, body = source.partition(BODY_START)
    body, marker, footer = body.partition(BODY_END)
    root = '<div class="contents">' if skin == 'web' else '<div id="contents">'
    container = re.fullmatch(r'(\s*' + re.escape(root) + r'[^\S\n]*\n).*?(\n([ \t]*)</div>\s*)', body, re.DOTALL)
    if not marker or not container:
        raise UnsafeEdit('기준 템플릿에서 행사 본문 div를 확인하지 못했습니다.')

    title = escape(registration['title'])
    description = escape(registration['description'])
    for key, value in (('global.site_title', title + ' | 성인용품 바나나몰'), ('in_description', description)):
        field = r"^([ \t]*\{" + re.escape(key) + r"\}[ \t]*=[ \t]*')(?:\\.|[^'\\\r\n])*('[ \t]*;)"
        header = replace_field(header, field, value)
    if skin == 'mobile':
        header = replace_field(header, r'(^[ \t]*<h1 class="title">)[^<\r\n]*(</h1>)?$', title)

    indent = container[3] + '\t'
    lines = []
    image_group = 0
    group_open = False
    for item in registration['media']:
        path = escape(urlsplit(item['url']).path, quote=True)
        url = '{global.imgdomain}' + path
        if item['kind'] == 'image':
            if not group_open:
                image_group += 1
                lines.append(f'{indent}<div class="con_box con{image_group:02d}">')
                group_open = True
            lines.append(f'{indent}\t<img src="{url}" alt="{title}">')
        else:
            if group_open:
                lines.append(f'{indent}</div>')
                group_open = False
            lines.extend([
                f'{indent}<div class="video_box">',
                f'{indent}\t<video controls playsinline preload="metadata">',
                f'{indent}\t\t<source src="{url}" type="video/mp4">',
                f'{indent}\t</video>',
                f'{indent}</div>',
            ])
    if group_open:
        lines.append(f'{indent}</div>')
    return header + BODY_START + container[1] + '\n'.join(lines) + container[2] + BODY_END + footer


def registration_plan(page, job):
    registration = job['registration']
    template = {'event': registration['template_event'], 'editors': registration['template_editors']}
    plan = {}
    for skin in SKINS:
        source, script, css = editor_source(page, template, skin)
        before = editor_source(page, job, skin)
        html = registration_html(source, registration, skin)
        plan[skin] = (before, [html, script, css])
    backup = ROOT / 'local_backups' / 'banana-publisher' / 'restore'
    backup.mkdir(parents=True, exist_ok=True)
    with (backup / (job['id'] + '.json')).open('x', encoding='utf-8') as stream:
        json.dump({'event': job['event'], 'skins': {skin: before for skin, (before, _) in plan.items()}},
                  stream, ensure_ascii=False)
    return plan


def publish(job):
    db = str(ROOT / 'office.db')
    saved = []
    writing = False
    try:
        with tempfile.TemporaryDirectory(prefix='banana-event-') as folder, Session(db, job['id'], folder) as session:
            try:
                page = _login_biz(session, db, lambda _: None)
                if job.get('registration'):
                    plan = registration_plan(page, job)
                else:
                    plan = {}
                    for skin in job['target']['skins']:
                        before = editor_source(page, job, skin)
                        changed = ImageEdit(before[0], job['target']).update(before[0])
                        plan[skin] = (before, [changed, *before[1:]])
                for skin, (before, expected) in plan.items():
                    if editor_source(page, job, skin) != before:
                        raise UnsafeEdit('게시 준비 중 편집기 내용이 바뀌었습니다.')
                    applied = page.evaluate('''values => ['htm', 'js', 'css'].map((key, index) => {
                        const ace = document.querySelector('#editor_' + key).env.editor;
                        if (ace.getValue() !== values[index]) ace.setValue(values[index], -1);
                        return ace.getValue() === values[index];
                    }).every(Boolean)''', expected)
                    if not applied:
                        raise UnsafeEdit('편집기에 적용한 HTML을 대조하지 못했습니다.')
                    writing = True
                    try:
                        page.call('publish_event', timeout=25000)
                    except EgoCommandError:
                        if session.reconciliation_required:
                            raise
                    if editor_source(page, job, skin) != expected:
                        return dict(state='unconfirmed', skins=saved,
                                    error='저장 후 편집기 내용이 예상과 다릅니다. 현재 페이지를 먼저 확인해 주세요.')
                    saved.append(skin)
                return dict(state='saved', skins=saved)
            finally:
                if not session.reconciliation_required:
                    try:
                        session.finish()
                    except EgoCommandError:
                        pass
    except UnsafeEdit as error:
        if not writing:
            return dict(state='blocked', error=str(error))
    except Exception:
        if not writing:
            return dict(state='blocked', error='비즈호스트 로그인 또는 편집 화면을 확인하지 못해 저장을 시작하지 않았습니다.')
    return dict(state='unconfirmed', skins=saved, error='저장한 범위를 모두 확인하지 못했습니다. 현재 페이지를 먼저 확인해 주세요.')


if __name__ == '__main__':
    print(json.dumps(publish(json.load(sys.stdin)), ensure_ascii=False))
