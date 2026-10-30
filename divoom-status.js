/* Mirror the existing visible Office state; no model calls, message text or device key. */
(()=>{
  'use strict';
  if(window.officeDivoom)return;
  const bridge='http://127.0.0.1:18766', observed=new Map();
  // A single browser session owns display observations; reloads keep its identity.
  const freshSource=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
  let sourceId;try{sourceId=sessionStorage.getItem('office-divoom-source-v1');if(!/^[0-9a-f]{32}$/.test(sourceId||'')){sourceId=freshSource();sessionStorage.setItem('office-divoom-source-v1',sourceId);}}catch{sourceId=freshSource();}
  let configuration=null, sending=false, lastPayload='', lastSent=0, retryAt=0, error='';
  window.officeDivoom={status:()=>({revision:'current-work-v3',connected:!!configuration&&!error,error,lastSent})};
  addEventListener('office-project-thread-state',event=>{
    const detail=event.detail;if(!detail?.id)return;
    const previous=observed.get(detail.id),now=Date.now();
    const finished=previous?.status==='진행 중'&&detail.status!=='진행 중'&&detail.completion&&detail.completion!==previous.completion;
    observed.set(detail.id,{...detail,at:now,completionUntil:detail.status==='진행 중'?0:finished?now+15000:previous?.completionUntil||0});
  });
  async function exchange(path,options={}){
    const result=await fetch(bridge+path,{...options,cache:'no-store',signal:AbortSignal.timeout(3500)});
    if(!result.ok)throw Error('디붐 연결 '+result.status);
    return result.json();
  }
  function actualState(row){
    // The Hand HQ mirrors its visible normalized supervisor state.
    const record=observed.get(row.thread);
    if(!record||Date.now()-record.at>45000||typeof record.status!=='string')return 'unknown';
    if(row.asset==='hand'){
      const visible=document.getElementById('workHandCompanion')?.dataset.frameState;
      if(['idle','working','completed','attention','unknown'].includes(visible))return visible;
    }
    if(record.status==='진행 중')return 'working';
    if(record.status==='확인 필요'||record.status.includes('승인')||record.status.includes('입력'))return 'attention';
    // Initial history is a baseline. Only an observed live finish gets one loop.
    if(record.completionUntil>Date.now())return 'completed';
    return 'idle';
  }
  async function sync(){
    if(sending||Date.now()<retryAt)return;
    sending=true;
    try{
      if(!configuration)configuration=await exchange('/config');
      const payload=JSON.stringify({source_id:sourceId,activity_version:2,states:configuration.screens.map(row=>({thread:row.thread,state:actualState(row)}))});
      if(payload===lastPayload&&Date.now()-lastSent<15000)return;
      const receipt=await exchange('/state',{method:'POST',headers:{'Content-Type':'application/json','X-Office-Divoom':configuration.nonce},body:payload});
      lastPayload=payload;lastSent=Date.now();error=receipt.accepted?'':receipt.reason==='another-source-active'?'다른 오피스 화면에서 상태를 전달 중이에요.':'오피스 화면을 새로고침해 주세요.';
    }catch(e){error=e.message;configuration=null;retryAt=Date.now()+10000;}
    finally{sending=false;}
  }
  addEventListener('office-project-thread-state',()=>setTimeout(sync,50));
  addEventListener('office-project-completion-seen',()=>setTimeout(sync,50));
  // Reuse native state observations. The interval only samples DOM and sends changed snapshots.
  setInterval(sync,1000);sync();
})();

/* User-operated selector. Native previews are on the real device, never fabricated images. */
(()=>{
 'use strict';
 if(window.officeDivoomPicker)return;
 const bridge='http://127.0.0.1:18766';let auth=null,state=null,screen=4,category='Nature&Weather',page=1,kind='native',rows=[],busy=false,wantClose=false,epoch=0;
 let dialog,grid,notice,save,undo,search,more,poll,entrySummary;
 async function request(path,body){
  const options={cache:'no-store',signal:AbortSignal.timeout(12000)};
  if(body){if(!auth)auth=await request('/config');Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Office-Divoom':auth.nonce},body:JSON.stringify(body)});}
  const response=await fetch(bridge+path,options);const data=await response.json();
  if(!response.ok){if(response.status===403)auth=null;throw Error(data.error||'디붐 연결을 확인해 주세요.');}return data;
 }
 function message(text,error=false){notice.textContent=text;notice.dataset.error=String(error);}
 function status(value){
  state=value;
  for(const b of dialog.querySelectorAll('[data-screen]')){
   const n=Number(b.dataset.screen),row=state.screens.find(x=>x.screen===n);b.classList.toggle('selected',screen===n);b.setAttribute('aria-pressed',String(screen===n));
   b.querySelector('small').textContent=row?.name||'현재 화면';
  }
  const p=state.preview;save.disabled=busy||!p||p.seconds_left<=0;undo.disabled=busy||!p;
  save.textContent=p?`${p.choice.screen}번에 이 화면 사용`:'이 화면 사용';
  const preview=dialog.querySelector('[data-preview-state]');preview.textContent=p?`${p.choice.screen}번에서 ${p.choice.name} 미리보기 · ${p.seconds_left}초 후 원래대로`:'선택한 화면은 먼저 디붐에서 미리 볼 수 있어요.';
  entrySummary.textContent=state.screens.map(x=>`${x.screen}번 ${x.name}`).join(' / ');
  if(state.error)message(state.error,true);
 }
 function render(){
  grid.replaceChildren();const needle=search.value.trim().toLowerCase();
  for(const row of rows.filter(x=>x.name.toLowerCase().includes(needle))){
   const card=document.createElement('article');card.className='dv-choice';
   if(row.kind==='gif'){
    const img=document.createElement('img');img.crossOrigin='anonymous';img.src=bridge+row.preview;img.alt=row.name;img.width=128;img.height=128;card.append(img);
   }else{
    const note=document.createElement('div');note.className='dv-native-note';note.textContent='디붐 기기에서\n실제 모습 미리보기';card.append(note);
   }
   const title=document.createElement('strong');title.textContent=row.name;card.append(title);
   const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=`${screen}번에서 미리보기`;button.disabled=busy;
   button.onclick=()=>operate(async()=>{const choice={screen,kind:row.kind};if(row.kind==='native')Object.assign(choice,{clock_id:row.clock_id,category:row.category,page:row.page});else choice.asset=row.asset;
    status(await request('/picker/preview',choice));message('디붐에서 실제 화면을 보고 마음에 들면 아래 ‘이 화면 사용’을 눌러주세요.');});
   card.append(button);grid.append(card);
  }
  if(!grid.childElementCount){const empty=document.createElement('p');empty.textContent='일치하는 화면이 없어요.';grid.append(empty);}
 }
 async function load(append=false){
  const id=++epoch;more.disabled=true;message('화면 목록을 가져오고 있어요…');
  try{
   if(kind==='gif'){rows=state.gifs;more.hidden=true;}
   else{const data=await request('/picker/catalog?category='+encodeURIComponent(category)+'&page='+page);if(id!==epoch)return;rows=append?[...rows,...data.items]:data.items;more.hidden=!data.has_more;}
   render();message(kind==='gif'?'GIF를 보고 원하는 화면에 미리 띄워보세요.':'기능성 화면은 공식 API에 이미지가 없어 디붐 기기에서 직접 미리 봐요.');
  }catch(e){if(id===epoch){if(append&&page>1)page--;message(e.message,true);}}finally{if(id===epoch)more.disabled=false;}
 }
 async function operate(fn){
  if(busy)return;busy=true;render();if(state)status(state);
  try{await fn();}catch(e){message(e.message,true);try{status(await request('/picker/status'));}catch{}}
  finally{busy=false;render();if(state)status(state);if(wantClose){wantClose=false;close();}}
 }
 async function close(){
  if(busy){wantClose=true;message('처리가 끝나면 원래 화면으로 돌리고 닫을게요.');return;}
  if(state?.preview){await operate(async()=>{status(await request('/picker/cancel',{preview_id:state.preview.id}));});if(state?.preview)entrySummary.textContent='원래 화면 복원을 기다리고 있어요. 연결되면 자동으로 다시 시도해요.';}
  dialog.close();clearInterval(poll);
 }
 async function open(){
  if(!dialog)mount();if(dialog.open)return;dialog.showModal();message('디붐 연결을 확인하고 있어요…');
  try{auth=await request('/config');status(await request('/picker/status'));page=1;await load();}
  catch(e){message(e.message,true);}
  if(!dialog.open)return;clearInterval(poll);poll=setInterval(async()=>{if(!dialog.open||busy)return;try{status(await request('/picker/status'));}catch(e){message(e.message,true);}},1000);
 }
 function mount(){
  const settings=document.getElementById('aemeath-settings');if(!settings||document.getElementById('divoomPickerEntry'))return;
  const style=document.createElement('style');style.textContent=`
  #divoomPickerEntry{display:flex;gap:16px;align-items:center;justify-content:space-between}#divoomPickerEntry h2{margin:0 0 6px}#divoomPickerEntry p{margin:0;color:var(--ao-muted,var(--ui-muted,#aeb6b0));font-size:13px}
  #divoomPicker{width:min(840px,94vw);max-height:88vh;padding:0;background:var(--ao-panel,var(--ui-panel,#1b2020));color:var(--ao-text,var(--ui-ink,#eaf0e8));border:1px solid var(--ao-border,var(--ui-line,#48554d));border-radius:14px;box-shadow:0 20px 80px #0009}#divoomPicker::backdrop{background:#0009}
  #divoomPicker header,#divoomPicker footer{padding:18px 22px;background:var(--ao-panel,var(--ui-panel,#1b2020));display:flex;align-items:center;justify-content:space-between;gap:12px}#divoomPicker header{border-bottom:1px solid #ffffff20}#divoomPicker h2{margin:0;font-size:21px}#divoomPicker .dv-body{padding:18px 22px}#divoomPicker p{font-size:13px;line-height:1.5}
  #divoomPicker .dv-screens{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px}#divoomPicker .dv-screens button{background:transparent;border:1px solid #758172;color:inherit;border-radius:9px;text-align:left;padding:11px}#divoomPicker .dv-screens small{display:block;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:5px}
  #divoomPicker button.selected{border-color:var(--ao-accent,var(--ui-sulfur,#c8fa46));background:var(--ao-accent-muted,#b3df4820)}#divoomPicker .dv-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}#divoomPicker .dv-tabs button{font-size:12px;padding:7px 10px}#divoomPicker input{box-sizing:border-box;width:100%;margin:0 0 12px}#divoomPicker .dv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(172px,1fr));gap:10px;max-height:38vh;overflow:auto;padding:2px}
  #divoomPicker .dv-choice{display:flex;flex-direction:column;align-items:center;gap:10px;min-width:0;padding:14px 10px;background:#ffffff05;border:1px solid var(--ao-border,#ffffff1d);border-radius:10px}#divoomPicker .dv-choice strong{font-size:13px;text-align:center;overflow-wrap:anywhere}#divoomPicker .dv-choice button{margin-top:auto;font-size:12px}#divoomPicker img{image-rendering:pixelated;background:#090d12;border-radius:3px}#divoomPicker .dv-native-note{height:76px;display:grid;place-items:center;white-space:pre-line;text-align:center;color:var(--ao-muted,var(--ui-muted,#a9b6ad));font-size:12px}
  #divoomPicker [data-notice]{min-height:20px;color:var(--ao-muted,var(--ui-muted,#bdc8c0))}#divoomPicker [data-error=true]{color:#ffb5a8}#divoomPicker footer{position:sticky;bottom:0;border-top:1px solid #ffffff20;flex-wrap:wrap}#divoomPicker [data-preview-state]{flex:1;min-width:200px;font-size:12px}#divoomPicker button:disabled{opacity:.45;cursor:default}#divoomPicker button:focus-visible{outline:2px solid var(--ao-accent,var(--ui-sulfur,#c8fa46));outline-offset:2px}
  @media(max-width:560px){#divoomPicker .dv-body,#divoomPicker header,#divoomPicker footer{padding:14px}#divoomPicker .dv-grid{grid-template-columns:repeat(2,minmax(0,1fr))}#divoomPicker .dv-choice img{width:100%;height:auto;max-width:128px}#divoomPickerEntry{align-items:flex-start;flex-direction:column}}
  `;document.head.append(style);
  const entry=document.createElement('article');entry.className='card';entry.id='divoomPickerEntry';entry.innerHTML='<div><h2>디붐 화면 선택</h2><p>3·4·5번을 직접 보고 골라요. 1·2번 팀 상태는 유지돼요.</p><p data-summary></p></div><button type="button">화면 고르기</button>';settings.prepend(entry);entrySummary=entry.querySelector('[data-summary]');entry.querySelector('button').onclick=open;
  dialog=document.createElement('dialog');dialog.id='divoomPicker';dialog.setAttribute('aria-labelledby','divoomPickerTitle');dialog.innerHTML=`<header><h2 id="divoomPickerTitle">디붐 화면 고르기</h2><button type="button" class="secondary" data-close aria-label="선택창 닫기">닫기 ×</button></header><div class="dv-body"><div class="dv-screens">${[3,4,5].map(n=>`<button type="button" data-screen="${n}">${n}번 화면<small></small></button>`).join('')}</div><div class="dv-tabs" data-categories></div><input type="search" placeholder="이름으로 찾기" aria-label="디붐 화면 이름 검색"><p data-notice role="status"></p><div class="dv-grid"></div><button type="button" class="secondary" data-more hidden>더 보기</button><p>미리보기는 60초 뒤 원래 화면으로 돌아와요. 기기에서 확인한 뒤 저장하면 유지돼요.</p></div><footer><span data-preview-state></span><button type="button" class="secondary" data-undo disabled>원래대로</button><button type="button" data-save disabled>이 화면 사용</button></footer>`;document.body.append(dialog);
  grid=dialog.querySelector('.dv-grid');notice=dialog.querySelector('[data-notice]');save=dialog.querySelector('[data-save]');undo=dialog.querySelector('[data-undo]');search=dialog.querySelector('input');more=dialog.querySelector('[data-more]');
  const tabs=dialog.querySelector('[data-categories]');for(const [id,label] of [['Nature&Weather','시계·날씨'],['Retro','레트로'],['normal','기본 화면'],['Tools','도구'],['saved','원래 순환'],['gif','움직이는 GIF']]){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=label;b.dataset.category=id;b.classList.toggle('selected',id===category);b.onclick=()=>{if(busy)return;kind=id==='gif'?'gif':'native';if(kind==='native')category=id;page=1;rows=[];search.value='';tabs.querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));load();};tabs.append(b);}
  for(const b of dialog.querySelectorAll('[data-screen]'))b.onclick=()=>operate(async()=>{if(state?.preview)status(await request('/picker/cancel',{preview_id:state.preview.id}));screen=Number(b.dataset.screen);if(state)status(state);});
  search.oninput=render;more.onclick=()=>{page++;load(true);};dialog.querySelector('[data-close]').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  undo.onclick=()=>operate(async()=>{status(await request('/picker/cancel',{preview_id:state.preview.id}));message('원래 화면으로 돌렸어요.');});
  save.onclick=()=>operate(async()=>{status(await request('/picker/commit',{preview_id:state.preview.id}));message('선택을 저장했어요. 다른 화면도 이어서 고를 수 있어요.');});
 }
 window.officeDivoomPicker={open};if(window.officeDivoom)window.officeDivoom.openPicker=open;
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
