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
LOCK_STANDARD = ('1732509267_1.jpg', 'fa92c9d66853850e905fd331b6b116e10e9713e7d83e2b148c784f7084e64b31')
LOCK_MOBILE_800 = ('1793351345_1.jpg', '529d40fd2a4bcf67b37d9bbb7050ee52e2f060e6d986e4fbf9c7739cef22fad6')
# Each source is the existing white +19 lock, identified in that slot's secondary-file cell.
AUXILIARY = {
    111: LOCK_STANDARD,
    138: ('1747963671_1.jpg', 'f473c891df52defe6f72f3bf89c03f9e8e8b495110fc2c46afd6f2de8e74c144'),
    437: LOCK_STANDARD,
    329: LOCK_STANDARD,
    325: ('1747962021_1.jpg', '69275ea58fa7587767c3b5f73a519bce08f057c3037cba96c175d9aeb813c296'),
    108: ('1747961904_1.gif', '1dd67b37351aa4867ea484d12a035d772c80903b8c8d463be83a14517e10cb6b'),
    47: ('1747961728_1.gif', 'e0116b405f960e5ae0f1fa032a684c441bda89135d87148f74bad3d6547a8bd9'),
    426: LOCK_MOBILE_800,
    427: LOCK_MOBILE_800,
}

READ = r'''({slot,link}) => {
    const form = document.querySelector('form#search[name="regiform"]');
    const rows = [...document.forms].find(f => f !== form && f.querySelector('input[name="cous"]'));
    const button = document.querySelectorAll('.btn_submit[data-form="#search"] > a');
    const value = (f,k) => f.elements.namedItem(k)?.value ?? '';
    if (!form || form.method !== 'post' || form.enctype !== 'multipart/form-data' ||
        form.getAttribute('action') !== '/adminx/popups/nmain_data2.banana.php?code=' ||
        value(form,'mode') !== 'w' || value(form,'index_no') !== String(slot) ||
        form.elements.namedItem('file')?.type !== 'file' || form.elements.namedItem('file2')?.type !== 'file' || button.length !== 1 ||
        button[0].textContent.trim() !== '등록하기' || !rows) return null;
    return [...rows.querySelectorAll('input[name^="data_idx"]')].flatMap(el => {
        const n = el.name.slice(8);
        const savedLink = value(rows,'links'+n).trim();
        try {
            const url = new URL(savedLink, 'https://www.bananamall.co.kr');
            if (!['www.bananamall.co.kr','m.bananamall.co.kr','bananamall.co.kr'].includes(url.hostname) || url.pathname !== link) return [];
        } catch { return []; }
        const fileUrl = name => [...(rows.elements.namedItem(name)?.closest('td')?.querySelectorAll('img,a') ?? [])]
            .map(e => e.src || e.href).find(u => u?.startsWith('https://cdn-banana.bizhost.kr/files/new_main/')) ?? null;
        return [{id:el.value, media:fileUrl('file'+n), auxiliary:fileUrl('files'+n),
            fields:{links:savedLink, text:value(rows,'text'+n), text2:value(rows,'text'+n+'_2'),
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
    if row['fields'] != item['fields']:
        return False
    return (read_image(page, row['media'], item['sha256']) is not None
            and read_image(page, row['auxiliary'], AUXILIARY[item['slot']][1]) is not None)


def read_image(page, url, expected):
    if not url or not url.startswith(MEDIA_ORIGIN):
        return None
    response = page.request.get(url, timeout=15000)
    if not response.ok:
        return None
    data = response.body()
    return data if hashlib.sha256(data).hexdigest() == expected else None


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
                auxiliary_files = {}
                for item in pending:
                    slot = item['slot']
                    name, sha256 = AUXILIARY[slot]
                    path = Path(folder) / name
                    if not path.exists():
                        data = read_image(page, MEDIA_ORIGIN + name, sha256)
                        if data is None:
                            return {'state': 'blocked', 'results': results, 'slot': slot,
                                    'error': '흰색 자물쇠 보조 이미지 원본을 확인할 수 없습니다.'}
                        path.write_bytes(data)
                    auxiliary_files[slot] = path
                for item in pending:
                    slot, fields = item['slot'], item['fields']
                    if rows_for(page, slot, fields['links']):
                        return {'state': 'blocked', 'results': results, 'slot': slot,
                                'error': '등록 준비 후 같은 이벤트 배너가 생겼습니다. 목록을 확인하세요.'}
                    file = Path(item['file'])
                    auxiliary = auxiliary_files[slot]
                    if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256'] or not page.evaluate(FILL, fields):
                        raise ValueError('배너 첨부 또는 입력값을 대조하지 못했습니다.')
                    page.call('set_banner_file', slot=slot, path=str(file), auxiliary=str(auxiliary), timeout=24000)
                    attached = page.evaluate(r'''({files,fields}) => {
                        const form = document.querySelector('form#search[name="regiform"]');
                        const attached = files.every(({key,name,size}) => {
                            const input = form?.elements.namedItem(key)?.files;
                            return input?.length === 1 && input[0].name === name && input[0].size === size;
                        });
                        return attached &&
                            Object.entries(fields).every(([key,value]) => form.elements.namedItem(key)?.value === value);
                    }''', {'files': [{'key': key, 'name': path.name, 'size': path.stat().st_size}
                                    for key, path in (('file', file), ('file2', auxiliary))], 'fields': fields})
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
