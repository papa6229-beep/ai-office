/* Personal work surface. All writes use an idempotency key, including explicit retries. */
(() => {
  'use strict';
  let overview = null, selected = null, busy = false, loading = false, uncertain = null;
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusNames = {todo:'예정', review:'검토중', doing:'진행중', done:'완료'};
  const stamp = value => { if (!value) return ''; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(d); };
  let followingChat = true, chatScrollQueued = false, chatScrollIntentUntil = 0, chatResizeObserver = null;
  function settleChatScroll(force = false) {
    if (force) followingChat = true;
    if (!followingChat || chatScrollQueued) return;
    chatScrollQueued = true;
    const frame = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
    // Notices, context and the unlocked composer can change the log height after
    // render(). Wait for that layout, then check once more on the next frame.
    frame(() => {
      const log = $('workMessages');
      if (followingChat && log) log.scrollTop = log.scrollHeight;
      frame(() => {
        chatScrollQueued = false;
        if (followingChat && log) log.scrollTop = log.scrollHeight;
      });
    });
  }
  function observeChatLayout() {
    const log = $('workMessages');
    if (!log || typeof ResizeObserver !== 'function') return;
    chatResizeObserver ||= new ResizeObserver(() => settleChatScroll());
    chatResizeObserver.disconnect();
    chatResizeObserver.observe(log);
    if (log.lastElementChild) chatResizeObserver.observe(log.lastElementChild);
  }
  let companionState = null, companionOutcome = null, companionError = '';
  function waitingForSelection() {
    const messages = overview?.messages || [];
    return [...(overview?.launcher_choices || []), ...(overview?.assistant_actions || [])].some(card => card.options?.length && messages.some(m => m.role === 'assistant' && m.id === card.message_id));
  }
  function companionUpdate() {
    const stage=$('workCompanion'), animation=$('workCompanionAnimation'), still=$('workCompanionStill'), status=$('workCompanionStatus');
    if (!stage || !animation || !still || !status) return;
    let next='idle', label='이야기를 기다리고 있어요';
    if (busy) {next='working';label='요청 처리 중';}
    else if (companionError || uncertain) {next='error';label=uncertain ? '결과 확인이 필요해요' : companionError;}
    else if ($('workCommand')?.value.trim()) {next='typing';label='이야기를 듣고 있어요';}
    else if (waitingForSelection()) {label='선택을 기다리고 있어요';}
    else if (companionOutcome) {next=companionOutcome.state;label=companionOutcome.label;}
    if (next !== companionState) {
      companionState=next;stage.setAttribute?.('data-state',next);
      stage.classList.toggle('is-gif-unavailable',false);stage.classList.toggle('is-still-unavailable',false);
      animation.src='/assets/aemeath/'+next+'.gif';still.src='/assets/aemeath/'+next+'.png';
    }
    if (status.textContent !== label) status.textContent=label;
  }
  function notice(message = '', error = false) {
    $('workNotice').textContent = message; $('workNotice').hidden = !message;
    $('workNotice').classList.toggle('work-error', error);
    settleChatScroll();
  }
  function locks() {
    const blocked = busy || !!uncertain || !overview?.action_token;
    $('work').querySelectorAll('button,input,textarea').forEach(el => {
      if (el.closest('#workLauncher,#workLauncherDialog,#workCalendar,#workFileDialog,#workStage,#workStageDialog') || el.matches('[data-work-preview]') || el.matches('[data-work-action="close-detail"],[data-files-action]')) return;
      if (el.matches('[data-work-action="desk"],[data-work-action="tasks"]')) return;
      if (el.matches('[data-work-action="reload"]')) { el.disabled = busy || !!uncertain || loading; return; }
      if (el.matches('[data-work-action="retry"]')) { el.disabled = busy; return; }
      el.disabled = blocked;
    });
    const reload=$('workUtilities')?.querySelector('[data-work-action="reload"]');
    if(reload)reload.disabled=busy || !!uncertain || loading;
    $('workRetry').hidden = !uncertain;
    $('workSend').textContent = busy ? '처리 중…' : '요청 보내기';
    $('workConnection').textContent = busy ? '요청 처리 중' : overview ? '업무 기록 연결됨' : '연결 필요';
    companionUpdate();
    settleChatScroll();
  }
  const taskHistory = new Map();
  let historySequence = 0, historyNotice = '';
  async function loadHistory(taskID) {
    const sequence = ++historySequence;
    historyNotice = '진행 기록을 불러오는 중…'; renderDetail(); locks();
    try {
      const response = await fetch('/api/work/task-history?id='+encodeURIComponent(taskID), {cache:'no-store'}), data = await response.json();
      if (!response.ok || data.error || data.task_id !== taskID || !Array.isArray(data.events)) throw new Error(data.error || '업무 이력을 불러오지 못했습니다.');
      if (sequence !== historySequence || selected !== taskID || !$('workTaskDialog').open) return;
      taskHistory.set(taskID, data.events); historyNotice = data.truncated ? '최근 1,000개의 진행 기록을 표시합니다.' : '';
    } catch (e) {
      if (sequence !== historySequence || selected !== taskID || !$('workTaskDialog').open) return;
      historyNotice = '전체 이력 연결 실패: '+e.message+' 현재 불러온 기록을 표시합니다.';
    }
    renderDetail(); locks();
  }
  function renderDetail(preserve = true) {
    const noteDraft = preserve ? $('workNoteText')?.value || '' : '';
    const pathDraft = preserve ? $('workResourcePath')?.value || '' : '';
    const task = overview?.tasks?.find(t => t.id === selected);
    $('workClearContext').hidden = !task;
    $('workContext').textContent = task ? `선택한 업무: ${task.title}` : '새 업무 또는 오늘 전체 업무에 대해 이야기합니다.';
    if (!task) { selected = null; $('workTaskDetailPanel').innerHTML = '<p class="work-empty">업무를 선택하면 진행 상태, 메모, 연결 자료를 볼 수 있습니다.</p>'; return; }
    const events = (taskHistory.get(task.id) || overview.events || []).filter(e => e.task_id === task.id).slice().sort((a,b) => String(a.created).localeCompare(String(b.created)));
    const eventNames = {created:'업무 등록', status:'상태 변경', note:'진행 메모', resource:'자료 연결'};
    const timeline = events.map(e => `<li><time>${escape(stamp(e.created))}</time><strong>${escape(eventNames[e.kind] || e.kind || '진행 기록')}</strong><p>${escape(e.kind === 'status' ? (statusNames[e.text] || e.text) : e.text)}</p></li>`).join('');
    $('workTaskDetailPanel').innerHTML = `<h4>${escape(task.title)}</h4><span class="work-state ${escape(task.status)}">${escape(statusNames[task.status] || task.status)}</span> <span class="work-muted">${task.due_date ? '마감 '+escape(task.due_date) : '마감일 없음'}</span>${task.detail ? `<div class="work-task-description">${escape(task.detail)}</div>` : ''}
      <div class="work-status-actions">${[['todo','예정으로 표시'],['review','검토중으로 표시'],['doing','진행중으로 표시'],['done','완료로 표시']].filter(([s])=>s!==task.status).map(([s,label])=>`<button type="button" class="work-secondary" data-work-status="${s}">${label}</button>`).join('')}</div>
      <h5>진행 기록</h5>${historyNotice ? `<p class="work-history-note" role="status">${escape(historyNotice)}</p>` : ''}<ol class="work-timeline">${timeline || '<li class="work-muted">아직 진행 기록이 없습니다.</li>'}</ol><h5>진행 메모</h5>${(task.notes || []).map(n=>`<div class="work-note">${escape(n.text)}<time>${escape(stamp(n.created))}</time></div>`).join('') || '<p class="work-muted">진행한 내용과 다음 할 일을 남겨두세요.</p>'}
      <form id="workNoteForm"><label class="work-sr-only" for="workNoteText">진행 메모</label><textarea id="workNoteText" rows="2" maxlength="6000" required placeholder="진행 내용 기록">${escape(noteDraft)}</textarea><div class="work-form-actions"><button type="submit" class="work-secondary">메모 남기기</button></div></form>
      <h5>연결 자료</h5>${(task.resources || []).map(r=>`<div class="work-resource">${escape(r.name || r.path)}<span class="work-resource-path">${escape(r.path)}${r.exists === false ? ' (경로를 찾을 수 없음)' : ''}</span>${r.exists === false ? '' : `<button type="button" class="work-quiet" data-work-preview="${escape(r.id)}">미리보기</button><button type="button" class="work-quiet" data-work-resource="${escape(r.id)}">열기</button>`}</div>`).join('')}
      <form id="workResourceForm"><label for="workResourcePath">파일 또는 폴더 경로</label><div class="work-resource-entry"><input id="workResourcePath" maxlength="4096" required value="${escape(pathDraft)}" placeholder="경로 직접 입력"><button type="button" class="work-quiet" data-work-action="pick-resource">폴더 선택</button></div><div class="work-form-actions"><button type="submit" class="work-secondary">자료 연결</button></div></form><p class="work-muted">원본 위치를 업무에 연결합니다.</p>`;
  }
  function render() {
    const tasks = overview.tasks || [], counts = overview.counts || {};
    $('workCounts').textContent = `예정 ${counts.todo ?? 0} · 검토중 ${counts.review ?? 0} · 진행중 ${counts.doing ?? 0} · 완료 ${counts.done ?? 0}`;
    $('workTaskList').innerHTML = tasks.length ? tasks.map(t=>`<button type="button" class="work-task-row" data-work-task="${escape(t.id)}" aria-pressed="${t.id === selected}"><span class="work-state ${escape(t.status)}">${escape(statusNames[t.status] || t.status)}</span><span class="work-task-copy"><span class="work-task-name">${escape(t.title)}</span><small>${t.due_date ? '마감 '+escape(t.due_date) : '마감일 없음'}</small><span class="work-task-badges">${t.notes?.length ? `<span class="work-task-badge">메모 ${t.notes.length}</span>` : ''}${t.resources?.length ? `<span class="work-task-badge">자료 ${t.resources.length}</span>` : ''}${(overview.events || []).some(e=>e.task_id === t.id) ? `<span class="work-task-badge">기록 ${(overview.events || []).filter(e=>e.task_id === t.id).length}</span>` : ''}</span></span><span aria-hidden="true">›</span></button>`).join('') : '<div class="work-empty work-task-empty"><span class="work-empty-mark" aria-hidden="true">＋</span><div><strong>오늘 할 일을 여기에 모아보세요</strong><p>비서에게 말하거나 ‘업무 추가’로 등록하세요.</p></div></div>';
    const messages = overview.messages || [];
    const log = $('workMessages');
    const choices = overview.launcher_choices || [], actions = overview.assistant_actions || [];
    log.innerHTML = messages.length ? messages.map(m=>{
      const options = m.role === 'assistant' ? choices.filter(c=>c.message_id === m.id).map(c=>`<div class="work-launcher-choice" role="group" aria-label="${escape(c.name)} 프로그램 선택"><span>${escape(c.name)} · 사용할 프로그램을 골라주세요.</span>${(c.options || []).map(option=>`<button type="button" class="work-secondary" data-work-launcher-choice="${escape(c.id)}" data-work-launcher-target="${escape(option.target)}"><strong>${escape(option.title)}</strong>${option.detail ? `<small>${escape(option.detail)}</small>` : ''}</button>`).join('')}</div>`).join('') : '';
      const pending = m.role === 'assistant' ? actions.filter(a=>a.message_id === m.id).map(a=>`<div class="work-assistant-action${a.kind === 'approval' ? ' work-action-approval' : ''}" role="group" aria-label="${escape(a.title)}"><b>${escape(a.title)}</b>${a.description ? `<p>${escape(a.description)}</p>` : ''}<div>${(a.options || []).map(option=>`<button type="button" class="work-secondary" data-work-assistant-action="${escape(a.id)}" data-work-assistant-option="${escape(option.id)}">${escape(option.label)}${option.description ? `<small>${escape(option.description)}</small>` : ''}</button>`).join('')}</div></div>`).join('') : '';
      return `<div class="work-message ${m.role === 'user' ? 'work-user' : ''}"><strong>${m.role === 'user' ? '나' : 'Aemeath'}</strong>${escape(m.content)}${options}${pending}</div>`;
    }).join('') : '<p class="work-empty">무슨 일을 해야 하나요?<br>업무를 등록하거나 바로가기에 추가할 프로그램을 알려주세요.</p>';
    renderDetail();
    locks();
    observeChatLayout();
    settleChatScroll();
  }
  window.workLoad = async function workLoad() {
    if (!$('work') || loading || busy || uncertain) return;
    loading = true; companionOutcome=null; companionError=''; companionUpdate();
    try {
      const response = await fetch('/api/work', {cache:'no-store'}), data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || '업무를 불러오지 못했습니다.');
      const initialLoad = !overview;
      overview = data; taskHistory.clear(); notice(); render();
      settleChatScroll(initialLoad);
      if ($('workTaskDialog').open && selected) loadHistory(selected);
    } catch (e) {
      companionError='연결을 확인해 주세요';
      notice(`업무 연결 실패: ${e.message} 화면을 다시 열어 재연결하세요.`, true);
      if (!overview) {
        $('workMessages').innerHTML = '<p class="work-empty">업무 기록에 연결하지 못했습니다.</p>';
        $('workTaskList').innerHTML = '<p class="work-empty">연결 후 업무를 확인할 수 있습니다.</p>';

      }
      locks();
    } finally { loading = false; locks(); }
  };
  function requestID() { return Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join(''); }
  async function write(endpoint, payload, cleanup, existing) {
    if (busy || (!existing && uncertain) || !overview?.action_token) return;
    const request = existing || {endpoint, body:{...payload, token:overview.action_token, request_id:requestID()}, cleanup};
    companionError=''; companionOutcome=null; busy = true; notice(); locks();
    let confirmedResponse = false;
    const stageRequest=['command','assistant-action','choose-launcher'].includes(request.endpoint);
    const stageEvent=(phase,result)=>{if(stageRequest)document.dispatchEvent(new CustomEvent('work-stage-request',{detail:{requestId:request.body.request_id,message:request.body.message || '선택한 요청 처리',phase,result}}));};
    stageEvent('start');
    try {
      if (existing) {
        // A server restart rotates the action token. Refresh only that field:
        // the original request ID and operation must survive an uncertain result.
        const sessionResponse = await fetch('/api/work', {cache:'no-store'});
        const session = await sessionResponse.json();
        if (!sessionResponse.ok || session.error || !session.action_token) {
          throw new Error(session.error || '재시도에 필요한 연결 정보를 가져오지 못했습니다.');
        }
        request.body.token = session.action_token;
        overview.action_token = session.action_token;
      }
      const response = await fetch('/api/work/'+request.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body)});
      const data = await response.json();
      // Authentication rejection and an in-flight request do not establish
      // whether the original attempt committed. Keep the same retry request.
      const unresolved = response.status === 403 || response.status === 409 || response.status === 202 ||
        /처리\s*중|processing|in[_ -]?progress/i.test([data.error, data.code, data.status].filter(Boolean).join(' '));
      confirmedResponse = !unresolved;
      if (unresolved) throw new Error(data.error || '요청을 처리 중입니다. 잠시 후 같은 요청으로 다시 확인하세요.');
      if (!response.ok || data.error) throw new Error(data.error || '요청을 처리하지 못했습니다.');
      uncertain = null; overview = data; stageEvent('done',data.result);
      companionOutcome=request.endpoint === 'command' ? {state:'answer',label:'답변이 도착했어요'} : {state:'agree',label:['task','note','status','resource'].includes(request.endpoint) ? '기록을 저장했어요' : '응답을 확인했어요'};
      if (data.result?.task_id && data.result.task_id !== selected) {
        selected = data.result.task_id; renderDetail(false);
      }
      taskHistory.delete(selected); request.cleanup?.(); render();
      if (stageRequest) settleChatScroll(true);
      if ($('workTaskDialog').open && selected) loadHistory(selected);
      // Conversation results are already rendered in workMessages. Keep the
      // banner for operation receipts and actual failures, not a second reply.
      const toolFailure = data.result?.tool_results?.find(result => ['error','uncertain'].includes(result.status));
      if (!stageRequest || toolFailure) {
        const summary = (toolFailure?.message || data.result?.message || '업무 기록을 저장했습니다.').split('\n')[0];
        notice(summary.length > 160 ? summary.slice(0,160)+'…' : summary, !!toolFailure);
      }
      if (data.result?.launcher_changed) document.dispatchEvent(new CustomEvent('work-launcher-changed'));
      if (data.result?.desk_changed) document.dispatchEvent(new CustomEvent('work-desk-changed'));
    } catch (e) {
      stageEvent(confirmedResponse ? 'error' : 'uncertain',{message:e.message});
      if (!confirmedResponse) uncertain = request;
      else uncertain = null;
      companionOutcome=null;companionError=confirmedResponse ? '요청을 확인해 주세요' : '결과 확인이 필요해요';
      notice(`${confirmedResponse ? '요청 실패' : '처리 결과 확인 실패'}: ${e.message}`, true);
    } finally { busy = false; locks(); }
  }
  function submit(event) {
    const form = event.target;
    if (!['workCommandForm','workTaskForm','workNoteForm','workResourceForm'].includes(form.id)) return;
    event.preventDefault();
    if (busy || uncertain) return;
    if (form.id === 'workCommandForm') {
      const message = $('workCommand').value.trim(); if (!message) return;
      write('command',{message,...(selected ? {task_id:selected} : {})},()=>{$('workCommand').value='';});
    } else if (form.id === 'workTaskForm') {
      const title = $('workTaskTitle').value.trim(); if (!title) return;
      write('task',{title,detail:$('workTaskDetail').value.trim(),due_date:$('workTaskDue').value},()=>{form.reset(); form.hidden=true;});
    } else if (form.id === 'workNoteForm') {
      const text = $('workNoteText').value.trim(); if (!text || !selected) return;
      write('note',{task_id:selected,text},()=>{if ($('workNoteText')) $('workNoteText').value='';});
    } else {
      const path = $('workResourcePath').value.trim(); if (!path || !selected) return;
      write('resource',{task_id:selected,path},()=>{if ($('workResourcePath')) $('workResourcePath').value='';});
    }
  }
  async function click(event) {
    const button = event.target.closest('button'); if (!button || button.disabled) return;
    if (button.dataset.workAssistantAction) { write('assistant-action',{pending_id:button.dataset.workAssistantAction,option_id:button.dataset.workAssistantOption}); return; }
    if (button.dataset.workLauncherChoice) { write('choose-launcher',{choice_id:button.dataset.workLauncherChoice,target:button.dataset.workLauncherTarget}); return; }
    if (button.dataset.workTask) { selected = button.dataset.workTask; renderDetail(false); render(); $('workTaskDialog').showModal(); loadHistory(selected); return; }
    if (button.dataset.workExample) { $('workCommand').value=button.dataset.workExample; companionOutcome=null;companionUpdate(); $('workCommand').focus(); return; }
    if (button.dataset.workStatus) { write('status',{task_id:selected,status:button.dataset.workStatus}); return; }
    if (button.dataset.workPreview) {
      try {const response=await fetch('/api/work/preview-resource',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:overview?.action_token,resource_id:button.dataset.workPreview})});const data=await response.json();if(!response.ok || data.error)throw Error(data.error || '미리보기를 열지 못했습니다.');document.dispatchEvent(new CustomEvent('work-stage-preview',{detail:{artifact:data.artifact}}));if($('workTaskDialog').open)$('workTaskDialog').close();}catch(e){notice(e.message,true);}return;
    }
    if (button.dataset.workResource) { write('open-resource',{resource_id:button.dataset.workResource}); return; }
    switch (button.dataset.workAction) {
      case 'close-detail': historySequence++; $('workTaskDialog').close(); break;
      case 'reload': window.workLoad(); break;
      case 'desk': show('desk'); break;
      case 'tasks': show('tasks'); break;
      case 'clear-context': selected=null; render(); break;
      case 'add-task': $('workTaskForm').hidden=false; $('workTaskTitle').focus(); break;
      case 'cancel-task': $('workTaskForm').hidden=true; break;
      case 'pick-resource': await pickFolder('workResourcePath'); break;
      case 'retry': if (uncertain) await write(null,null,null,uncertain); break;

    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('work')) return;
    const masthead=$('workMasthead'), brand=document.querySelector('.side .brand');
    function alignMasthead() {
      if (!masthead || !brand || !document.body.classList.contains('work-desktop')) return;
      const ruleTop=brand.getBoundingClientRect().bottom-(parseFloat(getComputedStyle(brand).borderBottomWidth) || 0);
      const height=Math.max(0,ruleTop-$('work').getBoundingClientRect().top);
      const value=height.toFixed(2)+'px';
      if (masthead.style.getPropertyValue('--work-masthead-height') !== value) masthead.style.setProperty('--work-masthead-height',value);
    }
    window.addEventListener('resize',alignMasthead);
    if (brand && typeof ResizeObserver === 'function') new ResizeObserver(alignMasthead).observe(brand);
    if (typeof MutationObserver === 'function') new MutationObserver(alignMasthead).observe(document.body,{attributes:true,attributeFilter:['class']});
    document.fonts?.ready?.then(alignMasthead);
    alignMasthead();
    $('workTaskDialog').addEventListener('close',()=>{historySequence++; if (selected) $('workTaskList').querySelector(`[data-work-task="${CSS.escape(selected)}"]`)?.focus();});
    $('work').addEventListener('submit',submit); $('work').addEventListener('click',click);
    $('workUtilities')?.addEventListener('click',click);
    const chatLog = $('workMessages');
    const chatScrollIntent = () => { chatScrollIntentUntil = Date.now()+1500; };
    chatLog.addEventListener('wheel',event=>{
      chatScrollIntent();
      if (event.deltaY < 0) followingChat = false;
    },{passive:true});
    chatLog.addEventListener('touchmove',chatScrollIntent,{passive:true});
    chatLog.addEventListener('pointerdown',chatScrollIntent,{passive:true});
    chatLog.addEventListener('keydown',event=>{
      if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)) {
        chatScrollIntent();
        if (['ArrowUp','PageUp','Home'].includes(event.key)) followingChat = false;
      }
    });
    chatLog.addEventListener('scroll',()=>{
      const atBottom = chatLog.scrollHeight-chatLog.scrollTop-chatLog.clientHeight < 8;
      if (atBottom) followingChat = true;
      else if (Date.now() < chatScrollIntentUntil) followingChat = false;
    },{passive:true});
    window.addEventListener('resize',()=>settleChatScroll());
    document.fonts?.ready?.then(()=>settleChatScroll());
    observeChatLayout();
    const command = $('workCommand');
    let composing = false;
    command.addEventListener('input',()=>{companionOutcome=null;companionUpdate();});
    command.addEventListener('change',()=>{companionOutcome=null;companionUpdate();});
    $('workCompanionAnimation')?.addEventListener('error',()=>{$('workCompanion')?.classList.toggle('is-gif-unavailable',true);});
    $('workCompanionStill')?.addEventListener('error',()=>{$('workCompanion')?.classList.toggle('is-still-unavailable',true);});
    command.addEventListener('compositionstart',()=>{composing = true;companionUpdate();});
    command.addEventListener('compositionend',()=>{composing = false;companionUpdate();});
    command.addEventListener('blur',()=>{composing = false;});
    command.addEventListener('keydown',event=>{
      if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
      // IME confirmation can report keyCode 229 even when isComposing is false.
      if (composing || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      if (event.repeat || command.disabled || busy || uncertain || !overview?.action_token || !command.value.trim()) return;
      $('workCommandForm').requestSubmit($('workSend'));
    });
    locks(); window.workLoad();
  });
})();

/* Launcher has its own busy state: assistant requests never lock quick launch. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state = null, busy = false, loading = false, pending = null, editing = null, imageData, imageReading = false, imageVersion = 0, imageURL = '';
  let selectedApp = null, bookmarks = null, bookmarkLoading = false, dialogVersion = 0;
  let launcherRefreshQueued = false;
  let dragItem = null, suppressLaunchUntil = 0, coverData, coverURL = '', coverReading = false, coverVersion = 0;
  let boardLayoutQueued=false, boardLayoutObserver=null;
  function measureBoard() {
    boardLayoutQueued=false;
    $('workLauncherGrid').querySelectorAll('.work-launcher-cell,.work-launcher-add-tile').forEach(cell=>{
      const tile=cell.matches?.('.work-launcher-add-tile') ? cell : cell.querySelector('.work-launcher-tile');
      const height=tile?.getBoundingClientRect?.().height;
      if (!height || !cell.style) return;
      // Eight-pixel tracks plus ten-pixel gutters keep the DOM order intact.
      const span='span '+Math.max(1,Math.ceil((height+10)/18));
      if (cell.style.gridRowEnd !== span) cell.style.gridRowEnd=span;
    });
  }
  function scheduleBoardLayout() {
    if (boardLayoutQueued) return;
    boardLayoutQueued=true;
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(measureBoard);
    else measureBoard();
  }
  function observeBoardLayout() {
    boardLayoutObserver?.disconnect();
    if (typeof ResizeObserver === 'function') {
      boardLayoutObserver ||= new ResizeObserver(scheduleBoardLayout);
      boardLayoutObserver.observe($('workLauncherBoard'));
      $('workLauncherGrid').querySelectorAll('.work-launcher-tile').forEach(tile=>boardLayoutObserver.observe(tile));
    }
    scheduleBoardLayout();
  }
  function boardItems() {
    const items = state?.items || [], known = new Map(items.map(item => [item.id,item]));
    const ordered = [], seen = new Set();
    for (const itemID of state?.board_order || []) {
      if (known.has(itemID) && !seen.has(itemID)) {ordered.push(known.get(itemID));seen.add(itemID);}
    }
    for (const item of items) if (!seen.has(item.id)) {ordered.push(item);seen.add(item.id);}
    return ordered;
  }
  function drainChatRefresh() {
    if (!launcherRefreshQueued || busy || loading || pending || $('workLauncherDialog').open) return;
    launcherRefreshQueued = false;
    load();
  }
  const id = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join('');
  function message(text = '', error = false) {
    $('workLauncherNotice').textContent = text;
    $('workLauncherNotice').classList.toggle('is-error', error);
    if ($('workLauncherDialog').open) $('workLauncherFormNotice').textContent = text;
  }
  function locks() {
    document.querySelectorAll('#workLauncher button,#workLauncherDialog button,#workLauncherDialog input,#workLauncherDialog select,#workLauncherDialog textarea').forEach(el => {
      const action = el.dataset.launcherAction;
      el.disabled = action === 'close' ? false : action === 'retry' ? busy : action === 'reload' ? busy || loading || !!pending : busy || loading || !!pending || !state?.action_token || imageReading || coverReading || bookmarkLoading;
    });
    $('workLauncherRetry').hidden = !pending;
    $('workLauncherDialogRetry').hidden = !pending;
    $('workLauncherSave').textContent = busy ? '저장 중…' : imageReading || coverReading ? '이미지 읽는 중…' : '바로가기 저장';
    $('workLauncher').classList.toggle('is-busy',busy);
    $('workLauncherGrid').querySelectorAll('[data-launcher-action="launch"]').forEach(tile => {tile.draggable = !dragBlocked();});
    const items = boardItems(), index = items.findIndex(x => x.id === editing);
    for (const [name, unavailable] of [['workLauncherMoveUp',index <= 0],['workLauncherMoveDown',index < 0 || index >= items.length-1]]) {
      $(name).disabled = $(name).disabled || unavailable;
    }
  }
  const iconURL = item => '/api/launcher/icon?id='+encodeURIComponent(item.id)+(item.icon_revision ? '&v='+encodeURIComponent(item.icon_revision) : '');
  const coverURLFor = item => '/api/launcher/cover?id='+encodeURIComponent(item.id)+(item.card_cover_revision ? '&v='+encodeURIComponent(item.card_cover_revision) : '');
  function icon(item) {
    return item.icon_image ? `<img src="${iconURL(item)}" alt="" loading="lazy">` : `<span>${esc(item.icon || Array.from(item.title || '✦').slice(0,2).join(''))}</span>`;
  }
  function render() {
    const items = boardItems();
    const tiles = items.map(item => {
      const type = item.kind === 'web' ? ['W','웹사이트'] : item.kind === 'folder' ? ['F','폴더'] : ['P','프로그램'];
      const artwork = item.card_cover
        ? `<img src="${coverURLFor(item)}" alt="" loading="lazy" draggable="false">`
        : `<span class="work-launcher-icon${item.icon_image ? ' is-image' : ''}" aria-hidden="true">${icon(item)}</span>`;
      return `<div class="work-launcher-cell"><button type="button" draggable="true" class="work-launcher-tile${item.card_cover ? ' has-cover' : ''}${item.available === false ? ' is-unavailable' : ''}" data-launcher-action="launch" data-launcher-id="${esc(item.id)}" title="${esc(item.title)} · ${esc(item.target)}" aria-label="${esc(item.title)} 열기${item.available === false ? ' · 경로 확인 필요' : ''}"><span class="work-launcher-cover${item.card_cover ? '' : ' is-icon-artwork'}">${artwork}</span><span class="work-launcher-card-content"><span class="work-launcher-kind" data-launcher-kind="${type[0]}" title="${type[1]}" aria-label="${type[1]}">${type[0]}</span><span class="work-launcher-name">${esc(item.title)}</span><span class="work-launcher-target" title="${esc(item.target)}">${esc(item.target)}</span>${item.card_description ? `<span class="work-launcher-description">${esc(item.card_description)}</span>` : ''}</span></button></div>`;
    }).join('');
    const addTile = '<button type="button" class="work-launcher-tile work-launcher-add-tile" data-launcher-action="add" aria-label="바로가기 카드 추가"><span aria-hidden="true">＋</span><span>새 카드</span></button>';
    $('workLauncherGrid').innerHTML = tiles + addTile;
    $('workLauncherCount').textContent = `${items.length} CARDS · SCROLL TO EXPLORE`;
    observeBoardLayout();
    locks();
  }
  async function load(refresh = false) {
    if (busy || loading || pending) return;
    loading = true; locks();
    try {
      const response = refresh && state?.action_token
        ? await fetch('/api/launcher/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.action_token})})
        : await fetch('/api/launcher',{cache:'no-store'});
      const data = await response.json();
      if (!response.ok || data.error) throw Error(data.error || '바로가기를 불러오지 못했습니다.');
      state = data; message(); render();
    } catch (e) { message(e.message,true); if (!state) $('workLauncherGrid').innerHTML = '<p class="work-launcher-empty">연결 후 바로가기를 확인할 수 있습니다.</p>'; }
    finally { loading = false; locks(); drainChatRefresh(); }
  }
  async function write(endpoint, payload, cleanup, previous) {
    if (busy || (!previous && pending) || !state?.action_token) return;
    const request = previous || {endpoint,body:{...payload,token:state.action_token,request_id:id()},cleanup};
    busy = true; message(request.endpoint === 'launch' ? '열기 요청 중…' : '저장 중…'); locks();
    let confirmed = false;
    try {
      if (previous) {
        const response = await fetch('/api/launcher',{cache:'no-store'}), data = await response.json();
        if (!response.ok || data.error || !data.action_token) throw Error(data.error || '연결 정보를 가져오지 못했습니다.');
        request.body.token = data.action_token; state.action_token = data.action_token;
      }
      const response = await fetch('/api/launcher/'+request.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body)}), data = await response.json();
      const unresolved = [202,403,409].includes(response.status) || /처리\s*중|processing|in[_ -]?progress/i.test([data.error,data.code,data.status].filter(Boolean).join(' '));
      confirmed = !unresolved;
      if (unresolved || !response.ok || data.error) throw Error(data.error || '요청을 처리하지 못했습니다.');
      pending = null; state = data;
      request.cleanup?.(); render(); message(data.result?.message || (request.endpoint === 'launch' ? '열기 요청을 전달했습니다.' : '바로가기를 저장했습니다.'));
    } catch (e) {
      if (!confirmed) pending = request; else pending = null;
      message(`${confirmed ? '요청 실패' : '처리 결과 확인 실패'}: ${e.message}`,true);
    } finally { busy = false; locks(); drainChatRefresh(); }
  }
  function kindFields() {
    const kind = $('workLauncherKind').value;
    const config = {app:['프로그램 전체 경로 (.exe / .lnk)','발견한 프로그램을 선택하거나 전체 경로를 입력하세요.','C:\\Program Files\\...\\app.exe'],web:['웹사이트 주소','http:// 또는 https://로 시작하는 웹사이트 주소를 입력하세요.','https://example.com'],folder:['폴더 전체 경로','작업 폴더를 선택하거나 전체 경로를 입력하세요.','C:\\Users\\...\\Documents']}[kind];
    $('workLauncherTargetLabel').textContent = config[0]; $('workLauncherTargetHelp').textContent = config[1]; $('workLauncherTarget').placeholder = config[2]; $('workLauncherPick').hidden = kind === 'web';
    $('workLauncherKindWrap').hidden = false;
    $('workLauncherSuggestionsWrap').hidden = kind !== 'app';
    $('workLauncherBookmarksWrap').hidden = kind !== 'web';
    const registered = kind === 'app' && !!selectedApp;
    $('workLauncherSelectedApp').hidden = !registered;
    $('workLauncherTargetWrap').hidden = registered;
    $('workLauncherTarget').required = !registered;
    $('workLauncherSelectedAppName').textContent = registered ? 'Windows 시작 메뉴 · '+selectedApp.title : '';
  }
  function appResults() {
    const query=$('workLauncherAppSearch').value.trim().toLocaleLowerCase(), all=state?.suggestions || [];
    const results=all.map((item,index)=>({item,index})).filter(({item})=>[item.title,item.detail,...(item.aliases || [])].filter(Boolean).join(' ').toLocaleLowerCase().includes(query));
    $('workLauncherAppCount').textContent=`${results.length}개 / 전체 ${all.length}개`+(state?.suggestion_source && state.suggestion_source !== 'windows-start-menu' ? ' · 바로가기에서 찾음' : '')+((state?.suggestion_warnings || []).length ? ' · '+state.suggestion_warnings.join(' ') : '');
    $('workLauncherSuggestions').innerHTML=results.length ? results.map(({item,index})=>`<button type="button" class="work-source-result" data-launcher-action="suggestion" data-source-index="${index}"><strong>${esc(item.title)}</strong><small>${esc(item.detail || item.source || (String(item.target).startsWith('startapp:') ? 'Windows 시작 메뉴' : item.target))}</small></button>`).join('') : '<p class="work-source-empty">일치하는 프로그램이 없습니다. 아래에서 실행 파일을 직접 선택할 수 있습니다.</p>';
    locks();
  }
  function bookmarkResults() {
    const query=$('workLauncherBookmarkSearch').value.trim().toLocaleLowerCase(), all=bookmarks?.items || [];
    const profile=$('workLauncherBookmarkProfile').value;
    const results=all.map((item,index)=>({item,index})).filter(({item})=>(!profile || JSON.stringify([item.browser,item.profile]) === profile) && [item.title,item.target,item.folder,item.browser,item.profile].join(' ').toLocaleLowerCase().includes(query));
    $('workLauncherBookmarkCount').textContent=`${results.length}개 / 불러온 북마크 ${all.length}개`+(bookmarks?.truncated ? ' · 일부만 불러옴' : '');
    $('workLauncherBookmarks').innerHTML=results.length ? results.map(({item,index})=>`<button type="button" class="work-source-result" data-launcher-action="bookmark" data-source-index="${index}"><strong>${esc(item.title)}</strong><small>${esc([item.browser,item.profile,item.folder].filter(Boolean).join(' · '))}</small><small>${esc(item.target)}</small></button>`).join('') : '<p class="work-source-empty">일치하는 북마크가 없습니다. 주소를 직접 입력할 수 있습니다.</p>';
    locks();
  }
  function chooseSource(suggestion) {
    if (!suggestion) return;
    selectedApp=String(suggestion.target).startsWith('startapp:') ? suggestion : null;
    $('workLauncherName').value=suggestion.title; $('workLauncherKind').value=suggestion.kind; $('workLauncherTarget').value=selectedApp ? '' : suggestion.target; $('workLauncherIcon').value=suggestion.icon || '✦';
    imageData=''; imageURL=''; imageVersion++; $('workLauncherImage').value='';
    $('workLauncherFormNotice').textContent=`${suggestion.title} 선택됨 · 아래에서 바로가기를 저장하세요.`;
    kindFields(); preview();
  }
  function preview() {
    $('workLauncherIconPreview').replaceChildren();
    const url = imageData || imageURL;
    if (url) { const img = document.createElement('img'); img.src=url; img.alt=''; $('workLauncherIconPreview').append(img); }
    else $('workLauncherIconPreview').textContent = $('workLauncherIcon').value || '✦';
  }
  function coverPreview() {
    const container = $('workLauncherCoverPreview'), url = coverData || coverURL;
    container.replaceChildren(); container.hidden = !url;
    if (url) {const image=document.createElement('img');image.src=url;image.alt='선택한 카드 커버';container.append(image);}
  }
  function open(item) {
    clearDrag();
    dialogVersion++; selectedApp=item?.kind === 'app' && String(item.target).startsWith('startapp:') ? item : null;
    editing = item?.id || null; imageData = undefined; imageURL = item?.icon_image ? iconURL(item) : ''; imageVersion++; imageReading=false;
    coverData=undefined;coverURL=item?.card_cover ? coverURLFor(item) : '';coverVersion++;coverReading=false;
    $('workLauncherForm').reset(); $('workLauncherDialogTitle').textContent = editing ? '바로가기 수정' : '바로가기 추가';
    $('workLauncherItemActions').hidden = !editing;
    ['workLauncherRemove','workLauncherMoveUp','workLauncherMoveDown'].forEach(name => {$(name).dataset.launcherId = editing || '';});
    $('workLauncherName').value = item?.title || ''; $('workLauncherKind').value = item?.kind || 'app'; $('workLauncherTarget').value = selectedApp ? '' : item?.target || ''; $('workLauncherIcon').value = item?.icon || '✦'; $('workLauncherFormNotice').textContent='';
    $('workLauncherDescription').value=item?.card_description || '';$('workLauncherCover').value='';
    $('workLauncherBookmarkPicker').hidden=!bookmarks; $('workLauncherBookmarkNotice').textContent=(bookmarks?.warnings || []).join(' ');
    appResults(); if (bookmarks) bookmarkResults();
    kindFields(); preview(); coverPreview(); $('workLauncherDialog').showModal(); locks(); $('workLauncherName').focus();
  }
  async function click(event) {
    const button = event.target.closest('[data-launcher-action]'); if (!button || button.disabled) return;
    const action=button.dataset.launcherAction, item=(state?.items || []).find(x=>x.id===button.dataset.launcherId);
    if (action === 'close') { dialogVersion++; $('workLauncherDialog').close(); return; }
    if (action === 'reload') { await load(true); return; }
    if (action === 'retry') { if (pending) await write(null,null,null,pending); return; }
    if (action === 'add') { open(); return; }
    if (action === 'edit') { if (item) open(item); return; }
    if (action === 'launch') { if (Date.now() < suppressLaunchUntil || dragItem) return; if (item) await write(action,{id:item.id}); return; }
    if (action === 'remove') {
      if (item) await write('remove',{id:item.id},()=>{$('workLauncherDialog').close();});
      return;
    }
    if (action === 'up' || action === 'down') {
      if (!item) return;
      const ids=boardItems().map(x=>x.id), from=ids.indexOf(item.id), to=from+(action === 'up' ? -1 : 1);
      if (to < 0 || to >= ids.length) return;
      [ids[from],ids[to]]=[ids[to],ids[from]]; await write('board-order',{ids},()=>{$('workLauncherDialog').close();}); return;
    }
    if (action === 'suggestion') {
      chooseSource(state?.suggestions?.[Number(button.dataset.sourceIndex)]); return;
    }
    if (action === 'bookmark') { chooseSource(bookmarks?.items?.[Number(button.dataset.sourceIndex)]); return; }
    if (action === 'manual-target') { selectedApp=null; kindFields(); $('workLauncherTarget').focus(); return; }
    if (action === 'bookmarks') {
      const version=dialogVersion; bookmarkLoading=true; locks(); $('workLauncherBookmarkNotice').textContent='북마크를 읽는 중…';
      try {
        const response=await fetch('/api/launcher/bookmarks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.action_token})}), data=await response.json();
        if (!response.ok || data.error) throw Error(data.error || '북마크를 읽지 못했습니다.');
        bookmarks=data;
        const profiles=[...new Set((data.items || []).map(x=>JSON.stringify([x.browser,x.profile])))];
        $('workLauncherBookmarkProfile').innerHTML='<option value="">모든 프로필</option>'+profiles.map(key=>`<option value="${esc(key)}">${esc(JSON.parse(key).filter(Boolean).join(' · '))}</option>`).join('');
        if (version === dialogVersion) { $('workLauncherBookmarkPicker').hidden=false; bookmarkResults(); $('workLauncherBookmarkNotice').textContent=(data.warnings || []).join(' '); }
      } catch(e) { if (version === dialogVersion) $('workLauncherBookmarkNotice').textContent=e.message; }
      finally {bookmarkLoading=false;locks();if (version === dialogVersion && bookmarks) $('workLauncherBookmarkSearch').focus();} return;
    }
    if (action === 'clear-image' || action === 'palette') {
      imageData=''; imageURL=''; imageVersion++; $('workLauncherImage').value='';
      if (action === 'palette') $('workLauncherIcon').value=button.dataset.icon;
      preview(); return;
    }
    if (action === 'clear-cover') {coverData=null;coverURL='';coverVersion++;coverReading=false;$('workLauncherCover').value='';coverPreview();locks();return;}
    if (action === 'pick') {
      if ($('workLauncherKind').value === 'folder') { await pickFolder('workLauncherTarget'); return; }
      busy=true; locks();
      try {
        const response=await fetch('/api/launcher/pick-program',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.action_token})}), data=await response.json();
        if (!response.ok || data.error) throw Error(data.error || '프로그램 선택창을 열지 못했습니다.');
        if (data.path) { selectedApp=null; $('workLauncherTarget').value=data.path; kindFields(); }
      } catch(e) { $('workLauncherFormNotice').textContent=e.message; }
      finally {busy=false;locks();drainChatRefresh();}
    }
  }
  async function submit(event) {
    if (event.target.id !== 'workLauncherForm') return;
    event.preventDefault(); if (busy || pending || imageReading || coverReading) return;
    const title=$('workLauncherName').value.trim(), kind=$('workLauncherKind').value, target=kind === 'app' && selectedApp ? selectedApp.target : $('workLauncherTarget').value.trim();
    const original = state?.items?.find(x => x.id === editing);
    const cardStyle={card_description:$('workLauncherDescription').value.trim(),...(coverData !== undefined ? {card_cover_data:coverData} : {})};
    if (original && kind === original.kind && target === original.target) {
      if (!title) return;
      await write('appearance',{id:editing,title,icon:$('workLauncherIcon').value.trim(),...cardStyle,...(imageData !== undefined ? {icon_data:imageData} : {})},()=>{$('workLauncherDialog').close();});
      return;
    }
    if (!title || !target) return;
    if (kind === 'web') { try { const url=new URL(target); if (!['http:','https:'].includes(url.protocol)) throw Error(); } catch(_) { $('workLauncherFormNotice').textContent='http:// 또는 https://로 시작하는 올바른 주소를 입력하세요.'; return; } }
    const payload={title,target,kind,icon:$('workLauncherIcon').value.trim(),...cardStyle,...(editing ? {id:editing} : {}),...(imageData !== undefined ? {icon_data:imageData} : {})};
    await write('save',payload,()=>{$('workLauncherDialog').close();});
  }
  function dragBlocked() { return busy || loading || !!pending || !state?.action_token || $('workLauncherDialog').open; }
  function clearDrag() {
    dragItem = null;
    $('workLauncherGrid').querySelectorAll('.is-dragging,.drop-before,.drop-after').forEach(tile => tile.classList.remove('is-dragging','drop-before','drop-after'));
  }
  function dragTarget(event) {
    const tile = event.target.closest('[data-launcher-action="launch"]');
    const item = state?.items?.find(x => x.id === tile?.dataset.launcherId);
    const source = state?.items?.find(x => x.id === dragItem);
    if (dragBlocked() || !tile || tile.disabled || !item || !source) return null;
    return {tile,item,source};
  }
  function dropAfter(event, tile) { const rect = tile.getBoundingClientRect(); return event.clientX >= rect.left + rect.width/2; }
  function dragStart(event) {
    const tile=event.target.closest('[data-launcher-action="launch"]');
    const item=state?.items?.find(x=>x.id===tile?.dataset.launcherId);
    if (dragBlocked() || !tile || tile.disabled || !item || !event.dataTransfer) {event.preventDefault();return;}
    clearDrag(); dragItem=item.id; suppressLaunchUntil=Date.now()+500;
    event.dataTransfer.clearData(); event.dataTransfer.setData('text/plain',item.id); event.dataTransfer.effectAllowed='move';
    tile.classList.add('is-dragging');
  }
  function dragOver(event) {
    const target=dragTarget(event); if (!target || event.dataTransfer?.files?.length) {event.preventDefault();if(event.dataTransfer)event.dataTransfer.dropEffect='none';return;}
    event.preventDefault(); event.dataTransfer.dropEffect='move';
    $('workLauncherGrid').querySelectorAll('.drop-before,.drop-after').forEach(tile=>tile.classList.remove('drop-before','drop-after'));
    if (target.item.id !== dragItem) target.tile.classList.add(dropAfter(event,target.tile) ? 'drop-after' : 'drop-before');
  }
  async function drop(event) {
    event.preventDefault(); // Do not let external URL/file drops navigate the work surface.
    const target=dragTarget(event), sourceID=dragItem;
    if (!target || event.dataTransfer?.files?.length || event.dataTransfer?.getData('text/plain') !== sourceID) {clearDrag();return;}
    const after=dropAfter(event,target.tile); suppressLaunchUntil=Date.now()+500; clearDrag();
    if (target.item.id === sourceID) return;
    const ordered=boardItems().map(x=>x.id);
    const reordered=ordered.filter(id=>id!==sourceID), at=reordered.indexOf(target.item.id)+(after ? 1 : 0);
    reordered.splice(at,0,sourceID);
    if (ordered.every((id,index)=>id===reordered[index])) return;
    await write('board-order',{ids:reordered});
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('workLauncher')) return;
    $('workLauncherPalette').innerHTML=['✦','🎨','🖥️','🌐','📁','📊','✉️','📝','🎬','⚙️'].map(x=>`<button type="button" data-launcher-action="palette" data-icon="${esc(x)}" aria-label="${esc(x)} 아이콘">${esc(x)}</button>`).join('');
    $('work').addEventListener('click',click); $('work').addEventListener('submit',submit);
    function editShortcut(event) {
      const tile=event.target.closest('[data-launcher-action="launch"]');
      if (!tile) return;
      event.preventDefault();
      if (tile.disabled || busy || loading || pending || !state?.action_token) return;
      const item=state.items.find(x=>x.id===tile.dataset.launcherId);
      if (item) open(item);
    }
    $('workLauncherGrid').addEventListener('dragstart',dragStart);
    $('workLauncherGrid').addEventListener('dragover',dragOver);
    $('workLauncherGrid').addEventListener('drop',drop);
    $('workLauncherGrid').addEventListener('dragend',()=>{if (dragItem) suppressLaunchUntil=Date.now()+500;clearDrag();});
    $('workLauncherGrid').addEventListener('dragleave',event=>{if (!$('workLauncherGrid').contains(event.relatedTarget)) $('workLauncherGrid').querySelectorAll('.drop-before,.drop-after').forEach(tile=>tile.classList.remove('drop-before','drop-after'));});
    $('workLauncherGrid').addEventListener('contextmenu',editShortcut);
    $('workLauncherGrid').addEventListener('load',scheduleBoardLayout,true);
    if (typeof window.addEventListener === 'function') window.addEventListener('resize',scheduleBoardLayout);
    $('workLauncherGrid').addEventListener('keydown',event=>{
      if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) editShortcut(event);
    });
    $('workLauncherKind').addEventListener('change',()=>{selectedApp=null;$('workLauncherTarget').value='';$('workLauncherFormNotice').textContent='';kindFields();});
    $('workLauncherAppSearch').addEventListener('input',appResults);
    $('workLauncherBookmarkSearch').addEventListener('input',bookmarkResults);
    $('workLauncherBookmarkProfile').addEventListener('change',bookmarkResults);
    $('workLauncherDialog').addEventListener('cancel',()=>{dialogVersion++;clearDrag();});
    $('workLauncherDialog').addEventListener('close',()=>{clearDrag();locks();drainChatRefresh();});
    document.addEventListener('work-launcher-changed',()=>{launcherRefreshQueued=true;drainChatRefresh();});
    $('workLauncherIcon').addEventListener('input',()=>{imageData='';imageURL='';imageVersion++;preview();});
    $('workLauncherImage').addEventListener('change',()=>{
      const file=$('workLauncherImage').files[0]; if (!file) return;
      if (file.size > 512*1024 || !/\.(png|jpe?g|webp|gif|ico)$/i.test(file.name) || (file.type && !/^image\/(png|jpeg|webp|gif|x-icon|vnd\.microsoft\.icon)$/i.test(file.type))) { $('workLauncherFormNotice').textContent='512KB 이하 PNG, JPG, WebP, GIF, ICO 이미지를 선택하세요.'; $('workLauncherImage').value=''; return; }
      const version=++imageVersion, reader=new FileReader(); imageReading=true; locks();
      reader.onload=()=>{if (version !== imageVersion) return; imageData=String(reader.result);imageURL='';imageReading=false;preview();locks();$('workLauncherFormNotice').textContent='이미지 아이콘을 선택했습니다. 저장하면 적용됩니다.';};
      reader.onerror=()=>{if (version !== imageVersion) return;imageReading=false;locks();$('workLauncherFormNotice').textContent='이미지를 읽지 못했습니다.';}; reader.readAsDataURL(file);
    });
    $('workLauncherCover').addEventListener('change',()=>{
      const file=$('workLauncherCover').files[0];if (!file) return;
      if (file.size > 4*1024*1024 || !/\.(png|jpe?g|webp|gif)$/i.test(file.name) || (file.type && !/^image\/(png|jpeg|webp|gif)$/i.test(file.type))) { $('workLauncherFormNotice').textContent='4MB 이하 PNG, JPG, WebP, GIF 커버 이미지를 선택하세요.';$('workLauncherCover').value='';return; }
      const version=++coverVersion,reader=new FileReader();coverReading=true;locks();
      reader.onload=()=>{if(version !== coverVersion)return;coverData=String(reader.result);coverURL='';coverReading=false;coverPreview();locks();$('workLauncherFormNotice').textContent='커버를 선택했습니다. 저장하면 카드에 적용됩니다.';};
      reader.onerror=()=>{if(version !== coverVersion)return;coverReading=false;locks();$('workLauncherFormNotice').textContent='커버 이미지를 읽지 못했습니다.';};reader.readAsDataURL(file);
    });
    // Native dialogs provide Escape dismissal and keyboard focus containment.
    locks(); load();
  });
})();

/* Calendar is local and independent of assistant/network request locks. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let year, month;
  function renderCalendar() {
    const today=new Date(), first=new Date(year,month,1), days=new Date(year,month+1,0).getDate();
    $('workCalendarMonth').textContent=`${year}년 ${month+1}월`;
    let cells='<span class="work-calendar-blank" aria-hidden="true"></span>'.repeat(first.getDay());
    for(let day=1;day<=days;day++) {
      const current=today.getFullYear()===year && today.getMonth()===month && today.getDate()===day;
      const weekday=new Date(year,month,day).getDay();
      cells+=`<span class="work-calendar-day${weekday===0 ? ' is-sunday' : weekday===6 ? ' is-saturday' : ''}${current ? ' is-today' : ''}"${current ? ' aria-current="date"' : ''} aria-label="${year}년 ${month+1}월 ${day}일">${day}</span>`;
    }
    $('workCalendarGrid').innerHTML=cells;
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('workCalendar')) return;
    const today=new Date(); year=today.getFullYear();month=today.getMonth();renderCalendar();
    $('workCalendar').addEventListener('click',event=>{
      const button=event.target.closest('[data-calendar-action]'); if (!button) return;
      if(button.dataset.calendarAction==='today'){const now=new Date();year=now.getFullYear();month=now.getMonth();}
      else {const next=new Date(year,month+(button.dataset.calendarAction==='prev' ? -1 : 1),1);year=next.getFullYear();month=next.getMonth();}
      renderCalendar();
    });
  });
})();

/* File search and chat use the same server capability; paths never become commands. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let token = '', items = [], busy = false, pending = null;
  const requestID = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join('');
  function message(text, error = false) {
    $('workFileStatus').textContent=text;
    $('workFileStatus').classList.toggle('work-error',error);
  }
  function lock() {
    $('workFileDialog').querySelectorAll('input,select,button').forEach(el => {
      if (el.dataset.filesAction === 'close') return;
      el.disabled=busy || (!!pending && el.dataset.filesAction !== 'retry');
    });
    $('workFileRetry').hidden=!pending;
  }
  async function connect() {
    const response=await fetch('/api/work',{cache:'no-store'}), data=await response.json();
    if (!response.ok || !data.action_token) throw Error('연결을 확인한 뒤 다시 시도하세요.');
    token=data.action_token;
  }
  async function search() {
    if (busy || pending) return;
    busy=true;items=[];$('workFileResults').innerHTML='';$('workFileCoverage').textContent='';lock();message('파일과 폴더 이름을 찾고 있어요…');
    try {
      if (!token) await connect();
      const response=await fetch('/api/files/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,query:$('workFileQuery').value.trim(),kind:$('workFileKind').value,limit:50})});
      const data=await response.json();
      if (!response.ok || data.error) {if(response.status===403)token='';throw Error(data.error || '파일 검색을 완료하지 못했습니다.');}
      items=Array.isArray(data.items) ? data.items : [];
      $('workFileResults').innerHTML=items.map((item,index)=>`<article class="work-file-result"><div><strong>${esc(item.name)}</strong><small>${esc(item.kind === 'folder' ? '폴더' : '파일')} · ${esc(item.source || '')}</small><p>${esc(item.path)}</p></div><div class="work-file-result-actions">${item.kind !== 'folder' && /\.(png|jpe?g|webp|gif|mp4|webm|txt|md|rst|csv|tsv|json|log|py|js|ts|tsx|jsx|css|html|xml|yaml|yml|toml|sql|java|kt|cs|go|rs|c|h|cpp|hpp|sh|pdf|docx|hwpx)$/i.test(item.name || item.path || '') ? `<button type="button" class="work-secondary" data-files-action="preview" data-files-index="${index}">미리보기</button>` : ''}<button type="button" class="work-secondary" data-files-action="reveal" data-files-index="${index}">위치 열기</button></div></article>`).join('');
      message(items.length ? `${items.length}개를 표시합니다.${data.truncated ? ' 검색 범위 또는 표시 개수에 제한이 있습니다.' : ''}` : '확인한 범위에서 일치하는 파일이나 폴더를 찾지 못했습니다.');
      const scopes=(data.scopes || []).map(scope=>typeof scope === 'string' ? scope : [scope.source || scope.name || scope.label,scope.path].filter(Boolean).join(' · '));
      $('workFileCoverage').textContent=['검색 범위: '+(scopes.join(' / ') || '서버가 확인한 자료'),...(data.warnings || []).map(String)].join('\n');
    } catch(error) {message(error.message,true);}
    finally {busy=false;lock();}
  }
  async function reveal(item) {
    if (busy) return;
    if (!pending) {
      if (!item?.id || !item.selection_token) {message('검색 결과를 새로 확인하세요.',true);return;}
      pending={id:item.id,selection_token:item.selection_token,request_id:requestID()};
    }
    busy=true;lock();message('탐색기에 위치 표시를 요청하고 있어요…');
    let responseReceived=false;
    try {
      if (!token) await connect();
      const response=await fetch('/api/files/reveal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,...pending})});
      const data=await response.json();responseReceived=true;
      if (!response.ok || data.error) {if(response.status===403)token='';throw Error(data.error || '위치를 열지 못했습니다. 다시 검색해 주세요.');}
      pending=null;message('탐색기에 위치 표시를 요청했습니다.');
    } catch(error) {
      if (responseReceived) pending=null;
      message(responseReceived ? error.message : '위치 열기 결과를 확인하지 못했습니다. 같은 요청 결과를 확인해 주세요.',true);
    } finally {busy=false;lock();}
  }
  function init() {
    const dialog=$('workFileDialog');if (!dialog) return;
    const fileClick=event=>{
      const button=event.target.closest('[data-files-action]');if (!button || button.disabled) return;
      const action=button.dataset.filesAction;
      if (action==='open') {if (document.body.dataset.activeView !== 'work') show('work');if(!dialog.open)dialog.showModal();$('workFileQuery').focus();return;}
      if (action==='close') {dialog.close();return;}
      if (action==='preview') {
        const item=items[Number(button.dataset.filesIndex)];if(!item || busy || pending)return;busy=true;lock();message('미리보기를 준비하고 있어요…');
        (async()=>{try{if(!token)await connect();const response=await fetch('/api/files/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,id:item.id,selection_token:item.selection_token})});const data=await response.json();if(!response.ok || data.error)throw Error(data.error || '미리보기를 열지 못했습니다.');document.dispatchEvent(new CustomEvent('work-stage-preview',{detail:{artifact:data.artifact}}));dialog.close();message('작업 화면에 미리보기를 열었습니다.');}catch(e){message(e.message,true);}finally{busy=false;lock();}})();return;
      }
      if (action==='reveal') {reveal(items[Number(button.dataset.filesIndex)]);return;}
      if (action==='retry' && pending) reveal();
    };
    $('work').addEventListener('click',fileClick);
    $('workUtilities')?.addEventListener('click',fileClick);
    $('workFileForm').addEventListener('submit',event=>{event.preventDefault();search();});
  }
  document.addEventListener('DOMContentLoaded',init);
})();
