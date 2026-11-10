'use strict';

const form = document.querySelector('#prepare');
const input = document.querySelector('#images');
const zone = document.querySelector('#drop-zone');
const fileList = document.querySelector('#files');
const results = document.querySelector('#results');
const message = document.querySelector('#message');
const skinResults = results.querySelector('.skins');
const panels = [...document.querySelectorAll('[data-skin]')];
let images = [];

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
      || url.protocol !== 'https:' || !['www.bananamall.co.kr', 'bananamall.co.kr'].includes(url.host)
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
    panel.querySelector('.state').textContent = status ? [names[status.status], status.folder].filter(Boolean).join(' · ') : '업로드 확인 필요';
    panel.querySelector('.uploads').replaceChildren(...(status?.files || []).map(file => {
      const row = document.createElement('li');
      row.textContent = file.name + (file.status === 'reused' ? ' · 기존 파일 확인' : ' · 업로드 확인');
      return row;
    }));
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const mode = event.submitter?.value;
  if (!mode || form.dataset.running) return;
  results.hidden = false;
  let url;
  try {
    if (images.length < 1 || images.length > 16 || images.some(file => file.size > 20 * 1024 * 1024))
      throw new Error('20MB 이하 이미지를 1장부터 16장까지 첨부하세요.');
    if (images.reduce((size, file) => size + file.size, 0) >= 100 * 1024 * 1024)
      throw new Error('첨부 이미지의 전체 크기를 100MB 미만으로 맞춰 주세요.');
    url = new URL(document.querySelector('#event-url').value.trim());
    editorLinks(url);
  } catch (error) {
    skinResults.hidden = true;
    message.textContent = error.message;
    return;
  }
  skinResults.hidden = false;
  const payload = new FormData();
  payload.set('url', url.origin + url.pathname);
  payload.set('mode', mode);
  for (const image of images) payload.append('images', image);
  // Open from the submit gesture so browsers can allow the editor tabs.
  for (const panel of panels) window.open(panel.querySelector('.editor').href, '_blank', 'noopener,noreferrer');
  form.dataset.running = 'true';
  for (const button of form.querySelectorAll('button')) button.disabled = true;
  message.textContent = 'SFTP에 연결해 웹·모바일 이미지를 준비 중입니다.';
  try {
    const response = await fetch('/api/prepare', {method: 'POST', body: payload});
    const data = await response.json();
    displayStatus(data.skins);
    message.textContent = response.ok ? '웹·모바일 이미지 업로드를 확인했습니다. 편집 화면에서 이어서 작업하세요.' : (data.error || '준비 결과를 확인하세요.');
  } catch {
    displayStatus();
    message.textContent = '서버 응답을 확인하지 못했습니다. 동일한 파일로 다시 실행하면 이미 올라간 파일을 먼저 대조합니다.';
  } finally {
    delete form.dataset.running;
    for (const button of form.querySelectorAll('button')) button.disabled = false;
  }
});
