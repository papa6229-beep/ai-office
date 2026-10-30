/* Manual workspace: local files and FFmpeg; no model or generation API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uid = () => crypto.randomUUID().replaceAll('-', '');
  const preset = 'Locked front orthographic camera at eye level. No camera movement, tilt or roll. A flat empty floor. A square pedestal rises vertically from below the floor. Several rigid pieces of the reference logo descend straight from above, one after another, and assemble into the exact reference logo on the pedestal. The completed logo rotates slowly through exactly one full 360-degree turn. Then the logo and pedestal descend together, completely below the floor, leaving the same empty scene as the first frame. Keep the logo shape, proportions, material and color consistent. Solid evenly lit bright green background and floor, no green reflections on the logo. No text, no people, no extra objects.';
  let state, token, ident, preview, busy = false, timer, opener;
  function preferences() {
    return {generation_resolution:Number($('vsGenerationResolution').value),generation_seconds:Number($('vsGenerationSeconds').value),output_edge:Number($('vsOutputEdge').value),output_seconds:Number($('vsOutputSeconds').value)};
  }
  function fillPreferences(project, draft) {
    let local;
    try { local = JSON.parse(localStorage.getItem('office-video-options-' + ident)); } catch (_) {}
    const options = draft?.preferences || local || project.preferences || {};
    $('vsGenerationResolution').value = [480,720].includes(options.generation_resolution) ? options.generation_resolution : 720;
    $('vsGenerationSeconds').value = options.generation_seconds ?? 8;
    $('vsOutputEdge').value = [720,1080,1920].includes(options.output_edge) ? options.output_edge : 1920;
    $('vsOutputSeconds').value = options.output_seconds ?? 0;
  }
  const url = (asset, download = false) => `/api/video-studio/media?project=${ident}&id=${asset.id}${download ? '&download=1' : ''}`;
  function notice(text = '', error = false) { $('vsNotice').textContent = text; $('vsNotice').classList.toggle('error', error); }
  async function request(path = '', payload) {
    const response = await fetch('/api/video-studio' + path, payload ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,token})} : {cache:'no-store'});
    const data = await response.json();
    if (response.status === 404 && path === '') throw Error('새 제작실을 실행하려면 오피스를 종료한 뒤 다시 열어 주세요. 저장된 작업과 파일은 유지됩니다.');
    if (!response.ok || data.error) throw Error(data.error || '제작실 연결을 확인해 주세요.');
    if (data.action_token) token = data.action_token;
    return data;
  }
  function cacheDraft() {
    if (!ident) return;
    try {
      localStorage.setItem('office-video-draft-' + ident, JSON.stringify({title:$('vsTitle').value,prompt:$('vsPrompt').value,preferences:preferences()}));
      localStorage.setItem('office-video-options-' + ident, JSON.stringify(preferences()));
    } catch (_) { /* Explicit save still works when storage is full. */ }
    $('vsSaved').textContent = '편집 중 · 저장을 눌러 보관';
  }
  function fillDraft(project) {
    let draft;
    try { draft = JSON.parse(localStorage.getItem('office-video-draft-' + ident)); } catch (_) {}
    $('vsTitle').value = draft?.title ?? project.title;
    $('vsPrompt').value = draft?.prompt ?? project.prompt;
    fillPreferences(project, draft);
    $('vsSaved').textContent = draft ? '이 브라우저에 남은 편집 내용' : 'PC에 저장됨';
  }
  async function save() {
    if (!ident) ident = uid();
    state = await request('/save', {id:ident,title:$('vsTitle').value || '새 영상 작업',prompt:$('vsPrompt').value,preferences:preferences()});
    try { localStorage.removeItem('office-video-draft-' + ident); } catch (_) {}
    $('vsSaved').textContent = 'PC에 저장됨';
    render();
  }
  async function exclusive(action) {
    if (busy) return;
    busy = true; locks(); notice();
    try { await action(); } catch (error) { notice(error.message, true); }
    finally { busy = false; locks(); }
  }
  function locks() {
    document.querySelectorAll('#videoStudio [data-vs-write], #videoStudio input[type=file], #vsProject').forEach(x => x.disabled = busy);
    $('vsTitle').disabled = busy; $('vsPrompt').disabled = busy;
    for (const id of ['vsGenerationResolution','vsGenerationSeconds']) $(id).disabled = busy;
    for (const id of ['vsOutputEdge','vsOutputSeconds']) $(id).disabled = busy || !state?.limits?.output_options;
    $('vsOutputNote').textContent = state?.limits?.output_options ? '출력 크기는 가로·세로의 최대 길이입니다. 원본보다 키우지 않습니다. 자를 길이 0은 원본 전체이며, 짧은 영상을 늘리지는 않습니다.' : '출력 크기·길이 선택은 오피스를 다시 시작한 후 적용할 수 있습니다.';
    const running = state?.project?.jobs?.some(x => x.status === 'running');
    $('vsConvert').disabled = busy || running || !state?.ffmpeg || !source();
  }
  function source() { return state?.project?.assets?.find(a => a.id === $('vsSource').value); }
  function playbackState(message) {
    const video = $('vsVideo');
    const visible = !video.hidden && Boolean(video.getAttribute('src'));
    $('vsPlay').disabled = !visible;
    $('vsPlay').textContent = visible && !video.paused ? '❚❚ 일시정지' : '▶ 미리보기 재생';
    if (message) $('vsPlayback').textContent = message;
  }
  function selectPreview(asset) {
    if (!asset) { preview = null; $('vsVideo').pause(); $('vsVideo').removeAttribute('src'); $('vsVideo').load(); $('vsVideo').hidden = true; $('vsStill').hidden = true; $('vsEmpty').hidden = false; $('vsDownload').hidden = true; playbackState('참조 이미지나 결과 영상을 선택해 주세요.'); return; }
    if (preview === asset.id) return;
    preview = asset.id;
    $('vsEmpty').hidden = true;
    $('vsVideo').pause();
    $('vsVideo').hidden = asset.kind !== 'video'; $('vsStill').hidden = asset.kind !== 'image';
    if (asset.kind === 'video') { $('vsVideo').src = url(asset); $('vsVideo').load(); $('vsStill').removeAttribute('src'); playbackState('영상을 불러오는 중입니다.'); }
    else { $('vsStill').src = url(asset); $('vsVideo').removeAttribute('src'); $('vsVideo').load(); playbackState('참조 이미지 미리보기'); }
    $('vsPreviewName').textContent = asset.name;
    $('vsDownload').hidden = false; $('vsDownload').href = url(asset, true);
    $('vsDownload').download = asset.name;
    $('vsDownload').textContent = asset.role === 'export' ? '투명 WebM 다운로드 ↓' : '이 파일 다운로드 ↓';
  }
  function render() {
    if (!state) return;
    $('vsProject').innerHTML = state.projects.map(p => `<option value="${p.id}">${esc(p.title)}</option>`).join('');
    $('vsProject').value = ident;
    const project = state.project;
    const assets = project?.assets || [];
    const selected = $('vsSource').value;
    const sources = assets.filter(a => a.kind === 'video' && a.role !== 'export');
    $('vsSource').innerHTML = '<option value="">결과 영상을 먼저 가져오세요</option>' + sources.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
    $('vsSource').value = sources.some(a => a.id === selected) ? selected : sources.at(-1)?.id || '';
    const refs = assets.filter(a => a.role === 'reference');
    $('vsReferences').innerHTML = refs.length ? refs.map(a => `<div class="vs-ref"><button type="button" data-vs-preview="${a.id}" title="${esc(a.name)}">${a.kind === 'image' ? `<img src="${url(a)}" alt="" loading="lazy">` : '<span class="vs-file-mark">▶</span>'}<span>${esc(a.name)}</span></button><a href="${url(a,true)}" download="${esc(a.name)}" title="참조 파일 다운로드">↓</a></div>`).join('') : '<p class="vs-muted">시작·완성 이미지나 참고 영상을 보관하세요.</p>';
    const jobs = project?.jobs || [];
    $('vsJobs').innerHTML = jobs.slice().reverse().map(j => {
      const output = assets.find(a => a.id === j.output);
      const label = {running:'로컬 변환 중',done:'완료',failed:'실패',cancelled:'취소됨',interrupted:'중단됨'}[j.status] || j.status;
      return `<div class="vs-job"><div><strong>${label}</strong><span>${esc(j.message || '이 창을 닫아도 변환은 계속됩니다.')}</span>${j.status === 'running' ? `<progress max="100" value="${j.progress || 0}" aria-label="변환 진행률"></progress>` : ''}</div>${j.status === 'running' ? `<button type="button" data-vs-cancel="${j.id}">취소</button>` : ''}${output ? `<button type="button" data-vs-preview="${output.id}">결과 보기</button><a href="${url(output,true)}" download="${esc(output.name)}">다운로드 ↓</a>` : ''}</div>`;
    }).join('') || '<p class="vs-muted">변환한 영상은 원본과 별도로 여기에 보관됩니다.</p>';
    $('vsRuntime').textContent = state.ffmpeg ? 'LOCAL · FFmpeg 준비됨' : 'FFmpeg 연결 확인 필요';
    if (!preview || !assets.some(a => a.id === preview)) { preview = null; selectPreview(assets.filter(a => a.role === 'export').at(-1) || source()); }
    locks(); schedule();
  }
  function schedule() {
    clearTimeout(timer);
    if (!$('videoStudio').open || !state?.project?.jobs?.some(j => j.status === 'running')) return;
    timer = setTimeout(async () => {
      if (busy) return schedule();
      try { const running = state.project.jobs.filter(j => j.status === 'running').map(j => j.id); state = await request('?project=' + ident); render(); const completed = state.project.jobs.find(j => running.includes(j.id) && j.status === 'done'); if (completed) selectPreview(state.project.assets.find(a => a.id === completed.output)); }
      catch (error) { notice(error.message, true); schedule(); }
    }, 1500);
  }
  async function uploadFiles(files, role) {
    await save();
    for (const file of files) {
      notice(file.name + ' 저장 중…');
      const response = await fetch('/api/video-studio/upload', {method:'POST',headers:{'Content-Type':'application/octet-stream','X-Office-Token':token,'X-Studio-Project':ident,'X-Studio-Role':role,'X-Studio-Filename':encodeURIComponent(file.name)},body:file});
      const data = await response.json();
      if (!response.ok || data.error) throw Error(data.error || '파일을 저장하지 못했습니다.');
      state = data;
      if (role === 'source') { preview = null; $('vsSource').value = ''; }
      render();
      if (role === 'source') selectPreview(source());
      else selectPreview(state.project.assets.filter(a => a.role === 'reference').at(-1));
    }
    notice('파일을 PC에 보관했습니다. Vibes에는 직접 업로드해 주세요.');
  }
  function build() {
    const dialog = document.createElement('dialog'); dialog.id = 'videoStudio'; dialog.className = 'vs-dialog';
    dialog.setAttribute('aria-labelledby','vsHeading');
    dialog.innerHTML = `<header class="vs-header"><div><span class="vs-eyebrow">AEMEATH / MANUAL STUDIO</span><h2 id="vsHeading">영상 제작실 <span>직접 만들고, 로컬에서 마무리</span></h2></div><button type="button" data-vs-close aria-label="제작실 닫기">✕</button></header>
      <div class="vs-toolbar"><label>작업 보관함<select id="vsProject" aria-label="저장된 작업"></select></label><button type="button" id="vsNew" data-vs-write>＋ 새 작업</button><span id="vsRuntime" class="vs-runtime"></span><a href="https://vibes.ai/" target="_blank" rel="noopener noreferrer" class="vs-primary">영상 만들기 · Vibes 열기 ↗</a></div>
      <p class="vs-flow">01 프롬프트·참조 준비 <span>→</span> 02 Vibes에서 직접 생성 <span>→</span> 03 결과 가져와 투명화</p>
      <p class="vs-note">이 제작실은 AI를 호출하지 않습니다. 영상 생성은 Vibes에서 직접 진행하며 해당 서비스의 계정·이용 조건이 적용됩니다.</p>
      <div id="vsNotice" class="vs-notice" role="status" aria-live="polite"></div>
      <div class="vs-layout"><section class="vs-inputs" aria-label="제작 준비"><div class="vs-section"><div class="vs-section-title"><h3><b>01</b> 프롬프트와 참조</h3><span id="vsSaved" class="vs-muted"></span></div><label>작업 이름<input id="vsTitle" maxlength="120" placeholder="예: THE HAND 로고 루프"></label><label>프롬프트<textarea id="vsPrompt" maxlength="20000" rows="10" placeholder="장면, 카메라, 동작, 마지막 화면 순서로 적으세요."></textarea></label><div class="vs-generation"><h4>Vibes 생성 준비 · 직접 적용</h4><div class="vs-key-controls"><label>희망 화질<select id="vsGenerationResolution"><option value="480">480p</option><option value="720" selected>720p</option></select></label><label>희망 길이 (초)<input id="vsGenerationSeconds" type="number" min="1" max="60" step="1" value="8"></label></div><p class="vs-muted">선택값은 복사한 프롬프트에 포함됩니다. Vibes 고급 설정에서 지원하는 화질·길이를 직접 선택하세요. 프롬프트만으로 정확한 화질·길이가 보장되지는 않습니다.</p></div><div class="vs-actions"><button type="button" id="vsSave" data-vs-write>작업 저장</button><button type="button" id="vsCopy">프롬프트 복사</button><button type="button" id="vsPreset">로고 조립 예문 넣기</button></div><div id="vsCopyFallback" hidden><label>설정이 포함된 복사용 문장<textarea id="vsCopyText" readonly rows="5"></textarea></label></div><p class="vs-muted">예문은 작성용 초안입니다. 한 번에 여러 동작이 어렵다면 상승·조립·회전·하강을 짧은 클립으로 나누세요.</p><label class="vs-file">＋ 참조 이미지·영상 보관<input id="vsReferenceFile" type="file" accept=".png,.jpg,.jpeg,.webp,.mp4,.webm" multiple></label><div id="vsReferences" class="vs-references"></div></div>
      <div class="vs-section"><h3><b>03</b> 결과 가져오기 · 배경 투명화</h3><label class="vs-file">＋ Vibes에서 받은 결과 영상 가져오기<input id="vsSourceFile" type="file" accept=".mp4,.webm"></label><label>변환할 원본<select id="vsSource"></select></label><div class="vs-key-controls"><label>배경 처리<select id="vsMode"><option value="green">초록 배경 제거</option><option value="blue">파랑 배경 제거</option><option value="custom">직접 색 지정</option><option value="none">기존 투명도 유지 · WebM 변환</option></select></label><label id="vsColorLabel" hidden>제거할 색<input id="vsColor" type="color" value="#00ff00"></label></div><div class="vs-key-controls" id="vsRanges"><label>색 허용 범위 <output id="vsSimilarityValue">0.18</output><input id="vsSimilarity" type="range" min="0.01" max="0.6" step="0.01" value="0.18"></label><label>가장자리 부드러움 <output id="vsBlendValue">0.06</output><input id="vsBlend" type="range" min="0" max="0.3" step="0.01" value="0.06"></label></div><p class="vs-muted">단색 배경 제거용입니다. 피사체에 같은 색이 있으면 함께 지워질 수 있습니다. 복잡한 배경의 자동 분리는 지원하지 않습니다.</p><div class="vs-key-controls"><label>WebM 출력 크기<select id="vsOutputEdge"><option value="1920">원본 크기 유지 · 최대 1920px</option><option value="1080">최대 1080px</option><option value="720">최대 720px</option></select></label><label>앞에서 자를 길이 (초)<input id="vsOutputSeconds" type="number" min="0" max="60" step="0.1" value="0"></label></div><p id="vsOutputNote" class="vs-muted"></p><button type="button" id="vsConvert" class="vs-primary" data-vs-write>로컬에서 투명 WebM 만들기</button><p class="vs-muted">입력: MP4·WebM / 100MB·60초 이하. 출력: 알파 WebM, 최대 1920px. 변환 한 번에 1개. 원본은 유지됩니다.</p></div></section>
      <section class="vs-output" aria-label="미리보기와 사용팁"><div class="vs-preview-card"><div class="vs-section-title"><h3>미리보기</h3><button type="button" id="vsPlay" disabled>▶ 미리보기 재생</button><div class="vs-backgrounds" role="group" aria-label="미리보기 배경"><button type="button" data-vs-bg="checker" aria-label="체크무늬 배경" aria-pressed="true">▦</button><button type="button" data-vs-bg="white" aria-label="흰 배경" aria-pressed="false">○</button><button type="button" data-vs-bg="dark" aria-label="어두운 배경" aria-pressed="false">●</button></div></div><p id="vsPlayback" class="vs-playback" role="status">참조 이미지나 결과 영상을 선택해 주세요.</p><div id="vsPreview" class="vs-preview" data-bg="checker"><div id="vsEmpty"><span>▶</span><p>결과 영상을 가져오면 여기에 표시됩니다.</p></div><video id="vsVideo" controls playsinline loop muted preload="auto" hidden></video><img id="vsStill" alt="선택한 참조 이미지" hidden></div><div class="vs-preview-caption"><span id="vsPreviewName">원본과 변환 결과를 직접 비교하세요.</span><a id="vsDownload" hidden>다운로드 ↓</a></div></div><div class="vs-section"><h3>변환 보관함</h3><div id="vsJobs"></div></div>
      <details class="vs-tips" open><summary>사용팁 · 영상에서 확인한 내용</summary><p>돈디AI의 <a href="https://www.youtube.com/watch?v=mpMo2GIynHw" target="_blank" rel="noopener noreferrer">Vibes·힉스필드 활용 영상 ↗</a>에서 확인한 구간입니다. 현재 화면과 제공 조건은 달라질 수 있습니다.</p><ol><li><a href="https://www.youtube.com/watch?v=mpMo2GIynHw&t=210s" target="_blank" rel="noopener noreferrer">3:30 · 해상도 설정 확인</a><span>영상에서는 고급 설정의 720p를 소개합니다.</span></li><li><a href="https://www.youtube.com/watch?v=mpMo2GIynHw&t=246s" target="_blank" rel="noopener noreferrer">4:06 · 어색한 부분만 수정</a><span>참조 이미지에서 고칠 부분을 좁혀 수정합니다.</span></li><li><a href="https://www.youtube.com/watch?v=mpMo2GIynHw&t=274s" target="_blank" rel="noopener noreferrer">4:34 · 참조 이미지부터 시작</a><span>이미지를 시작 프레임으로 올리고 영상으로 만듭니다.</span></li></ol><p><strong>제작실 적용 팁</strong> 카메라를 고정하고 한 클립에 한 동작부터 시도하세요. 투명화할 영상은 피사체와 겹치지 않는 균일한 단색 배경으로 만드세요. 이 부분은 제작실의 권장 방식입니다.</p><p class="vs-muted">영상 제목의 ‘무료·무제한’은 확인된 이용 보장이 아닙니다. 투명 WebM 출력은 이 제작실의 로컬 기능입니다.</p></details></section></div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener('close', () => { clearTimeout(timer); $('vsVideo').pause(); opener?.focus(); });
    dialog.querySelector('[data-vs-close]').onclick = () => dialog.close();
    $('vsTitle').oninput = cacheDraft; $('vsPrompt').oninput = cacheDraft;
    $('vsSave').onclick = () => exclusive(async () => { await save(); notice('작업을 PC에 저장했습니다.'); });
    $('vsNew').onclick = () => exclusive(async () => { await save(); ident = uid(); preview = null; $('vsTitle').value = '새 영상 작업'; $('vsPrompt').value = ''; fillPreferences({}); await save(); });
    $('vsProject').onchange = event => { const next = event.target.value; exclusive(async () => { await save(); ident = next; preview = null; state = await request('?project=' + ident); fillDraft(state.project); render(); }); };
    $('vsCopy').onclick = async () => {
      const options = preferences();
      const text = $('vsPrompt').value + `\n\nTarget duration: ${options.generation_seconds} seconds. Target resolution: ${options.generation_resolution}p.`;
      try { await navigator.clipboard.writeText(text); notice('희망 화질·길이를 포함해 복사했습니다. Vibes에 붙여넣고 실제 설정도 직접 선택해 주세요.'); }
      catch (_) { $('vsCopyText').value = text; $('vsCopyFallback').hidden = false; $('vsCopyText').focus(); $('vsCopyText').select(); notice('아래 복사용 문장을 Ctrl+C로 복사해 주세요.'); }
    };
    for (const id of ['vsGenerationResolution','vsGenerationSeconds','vsOutputEdge','vsOutputSeconds']) $(id).oninput = cacheDraft;
    $('vsPlay').onclick = async () => {
      const video = $('vsVideo');
      try { if (video.paused) await video.play(); else video.pause(); }
      catch (error) { playbackState('재생을 시작하지 못했습니다. 파일 다운로드 후 로컬 플레이어에서 확인해 주세요.'); }
    };
    $('vsVideo').onloadeddata = () => playbackState('재생 준비됨 · 위의 미리보기 재생을 누르세요.');
    $('vsVideo').onplay = () => playbackState('미리보기 재생 중');
    $('vsVideo').onpause = () => playbackState('일시정지 · 미리보기 재생을 눌러 계속 보세요.');
    $('vsVideo').onerror = () => { const code = $('vsVideo').error?.code; playbackState(code === 4 ? '이 환경에서 지원하지 않는 영상 형식입니다. 파일을 다운로드해 확인해 주세요.' : code === 3 ? '영상 디코딩에 실패했습니다. 다른 MP4 원본으로 확인해 주세요.' : '영상을 불러오지 못했습니다. 원본을 다시 선택하거나 파일을 다운로드해 주세요.'); };
    $('vsStill').onerror = () => playbackState('참조 이미지를 불러오지 못했습니다. 다른 이미지를 선택해 주세요.');
    $('vsPreset').onclick = () => { if ($('vsPrompt').value.trim()) { notice('작성 중인 내용이 있습니다. 새 작업에서 예문을 넣어 주세요.'); return; } $('vsPrompt').value = preset; cacheDraft(); };
    $('vsReferenceFile').onchange = event => { const files = [...event.target.files]; event.target.value = ''; exclusive(() => uploadFiles(files,'reference')); };
    $('vsSourceFile').onchange = event => { const files = [...event.target.files]; event.target.value = ''; exclusive(() => uploadFiles(files,'source')); };
    $('vsSource').onchange = () => { selectPreview(source()); locks(); };
    $('vsMode').onchange = () => { $('vsColorLabel').hidden = $('vsMode').value !== 'custom'; $('vsRanges').hidden = $('vsMode').value === 'none'; };
    for (const key of ['Similarity','Blend']) $('vs' + key).oninput = () => $('vs' + key + 'Value').value = $('vs' + key).value;
    $('vsConvert').onclick = () => exclusive(async () => { const asset = source(); await save(); state = await request('/convert',{project:ident,asset:asset.id,request_id:uid(),mode:$('vsMode').value,color:$('vsColor').value,similarity:Number($('vsSimilarity').value),blend:Number($('vsBlend').value),preferences:preferences()}); render(); notice('이 PC에서 변환을 시작했습니다. 완료 후 결과 보기를 눌러 확인하세요.'); });
    dialog.addEventListener('click', event => {
      const button = event.target.closest('button'); if (!button) return;
      if (button.dataset.vsPreview) selectPreview(state.project.assets.find(a => a.id === button.dataset.vsPreview));
      if (button.dataset.vsCancel) exclusive(async () => { state = await request('/cancel',{project:ident,job:button.dataset.vsCancel}); render(); notice('취소를 요청했습니다.'); });
      if (button.dataset.vsBg) { $('vsPreview').dataset.bg = button.dataset.vsBg; dialog.querySelectorAll('[data-vs-bg]').forEach(x => x.setAttribute('aria-pressed',String(x === button))); }
    });
  }
  async function open() {
    if (!$('videoStudio')) build();
    opener = document.activeElement;
    if (!$('videoStudio').open) $('videoStudio').showModal();
    await exclusive(async () => {
      state = await request();
      if (ident && state.projects.some(p => p.id === ident)) { state = await request('?project=' + ident); }
      else if (state.projects.length) { ident = state.projects[0].id; state = await request('?project=' + ident); }
      else { ident = uid(); $('vsTitle').value = '새 영상 작업'; $('vsPrompt').value = ''; await save(); }
      fillDraft(state.project); render(); $('vsTitle').focus();
    });
  }
  window.OfficeVideoStudio = Object.freeze({open});
})();
