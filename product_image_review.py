"""Local-only product image/title and footer-gap review. No model calls."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
GAP_REVIEW_PX = 300  # Trial threshold: flag for review, never silently approve.
MAX_DOWNLOAD = 24 * 1024 * 1024
MAX_PIXELS = 32_000_000


def normalize(value):
    return re.sub(r'[^a-z0-9가-힣]', '', unicodedata.normalize('NFKC', value).lower())


def core_name(value):
    value = re.sub(r'^\[완료\]\s*', '', value)
    value = re.sub(r'^[abc]군\s*[-:：]?\s*', '', value, flags=re.I)
    value = re.sub(r'^\[[^]]+\]\s*', '', value)
    return value.split('(', 1)[0].strip()


def available():
    """Check local dependencies before a run can mark any products complete."""
    import importlib.util
    if not all(importlib.util.find_spec(name) for name in ('PIL', 'numpy')):
        raise ValueError('로컬 이미지 검사 라이브러리가 없음')
    if not (ROOT / 'local_image_ocr.ps1').is_file():
        raise ValueError('로컬 OCR 실행 파일이 없음')
    powershell = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not powershell.is_file():
        raise ValueError('Windows 로컬 OCR 실행 환경이 없음')
    return powershell


def _ocr(image, folder, label, checkpoint):
    from PIL import Image, ImageOps
    import task_control
    checkpoint()
    header = ImageOps.grayscale(image).point(lambda x: 0 if x < 210 else 255).convert('RGB')
    factor = min(2., 2500 / max(header.size))
    header = header.resize(tuple(max(1, round(x * factor)) for x in header.size), Image.Resampling.LANCZOS)
    source, target = folder / (label + '.png'), folder / (label + '.json')
    header.save(source)
    command = [str(available()), '-NoProfile', '-File', str(ROOT / 'local_image_ocr.ps1'),
               '-ImagePath', str(source), '-ResultPath', str(target)]
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
        with task_control.interruptible(process):
            deadline = time.monotonic() + 25
            while process.poll() is None:
                checkpoint()
                if time.monotonic() >= deadline:
                    process.kill()
                    raise ValueError('로컬 이미지 문자 인식 시간 초과')
                time.sleep(.1)
            checkpoint()
            if process.returncode:
                raise ValueError('로컬 이미지 문자 인식 실패')
    return json.loads(target.read_text(encoding='utf-8-sig'))


DESCRIPTION = r'''() => {
 const root=document.querySelector('[itemprop=description] .memo');
 const notice=document.querySelector('#wrap_prd_notice .title_prd_review');
 if(!root||!notice)throw Error('상세 내용/주의사항 경계 확인 불가');
 const images=[...root.querySelectorAll('img')].filter(e=>{
   const r=e.getBoundingClientRect();return r.width>=100&&r.height>=100;
 });
 if(!images.length||images.length>24)throw Error('검사할 상세 이미지 구성 확인 필요');
 if(images.some(e=>!e.complete||!e.naturalWidth))throw Error('상세 이미지 로딩 미완료');
 const rectangles=images.map(e=>{const r=e.getBoundingClientRect();return {
   src:e.currentSrc,width:e.naturalWidth,height:e.naturalHeight,
   top:r.top+scrollY,bottom:r.bottom+scrollY,renderedHeight:r.height};});
 let lastTextBottom=0;
 const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
 let node;
 while(node=walker.nextNode()){
   if(!node.textContent.trim()||node.parentElement.closest('script,style,noscript'))continue;
   const range=document.createRange();range.selectNodeContents(node);
   for(const rect of range.getClientRects())if(rect.height&&rect.width)lastTextBottom=Math.max(lastTextBottom,rect.bottom+scrollY);
 }
 return {htmlTitle:root.querySelector('.title-prd')?.innerText||'',images:rectangles,
   textBottom:lastTextBottom,noticeTop:notice.getBoundingClientRect().top+scrollY};
}'''


def inspect(page, name, folder, checkpoint=lambda: None):
    """Read an already authenticated detail page; return explicit issues."""
    from PIL import Image
    import numpy as np
    started = time.monotonic()
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    output = {'state': 'needs_review', 'reasons': [], 'external_model_calls': 0,
              'threshold_css_px': GAP_REVIEW_PX, 'threshold_provisional': True, 'typo_check': False}
    reasons = output['reasons']
    try:
        checkpoint()
        # Scroll only the task tab to trigger lazy image loading; never desktop input.
        root = page.locator('[itemprop=description] .memo')
        root.wait_for(state='attached', timeout=12000)
        images = root.locator('img')
        for index in range(min(images.count(), 24)):
            checkpoint()
            image = images.nth(index)
            if image.is_visible(): image.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_function("""() => [...document.querySelectorAll('[itemprop=description] .memo img')]
            .filter(e=>e.getBoundingClientRect().width>=100).every(e=>e.complete&&e.naturalWidth>0)""", timeout=12000)
        info = page.evaluate(DESCRIPTION)
        output['observed'] = info
        # Product titles may be absent from images or partial in the HTML.
        # The user excluded title matching from the acceptance criteria.
        output.update(title_check='not_required', image_title_matches=None, ocr=[])

        downloaded = {}
        def load(index):
            if index in downloaded: return downloaded[index]
            checkpoint()
            meta = info['images'][index]
            host = (urlsplit(meta['src']).hostname or '').lower()
            if urlsplit(meta['src']).scheme != 'https' or host not in {'cdn-banana.bizhost.kr', 'www.bananamall.co.kr', 'bananamall.co.kr'}:
                raise ValueError('검사 가능한 상세 이미지 출처 확인 필요')
            if meta['width'] * meta['height'] > MAX_PIXELS: raise ValueError('상세 이미지 크기 확인 필요')
            response = page.request.get(meta['src'], timeout=15000)
            if not response.ok: raise ValueError('상세 이미지 다운로드 실패')
            body = response.body()
            if len(body) > MAX_DOWNLOAD: raise ValueError('상세 이미지 용량 초과')
            image = Image.open(io.BytesIO(body))
            if image.width * image.height > MAX_PIXELS: raise ValueError('상세 이미지 해상도 초과')
            image = image.convert('RGB')
            if len(downloaded) >= 2:
                downloaded.pop(next(iter(downloaded)))
            downloaded[index] = image
            return image

        # Walk backward through blank end images, including padding inside the JPEG.
        last_content = info['textBottom']
        for index in range(len(info['images']) - 1, -1, -1):
            image = load(index); pixels = np.asarray(image)
            rows = np.flatnonzero((pixels.min(axis=2) < 245).mean(axis=1) > .01)
            if len(rows):
                meta = info['images'][index]
                last_content = max(last_content, meta['top'] + (int(rows[-1]) + 1) * meta['renderedHeight'] / image.height)
                break
        if not last_content: raise ValueError('상세 내용 끝 위치 확인 불가')
        gap = info['noticeTop'] - last_content
        output['gap_css_px'] = round(gap, 1)
        if gap < 0: reasons.append('상세 내용·주의사항 경계 겹침 확인 필요')
        elif gap >= GAP_REVIEW_PX: reasons.append(f'하단 큰 여백 검토 필요: {gap:.0f}px (임시 기준 {GAP_REVIEW_PX}px)')
        output['state'] = 'pass' if not reasons else 'needs_review'
    except Exception as error:
        import task_control
        if isinstance(error, task_control.Stopped): raise
        reasons.append(str(error).split('\n')[0][:180])
    output['elapsed_seconds'] = round(time.monotonic() - started, 3)
    (folder / 'review.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    return output
