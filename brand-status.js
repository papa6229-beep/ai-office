(() => {
  const root = document.getElementById('brand-status');
  if (!root) return;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let data = null, busy = false, polling = false;
  root.innerHTML = `<div class="brand-head"><div><h2>브랜드 작업현황</h2><p>누가 맡았고, 어디까지 진행됐는지 확인하세요.</p></div><button id="brandRefresh">최신 상태 확인</button></div><div id="brandStamp" role="status">현황을 불러오는 중이에요.</div><div id="brandSummary" class="brand-summary"></div><div class="brand-tools"><label>브랜드 검색 <input id="brandSearch" type="search" placeholder="브랜드·차수·담당자"></label><label>담당자 <select id="brandPerson"><option value="">전체</option><option>이석환</option><option>안용훈</option><option>조현진</option><option>조혜연</option><option>미배정</option><option>미확인</option></select></label><label>상태 <select id="brandState"><option value="">전체</option><option value="진행 중">진행 중</option><option value="완료">완료</option><option value="보류">보류</option><option value="대기">대기</option><option value="미확인">미확인</option></select></label></div><p class="brand-note">자료 전달 완료와 실제 작업 완료는 달라요. 보고서 기준일과 확인되지 않은 상태를 함께 표시합니다.</p><div class="brand-table-wrap"><table><thead><tr><th>브랜드·차수</th><th>담당</th><th>자료 전달</th><th>작업 상태</th><th>전체 / 남음</th><th>확인 기준</th></tr></thead><tbody id="brandRows"></tbody></table></div><details class="brand-extra"><summary>이벤트·기타 업무</summary><div id="brandEvents"></div></details><details class="brand-extra"><summary id="brandUnresolvedTitle">확인이 필요한 자료</summary><div id="brandUnresolved"></div></details>`;
  const el = id => document.getElementById(id);
  el('brandState').insertAdjacentHTML('beforeend','<option value="미수령">미수령</option><option value="작업 예정">작업 예정</option>');
  root.querySelector('.brand-head p').textContent='입고 차수를 기준으로 보고서·메신저·자료 폴더를 함께 대조합니다.';
  root.querySelector('.brand-note').textContent='입고일과 리스트 날짜는 별개예요. 연결된 보고서는 자동 갱신하고, 차수가 불분명한 자료는 확인 대상으로 남깁니다.';
  el('brandRows').closest('table').querySelector('th').textContent='브랜드 · 입고일 / 리스트 날짜';
  root.insertAdjacentHTML('beforeend','<details class="brand-extra"><summary id="brandCandidateTitle">새 연결 확인</summary><div id="brandCandidates"></div></details><details class="brand-extra"><summary id="brandHistoryTitle">기타 배정분 · 기존 이력</summary><div id="brandHistory"></div></details><details class="brand-extra"><summary>자동 연결 범위</summary><div id="brandCoverage"></div></details>');
  function proof(r) {
    const dates=[['자료 수신',r.received_at],['작업 시작',r.start_date],['완료본 수신',r.completion_received],['상품팀 전달',r.forwarded_at]];
    return `<div class="brand-proof">${dates.filter(([,v])=>v).map(([k,v])=>`<p>${escape(k)}: ${escape(v)}</p>`).join('')}${r.forward_basis?`<p>${escape(r.forward_basis)}</p>`:''}${(r.files||[]).map(f=>`<p><span>${escape(f.role)}</span> ${escape(f.name)}<small class="brand-path">${escape(f.path)}</small></p>`).join('')}${r.unverified?`<p class="brand-caution">미확인: ${escape(r.unverified)}</p>`:''}${(r.reference_messages||[]).map(m=>`<p><small>${escape(m.posted)} · ${escape(m.room)} · ${escape(m.sender)} · 차수 참고 자료</small>${escape(m.text)}</p>`).join('')}</div>`;
  }
  function render() {
    if (!data) return;
    const rows = data.brands || [], search = el('brandSearch').value.trim().toLowerCase(), person = el('brandPerson').value, state = el('brandState').value;
    const count = status => rows.filter(r => r.work_status === status).length;
    el('brandSummary').innerHTML = [['입고 차수',rows.length],['진행 중',count('진행 중')],['완료',count('완료')],['작업 예정',count('작업 예정')],['미수령',count('미수령')]].map(([label,n])=>`<div><span>${label}</span><strong>${n}</strong></div>`).join('');
    const filtered = rows.filter(r=>(!person||r.person===person)&&(!state||(r.work_status||'').includes(state))&&(!search||[r.brand,r.batch,r.person,r.note,...(r.list_dates||[])].join(' ').toLowerCase().includes(search)));
    el('brandRows').innerHTML = filtered.length ? filtered.map(r=>{
      const tone = r.work_status==='완료'?'done':r.work_status==='진행 중'?'doing':r.work_status==='보류'?'hold':'unknown';
      const delivered = [...new Set((r.delivery_evidence||[]).map(item=>item.name))];
      return `<tr><td><strong>${escape(r.brand)}</strong><small>${r.arrival_date?'입고 ':''}${escape(r.arrival_date||r.batch)}</small>${r.list_dates?.length?`<small>리스트 ${escape(r.list_dates.join(' · '))}</small>`:''}<details><summary>연결 근거 · 자료 · 대화</summary><p>${escape(r.image_source)}</p>${delivered.length?`<p>발신 확인: ${escape(delivered.join(' · '))}</p>`:''}${r.note?`<p>${escape(r.note)}</p>`:''}${r.folder?`<p class="brand-path">${escape(r.folder)}</p>`:''}${proof(r)}</details></td><td>${escape(r.person)}</td><td>${escape(r.delivery)}${r.delivery_warning?`<small>${escape(r.delivery_warning)}</small>`:''}${r.material_warning?`<small class="brand-caution">${escape(r.material_warning)}</small>`:''}${r.forwarded_at?'<small>상품팀 완료본 전달 확인</small>':''}</td><td><span class="brand-state ${tone}">${escape(r.work_status)}</span>${r.completion_warning?`<small>${escape(r.completion_warning)}</small>`:''}</td><td>${r.total!=null?`${escape(r.total)} / ${escape(r.remaining)}`:'미확인'}${r.report_day?'<small>보고서 집계 기준</small>':''}</td><td>${escape(r.state_basis||'대화·파일 근거')}${r.report_day?`<small>보고서 ${escape(r.report_day)}</small>`:''}${r.confirmed_at?`<small>사용자 기준 ${escape(r.confirmed_at.slice(0,10))}</small>`:''}${r.completed?`<small>완료 ${escape(r.completed)}</small>`:''}${r.completion_basis?`<small>${escape(r.completion_basis)}</small>`:''}</td></tr>`;
    }).join('') : '<tr><td colspan="6">조건에 맞는 브랜드가 없어요.</td></tr>';
    const warnings = data.warnings || [];
    const auto=data.auto_sync||{};
    el('brandStamp').textContent = ((busy||data.busy)?'최신 기록을 대조하고 있어요. 기존 현황을 먼저 표시합니다.':'자료 대조: '+(data.as_of||'미확인').replace('T',' '))+(auto.enabled?' · 5분마다 코드 자동 대조':'')+(auto.error?' · 자동 대조 실패, 마지막 성공 현황':'')+(warnings.length?' · '+warnings.join(' / '):'');
    el('brandEvents').innerHTML = (data.events||[]).map(e=>`<div><strong>${escape(e.name)}</strong><span>${escape(e.person)} · ${escape(e.delivery)}</span><p>${escape(e.status)}</p></div>`).join('');
    const unknown=data.unresolved_materials||[];
    el('brandUnresolvedTitle').textContent=`확인이 필요한 자료 ${unknown.length}묶음`;
    el('brandUnresolved').innerHTML=unknown.map(x=>`<div><strong>${escape(x.name)}</strong><p>${escape(x.status)}</p><small class="brand-path">${escape(x.path)}</small></div>`).join('');
    const candidates=data.link_candidates||[];
    el('brandCandidateTitle').textContent=`차수 연결을 확인할 자료 ${candidates.length}개`;
    el('brandCandidates').innerHTML=candidates.map(f=>`<div><strong>${escape(f.name)}</strong><p class="brand-path">${escape(f.path)}</p><small>등록한 입고 차수와 아직 연결되지 않은 자료예요. 과거 차수일 수 있어 자동 완료로 처리하지 않아요.</small></div>`).join('');
    const history=data.other_batches||[];
    el('brandHistoryTitle').textContent=`기타 배정분 · 기존 이력 ${history.length}건`;
    el('brandHistory').innerHTML=history.map(r=>`<div><strong>${escape(r.brand)} · ${escape(r.batch)}</strong><span>${escape(r.person)} · ${escape(r.work_status)}</span><small>현재 입고 목록과 차수 연결이 확인되지 않은 기존 기록</small></div>`).join('');
    const coverage=data.coverage||{}, ranges=coverage.nateon?.native_exports||[];
    el('brandCoverage').innerHTML=`<div><p>한비로: ${escape(coverage.hanbiro||'조회 전')}</p><p>네이트온: ${escape(coverage.nateon?.scope||'조회 전')}</p>${ranges.map(r=>`<small>공식 내보내기: ${escape(r.first)} ~ ${escape(r.last)}</small>`).join('')}<p>폴더 파일 ${escape(coverage.files_scanned??0)}개 대조 · 연결 ${escape(coverage.linked_files??0)}개</p><p>본문만 같은 대화나 날짜가 다른 파일은 자동 상태 변경 근거로 쓰지 않습니다. 새로운 배정·발신·파일 이동은 수행하지 않아요.</p></div>`;
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
    try{const response=await fetch('/api/work/brand-status',{cache:'no-store'});if(!response.ok)throw Error('현황 연결을 준비 중이에요.');data=await response.json();render();}
    catch(error){el('brandStamp').textContent=error.message;}
  };
  el('brandRefresh').addEventListener('click',refresh);
  for(const id of ['brandSearch','brandPerson','brandState'])el(id).addEventListener(id==='brandSearch'?'input':'change',render);
  setInterval(async()=>{
    if(busy||polling||!data||root.offsetParent===null||document.hidden)return;
    polling=true;
    try{await window.brandStatusLoad();}finally{polling=false;}
  },15000);
})();
