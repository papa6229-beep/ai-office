(() => {
  const root=document.getElementById('brand-status');
  if(!root)return;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const states=['작업중','완료','작업예정','대기·보류'];
  const people=['','이석환','안용훈','조현진','조혜연'];
  const options=(values,current)=>values.map(v=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(v||'미지정')}</option>`).join('');
  let data=null,token='',polling=false,saving=false,editing=null;
  root.innerHTML=`<div class="brand-head"><div><h2>신상품 샘플 등록 현황</h2><p>상품팀과 웹팀이 함께 관리하는 입고 차수별 기반표</p></div><div class="brand-head-actions"><a href="/brand-register" target="_blank" rel="noopener">표만 열기 ↗</a><button id="brandRefresh" type="button">표 새로고침</button></div></div>
    <div class="brand-owners"><span><i class="product-dot"></i>상품팀: 입고 · 리스트작업일 · 전달 · 우선순위</span><span><i class="web-dot"></i>웹팀: 현상태 · 담당자 · 비고</span></div>
    <div class="brand-tools"><label>브랜드 검색<input id="brandSearch" type="search" placeholder="브랜드·입고일·담당자"></label><label>담당자<select id="brandPerson"><option value="">전체</option><option value="__blank">미배정</option>${options(people.filter(Boolean),'')}</select></label><label>상태<select id="brandState"><option value="">전체</option>${options(states,'')}</select></label></div>
    <div id="brandStamp" role="status">기반표를 불러오는 중이에요.</div><p id="brandCounts" class="brand-counts"></p>
    <div class="brand-table-wrap"><table class="brand-register-table"><thead><tr><th>순번</th><th>브랜드</th><th>총 가짓수</th><th>종류(상세)</th><th class="product-field">물류입고일</th><th class="product-field">리스트작업일</th><th class="product-field">웹팀 전달여부</th><th class="product-field">우선순위</th><th class="web-field">현상태</th><th class="web-field">담당자</th><th>단종 체크 여부</th><th>비고</th></tr></thead><tbody id="brandRows"></tbody></table></div>
    <p class="brand-note">입고일과 리스트 파일 날짜는 별개입니다. 브랜드명을 누르면 수정할 수 있습니다. 전달 X인 행은 상태를 비워둡니다.</p>
    <details class="brand-extra"><summary>기존 대조 근거와 다른 배정분</summary><div id="brandHistory"></div></details>
    <dialog id="brandEditDialog" class="brand-dialog" aria-labelledby="brandEditTitle"><form id="brandEditForm"><div class="brand-dialog-head"><div><h3 id="brandEditTitle"></h3><p id="brandEditSub"></p></div><button type="button" data-close="brandEditDialog" aria-label="수정 취소">×</button></div><div class="brand-edit-tabs"><button type="button" data-section="product">상품팀 항목</button><button type="button" data-section="web">웹팀 항목</button></div><div id="brandEditFields" class="brand-edit-fields"></div><p id="brandEditError" class="brand-edit-error" role="alert"></p><div class="brand-dialog-actions"><button type="button" data-close="brandEditDialog">취소</button><button id="brandSave" type="submit">변경 저장</button></div></form></dialog>
    <dialog id="brandProofDialog" class="brand-dialog"><div class="brand-dialog-head"><h3>연결 근거</h3><button type="button" data-close="brandProofDialog" aria-label="근거 닫기">×</button></div><div id="brandProofBody" class="brand-proof"></div></dialog>`;
  const el=id=>document.getElementById(id);
  const display=v=>v===null||v===undefined||v===''?'미확인':esc(v);
  const deliveryMark=v=>({'수령 확인':'O','전달 완료':'O','미수령':'X','미전달':'X','O':'O','X':'X'})[v]||'';
  function render(){
    if(!data)return;
    const statusMap={'진행 중':'작업중','작업 예정':'작업예정','대기':'대기·보류','보류':'대기·보류','미수령':''};
    const all=(data.brands||[]).map(r=>({...r,work_status:deliveryMark(r.table_fields?.web_delivery)==='X'?'':statusMap[r.work_status]??r.work_status??''})),query=el('brandSearch').value.trim().toLowerCase(),person=el('brandPerson').value,state=el('brandState').value;
    const rows=all.filter(r=>(!person||(person==='__blank'?!r.person:r.person===person))&&(!state||r.work_status===state)&&(!query||[r.brand,r.arrival_date,r.person].join(' ').toLowerCase().includes(query)));
    el('brandCounts').textContent=`전체 ${all.length}건 · 작업중 ${all.filter(r=>r.work_status==='작업중').length} · 완료 ${all.filter(r=>r.work_status==='완료').length} · 작업예정 ${all.filter(r=>r.work_status==='작업예정').length} · 대기·보류 ${all.filter(r=>r.work_status==='대기·보류').length}`;
    el('brandRows').innerHTML=rows.map(r=>{
      const f=r.table_fields||{},tone=r.work_status==='완료'?'done':r.work_status==='작업중'?'doing':r.work_status==='작업예정'?'scheduled':r.work_status==='대기·보류'?'hold':'';
      return `<tr class="brand-row ${tone}"><td>${esc(r.sequence)}</td><td><button type="button" class="brand-edit-name" data-edit="${esc(r.id)}" title="${esc(r.brand)} 항목 수정">${esc(r.brand)}</button></td><td>${display(f.total_count)}</td><td>${display(f.kind_count)}</td><td>${display(f.arrival_date||r.arrival_date)}</td><td>${display(f.list_work_date)}</td><td>${esc(deliveryMark(f.web_delivery)||'—')}</td><td><span class="${f.priority==='최우선'?'brand-priority':''}">${display(f.priority)}</span></td><td class="brand-status-cell">${esc(r.work_status)}</td><td>${esc(['미확인','미배정'].includes(r.person)?'':r.person)}</td><td class="${f.discontinuation==='확인 필요'?'brand-caution':''}">${esc(f.discontinuation??'')}</td><td class="brand-remarks">${esc(f.remarks??'')}</td></tr>`;
    }).join('')||'<tr><td colspan="12">조건에 맞는 입고 차수가 없어요.</td></tr>';
    el('brandStamp').textContent='표 저장 기준: '+(data.table_updated_at||data.registry_confirmed_at?new Date(data.table_updated_at||data.registry_confirmed_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}):'');
    el('brandHistory').innerHTML=`<p>기존 파일·대화 대조 자료는 각 행의 ‘근거’에서 볼 수 있습니다. 파일 정리·전달 결과는 별도 이벤트 기록으로 연결됩니다.</p><p>기타 배정분 ${(data.other_batches||[]).length}건 · 차수 연결 확인 자료 ${(data.link_candidates||[]).length}개를 보존하고 있습니다.</p>`;
  }
  async function load(){
    const response=await fetch('/api/work/brand-status',{cache:'no-store'});
    if(!response.ok)throw Error('기반표를 불러오지 못했어요. 다시 시도해 주세요.');
    data=await response.json();token=data.action_token||token;render();
    const external=root.querySelector('.brand-head-actions a');
    if(external&&data.origin){external.href=data.origin;external.textContent='독립 현황판 열기 ↗';}
  }
  function editFields(section){
    if(editing.section)editing.drafts[editing.section]=Object.fromEntries(new FormData(el('brandEditForm')));
    editing.section=section;const f={...editing.row.table_fields,...editing.drafts[section]};
    f.web_delivery=deliveryMark(f.web_delivery);
    const input=(key,title,type='text')=>`<label>${title}<input name="${key}" type="${type}" value="${esc(f[key]??'')}" ${type==='number'?'min="0" step="1"':''} ${key==='brand'?'required':''}></label>`;
    const select=(key,title,list)=>`<label>${title}<select name="${key}">${options(list,f[key]??'')}</select></label>`;
    el('brandEditFields').innerHTML=section==='product'?
      input('brand','브랜드')+input('total_count','총 가짓수','number')+input('kind_count','종류(상세)','number')+input('arrival_date','물류입고일','date')+input('list_work_date','리스트작업일','date')+select('web_delivery','웹팀 전달여부',f.web_delivery?['O','X']:['','O','X'])+select('priority','우선순위',['','최우선','1순위','2순위','3순위','미지정'])+input('discontinuation','단종 체크 여부'):
      select('work_status','현상태',['',...states])+select('person','담당자',people)+`<label class="brand-remarks-input">비고 · 대기·보류 사유<textarea name="remarks" maxlength="1000" rows="3">${esc(f.remarks??'')}</textarea></label>`;
    const stateField=el('brandEditFields').querySelector('[name=work_status]');
    if(stateField&&deliveryMark(editing.row.table_fields?.web_delivery)==='X'){stateField.value='';stateField.disabled=true;}
    for(const button of root.querySelectorAll('[data-section]'))button.classList.toggle('selected',button.dataset.section===section);
    el('brandEditError').textContent='';
  }
  root.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.close&&!saving){el(button.dataset.close).close();return;}
    if(button.dataset.section&&editing&&!saving){editFields(button.dataset.section);return;}
    const id=button.dataset.edit||button.dataset.proof;if(!id)return;
    const row=(data?.brands||[]).find(r=>r.id===id);if(!row)return;
    if(button.dataset.edit){
      editing={row,section:null,drafts:{}};el('brandEditTitle').textContent=row.brand;el('brandEditSub').textContent='물류입고 '+row.arrival_date;editFields('web');el('brandEditDialog').showModal();
    }else{
      el('brandProofBody').innerHTML=`<p>상태 기준: ${esc(row.state_basis||'사용자 확인')}</p>${row.note?`<p>${esc(row.note)}</p>`:''}${(row.files||[]).map(f=>`<p><strong>${esc(f.role)}</strong> ${esc(f.name)}<small class="brand-path">${esc(f.path)}</small></p>`).join('')}${row.unverified?`<p class="brand-caution">${esc(row.unverified)}</p>`:''}${(row.reference_messages||[]).map(m=>`<p><small>${esc(m.posted)} · ${esc(m.room)} · ${esc(m.sender)} · 차수 참고 자료</small>${esc(m.text)}</p>`).join('')}`;
      el('brandProofDialog').showModal();
    }
  });
  el('brandEditDialog').addEventListener('cancel',event=>{if(saving)event.preventDefault();});
  el('brandEditForm').addEventListener('submit',async event=>{
    event.preventDefault();if(saving||!editing)return;
    const fields=Object.fromEntries(new FormData(event.target));
    for(const key of ['total_count','kind_count'])if(key in fields)fields[key]=fields[key]===''?null:Number(fields[key]);
    const payload={id:editing.row.id,expected_revision:editing.row.row_revision,section:editing.section,fields};
    saving=true;for(const field of el('brandEditForm').querySelectorAll('input,select,textarea,button'))field.disabled=true;el('brandEditError').textContent='';
    try{
      if(typeof api==='function')data=await api('work/brand-status-save',payload);
      else{
        const response=await fetch('/api/work/brand-status-save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,token})});
        const value=await response.json();if(!response.ok)throw Error(value.error||'저장하지 못했어요.');data=value;
      }
      el('brandEditDialog').close();render();el('brandStamp').textContent=data.save_result?.unchanged?'변경된 값이 없습니다.':'변경 사항을 저장했어요.';
    }catch(error){el('brandEditError').textContent=error.message;}
    finally{saving=false;for(const field of el('brandEditForm').querySelectorAll('input,select,textarea,button'))field.disabled=false;}
  });
  window.brandStatusLoad=async()=>{try{await load();}catch(error){el('brandStamp').textContent=error.message;}};
  el('brandRefresh').addEventListener('click',window.brandStatusLoad);
  for(const id of ['brandSearch','brandPerson','brandState'])el(id).addEventListener(id==='brandSearch'?'input':'change',render);
  setInterval(async()=>{if(polling||saving||!data||el('brandEditDialog').open||el('brandProofDialog').open||root.offsetParent===null||document.hidden)return;polling=true;try{await window.brandStatusLoad();}finally{polling=false;}},15000);
})();
