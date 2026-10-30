(()=>{
  const root=document.getElementById('codexUsageWidget');
  if(!root)return;
  const refreshInterval=60*60*1000;
  let lastLoadAt=0;
  const status=root.querySelector('.codex-usage-status');
  const refresh=root.querySelector('.codex-usage-refresh');
  const stamp=root.querySelector('.codex-usage-foot');
  const rows=root.querySelector('.codex-usage-windows');
  const gauge=root.querySelector('.codex-usage-gauge');
  const value=root.querySelector('.codex-usage-value');
  const period=root.querySelector('.codex-usage-period');
  const reset=root.querySelector('.codex-usage-reset');
  const escapeText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const validWindow=item=>item&&typeof item.used_percent==='number'&&Number.isFinite(item.used_percent)&&item.used_percent>=0&&item.used_percent<=100;
  const labelFor=minutes=>{
    if(minutes===10080)return '주간';
    if(!Number.isFinite(minutes)||minutes<=0)return '현재 한도';
    return minutes%1440===0?`${minutes/1440}일`:minutes%60===0?`${minutes/60}시간`:`${minutes}분`;
  };
  const resetLabel=seconds=>Number.isFinite(seconds)&&seconds>0?new Date(seconds*1000).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';
  const percent=used=>Math.round((100-used)*10)/10;
  function unavailable(message){
    root.dataset.state='unavailable';
    gauge.style.setProperty('--remaining',0);
    gauge.setAttribute('aria-label','잔여 사용량 조회 불가');
    value.textContent='—';period.textContent='잔여 사용량';reset.textContent='데이터 확인 필요';
    rows.replaceChildren();status.textContent=message;
  }
  async function load(){
    if(refresh.disabled)return;
    lastLoadAt=Date.now();
    refresh.disabled=true;root.setAttribute('aria-busy','true');
    status.textContent='사용량 확인 중…';
    const controller=new AbortController();
    const timeout=window.setTimeout(()=>controller.abort(),25000);
    try{
      const response=await fetch('/api/work/codex-usage',{cache:'no-store',signal:controller.signal});
      const data=await response.json();
      if(!response.ok||!data.ok)throw new Error(data.error||'사용량을 가져오지 못했어요.');
      const windows=[data.primary,data.secondary].filter(validWindow);
      const main=windows.find(item=>item.window_minutes===10080)||windows[0];
      if(main){
        const remaining=percent(main.used_percent);
        root.dataset.state=remaining<=10?'low':remaining<=25?'warning':'ready';
        gauge.style.setProperty('--remaining',remaining);
        gauge.setAttribute('aria-label',`${labelFor(main.window_minutes)} ${remaining}% 남음`);
        value.innerHTML=`${remaining}<small>%</small>`;
        period.textContent=`${labelFor(main.window_minutes)} 남음`;
        const resetAt=resetLabel(main.resets_at);
        reset.textContent=resetAt?`${resetAt} 초기화`:'초기화 시각 미제공';
        rows.innerHTML=windows.filter(item=>item!==main).map(item=>{
          const left=percent(item.used_percent),label=labelFor(item.window_minutes),resetAt=resetLabel(item.resets_at);
          return `<div class="codex-usage-window"><div class="codex-usage-window-label"><span>${escapeText(label)} 남음</span><b>${left}%</b></div><div class="codex-usage-meter" role="meter" aria-label="${escapeText(label)} 잔여량" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${left}"><span style="width:${left}%"></span></div>${resetAt?`<div class="codex-usage-reset">${escapeText(resetAt)} 초기화</div>`:''}</div>`;
        }).join('');
        status.textContent=`${main.used_percent}% 사용 · 계정 전체 한도`;
      }else{
        unavailable('계정에서 한도 비율을 제공하지 않았어요.');
      }
      const checked=new Date(data.checked_at);
      stamp.textContent=Number.isNaN(checked.getTime())?'1시간마다 자동 갱신':`${checked.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})} 확인 · 1시간마다 갱신`;
    }catch(error){
      unavailable(error.name==='AbortError'?'조회가 지연돼요. 새로고침으로 다시 확인해 주세요.':error.message||'사용량 정보를 불러오지 못했어요.');
      stamp.textContent='조회 실패 · 1시간 뒤 다시 확인';
    }finally{
      window.clearTimeout(timeout);refresh.disabled=false;root.setAttribute('aria-busy','false');
    }
  }
  refresh.addEventListener('click',load);
  const loadIfDue=()=>{if(!document.hidden&&Date.now()-lastLoadAt>=refreshInterval)load()};
  document.addEventListener('visibilitychange',loadIfDue);
  load();
  window.setInterval(loadIfDue,refreshInterval);
})();
