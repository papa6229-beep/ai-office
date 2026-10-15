/* App-level organization is reversible; source paths are never changed here. */
let deskGroupBusy=false,deskGroupView='suggestions',deskActiveCollection=null,deskDuplicateResult=null;
function deskGroupDialog(){return document.getElementById('deskGroupsDialog');}
function deskApplyGroupState(data){for(const key of ['collections','suggestions','dismissed_count','auto_organize_enabled','last_auto_group_ids'])if(key in data)deskData[key]=data[key];deskData.grouping_note=data.note;deskRender();}
function deskRenderCollections(query=''){
 const collections=deskData.collections||[],suggestions=deskData.suggestions||[];
 const visible=collections.filter(g=>!query||(g.label+' '+g.items.map(x=>x.name).join(' ')).toLocaleLowerCase().includes(query));
 document.getElementById('deskCollections').innerHTML=visible.length?visible.map(g=>`<button type="button" class="desk-workbox" data-collection="${esc(g.id)}"><span class="desk-box-icon" aria-hidden="true">▱</span><span class="desk-box-title">${esc(g.label)}</span><span class="desk-box-count">${g.source==='content_ai'?'내용으로 연결 · ':g.source==='automatic'?'이름으로 묶음 · ':''}${g.items.length}개${g.missing_count?' · 찾을 수 없음 '+g.missing_count+'개':''}</span><span class="desk-box-examples">${g.items.slice(0,2).map(x=>esc(x.name)).join('<br>')}</span></button>`).join(''):`<div class="desk-pin-placeholder">${query?'검색에 맞는 작업함이 없습니다.':'관련 이름이 있는 자료는 자동으로 묶습니다. AI 내용 분석을 켜면 연결되는 업무 자료가 여기에 나타납니다.'}</div>`;
 document.getElementById('deskCollectionsCount').textContent=collections.length+'개';
 const enabled=deskData.auto_organize_enabled!==false;
 document.getElementById('deskAutomationState').textContent=enabled?'자동정리 켜짐':'자동정리 일시 정지';
 document.getElementById('deskAutomationToggle').textContent=enabled?'자동정리 일시 정지':'자동정리 다시 시작';
 document.getElementById('deskUndoAuto').disabled=!(deskData.last_auto_group_ids||[]).length;
 document.getElementById('deskSuggestCount').textContent=suggestions.length+'개';
 document.getElementById('deskGroupedCount').textContent=collections.reduce((n,g)=>n+g.items.length,0)+'개';
}
async function deskMaybeAutoOrganize(){
 if(!deskData||deskGroupBusy||deskData.auto_organize_enabled===false||!(deskData.suggestions||[]).length)return;
 deskGroupBusy=true;
 try{const before=(deskData.collections||[]).length;const data=await deskRequest('auto-organize',{enabled:true});deskApplyGroupState(data);const created=(data.collections||[]).length-before;if(created>0)deskMessage(`관련 자료를 ${created}개 작업함으로 자동 정리했습니다. 원본 위치는 유지됩니다.`);}
 catch(e){deskMessage('자동 작업함 정리를 완료하지 못했습니다: '+e.message);}
 finally{deskGroupBusy=false;}
}
function deskOpenGroups(mode='suggestions',collectionId=null){
 deskGroupView=mode;deskActiveCollection=collectionId;
 const title=document.getElementById('deskGroupsTitle'),body=document.getElementById('deskGroupsBody');
 if(mode==='collection'){
  const group=(deskData.collections||[]).find(g=>g.id===collectionId);if(!group){deskGroupDialog().close();return;}
  title.textContent=group.label;
  body.innerHTML=`<p class="desk-group-note">작업함은 앱 안의 묶음입니다. 파일을 빼거나 작업함을 해제해도 원본은 유지됩니다.</p>${group.evidence?`<p class="desk-group-note"><strong>함께 묶은 이유</strong> · ${esc(group.evidence)}</p>`:''}<div class="desk-group-edit"><label for="deskGroupLabel">작업함 이름</label><input id="deskGroupLabel" maxlength="120" value="${esc(group.label)}"><button type="button" data-group-action="rename">이름 저장</button><button type="button" class="desk-group-secondary" data-group-action="dissolve">작업함 해제</button></div>${group.missing_count?`<p class="desk-group-note">원래 위치에서 찾을 수 없는 항목 ${group.missing_count}개가 있습니다.</p>`:''}<div class="desk-items">${group.items.map(item=>`<div class="desk-member">${deskCard(item,(deskData.pinned_ids||[]).includes(item.id))}<button type="button" class="desk-remove-member" data-remove-member="${esc(item.id)}">작업함에서 빼기</button></div>`).join('')}</div>`;
 }else if(mode==='create'){
  title.textContent='작업함 만들기';const occupied=new Set((deskData.collections||[]).flatMap(g=>g.item_ids));const available=(deskData.items||[]).filter(x=>x.kind!=='app'&&!occupied.has(x.id));
  body.innerHTML=`<p class="desk-group-note">자동으로 연결하지 못한 자료를 직접 묶을 때 사용하세요. 최대 100개까지 선택할 수 있습니다.</p><div class="desk-group-edit"><label for="deskNewGroupLabel">작업함 이름</label><input id="deskNewGroupLabel" maxlength="120" placeholder="예: 이번 주 행사 준비"><button type="button" data-group-action="create">선택한 자료 묶기</button></div><input id="deskManualSearch" type="search" placeholder="선택할 파일이나 폴더 찾기" aria-label="작업함에 넣을 자료 검색"><div id="deskManualItems" class="desk-manual-items">${available.map(x=>`<label class="desk-manual-item" data-search="${esc(x.name.toLocaleLowerCase())}"><input type="checkbox" value="${esc(x.id)}"><span>${esc(x.name)}<small>${esc(x.category)}</small></span></label>`).join('')||'<p class="desk-group-note">모든 자료가 이미 작업함에 있습니다.</p>'}</div>`;
 }else if(mode==='duplicates'){
  title.textContent='내용이 같은 파일';
  if(!deskDuplicateResult)body.innerHTML='<p class="desk-group-note">같은 크기의 파일 내용을 비교하고 있습니다. 파일을 변경하지 않습니다.</p>';
  else{const r=deskDuplicateResult,occupied=new Set((deskData.collections||[]).flatMap(g=>g.item_ids));body.innerHTML=`<p class="desk-group-note">${r.checked}개 파일의 내용을 비교해 ${r.groups.length}개 동일 내용 묶음을 확인했습니다. 같은 파일도 다른 작업에 필요한 위치일 수 있습니다.</p><div class="desk-proposal-grid">${r.groups.map((g,index)=>`<article class="desk-proposal"><h4>동일 내용 ${g.items.length}개</h4><p>${esc(g.reason)}</p><p class="desk-group-note">추가 복사본 용량 ${formatSize(g.duplicate_bytes)} · 삭제 대상으로 확정하지 않았습니다.</p>${deskGroupPreview(g.items)}<button type="button" data-duplicate-group="${index}" ${g.item_ids.some(id=>occupied.has(id))?'disabled':''}>비교 작업함으로 묶기</button>${g.item_ids.some(id=>occupied.has(id))?'<p class="desk-group-note">다른 작업함에 있는 자료가 포함돼 있습니다.</p>':''}</article>`).join('')||'<p class="desk-group-note">이번 비교 범위에서 동일 내용 묶음을 찾지 못했습니다.</p>'}</div>${r.warnings.length?`<details class="desk-group-warnings"><summary>비교하지 못한 항목 ${r.warnings.length}개</summary><ul>${r.warnings.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`:''}`;}
 }else{
  title.textContent='정리 제안함';const suggestions=deskData.suggestions||[];
  body.innerHTML=`<p class="desk-group-note">${esc(deskData.grouping_note||'파일명으로 찾은 관련 자료 후보입니다. 업무 관계와 최신 버전은 아직 확정하지 않았습니다.')}</p><div class="desk-group-toolbar"><button type="button" data-group-action="duplicates">내용이 같은 파일 확인</button><button type="button" class="desk-group-secondary" data-group-action="restore-dismissed" ${(deskData.dismissed_count||0)?'':'disabled'}>보류한 제안 다시 보기 (${deskData.dismissed_count||0})</button></div><div class="desk-proposal-grid">${suggestions.map(g=>`<article class="desk-proposal"><h4>${esc(g.label)} <small>${g.items.length}개</small></h4><p>${esc(g.reason)}</p>${deskGroupPreview(g.items)}<label for="proposal-${esc(g.id)}">작업함 이름</label><input id="proposal-${esc(g.id)}" maxlength="120" value="${esc(g.label)}"><div class="desk-proposal-actions"><button type="button" data-accept-proposal="${esc(g.id)}">작업함으로 묶기</button><button type="button" class="desk-group-secondary" data-dismiss-proposal="${esc(g.id)}">보류</button></div></article>`).join('')||'<div class="desk-group-note desk-proposal-empty">남은 관련 자료 제안이 없습니다. 자동 작업함과 내용이 같은 파일을 확인할 수 있습니다.</div>'}</div>`;
 }
 if(!deskGroupDialog().open)deskGroupDialog().showModal();
}
function deskGroupPreview(items){const preview=items.slice(0,3);return `<div class="desk-group-preview">${preview.map(x=>`<div>${x.kind==='file'&&['.png','.jpg','.jpeg','.webp'].includes(x.extension)&&x.size<=8388608?`<img src="/api/desk/thumbnail?id=${encodeURIComponent(x.id)}" loading="lazy" alt="">`:`<span class="desk-preview-type">${esc(x.extension||x.category)}</span>`}<span>${esc(x.name)}</span></div>`).join('')}</div><details class="desk-group-filelist"><summary>전체 ${items.length}개 파일 이름 확인</summary><ul>${items.map(x=>`<li>${esc(x.name)}</li>`).join('')}</ul></details>`;}
async function deskGroupMutate(action,payload,button){
 if(deskGroupBusy)return false;deskGroupBusy=true;if(button)button.disabled=true;
 try{const data=await deskRequest(action,payload);deskApplyGroupState(data);deskMessage('작업함 정리를 저장했습니다.');return true;}
 catch(e){deskMessage(e.message);document.getElementById('deskGroupsStatus').textContent=e.message;return false;}
 finally{deskGroupBusy=false;if(button)button.disabled=false;}
}
async function deskCheckDuplicates(button){
 if(deskGroupBusy)return;deskGroupBusy=true;if(button)button.disabled=true;deskDuplicateResult=null;deskOpenGroups('duplicates');
 try{deskDuplicateResult=await deskRequest('duplicates',{});deskOpenGroups('duplicates');}
 catch(e){document.getElementById('deskGroupsBody').textContent='내용 비교를 완료하지 못했습니다: '+e.message;}
 finally{deskGroupBusy=false;if(button)button.disabled=false;}
}
document.addEventListener('DOMContentLoaded',()=>{
 document.getElementById('deskSuggestOpen').addEventListener('click',()=>deskOpenGroups('suggestions'));
 document.getElementById('deskCreateGroup').addEventListener('click',()=>deskOpenGroups('create'));
 document.getElementById('deskGroupsClose').addEventListener('click',()=>deskGroupDialog().close());
 document.getElementById('deskShowGrouped').addEventListener('change',deskRender);
 document.getElementById('deskAutomationToggle').addEventListener('click',async e=>{const enabled=deskData.auto_organize_enabled===false;await deskGroupMutate('auto-organize',{enabled},e.currentTarget);});
 document.getElementById('deskUndoAuto').addEventListener('click',async e=>{if(await deskGroupMutate('undo-auto',{},e.currentTarget))deskMessage('최근 자동 작업함을 풀고 자동정리를 일시 정지했습니다.');});
 document.getElementById('deskCollections').addEventListener('click',e=>{const b=e.target.closest('[data-collection]');if(b)deskOpenGroups('collection',b.dataset.collection);});
 document.getElementById('deskGroupsBody').addEventListener('input',e=>{if(e.target.id==='deskManualSearch'){const query=e.target.value.toLocaleLowerCase();document.querySelectorAll('#deskManualItems [data-search]').forEach(x=>x.hidden=!x.dataset.search.includes(query));}});
 document.getElementById('deskGroupsBody').addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button||deskGroupBusy)return;document.getElementById('deskGroupsStatus').textContent='';
  if(button.dataset.acceptProposal){const g=(deskData.suggestions||[]).find(x=>x.id===button.dataset.acceptProposal);if(g&&await deskGroupMutate('create-collection',{label:document.getElementById('proposal-'+g.id).value,item_ids:g.item_ids},button))deskOpenGroups('suggestions');}
  else if(button.dataset.dismissProposal){if(await deskGroupMutate('dismiss-suggestion',{id:button.dataset.dismissProposal},button))deskOpenGroups('suggestions');}
  else if(button.dataset.removeMember){if(await deskGroupMutate('remove-member',{collection_id:deskActiveCollection,item_id:button.dataset.removeMember},button))deskOpenGroups('collection',deskActiveCollection);}
  else if(button.dataset.duplicateGroup!==undefined){const g=deskDuplicateResult?.groups[Number(button.dataset.duplicateGroup)];if(g&&await deskGroupMutate('create-collection',{label:'동일 내용 · '+g.items[0].name.slice(0,95),item_ids:g.item_ids},button))deskOpenGroups('duplicates');}
  else if(button.dataset.groupAction==='rename'){if(await deskGroupMutate('rename-collection',{id:deskActiveCollection,label:document.getElementById('deskGroupLabel').value},button))deskOpenGroups('collection',deskActiveCollection);}
  else if(button.dataset.groupAction==='dissolve'){if(await deskGroupMutate('dissolve-collection',{id:deskActiveCollection},button))deskGroupDialog().close();}
  else if(button.dataset.groupAction==='create'){const item_ids=[...document.querySelectorAll('#deskManualItems input:checked')].map(x=>x.value);if(await deskGroupMutate('create-collection',{label:document.getElementById('deskNewGroupLabel').value,item_ids},button))deskGroupDialog().close();}
  else if(button.dataset.groupAction==='restore-dismissed'){if(await deskGroupMutate('restore-dismissed',{},button))deskOpenGroups('suggestions');}
  else if(button.dataset.groupAction==='duplicates')deskCheckDuplicates(button);
 });
});
