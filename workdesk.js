/* Personal work surface. All writes use an idempotency key, including explicit retries. */
(() => {
  'use strict';
  let overview = null, selected = null, busy = false, loading = false, uncertain = null;
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusNames = {todo:'예정', doing:'진행 중', done:'완료'};
  const localDay = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
  const stamp = value => { if (!value) return ''; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(d); };
  function notice(message = '', error = false) {
    $('workNotice').textContent = message; $('workNotice').hidden = !message;
    $('workNotice').classList.toggle('work-error', error);
  }
  function locks() {
    const blocked = busy || !!uncertain || !overview?.action_token;
    $('work').querySelectorAll('button,input,textarea').forEach(el => {
      if (el.matches('[data-work-action="desk"],[data-work-action="tasks"]')) return;
      if (el.matches('[data-work-action="reload"]')) { el.disabled = busy || !!uncertain || loading; return; }
      if (el.matches('[data-work-action="retry"]')) { el.disabled = busy; return; }
      el.disabled = blocked;
    });
    $('workRetry').hidden = !uncertain;
    $('workSend').textContent = busy ? '처리 중…' : '요청 보내기';
    $('workConnection').textContent = busy ? '요청 처리 중' : overview ? '업무 기록 연결됨' : '연결 필요';
  }
  function renderDetail(preserve = true) {
    const noteDraft = preserve ? $('workNoteText')?.value || '' : '';
    const pathDraft = preserve ? $('workResourcePath')?.value || '' : '';
    const task = overview?.tasks?.find(t => t.id === selected);
    $('workClearContext').hidden = !task;
    $('workContext').textContent = task ? `선택한 업무: ${task.title}` : '새 업무 또는 오늘 전체 업무에 대해 이야기합니다.';
    if (!task) { selected = null; $('workTaskDetailPanel').innerHTML = '<p class="work-empty">업무를 선택하면 진행 상태, 메모, 연결 자료를 볼 수 있습니다.</p>'; return; }
    $('workTaskDetailPanel').innerHTML = `<h4>${escape(task.title)}</h4><span class="work-state ${escape(task.status)}">${escape(statusNames[task.status] || task.status)}</span> <span class="work-muted">${task.due_date ? '마감 '+escape(task.due_date) : '마감일 없음'}</span>${task.detail ? `<div class="work-task-description">${escape(task.detail)}</div>` : ''}
      <div class="work-status-actions">${[['todo','예정으로 표시'],['doing','진행 중으로 표시'],['done','완료로 표시']].filter(([s])=>s!==task.status).map(([s,label])=>`<button type="button" class="work-secondary" data-work-status="${s}">${label}</button>`).join('')}</div>
      <h5>진행 메모</h5>${(task.notes || []).map(n=>`<div class="work-note">${escape(n.text)}<time>${escape(stamp(n.created))}</time></div>`).join('') || '<p class="work-muted">진행한 내용과 다음 할 일을 남겨두세요.</p>'}
      <form id="workNoteForm"><label class="work-sr-only" for="workNoteText">진행 메모</label><textarea id="workNoteText" rows="2" maxlength="6000" required placeholder="진행 내용 기록">${escape(noteDraft)}</textarea><div class="work-form-actions"><button type="submit" class="work-secondary">메모 남기기</button></div></form>
      <h5>연결 자료</h5>${(task.resources || []).map(r=>`<div class="work-resource">${escape(r.name || r.path)}<span class="work-resource-path">${escape(r.path)}${r.exists === false ? ' (경로를 찾을 수 없음)' : ''}</span>${r.exists === false ? '' : `<button type="button" class="work-quiet" data-work-resource="${escape(r.id)}">열기</button>`}</div>`).join('')}
      <form id="workResourceForm"><label for="workResourcePath">파일 또는 폴더 경로</label><div class="work-resource-entry"><input id="workResourcePath" maxlength="4096" required value="${escape(pathDraft)}" placeholder="경로 직접 입력"><button type="button" class="work-quiet" data-work-action="pick-resource">폴더 선택</button></div><div class="work-form-actions"><button type="submit" class="work-secondary">자료 연결</button></div></form><p class="work-muted">원본 위치를 업무에 연결합니다.</p>`;
  }
  function render() {
    const tasks = overview.tasks || [], counts = overview.counts || {};
    $('workCounts').textContent = `예정 ${counts.todo ?? 0} · 진행 중 ${counts.doing ?? 0} · 완료 ${counts.done ?? 0}`;
    $('workTaskList').innerHTML = tasks.length ? tasks.map(t=>`<button type="button" class="work-task-row" data-work-task="${escape(t.id)}" aria-pressed="${t.id === selected}"><span class="work-state ${escape(t.status)}">${escape(statusNames[t.status] || t.status)}</span><span class="work-task-copy"><span class="work-task-name">${escape(t.title)}</span><small>${t.due_date ? '마감 '+escape(t.due_date) : '마감일 없음'}${t.resources?.length ? ' · 연결 자료 '+t.resources.length+'개' : ''}</small></span><span aria-hidden="true">›</span></button>`).join('') : '<p class="work-empty">아직 등록한 업무가 없습니다.<br>비서에게 요청하거나 ‘업무 추가’로 첫 업무를 남겨보세요.</p>';
    const messages = overview.messages || [];
    const log = $('workMessages'), nearBottom = log.scrollHeight-log.scrollTop-log.clientHeight < 50;
    log.innerHTML = messages.length ? messages.map(m=>`<div class="work-message ${m.role === 'user' ? 'work-user' : ''}"><strong>${m.role === 'user' ? '나' : '업무 비서'}</strong>${escape(m.content)}</div>`).join('') : '<p class="work-empty">무슨 일을 해야 하나요?<br>업무를 등록하거나 진행 내용을 알려주세요.</p>';
    if (nearBottom) log.scrollTop = log.scrollHeight;
    renderDetail();
    const report = overview.report;
    $('workReportMeta').textContent = report ? `${report.day || ''} · ${stamp(report.created)} 저장` : '아직 저장된 보고서가 없습니다. 업무 기록을 모아 보고서를 작성하세요.';
    $('workReportBody').textContent = report?.body || '';
    $('workReportBody').hidden = !report; $('workReportActions').hidden = !report;
    locks();
  }
  window.workLoad = async function workLoad() {
    if (!$('work') || loading || busy || uncertain) return;
    loading = true;
    try {
      const response = await fetch('/api/work', {cache:'no-store'}), data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || '업무를 불러오지 못했습니다.');
      overview = data; notice(); render();
    } catch (e) {
      notice(`업무 연결 실패: ${e.message} 화면을 다시 열어 재연결하세요.`, true);
      if (!overview) {
        $('workMessages').innerHTML = '<p class="work-empty">업무 기록에 연결하지 못했습니다.</p>';
        $('workTaskList').innerHTML = '<p class="work-empty">연결 후 업무를 확인할 수 있습니다.</p>';
        $('workReportMeta').textContent = '연결 후 저장된 보고서를 확인할 수 있습니다.';
      }
      locks();
    } finally { loading = false; locks(); }
  };
  function requestID() { return Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join(''); }
  async function write(endpoint, payload, cleanup, existing) {
    if (busy || (!existing && uncertain) || !overview?.action_token) return;
    const request = existing || {endpoint, body:{...payload, token:overview.action_token, request_id:requestID()}, cleanup};
    busy = true; notice(); locks();
    let confirmedResponse = false;
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
      uncertain = null; overview = data;
      if (data.result?.task_id && data.result.task_id !== selected) {
        selected = data.result.task_id; renderDetail(false);
      }
      request.cleanup?.(); render();
      notice(data.result?.message || '업무 기록을 저장했습니다.');
    } catch (e) {
      if (!confirmedResponse) uncertain = request;
      else uncertain = null;
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
    if (button.dataset.workTask) { selected = button.dataset.workTask; renderDetail(false); render(); return; }
    if (button.dataset.workExample) { $('workCommand').value=button.dataset.workExample; $('workCommand').focus(); return; }
    if (button.dataset.workStatus) { write('status',{task_id:selected,status:button.dataset.workStatus}); return; }
    if (button.dataset.workResource) { write('open-resource',{resource_id:button.dataset.workResource}); return; }
    switch (button.dataset.workAction) {
      case 'reload': window.workLoad(); break;
      case 'desk': show('desk'); break;
      case 'tasks': show('tasks'); break;
      case 'clear-context': selected=null; render(); break;
      case 'add-task': $('workTaskForm').hidden=false; $('workTaskTitle').focus(); break;
      case 'cancel-task': $('workTaskForm').hidden=true; break;
      case 'pick-resource': await pickFolder('workResourcePath'); break;
      case 'report': write('report',{day:localDay()}); break;
      case 'retry': if (uncertain) await write(null,null,null,uncertain); break;
      case 'copy-report':
        try { await navigator.clipboard.writeText(overview.report.body); notice('보고서를 복사했습니다.'); }
        catch (_) { notice('복사하지 못했습니다. 텍스트 저장 버튼을 사용하세요.',true); } break;
      case 'download-report': {
        const url=URL.createObjectURL(new Blob([overview.report.body],{type:'text/plain;charset=utf-8'})), link=document.createElement('a');
        link.href=url; link.download=`업무보고서-${overview.report.day || localDay()}.txt`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); break;
      }
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('work')) return;
    $('workDate').textContent=new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',weekday:'long'}).format(new Date());
    $('work').addEventListener('submit',submit); $('work').addEventListener('click',click);
    locks(); window.workLoad();
  });
})();
