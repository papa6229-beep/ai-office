"""Fixed, user-prepared rehearsal. Uses the builder's supported JSON import.

No model calls, product publishing or messenger actions. Private images, copy,
approved project and run receipts stay under local_backups/detail-builder-demo.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import time
import stat
import unicodedata
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'local_backups' / 'detail-builder-demo'
DB = ROOT / 'office.db'
ORIGIN = 'https://detail-page-builder-bice.vercel.app/'
CARD = 'abe8efb1d0394c0b80ba62c64af36dbd'
MATERIALS = ('2026.10.20_후이저우_샘플가격_경원작성.xlsx', '후이저우 샘플 이미지.zip')
ASSETS = {
    'mainImage': 'IMG_0945.jpg', 'watermarkImage': '워터마크.png',
    'videoInsertImage': 'video01.gif', 'packageImage': 'package.JPG',
    'featureImage': 'IMG_0947.jpg', 'point1Image1': 'IMG_0954.jpg',
    'point1Image2': 'IMG_0963.jpg', 'point2Image1': 'IMG_0950.jpg',
    'point2Image2': 'IMG_0964.jpg', 'point2Image3': 'IMG_0946.jpg',
    'sizeImage': 'size.png',
}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def _lock():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    with (PRIVATE / 'run.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'1'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _document(page, compact=False):
    return page.evaluate('''async compact => {
      const db=await new Promise((ok,no)=>{const r=indexedDB.open('detail-page-builder-workspace',1);
        r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
      const read=(s,k)=>new Promise((ok,no)=>{const r=db.transaction(s,'readonly').objectStore(s).get(k);
        r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
      try {const a=await read('workspace','active');if(!a)throw Error('No active project');
        const [meta,doc]=await Promise.all([read('headers',a.id),read('documents',a.id)]);
        const walk=async v=>{
          if(typeof v==='string' && v.startsWith('data:image/')) {
            const h=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v));
            return {image_sha256:Array.from(new Uint8Array(h),x=>x.toString(16).padStart(2,'0')).join('')};
          }
          if(Array.isArray(v))return await Promise.all(v.map(walk));
          if(v && typeof v==='object')return Object.fromEntries(await Promise.all(
            Object.entries(v).map(async([k,x])=>[k,await walk(x)])));
          return v;
        };
        return {meta,data:compact ? await walk(doc.data) : doc.data};} finally {db.close();}
    }''', compact)


def _compact(value):
    if isinstance(value, str) and value.startswith('data:image/'):
        return {'image_sha256': hashlib.sha256(value.encode('utf-8')).hexdigest()}
    if isinstance(value, dict):
        return {key: _compact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact(item) for item in value]
    return value


def _saved(page):
    page.get_by_role('status', name='자동 저장 상태', exact=True).filter(has_text='저장됨').wait_for(timeout=20000)


def _new_blank(page):
    """The demo opens an empty editor, never the restored finished document."""
    current = _document(page, compact=True)
    empty = not current['data'].get('productNameKr') and not any(current['data'].get(key) for key in ASSETS)
    if not empty or not page.locator('aside').count():
        if page.locator('aside').count():
            _saved(page)
            page.get_by_role('button', name='작업 홈', exact=True).click()
        page.get_by_role('button', name='새 상품 작업', exact=True).click()
        page.locator('aside').wait_for(state='visible', timeout=15000)
    _saved(page)
    current = _document(page, compact=True)
    if current['data'].get('productNameKr') or any(current['data'].get(key) for key in ASSETS):
        raise ValueError('시연 시작 화면이 빈 새 상품 작업이 아닙니다.')
    return {'project_id': current['meta']['id'], 'empty_editor': True}


def capture(page, source_dir):
    """Explicit preparation only: capture the already approved rehearsal, not AI output."""
    _saved(page)
    record = _document(page)
    data = record['data']
    if data['productNameKr'] != '소프트 텅 리얼 페라':
        raise ValueError('시연 상품과 현재 작업이 다릅니다.')
    manifest = {'version': 1, 'origin': ORIGIN, 'prepared_at': time.time(),
                'source_dir': str(Path(source_dir).resolve()),
                'destination': str(Path(source_dir).resolve().parents[1]),
                'incoming': str(Path.home() / 'Documents' / '네이트온 받은 파일'),
                'materials': [], 'assets': {}, 'template': 'approved-project.json'}
    cache = PRIVATE / 'assets'
    cache.mkdir(parents=True, exist_ok=True)
    for key, name in ASSETS.items():
        path = Path(source_dir) / name
        if not path.is_file() or not data.get(key):
            raise ValueError('시연 자료가 없습니다: ' + name)
        digest = _sha(path)
        copy = cache / name
        if copy.exists() and _sha(copy) != digest:
            raise ValueError('기존 시연 원본과 다른 파일입니다: ' + name)
        if not copy.exists():
            shutil.copy2(path, copy)
        manifest['assets'][key] = {'name': name, 'sha256': digest}
        # The package retains the approved cutout; other slots keep source bytes.
        actual = base64.b64decode(data[key].split(',', 1)[1])
        if key != 'packageImage' and hashlib.sha256(actual).hexdigest() != digest:
            raise ValueError('원본 사진과 빌더 내용이 다릅니다: ' + name)
    if not data.get('imageEdits', {}).get('packageImage'):
        raise ValueError('승인된 패키지 누끼 결과가 없습니다.')
    for name in MATERIALS:
        path = Path(manifest['destination']) / name
        manifest['materials'].append({'name': name, 'sha256': _sha(path)})
    project = {'format': 'detail-page-builder-project', 'version': 1,
               'name': '소프트 텅 리얼 페라 · 시연 준비본', 'data': data}
    _write(PRIVATE / manifest['template'], project)
    manifest['template_sha256'] = _sha(PRIVATE / manifest['template'])
    _write(PRIVATE / 'manifest.json', manifest)
    return {'prepared': True, 'assets': len(ASSETS), 'model_calls': 0}


def readiness():
    manifest = json.loads((PRIVATE / 'manifest.json').read_text(encoding='utf-8'))
    template = PRIVATE / manifest['template']
    if _sha(template) != manifest['template_sha256']:
        raise ValueError('준비된 시연 작업 파일이 변경됐습니다.')
    for asset in manifest['assets'].values():
        if _sha(PRIVATE / 'assets' / asset['name']) != asset['sha256']:
            raise ValueError('준비된 사진이 변경됐습니다: ' + asset['name'])
    return manifest


def _launch(request_id):
    origin = 'http://127.0.0.1:8765'
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(origin + '/api/launcher', timeout=10) as response:
        state = json.load(response)
    body = {'id': CARD, 'request_id': uuid.uuid5(uuid.NAMESPACE_URL, request_id + ':builder').hex,
            'token': state['action_token']}
    request = urllib.request.Request(origin + '/api/launcher/launch',
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'Origin': origin})
    with client.open(request, timeout=45) as response:
        result = json.load(response)
    if result.get('error'):
        raise ValueError(result['error'])


def _prepare(manifest, request_id):
    import file_operations as files
    incoming, destination = Path(manifest['incoming']), Path(manifest['destination'])
    destination.mkdir(parents=True, exist_ok=True)
    actions = []
    archive_path = None
    normal = lambda name: re.sub(r'\s+', '', unicodedata.normalize('NFKC', name)).casefold()
    def approved_archive(path):
        with zipfile.ZipFile(path) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 500*1024*1024:
                raise ValueError('시연 압축파일의 크기를 확인하세요.')
            for asset in manifest['assets'].values():
                matches = [item for item in entries if normal(Path(item.filename.replace('\\', '/')).name) == normal(asset['name'])]
                if len(matches) != 1 or hashlib.sha256(archive.read(matches[0])).hexdigest() != asset['sha256']:
                    raise ValueError('준비한 시연 사진과 다른 압축파일입니다: ' + asset['name'])
    for item in manifest['materials']:
        target, source = destination / item['name'], incoming / item['name']
        is_zip = target.suffix.lower() == '.zip'
        if is_zip and not target.exists() and not source.exists():
            matches = [p for parent in (incoming, destination) for p in parent.glob('*.zip')
                       if normal(p.name) == normal(item['name'])]
            if not matches or (len(matches) > 1 and len({_sha(p) for p in matches}) != 1):
                raise ValueError('후이저우 시연 압축파일을 하나로 확인하지 못했습니다.')
            source = matches[0]
            target = destination / source.name
        if is_zip:
            actual = target if target.exists() else source
            if not actual.is_file():
                raise ValueError('시연 이미지 압축파일이 없습니다.')
            if _sha(actual) != item['sha256']:
                approved_archive(actual)
            archive_path = target
        if target.exists():
            if not is_zip and _sha(target) != item['sha256']:
                raise ValueError('테스트 폴더의 자료가 변경됐습니다. 덮어쓰지 않았습니다.')
            continue
        if not source.is_file() or (not is_zip and _sha(source) != item['sha256']):
            raise ValueError('오늘 시연에 준비한 수신 자료가 아닙니다: ' + item['name'])
        actions.append({'op': 'move', 'source': files.observe(source),
                        'parent': files.observe(destination), 'name': target.name})
    if actions:
        job = files.plan(DB, actions, '상세페이지 시연 자료 준비', request_id, conflict='error')
        files.run(DB, job['id'])
        if files.load(DB, job['id'])['status'] != 'completed':
            raise ValueError('자료 이동이 완료되지 않았습니다.')
    # Prepared originals include the user's later additions (size/logo/video/box).
    # Reuse known extraction; restore only missing files, never replace changed work.
    assets = Path(manifest['source_dir'])
    assets.mkdir(parents=True, exist_ok=True)
    if archive_path:
        with zipfile.ZipFile(archive_path) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            direct_images = any(normal(Path(item.filename).name) == normal('IMG_0945.jpg') for item in entries)
            if direct_images:
                approved_archive(archive_path)
                for item in entries:
                    relative = Path(item.filename.replace('\\', '/'))
                    target = (assets / relative).resolve()
                    if not target.is_relative_to(assets.resolve()) or stat.S_ISLNK(item.external_attr >> 16):
                        raise ValueError('압축파일 경로를 확인하세요.')
                    data = archive.read(item)
                    if target.exists():
                        if _sha(target) != hashlib.sha256(data).hexdigest():
                            raise ValueError('해제 위치의 기존 파일을 덮어쓰지 않습니다.')
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
    for item in manifest['assets'].values():
        target = assets / item['name']
        if target.exists() and _sha(target) != item['sha256']:
            raise ValueError('사용자가 변경한 사진을 덮어쓰지 않습니다: ' + item['name'])
        if not target.exists():
            shutil.copy2(PRIVATE / 'assets' / item['name'], target)
    return {'materials_reused': not actions, 'assets': len(manifest['assets'])}


def _import(page, template, name, folder):
    # A unique per-request project name lets an interrupted import resume safely.
    existing = page.evaluate('''async name=>{const db=await new Promise((ok,no)=>{
      const r=indexedDB.open('detail-page-builder-workspace',1);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
      try{return await new Promise((ok,no)=>{const r=db.transaction('headers','readonly').objectStore('headers').getAll();
        r.onsuccess=()=>ok(r.result.filter(x=>x.customName===name||x.name===name));r.onerror=()=>no(r.error)})}
      finally{db.close()}}''', name)
    if len(existing) > 1:
        raise ValueError('동일 실행의 상품 작업이 중복되어 있습니다.')
    opener = page.get_by_role('button', name='불러오기', exact=True)
    if not opener.count():
        opener = page.get_by_role('button', name='작업 보관함 열기', exact=True)
    dialog = page.get_by_role('dialog', name='작업 보관함', exact=True)
    if not dialog.count() or not dialog.is_visible():
        opener.click()
    if existing:
        dialog.locator('[data-project-row]').filter(has_text=name).get_by_role('button', name='열기', exact=True).click()
    else:
        project = json.loads(template.read_text(encoding='utf-8'))
        project['name'] = name
        import_file = folder / 'import-project.json'
        _write(import_file, project)
        # Browser and files live on this same PC. Pass the path directly instead
        # of serialising the 48 MB prepared document through Playwright twice.
        session = page.context.new_cdp_session(page)
        try:
            root = session.send('DOM.getDocument', {'depth': 0})['root']['nodeId']
            node = session.send('DOM.querySelector', {'nodeId': root,
                'selector': 'dialog[aria-label="작업 보관함"] input[type="file"]'})['nodeId']
            if not node:
                raise ValueError('작업 파일 가져오기 입력칸을 확인하지 못했습니다.')
            session.send('DOM.setFileInputFiles', {'nodeId': node, 'files': [str(import_file.resolve())]})
        finally:
            session.detach()
    dialog.wait_for(state='hidden', timeout=60000)
    _saved(page)
    actual = _document(page, compact=True)
    expected = json.loads(template.read_text(encoding='utf-8'))['data']
    if actual['data'] != _compact(expected):
        raise ValueError('가져온 상품의 사진·문구·배치가 준비본과 다릅니다.')
    return {'project_id': actual['meta']['id'], 'reused': bool(existing)}


def _verify_export(path, manifest):
    from PIL import Image
    expected = {'main1.jpg', 'main2.gif', 'main3.jpg'}
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() or set(archive.namelist()) != expected:
            raise ValueError('분할 저장 ZIP의 구성이 다릅니다.')
        rows = []
        for name in sorted(expected):
            content = archive.read(name)
            with Image.open(io.BytesIO(content)) as image:
                if image.width != 800 or image.height <= 0:
                    raise ValueError('출력 이미지 크기를 확인하지 못했습니다.')
                rows.append({'name': name, 'width': image.width, 'height': image.height,
                             'frames': getattr(image, 'n_frames', 1)})
                image.verify()
        gif = manifest['assets']['videoInsertImage']['sha256']
        if hashlib.sha256(archive.read('main2.gif')).hexdigest() != gif:
            raise ValueError('저장된 GIF가 준비한 원본과 다릅니다.')
    return rows


def _verify_thumbnails(path):
    from PIL import Image
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if archive.testzip() or len(names) != 4 or len(set(names)) != 4:
            raise ValueError('썸네일 ZIP의 파일 수가 다릅니다.')
        rows = []
        for name in names:
            if Path(name).suffix.lower() not in {'.jpg', '.jpeg'}:
                raise ValueError('썸네일 출력 형식을 확인하세요.')
            with Image.open(io.BytesIO(archive.read(name))) as image:
                rows.append({'name': name, 'width': image.width, 'height': image.height})
                image.verify()
        if {(row['width'], row['height']) for row in rows} != {(202,202),(400,400),(500,500),(274,411)}:
            raise ValueError('썸네일 4종의 크기를 확인하지 못했습니다.')
        return rows


def run(request_id, phase='all', emit=print):
    from detail_builder_demo_ego import run as ego_run
    return ego_run(request_id,phase,emit)


def _legacy_chrome_reference_only(request_id, phase='all', emit=print):
    if not re.fullmatch('[0-9a-f]{32}', request_id) or phase not in {'prepare', 'build', 'all'}:
        raise ValueError('요청 ID와 실행 단계를 확인하세요.')
    from playwright.sync_api import sync_playwright
    import bizhost_inspector
    import report_automation
    import work_log
    import task_control
    with _lock():
        manifest = readiness()
        folder = PRIVATE / 'runs' / request_id
        path = folder / 'receipt.json'
        result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'request_id': request_id, 'phase': phase, 'started': time.time(), 'steps': {},
            'mode': 'code', 'model_calls': 0}
        if result['phase'] != phase:
            raise ValueError('같은 요청 ID의 실행 단계는 변경하지 않습니다.')
        if result.get('state') == 'completed':
            if result['steps'].get('export'):
                _verify_export(Path(result['steps']['export']['path']), manifest)
            if result['steps'].get('thumbnails'):
                _verify_thumbnails(Path(result['steps']['thumbnails']['path']))
            if phase == 'prepare' or result['steps'].get('thumbnails'):
                return result
        result['state'] = 'running'; _write(path, result)
        def stage(name, message, operation):
            task_control.checkpoint()
            if name in result['steps']:
                return result['steps'][name]
            emit(message)
            value = operation()
            result['steps'][name] = value
            _write(path, result)
            return value
        try:
            if phase in {'prepare', 'all'}:
                stage('materials', '태준님, 준비한 자료를 테스트 폴더에 정리할게요.',
                      lambda: _prepare(manifest, request_id))
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.connect_over_cdp(bizhost_inspector.browser_endpoint())
                except Exception:
                    _launch(request_id)
                    browser = playwright.chromium.connect_over_cdp(bizhost_inspector.browser_endpoint())
                context = browser.contexts[0]
                pages = [p for p in context.pages if p.url.startswith(ORIGIN)]
                if not pages:
                    _launch(request_id)
                    pages = [p for p in context.pages if p.url.startswith(ORIGIN)]
                if not pages:
                    raise ValueError('상세페이지 빌더 탭을 확인하지 못했습니다.')
                page = pages[-1]
                page.set_default_timeout(12000)
                stage('empty_editor', '빈 새 상품 작업 화면부터 준비할게요.', lambda: _new_blank(page))
                if phase in {'prepare', 'all'}:
                    stage('bizhost', '자료 준비가 끝났어요. 비즈호스트 상품관리도 준비할게요.',
                          lambda: {'url': report_automation._login_biz(context, DB, emit).url,
                                   'search_submitted': False})
                if phase in {'build', 'all'}:
                    project_name = '소프트 텅 리얼 페라 · 시연 ' + request_id[:8]
                    imported = stage('project', '준비한 시연 상품의 사진·문구·배치를 한 번에 등록할게요.',
                                     lambda: _import(page, PRIVATE / manifest['template'], project_name, folder))
                    current = _document(page, compact=True)
                    if current['meta']['id'] != imported['project_id']:
                        _import(page, PRIVATE / manifest['template'], project_name, folder)
                    elif current['data'] != _compact(json.loads((PRIVATE / manifest['template']).read_text(encoding='utf-8'))['data']):
                        raise ValueError('가져온 뒤 사용자가 수정한 작업은 덮어쓰거나 출력하지 않습니다.')
                    def export():
                        target = Path(manifest['destination']) / '상세페이지' / request_id / 'detail_split_소프트 텅 리얼 페라.zip'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not target.exists():
                            with page.expect_download(timeout=90000) as pending:
                                page.get_by_role('button', name='전체 이미지 저장', exact=True).click()
                            download = pending.value
                            if not download.suggested_filename.endswith('.zip'):
                                raise ValueError('GIF 분할 ZIP 대신 다른 형식이 반환됐습니다.')
                            download.save_as(str(target))
                            if download.failure():
                                raise ValueError('이미지 내려받기를 완료하지 못했습니다.')
                        return {'path': str(target), 'items': _verify_export(target, manifest),
                                'bytes': target.stat().st_size}
                    stage('export', '워터마크 배치까지 마쳤어요. 전체 이미지를 저장할게요.', export)
                    def thumbnails():
                        target = Path(manifest['destination']) / '상세페이지' / request_id / 'thumbnails_소프트 텅 리얼 페라.zip'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not target.exists():
                            with page.expect_download(timeout=60000) as pending:
                                page.get_by_role('button', name='썸네일 저장', exact=True).click()
                            download = pending.value
                            if not download.suggested_filename.endswith('.zip'):
                                raise ValueError('썸네일 ZIP을 받지 못했습니다.')
                            download.save_as(str(target))
                            if download.failure():
                                raise ValueError('썸네일 저장을 완료하지 못했습니다.')
                        return {'path': str(target), 'items': _verify_thumbnails(target),
                                'bytes': target.stat().st_size}
                    stage('thumbnails', '상세페이지 저장이 끝났어요. 썸네일 4종도 저장할게요.', thumbnails)
            result.update(state='completed', elapsed_seconds=round(time.time()-result['started'], 2))
            _write(path, result)
            work_log.sync(DB, force=True)
            work_log.activity(DB, '상세페이지 시연 코드 실행',
                '준비된 후이저우 시연 자료로 '+phase+' 단계 완료. 새 AI 생성·쇼핑몰 상품 변경 없음.',
                application='상세페이지 빌더', status='completed', request_id=request_id)
            return result
        except Exception as error:
            result.update(state='partial', error=str(error).splitlines()[0][:350])
            _write(path, result)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only preparation check; no browser actions')
    parser.add_argument('--request-id')
    parser.add_argument('--phase', choices=['prepare', 'build', 'all'], default='all')
    args = parser.parse_args()
    if args.check:
        manifest = readiness()
        print(json.dumps({'ready': True, 'assets': len(manifest['assets']),
                          'browser_actions': False, 'model_calls': 0}, ensure_ascii=False))
    elif args.request_id:
        print(json.dumps(run(args.request_id, args.phase, emit=lambda message: print(message, flush=True)), ensure_ascii=False))
    else:
        parser.error('--check 또는 --request-id가 필요합니다.')


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
