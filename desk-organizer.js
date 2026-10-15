/* One action handles the batch; only results and exceptions need review. */
let deskOrganizerJob = null;
let deskOrganizerBusy = false;
const organizerLabels = {planned:'정리 준비', moving:'진행 중', moved:'정리됨', skipped:'보류',
    undoing:'되돌리는 중', undone:'되돌림', exception:'확인 필요'};
const organizerJobLabels = {planned:'정리 준비', completed:'정리 완료', partial:'일부 확인 필요',
    undone:'되돌리기 완료', undo_partial:'일부 되돌리기 확인 필요'};

function deskOrganizerRender() {
    const job = deskOrganizerJob;
    const items = job?.items || [];
    document.getElementById('deskOrganizerUndo').disabled = deskOrganizerBusy || !items.some(x=>['moved','undoing'].includes(x.status));
    const undoDirection = job?.direction === 'undo' || items.some(x=>['undoing','undone'].includes(x.status));
    document.getElementById('deskOrganizerResume').hidden = !job || !(items.some(x=>['planned','moving','undoing'].includes(x.status)) || (undoDirection && job.status!=='undone'));
    document.getElementById('deskOrganizerResume').textContent = undoDirection?'중단된 되돌리기 이어가기':'중단된 정리 이어가기';
    document.getElementById('deskOrganizerResume').disabled = deskOrganizerBusy;
    document.getElementById('deskOrganizerStart').disabled = deskOrganizerBusy;
    document.getElementById('deskOrganizerStart').textContent = job?'새 샘플 자동 정리':'샘플 자동 정리';
    const body = document.getElementById('deskOrganizerBody');
    if (!job) {
        body.innerHTML='<div class="desk-proposal-empty">샘플 5개를 만들고 자동 정리합니다. 디자인 연결 자료 1개는 확인 대상으로 남깁니다.</div>';
        return;
    }
    const count = statuses=>items.filter(x=>statuses.includes(x.status)).length;
    const attention=items.filter(x=>['exception','skipped'].includes(x.status)||(['moving','undoing'].includes(x.status)&&x.reason)).length;
    body.innerHTML=`<div class="organizer-summary"><div><span>정리됨</span><b>${count(['moved'])}</b></div><div><span>되돌림</span><b>${count(['undone'])}</b></div><div><span>확인 필요·보류</span><b>${attention}</b></div></div><h4>${esc(organizerJobLabels[job.status]||job.status)}</h4><div class="organizer-results">${items.map(x=>`<article class="organizer-result ${x.status==='exception'?'organizer-exception':''}"><div><strong>${esc(x.name)}</strong><span class="organizer-state">${esc(['moving','undoing'].includes(x.status)&&x.reason?'확인 필요':(organizerLabels[x.status]||x.status))}</span></div><p>${x.status==='undone'?'원래 위치로 복원':x.destination?esc(x.destination.split(/[\\/]/).slice(-2).join(' / ')):'시험 폴더에 유지'}</p>${x.reason?`<p>${esc(x.reason)}</p>`:''}</article>`).join('')}</div><details class="desk-group-filelist"><summary>시험 폴더 위치·기록 확인</summary><p class="organizer-path">원래 위치: ${esc(job.source_dir)}<br>정리 위치: ${esc(job.destination_dir)}<br>기록 번호: ${esc(job.id)}</p></details>`;
}

async function deskOrganizerLoad() {
    const response = await fetch('/api/desk/organizer');
    const result = await response.json();
    if (!response.ok) throw Error(result.error||'정리 기록을 읽을 수 없습니다.');
    deskOrganizerJob = result.job;
    deskOrganizerRender();
}

async function deskOrganizerAction(action) {
    if (deskOrganizerBusy || !deskData) return;
    deskOrganizerBusy = true;
    deskOrganizerRender();
    const status = document.getElementById('deskOrganizerStatus');
    status.textContent=action==='undo'?'원래 위치로 되돌리고 있습니다.':'샘플 파일을 자동으로 정리하고 있습니다.';
    try {
        if (action==='start') deskOrganizerJob=(await deskRequest('organizer-create',{})).job;
        if (!deskOrganizerJob) throw Error('먼저 샘플 정리를 시작하세요.');
        const recoveringUndo=deskOrganizerJob.direction==='undo'||deskOrganizerJob.items.some(x=>['undoing','undone'].includes(x.status));
        const operation=action==='undo'||recoveringUndo?'organizer-undo':'organizer-execute';
        deskOrganizerJob=(await deskRequest(operation,{id:deskOrganizerJob.id})).job;
        status.textContent=organizerJobLabels[deskOrganizerJob.status]||'정리 결과를 확인하세요.';
    } catch(e) {
        status.textContent=e.message;
        // A lost response can follow a completed move. Read the persisted record first.
        try {await deskOrganizerLoad();} catch(readError) {status.textContent+=' · '+readError.message;}
    } finally {
        deskOrganizerBusy=false;
        deskOrganizerRender();
    }
}

document.addEventListener('DOMContentLoaded',()=>{
    document.getElementById('deskOrganizerOpen').addEventListener('click',async()=>{
        const dialog=document.getElementById('deskOrganizerDialog');
        document.getElementById('deskOrganizerStatus').textContent='정리 기록을 확인하고 있습니다.';
        dialog.showModal();
        deskOrganizerBusy=true;deskOrganizerRender();
        try {await deskOrganizerLoad();document.getElementById('deskOrganizerStatus').textContent='';}
        catch(e) {document.getElementById('deskOrganizerStatus').textContent=e.message;}
        finally {deskOrganizerBusy=false;deskOrganizerRender();}
    });
    document.getElementById('deskOrganizerClose').addEventListener('click',()=>document.getElementById('deskOrganizerDialog').close());
    document.getElementById('deskOrganizerStart').addEventListener('click',()=>deskOrganizerAction('start'));
    document.getElementById('deskOrganizerResume').addEventListener('click',()=>deskOrganizerAction('resume'));
    document.getElementById('deskOrganizerUndo').addEventListener('click',()=>deskOrganizerAction('undo'));
});
