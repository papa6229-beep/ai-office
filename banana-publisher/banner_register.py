"""Register explicit event banner selections through the BizHost form."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path


SLOTS = {
    111: ('PC', '검색창 우측', 178, 90),
    138: ('PC', '상품 우측 사이드', 250, 130),
    437: ('PC', '커뮤니티 큰 배너', 500, 387),
    329: ('PC', '이벤트 상단 스팟', 840, 640),
    325: ('PC', '카테고리 중간 긴 배너', 840, 78),
    108: ('PC', '최상단 단독', 1020, 70),
    47: ('모바일', '최상단', 602, 70),
    426: ('모바일', '메인 가운데 이벤트', 800, 250),
    427: ('모바일', '메인 중간 롤링', 800, 250),
}
BACKGROUND_SLOTS = {108, 47}
ADMIN_PATH = '/adminx/popups/nmain_data2.banana.php'
ADMIN_ORIGIN = 'https://banana.bizhost.kr'
MEDIA_ORIGIN = 'https://cdn-banana.bizhost.kr/files/new_main/'

READ = r'''({slot,link}) => {
    const form = document.querySelector('form#search[name="regiform"]');
    const rows = [...document.forms].find(f => f !== form && f.querySelector('input[name="cous"]'));
    const button = document.querySelectorAll('.btn_submit[data-form="#search"] > a');
    const value = (f,k) => f.elements.namedItem(k)?.value ?? '';
    if (!form || form.method !== 'post' || form.enctype !== 'multipart/form-data' ||
        form.getAttribute('action') !== '/adminx/popups/nmain_data2.banana.php?code=' ||
        value(form,'mode') !== 'w' || value(form,'index_no') !== String(slot) ||
        form.elements.namedItem('file')?.type !== 'file' || button.length !== 1 ||
        button[0].textContent.trim() !== '등록하기' || !rows) return null;
    return [...rows.querySelectorAll('input[name^="data_idx"]')].flatMap(el => {
        const n = el.name.slice(8);
        const savedLink = value(rows,'links'+n).trim();
        try {
            const url = new URL(savedLink, 'https://www.bananamall.co.kr');
            if (!['www.bananamall.co.kr','m.bananamall.co.kr','bananamall.co.kr'].includes(url.hostname) || url.pathname !== link) return [];
        } catch { return []; }
        const media = [...(rows.elements.namedItem('file'+n)?.closest('tr')?.querySelectorAll('img,a') ?? [])]
            .map(e => e.src || e.href).find(u => u?.startsWith('https://cdn-banana.bizhost.kr/files/new_main/'));
        return [{id:el.value, media:media??null, fields:{links:savedLink, text:value(rows,'text'+n), text2:value(rows,'text'+n+'_2'),
            target:value(rows,'target'+n), orders:value(rows,'orders'+n), isuse:value(rows,'isuse'+n),
            sdate:value(rows,'sdate'+n), edate:value(rows,'edate'+n)}}];
    });
}'''

FILL = r'''fields => {
    const form = document.querySelector('form#search[name="regiform"]');
    for (const [key,value] of Object.entries(fields)) {
        const field = form?.elements.namedItem(key);
        if (!field) return false;
        field.value = value;
        if (field.value !== value) return false;
    }
    return true;
}'''


def rows_for(page, slot, link):
    url = f'{ADMIN_ORIGIN}{ADMIN_PATH}?index_no={slot}'
    page.goto(url, timeout=25000)
    if page.url != url:
        raise ValueError('배너 등록 페이지 주소가 바뀌었습니다.')
    rows = page.evaluate(READ, {'slot': slot, 'link': link})
    if rows is None:
        raise ValueError('비즈호스트의 배너 등록 양식을 확인할 수 없습니다.')
    return rows


def matches(page, row, item):
    if row['fields'] != item['fields'] or not row['media'] or not row['media'].startswith(MEDIA_ORIGIN):
        return False
    response = page.request.get(row['media'], timeout=15000)
    return response.ok and hashlib.sha256(response.body()).hexdigest() == item['sha256']


def register(job, root):
    from ego_report_session import EgoCommandError, Session
    from report_automation import _login_biz

    db = str(root / 'office.db')
    results = []
    active = None
    try:
        with tempfile.TemporaryDirectory(prefix='banana-banner-ego-') as folder, Session(db, job['id'], folder) as session:
            try:
                page = _login_biz(session, db, lambda _: None)
                pending = []
                for item in job['items']:
                    slot, link = item['slot'], item['fields']['links']
                    rows = rows_for(page, slot, link)
                    if not rows:
                        pending.append(item)
                    elif len(rows) == 1 and matches(page, rows[0], item):
                        results.append({'slot': slot, 'state': 'existing'})
                    else:
                        return {'state': 'blocked', 'results': results, 'slot': slot,
                                'error': '같은 이벤트 주소의 기존 배너가 있습니다. 기존 등록을 확인하세요.'}
                for item in pending:
                    slot, fields = item['slot'], item['fields']
                    if rows_for(page, slot, fields['links']):
                        return {'state': 'blocked', 'results': results, 'slot': slot,
                                'error': '등록 준비 후 같은 이벤트 배너가 생겼습니다. 목록을 확인하세요.'}
                    file = Path(item['file'])
                    if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256'] or not page.evaluate(FILL, fields):
                        raise ValueError('배너 첨부 또는 입력값을 대조하지 못했습니다.')
                    page.call('set_banner_file', slot=slot, path=str(file), timeout=12000)
                    attached = page.evaluate(r'''({name,size,fields}) => {
                        const form = document.querySelector('form#search[name="regiform"]');
                        const files = form?.elements.namedItem('file')?.files;
                        return files?.length === 1 && files[0].name === name && files[0].size === size &&
                            Object.entries(fields).every(([key,value]) => form.elements.namedItem(key)?.value === value);
                    }''', {'name': file.name, 'size': file.stat().st_size, 'fields': fields})
                    if not attached:
                        raise ValueError('비즈호스트의 첨부 또는 입력값을 확인하지 못했습니다.')
                    active = slot
                    try:
                        page.call('submit_banner', slot=slot, timeout=26000)
                    except EgoCommandError:
                        if session.reconciliation_required:
                            raise
                    rows = rows_for(page, slot, fields['links'])
                    if len(rows) != 1 or not matches(page, rows[0], item):
                        return {'state': 'unconfirmed', 'results': results, 'slot': slot,
                                'error': '저장 뒤 등록 내용과 이미지 원본을 모두 대조하지 못했습니다.'}
                    results.append({'slot': slot, 'state': 'registered'})
                    active = None
                return {'state': 'completed', 'results': results}
            finally:
                if not session.reconciliation_required:
                    try:
                        session.finish()
                    except EgoCommandError:
                        pass
    except (OSError, ValueError, EgoCommandError):
        return {'state': 'unconfirmed' if active else 'blocked', 'results': results, 'slot': active,
                'error': '비즈호스트의 등록 결과를 확인해 주세요.' if active else '비즈호스트 입력을 확인하지 못해 다음 등록을 멈췄습니다.'}


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    print(json.dumps(register(json.load(sys.stdin), root), ensure_ascii=False))
