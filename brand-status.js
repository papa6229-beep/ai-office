(() => {
  const root = document.getElementById('brand-status');
  if (!root) return;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let data = null, busy = false;
  root.innerHTML = `<div class="brand-head"><div><h2>브랜드 작업현황</h2><p>누가 맡았고, 어디까지 진행됐는지 확인하세요.</p></div><button id="brandRefresh">최신 상태 확인</button></div><div id="brandStamp" role="status">현황을 불러오는 중이에요.</div><div id="brandSummary" class="brand-summary"></div><div class="brand-tools"><label>브랜드 검색 <input id="brandSearch" type="search" placeholder="브랜드·차수·담당자"></label><label>담당자 <select id="brandPerson"><option value="">전체</option><option>이석환</option><option>안용훈</option><option>조현진</option><option>조혜연</option><option>미배정</option><option>미확인</option></select></label><label>상태 <select id="brandState"><option value="">전체</option><option value="진행 중">진행 중</option><option value="완료">완료</option><option value="보류">보류</option><option value="대기">대기</option><option value="미확인">미확인</option></select></label></div><p class="brand-note">자료 전달 완료와 실제 작업 완료는 달라요. 보고서 기준일과 확인되지 않은 상태를 함께 표시합니다.</p><div class="brand-table-wrap"><table><thead><tr><th>브랜드·차수</th><th>담당</th><th>자료 전달</th><th>작업 상태</th><th>전체 / 남음</th><th>확인 기준</th></tr></thead><tbody id="brandRows"></tbody></table></div><details class="brand-extra"><summary>이벤트·기타 업무</summary><div id="brandEvents"></div></details><details class="brand-extra"><summary id="brandUnresolvedTitle">확인이 필요한 자료</summary><div id="brandUnresolved"></div></details>`;
  const el = id => document.getElementById(id);
  function render() {
    if (!data) return;
    const rows = data.brands || [], search = el('brandSearch').value.trim().toLowerCase(), person = el('brandPerson').value, state = el('brandState').value;
    const count = status => rows.filter(r => r.work_status === status).length;
    el('brandSummary').innerHTML = [['브랜드·배정분',rows.length],['진행 중',count('진행 중')],['완료',count('완료')],['보류',count('보류')]].map(([label,n])=>`<div><span>${label}</span><strong>${n}</strong></div>`).join('');
    const filtered = rows.filter(r=>(!person||r.person===person)&&(!state||(r.work_status||'').includes(state))&&(!search||[r.brand,r.batch,r.person,r.note].join(' ').toLowerCase().includes(search)));
    el('brandRows').innerHTML = filtered.length ? filtered.map(r=>{
      const tone = r.work_status==='완료'?'done':r.work_status==='진행 중'?'doing':r.work_status==='보류'?'hold':'unknown';
      const delivered = [...new Set((r.delivery_evidence||[]).map(item=>item.name))];
      return `<tr><td><strong>${escape(r.brand)}</strong><small>${escape(r.batch)}</small><details><summary>자료·메모</summary><p>${escape(r.image_source)}</p>${delivered.length?`<p>발신 확인: ${escape(delivered.join(' · '))}</p>`:''}${r.note?`<p>기존 대조 메모: ${escape(r.note)}</p>`:''}${r.folder?`<p class="brand-path">${escape(r.folder)}</p>`:''}</details></td><td>${escape(r.person)}</td><td>${escape(r.delivery)}${r.delivery_warning?`<small>${escape(r.delivery_warning)}</small>`:''}</td><td><span class="brand-state ${tone}">${escape(r.work_status)}</span>${r.completion_warning?`<small>${escape(r.completion_warning)}</small>`:''}</td><td>${r.total!=null?`${escape(r.total)} / ${escape(r.remaining)}`:'미확인'}</td><td>${escape(r.report_day||'대화·파일 근거')}${r.completed?`<small>완료 ${escape(r.completed)}</small>`:''}${r.completion_basis?`<small>${escape(r.completion_basis)}</small>`:''}</td></tr>`;
    }).join('') : '<tr><td colspan="6">조건에 맞는 브랜드가 없어요.</td></tr>';
    const warnings = data.warnings || [];
    el('brandStamp').textContent = (busy?'최신 기록을 대조하고 있어요. 기존 현황을 먼저 표시합니다.':'자료 대조: '+(data.as_of||'미확인').replace('T',' '))+(warnings.length?' · '+warnings.join(' / '):'');
    el('brandEvents').innerHTML = (data.events||[]).map(e=>`<div><strong>${escape(e.name)}</strong><span>${escape(e.person)} · ${escape(e.delivery)}</span><p>${escape(e.status)}</p></div>`).join('');
    const unknown=data.unresolved_materials||[];
    el('brandUnresolvedTitle').textContent=`확인이 필요한 자료 ${unknown.length}묶음`;
    el('brandUnresolved').innerHTML=unknown.map(x=>`<div><strong>${escape(x.name)}</strong><p>${escape(x.status)}</p><small class="brand-path">${escape(x.path)}</small></div>`).join('');
    el('brandRefresh').disabled=busy;
  }
  async function refresh() {
    if(busy)return;
    busy=true;render();el('brandRefresh').disabled=true;
    try{data=await api('work/brand-status-sync',{});}
    catch(error){el('brandStamp').textContent='최신 기록을 확인하지 못했어요. 마지막 현황을 유지합니다. '+error.message;return;}
    finally{busy=false;el('brandRefresh').disabled=false;}
    render();
  }
  window.brandStatusLoad=async()=>{
    try{const response=await fetch('/api/work/brand-status',{cache:'no-store'});if(!response.ok)throw Error('현황 연결을 준비 중이에요.');data=await response.json();render();if(data.configured)await refresh();}
    catch(error){el('brandStamp').textContent=error.message;}
  };
  el('brandRefresh').addEventListener('click',refresh);
  for(const id of ['brandSearch','brandPerson','brandState'])el(id).addEventListener(id==='brandSearch'?'input':'change',render);
})();
