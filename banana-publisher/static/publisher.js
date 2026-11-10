'use strict';

const form = document.querySelector('#prepare');
const input = document.querySelector('#images');
const zone = document.querySelector('#drop-zone');
const fileList = document.querySelector('#files');
const results = document.querySelector('#results');
const message = document.querySelector('#message');
const skinResults = results.querySelector('.skins');
const panels = [...document.querySelectorAll('[data-skin]')];
const picker = document.querySelector('#image-picker');
const choices = document.querySelector('#choices');
const publishButton = document.querySelector('#publish');
const closePicker = document.querySelector('#close-picker');
const publishStatus = document.querySelector('#publish-status');
const previewDialog = document.querySelector('#preview-dialog');
const previewImage = document.querySelector('#preview-image');
const previewTitle = document.querySelector('#preview-title');
const pickerTitle = document.querySelector('#picker-title');
const pickerHint = document.querySelector('#picker-hint');
const pickerInstructions = document.querySelector('#picker-instructions');
const skinNames = {web: '웹', mobile: '모바일'};
let attachments = [];
let prepared;
let publishing = false;

function renderAttachments() {
  fileList.replaceChildren(...attachments.map((file, index) => {
    const row = document.createElement('li');
    row.textContent = file.name;
    for (const [label, offset] of [['위로', -1], ['아래로', 1], ['빼기', 0]]) {
      const action = document.createElement('button');
      action.type = 'button';
      action.textContent = label;
      action.setAttribute('aria-label', `${file.name} ${label}`);
      const next = index + offset;
      action.disabled = next < 0 || next >= attachments.length;
      action.addEventListener('click', () => {
        attachments.splice(index, 1);
        if (offset) attachments.splice(next, 0, file);
        renderAttachments();
        if (offset) fileList.children[next]?.querySelector(`button[aria-label="${CSS.escape(file.name + ' ' + label)}"]:enabled`)?.focus();
      });
      row.append(action);
    }
    return row;
  }));
}

function addAttachments(files) {
  if (form.dataset.running) return;
  attachments.push(...files);
  renderAttachments();
}

input.addEventListener('change', () => {
  addAttachments(input.files);
  input.value = '';
});
zone.addEventListener('dragover', event => {
  event.preventDefault();
  zone.dataset.drag = '';
});
zone.addEventListener('dragleave', () => delete zone.dataset.drag);
zone.addEventListener('drop', event => {
  event.preventDefault();
  delete zone.dataset.drag;
  addAttachments(event.dataTransfer.files);
});

function editorLinks(url) {
  if (!/^\/etc\/[A-Za-z0-9][A-Za-z0-9_-]{0,99}\.php$/.test(url.pathname)
      || url.protocol !== 'https:' || !['www.bananamall.co.kr', 'bananamall.co.kr', 'm.bananamall.co.kr'].includes(url.host)
      || url.username || url.password) throw new Error('바나나몰의 /etc/이벤트이름.php 주소를 입력하세요.');
  for (const panel of panels) {
    const editor = new URL(panel.dataset.editor, window.location.origin);
    editor.searchParams.set('url', url.origin + url.pathname);
    panel.querySelector('.editor').href = editor.href;
    panel.querySelector('.state').textContent = '업로드 결과 대기';
    panel.querySelector('.uploads').replaceChildren();
  }
}

function displayStatus(skins = {}) {
  const names = {waiting: '업로드 대기', running: '진행 중', ready: '파일 확인 완료', failed: '확인 필요'};
  for (const panel of panels) {
    const status = skins[panel.dataset.skin];
    panel.querySelector('.state').textContent = status ? [names[status.status], status.shared && '웹·모바일 공용 폴더', status.folder].filter(Boolean).join(' · ') : '업로드 확인 필요';
    panel.querySelector('.uploads').replaceChildren(...(status?.files || []).map(file => {
      const row = document.createElement('li');
      row.textContent = file.name + (file.status === 'reused' ? ' · 기존 파일 확인' : ' · 업로드 확인');
      return row;
    }));
  }
}

function pickImage(data) {
  prepared = data;
  const deleting = data.mode === 'delete';
  pickerTitle.textContent = deleting ? '삭제할 이미지 선택' : '교체할 이미지 선택';
  pickerHint.textContent = deleting
    ? `${data.event} · 선택한 이미지를 페이지에서 삭제합니다.`
    : `${data.event} · 업로드한 새 이미지로 바꿀 기존 이미지를 선택하세요.`;
  publishButton.textContent = deleting ? '삭제 후 게시' : '교체 후 게시';
  publishButton.disabled = true;
  publishStatus.textContent = '';
  pickerInstructions.hidden = false;
  choices.classList.remove('registration-preview');
  choices.replaceChildren(...data.candidates.map((item, index) => {
    const card = document.createElement('article');
    card.className = 'choice';
    const label = document.createElement('label');
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'candidate';
    radio.value = index;
    const preview = document.createElement('img');
    preview.src = item.preview;
    preview.alt = '';
    preview.loading = 'lazy';
    const zoom = document.createElement('button');
    zoom.type = 'button';
    zoom.className = 'choice-preview';
    zoom.setAttribute('aria-label', `${item.label} 크게 보기`);
    zoom.title = '크게 보기';
    zoom.append(preview);
    zoom.addEventListener('click', () => {
      previewTitle.textContent = item.label;
      previewImage.alt = item.label;
      previewImage.src = item.preview;
      previewDialog.showModal();
      previewImage.parentElement.scrollTop = 0;
    });
    const caption = document.createElement('small');
    caption.textContent = item.skins.map(skin => skinNames[skin]).join('·');
    label.append(radio, item.label, caption);
    card.append(zoom, label);
    return card;
  }));
  picker.showModal();
}

function previewRegistration(data) {
  prepared = data;
  pickerTitle.textContent = '신규 등록 순서 미리보기';
  pickerHint.textContent = `${data.event} · ${data.title} · ${data.description}`;
  pickerInstructions.hidden = true;
  publishButton.textContent = '등록하기';
  publishButton.disabled = false;
  publishStatus.textContent = '';
  choices.classList.add('registration-preview');
  choices.replaceChildren(...data.media.map((item, index) => {
    const figure = document.createElement('figure');
    const caption = document.createElement('figcaption');
    caption.textContent = `${index + 1}. ${item.name}`;
    const media = document.createElement(item.kind === 'video' ? 'video' : 'img');
    media.src = item.url;
    if (item.kind === 'video') {
      media.controls = true;
      media.playsInline = true;
      media.preload = 'metadata';
    } else {
      media.alt = item.name;
      media.loading = 'lazy';
    }
    figure.append(media, caption);
    return figure;
  }));
  picker.showModal();
}

choices.addEventListener('change', () => {
  publishButton.disabled = !prepared || !choices.querySelector('input:checked');
});
closePicker.addEventListener('click', () => picker.close());
document.querySelector('#close-preview').addEventListener('click', () => previewDialog.close());
previewDialog.addEventListener('close', () => previewImage.removeAttribute('src'));
picker.addEventListener('cancel', event => {
  if (publishing) event.preventDefault();
});
picker.addEventListener('close', () => {
  for (const video of choices.querySelectorAll('video')) video.pause();
});

publishButton.addEventListener('click', async () => {
  const selected = choices.querySelector('input:checked');
  if (!prepared || publishing || (prepared.mode !== 'new' && !selected)) return;
  const registration = prepared.mode === 'new';
  publishing = true;
  closePicker.disabled = publishButton.disabled = true;
  for (const radio of choices.querySelectorAll('input')) radio.disabled = true;
  publishStatus.textContent = '비즈호스트에 저장하고 공개 페이지를 확인하고 있습니다.';
  try {
    const endpoint = registration ? 'registrations' : 'replacements';
    const response = await fetch(`/api/${endpoint}/${prepared.replacement_id}/publish`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(registration ? {} : {candidate: Number(selected.value)}),
    });
    const data = await response.json();
    if (response.ok && data.state === 'published') {
      message.textContent = `${data.skins.map(skin => skinNames[skin]).join('·')} 공개 페이지에 반영했습니다.`;
      if (prepared.mode !== 'delete') {
        attachments = [];
        renderAttachments();
      }
      picker.close();
    } else {
      publishStatus.textContent = data.error || '게시 결과를 확인할 수 없습니다. 공개 페이지를 먼저 확인하세요.';
      message.textContent = publishStatus.textContent;
    }
  } catch {
    publishStatus.textContent = '게시 결과 응답이 끊겼습니다. 공개 페이지를 먼저 확인하세요.';
    message.textContent = publishStatus.textContent;
  } finally {
    publishing = false;
    closePicker.disabled = false;
    prepared = null;
  }
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const mode = event.submitter?.value;
  if (!mode || form.dataset.running) return;
  results.hidden = false;
  let url;
  const title = document.querySelector('#event-title').value.trim();
  const description = document.querySelector('#event-description').value.trim();
  try {
    if (mode === 'replace' && attachments.length !== 1)
      throw new Error('교체할 새 이미지 한 장을 첨부하세요.');
    if (mode === 'replace' && /\.mp4$/i.test(attachments[0].name))
      throw new Error('이미지 교체에는 이미지 파일을 첨부하세요.');
    if (mode === 'new' && (!title || !description || attachments.length < 1 || attachments.length > 16))
      throw new Error('행사명과 검색 설명을 입력하고 파일을 1개부터 16개까지 첨부하세요.');
    if (mode !== 'delete' && attachments.some(file => file.size > (/\.mp4$/i.test(file.name) ? 80 : 20) * 1024 * 1024))
      throw new Error('이미지는 20MB, MP4는 80MB 이하로 첨부하세요.');
    if (mode !== 'delete' && attachments.reduce((size, file) => size + file.size, 0) >= 100 * 1024 * 1024)
      throw new Error('첨부 파일의 전체 크기를 100MB 미만으로 맞춰 주세요.');
    url = new URL(document.querySelector('#event-url').value.trim());
    editorLinks(url);
  } catch (error) {
    skinResults.hidden = true;
    message.textContent = error.message;
    return;
  }
  skinResults.hidden = mode === 'delete';
  const payload = new FormData();
  payload.set('url', url.origin + url.pathname);
  payload.set('mode', mode);
  if (mode === 'new') {
    payload.set('event_title', title);
    payload.set('event_description', description);
  }
  if (mode !== 'delete') for (const file of attachments) payload.append('images', file);
  form.dataset.running = 'true';
  const buttons = [...form.querySelectorAll('button:enabled')];
  for (const button of buttons) button.disabled = true;
  message.textContent = mode === 'delete' ? '기존 이벤트의 이미지를 확인하고 있습니다.' : 'SFTP에 연결해 웹·모바일 공용 파일을 준비 중입니다.';
  try {
    const response = await fetch('/api/prepare', {method: 'POST', body: payload});
    const data = await response.json();
    displayStatus(data.skins);
    if (!response.ok) {
      message.textContent = data.error || '준비 결과를 확인하세요.';
    } else if (mode === 'new') {
      message.textContent = '공용 폴더에 파일을 올렸습니다. 순서를 확인한 뒤 등록하세요.';
      previewRegistration(data);
    } else {
      message.textContent = mode === 'delete' ? '기존 이미지를 확인했습니다.' : '새 이미지 업로드를 확인했습니다.';
      pickImage(data);
    }
  } catch {
    displayStatus();
    message.textContent = '서버 응답을 확인하지 못했습니다. 동일한 파일로 다시 실행하면 이미 올라간 파일을 먼저 대조합니다.';
  } finally {
    delete form.dataset.running;
    for (const button of buttons) button.disabled = false;
  }
});

const bannerTool = document.querySelector('#banner-tool');
const bannerForm = document.querySelector('#banner-form');
const bannerDialog = document.querySelector('#banner-confirm');
const bannerRegister = document.querySelector('#banner-register');
const bannerResult = document.querySelector('#banner-result');
const bannerSlots = [...document.querySelectorAll('[data-banner-slot]')];
const bannerNames = new Map(bannerSlots.map(slot => [Number(slot.dataset.bannerSlot), slot.querySelector('strong').textContent]));
const bannerFiles = document.querySelector('#banner-files');
const bannerFileStatus = document.querySelector('#banner-file-status');
const bannerDuplicates = document.querySelector('#banner-duplicates');
const bannerSizes = new Set(bannerSlots.map(slot => slot.dataset.bannerSize));
const bannerBackgrounds = bannerSlots.flatMap(slot => {
  const field = slot.querySelector('[data-banner-background]');
  return field ? [{field, size: slot.dataset.bannerSize, status: slot.querySelector('[data-banner-background-status]')}] : [];
});
const bannerBackgroundSizes = new Set(bannerBackgrounds.map(({size}) => size));
const bannerBackgroundColors = new WeakMap();
let bannerGroups = new Map();
let bannerSelected = new Map();
let bannerScan = 0;
let scanningBanners = false;
let bannerPayload;

bannerTool.addEventListener('toggle', () => {
  if (!bannerTool.open) return;
  const field = document.querySelector('#banner-url');
  if (!field.value) field.value = document.querySelector('#event-url').value;
});

function detectBannerBackground(bitmap) {
  // Read both sides where the storefront's surrounding color meets the image.
  const canvas = document.createElement('canvas');
  canvas.width = 2;
  canvas.height = bitmap.height;
  const context = canvas.getContext('2d', {willReadFrequently: true});
  const edges = [0, bitmap.width - 1].map((x, side) => {
    context.drawImage(bitmap, x, 0, 1, canvas.height, side, 0, 1, canvas.height);
    const pixels = context.getImageData(side, 0, 1, canvas.height).data;
    const colors = [];
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3] >= 128) colors.push([pixels[i], pixels[i + 1], pixels[i + 2]]);
    }
    return colors;
  });
  const pixels = edges.flat();
  if (!pixels.length) return;
  const median = [0, 1, 2].map(channel => {
    const values = pixels.map(pixel => pixel[channel]).sort((a, b) => a - b);
    return values[Math.floor(values.length / 2)];
  });
  const uniform = edges.every(edge => edge.filter(pixel =>
    pixel.every((value, channel) => Math.abs(value - median[channel]) <= 12)).length >= canvas.height * 0.9);
  const visibleEdges = edges.filter(edge => edge.length);
  // Give each visible edge the same weight, even when one is partly transparent.
  const rgb = uniform ? median : median.map((_, channel) => Math.round(visibleEdges.reduce((sum, edge) =>
    sum + edge.reduce((total, pixel) => total + pixel[channel], 0) / edge.length, 0) / visibleEdges.length));
  return {color: '#' + rgb.map(value => value.toString(16).padStart(2, '0')).join('').toUpperCase(), uniform};
}

for (const {field, status} of bannerBackgrounds) {
  field.addEventListener('input', () => {
    delete field.dataset.autoBackground;
    status.textContent = field.value.trim() ? '직접 입력한 색' : '';
  });
}

function drawBannerMatches() {
  for (const slot of bannerSlots) {
    const size = slot.dataset.bannerSize;
    slot.querySelector('[data-banner-match]').textContent = bannerSelected.get(size)?.name ||
      (bannerGroups.has(size) ? '같은 규격에서 파일 선택 필요' : '미첨부');
  }
  for (const {field, size, status} of bannerBackgrounds) {
    const file = bannerSelected.get(size);
    if (field.dataset.autoBackground) field.value = '';
    delete field.dataset.autoBackground;
    if (field.value.trim()) continue;
    const background = file && bannerBackgroundColors.get(file);
    if (background) {
      field.value = background.color;
      field.dataset.autoBackground = 'true';
      status.textContent = background.uniform ? '좌우 가장자리에서 자동 입력' : '좌우 색이 섞여 유사 색상으로 자동 입력';
    } else {
      status.textContent = file ? '가장자리에서 색을 읽을 수 없습니다.' : '';
    }
  }
}

bannerFiles.addEventListener('change', async () => {
  const stamp = ++bannerScan;
  const groups = new Map();
  const skipped = [];
  bannerGroups = new Map();
  bannerSelected = new Map();
  bannerDuplicates.replaceChildren();
  drawBannerMatches();
  const files = [...bannerFiles.files];
  if (files.length > 32) {
    scanningBanners = false;
    bannerFileStatus.textContent = '한 번에 파일 32개까지 규격을 확인할 수 있습니다.';
    return;
  }
  scanningBanners = true;
  bannerFileStatus.textContent = '배너 픽셀 규격을 확인하고 있습니다.';
  for (const file of files) {
    if (stamp !== bannerScan) return;
    try {
      if (!/\.(jpe?g|gif)$/i.test(file.name) || !file.size || file.size > 20 * 1024 * 1024)
        throw Error('JPG/GIF, 20MB 이하');
      const bitmap = await createImageBitmap(file, {imageOrientation: 'none'});
      const size = `${bitmap.width}x${bitmap.height}`;
      try {
        if (bannerBackgroundSizes.has(size)) bannerBackgroundColors.set(file, detectBannerBackground(bitmap));
      } catch {
        // Color extraction is optional; the selected file can still be used with a manual color.
      } finally {
        bitmap.close();
      }
      if (!bannerSizes.has(size)) throw Error(`${size.replace('x', '×')}px 제외 규격`);
      if (!groups.has(size)) groups.set(size, []);
      groups.get(size).push(file);
    } catch (error) {
      skipped.push(`${file.name} (${error.message || '이미지 읽기 실패'})`);
    }
  }
  if (stamp !== bannerScan) return;
  scanningBanners = false;
  bannerGroups = groups;
  for (const [size, matches] of groups) {
    if (matches.length === 1) {
      bannerSelected.set(size, matches[0]);
      continue;
    }
    const label = document.createElement('label');
    const select = document.createElement('select');
    label.textContent = `${size.replace('x', '×')}px에 사용할 파일 `;
    select.append(new Option('선택하세요', ''));
    matches.forEach((file, index) => select.append(new Option(`${index + 1}. ${file.name}`, String(index))));
    select.addEventListener('change', () => {
      if (select.value === '') bannerSelected.delete(size);
      else bannerSelected.set(size, matches[Number(select.value)]);
      drawBannerMatches();
    });
    label.append(select);
    bannerDuplicates.append(label);
  }
  bannerFileStatus.textContent = [`${groups.size}개 규격 확인`, skipped.length ? `제외된 파일 ${skipped.join(', ')}` : ''].filter(Boolean).join(' · ');
  drawBannerMatches();
});

bannerForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (bannerForm.dataset.running || scanningBanners) return;
  if (!bannerGroups.size || bannerSelected.size !== bannerGroups.size) {
    bannerResult.textContent = bannerGroups.size ? '같은 규격으로 들어온 파일 중 한 장을 선택하세요.' : '등록 가능한 배너 파일을 첨부하세요.';
    return;
  }
  const payload = new FormData(bannerForm);
  const selectedFiles = new Map(bannerSelected);
  if ([...selectedFiles.values()].reduce((sum, file) => sum + file.size, 0) >= 100 * 1024 * 1024) {
    bannerResult.textContent = '선택한 파일 합계를 100MB 미만으로 맞춰 주세요.';
    return;
  }
  const selected = bannerSlots.filter(slot => selectedFiles.has(slot.dataset.bannerSize));
  for (const slot of selected) {
    const background = slot.querySelector('input[type=text]');
    if (background && !/^#[0-9a-f]{6}$/i.test(background.value.trim())) {
      bannerResult.textContent = `${bannerNames.get(Number(slot.dataset.bannerSlot))}의 배경색을 #RRGGBB 형식으로 입력하세요.`;
      return;
    }
  }
  const submit = event.submitter;
  bannerForm.dataset.running = 'seo';
  if (submit) submit.disabled = true;
  bannerResult.textContent = '이벤트 페이지에서 SEO 검색 설명을 읽고 있습니다.';
  try {
    const response = await fetch('/api/banners/seo', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: payload.get('url')})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'SEO 검색 설명을 읽을 수 없습니다.');
    payload.set('seo', data.seo);
    payload.set('request_id', crypto.randomUUID().replaceAll('-', ''));
    for (const file of selectedFiles.values()) payload.append('files', file);
    bannerPayload = payload;
    document.querySelector('#banner-summary').textContent = [payload.get('url'), `노출 문구 · ${data.seo}`,
      `${payload.get('start')} ~ ${payload.get('end')} (한국 시간)`,
      payload.get('visible') === 'Y' ? '지정 기간에 노출' : '노출 안 함으로 등록'].join('\n');
    document.querySelector('#banner-selection').replaceChildren(...selected.map(slot => {
      const row = document.createElement('li');
      row.textContent = `${bannerNames.get(Number(slot.dataset.bannerSlot))} · ${selectedFiles.get(slot.dataset.bannerSize).name}`;
      const background = payload.get(`background_${slot.dataset.bannerSlot}`)?.trim();
      if (background) row.append(` · 배경 ${background}`);
      return row;
    }));
    bannerResult.textContent = '';
    bannerDialog.showModal();
  } catch (error) {
    bannerResult.textContent = error.message;
  } finally {
    delete bannerForm.dataset.running;
    if (submit) submit.disabled = false;
  }
});

document.querySelector('#banner-cancel').addEventListener('click', () => bannerDialog.close());
bannerRegister.addEventListener('click', async () => {
  if (!bannerPayload || bannerForm.dataset.running) return;
  const payload = bannerPayload;
  bannerPayload = null;
  bannerDialog.close();
  bannerForm.dataset.running = 'true';
  const buttons = [...bannerForm.querySelectorAll('button:enabled')];
  for (const button of buttons) button.disabled = true;
  bannerResult.textContent = '비즈호스트의 위치별 양식을 확인하고 배너를 등록 중입니다.';
  try {
    const response = await fetch('/api/banners/register', {method: 'POST', body: payload});
    const data = await response.json();
    const rows = (data.results || []).map(row => `${bannerNames.get(row.slot)} · ${row.state === 'existing' ? '동일 등록 확인' : '등록 확인'}`);
    if (data.error) rows.push([bannerNames.get(data.slot), data.error].filter(Boolean).join(' · '));
    bannerResult.textContent = rows.join('\n') || '배너 등록 결과를 확인하세요.';
  } catch {
    bannerResult.textContent = '등록 응답을 받지 못했습니다. 비즈호스트 목록을 확인한 뒤 진행하세요.';
  } finally {
    delete bannerForm.dataset.running;
    for (const button of buttons) button.disabled = false;
  }
});
