"""Use local OCR and the existing authenticated Ego product/editor connection."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener, ProxyHandler

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ego_report_session import Session
from report_automation import _login_biz
from product_links import proposals, match_products, linked_html, linked_css


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('이미지 또는 이벤트 주소가 다른 위치로 이동했습니다.')


def download_images(images, folder, expected=False):
    if not 1 <= len(images) <= 40:
        raise ValueError('분석할 이벤트 이미지를 1~40개로 확인하세요.')
    result, inputs = [], []
    total = 0
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for index, item in enumerate(images):
        url = item.get('url') or item.get('preview')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'cdn-banana.bizhost.kr' or '/etc/' not in parsed.path:
            raise ValueError('이벤트의 실제 CDN 이미지만 분석합니다.')
        with opener.open(Request(url, headers={'Cache-Control': 'no-cache'}), timeout=20) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
        total += len(raw)
        if len(raw) > 20 * 1024 * 1024 or total > 100 * 1024 * 1024:
            raise ValueError('분석 이미지 크기가 허용 범위를 넘었습니다.')
        sha256 = hashlib.sha256(raw).hexdigest()
        if expected and sha256 != item['sha256']:
            raise ValueError('분석 후 이벤트 이미지가 변경되었습니다. 다시 분석하세요.')
        source = folder / f'{index}.source'
        source.write_bytes(raw)
        with Image.open(source) as image:
            if image.width * image.height > 60_000_000:
                raise ValueError('이미지 픽셀 크기가 허용 범위를 넘었습니다.')
            width, height = image.size
            image.seek(0)
            # Temporary OCR copy. Published and uploaded image bytes stay intact.
            copy = image.convert('RGB')
            copy.thumbnail((2400, 2400))
            path = folder / f'{index}.png'
            copy.save(path)
            scale_x, scale_y = width / copy.width, height / copy.height
        result.append(dict(path=parsed.path, url=url, width=width, height=height,
                           sha256=sha256, skins=item['skins']))
        inputs.append(dict(id=str(index), path=str(path), scale_x=scale_x, scale_y=scale_y))
    return result, inputs


def recognize(inputs, folder):
    request, output = folder / 'ocr-input.json', folder / 'ocr-output.json'
    request.write_text(json.dumps(inputs), encoding='utf-8')
    powershell = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(powershell), '-NoProfile', '-File', str(Path(__file__).with_name('product_links_ocr.ps1')),
                             '-InputPath', str(request), '-ResultPath', str(output)], capture_output=True,
                            timeout=150, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not output.is_file():
        raise ValueError('이 PC의 한국어 이미지 문자 인식 연결을 확인하세요.')
    results = json.loads(output.read_text('utf-8-sig'))
    for row, settings in zip(results, inputs):
        for line in row['lines']:
            for word in line['words']:
                for key in ('x', 'width'):
                    word[key] *= settings['scale_x']
                for key in ('y', 'height'):
                    word[key] *= settings['scale_y']
    return results


COLLECT = r'''() => {
 const form=document.forms.listform;
 if(!form)throw Error('상품 목록을 확인하지 못했습니다.');
 const total=document.body.innerText.match(/상품은\s*총\s*([\d,]+)\s*개/);
 if(!total)throw Error('검색 결과 개수가 없습니다.');
 const rows=[...form.querySelectorAll('a[href*="code=g1_2_4"]')].map(a=>{
  const tr=a.closest('tr'), cells=[...tr.children].map(c=>c.innerText.trim());
  if(cells.length<12)throw Error('상품 목록 열이 변경되었습니다.');
  const id=new URL(a.href).searchParams.get('index_no');
  const match=cells[10].match(/^([\d,]+)원/);
  if(!/^\d+$/.test(id||'')||!match)throw Error('상품 번호와 가격을 확인하지 못했습니다.');
  return {id,name:a.innerText.trim(),price:Number(match[1].replaceAll(',','')),
   selling:cells[11].includes('판매중')&&!cells[11].includes('진열안함'),
   thumbnail:tr.querySelector('a[href*="/shop/view.php"] img')?.src,
   url:'https://www.bananamall.co.kr/shop/view.php?index_no='+id};
 });
 return {total:Number(total[1].replaceAll(',','')),rows};
}'''


def catalog(page, queries):
    rows = {}
    for query in sorted(set(queries)):
        seen = set()
        total = None
        for number in range(1, 11):
            params = dict(code='g1_2', fid='1', key='gname', keyword=query, numper='40', page=number,
                          seasonall='Y', showtype='cates', sortcol='regi_date', sortby='desc')
            page.goto('https://banana.bizhost.kr/adminx/goods.php?' + urlencode(params))
            if page.locator('form[name=search] input[name=keyword]').input_value() != query:
                raise ValueError('비즈호스트 상품 검색어가 일치하지 않습니다.')
            data = page.evaluate(COLLECT)
            if total is not None and data['total'] != total:
                raise ValueError('조회 도중 상품 목록이 변경되었습니다.')
            total = data['total']
            previous = len(seen)
            for row in data['rows']:
                seen.add(row['id'])
                if row.pop('selling') and row['price'] > 0:
                    rows[row['id']] = row
            if len(seen) >= total:
                break
            if len(seen) == previous:
                raise ValueError('상품 검색 결과가 일부 누락되었습니다.')
        else:
            raise ValueError('검색 결과가 너무 많습니다. 상품명을 좁혀 확인하세요.')
    return list(rows.values())


def analyze(job):
    with tempfile.TemporaryDirectory(prefix='banana-link-analysis-') as folder:
        folder = Path(folder)
        images, inputs = download_images(job['images'], folder)
        text = recognize(inputs, folder)
        regions = [region for image, item in zip(images, text) for region in proposals(image, item['lines'])]
        if not regions:
            raise ValueError('상품명과 가격이 함께 있는 영역을 찾지 못했습니다. 원본 이미지 해상도를 확인하세요.')
        brands = {region['label'].split()[0] for region in regions}
        if len(brands) > 12:
            raise ValueError('상품명 인식 후보가 너무 많습니다. 이미지를 나누어 확인하세요.')
        with Session(str(ROOT / 'office.db'), job['id'], folder / 'ego') as session:
            try:
                page = _login_biz(session, str(ROOT / 'office.db'), lambda _: None)
                products = catalog(page, brands)
                regions = match_products(regions, products)
            finally:
                if not session.reconciliation_required:
                    session.finish()
        return dict(state='ready', images=images, regions=regions,
                    stage=f'상품 영역 {len(regions)}개를 찾았습니다. 상품과 연결 범위를 확인하세요.')


def publication_plan(page, job):
    from publish_edit import editor_source, UnsafeEdit
    with tempfile.TemporaryDirectory(prefix='banana-link-recheck-') as folder:
        download_images(job['images'], Path(folder), expected=True)
    brands = {row['product']['name'].split(']')[-1].strip().split()[0] for row in job['selected']}
    actual = {row['id']: row for row in catalog(page, brands)}
    for row in job['selected']:
        product = actual.get(row['product_id'])
        if not product or product['name'] != row['product']['name'] or product['price'] != row['product']['price']:
            raise UnsafeEdit('상품명·가격·판매 상태가 분석 후 변경되었습니다. 다시 분석하세요.')
    plan = {}
    for skin in ('web', 'mobile'):
        before = editor_source(page, job, skin)
        selected = [row for row in job['selected'] if any(image['path'] == row['image_path'] and skin in image['skins'] for image in job['images'])]
        if not selected:
            raise UnsafeEdit('PC와 모바일에 연결할 상품 영역을 확인하세요.')
        expected = [linked_html(before[0], job['images'], selected, skin), before[1], linked_css(before[2])]
        plan[skin] = before, expected
    backup = ROOT / 'local_backups/banana-publisher/product-links'
    backup.mkdir(parents=True, exist_ok=True)
    with (backup / (job['id'] + '.json')).open('x', encoding='utf-8') as stream:
        json.dump(dict(event=job['event'], skins={s: before for s, (before, _) in plan.items()},
                       images=job['images'], selected=job['selected']), stream, ensure_ascii=False)
    return plan


def verify_public(job):
    from html.parser import HTMLParser
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.current = None
            self.rows = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'img':
                self.current = urlsplit(attrs.get('src', '')).path
            if tag == 'a' and attrs.get('class') == 'banana-product-link':
                self.rows.append((self.current, attrs))
    verified = []
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for skin, host, agent in [('web', 'www', 'Mozilla/5.0 Windows'), ('mobile', 'm', 'Mozilla/5.0 (iPhone) Mobile')]:
        with opener.open(Request(f'https://{host}.bananamall.co.kr/etc/{job["event"]}.php',
                                  headers={'User-Agent': agent, 'Cache-Control': 'no-cache'}), timeout=20) as result:
            raw = result.read(4 * 1024 * 1024).decode(result.headers.get_content_charset() or 'utf-8', errors='replace')
        parser = Links()
        parser.feed(raw)
        expected = [row for row in job['selected'] if any(image['path'] == row['image_path'] and skin in image['skins'] for image in job['images'])]
        wanted = {(row['image_path'], row['product_id']) for row in expected}
        observed = {(path, row.get('data-product-id')) for path, row in parser.rows
                    if row.get('href') == f'https://{host}.bananamall.co.kr/shop/view.php?index_no={row.get("data-product-id")}'
                    and row.get('target') == '_blank' and 'noopener' in row.get('rel', '')}
        if observed != wanted or len(parser.rows) != len(expected):
            raise ValueError(f'{skin} 공개 링크의 개수 또는 목적지를 아직 확인하지 못했습니다.')
        verified.append(skin)
    return dict(state='published', skins=verified, link_count=len(job['selected']), stage='PC·모바일 상품 링크가 공개 페이지에 반영되었습니다.')


def run(job):
    if job['action'] == 'analyze':
        return analyze(job)
    if job['action'] == 'publish':
        from publish_edit import publish
        result = publish(dict(job, product_links=True))
        if result['state'] != 'saved':
            return result
        for attempt in range(4):
            try:
                return verify_public(job)
            except (OSError, ValueError):
                if attempt < 3:
                    time.sleep(2)
        return dict(state='unconfirmed', skins=result['skins'], error='스킨 저장은 확인했습니다. 공개 페이지의 링크 반영을 확인해야 합니다.')
    raise ValueError('상품 링크 작업을 확인하세요.')


if __name__ == '__main__':
    try:
        print(json.dumps(run(json.load(sys.stdin)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps(dict(state='failed', error=str(error)[:300] if isinstance(error, ValueError)
                              else '상품 링크 분석을 완료하지 못했습니다. 로그인과 이미지 연결을 확인하세요.'), ensure_ascii=False))
