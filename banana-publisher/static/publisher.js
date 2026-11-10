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
const skinNames = {web: '웹', mobile: '모바일'};
let images = [];
let prepared;
let publishing = false;

function choose(files) {
  images = [...files];
  fileList.replaceChildren(...images.map(file => {
    const row = document.createElement('li');
    row.textContent = file.name;
    return row;
  }));
}

input.addEventListener('change', () => choose(input.files));
zone.addEventListener('dragover', event => {
  event.preventDefault();
  zone.dataset.drag = '';
});
zone.addEventListener('dragleave', () => delete zone.dataset.drag);
zone.addEventListener('drop', event => {
  event.preventDefault();
  delete zone.dataset.drag;
  input.value = '';
  choose(event.dataTransfer.files);
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
  const names = {waiting: '업로드 대기', running: '진행 중', ready: '이미지 확인 완료', failed: '확인 필요'};
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
  document.querySelector('#picker-title').textContent = deleting ? '삭제할 이미지 선택' : '교체할 이미지 선택';
  document.querySelector('#picker-hint').textContent = deleting
    ? `${data.event} · 선택한 이미지를 페이지에서 삭제합니다.`
    : `${data.event} · 업로드한 새 이미지로 바꿀 기존 이미지를 선택하세요.`;
  publishButton.textContent = deleting ? '삭제 후 게시' : '교체 후 게시';
  publishButton.disabled = true;
  publishStatus.textContent = '';
  choices.replaceChildren(...data.candidates.map((item, index) => {
    const label = document.createElement('label');
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'candidate';
    radio.value = index;
    const preview = document.createElement('img');
    preview.src = item.preview;
    preview.alt = '';
    preview.loading = 'lazy';
    const caption = document.createElement('small');
    caption.textContent = item.skins.map(skin => skinNames[skin]).join('·');
    label.append(radio, preview, item.label, caption);
    return label;
  }));
  picker.showModal();
}

choices.addEventListener('change', () => {
  publishButton.disabled = !prepared || !choices.querySelector('input:checked');
});
closePicker.addEventListener('click', () => picker.close());
picker.addEventListener('cancel', event => {
  if (publishing) event.preventDefault();
});

publishButton.addEventListener('click', async () => {
  const selected = choices.querySelector('input:checked');
  if (!prepared || !selected || publishing) return;
  publishing = true;
  closePicker.disabled = publishButton.disabled = true;
  for (const radio of choices.querySelectorAll('input')) radio.disabled = true;
  publishStatus.textContent = '비즈호스트에 저장하고 공개 페이지를 확인하고 있습니다.';
  try {
    const response = await fetch(`/api/replacements/${prepared.replacement_id}/publish`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({candidate: Number(selected.value)}),
    });
    const data = await response.json();
    if (response.ok && data.state === 'published') {
      message.textContent = `${data.skins.map(skin => skinNames[skin]).join('·')} 공개 페이지에 반영했습니다.`;
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
  try {
    if (mode === 'replace' && images.length !== 1)
      throw new Error('교체할 새 이미지 한 장을 첨부하세요.');
    if (mode === 'new' && (images.length < 1 || images.length > 16))
      throw new Error('이미지를 1장부터 16장까지 첨부하세요.');
    if (mode !== 'delete' && images.some(file => file.size > 20 * 1024 * 1024))
      throw new Error('20MB 이하 이미지를 1장부터 16장까지 첨부하세요.');
    if (mode !== 'delete' && images.reduce((size, file) => size + file.size, 0) >= 100 * 1024 * 1024)
      throw new Error('첨부 이미지의 전체 크기를 100MB 미만으로 맞춰 주세요.');
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
  if (mode !== 'delete') for (const image of images) payload.append('images', image);
  // Open from the submit gesture so browsers can allow the editor tabs.
  if (mode === 'new') for (const panel of panels) window.open(panel.querySelector('.editor').href, '_blank', 'noopener,noreferrer');
  form.dataset.running = 'true';
  const buttons = [...form.querySelectorAll('button:enabled')];
  for (const button of buttons) button.disabled = true;
  message.textContent = mode === 'delete' ? '기존 이벤트의 이미지를 확인하고 있습니다.' : 'SFTP에 연결해 웹·모바일 이미지를 준비 중입니다.';
  try {
    const response = await fetch('/api/prepare', {method: 'POST', body: payload});
    const data = await response.json();
    displayStatus(data.skins);
    if (!response.ok) {
      message.textContent = data.error || '준비 결과를 확인하세요.';
    } else if (mode === 'new') {
      message.textContent = '웹·모바일 이미지 업로드를 확인했습니다. 편집 화면에서 이어서 작업하세요.';
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
