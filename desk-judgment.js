/* Content understanding first; approval concerns only the visible candidate batch. */
let judgeData=null,judgeBusy=false,judgeLoading=false,judgeView='candidates',judgeSnapshot=null;
const judgeRoles={meaningful_design:'작업 결과물',reference:'참고 자료',logo:'로고·브랜드',meme:'짤·밈',photo:'사진',screenshot:'스크린샷',draft:'초안·작업 중',unknown:'용도 판단 중'};
const judgeDisposition={keep:'업무 자료로 보존',archive:'보관할 자료',review_delete:'정리 후보',unknown:'판단 보류'};
function judgeCandidates(data){return (data?.candidates||[]).filter(item=>item.hold_allowed!==false);}
function judgeNotice(message){document.getElementById('judgeNotice').textContent=message||'';}
function judgeRender(){
    const data=judgeData;if(!data)return;
    document.getElementById('judgeState').textContent=data.running?'내용을 살펴보는 중':data.enabled?'자동 분석 켜짐':'자동 분석 멈춤';
    document.getElementById('judgeProgress').textContent=data.running?(data.phase==='grouping'?data.message||'읽은 내용을 비교해 업무별로 묶고 있습니다.':`${data.processed||0} / ${data.total||0}개 확인 · 결과가 차례로 나타납니다.`):data.last_error||data.message||'바탕화면 내용을 직접 읽고 관계를 찾습니다.';
    document.getElementById('judgeStart').textContent=data.enabled?'변경 자료 확인':'AI로 바탕화면 살펴보기';
    document.getElementById('judgeStart').disabled=judgeBusy||data.running;
    document.getElementById('judgeStop').hidden=!data.enabled;document.getElementById('judgeStop').disabled=judgeBusy;
    document.getElementById('judgeReadCount').textContent=data.analyzed||0;
    document.getElementById('judgeImageCount').textContent=data.image_count||0;
    document.getElementById('judgeGroupCount').textContent=data.applied_group_count||0;
    const candidates=judgeCandidates(data);
    document.getElementById('judgeCandidateCount').textContent=candidates.length;
    const empty=candidates.filter(x=>(x.flags||[]).includes('empty_verified')).length;
    document.getElementById('judgeCandidateText').textContent=candidates.length?(empty?`전체를 읽어 내용이 없는 메모 ${empty}개를 확인했습니다.`:'내용을 살펴본 결과 정리 여부를 확인할 자료가 있습니다.'):'현재 확인된 삭제 후보가 없습니다. 애매한 자료는 보존합니다.';
    document.getElementById('judgeReview').disabled=judgeBusy||!candidates.length;
    document.getElementById('judgeImageSummary').innerHTML=Object.entries(judgeRoles).map(([role,label])=>{const count=(data.items||[]).filter(x=>x.kind==='image'&&x.visual_role===role&&x.status==='analyzed').length;return count?`<span>${esc(label)} <b>${count}</b></span>`:'';}).join('')||'<span>이미지의 실제 내용을 확인하면 용도별 결과가 나타납니다.</span>';
    const recovery=data.recovery?.batches||[];
    document.getElementById('judgeRecovery').hidden=!recovery.length;
    document.getElementById('judgeDetailsCount').textContent=`내용 결과 ${data.analyzed||0}개 · 보호·보류 ${data.deferred_count||0}개`;
    if(data.group_warning)judgeNotice(data.group_warning);
}
async function judgeLoad(quiet=false){
    if(judgeLoading||judgeBusy||document.getElementById('judgeDialog').open)return;
    judgeLoading=true;
    try{const r=await fetch('/api/desk/judgment');const data=await r.json();if(!r.ok)throw Error(data.error||'분석 결과를 읽을 수 없습니다.');
        const changed=JSON.stringify(judgeData?.groups)!==JSON.stringify(data.groups)||judgeData?.applied_group_count!==data.applied_group_count;
        judgeData=data;judgeRender();if(changed&&deskData)await deskLoad(true);
    }catch(e){if(!quiet)judgeNotice(e.message);}finally{judgeLoading=false;}
}
async function judgeAction(action,payload={}){
    if(judgeBusy||!deskData)return;
    judgeBusy=true;judgeRender();judgeNotice('처리하고 있습니다.');
    try{judgeData=await deskRequest('judgment-'+action,payload);judgeNotice(action==='start'?'파일을 고르지 않아도 내용을 차례로 살펴봅니다.':action==='hold'?'승인한 후보의 처리 결과를 확인하세요. 원위치 복원이 가능합니다.':action==='retain'?'남겨두기로 기억했습니다. 같은 내용으로 다시 묻지 않습니다.':action==='restore'?'원위치 복원 결과를 확인하세요.':'자동 분석을 멈췄습니다.');await deskLoad(true);return true;}
    catch(e){judgeNotice(e.message);return false;}finally{judgeBusy=false;judgeRender();}
}
function judgeRow(item,selectable=false){
    const image=item.kind==='image'&&['.png','.jpg','.jpeg','.webp'].includes(item.extension);
    const allowed=item.hold_allowed!==false;
    const role=item.kind==='image'?judgeRoles[item.visual_role]||'이미지':(item.flags||[]).includes('empty_verified')?'빈 메모':'문서·메모';
    return `<article class="judge-result"><div class="judge-result-main">${selectable?`<input type="checkbox" data-judge-select="${esc(item.id)}" ${allowed?'checked':'disabled'} aria-label="${esc(item.name)} 선택">`:''}${image?`<img src="/api/desk/thumbnail?id=${encodeURIComponent(item.id)}" alt="${esc(item.name)} 미리보기" loading="lazy">`:'<span class="judge-file-glyph">'+esc(item.extension||'파일')+'</span>'}<div><strong>${esc(item.name)}</strong><span class="judge-role">${esc(role)} · ${esc(judgeDisposition[item.disposition]||item.status)}</span><p>${esc(item.summary||item.reason||'내용을 확인하지 못해 원본을 보존했습니다.')}</p><small>${esc(item.reason||'')}</small>${item.hold_note?`<small class="judge-protected">${esc(item.hold_note)}</small>`:''}<small>${item.usage?.opens?`앱에서 직접 연 횟수 ${item.usage.opens}회`:'사용 이력은 아직 확인되지 않았습니다.'}</small></div></div><div class="judge-result-actions"><button type="button" data-judge-open="${esc(item.id)}">파일 열기</button>${selectable?`<button type="button" class="secondary" data-judge-retain="${esc(item.id)}">남겨두기</button>`:''}</div></article>`;
}
function judgeOpen(view='candidates'){
    if(!judgeData)return;
    judgeView=view;judgeSnapshot=JSON.parse(JSON.stringify(judgeData));
    const title=document.getElementById('judgeDialogTitle'),body=document.getElementById('judgeDialogBody'),actions=document.getElementById('judgeApproval');
    document.getElementById('judgeDialogNotice').textContent='';actions.hidden=view!=='candidates';
    if(view==='candidates'){
        title.textContent='이 자료를 바탕화면에서 치울까요?';
        body.innerHTML='<p class="judge-dialog-caption">열어보고 싶으면 바로 열 수 있습니다. 선택된 후보만 복구 보관함으로 옮기며, 원위치로 되돌릴 수 있습니다. Windows 휴지통·영구 삭제는 실행하지 않습니다.</p>'+judgeCandidates(judgeSnapshot).map(x=>judgeRow(x,true)).join('');
    }else if(view==='recovery'){
        title.textContent='바탕화면에서 치운 자료';
        body.innerHTML=(judgeSnapshot.recovery?.batches||[]).map(batch=>`<article class="judge-recovery-batch"><h4>보관 ${batch.held_count||0}개 · 복원 ${batch.restored_count||0}개</h4>${(batch.items||[]).map(x=>`<p>${esc(x.name)} <span>${esc(({held:'복구 보관 중',restored:'원위치 복원',moving:'처리 결과 확인 중',restoring:'복원 확인 중',exception:'확인 필요'})[x.status]||x.status)}</span>${x.reason?`<small>${esc(x.reason)}</small>`:''}</p>`).join('')}<button type="button" data-judge-restore="${esc(batch.batch_id)}">이 묶음 원위치 복원</button></article>`).join('');
    }else{
        title.textContent='AI가 읽고 판단한 내용';
        const rows=(judgeSnapshot.items||[]).filter(x=>view!=='images'||x.kind==='image');
        body.innerHTML='<p class="judge-dialog-caption">내용 요약과 용도는 AI 판단입니다. 단순한 이미지·짤·초안도 필요한 자료일 수 있어 자동 삭제하지 않습니다.</p>'+rows.map(x=>judgeRow(x)).join('');
    }
    document.getElementById('judgeDialog').showModal();judgeSelectionCount();
}
function judgeSelectionCount(){const count=document.querySelectorAll('#judgeDialog input[data-judge-select]:checked').length;document.getElementById('judgeHold').textContent=`선택 ${count}개 복구 보관함으로 치우기`;document.getElementById('judgeHold').disabled=judgeBusy||!count;}
document.addEventListener('DOMContentLoaded',()=>{
    document.getElementById('judgeStart').addEventListener('click',()=>judgeAction('start'));
    document.getElementById('judgeStop').addEventListener('click',()=>judgeAction('stop'));
    document.getElementById('judgeReview').addEventListener('click',()=>judgeOpen());
    document.getElementById('judgeImages').addEventListener('click',()=>judgeOpen('images'));
    document.getElementById('judgeDetails').addEventListener('click',()=>judgeOpen('all'));
    document.getElementById('judgeRecovery').addEventListener('click',()=>judgeOpen('recovery'));
    document.getElementById('judgeDialogClose').addEventListener('click',()=>document.getElementById('judgeDialog').close());
    document.getElementById('judgeDialog').addEventListener('change',judgeSelectionCount);
    document.getElementById('judgeHold').addEventListener('click',async()=>{
        const ids=[...document.querySelectorAll('#judgeDialog input[data-judge-select]:checked')].map(x=>x.dataset.judgeSelect);
        document.getElementById('judgeHold').disabled=true;
        const ok=await judgeAction('hold',{revision:judgeSnapshot.revision,candidate_ids:ids,request_id:crypto.randomUUID().replaceAll('-','')});
        if(ok){document.getElementById('judgeDialog').close();judgeOpen('recovery');}else{document.getElementById('judgeDialogNotice').textContent=document.getElementById('judgeNotice').textContent;judgeSelectionCount();}
    });
    document.getElementById('judgeRetainAll').addEventListener('click',async()=>{if(await judgeAction('retain',{item_ids:judgeCandidates(judgeSnapshot).map(x=>x.id)}))document.getElementById('judgeDialog').close();});
    document.getElementById('judgeDialogBody').addEventListener('click',async e=>{
        const button=e.target.closest('button');if(!button||judgeBusy)return;
        if(button.dataset.judgeOpen){try{await deskRequest('open',{id:button.dataset.judgeOpen});}catch(error){document.getElementById('judgeDialogNotice').textContent=error.message;}}
        else if(button.dataset.judgeRetain){if(await judgeAction('retain',{item_ids:[button.dataset.judgeRetain]})){document.getElementById('judgeDialog').close();judgeOpen();}}
        else if(button.dataset.judgeRestore){if(await judgeAction('restore',{batch_id:button.dataset.judgeRestore})){document.getElementById('judgeDialog').close();judgeOpen('recovery');}}
    });
    judgeLoad();setInterval(()=>{if(!document.hidden&&!document.getElementById('desk').classList.contains('hide'))judgeLoad(true);},5000);
});
