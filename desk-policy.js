/* Saved folder scope; one batch action rather than per-file approvals. */
let policyData = {policy:null,jobs:[],running:false,last_error:''};
let policyBusy = false;
let policyFormReady = false;
let policyFormDirty = false;
let policyPending = null;
const policyItemLabels={planned:'이동 예정',moving:'처리 확인 중',moved:'정리됨',skipped:'보류',undoing:'복원 확인 중',undone:'되돌림',exception:'확인 필요'};
const policyJobLabels={planned:'정리 미리보기',completed:'정리 완료',partial:'일부 확인 필요',undone:'취소·되돌리기 완료',undo_partial:'일부 복원 확인 필요',unavailable:'기록 확인 필요'};

function policyMessage(text) { document.getElementById('policyMessage').textContent=text; }
function policyAttention(item) { return ['skipped','exception'].includes(item.status)||(['moving','undoing'].includes(item.status)&&item.reason); }
function policyRender(fill=false) {
    const policy=policyData.policy, jobs=policyData.jobs||[];
    if(fill && policy && !policyFormDirty) {
        document.getElementById('folder').value=policy.source_dir;
        document.getElementById('policyDestination').value=policy.destination_dir;
        document.getElementById('policyExclusions').value=(policy.exclusions||[]).join('\n');
        document.getElementById('policyAuthorized').checked=false;
    }
    const enabled=!!policy?.auto_enabled;
    const blocked=policyBusy||policyData.running||!!policyPending;
    document.getElementById('policyRetry').hidden=!policyPending;
    document.getElementById('policyRetry').disabled=policyBusy;
    document.getElementById('policyRefresh').disabled=policyBusy;
    document.getElementById('policyState').textContent=!policy?'범위 설정 전':policyData.running?'정리 처리 중':enabled?'자동 정리 켜짐':'자동 정리 일시 정지';
    document.getElementById('policyScope').innerHTML=policy?`<p><span>원본</span>${esc(policy.source_dir)}</p><p><span>보관</span>${esc(policy.destination_dir)}</p>`:'';
    document.getElementById('policyToggle').textContent=enabled?'자동 정리 일시 정지':'자동 정리 시작';
    for(const id of ['policyToggle','policyRun','policyPreview'])document.getElementById(id).disabled=blocked||!policy;
    document.getElementById('policySave').disabled=blocked||enabled;
    const items=jobs[0]?.items||[];
    const count=status=>items.filter(x=>x.status===status).length;
    document.getElementById('policyMetrics').innerHTML=`<div><span>정리됨</span><b>${count('moved')}</b></div><div><span>취소·되돌림</span><b>${count('undone')}</b></div><div><span>확인 필요·보류</span><b>${items.filter(policyAttention).length}</b></div>`;
    document.getElementById('policyJobCount').textContent=jobs.length?`최근 ${jobs.length}회`:'';
    document.getElementById('policyJobs').innerHTML=jobs.map((job,index)=>{
        const undoDirection=job.direction==='undo'||(job.items||[]).some(x=>['undoing','undone'].includes(x.status));
        const pending=(job.items||[]).some(x=>['planned','moving','undoing'].includes(x.status))||(undoDirection&&job.status!=='undone');
        const cancelable=job.status==='planned'&&(job.items||[]).some(x=>x.status==='planned');
        const undoable=cancelable||(job.items||[]).some(x=>['moved','moving','undoing'].includes(x.status));
        const rows=(job.items||[]).map(item=>`<div class="policy-result-row ${policyAttention(item)?'policy-attention':''}"><div><strong>${esc(item.name)}</strong><small>${esc(item.reason||((item.status==='undone')?'원래 위치로 복원':item.destination?item.destination.split(/[\\/]/).slice(-2).join(' / '):'원래 위치에 유지'))}</small></div><span>${esc(policyItemLabels[item.status]||item.status)}</span></div>`).join('');
        const time=job.created_at?new Date(job.created_at).toLocaleString('ko-KR'):'저장한 정리 기록';
        return `<article class="card policy-job"><div class="policy-job-heading"><div><h4>${esc(policyJobLabels[job.status]||job.status)}</h4><small>${esc(time)}</small></div><div class="actions">${pending?`<button type="button" class="secondary" data-policy-resume="${esc(job.id)}" ${blocked?'disabled':''}>${undoDirection?'되돌리기 이어가기':'이 정리 실행·이어가기'}</button>`:''}${undoable?`<button type="button" class="secondary" data-policy-undo="${esc(job.id)}" ${blocked?'disabled':''}>${cancelable?'미리보기 취소':'이 정리 되돌리기'}</button>`:''}</div></div>${job.error?`<p class="policy-error">${esc(job.error)}</p>`:''}<details ${index===0?'open':''}><summary>파일 ${job.items.length}개 · 확인·보류 ${job.items.filter(policyAttention).length}개</summary><div>${rows||'<p class="muted">이번 범위에 정리할 파일이 없습니다.</p>'}</div></details>${(job.warnings||[]).length?`<p class="muted">${job.warnings.map(esc).join(' · ')}</p>`:''}</article>`;
    }).join('')||'<div class="card muted">아직 정리 기록이 없습니다. 범위를 저장하고 미리보기를 확인하세요.</div>';
    if(policyData.last_error && !policyPending)policyMessage(policyData.last_error);
}

async function policyLoad(quiet=false) {
    if(policyBusy)return;
    try {
        const response=await fetch('/api/desk/policy');
        const result=await response.json();
        if(!response.ok)throw Error(result.error||'정리 상태를 읽을 수 없습니다.');
        const changed=JSON.stringify(result)!==JSON.stringify(policyData);
        policyData=result;
        if(changed||!policyFormReady||!quiet)policyRender(!policyFormReady);
        policyFormReady=true;
    } catch(e) {if(!quiet)policyMessage(e.message);}
}

function policyCommand(action,payload) {
    const request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');
    if(['undo','resume'].includes(action)) {
        const expected_job=policyData.job_scopes?.find(job=>job.id===payload.id);
        if(!expected_job)throw Error('작업 범위를 새로 확인한 뒤 다시 선택하세요.');
        return {action,payload:{...payload,request_id,expected_job}};
    }
    if(!policyData.policy_scope)throw Error('정리 범위를 새로 확인한 뒤 다시 실행하세요.');
    return {action,payload:{...payload,request_id,expected_scope:policyData.policy_scope}};
}

async function policyPost(command) {
    const response=await fetch('/api/desk/policy-'+command.action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...command.payload,token:deskData?.action_token})});
    const result=await response.json();
    if(!response.ok || result.error) {const error=Error(result.error||'정리 요청을 완료하지 못했습니다.');error.acknowledged=true;throw error;}
    return result;
}

async function policyAction(action,payload={},retry=false) {
    if(policyBusy)return;
    if(policyPending && !retry){policyMessage('이전 요청의 결과를 먼저 확인하세요.');return;}
    if(!deskData){policyMessage('작업 데스크 연결 후 다시 실행하세요.');return;}
    policyBusy=true;policyRender();
    policyMessage(action==='undo'?'원래 위치로 되돌리고 있습니다.':'정리 범위를 확인하고 있습니다.');
    try {
        if(action==='save')policyData=await deskRequest('policy-save',payload);
        else {
            policyPending=retry ? policyPending : policyCommand(action,payload);
            policyData=await policyPost(policyPending);
            policyPending=null;
        }
        if(action==='save') {
            policyFormDirty=false;
            policyPending=policyCommand('preview',{});
            policyData=await policyPost(policyPending);
            policyPending=null;
            policyMessage('범위를 저장했습니다. 이동 예정 파일을 확인한 뒤 자동 정리를 시작하세요.');
        } else if(action==='enabled')policyMessage(policyData.policy?.auto_enabled?'자동 정리를 시작했습니다. 다음 확인부터 이 범위를 처리합니다.':'자동 정리를 일시 정지했습니다.');
        else policyMessage(action==='undo'?'되돌리기 결과를 확인하세요.':action==='preview'?'미리보기입니다. 파일은 이동하지 않았습니다.':'정리 결과를 확인하세요.');
        policyRender(action==='save');
    } catch(e) {
        if(e.acknowledged)policyPending=null;
        policyMessage(policyPending?'실행 결과를 확인하지 못했습니다. 같은 요청 결과를 확인한 뒤 다음 작업을 진행하세요.':e.message);
        // Read durable state after an interrupted response, without silently retrying moves.
        try {const r=await fetch('/api/desk/policy');if(r.ok){policyData=await r.json();policyRender();}}catch(_){}
    } finally {policyBusy=false;policyRender();}
}

document.addEventListener('DOMContentLoaded',()=>{
    for(const id of ['folder','policyDestination','policyExclusions'])document.getElementById(id).addEventListener('input',()=>{policyFormDirty=true;});
    document.getElementById('policySave').addEventListener('click',()=>{
        if(!document.getElementById('policyAuthorized').checked){policyMessage('선택한 범위의 정리 허용을 확인하세요.');return;}
        policyAction('save',{source_dir:document.getElementById('folder').value.trim(),destination_dir:document.getElementById('policyDestination').value.trim(),exclusions:document.getElementById('policyExclusions').value.split('\n').map(x=>x.trim()).filter(Boolean),auto_enabled:false,authorized:true});
    });
    document.getElementById('policyToggle').addEventListener('click',()=>policyAction('enabled',{enabled:!policyData.policy?.auto_enabled}));
    document.getElementById('policyRun').addEventListener('click',()=>policyAction('run'));
    document.getElementById('policyPreview').addEventListener('click',()=>policyAction('preview'));
    document.getElementById('policyRefresh').addEventListener('click',()=>policyLoad());
    document.getElementById('policyRetry').addEventListener('click',()=>{if(policyPending)policyAction(policyPending.action,{},true);});
    document.getElementById('policyJobs').addEventListener('click',e=>{const button=e.target.closest('button');if(button?.dataset.policyUndo)policyAction('undo',{id:button.dataset.policyUndo});else if(button?.dataset.policyResume)policyAction('resume',{id:button.dataset.policyResume});});
    document.addEventListener('work-desk-changed',()=>policyLoad(true));
    setInterval(()=>{if(!document.hidden&&!document.getElementById('files').classList.contains('hide'))policyLoad(true);},10000);
});
