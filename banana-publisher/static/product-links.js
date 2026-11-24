'use strict';

(() => {
  const start = document.querySelector('#product-links-auto');
  const dialog = document.querySelector('#product-links-dialog');
  const status = document.querySelector('#product-links-status');
  const content = document.querySelector('#product-links-content');
  const publish = document.querySelector('#product-links-publish');
  const close = document.querySelector('#product-links-close');
  let job;
  let busy = false;
  let timer;
  const money = value => value ? `${Number(value).toLocaleString('ko-KR')}원` : '확인 필요';

  function pending(value) {
    busy = value;
    start.disabled = value;
    close.disabled = value;
    publish.disabled = value || !job?.regions.some(region => region.product_id && !region.skipped)
      || job?.regions.some(region => !region.skipped && !region.product_id);
  }

  async function api(url, data) {
    const response = await fetch(url, data === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '작업 상태를 확인하지 못했습니다.');
    return result;
  }

  function render() {
    content.replaceChildren();
    for (const image of job.images) {
      const regions = job.regions.filter(region => region.image_path === image.path);
      if (!regions.length) continue;
      const section = document.createElement('section');
      section.className = 'product-links-section';
      const figure = document.createElement('div');
      figure.className = 'product-links-image';
      const img = document.createElement('img');
      img.src = image.url;
      img.alt = '이벤트 상품 링크 영역 미리보기';
      img.width = image.width;
      img.height = image.height;
      figure.append(img);
      const choices = document.createElement('div');
      choices.className = 'product-links-choices';
      for (const region of regions) {
        const index = job.regions.indexOf(region);
        const overlay = document.createElement('button');
        overlay.type = 'button';
        overlay.className = 'product-links-overlay';
        for (const [property, key] of [['left', 'x'], ['top', 'y'], ['width', 'width'], ['height', 'height']]) {
          overlay.style[property] = `${region[key] * 100}%`;
        }
        const row = document.createElement('article');
        row.className = 'product-links-choice';
        row.id = `product-link-choice-${index}`;
        const label = document.createElement('label');
        label.htmlFor = `product-link-select-${index}`;
        label.textContent = region.label;
        const select = document.createElement('select');
        select.id = label.htmlFor;
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '상품 후보를 확인하고 선택하세요';
        select.append(placeholder);
        for (const candidate of region.candidates) {
          const option = document.createElement('option');
          option.value = candidate.id;
          option.textContent = `${candidate.name} · ${money(candidate.price)}`;
          select.append(option);
        }
        select.value = region.product_id || '';
        const detail = document.createElement('p');
        detail.className = 'hint';
        detail.textContent = `이미지에서 읽은 행사 가격 ${money(region.event_price)}`;
        const link = document.createElement('a');
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        const skipLabel = document.createElement('label');
        skipLabel.className = 'product-links-skip';
        const skip = document.createElement('input');
        skip.type = 'checkbox';
        skip.checked = Boolean(region.skipped);
        skipLabel.append(skip, ' 이 영역 제외');
        function update() {
          const selected = region.candidates.find(candidate => candidate.id === region.product_id);
          link.hidden = !selected;
          if (selected) {
            link.href = selected.url;
            link.textContent = `${selected.name} 상품 확인 ↗`;
          }
          overlay.textContent = selected ? '상품 연결' : '확인 필요';
          overlay.setAttribute('aria-label', `${selected?.name || region.label} 연결 영역`);
          overlay.classList.toggle('needs-review', !selected);
          overlay.hidden = Boolean(region.skipped);
          select.disabled = Boolean(region.skipped);
          pending(false);
        }
        select.addEventListener('change', () => { region.product_id = select.value || null; update(); });
        skip.addEventListener('change', () => { region.skipped = skip.checked; update(); });
        overlay.addEventListener('click', () => { row.scrollIntoView({block: 'center', behavior: 'smooth'}); select.focus(); });
        row.append(label, select, detail, link, skipLabel);
        choices.append(row);
        figure.append(overlay);
        update();
      }
      section.append(figure, choices);
      content.append(section);
    }
  }

  async function poll() {
    try {
      const value = await api(`/api/product-links/${job.id}`);
      job = value;
      status.textContent = value.error || value.stage || '상품 연결 상태를 확인하고 있습니다.';
      if (['analyzing', 'publishing'].includes(value.state)) {
        timer = setTimeout(poll, 1200);
        return;
      }
      pending(false);
      if (value.state === 'ready') render();
      else {
        publish.disabled = true;
        if (value.state === 'published') content.replaceChildren();
      }
    } catch (error) {
      status.textContent = error.message;
      pending(false);
      publish.disabled = true;
    }
  }

  start.addEventListener('click', async () => {
    if (busy) return;
    clearTimeout(timer);
    job = undefined;
    content.replaceChildren();
    status.textContent = '이미지와 상품 목록을 확인하고 있습니다.';
    dialog.showModal();
    pending(true);
    try {
      job = await api('/api/product-links/analyze', {url: document.querySelector('#event-url').value.trim()});
      await poll();
    } catch (error) {
      status.textContent = error.message;
      pending(false);
    }
  });
  publish.addEventListener('click', async () => {
    if (busy || !job || job.state !== 'ready') return;
    pending(true);
    for (const select of content.querySelectorAll('select,input')) select.disabled = true;
    try {
      const regions = job.regions.filter(region => !region.skipped);
      job = await api(`/api/product-links/${job.id}/publish`, {regions});
      status.textContent = job.stage;
      await poll();
    } catch (error) {
      status.textContent = error.message;
      pending(false);
    }
  });
  close.addEventListener('click', () => { clearTimeout(timer); dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
})();
