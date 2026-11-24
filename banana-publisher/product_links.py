"""Image-relative event links, local OCR proposals and durable publishing jobs."""
from contextlib import closing
from difflib import SequenceMatcher
from html import escape
import json
import math
import re
import threading
import time
import unicodedata
import uuid
from urllib.parse import urlsplit

START = '<!-- banana-product-links:start -->'
END = '<!-- banana-product-links:end -->'
CSS = '''/* banana-product-links:start */
.banana-product-image{position:relative;display:block;width:100%;line-height:0}
.banana-product-image>img{display:block;width:100%;height:auto}
.banana-product-image>a.banana-product-link{position:absolute;display:block;z-index:1;background:transparent;box-sizing:border-box;line-height:normal}
.banana-product-image>a.banana-product-link:focus-visible{outline:3px solid #184eff;outline-offset:-3px;background:rgba(24,78,255,.12)}
/* banana-product-links:end */'''


def normalized(value):
    value = re.sub(r'\[[^]]*\]', '', value).split('(', 1)[0]
    value = value.lower().replace('오수가', 'osuga').replace('위바이브', 'wevibe')
    value = value.replace('싱크 오', '싱크o').replace('싱크 고', '싱크go')
    value = re.sub(r'[^a-z0-9가-힣]', '', value)
    return unicodedata.normalize('NFKD', value)


def rectangle(words):
    left = min(word['x'] for word in words)
    top = min(word['y'] for word in words)
    return dict(x=left, y=top, width=max(w['x'] + w['width'] for w in words) - left,
                height=max(w['y'] + w['height'] for w in words) - top)


def proposals(image, lines):
    """Find a product title above each price, including multi-column cards."""
    width, height = image['width'], image['height']
    lines = [dict(line, **rectangle(line['words'])) for line in lines if line.get('words')]
    titles = [line for line in lines if re.match(r'^[가-힣]{2,}\s+[가-힣A-Za-z0-9]', line['text'])
              and not re.search(r'[#원%]|(?:이벤트|방법|주의|상품금액)', line['text'])
              and line['height'] >= max(16, width * .025)]
    buttons = [line for line in lines if re.search(r'보러\s*가기|바로\s*가기|상품\s*보기|구매\s*하기', line['text'])]
    found = {}
    for price in lines:
        amounts = re.findall(r'[\d][\d,.]{3,}\s*원', price['text'])
        if not amounts:
            continue
        # Use the rightmost amount when crossed-out and discounted prices share a line.
        value = int(re.sub(r'\D', '', amounts[-1]))
        column = 0 if price['x'] + price['width'] / 2 < width / 2 else 1
        nearby = [title for title in titles if 0 < price['y'] - title['y'] < min(340, height * .35)
                  and (0 if title['x'] + title['width'] / 2 < width / 2 else 1) == column]
        if not nearby:
            continue
        title = max(nearby, key=lambda row: row['y'])
        # A single recognized title owns all prices above its button.
        key = (title['text'], title['x'], title['y'])
        matching = [button for button in buttons if 0 < button['y'] - price['y'] < 150
                    and (0 if button['x'] + button['width'] / 2 < width / 2 else 1) == column]
        button = min(matching, key=lambda row: row['y']) if matching else None
        paired = any(abs(other['y'] - title['y']) < 30 and abs(other['x'] - title['x']) > width * .3 for other in titles)
        left = max(0, title['x'] - 22)
        right = width * .49 if paired and column == 0 else width * .96
        bottom = button['y'] + button['height'] + 20 if button else price['y'] + price['height'] + 48
        found[key] = dict(image_path=image['path'], label=title['text'], event_price=value,
                          x=round(left / width, 6), y=round(max(0, title['y'] - 16) / height, 6),
                          width=round((right - left) / width, 6),
                          height=round(min(height - title['y'] + 16, bottom - title['y'] + 16) / height, 6),
                          status='review', product_id=None, candidates=[])
    return sorted(found.values(), key=lambda row: (row['y'], row['x']))


def match_products(regions, products):
    for region in regions:
        query = normalized(region['label'])
        candidates = []
        for product in products:
            name = normalized(product['name'])
            similarity = 1.0 if name.startswith(query) and len(query) >= 10 else SequenceMatcher(None, query, name).ratio()
            if similarity < .57:
                continue
            price_match = region['event_price'] in {product['price'], round(product['price'] * .8)}
            score = similarity * .85 + (.15 if price_match else 0)
            candidates.append(dict(product, score=round(score, 4), name_score=round(similarity, 4), price_match=price_match))
        candidates.sort(key=lambda row: row['score'], reverse=True)
        region['candidates'] = candidates[:5]
        if candidates:
            first = candidates[0]
            margin = first['score'] - candidates[1]['score'] if len(candidates) > 1 else 1
            if first['name_score'] >= .77 and first['price_match'] and margin >= .045:
                region.update(product_id=first['id'], status='matched')
    return regions


def validated_regions(images, regions, proposed):
    if not isinstance(proposed, list) or not 1 <= len(proposed) <= 150:
        raise ValueError('연결할 상품 영역을 한 개 이상 확인하세요.')
    allowed = {}
    for region in regions:
        allowed.setdefault(region['image_path'], {}).update({p['id']: p for p in region['candidates']})
    actual = {image['path'] for image in images}
    result = []
    for row in proposed:
        if not isinstance(row, dict) or row.get('image_path') not in actual:
            raise ValueError('분석한 원본 이미지의 영역만 게시할 수 있습니다.')
        product = allowed.get(row['image_path'], {}).get(row.get('product_id'))
        if product is None:
            raise ValueError('검색 결과에서 연결할 상품을 선택하세요.')
        numbers = [row.get(k) for k in ('x', 'y', 'width', 'height')]
        if any(type(v) not in {int, float} or not math.isfinite(v) for v in numbers):
            raise ValueError('상품 영역의 위치를 확인하세요.')
        x, y, width, height = numbers
        if min(x, y) < 0 or min(width, height) <= .002 or x + width > 1.000001 or y + height > 1.000001:
            raise ValueError('상품 영역이 이미지 밖에 있습니다.')
        for previous in result:
            if previous['image_path'] == row['image_path']:
                intersect = max(0, min(x + width, previous['x'] + previous['width']) - max(x, previous['x'])) * max(0, min(y + height, previous['y'] + previous['height']) - max(y, previous['y']))
                if intersect > min(width * height, previous['width'] * previous['height']) * .15:
                    raise ValueError('상품 클릭 영역이 겹칩니다. 미리보기에서 영역을 조정하세요.')
        result.append(dict(image_path=row['image_path'], x=x, y=y, width=width, height=height,
                           product_id=product['id'], label=product['name'], product=product))
    return result


def linked_html(source, images, regions, skin):
    from publish_edit import BODY_START, BODY_END, IMAGE_ROOTS, UnsafeEdit
    if source.count(BODY_START) != 1 or source.count(BODY_END) != 1:
        raise UnsafeEdit('이벤트 본문 경계를 확인하지 못해 링크를 저장하지 않았습니다.')
    prefix, _, body = source.partition(BODY_START)
    body, _, suffix = body.partition(BODY_END)
    # Only undo our own wrappers. Existing editor content remains the baseline.
    block = re.compile(re.escape(START) + r'(.*?)' + re.escape(END), re.S)
    def unwrap(match):
        original = re.search(r'<img\b[^>]*>', match[1], re.I)
        if original is None:
            raise UnsafeEdit('기존 상품 링크의 원본 이미지를 확인하지 못했습니다.')
        return original[0]
    body = block.sub(unwrap, body)
    selected = {image['path']: image for image in images if skin in image['skins']}
    found = {path: 0 for path in selected}
    # Images inside existing links, maps, picture/srcset or lazy loaders require review.
    from html.parser import HTMLParser
    class LinkImages(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.edits = []
            self.lines = [0, *(m.end() for m in re.finditer('\n', body))]
        def handle_starttag(self, tag, attrs):
            if tag in {'a', 'picture'}:
                self.stack.append(tag)
            if tag != 'img':
                return
            raw = self.get_starttag_text()
            values = dict(attrs)
            src = values.get('src', '')
            root = next((base for base in IMAGE_ROOTS if src.startswith(base + '/')), None)
            path = urlsplit(src[len(root):]).path if root else None
            if path not in selected:
                return
            found[path] += 1
            rows = [r for r in regions if r['image_path'] == path]
            if not rows:
                return
            if self.stack or any(key in values for key in ('srcset', 'usemap', 'data-src', 'data-lazy-src')):
                raise UnsafeEdit('이미 연결되었거나 반응형 속성이 있는 이미지는 미리 검토해야 합니다.')
            device = 'm' if skin == 'mobile' else 'www'
            anchors = []
            for row in rows:
                style = ';'.join(f'{key}:{row[value] * 100:.6f}%' for key, value in
                                 (('left', 'x'), ('top', 'y'), ('width', 'width'), ('height', 'height')))
                url = f'https://{device}.bananamall.co.kr/shop/view.php?index_no={row["product_id"]}'
                anchors.append(f'<a class="banana-product-link" data-product-id="{row["product_id"]}" href="{url}" target="_blank" rel="noopener noreferrer" aria-label="{escape(row["label"], quote=True)} 상품 보기" style="{style}"></a>')
            replacement = START + '<div class="banana-product-image">' + raw + ''.join(anchors) + '</div>' + END
            line, column = self.getpos()
            offset = self.lines[line - 1] + column
            self.edits.append((offset, offset + len(raw), replacement))
        def handle_endtag(self, tag):
            if tag in self.stack:
                self.stack.remove(tag)
    parser = LinkImages()
    parser.feed(body)
    if any(count != 1 for count in found.values()):
        raise UnsafeEdit('원본 이벤트 이미지가 변경되거나 여러 번 등장해 링크 저장을 멈췄습니다.')
    for start, end, replacement in reversed(parser.edits):
        body = body[:start] + replacement + body[end:]
    return prefix + BODY_START + body + BODY_END + suffix


def linked_css(source):
    pattern = re.compile(r'/\* banana-product-links:start \*/.*?/\* banana-product-links:end \*/', re.S)
    return pattern.sub('', source).rstrip() + '\n\n' + CSS + '\n'


def install(app, lock, selection_db, run_worker, settings, event_plan, publish_enabled, sources):
    from flask import jsonify, request
    def connection():
        db = selection_db()
        db.execute('''CREATE TABLE IF NOT EXISTS product_link_jobs (
            id TEXT PRIMARY KEY, created REAL NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL)''')
        db.commit()
        return db

    def save(ident, data):
        with closing(connection()) as db, db:
            db.execute('UPDATE product_link_jobs SET state=?,data=? WHERE id=?',
                       (data['state'], json.dumps(data, ensure_ascii=False), ident))

    def load(ident):
        if not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('상품 링크 작업 번호를 확인하세요.')
        with closing(connection()) as db:
            row = db.execute('SELECT data FROM product_link_jobs WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('상품 링크 작업을 찾지 못했습니다.')
        return json.loads(row[0])

    def analyze(ident, job):
        try:
            _, candidates = sources(settings()['paths'], job['event'])
            job.update(stage='이미지에서 상품과 보러가기 영역을 읽고 있습니다.')
            save(ident, job)
            value = run_worker('product_links_worker.py', dict(action='analyze', id=ident, images=candidates), 900)
            if value.get('state') != 'ready':
                raise ValueError(value.get('error', '상품 후보를 확인하지 못했습니다.'))
            job.update(value, id=ident)
        except Exception as error:
            job.update(state='failed', error=str(error)[:300] if isinstance(error, ValueError) else '상품 분석 연결을 확인하지 못했습니다. 게시를 시작하지 않았습니다.')
        finally:
            save(ident, job)
            lock.release()

    def publish(ident, job):
        try:
            outcome = run_worker('product_links_worker.py', dict(action='publish', **job), 900)
            job.update(outcome)
        except Exception:
            job.update(state='unconfirmed', error='게시 응답을 받지 못했습니다. PC·모바일 공개 페이지에서 적용 범위를 먼저 확인하세요.')
        finally:
            save(ident, job)
            lock.release()

    @app.post('/api/product-links/analyze')
    def begin_analysis():
        if not publish_enabled:
            return jsonify(error='이 PC의 비즈호스트 연결을 확인하세요.'), 503
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict) or set(data) != {'url'} or not isinstance(data['url'], str):
                raise ValueError('게시한 이벤트 주소를 입력하세요.')
            event, editors = event_plan(data['url'])
            if not lock.acquire(blocking=False):
                return jsonify(error='다른 게시 작업이 진행 중입니다.'), 409
            ident = uuid.uuid4().hex
            job = dict(id=ident, event=event, editors=editors, state='analyzing',
                       stage='PC·모바일 이벤트 이미지를 확인하고 있습니다.', images=[], regions=[])
            try:
                with closing(connection()) as db, db:
                    db.execute('INSERT INTO product_link_jobs VALUES(?,?,?,?)',
                               (ident, time.time(), 'analyzing', json.dumps(job)))
                threading.Thread(target=analyze, args=(ident, job), daemon=True).start()
            except Exception:
                lock.release()
                raise
            return jsonify(job), 202
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.get('/api/product-links/<ident>')
    def status(ident):
        try:
            value = load(ident)
            if value['state'] in {'analyzing', 'publishing'} and not lock.locked():
                value.update(state='unconfirmed', error='실행이 중단되었습니다. 공개 페이지와 기존 작업 결과를 먼저 확인하세요.')
                save(ident, value)
            return jsonify(value)
        except ValueError as error:
            return jsonify(error=str(error)), 404

    @app.post('/api/product-links/<ident>/publish')
    def begin_publish(ident):
        if not publish_enabled:
            return jsonify(error='이 PC의 비즈호스트 연결을 확인하세요.'), 503
        if not lock.acquire(blocking=False):
            return jsonify(error='다른 게시 작업이 진행 중입니다.'), 409
        try:
            job = load(ident)
            if job['state'] != 'ready':
                lock.release()
                return jsonify(job), 200 if job['state'] == 'published' else 409
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or set(body) != {'regions'}:
                raise ValueError('연결할 상품 영역을 확인하세요.')
            job['selected'] = validated_regions(job['images'], job['regions'], body['regions'])
            job.update(state='publishing', stage='상품과 원본 이미지를 다시 확인하고 PC·모바일에 저장하고 있습니다.')
            save(ident, job)
            threading.Thread(target=publish, args=(ident, job), daemon=True).start()
            return jsonify(job), 202
        except (ValueError, TypeError) as error:
            lock.release()
            return jsonify(error=str(error)), 400
        except Exception:
            lock.release()
            raise
