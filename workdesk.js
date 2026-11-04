/* Page changes are offered once and applied only by an explicit safe user click. */
(() => {
  'use strict';
  const seen=new Set();let pending=null,bar=null,label=null,button=null,applying=false,saveTimer=null,fieldsRestored=false,workRestored=false,nativeRequest=null;
  const storage='office-view-recovery.v1';
  function snapshot(){
    const fields={};
    for(const element of document.querySelectorAll('textarea[id],#workResourcePath')){
      if(element.value&&element.value.length<=12000)fields[element.id]={value:element.value,start:element.selectionStart,end:element.selectionEnd};
    }
    try{sessionStorage.setItem(storage,JSON.stringify({fields,view:document.body.dataset.activeView,work:window.officeWorkSession?.snapshot(),at:Date.now()}));}catch{}
    try{const value=document.getElementById('workCommand')?.value;if(value)sessionStorage.setItem('ai-office-command-draft',value);else sessionStorage.removeItem('ai-office-command-draft');}catch{}
  }
  function reasons(includeDrafts=true){
    const result=[],state=window.officeWorkSession?.state()||{};
    if(state.busy||state.uncertain||state.uploading||state.recordBusy)result.push('처리 중인 작업');
    if(state.attachments||document.querySelector('input[type=file]')?.files?.length)result.push('첨부 자료');
    if([...document.querySelectorAll('dialog[open],[role=dialog]:not(dialog)')].some(e=>!e.hidden&&e.getClientRects().length))result.push('열린 작업창');
    // An embedded messenger is owned by the native shell, outside this DOM.
    // The shell checks its actual native controls again before real recovery.
    if(document.querySelector('#workNotificationsDialog[open]'))result.push('열린 메신저');
    if(includeDrafts&&[...document.querySelectorAll('textarea')].some(e=>!e.disabled&&e.value.trim()))result.push('작성 중인 입력');
    const focused=document.activeElement;
    if(document.hasFocus()&&focused?.matches('input:not([type=button]):not([type=submit]),textarea,[contenteditable=true]'))result.push('입력 중');
    return result;
  }
  function update(){
    if(!bar||!pending)return;
    const busy=reasons();button.disabled=applying||busy.length>0;
    label.textContent=busy.length?'새 버전이 있어요. 입력·첨부·열린 창을 정리한 뒤 적용할 수 있어요.':'새 버전이 준비됐어요. 원하는 때 적용하세요.';
  }
  function offer(key){
    if(!key||seen.has(key))return;seen.add(key);pending=key;
    if(!bar){
      bar=document.createElement('aside');bar.id='officeUpdateNotice';bar.setAttribute('role','status');
      bar.style.cssText='position:fixed;right:20px;top:12px;max-width:490px;display:flex;align-items:center;gap:12px;padding:12px 16px;background:#202824;color:#e6f2e7;border:1px solid #60725c;border-radius:12px;z-index:9000;font-size:13px;box-shadow:0 6px 24px #0005';
      label=document.createElement('span');button=document.createElement('button');button.type='button';button.textContent='새 버전 적용';
      button.style.cssText='flex:none;padding:8px 12px;border-radius:8px;border:1px solid #91a87b;background:#d7e8bc;color:#182014;cursor:pointer';
      button.addEventListener('click',()=>{
        if(applying||reasons().length){update();return;}
        snapshot();applying=true;button.disabled=true;
        // Only this user gesture replaces the page. Feedback and server recovery
        // never call reload; no command/file operation is replayed here.
        if(window.officeNativeMessengerHost&&window.chrome?.webview){
          nativeRequest=crypto.randomUUID().replaceAll('-','');
          window.chrome.webview.postMessage({type:'office-page-update',id:nativeRequest});
          const request=nativeRequest;setTimeout(()=>{if(nativeRequest===request){nativeRequest=null;applying=false;update();label.textContent='앱의 안전한 적용 응답을 기다립니다. 작성 내용은 유지돼요.';}},5000);
        }else apply();
      });bar.append(label,button);document.body.append(bar);
    }
    update();
  }
  function apply(){
    if(reasons().length){applying=false;update();return;}
    snapshot();try{sessionStorage.setItem('office-applied-update',pending);}catch{}
    location.reload();
  }
  function restore(){
    try{
      const saved=JSON.parse(sessionStorage.getItem(storage)||'null');if(!saved||Date.now()-saved.at>86400000)return;
      for(const [id,data] of Object.entries(fieldsRestored?{}:saved.fields||{})){
        const element=document.getElementById(id);
        if(element&&!element.value&&typeof data.value==='string'){element.value=data.value;try{element.setSelectionRange(data.start,data.end);}catch{}}
      }
      fieldsRestored=true;
      if(!workRestored&&window.officeWorkSession?.state().ready){workRestored=true;window.officeWorkSession.restore(saved.work);}
    }catch{}
  }
  window.officeUpdates={offer,snapshot,reasons,restore,prepareRecovery(){snapshot();return !reasons(false).length;}};
  window.chrome?.webview?.addEventListener('message',event=>{
    const data=event.data;if(data?.type!=='office-page-update-result'||data.id!==nativeRequest)return;
    nativeRequest=null;
    if(data.ok)apply();else{applying=false;update();label.textContent=data.error||'열린 메신저·미리보기 창을 정리한 뒤 적용해 주세요.';}
  });
  document.addEventListener('input',()=>{clearTimeout(saveTimer);saveTimer=setTimeout(snapshot,200);update();},true);
  document.addEventListener('focusin',update,true);document.addEventListener('focusout',()=>setTimeout(update,0),true);
  document.addEventListener('close',update,true);document.addEventListener('work-attachments-changed',()=>{snapshot();update();});
  document.addEventListener('click',()=>{if(pending)setTimeout(update,0);},true);
  addEventListener('pagehide',snapshot);addEventListener('office-main-conversation-rendered',()=>{restore();update();});
  document.addEventListener('DOMContentLoaded',restore,{once:true});
})();

/* Personal work surface. All writes use an idempotency key, including explicit retries. */
(() => {
  'use strict';
  let overview = null, selected = null, busy = false, loading = false, uncertain = null;
  // Manual records are short, idempotent requests, not assistant turns.
  let recordBusy = false, recordUncertain = null, recordRevision = 0;
  const recordEndpoints = new Set(['task','task-edit','note','status','resource','open-resource']);
  const recordFields = ['tasks','events','activity_days','day_events','counts'];
  let selectedDay = new Date().toLocaleDateString('en-CA');
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusNames = {todo:'예정', review:'검토중', doing:'진행중', done:'완료'};
  const stamp = value => { if (!value) return ''; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : new Intl.DateTimeFormat('ko-KR',{month:'long',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(d); };
  let followingChat = true, chatScrollQueued = false, chatScrollIntentUntil = 0, chatResizeObserver = null;
  let chatReadingTop=0,chatReadingAnchor=null;
  function rememberChatPosition(){
    const log=$('workMessages');
    if(!log||window.officeMainChatVisible===false||!log.clientHeight)return;
    chatReadingTop=log.scrollTop;
    if(followingChat){chatReadingAnchor=null;return;}
    const top=log.getBoundingClientRect().top;
    const first=[...log.querySelectorAll('[data-work-message-id]')].find(row=>row.getBoundingClientRect().bottom>top+1);
    chatReadingAnchor=first?{id:first.dataset.workMessageId,offset:first.getBoundingClientRect().top-top}:null;
  }
  function applyChatPosition(){
    const log=$('workMessages');
    if(!log||window.officeMainChatVisible===false||!log.clientHeight)return;
    if(followingChat){log.scrollTop=log.scrollHeight;return;}
    const anchor=chatReadingAnchor&&[...log.querySelectorAll('[data-work-message-id]')].find(row=>row.dataset.workMessageId===chatReadingAnchor.id);
    log.scrollTop=anchor?log.scrollTop+anchor.getBoundingClientRect().top-log.getBoundingClientRect().top-chatReadingAnchor.offset:chatReadingTop;
  }
  window.officeMainChatScrollState=()=>{rememberChatPosition();return {top:chatReadingTop,following:followingChat};};
  window.officeMainChatScrollTo=position=>{
    followingChat=position===null;
    if(!followingChat&&Number.isFinite(position)){
      if(Math.abs(position-chatReadingTop)>1)chatReadingAnchor=null;
      chatReadingTop=Math.max(0,position);
    }
    // A view restoration is not human scrolling. Queued frames read the
    // latest state instead of replaying an obsolete numeric position.
    chatScrollIntentUntil=0;applyChatPosition();
    requestAnimationFrame(applyChatPosition);
  };
  function settleChatScroll(force = false) {
    if(window.officeMainChatVisible===false)return;
    if (force) followingChat = true;
    if (chatScrollQueued) return;
    chatScrollQueued = true;
    const frame = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
    // Notices, context and the unlocked composer can change the log height after
    // render(). Wait for that layout, then check once more on the next frame.
    frame(() => {
      const log = $('workMessages');
      applyChatPosition();
      frame(() => {
        chatScrollQueued = false;
        applyChatPosition();
      });
    });
  }
  function observeChatLayout() {
    const log = $('workMessages');
    if (!log || typeof ResizeObserver !== 'function') return;
    chatResizeObserver ||= new ResizeObserver(() => settleChatScroll());
    chatResizeObserver.disconnect();
    chatResizeObserver.observe(log);
    if (log.lastElementChild) chatResizeObserver.observe(log.lastElementChild);
  }
  let companionState = null, companionOutcome = null, companionError = '';
  let liveActivity = null;
  let desktopActivity=null;
  let activeRequest=null, serverRequest=null, stopRequested=false;
  const controlledRequest=()=>activeRequest || serverRequest;
  const controlPending={}, controlSubmitting={};
  let outcomeVisibleUntil=0, outcomeTimer=null;
  let notificationUnread=0,notificationLatest=null;
  document.addEventListener('office-notifications-state',event=>{
    notificationUnread=Math.max(0,Number(event.detail?.unread)||0);notificationLatest=event.detail?.latest||null;companionUpdate();
  });
  let companionManifest=null, companionVariant=-1, companionRevision=null, companionManifestLoading=false;
  let companionPlaybackTimer=null,companionPlaybackVersion=0,companionObjectURL=null,companionImageEvents=null;
  const companionGifCache=new Map();
  function stopCompanionPlayback(){
    clearTimeout(companionPlaybackTimer);companionPlaybackTimer=null;companionPlaybackVersion++;
    companionImageEvents?.abort();companionImageEvents=null;
    if(companionObjectURL){URL.revokeObjectURL(companionObjectURL);companionObjectURL=null;}
  }
  function gifCycleDuration(bytes){
    if(bytes.length<13||String.fromCharCode(...bytes.subarray(0,3))!=='GIF')return 8000;
    let cursor=13+(bytes[10]&128?3*(1<<((bytes[10]&7)+1)):0),delay=100,total=0;
    const skipBlocks=()=>{while(cursor<bytes.length){const size=bytes[cursor++];if(!size)return;cursor+=size;}};
    while(cursor<bytes.length){
      const block=bytes[cursor++];
      if(block===59)break;
      if(block===33){
        const label=bytes[cursor++];
        if(label===249&&bytes[cursor]===4)delay=Math.max(20,((bytes[cursor+2]|bytes[cursor+3]<<8)*10)||100);
        skipBlocks();
      }else if(block===44){
        const packed=bytes[cursor+8];cursor+=9;
        if(packed&128)cursor+=3*(1<<((packed&7)+1));
        cursor++;skipBlocks();total+=delay;delay=100;
      }else break;
    }
    return total||8000;
  }
  async function workingGif(source){
    if(!companionGifCache.has(source)){
      const pending=fetch(source,{cache:'force-cache',signal:AbortSignal.timeout(10000)}).then(async response=>{
        if(!response.ok)throw Error('Animation unavailable');
        const bytes=new Uint8Array(await response.arrayBuffer());
        return {blob:new Blob([bytes],{type:'image/gif'}),duration:gifCycleDuration(bytes)};
      }).catch(error=>{companionGifCache.delete(source);throw error;});
      companionGifCache.set(source,pending);
    }
    return companionGifCache.get(source);
  }
  function companionVariantUpdate(next,rotate=false) {
    const stage=$('workCompanion'), animation=$('workCompanionAnimation'), still=$('workCompanionStill');
    stopCompanionPlayback();
    const variants=(companionManifest?.states?.[next] || []).filter(item=>attachmentURL(item.url) || /^\/assets\/aemeath\/[a-z]+\.(gif|png)$/.test(item.url || ''));
    if(rotate && variants.length>1)companionVariant=(companionVariant+1)%variants.length;
    else if(!rotate)companionVariant=next==='working'?0:variants.length-1;
    const variant=variants[companionVariant];
    animation.dataset.companionVariant=variant?.id || 'builtin-'+next;
    const source=variant?.url || '/assets/aemeath/'+(next==='alert'?'typing':next)+'.gif';
    const stillSource=attachmentURL(variant?.still_url) || (/^\/assets\/aemeath\/[a-z]+\.png$/.test(variant?.still_url || '') ? variant.still_url : '/assets/aemeath/'+(next==='alert'?'typing':next)+'.png');
    if(animation.getAttribute('src')!==source){stage.classList.remove('is-gif-unavailable');animation.src=source;}
    if(still.getAttribute('src')!==stillSource){stage.classList.remove('is-still-unavailable');still.src=stillSource;}
    if(next!=='working'||variants.length<2||document.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    const version=companionPlaybackVersion;
    const advance=duration=>{
      if(version!==companionPlaybackVersion||companionState!=='working'||document.hidden)return;
      companionPlaybackTimer=setTimeout(()=>{if(version===companionPlaybackVersion&&companionState==='working')companionVariantUpdate('working',true);},duration);
    };
    // A fresh object URL restarts the GIF from its first frame on every turn.
    workingGif(source).then(({blob,duration})=>{
      if(version!==companionPlaybackVersion||companionState!=='working'||document.hidden)return;
      companionObjectURL=URL.createObjectURL(blob);
      companionImageEvents=new AbortController();
      animation.addEventListener('load',()=>{stage.classList.remove('is-gif-unavailable');advance(duration);},{once:true,signal:companionImageEvents.signal});
      animation.addEventListener('error',()=>advance(duration),{once:true,signal:companionImageEvents.signal});
      animation.src=companionObjectURL;
    }).catch(()=>advance(8000));
  }
  function acceptCompanionManifest(manifest) {
    if(!manifest?.states || manifest.revision===companionRevision)return;
    companionManifest=manifest;companionRevision=manifest.revision;
    companionVariantUpdate(companionState || 'idle');
  }
  async function refreshCompanionManifest() {
    if(companionManifestLoading)return;companionManifestLoading=true;
    try{const response=await fetch('/api/work/companion-assets',{cache:'no-store'});const data=await response.json();if(response.ok && !data.error)acceptCompanionManifest(data);}
    catch{/* Built-in animation remains available when the manifest cannot be read. */}
    finally{companionManifestLoading=false;}
  }
  function waitingForSelection() {
    const messages = overview?.messages || [];
    return [...(overview?.launcher_choices || []), ...(overview?.assistant_actions || [])].some(card => card.options?.length && messages.some(m => m.role === 'assistant' && m.id === card.message_id));
  }
  function companionUpdate() {
    const stage=$('workCompanion'), animation=$('workCompanionAnimation'), still=$('workCompanionStill'), status=$('workCompanionStatus');
    if (!stage || !animation || !still || !status) return;
    let next='idle', label='이야기를 기다리고 있어요';
    const desktopFresh=desktopActivity && Date.now()/1000-desktopActivity.observed_at<10;
    const desktopBusy=desktopFresh && ['working','thinking','draft','waiting'].includes(desktopActivity.phase);
    const desktopRequest=(activeRequest?.execution || serverRequest?.execution)?.mode==='desktop';
    if (desktopBusy) {
      next=desktopActivity.phase==='draft'?'answer':desktopActivity.phase==='waiting'?'idle':'working';
      label=stopRequested?'중지 요청을 처리하고 있어요':desktopFresh?desktopActivity.label:'작업 상태를 다시 확인하고 있어요';
    }
    else if (desktopRequest && (busy || serverRequest)) {
      // Transport/HTTP lifetime is not evidence that the agent is thinking.
      // Only a fresh native observation may show active AI work.
      next='idle';
      label=desktopFresh && desktopActivity.phase==='idle' ? '이야기를 기다리고 있어요' : '연결 상태를 확인하고 있어요';
    }
    else if (busy || serverRequest) {
      next=['draft','done'].includes(liveActivity?.phase) ? 'answer' : liveActivity?.phase==='error' ? 'error' : liveActivity?.phase==='waiting' ? 'idle' : 'working';
      label=stopRequested ? '중지 요청을 처리하고 있어요' : liveActivity?.label || '요청을 처리하고 있어요';
    }
    else if (companionError || uncertain) {next='error';label=uncertain ? '결과 확인이 필요해요' : companionError;}
    else if (companionOutcome && Date.now()<outcomeVisibleUntil) {next=companionOutcome.state;label=companionOutcome.label;}
    else if(notificationUnread>0){next='alert';label='새 알림을 확인해 주세요';}
    else if ((window.officeJobs?.runningCount||0)>0) {next='working';label='별도 업무를 진행하고 있어요';}
    else if ($('workCommand')?.value.trim()) {next='typing';label='이야기를 듣고 있어요';}
    else if (waitingForSelection()) {label='선택을 기다리고 있어요';}
    else if (companionOutcome) {next=companionOutcome.state;label=companionOutcome.label;}
    // An incoming notice remains visible even while the work label is busy.
    // Only the presentation switches; running work and its controls continue.
    const activity=next;
    if(notificationUnread>0)next='alert';
    stage.dataset.activity=activity;
    stage.dataset.attention=notificationUnread>0?'notification':(companionError||uncertain||waitingForSelection()?'action':'');
    const alert=$('workCompanionAlert');
    if(alert){
      alert.hidden=notificationUnread===0;
      $('workCompanionAlertTitle').textContent=(notificationLatest?.source||'새 알림')+' · '+notificationUnread+'건';
      $('workCompanionAlertPreview').textContent=[notificationLatest?.title,notificationLatest?.body].filter(Boolean).join(' · ').replace(/\s+/g,' ').slice(0,180)||'도착한 알림을 확인해 주세요';
      alert.title=[notificationLatest?.source,notificationLatest?.title,notificationLatest?.body].filter(Boolean).join('\n');
      alert.onclick=()=>{
        const id=notificationLatest?.id;
        if(id&&typeof window.workNotificationOpen==='function')window.workNotificationOpen(id);
        else if(typeof window.workStageSelect==='function')window.workStageSelect('notifications');
      };
    }
    if (next !== companionState) {
      companionState=next;stage.setAttribute?.('data-state',next);
      stage.classList.toggle('is-gif-unavailable',false);stage.classList.toggle('is-still-unavailable',false);
      companionVariantUpdate(next);
    }
    if (status.textContent !== label) status.textContent=label;
    const connection=$('workConnection');
    if(connection)connection.textContent=(desktopBusy || busy || serverRequest) ? label : companionError || (uncertain ? '결과 확인이 필요해요' : waitingForSelection() ? '선택을 기다리고 있어요' : companionOutcome?.label || (overview ? '업무 기록 연결됨' : '연결 필요'));
    stage.classList.toggle('has-notification',notificationUnread>0);
    const acknowledge=$('workNotificationAcknowledge');
    if(acknowledge)acknowledge.hidden=notificationUnread===0;
  }
  function notice(message = '', error = false) {
    $('workNotice').textContent = message; $('workNotice').hidden = !message;
    $('workNotice').classList.toggle('work-error', error);
    settleChatScroll();
  }
  function locks() {
    const current=controlledRequest();
    const blocked = busy || !!uncertain || !overview?.action_token;
    $('work').querySelectorAll('button,input,textarea,select').forEach(el => {
      if(el.closest('[data-independent-work]'))return;
      if(el.closest('#workOtherRequests')){el.disabled=false;return;}
      if(el.matches('[data-work-action="cancel-task"],[data-work-action="close-detail"]')){el.disabled=false;return;}
      if(el.matches('[data-work-action="retry-record"]')){el.disabled=recordBusy;return;}
      if(el.closest('.work-tasks,#workTaskDialog')){el.disabled=recordBusy || !!recordUncertain || !overview?.action_token;return;}
      if(el.id==='workStop'){el.hidden=!current;el.disabled=!current || stopRequested || !!controlSubmitting.stop;return;}
      if(current && el.id==='workCommand'){el.disabled=false;return;}
      if(current && el.id==='workSend'){el.disabled=stopRequested || !!controlSubmitting.message;return;}
      if (el.closest('#workNotifications,#workNotificationDialog,#workLauncher,#workLauncherDialog,#workCalendar,#workFileDialog,#workStage,#workStageDialog') || ['workNotificationAcknowledge','workCompanionAlert'].includes(el.id) || el.matches('[data-work-preview]') || el.matches('[data-work-action="close-detail"],[data-files-action]')) return;
      if (el.matches('[data-work-action="desk"],[data-work-action="tasks"]')) return;
      if (el.matches('[data-work-action="reload"]')) { el.disabled = loading || recordBusy || !!recordUncertain; return; }
      if (el.matches('[data-work-action="retry"]')) { el.disabled = busy; return; }
      // The main request may lock only controls that belong to this chat.
      // Every other feature owns its readiness and in-flight state itself.
      if(el.closest('#workCommandForm') || el.matches('[data-work-assistant-action],[data-work-launcher-choice]'))el.disabled = blocked;
    });
    const reload=$('workUtilities')?.querySelector('[data-work-action="reload"]');
    if(reload)reload.disabled=loading || recordBusy || !!recordUncertain;
    $('workRetry').hidden = !uncertain;
    $('workRecordRetry').hidden = !recordUncertain;
    if(uploading || attachments.some(item=>!item.id)) $('workSend').disabled=true;
    if($('workAttach')) $('workAttach').disabled=blocked || uploading || attachments.length>=6;
    if($('workAttachmentInput')) $('workAttachmentInput').disabled=blocked || uploading;
    renderAttachments();
    $('workSend').textContent = current ? controlSubmitting.message ? '보내는 중…' : '추가 메시지 보내기' : busy ? '처리 중…' : uploading ? '첨부 중…' : '요청 보내기';
    if($('workStop'))$('workStop').textContent=stopRequested ? '중지 요청됨' : controlSubmitting.stop ? '중지 요청 중…' : '작업 중지';
    companionUpdate();
    settleChatScroll();
  }
  let attachmentDraft='', attachments=[], uploading=false;
  window.officeWorkSession={
    state:()=>({ready:!!overview,busy,uncertain:!!uncertain,recordBusy:recordBusy||!!recordUncertain,uploading,attachments:attachments.length}),
    snapshot:()=>({selectedDay,selected,attachments:attachments.filter(a=>a.id&&!a.uploading&&!a.error),attachmentDraft}),
    restore(value){
      if(!value||busy||uncertain)return;
      if(/^\d{4}-\d{2}-\d{2}$/.test(value.selectedDay||''))selectedDay=value.selectedDay;
      if(value.selected&&overview?.tasks?.some(t=>t.id===value.selected))selected=value.selected;
      if(!attachments.length&&Array.isArray(value.attachments)&&value.attachments.length<=6){
        attachments=value.attachments.filter(a=>a&&/^[a-f0-9]{32}$/.test(a.id||''));
        attachmentDraft=/^[a-f0-9]{32}$/.test(value.attachmentDraft||'')?value.attachmentDraft:'';
        if(attachments.length)renderAttachments();
      }
    }
  };
  const acceptedExtensions=new Set('gif png jpg jpeg webp ico pdf docx txt md csv tsv json py js ts jsx tsx html css xml yaml yml sql log rst toml zip'.split(' '));
  function attachmentURL(value,download=false) {
    try {const url=new URL(value,location.origin);return url.origin===location.origin && url.pathname==='/api/work/attachment' && /^[a-f0-9-]{32,36}$/i.test(url.searchParams.get('id') || '') && (!download || url.searchParams.get('download')==='1') ? url.href : '';}
    catch{return '';}
  }
  function attachmentMarkup(items,pending=false) {
    return items.map(item=>{
      const thumb=item.kind==='image' ? attachmentURL(item.preview_url) : '';
      const href=attachmentURL(item.download_url,true);
      return `<span class="work-attachment-chip">${thumb ? `<img src="${escape(thumb)}" alt="" loading="lazy">` : '<span aria-hidden="true">▤</span>'}<span><b>${escape(item.name)}</b><small>${escape(item.error || (item.uploading ? '업로드 중…' : '첨부됨'))}</small></span>${pending ? `<button type="button" data-attachment-remove="${escape(item.upload_id)}" aria-label="${escape(item.name)} 첨부 취소">×</button>` : href ? `<a href="${escape(href)}" download aria-label="${escape(item.name)} 다운로드">↓</a>` : ''}</span>`;
    }).join('');
  }
  function renderAttachments() {
    const root=$('workAttachments');if(!root)return;
    root.hidden=!attachments.length;root.innerHTML=attachmentMarkup(attachments,true);
    window.officeChatContent?.enhanceAttachments(root);
    root.querySelectorAll('button').forEach(button=>button.disabled=busy || !!uncertain || uploading);
    document.dispatchEvent(new Event('work-attachments-changed'));
    settleChatScroll();
  }
  async function addAttachments(files) {
    if(busy || uncertain || uploading || !overview?.action_token)return;
    const list=Array.from(files || []);if(!list.length)return;
    if(attachments.length+list.length>6){notice('파일은 한 번에 6개까지 첨부할 수 있어요.',true);return;}
    for(const file of list){
      if(!acceptedExtensions.has(file.name.split('.').pop().toLowerCase()) || !file.size || file.size>10*1024*1024){notice('지원하는 파일 형식인지, 파일 크기가 10MB 이하인지 확인해 주세요.',true);return;}
      if(!/\.(gif|png|jpe?g|webp|ico|zip)$/i.test(file.name) && file.size>8*1024*1024){notice('문서·코드는 8MB, 이미지·ZIP은 10MB 이하로 올려줘.',true);return;}
    }
    if(attachments.reduce((sum,item)=>sum+(item.size || 0),0)+list.reduce((sum,file)=>sum+file.size,0)>30*1024*1024){notice('첨부 파일 합계는 30MB까지 가능해요.',true);return;}
    attachmentDraft ||= requestID();uploading=true;locks();notice();
    try {
      for(const file of list){
        const item={upload_id:requestID(),name:file.name,size:file.size,uploading:true};attachments.push(item);renderAttachments();
        try {
          const dataURL=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('파일을 읽지 못했습니다.'));reader.readAsDataURL(file);});
          const response=await fetch('/api/work/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:overview.action_token,request_id:attachmentDraft,upload_id:item.upload_id,name:file.name,data_url:dataURL})});
          const data=await response.json();if(!response.ok || data.error || !data.attachment?.id)throw Error(data.error || '파일 업로드에 실패했습니다.');
          Object.assign(item,data.attachment,{uploading:false});
        }catch(error){item.uploading=false;item.error=error.message;notice(error.message,true);}
        renderAttachments();
      }
    }finally{uploading=false;renderAttachments();locks();}
  }
  const taskHistory = new Map();
  let historySequence = 0, historyNotice = '';
  async function loadHistory(taskID) {
    const sequence = ++historySequence;
    historyNotice = '진행 기록을 불러오는 중…'; renderDetail(); locks();
    try {
      const response = await fetch('/api/work/task-history?id='+encodeURIComponent(taskID), {cache:'no-store'}), data = await response.json();
      if (!response.ok || data.error || data.task_id !== taskID || !Array.isArray(data.events)) throw new Error(data.error || '업무 이력을 불러오지 못했습니다.');
      if (sequence !== historySequence || selected !== taskID || !$('workTaskDialog').open) return;
      taskHistory.set(taskID, data.events); historyNotice = data.truncated ? '최근 1,000개의 진행 기록을 표시합니다.' : '';
    } catch (e) {
      if (sequence !== historySequence || selected !== taskID || !$('workTaskDialog').open) return;
      historyNotice = '전체 이력 연결 실패: '+e.message+' 현재 불러온 기록을 표시합니다.';
    }
    renderDetail(); locks();
  }
  let detailRenderKey=null;
  function renderDetail(preserve = true) {
    const noteDraft = preserve ? $('workNoteText')?.value || '' : '';
    const pathDraft = preserve ? $('workResourcePath')?.value || '' : '';
    const task = overview?.tasks?.find(t => t.id === selected);
    $('workClearContext').hidden = !task;
    $('workContext').textContent = task ? `선택한 업무: ${task.title}` : '새 업무 또는 오늘 전체 업무에 대해 이야기합니다.';
    if (!task) { selected = null; $('workTaskDetailPanel').innerHTML = '<p class="work-empty">업무를 선택하면 진행 상태, 메모, 연결 자료를 볼 수 있습니다.</p>'; return; }
    const events = (taskHistory.get(task.id) || overview.events || []).filter(e => e.task_id === task.id).slice().sort((a,b) => String(a.created).localeCompare(String(b.created)));
    const nextDetailKey=JSON.stringify([task,events,historyNotice,noteDraft,pathDraft]);
    const panel=$('workTaskDetailPanel');
    if(preserve&&panel.dataset.taskId===task.id&&(detailRenderKey===nextDetailKey||panel.contains(document.activeElement)))return;
    detailRenderKey=nextDetailKey;panel.dataset.taskId=task.id;
    const eventNames = {created:'업무 등록',edited:'업무 수정', status:'상태 변경', note:'진행 메모', resource:'자료 연결'};
    const timeline = events.map(e => `<li><time>${escape(stamp(e.created))}</time><strong>${escape(eventNames[e.kind] || e.kind || '진행 기록')}</strong><p>${escape(e.kind === 'status' ? (statusNames[e.text] || e.text) : e.text)}</p></li>`).join('');
    $('workTaskDetailPanel').innerHTML = `<h4>${escape(task.title)}</h4><span class="work-state ${escape(task.status)}">${escape(statusNames[task.status] || task.status)}</span> <span class="work-muted">${task.due_date ? '마감 '+escape(task.due_date) : '마감일 없음'}</span>${task.detail ? `<div class="work-task-description">${escape(task.detail)}</div>` : ''}
      <div class="work-status-actions">${[['todo','예정으로 표시'],['review','검토중으로 표시'],['doing','진행중으로 표시'],['done','완료로 표시']].filter(([s])=>s!==task.status).map(([s,label])=>`<button type="button" class="work-secondary" data-work-status="${s}">${label}</button>`).join('')}</div>
      <h5>진행 기록</h5>${historyNotice ? `<p class="work-history-note" role="status">${escape(historyNotice)}</p>` : ''}<ol class="work-timeline">${timeline || '<li class="work-muted">아직 진행 기록이 없습니다.</li>'}</ol><h5>진행 메모</h5>${(task.notes || []).map(n=>`<div class="work-note">${escape(n.text)}<time>${escape(stamp(n.created))}</time></div>`).join('') || '<p class="work-muted">진행한 내용과 다음 할 일을 남겨두세요.</p>'}
      <form id="workNoteForm"><label class="work-sr-only" for="workNoteText">진행 메모</label><textarea id="workNoteText" rows="2" maxlength="6000" required placeholder="진행 내용 기록">${escape(noteDraft)}</textarea><div class="work-form-actions"><button type="submit" class="work-secondary">메모 남기기</button></div></form>
      <h5>연결 자료</h5>${(task.resources || []).map(r=>`<div class="work-resource">${escape(r.name || r.path)}<span class="work-resource-path">${escape(r.path)}${r.exists === false ? ' (경로를 찾을 수 없음)' : ''}</span>${r.exists === false ? '' : `<button type="button" class="work-quiet" data-work-preview="${escape(r.id)}">미리보기</button><button type="button" class="work-quiet" data-work-resource="${escape(r.id)}">열기</button>`}</div>`).join('')}
      <form id="workResourceForm"><label for="workResourcePath">파일 또는 폴더 경로</label><div class="work-resource-entry"><input id="workResourcePath" maxlength="4096" required value="${escape(pathDraft)}" placeholder="경로 직접 입력"><button type="button" class="work-quiet" data-work-action="pick-resource">폴더 선택</button></div><div class="work-form-actions"><button type="submit" class="work-secondary">자료 연결</button></div></form><p class="work-muted">원본 위치를 업무에 연결합니다.</p>`;
  }
  document.addEventListener('focusout',event=>{if(event.target.closest('#workTaskDetailPanel'))setTimeout(()=>{if(overview)renderDetail();},0);});
  // Read-only conversation projection. A displayed native reply is never an execution receipt.
  const nativeConversation = new Map();
  const messageText = m => String(m.content ?? m.text ?? '').replace(/^\[AI OFFICE 요청 [0-9a-f]{32}\]\s*/, '').trim();
  const messageSignature = m => m.role+'\0'+messageText(m).replace(/\s+/g,' ');
  function mainConversationMessages(){
    const base=(overview?.messages||[]).map(m=>({...m}));
    const native=[...nativeConversation.values()].sort((a,b)=>(a.turn_started_at||0)-(b.turn_started_at||0)||(a.position||0)-(b.position||0));
    const used=new Set(),anchors=new Map();
    for(let i=0;i<native.length;i++){
      const m=native[i],signature=messageSignature(m);
      let at=-1,distance=Infinity;
      base.forEach((b,index)=>{if(used.has(index)||messageSignature(b)!==signature)return;
        const delta=Math.abs((Date.parse(b.created)||0)-(Number(m.turn_started_at)||0)*1000);
        if(delta<=distance){distance=delta;at=index;}});
      if(at>=0){used.add(at);anchors.set(i,at);Object.assign(base[at],{native_id:m.id,native_turn:m.turn_id,phase:m.phase,parts:m.parts||base[at].parts});}
    }
    const result=base.map((m,index)=>({...m,_order:index}));
    for(let i=0;i<native.length;i++){
      if(anchors.has(i))continue;
      const m=native[i];
      let nextAnchor=-1;
      for(let j=i+1;j<native.length;j++)if(anchors.has(j)){nextAnchor=j;break;}
      let boundary=base.length;
      if(nextAnchor>=0)boundary=anchors.get(nextAnchor);
      else{
        const nextTurn=native.find(x=>(x.turn_started_at||0)>(m.turn_started_at||0));
        if(nextTurn){const nextTime=Number(nextTurn.turn_started_at)*1000-1000;const index=base.findIndex(x=>(Date.parse(x.created)||0)>=nextTime);if(index>=0)boundary=index;}
      }
      result.push({...m,id:'native:'+m.turn_id+':'+m.id,native_id:m.id,native_turn:m.turn_id,content:m.text,
        _order:boundary-.5+(i/(native.length+1))*.4});
    }
    // Internal delivery rejection remains in Workspace, not as a substitute for the actual reply.
    const hasNativeFinal=native.some(m=>m.role==='assistant'&&['final','final_answer'].includes(m.phase));
    return result.filter(m=>!(hasNativeFinal&&!m.native_id&&m.role==='assistant'&&/^현재 요청 표식이 없는 답변을 분리했습니다\./.test(messageText(m))))
      .sort((a,b)=>a._order-b._order);
  }
  window.officeMainNativeConversation={
    update(data){
      let changed=false;
      for(const raw of data.messages||[]){
        if(!raw.id||!['user','assistant'].includes(raw.role)||String(raw.id).startsWith('local:'))continue;
        let text=String(raw.text||'');
        if(raw.role==='user'){
          if(/^# AGENTS\.md instructions|^<environment_context>|^<heartbeat>/.test(text))continue;
          const framed=text.match(/\[현재 사용자 요청 시작\]\s*([\s\S]*?)\s*\[현재 사용자 요청 끝\]/);
          if(framed)text=framed[1];
          if(text.startsWith('<send_user_message_question_reply>')){
            try{text=JSON.parse(text.replace(/^<send_user_message_question_reply>\s*|\s*<\/send_user_message_question_reply>$/g,'')).map(x=>x.answer).join('\n');}catch(_){continue;}
          }
        }
        if(!text.trim())continue;
        const m={...raw,text,...(text!==raw.text?{parts:null}:{})},key=m.turn_id+':'+m.id;
        const old=nativeConversation.get(key);
        if(!old||JSON.stringify(old)!==JSON.stringify(m)){nativeConversation.set(key,m);changed=true;}
      }
      if(nativeConversation.size>240){const sorted=[...nativeConversation.entries()].sort((a,b)=>(a[1].turn_started_at||0)-(b[1].turn_started_at||0)||(a[1].position||0)-(b[1].position||0));for(const [key] of sorted.slice(0,sorted.length-240))nativeConversation.delete(key);}
      if(changed&&overview)renderConversation();
    },
    messages(){return [...nativeConversation.values()];}
  };

  let conversationRenderKey=null;
  function renderConversation(){
    if(!overview)return;
    const messages = mainConversationMessages();
    const log = $('workMessages');
    const nextKey=JSON.stringify([messages,overview.launcher_choices||[],overview.assistant_actions||[],busy,!!uncertain]);
    if(conversationRenderKey===nextKey)return;
    const known=new Set(messages.map(m=>String(m.id)));
    const transient=busy?[...log.querySelectorAll('[data-work-message-id]')].filter(row=>!row.dataset.nativeMessageId&&!known.has(row.dataset.workMessageId)&&!messages.some(m=>m.role===(row.classList.contains('work-user')?'user':'assistant')&&messageText(m)===(row.querySelector('[data-work-message-text]')?.textContent||'').trim())):[];
    rememberChatPosition();
    const choices = overview.launcher_choices || [], actions = overview.assistant_actions || [];
    log.innerHTML = messages.length ? messages.map(m=>{
      const options = m.role === 'assistant' ? choices.filter(c=>c.message_id === m.id).map(c=>`<div class="work-launcher-choice" role="group" aria-label="${escape(c.name)} 프로그램 선택"><span>${escape(c.name)} · 사용할 프로그램을 골라주세요.</span>${(c.options || []).map(option=>`<button type="button" class="work-secondary" data-work-launcher-choice="${escape(c.id)}" data-work-launcher-target="${escape(option.target)}"><strong>${escape(option.title)}</strong>${option.detail ? `<small>${escape(option.detail)}</small>` : ''}</button>`).join('')}</div>`).join('') : '';
      const pending = m.role === 'assistant' ? actions.filter(a=>a.message_id === m.id).map(a=>`<div class="work-assistant-action${a.kind === 'approval' ? ' work-action-approval' : ''}" role="group" aria-label="${escape(a.title)}"><b>${escape(a.title)}</b>${a.description ? `<p>${escape(a.description)}</p>` : ''}<div>${(a.options || []).map(option=>`<button type="button" class="work-secondary" data-work-assistant-action="${escape(a.id)}" data-work-assistant-option="${escape(option.id)}">${escape(option.label)}${option.description ? `<small>${escape(option.description)}</small>` : ''}</button>`).join('')}</div></div>`).join('') : '';
      return `<div data-work-message-id="${escape(m.id)}" class="work-message ${m.role === 'user' ? 'work-user' : ''}"><strong>${m.role === 'user' ? '태준' : 'Aemeath'}</strong><span data-work-message-text>${escape(m.content)}</span>${m.attachments?.length ? `<div class="work-message-attachments">${attachmentMarkup(m.attachments)}</div>` : ''}${options}${pending}</div>`;
    }).join('') : '<p class="work-empty">무슨 일을 해야 하나요?<br>업무를 등록하거나 바로가기에 추가할 프로그램을 알려주세요.</p>';
    for(const message of messages){const row=[...log.querySelectorAll('[data-work-message-id]')].find(n=>n.dataset.workMessageId===String(message.id));if(row)window.officeChatContent?.render(row.querySelector('[data-work-message-text]'),message);}
    window.officeChatContent?.enhanceAttachments(log);
    for(const row of transient)log.append(row);
    for(const m of messages){if(!m.native_id)continue;const row=[...log.querySelectorAll('[data-work-message-id]')].find(n=>n.dataset.workMessageId===String(m.id));if(row){row.dataset.nativeMessageId=m.native_id;row.dataset.messageId=m.native_id;}}
    applyChatPosition();
    conversationRenderKey=nextKey;
    dispatchEvent(new Event('office-main-conversation-rendered'));
  }
  function render(recordsOnly = busy && !!activeRequest) {
    // The day list is based on the task's creation date only. Historical
    // activity belongs in the task detail/history, not in another day's list.
    const tasks = (overview.tasks || []).filter(t => (t.created || '').slice(0,10) === selectedDay), counts = {todo:0,review:0,doing:0,done:0};
    $('workCalendarGrid')?.querySelectorAll('[data-calendar-day]').forEach(button=>button.classList.toggle('has-work',(overview.activity_days || []).includes(button.dataset.calendarDay)));
    tasks.forEach(t => { if (counts[t.status] !== undefined) counts[t.status]++; });
    const selectedDate = new Date(selectedDay+'T00:00:00');
    $('workTasksTitle').textContent = `${selectedDate.getMonth()+1}월 ${selectedDate.getDate()}일 업무`;
    $('workCounts').textContent = `예정 ${counts.todo ?? 0} · 검토중 ${counts.review ?? 0} · 진행중 ${counts.doing ?? 0} · 완료 ${counts.done ?? 0}`;
    $('workTaskList').innerHTML = tasks.length ? tasks.map(t=>`<button type="button" class="work-task-row" data-work-task="${escape(t.id)}" aria-pressed="${t.id === selected}"><span class="work-task-copy"><span class="work-task-name">${escape(t.title)}</span></span><span class="work-state ${escape(t.status)}">${escape(statusNames[t.status] || t.status)}</span></button>`).join('') : '<div class="work-empty work-task-empty"><span class="work-empty-mark" aria-hidden="true">＋</span><div><strong>이 날짜에 등록되거나 진행한 업무가 없어요</strong><p>캘린더 날짜를 바꾸거나 업무를 추가해 보세요.</p></div></div>';
    if (!recordsOnly) renderConversation();
    renderDetail();
    locks();
    observeChatLayout();
    settleChatScroll();
  }
  document.addEventListener('work-stage-verification',()=>{if(!busy&&!uncertain)window.workLoad?.();});
  window.workSelectDay = function workSelectDay(day){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day||''))return;
    selectedDay=day;selected=null;render();
  };
  window.workLoad = async function workLoad() {
    if (!$('work') || loading || recordBusy || recordUncertain) return;
    // Refresh the records without replacing the live conversation/draft.
    if(busy || uncertain)return refreshRecords();
    const initialRecordRevision=recordRevision;
    loading = true; companionOutcome=null; companionError=''; companionUpdate();
    try {
      const response = await fetch('/api/work', {cache:'no-store'}), data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || '업무를 불러오지 못했습니다.');
      const initialLoad = !overview;
      if(overview && recordRevision!==initialRecordRevision){
        for(const field of recordFields)data[field]=overview[field];
      }
      overview = data;if(initialLoad)window.officeUpdates?.restore();if(data.companion_assets)acceptCompanionManifest(data.companion_assets);taskHistory.clear();notice();render();
      if(initialLoad){try{const draft=sessionStorage.getItem('ai-office-command-draft');if(draft && !$('workCommand').value)$('workCommand').value=draft;sessionStorage.removeItem('ai-office-command-draft');}catch(_){}window.officeUpdates?.restore();}
      settleChatScroll(initialLoad);
      if ($('workTaskDialog').open && selected) loadHistory(selected);
    } catch (e) {
      companionError='연결을 확인해 주세요';
      notice(`업무 연결 실패: ${e.message} 화면을 다시 열어 재연결하세요.`, true);
      if (!overview) {
        $('workMessages').innerHTML = '<p class="work-empty">업무 기록에 연결하지 못했습니다.</p>';
        $('workTaskList').innerHTML = '<p class="work-empty">연결 후 업무를 확인할 수 있습니다.</p>';

      }
      locks();
    } finally { loading = false; locks(); }
  };
  let mirrorRevision=null;
  async function refreshExternalChat(){
    try{
      if(document.hidden)return;
      const response=await fetch('/api/work/chat-updates',{cache:'no-store'});
      if(!response.ok)return;
      const data=await response.json();
      desktopActivity=data.enabled ? data.activity || null : null;
      document.dispatchEvent(new CustomEvent('office-desktop-activity',{detail:desktopActivity}));
      companionUpdate();
      if(!data.enabled || data.revision===mirrorRevision || loading || recordBusy)return;
      if(!busy && !uncertain){
        await window.workLoad();
      }else{
        // A running POST must not hide direct desktop replies or detach its live draft.
        const response=await fetch('/api/work',{cache:'no-store'}), latest=await response.json();
        if(!response.ok || latest.error)return;
        const log=$('workMessages');
        const panelUsers=new Set(data.user_message_ids||[]);
        for(const message of latest.messages || []){
          if(message.role!=='assistant'&&!panelUsers.has(message.id))continue;
          let row=[...log.querySelectorAll('[data-work-message-id]')].find(node=>node.dataset.workMessageId===message.id);
          if(!row){
            row=document.createElement('div');row.className='work-message'+(message.role==='user'?' work-user':'');row.dataset.workMessageId=message.id;
            const label=document.createElement('strong');label.textContent=message.role==='user'?'태준':'Aemeath';
            const content=document.createElement('span');content.dataset.workMessageText='';row.append(label,content);log.append(row);
          }
          // Preserve actions/attachments on pre-existing message cards.
          const content=row.querySelector('[data-work-message-text]');if(content){if(window.officeChatContent)window.officeChatContent.render(content,message);else content.textContent=message.content;}
          if(message.attachments?.length&&!row.querySelector('.work-message-attachments')){
            const files=document.createElement('div');files.className='work-message-attachments';files.innerHTML=attachmentMarkup(message.attachments);row.append(files);
            window.officeChatContent?.enhanceAttachments(files);
          }
        }
        observeChatLayout();settleChatScroll();
      }
      if(nativeConversation.size)renderConversation();
      mirrorRevision=data.revision;
    }catch(_){/* Preserve the current conversation and unsent composer. */}
    finally{setTimeout(refreshExternalChat,1000);}
  }
  document.addEventListener('work-jobs-state',event=>{
    const rows=event.detail?.items || [];
    const previous=serverRequest?.body.request_id;
    const running=rows.find(row=>row.channel==='office' && row.status==='running');
    const activeRow=activeRequest && rows.find(row=>row.id===activeRequest.body.request_id);
    if(activeRow?.execution)activeRequest.execution=activeRow.execution;
    serverRequest=running ? {body:{request_id:running.id},execution:running.execution} : null;
    if(overview && event.detail?.token)overview.action_token=event.detail.token;
    // A reload/other tab can own the POST. Restore controls from actual server state.
    if(previous!==serverRequest?.body.request_id){stopRequested=false;delete controlPending.message;delete controlPending.stop;}
    const done=activeRequest && rows.find(row=>row.id===activeRequest.body.request_id && ['done','error','cancelled','waiting'].includes(row.status));
    if(!busy && uncertain && done){uncertain=null;activeRequest=null;stopRequested=false;notice();window.workLoad();}
    else if(!busy && !uncertain && previous && !serverRequest){notice();window.workLoad();}
    locks();
  });
  function requestID() { return Array.from(crypto.getRandomValues(new Uint8Array(16)), b=>b.toString(16).padStart(2,'0')).join(''); }
  async function sendControl(kind,externalMessage) {
    const external=typeof externalMessage==='string';
    if(!controlledRequest() || controlSubmitting[kind] || (kind==='message' && stopRequested)){
      if(external)throw Error('추가 지시를 전달하는 중이거나 작업이 종료됐어요. 현재 상태를 확인한 뒤 다시 눌러 주세요.');
      return;
    }
    const target=controlledRequest().body.request_id;
    const text=kind==='message' ? (external?externalMessage.trim():$('workCommand').value.trim()) : '';
    if(external && controlPending[kind]?.request_id===target && controlPending[kind].message!==text)throw Error('이전 추가 지시의 전달 상태를 먼저 확인해 주세요.');
    if(kind==='message' && !text && !controlPending[kind])return;
    const payload=(controlPending[kind]?.request_id===target ? controlPending[kind] : null) || {request_id:target,input_id:requestID(),kind,message:text};
    controlPending[kind]=payload;controlSubmitting[kind]=true;locks();
    try {
      const response=await fetch('/api/work/control',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...payload,token:overview.action_token})});
      const data=await response.json();
      if(!response.ok || !data.accepted)throw Error(data.message || data.error || '추가 요청을 전달하지 못했어요.');
      delete controlPending[kind];
      if(kind==='stop')stopRequested=true;
      if(!external && kind==='message' && $('workCommand').value.trim()===payload.message)$('workCommand').value='';
      if(controlledRequest()?.body.request_id===target){
        const row=document.createElement('div');row.className='work-message work-user';row.dataset.workMessageId=payload.input_id;
        const label=document.createElement('strong');label.textContent='태준';
        row.append(label,document.createTextNode(kind==='stop' ? '작업 중지 요청' : payload.message));$('workMessages').append(row);
        notice(kind==='stop' ? '중지를 요청했어요. 실행 중인 동작의 결과를 정리하고 있어요.' : '추가 메시지를 보냈어요. 다음 판단에 반영해요.');
        settleChatScroll(true);
      } else if(!busy)window.workLoad();
      return data;
    } catch(error){notice(error.message,true);if(external)throw error;}
    finally{controlSubmitting[kind]=false;locks();}
  }
  function liveReply(request) {
    const enabled=['command','assistant-action'].includes(request.endpoint);
    let stopped=false, timer=null, controller=null, revision=-1;
    const log=$('workMessages');
    const user=document.createElement('div'), draft=document.createElement('div');
    if(enabled && request.endpoint==='command' && request.body.message){
      user.className='work-message work-user';
      const label=document.createElement('strong');label.textContent='태준';
      user.append(label,document.createTextNode(request.body.message));log.append(user);
    }
    draft.className='work-message work-live-reply';draft.hidden=true;
    const label=document.createElement('strong'), text=document.createElement('span');
    label.textContent='Aemeath · 작성 중';draft.append(label,text);
    if(enabled)log.append(draft);
    async function poll(){
      if(stopped || !enabled)return;
      controller=new AbortController();
      const deadline=setTimeout(()=>controller?.abort(),4000);
      try{
        const response=await fetch('/api/work/stream',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({token:request.body.token,request_id:request.body.request_id}),signal:controller.signal,cache:'no-store'});
        if(response.status===403){stopped=true;return;}
        const data=await response.json();
        if(stopped || data.request_id!==request.body.request_id)return;
        if(data.revision!==revision){
          revision=data.revision;text.textContent=data.text || '';draft.hidden=!data.text;
          label.textContent=data.phase==='draft' ? 'Aemeath · 답변 작성 중' : 'Aemeath · 진행 상황';
          liveActivity={phase:data.phase,label:data.label || (data.text ? '답변을 작성하고 있어요' : '요청 처리 중')};
          companionUpdate();
          settleChatScroll();
        }
        // The original POST owns completion, errors and retry semantics.
        // This view is provisional and cannot trigger or retry an action.
      }catch(_){/* The final POST response remains authoritative. */}
      finally{clearTimeout(deadline);controller=null;if(!stopped)timer=setTimeout(poll,150);}
    }
    if(enabled){timer=setTimeout(poll,30);settleChatScroll(true);}
    return ()=>{stopped=true;clearTimeout(timer);controller?.abort();draft.remove();user.remove();};
  }
  const sourceReloadWatches=new Set();
  function reflectSourceReload(ticket){
    if(!ticket?.id)return;
    if(sourceReloadWatches.has(ticket.id))return;sourceReloadWatches.add(ticket.id);
    document.dispatchEvent(new CustomEvent('work-stage-notice',{detail:{message:ticket.message || '수정한 소스를 반영하고 있어요.'}}));
    if(ticket.mode==='refresh'){window.officeUpdates?.offer('ticket:'+ticket.id);return;}
    let attempts=0;
    async function poll(){
      try{
        const response=await fetch('/api/work/reload-status',{cache:'no-store'});
        const data=await response.json();
        if(data.id===ticket.id){
          if(['active','rolled_back'].includes(data.state)){
            document.dispatchEvent(new CustomEvent('work-stage-notice',{detail:{message:data.state==='active'?'기능이 반영됐어요. 현재 화면과 입력은 유지합니다.':'기존 기능으로 복구됐어요. 현재 화면은 유지합니다.'}}));return;
          }
          if(data.state==='failed'){document.dispatchEvent(new CustomEvent('work-stage-notice',{detail:{message:data.message,error:true}}));return;}
        }
      }catch(_){/* The expected restart briefly closes this connection. */}
      if(++attempts<240)setTimeout(poll,document.hidden?10000:2000);
      else document.dispatchEvent(new CustomEvent('work-stage-notice',{detail:{message:'소스는 저장됐어요. 진행 중인 작업이 끝나면 자동 반영돼요.'}}));
    }
    setTimeout(poll,600);
  }
  document.addEventListener('work-independent-activity',()=>companionUpdate());
  document.addEventListener('work-independent-result',event=>{
    const result=event.detail?.result;if(result?.source_reload)reflectSourceReload(result.source_reload);
    if(!busy&&!uncertain)window.workLoad?.();
  });
  function recordNotice(message='', error=false) {
    const node=$('workRecordNotice');node.textContent=message;node.hidden=!message;
    node.classList.toggle('work-error',error);
  }
  function acceptRecords(data) {
    for(const field of recordFields)if(field in data)overview[field]=data[field];
  }
  async function refreshRecords() {
    const revision=recordRevision;
    try {
      const response=await fetch('/api/work',{cache:'no-store'}), data=await response.json();
      if(!response.ok || data.error)throw Error(data.error || '업무 목록을 불러오지 못했습니다.');
      if(recordBusy || recordUncertain || revision!==recordRevision)return;
      acceptRecords(data);taskHistory.clear();render(true);
    }catch(error){recordNotice('업무 목록 새로고침 실패: '+error.message,true);}
  }
  async function writeRecord(endpoint,payload,cleanup,existing) {
    if(recordBusy || (!existing && recordUncertain) || !overview?.action_token)return;
    const request=existing || {endpoint,body:{...payload,token:overview.action_token,request_id:requestID()},cleanup};
    const opening=request.endpoint==='open-resource';
    recordBusy=true;recordRevision++;recordNotice(opening?'등록한 자료를 열고 있어요…':'업무 기록을 저장하고 있어요…');locks();
    let confirmed=false;
    try {
      if(existing){
        const response=await fetch('/api/work',{cache:'no-store'}), session=await response.json();
        if(!response.ok || !session.action_token)throw Error('재확인에 필요한 연결 정보를 가져오지 못했습니다.');
        request.body.token=session.action_token;overview.action_token=session.action_token;
      }
      const response=await fetch('/api/work/'+request.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body)});
      const data=await response.json();
      const unresolved=[403,409,202].includes(response.status) || /처리\s*중|processing|in[_ -]?progress/i.test([data.error,data.code,data.status].filter(Boolean).join(' '));
      confirmed=!unresolved;
      if(unresolved || !response.ok || data.error)throw Error(data.error || '저장 결과를 확인하지 못했습니다.');
      recordUncertain=null;acceptRecords(data);taskHistory.clear();
      request.cleanup?.();render(true);recordNotice(data.result?.message || (opening?'등록한 자료 열기를 요청했어요.':'업무 기록을 저장했습니다.'));
      if($('workTaskDialog').open && selected)loadHistory(selected);
    }catch(error){
      recordUncertain=confirmed ? null : request;
      recordNotice((opening?(confirmed?'자료 열기 실패: ':'자료 열기 결과 확인 필요: '):(confirmed?'저장 실패: ':'저장 결과 확인 필요: '))+error.message,true);
      if(['task','task-edit'].includes(request.endpoint) && $('workTaskCreateDialog').open){$('workTaskFormNotice').textContent=error.message;$('workTaskFormNotice').hidden=false;}
    }finally{recordBusy=false;locks();if(!recordUncertain)refreshRecords();}
  }
  async function write(endpoint, payload, cleanup, existing) {
    if(recordEndpoints.has(endpoint))return writeRecord(endpoint,payload,cleanup,existing);
    if (busy || (!existing && uncertain) || !overview?.action_token) return;
    const initialRecordRevision=recordRevision;
    const request = existing || {endpoint, body:{...payload, ...(['command','assistant-action'].includes(endpoint)&&window.officeUI?{client:window.officeUI.capture()}:{}), token:overview.action_token, request_id:payload.request_id || requestID()}, cleanup};
    companionError=''; companionOutcome=null; liveActivity=null; clearTimeout(outcomeTimer); busy = true; notice(); locks();
    let confirmedResponse = false;
    const stageRequest=['command','assistant-action','choose-launcher'].includes(request.endpoint);
    if(['command','assistant-action'].includes(request.endpoint)){activeRequest=request;stopRequested=false;locks();}
    const stageEvent=(phase,result)=>{if(stageRequest)document.dispatchEvent(new CustomEvent('work-stage-request',{detail:{requestId:request.body.request_id,message:request.body.message || '선택한 요청 처리',phase,result}}));};
    stageEvent('start');
    let stopLive=()=>{};
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
      stopLive=liveReply(request);
      const response = await fetch('/api/work/'+request.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body)});
      const data = await response.json();
      stopLive();
      // Authentication rejection and an in-flight request do not establish
      // whether the original attempt committed. Keep the same retry request.
      const unresolved = response.status === 403 || response.status === 409 || response.status === 202 ||
        /처리\s*중|processing|in[_ -]?progress/i.test([data.error, data.code, data.status].filter(Boolean).join(' '));
      confirmedResponse = !unresolved;
      if (unresolved) throw new Error(data.error || '요청을 처리 중입니다. 잠시 후 같은 요청으로 다시 확인하세요.');
      if (!response.ok || data.error) throw new Error(data.error || '요청을 처리하지 못했습니다.');
      // A delayed assistant snapshot must not replace records saved meanwhile.
      const currentRecords=Object.fromEntries(recordFields.map(field=>[field,overview[field]]));
      uncertain = null; overview = data;
      if(recordRevision!==initialRecordRevision)acceptRecords(currentRecords);
      stageEvent(data.result?.execution_state==='error'?'error':'done',data.result);
      companionOutcome=request.endpoint === 'command' ? {state:'answer',label:'답변이 도착했어요'} : {state:'agree',label:['task','task-edit','note','status','resource'].includes(request.endpoint) ? '기록을 저장했어요' : '응답을 확인했어요'};
      if(data.result?.execution_state==='waiting')companionOutcome={state:'idle',label:'선택을 기다리고 있어요'};
      if(data.result?.execution_state==='cancelled')companionOutcome={state:'idle',label:'작업을 중지했어요'};
      if(data.result?.execution_state==='error'){
        const capacity=(data.result.tool_results||[]).some(row=>row.data?.model_failure?.code==='server_overloaded');
        companionOutcome=capacity?{state:'idle',label:'모델 혼잡으로 중단됐어요 · 다른 요청은 가능해요'}:null;
        companionError=capacity?'':'요청 결과를 확인해 주세요';
      }
      outcomeVisibleUntil=Date.now()+8000;
      outcomeTimer=setTimeout(companionUpdate,8100);
      if (data.result?.task_id && data.result.task_id !== selected && recordRevision===initialRecordRevision && !$('workTaskCreateDialog').open && !$('workTaskDialog').open) {
        selected = data.result.task_id; renderDetail(false);
      }
      taskHistory.delete(selected); request.cleanup?.(); render(false);
      if (stageRequest) settleChatScroll(true);
      if ($('workTaskDialog').open && selected) loadHistory(selected);
      // The chat owns the final reply; execution receipts live in WORKSPACE.
      if (stageRequest) notice();
      else notice(data.result?.message || '업무 기록을 저장했습니다.');
      if(data.companion_assets)acceptCompanionManifest(data.companion_assets);
      if(data.result?.companion_changed){companionOutcome=null;companionState=null;await refreshCompanionManifest();}
      if (data.result?.launcher_changed) document.dispatchEvent(new CustomEvent('work-launcher-changed'));
      if (data.result?.desk_changed) document.dispatchEvent(new CustomEvent('work-desk-changed'));
      if (data.result?.source_reload) reflectSourceReload(data.result.source_reload);
    } catch (e) {
      stageEvent(confirmedResponse ? 'error' : 'uncertain',{message:e.message});
      if (!confirmedResponse) {uncertain = request;if(!$('workCommand').value.trim() && request.endpoint==='command')$('workCommand').value=request.body.message || '';}
      else uncertain = null;
      companionOutcome=null;companionError=confirmedResponse ? '요청을 확인해 주세요' : '결과 확인이 필요해요';
      notice(`${confirmedResponse ? '요청 실패' : '처리 결과 확인 실패'}: ${e.message}`, true);
      if(['task','task-edit'].includes(request.endpoint)&&$('workTaskCreateDialog').open){$('workTaskFormNotice').textContent=e.message;$('workTaskFormNotice').hidden=false;}
    } finally { stopLive();liveActivity=null;busy = false;if(!uncertain){activeRequest=null;stopRequested=false;delete controlPending.message;delete controlPending.stop;}locks();if(recordRevision!==initialRecordRevision && !recordBusy && !recordUncertain)refreshRecords(); }
  }
  function openTaskEditor(task=null){
    const form=$('workTaskForm');form.reset();form.dataset.taskId=task?.id||'';
    $('workTaskFormNotice').hidden=true;$('workTaskFormNotice').textContent='';
    $('workTaskCreateTitle').textContent=task?'업무 수정':'업무 추가';
    $('workTaskTitle').value=task?.title||'';$('workTaskDetail').value=task?.detail||'';
    $('workTaskStatus').value=task?.status==='todo'?'review':task?.status||'review';
    form.dataset.day=task ? '' : selectedDay;
    form.querySelector('[type="submit"]').textContent=task?'변경 저장':'업무 저장';
    $('workTaskCreateDialog').showModal();$('workTaskTitle').focus();
  }
  window.workNotificationCommand=async message=>{
    if(!overview?.action_token)throw Error('오피스 연결 상태를 확인해 주세요.');
    if(typeof message!=='string'||!message.trim()||message.length>6000)throw Error('답장 내용을 확인해 주세요.');
    if(controlledRequest()){
      if(message.length>4000)throw Error('추가 지시는 4000자 이내로 전달해 주세요.');
      return sendControl('message',message);
    }
    if(busy||uncertain)throw Error('현재 요청의 처리 상태를 확인하고 있어요. 잠시 뒤 다시 눌러 주세요.');
    if(attachments.length||$('workCommand')?.value.trim())throw Error('작성 중인 채팅을 먼저 보내거나 비워 주세요.');
    return write('command',{message,request_id:requestID()},()=>{});
  };
  function submit(event) {
    const form = event.target;
    if (!['workCommandForm','workTaskForm','workNoteForm','workResourceForm'].includes(form.id)) return;
    event.preventDefault();
    if (form.id==='workCommandForm' && controlledRequest()){sendControl('message');return;}
    if (form.id==='workCommandForm' && (busy || uncertain)) return;
    if (form.id === 'workCommandForm') {
      const message = $('workCommand').value.trim(); if (!message) return;
      if(uploading || attachments.some(item=>!item.id))return;
      write('command',{message,request_id:attachmentDraft || requestID(),attachment_ids:attachments.map(item=>item.id),...(selected ? {task_id:selected} : {})},()=>{});
      $('workCommand').value='';attachments=[];attachmentDraft='';renderAttachments();
    } else if (form.id === 'workTaskForm') {
      const title = $('workTaskTitle').value.trim(); if (!title) return;
      const taskID=form.dataset.taskId;
      write(taskID?'task-edit':'task',{title,detail:$('workTaskDetail').value.trim(),status:$('workTaskStatus').value,...(taskID?{task_id:taskID}:{day:form.dataset.day || selectedDay})},()=>{form.reset();form.dataset.taskId='';$('workTaskCreateDialog').close();});
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
    if(button.id==='workStop'){sendControl('stop');return;}
    if (button.dataset.workAssistantAction) { write('assistant-action',{pending_id:button.dataset.workAssistantAction,option_id:button.dataset.workAssistantOption}); return; }
    if (button.dataset.workLauncherChoice) { write('choose-launcher',{choice_id:button.dataset.workLauncherChoice,target:button.dataset.workLauncherTarget}); return; }
    if (button.dataset.workTask) {const task=overview?.tasks?.find(t=>t.id===button.dataset.workTask);if(!task)return;selected=task.id;render();openTaskEditor(task);return;}
    if (button.dataset.workExample) { $('workCommand').value=button.dataset.workExample; companionOutcome=null;companionUpdate(); $('workCommand').focus(); return; }
    if (button.dataset.workStatus) { write('status',{task_id:selected,status:button.dataset.workStatus}); return; }
    if (button.dataset.workPreview) {
      try {const response=await fetch('/api/work/preview-resource',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:overview?.action_token,resource_id:button.dataset.workPreview})});const data=await response.json();if(!response.ok || data.error)throw Error(data.error || '미리보기를 열지 못했습니다.');document.dispatchEvent(new CustomEvent('work-stage-preview',{detail:{artifact:data.artifact}}));if($('workTaskDialog').open)$('workTaskDialog').close();}catch(e){notice(e.message,true);}return;
    }
    if (button.dataset.workResource) { write('open-resource',{resource_id:button.dataset.workResource}); return; }
    switch (button.dataset.workAction) {
      case 'close-detail': historySequence++; $('workTaskDialog').close(); break;
      case 'reload': window.workLoad(); break;
      case 'desk': show('desk'); break;
      case 'tasks': show('tasks'); break;
      case 'clear-context': selected=null; render(); break;
      case 'add-task': openTaskEditor(); break;
      case 'cancel-task': $('workTaskCreateDialog').close(); break;
      case 'cancel-other-request': $('workOtherRequestDialog').close(); break;
      case 'pick-resource': await pickFolder('workResourcePath'); break;
      case 'retry': if (uncertain) await write(null,null,null,uncertain); break;
      case 'retry-record': if(recordUncertain)await writeRecord(null,null,null,recordUncertain); break;

    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('work')) return;
    const masthead=$('workMasthead'), brand=document.querySelector('.side .brand');
    function alignMasthead() {
      if (!masthead || !brand || !document.body.classList.contains('work-desktop')) return;
      const ruleTop=brand.getBoundingClientRect().bottom-(parseFloat(getComputedStyle(brand).borderBottomWidth) || 0);
      const height=Math.max(0,ruleTop-$('work').getBoundingClientRect().top);
      const value=height.toFixed(2)+'px';
      if (masthead.style.getPropertyValue('--work-masthead-height') !== value) masthead.style.setProperty('--work-masthead-height',value);
    }
    window.addEventListener('resize',alignMasthead);
    if (brand && typeof ResizeObserver === 'function') new ResizeObserver(alignMasthead).observe(brand);
    if (typeof MutationObserver === 'function') new MutationObserver(alignMasthead).observe(document.body,{attributes:true,attributeFilter:['class']});
    document.fonts?.ready?.then(alignMasthead);
    alignMasthead();
    $('workTaskDialog').addEventListener('close',()=>{historySequence++; if (selected) $('workTaskList').querySelector(`[data-work-task="${CSS.escape(selected)}"]`)?.focus();});
    $('work').addEventListener('submit',submit); $('work').addEventListener('click',click);
    $('workUtilities')?.addEventListener('click',click);
    const chatLog = $('workMessages');
    const chatScrollIntent = () => { rememberChatPosition();chatScrollIntentUntil = Date.now()+1500; };
    chatLog.addEventListener('wheel',event=>{
      chatScrollIntent();
      if (event.deltaY < 0) followingChat = false;
    },{passive:true});
    chatLog.addEventListener('touchmove',chatScrollIntent,{passive:true});
    chatLog.addEventListener('pointerdown',chatScrollIntent,{passive:true});
    chatLog.addEventListener('keydown',event=>{
      if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)) {
        chatScrollIntent();
        if (['ArrowUp','PageUp','Home'].includes(event.key)) followingChat = false;
      }
    });
    chatLog.addEventListener('scroll',()=>{
      if(window.officeMainChatVisible===false||!chatLog.clientHeight)return;
      const atBottom = chatLog.scrollHeight-chatLog.scrollTop-chatLog.clientHeight < 8;
      if (atBottom) followingChat = true;
      else if (Date.now() < chatScrollIntentUntil) followingChat = false;
      rememberChatPosition();
    },{passive:true});
    window.addEventListener('resize',()=>settleChatScroll());
    document.fonts?.ready?.then(()=>settleChatScroll());
    observeChatLayout();
    const command = $('workCommand');
    $('workAttach')?.addEventListener('click',()=>$('workAttachmentInput').click());
    $('workAttachmentInput')?.addEventListener('change',event=>{addAttachments(event.target.files);event.target.value='';});
    $('workAttachments')?.addEventListener('click',async event=>{
      const button=event.target.closest('[data-attachment-remove]');if(!button || busy || uncertain || uploading || !overview?.action_token)return;
      const item=attachments.find(item=>item.upload_id===button.dataset.attachmentRemove);if(!item)return;
      uploading=true;locks();notice();
      try{
        const response=await fetch('/api/work/upload-remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:overview.action_token,request_id:attachmentDraft,attachment_id:item.id || item.upload_id})});
        const data=await response.json();if(!response.ok || data.error || data.ok!==true)throw Error(data.error || '첨부 파일을 취소하지 못했습니다.');
        attachments=attachments.filter(candidate=>candidate!==item);
        if(!attachments.length)attachmentDraft='';
      }catch(error){notice(error.message,true);}
      finally{uploading=false;renderAttachments();locks();}
    });
    const composer=$('workCommandForm');
    const draggingFiles=event=>Array.from(event.dataTransfer?.types || []).includes('Files');
    composer.addEventListener('dragover',event=>{if(draggingFiles(event)){event.preventDefault();event.dataTransfer.dropEffect=busy || uncertain || uploading ? 'none' : 'copy';composer.classList.add('is-file-drag');}});
    composer.addEventListener('dragleave',event=>{if(!composer.contains(event.relatedTarget))composer.classList.remove('is-file-drag');});
    composer.addEventListener('drop',event=>{if(draggingFiles(event)){event.preventDefault();composer.classList.remove('is-file-drag');addAttachments(event.dataTransfer.files);}});
    command.addEventListener('paste',event=>{if(event.clipboardData?.files?.length){event.preventDefault();addAttachments(event.clipboardData.files);}});
    // File drops outside the composer must not navigate away from an unfinished conversation.
    document.addEventListener('dragover',event=>{if(draggingFiles(event))event.preventDefault();});
    document.addEventListener('drop',event=>{if(draggingFiles(event))event.preventDefault();});
    let composing = false;
    command.addEventListener('input',()=>{companionOutcome=null;companionUpdate();});
    command.addEventListener('change',()=>{companionOutcome=null;companionUpdate();});
    $('workCompanionAnimation')?.addEventListener('error',()=>{$('workCompanion')?.classList.toggle('is-gif-unavailable',true);});
    $('workCompanionStill')?.addEventListener('error',()=>{$('workCompanion')?.classList.toggle('is-still-unavailable',true);});
    command.addEventListener('compositionstart',()=>{composing = true;companionUpdate();});
    command.addEventListener('compositionend',()=>{composing = false;companionUpdate();});
    command.addEventListener('blur',()=>{composing = false;});
    command.addEventListener('keydown',event=>{
      if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
      // IME confirmation can report keyCode 229 even when isComposing is false.
      if (composing || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      if (event.repeat || command.disabled || ((busy || uncertain) && !controlledRequest()) || $('workSend').disabled || !overview?.action_token || !command.value.trim()) return;
      $('workCommandForm').requestSubmit($('workSend'));
    });
    document.addEventListener('work-companion-changed',refreshCompanionManifest);
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden)stopCompanionPlayback();
      else if(companionState==='working')companionVariantUpdate('working');
    });
    matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',()=>{
      if(companionState==='working')companionVariantUpdate('working');
    });
    window.addEventListener('pagehide',stopCompanionPlayback);
    setInterval(()=>{if(!document.hidden && !busy && companionState==='idle' && !matchMedia('(prefers-reduced-motion: reduce)').matches)companionVariantUpdate('idle',true);},8000);
    refreshCompanionManifest();
    locks(); window.workLoad(); refreshExternalChat();
  });
})();

/* Launcher has its own busy state: assistant requests never lock quick launch. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const requestStoreKey='aemeath.otherRequests.v1';
  const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  let editingRequestIndex=-1, editingRequestSnapshot=null;
  function renderOtherRequests(){
    const list=$('workOtherRequestList');if(!list)return;
    let items=[];try{items=JSON.parse(localStorage.getItem(requestStoreKey)||'[]');}catch{}
    list.innerHTML=items.length?items.map((item,index)=>`<button type="button" class="work-other-request-row" data-other-request-index="${index}" aria-label="${escapeHTML(item.team)} 타부서 요청 수정: ${escapeHTML(item.content)}"><strong>${escapeHTML(item.team)} · ${escapeHTML(item.status)}</strong><span class="work-other-request-content" title="${escapeHTML(item.content)}">${escapeHTML(item.content)}</span><small>${escapeHTML(item.start||'시작일 미정')} → ${escapeHTML(item.due||'완료 예정일 미정')}</small></button>`).join(''):'<p class="work-empty">등록된 요청사항이 없어요.</p>';
  }
  function openOtherRequest(index=-1){
    let items=[];try{items=JSON.parse(localStorage.getItem(requestStoreKey)||'[]');}catch{}
    editingRequestIndex=index;const item=items[index];
    editingRequestSnapshot=item?JSON.stringify(item):null;
    $('workOtherRequestDelete').hidden=!item;
    $('workOtherRequestError').hidden=true;
    $('workOtherRequestTitle').textContent=item?'타부서 요청 수정':'타부서 요청 추가';
    $('workRequestTeam').value=item?.team||'기획팀';$('workRequestContent').value=item?.content||'';
    $('workRequestStatus').value=item?.status||'review';$('workRequestStart').value=item?.start||'';$('workRequestDue').value=item?.due||'';
    $('workOtherRequestDialog').showModal();
  }
  function deleteOtherRequest(){
    if(editingRequestIndex<0 || !editingRequestSnapshot)return;
    const selected=JSON.parse(editingRequestSnapshot);
    if(!window.confirm(`${selected.team} 요청사항을 삭제할까요?\n\n${selected.content}\n\n삭제한 요청은 복구할 수 없어요.`))return;
    const error=$('workOtherRequestError');
    try{
      const items=JSON.parse(localStorage.getItem(requestStoreKey)||'[]');
      if(!Array.isArray(items) || JSON.stringify(items[editingRequestIndex])!==editingRequestSnapshot){
        error.textContent='요청 목록이 변경됐어요. 창을 닫고 삭제할 요청을 다시 선택해 주세요.';error.hidden=false;renderOtherRequests();return;
      }
      items.splice(editingRequestIndex,1);
      localStorage.setItem(requestStoreKey,JSON.stringify(items));
    }catch{
      error.textContent='요청을 삭제하지 못했어요. 브라우저 저장 공간을 확인하고 다시 시도해 주세요.';error.hidden=false;return;
    }
    $('workOtherRequestDialog').close();renderOtherRequests();
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if(!$('workOtherRequestForm'))return;
    renderOtherRequests();
    document.querySelector('[data-work-action="add-other-request"]').addEventListener('click',()=>openOtherRequest());
    $('workOtherRequestDelete').addEventListener('click',deleteOtherRequest);
    $('workOtherRequestList').addEventListener('click',event=>{const card=event.target.closest('[data-other-request-index]');if(card)openOtherRequest(Number(card.dataset.otherRequestIndex));});
    $('workOtherRequestForm').addEventListener('submit',event=>{
      event.preventDefault();
      const item={team:$('workRequestTeam').value,status:$('workRequestStatus').value,content:$('workRequestContent').value.trim(),start:$('workRequestStart').value,due:$('workRequestDue').value};
      if(!item.content)return;
      let items=[];try{items=JSON.parse(localStorage.getItem(requestStoreKey)||'[]');}catch{}
      if(editingRequestIndex>=0 && editingRequestIndex<items.length)items[editingRequestIndex]=item;else items.push(item);
      localStorage.setItem(requestStoreKey,JSON.stringify(items));editingRequestIndex=-1;
      event.currentTarget.reset();$('workOtherRequestDialog').close();renderOtherRequests();
    });
    $('workOtherRequestDialog').addEventListener('close',()=>{
      editingRequestIndex=-1;editingRequestSnapshot=null;
      $('workOtherRequestForm').reset();$('workOtherRequestDelete').hidden=true;$('workOtherRequestError').hidden=true;
    });
  });
})();

(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state = null, busy = false, loading = false, pending = null, editing = null, imageData, imageReading = false, imageVersion = 0, imageURL = '';
  let selectedApp = null, bookmarks = null, bookmarkLoading = false, dialogVersion = 0;
  let launcherRefreshQueued = false;
  let dragItem = null, suppressLaunchUntil = 0, coverData, coverURL = '', coverReading = false, coverVersion = 0;
  let boardLayoutQueued=false, boardLayoutObserver=null;
  function measureBoard() {
    boardLayoutQueued=false;
    const grid=$('workLauncherGrid');if(!grid)return;
    grid.querySelectorAll('.work-launcher-cell,.work-launcher-add-tile').forEach(cell=>{
      // Equal-height CSS rows own layout; clear spans left by the old masonry.
      if (cell.style.gridRowEnd) cell.style.removeProperty('grid-row-end');
      const content=cell.querySelector('.work-launcher-card-content');
      if(!content)return;
      const name=content.querySelector('.work-launcher-name'),target=content.querySelector('.work-launcher-target'),description=content.querySelector('.work-launcher-description');
      if(!name||!target)return;
      const style=getComputedStyle(content),line=parseFloat(getComputedStyle(target).lineHeight),gap=parseFloat(style.rowGap)||0;
      let room=content.clientHeight-(parseFloat(style.paddingTop)||0)-(parseFloat(style.paddingBottom)||0)-name.getBoundingClientRect().height-gap;
      if(description){
        const descriptionLine=parseFloat(getComputedStyle(description).lineHeight)||line;
        description.hidden=room<line+descriptionLine+gap;
        if(!description.hidden)room-=descriptionLine+gap;
      }
      const lines=String(Math.max(1,Math.floor(room/line)));
      if(target.style.getPropertyValue('--launcher-target-lines')!==lines)target.style.setProperty('--launcher-target-lines',lines);
    });
  }
  function scheduleBoardLayout() {
    if (boardLayoutQueued) return;
    boardLayoutQueued=true;
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(measureBoard);
    else measureBoard();
  }
  document.fonts?.ready.then(scheduleBoardLayout);
  function observeBoardLayout() {
    boardLayoutObserver?.disconnect();
    if (typeof ResizeObserver === 'function') {
      boardLayoutObserver ||= new ResizeObserver(scheduleBoardLayout);
      boardLayoutObserver.observe($('workLauncherBoard'));
      $('workLauncherGrid').querySelectorAll('.work-launcher-tile').forEach(tile=>boardLayoutObserver.observe(tile));
    }
    scheduleBoardLayout();
  }
  function boardItems() {
    const items = state?.items || [], known = new Map(items.map(item => [item.id,item]));
    const ordered = [], seen = new Set();
    for (const itemID of state?.board_order || []) {
      if (known.has(itemID) && !seen.has(itemID)) {ordered.push(known.get(itemID));seen.add(itemID);}
    }
    for (const item of items) if (!seen.has(item.id)) {ordered.push(item);seen.add(item.id);}
    return ordered;
  }
  function drainChatRefresh() {
    if (!launcherRefreshQueued || busy || loading || pending || $('workLauncherDialog').open) return;
    launcherRefreshQueued = false;
    load();
  }
  const id = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join('');
  function message(text = '', error = false) {
    $('workLauncherNotice').textContent = text;
    $('workLauncherNotice').classList.toggle('is-error', error);
    if ($('workLauncherDialog').open) $('workLauncherFormNotice').textContent = text;
  }
  function locks() {
    document.querySelectorAll('#workLauncher button,#workLauncherDialog button,#workLauncherDialog input,#workLauncherDialog select,#workLauncherDialog textarea').forEach(el => {
      const action = el.dataset.launcherAction;
      el.disabled = action === 'close' ? false : action === 'retry' ? busy : action === 'reload' ? busy || loading || !!pending : busy || loading || !!pending || !state?.action_token || imageReading || coverReading || bookmarkLoading;
    });
    $('workLauncherGrid').querySelectorAll('a[data-launcher-action="launch"]').forEach(link=>{
      link.disabled=busy || loading || !!pending || !state?.action_token || imageReading || coverReading || bookmarkLoading;
      link.setAttribute('aria-disabled',String(link.disabled));link.tabIndex=link.disabled ? -1 : 0;
    });
    $('workLauncherRetry').hidden = !pending;
    $('workLauncherDialogRetry').hidden = !pending;
    $('workLauncherSave').textContent = busy ? '저장 중…' : imageReading || coverReading ? '이미지 읽는 중…' : '바로가기 저장';
    $('workLauncher').classList.toggle('is-busy',busy);
    $('workLauncherGrid').querySelectorAll('[data-launcher-action="launch"]').forEach(tile => {tile.draggable = !dragBlocked();});
    const items = boardItems(), index = items.findIndex(x => x.id === editing);
    for (const [name, unavailable] of [['workLauncherMoveUp',index <= 0],['workLauncherMoveDown',index < 0 || index >= items.length-1]]) {
      $(name).disabled = $(name).disabled || unavailable;
    }
  }
  const iconURL = item => '/api/launcher/icon?id='+encodeURIComponent(item.id)+(item.icon_revision ? '&v='+encodeURIComponent(item.icon_revision) : '');
  const coverURLFor = item => '/api/launcher/cover?id='+encodeURIComponent(item.id)+(item.card_cover_revision ? '&v='+encodeURIComponent(item.card_cover_revision) : '');
  function webLink(item) {
    if(item.kind!=='web' || typeof item.target!=='string' || /[\s\\\x00-\x1f\x7f]/.test(item.target))return '';
    try{const url=new URL(item.target);return ['http:','https:'].includes(url.protocol) && url.hostname && !url.username && !url.password ? url.href : '';}
    catch{return '';}
  }
  function icon(item) {
    return item.icon_image ? `<img src="${iconURL(item)}" alt="" loading="lazy">` : `<span>${esc(item.icon || Array.from(item.title || '✦').slice(0,2).join(''))}</span>`;
  }
  function render() {
    const items = boardItems();
    const tiles = items.map(item => {
      // The internal native capability is independent of an older server's
      // executable-path validation while its backend reload is waiting.
      if(['office:messengers','office:video-studio'].includes(item.target))item={...item,available:true};
      const type = item.target==='office:video-studio' ? ['V','영상 제작실'] : item.target==='office:messengers' ? ['M','통합메신저'] : item.kind === 'web' ? ['W','웹사이트'] : item.kind === 'folder' ? ['F','폴더'] : ['P','프로그램'];
      const artwork = item.card_cover
        ? `<img src="${coverURLFor(item)}" alt="" loading="lazy" draggable="false">`
        : `<span class="work-launcher-icon${item.icon_image ? ' is-image' : ''}" aria-hidden="true">${icon(item)}</span>`;
      const href=webLink(item),tag=href ? 'a' : 'button';
      const targetLabel=['office:messengers','office:video-studio'].includes(item.target)?'에이메스 내부 팝업':item.target;
      const attributes=href ? `href="${esc(href)}" target="_blank" rel="noopener noreferrer"` : 'type="button"';
      return `<div class="work-launcher-cell"><${tag} ${attributes} draggable="true" class="work-launcher-tile${item.card_cover ? ' has-cover' : ''}${item.available === false ? ' is-unavailable' : ''}" data-launcher-action="launch" data-launcher-id="${esc(item.id)}" title="${esc(item.title)} · ${esc(targetLabel)}" aria-label="${esc(item.title)} 열기${item.available === false ? ' · 경로 확인 필요' : ''}"><span class="work-launcher-cover${item.card_cover ? '' : ' is-icon-artwork'}">${artwork}</span><span class="work-launcher-card-content"><span class="work-launcher-kind" data-launcher-kind="${type[0]}" title="${type[1]}" aria-label="${type[1]}">${type[0]}</span><span class="work-launcher-name">${esc(item.title)}</span><span class="work-launcher-target" title="${esc(targetLabel)}">${esc(targetLabel)}</span>${item.card_description ? `<span class="work-launcher-description">${esc(item.card_description)}</span>` : ''}</span></${tag}></div>`;
    }).join('');
    const addTile = '<button type="button" class="work-launcher-tile work-launcher-add-tile" data-launcher-action="add" aria-label="바로가기 카드 추가"><span aria-hidden="true">＋</span><span>새 카드</span></button>';
    // The old drag source may be detached before its dragend reaches the grid.
    finishDrag();
    $('workLauncherGrid').innerHTML = tiles + addTile;
    $('workLauncherCount').textContent = `${items.length} CARDS · SCROLL TO EXPLORE`;
    observeBoardLayout();
    locks();
  }
  let launcherRetryTimer=null,launcherReadFailures=0;
  async function load(refresh = false) {
    if (busy || loading || pending) return;
    clearTimeout(launcherRetryTimer);
    loading = true; locks();
    const controller=new AbortController(),deadline=setTimeout(()=>controller.abort(),6000);
    try {
      const response = refresh && state?.action_token
        ? await fetch('/api/launcher/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.action_token}),signal:controller.signal})
        : await fetch('/api/launcher',{cache:'no-store',signal:controller.signal});
      const data = await response.json();
      if (!response.ok || data.error) throw Error(data.error || '바로가기를 불러오지 못했습니다.');
      state = data;launcherReadFailures=0;message();render();
    } catch (e) {
      message(e.name==='AbortError'?'바로가기 연결 응답을 기다리고 있어요.':e.message,true);
      if (!state) {
        $('workLauncherGrid').innerHTML = '<div class="work-launcher-empty"><p>바로가기 목록 연결을 확인하고 있어요.</p><button type="button" data-launcher-action="reload">목록 다시 연결</button></div>';
        // Only repeat an initial read. Never repeat refresh/launch/save POSTs.
        if(!refresh&&launcherReadFailures<4)launcherRetryTimer=setTimeout(()=>load(false),1000*2**launcherReadFailures++);
      }
    }
    finally {clearTimeout(deadline);loading = false;locks();drainChatRefresh();}
  }
  addEventListener('online',()=>{if(!state){launcherReadFailures=0;load(false);}});
  async function write(endpoint, payload, cleanup, previous) {
    if (busy || (!previous && pending) || !state?.action_token) return;
    const request = previous || {endpoint,body:{...payload,token:state.action_token,request_id:id()},cleanup};
    busy = true; message(request.endpoint === 'launch' ? '열기 요청 중…' : '저장 중…'); locks();
    let confirmed = false;
    try {
      if (previous) {
        const response = await fetch('/api/launcher',{cache:'no-store'}), data = await response.json();
        if (!response.ok || data.error || !data.action_token) throw Error(data.error || '연결 정보를 가져오지 못했습니다.');
        request.body.token = data.action_token; state.action_token = data.action_token;
      }
      const response = await fetch('/api/launcher/'+request.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request.body)}), data = await response.json();
      const unresolved = [202,403,409].includes(response.status) || /처리\s*중|processing|in[_ -]?progress/i.test([data.error,data.code,data.status].filter(Boolean).join(' '));
      confirmed = !unresolved;
      if (unresolved || !response.ok || data.error) throw Error(data.error || '요청을 처리하지 못했습니다.');
      pending = null; state = data;
      request.cleanup?.(); render(); message(data.result?.message || (request.endpoint === 'launch' ? '열기 요청을 전달했습니다.' : '바로가기를 저장했습니다.'));
    } catch (e) {
      if (!confirmed) pending = request; else pending = null;
      message(`${confirmed ? '요청 실패' : '처리 결과 확인 실패'}: ${e.message}`,true);
    } finally { busy = false; locks(); drainChatRefresh(); }
  }
  function kindFields() {
    const kind = $('workLauncherKind').value;
    const config = {app:['프로그램 전체 경로 (.exe / .lnk)','발견한 프로그램을 선택하거나 전체 경로를 입력하세요.','C:\\Program Files\\...\\app.exe'],web:['웹사이트 주소','http:// 또는 https://로 시작하는 웹사이트 주소를 입력하세요.','https://example.com'],folder:['폴더 전체 경로','작업 폴더를 선택하거나 전체 경로를 입력하세요.','C:\\Users\\...\\Documents']}[kind];
    $('workLauncherTargetLabel').textContent = config[0]; $('workLauncherTargetHelp').textContent = config[1]; $('workLauncherTarget').placeholder = config[2]; $('workLauncherPick').hidden = kind === 'web';
    $('workLauncherKindWrap').hidden = false;
    $('workLauncherSuggestionsWrap').hidden = kind !== 'app';
    $('workLauncherBookmarksWrap').hidden = kind !== 'web';
    const registered = kind === 'app' && !!selectedApp;
    $('workLauncherSelectedApp').hidden = !registered;
    $('workLauncherTargetWrap').hidden = registered;
    $('workLauncherTarget').required = !registered;
    $('workLauncherSelectedAppName').textContent = registered ? 'Windows 시작 메뉴 · '+selectedApp.title : '';
  }
  function appResults() {
    const query=$('workLauncherAppSearch').value.trim().toLocaleLowerCase(), all=state?.suggestions || [];
    const results=all.map((item,index)=>({item,index})).filter(({item})=>[item.title,item.detail,...(item.aliases || [])].filter(Boolean).join(' ').toLocaleLowerCase().includes(query));
    $('workLauncherAppCount').textContent=`${results.length}개 / 전체 ${all.length}개`+(state?.suggestion_source && state.suggestion_source !== 'windows-start-menu' ? ' · 바로가기에서 찾음' : '')+((state?.suggestion_warnings || []).length ? ' · '+state.suggestion_warnings.join(' ') : '');
    $('workLauncherSuggestions').innerHTML=results.length ? results.map(({item,index})=>`<button type="button" class="work-source-result" data-launcher-action="suggestion" data-source-index="${index}"><strong>${esc(item.title)}</strong><small>${esc(item.detail || item.source || (String(item.target).startsWith('startapp:') ? 'Windows 시작 메뉴' : item.target))}</small></button>`).join('') : '<p class="work-source-empty">일치하는 프로그램이 없습니다. 아래에서 실행 파일을 직접 선택할 수 있습니다.</p>';
    locks();
  }
  function bookmarkResults() {
    const query=$('workLauncherBookmarkSearch').value.trim().toLocaleLowerCase(), all=bookmarks?.items || [];
    const profile=$('workLauncherBookmarkProfile').value;
    const results=all.map((item,index)=>({item,index})).filter(({item})=>(!profile || JSON.stringify([item.browser,item.profile]) === profile) && [item.title,item.target,item.folder,item.browser,item.profile].join(' ').toLocaleLowerCase().includes(query));
    $('workLauncherBookmarkCount').textContent=`${results.length}개 / 불러온 북마크 ${all.length}개`+(bookmarks?.truncated ? ' · 일부만 불러옴' : '');
    $('workLauncherBookmarks').innerHTML=results.length ? results.map(({item,index})=>`<button type="button" class="work-source-result" data-launcher-action="bookmark" data-source-index="${index}"><strong>${esc(item.title)}</strong><small>${esc([item.browser,item.profile,item.folder].filter(Boolean).join(' · '))}</small><small>${esc(item.target)}</small></button>`).join('') : '<p class="work-source-empty">일치하는 북마크가 없습니다. 주소를 직접 입력할 수 있습니다.</p>';
    locks();
  }
  function chooseSource(suggestion) {
    if (!suggestion) return;
    selectedApp=String(suggestion.target).startsWith('startapp:') ? suggestion : null;
    $('workLauncherName').value=suggestion.title; $('workLauncherKind').value=suggestion.kind; $('workLauncherTarget').value=selectedApp ? '' : suggestion.target; $('workLauncherIcon').value=suggestion.icon || '✦';
    imageData=''; imageURL=''; imageVersion++; $('workLauncherImage').value='';
    $('workLauncherFormNotice').textContent=`${suggestion.title} 선택됨 · 아래에서 바로가기를 저장하세요.`;
    kindFields(); preview();
  }
  function preview() {
    $('workLauncherIconPreview').replaceChildren();
    const url = imageData || imageURL;
    if (url) { const img = document.createElement('img'); img.src=url; img.alt=''; $('workLauncherIconPreview').append(img); }
    else $('workLauncherIconPreview').textContent = $('workLauncherIcon').value || '✦';
  }
  function coverPreview() {
    const container = $('workLauncherCoverPreview'), url = coverData || coverURL;
    container.replaceChildren(); container.hidden = !url;
    if (url) {const image=document.createElement('img');image.src=url;image.alt='선택한 카드 커버';container.append(image);}
  }
  function open(item) {
    clearDrag();
    dialogVersion++; selectedApp=item?.kind === 'app' && String(item.target).startsWith('startapp:') ? item : null;
    editing = item?.id || null; imageData = undefined; imageURL = item?.icon_image ? iconURL(item) : ''; imageVersion++; imageReading=false;
    coverData=undefined;coverURL=item?.card_cover ? coverURLFor(item) : '';coverVersion++;coverReading=false;
    $('workLauncherForm').reset(); $('workLauncherDialogTitle').textContent = editing ? '바로가기 수정' : '바로가기 추가';
    $('workLauncherItemActions').hidden = !editing;
    ['workLauncherRemove','workLauncherMoveUp','workLauncherMoveDown'].forEach(name => {$(name).dataset.launcherId = editing || '';});
    $('workLauncherName').value = item?.title || ''; $('workLauncherKind').value = item?.kind || 'app'; $('workLauncherTarget').value = selectedApp ? '' : item?.target || ''; $('workLauncherIcon').value = item?.icon || '✦'; $('workLauncherFormNotice').textContent='';
    $('workLauncherDescription').value=item?.card_description || '';$('workLauncherCover').value='';
    $('workLauncherBookmarkPicker').hidden=!bookmarks; $('workLauncherBookmarkNotice').textContent=(bookmarks?.warnings || []).join(' ');
    appResults(); if (bookmarks) bookmarkResults();
    kindFields(); preview(); coverPreview(); $('workLauncherDialog').showModal(); locks(); $('workLauncherName').focus();
  }
  async function click(event) {
    const button = event.target.closest('[data-launcher-action]'); if (!button) return;
    if(button.disabled){event.preventDefault();return;}
    const action=button.dataset.launcherAction, item=(state?.items || []).find(x=>x.id===button.dataset.launcherId);
    if (action === 'close') { dialogVersion++; $('workLauncherDialog').close(); return; }
    if (action === 'reload') { await load(true); return; }
    if (action === 'retry') { if (pending) await write(null,null,null,pending); return; }
    if (action === 'add') { open(); return; }
    if (action === 'edit') { if (item) open(item); return; }
    if (action === 'launch') {
      if(Date.now() < suppressLaunchUntil || dragItem || !item){event.preventDefault();return;}
      if(item.target==='office:video-studio'){
        event.preventDefault();
        try{await window.OfficeVideoStudio.open();message('수동형 영상 제작실을 열었어요.');}
        catch(error){message(error.message,true);}
        return;
      }
      const messengerSource=item.target==='office:messengers'?'messengers':item.kind==='app'&&window.officeNativeMessenger?.sourceFor(item.title);
      if(messengerSource){
        event.preventDefault();
        try{await window.officeMessenger.open(messengerSource);message('기존 통합메신저 PC 화면 팝업을 열었어요.');}
        catch(error){message(error.message,true);}
        return;
      }
      if(item.kind==='web'){
        if(!button.matches('a[href]')){event.preventDefault();message('웹 주소를 확인해 주세요.',true);return;}
        // Native navigation belongs to the browser/profile hosting this office.
        // Do not send a click to the separate automation Chrome workspace.
        message('현재 브라우저의 새 탭으로 열기를 요청했습니다.');return;
      }
      event.preventDefault();await write(action,{id:item.id});return;
    }
    if (action === 'remove') {
      if (item) await write('remove',{id:item.id},()=>{$('workLauncherDialog').close();});
      return;
    }
    if (action === 'up' || action === 'down') {
      if (!item) return;
      const ids=boardItems().map(x=>x.id), from=ids.indexOf(item.id), to=from+(action === 'up' ? -1 : 1);
      if (to < 0 || to >= ids.length) return;
      [ids[from],ids[to]]=[ids[to],ids[from]]; await write('board-order',{ids},()=>{$('workLauncherDialog').close();}); return;
    }
    if (action === 'suggestion') {
      chooseSource(state?.suggestions?.[Number(button.dataset.sourceIndex)]); return;
    }
    if (action === 'bookmark') { chooseSource(bookmarks?.items?.[Number(button.dataset.sourceIndex)]); return; }
    if (action === 'manual-target') { selectedApp=null; kindFields(); $('workLauncherTarget').focus(); return; }
    if (action === 'bookmarks') {
      const version=dialogVersion; bookmarkLoading=true; locks(); $('workLauncherBookmarkNotice').textContent='북마크를 읽는 중…';
      try {
        const response=await fetch('/api/launcher/bookmarks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.action_token})}), data=await response.json();
        if (!response.ok || data.error) throw Error(data.error || '북마크를 읽지 못했습니다.');
        bookmarks=data;
        const profiles=[...new Set((data.items || []).map(x=>JSON.stringify([x.browser,x.profile])))];
        $('workLauncherBookmarkProfile').innerHTML='<option value="">모든 프로필</option>'+profiles.map(key=>`<option value="${esc(key)}">${esc(JSON.parse(key).filter(Boolean).join(' · '))}</option>`).join('');
        if (version === dialogVersion) { $('workLauncherBookmarkPicker').hidden=false; bookmarkResults(); $('workLauncherBookmarkNotice').textContent=(data.warnings || []).join(' '); }
      } catch(e) { if (version === dialogVersion) $('workLauncherBookmarkNotice').textContent=e.message; }
      finally {bookmarkLoading=false;locks();if (version === dialogVersion && bookmarks) $('workLauncherBookmarkSearch').focus();} return;
    }
    if (action === 'clear-image' || action === 'palette') {
      imageData=''; imageURL=''; imageVersion++; $('workLauncherImage').value='';
      if (action === 'palette') $('workLauncherIcon').value=button.dataset.icon;
      preview(); return;
    }
    if (action === 'clear-cover') {coverData=null;coverURL='';coverVersion++;coverReading=false;$('workLauncherCover').value='';coverPreview();locks();return;}
    if (action === 'pick') {
      if ($('workLauncherKind').value === 'folder') { await pickFolder('workLauncherTarget'); return; }
      busy=true; locks();
      try {
        const response=await fetch('/api/launcher/pick-program',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.action_token})}), data=await response.json();
        if (!response.ok || data.error) throw Error(data.error || '프로그램 선택창을 열지 못했습니다.');
        if (data.path) { selectedApp=null; $('workLauncherTarget').value=data.path; kindFields(); }
      } catch(e) { $('workLauncherFormNotice').textContent=e.message; }
      finally {busy=false;locks();drainChatRefresh();}
    }
  }
  async function submit(event) {
    if (event.target.id !== 'workLauncherForm') return;
    event.preventDefault(); if (busy || pending || imageReading || coverReading) return;
    const title=$('workLauncherName').value.trim(), kind=$('workLauncherKind').value, target=kind === 'app' && selectedApp ? selectedApp.target : $('workLauncherTarget').value.trim();
    const original = state?.items?.find(x => x.id === editing);
    const cardStyle={card_description:$('workLauncherDescription').value.trim(),...(coverData !== undefined ? {card_cover_data:coverData} : {})};
    if (original && kind === original.kind && target === original.target) {
      if (!title) return;
      await write('appearance',{id:editing,title,icon:$('workLauncherIcon').value.trim(),...cardStyle,...(imageData !== undefined ? {icon_data:imageData} : {})},()=>{$('workLauncherDialog').close();});
      return;
    }
    if (!title || !target) return;
    if (kind === 'web') { try { const url=new URL(target); if (!['http:','https:'].includes(url.protocol)) throw Error(); } catch(_) { $('workLauncherFormNotice').textContent='http:// 또는 https://로 시작하는 올바른 주소를 입력하세요.'; return; } }
    const payload={title,target,kind,icon:$('workLauncherIcon').value.trim(),...cardStyle,...(editing ? {id:editing} : {}),...(imageData !== undefined ? {icon_data:imageData} : {})};
    await write('save',payload,()=>{$('workLauncherDialog').close();});
  }
  function dragBlocked() { return busy || loading || !!pending || !state?.action_token || $('workLauncherDialog').open; }
  function clearDrag() {
    dragItem = null;
    $('workLauncherGrid').querySelectorAll('.is-dragging,.drop-before,.drop-after').forEach(tile => tile.classList.remove('is-dragging','drop-before','drop-after'));
  }
  function finishDrag() {
    if (dragItem) suppressLaunchUntil = Date.now() + 500;
    clearDrag();
  }
  function dragTarget(event) {
    const tile = event.target.closest('[data-launcher-action="launch"]');
    const item = state?.items?.find(x => x.id === tile?.dataset.launcherId);
    const source = state?.items?.find(x => x.id === dragItem);
    if (dragBlocked() || !tile || tile.disabled || !item || !source) return null;
    return {tile,item,source};
  }
  function dropAfter(event, tile) { const rect = tile.getBoundingClientRect(); return event.clientX >= rect.left + rect.width/2; }
  function dragStart(event) {
    const tile=event.target.closest('[data-launcher-action="launch"]');
    const item=state?.items?.find(x=>x.id===tile?.dataset.launcherId);
    if (dragBlocked() || !tile || tile.disabled || !item || !event.dataTransfer) {event.preventDefault();return;}
    clearDrag();
    event.dataTransfer.clearData(); event.dataTransfer.setData('text/plain',item.id); event.dataTransfer.effectAllowed='move';
    dragItem=item.id; suppressLaunchUntil=Date.now()+500;
    tile.classList.add('is-dragging');
  }
  function dragOver(event) {
    const target=dragTarget(event); if (!target || event.dataTransfer?.files?.length) {event.preventDefault();if(event.dataTransfer)event.dataTransfer.dropEffect='none';return;}
    event.preventDefault(); event.dataTransfer.dropEffect='move';
    $('workLauncherGrid').querySelectorAll('.drop-before,.drop-after').forEach(tile=>tile.classList.remove('drop-before','drop-after'));
    if (target.item.id !== dragItem) target.tile.classList.add(dropAfter(event,target.tile) ? 'drop-after' : 'drop-before');
  }
  async function drop(event) {
    event.preventDefault(); // Do not let external URL/file drops navigate the work surface.
    const target=dragTarget(event), sourceID=dragItem;
    if (!target || event.dataTransfer?.files?.length || event.dataTransfer?.getData('text/plain') !== sourceID) {clearDrag();return;}
    const after=dropAfter(event,target.tile); suppressLaunchUntil=Date.now()+500; clearDrag();
    if (target.item.id === sourceID) return;
    const ordered=boardItems().map(x=>x.id);
    const reordered=ordered.filter(id=>id!==sourceID), at=reordered.indexOf(target.item.id)+(after ? 1 : 0);
    reordered.splice(at,0,sourceID);
    if (ordered.every((id,index)=>id===reordered[index])) return;
    await write('board-order',{ids:reordered});
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('workLauncher')) return;
    $('workLauncherPalette').innerHTML=['✦','🎨','🖥️','🌐','📁','📊','✉️','📝','🎬','⚙️'].map(x=>`<button type="button" data-launcher-action="palette" data-icon="${esc(x)}" aria-label="${esc(x)} 아이콘">${esc(x)}</button>`).join('');
    $('work').addEventListener('click',click); $('work').addEventListener('submit',submit);
    function editShortcut(event) {
      const tile=event.target.closest('[data-launcher-action="launch"]');
      if (!tile) return;
      event.preventDefault();
      if (tile.disabled || busy || loading || pending || !state?.action_token) return;
      const item=state.items.find(x=>x.id===tile.dataset.launcherId);
      if (item) open(item);
    }
    $('workLauncherGrid').addEventListener('dragstart',dragStart);
    $('workLauncherGrid').addEventListener('dragover',dragOver);
    $('workLauncherGrid').addEventListener('drop',drop);
    $('workLauncherGrid').addEventListener('dragend',finishDrag);
    $('workLauncherGrid').addEventListener('pointerdown',event=>{
      if (event.button !== 0) return;
      // A new press is independent of a drag interrupted by the host window.
      if (dragItem) clearDrag();
      suppressLaunchUntil = 0;
    });
    $('workLauncherGrid').addEventListener('dragleave',event=>{if (!$('workLauncherGrid').contains(event.relatedTarget)) $('workLauncherGrid').querySelectorAll('.drop-before,.drop-after').forEach(tile=>tile.classList.remove('drop-before','drop-after'));});
    $('workLauncherGrid').addEventListener('contextmenu',editShortcut);
    $('workLauncherGrid').addEventListener('auxclick',event=>{
      const link=event.target.closest('a[data-launcher-action="launch"]');
      if(link && (link.disabled || dragItem || Date.now()<suppressLaunchUntil))event.preventDefault();
    });
    $('workLauncherGrid').addEventListener('load',scheduleBoardLayout,true);
    if (typeof window.addEventListener === 'function') window.addEventListener('resize',scheduleBoardLayout);
    $('workLauncherGrid').addEventListener('keydown',event=>{
      if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) editShortcut(event);
    });
    $('workLauncherKind').addEventListener('change',()=>{selectedApp=null;$('workLauncherTarget').value='';$('workLauncherFormNotice').textContent='';kindFields();});
    $('workLauncherAppSearch').addEventListener('input',appResults);
    $('workLauncherBookmarkSearch').addEventListener('input',bookmarkResults);
    $('workLauncherBookmarkProfile').addEventListener('change',bookmarkResults);
    $('workLauncherDialog').addEventListener('cancel',()=>{dialogVersion++;clearDrag();});
    $('workLauncherDialog').addEventListener('close',()=>{clearDrag();locks();drainChatRefresh();});
    document.addEventListener('work-launcher-changed',()=>{launcherRefreshQueued=true;drainChatRefresh();});
    $('workLauncherIcon').addEventListener('input',()=>{imageData='';imageURL='';imageVersion++;preview();});
    $('workLauncherImage').addEventListener('change',()=>{
      const file=$('workLauncherImage').files[0]; if (!file) return;
      if (file.size > 512*1024 || !/\.(png|jpe?g|webp|gif|ico)$/i.test(file.name) || (file.type && !/^image\/(png|jpeg|webp|gif|x-icon|vnd\.microsoft\.icon)$/i.test(file.type))) { $('workLauncherFormNotice').textContent='512KB 이하 PNG, JPG, WebP, GIF, ICO 이미지를 선택하세요.'; $('workLauncherImage').value=''; return; }
      const version=++imageVersion, reader=new FileReader(); imageReading=true; locks();
      reader.onload=()=>{if (version !== imageVersion) return; imageData=String(reader.result);imageURL='';imageReading=false;preview();locks();$('workLauncherFormNotice').textContent='이미지 아이콘을 선택했습니다. 저장하면 적용됩니다.';};
      reader.onerror=()=>{if (version !== imageVersion) return;imageReading=false;locks();$('workLauncherFormNotice').textContent='이미지를 읽지 못했습니다.';}; reader.readAsDataURL(file);
    });
    $('workLauncherCover').addEventListener('change',()=>{
      const file=$('workLauncherCover').files[0];if (!file) return;
      if (file.size > 4*1024*1024 || !/\.(png|jpe?g|webp|gif)$/i.test(file.name) || (file.type && !/^image\/(png|jpeg|webp|gif)$/i.test(file.type))) { $('workLauncherFormNotice').textContent='4MB 이하 PNG, JPG, WebP, GIF 커버 이미지를 선택하세요.';$('workLauncherCover').value='';return; }
      const version=++coverVersion,reader=new FileReader();coverReading=true;locks();
      reader.onload=()=>{if(version !== coverVersion)return;coverData=String(reader.result);coverURL='';coverReading=false;coverPreview();locks();$('workLauncherFormNotice').textContent='커버를 선택했습니다. 저장하면 카드에 적용됩니다.';};
      reader.onerror=()=>{if(version !== coverVersion)return;coverReading=false;locks();$('workLauncherFormNotice').textContent='커버 이미지를 읽지 못했습니다.';};reader.readAsDataURL(file);
    });
    // Native dialogs provide Escape dismissal and keyboard focus containment.
    locks(); load();
  });
})();

/* Calendar is local and independent of assistant/network request locks. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let year, month, selectedCalendarDay=new Date().toLocaleDateString('en-CA');
  function renderCalendar() {
    const today=new Date(), first=new Date(year,month,1), days=new Date(year,month+1,0).getDate();
    $('workCalendarMonth').textContent=`${year}년 ${month+1}월`;
    let cells='<span class="work-calendar-blank" aria-hidden="true"></span>'.repeat(first.getDay());
    for(let day=1;day<=days;day++) {
      const current=today.getFullYear()===year && today.getMonth()===month && today.getDate()===day;
      const dateKey=`${year}-${String(month+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
      const selected=dateKey===selectedCalendarDay;
      const weekday=new Date(year,month,day).getDay();
      cells+=`<button type="button" class="work-calendar-day${weekday===0 ? ' is-sunday' : weekday===6 ? ' is-saturday' : ''}${current ? ' is-today' : ''}${selected ? ' is-selected' : ''}" data-calendar-day="${dateKey}"${current ? ' aria-current="date"' : ''} aria-pressed="${selected}" aria-label="${year}년 ${month+1}월 ${day}일">${day}</button>`;
    }
    $('workCalendarGrid').innerHTML=cells;
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if (!$('workCalendar')) return;
    const today=new Date(); year=today.getFullYear();month=today.getMonth();renderCalendar();
    $('workCalendar').addEventListener('click',event=>{
      const dayButton=event.target.closest('[data-calendar-day]');
      if(dayButton){selectedCalendarDay=dayButton.dataset.calendarDay;window.workSelectDay?.(selectedCalendarDay);renderCalendar();return;}
      const button=event.target.closest('[data-calendar-action]'); if (!button) return;
      if(button.dataset.calendarAction==='today'){const now=new Date();year=now.getFullYear();month=now.getMonth();selectedCalendarDay=now.toLocaleDateString('en-CA');window.workSelectDay?.(selectedCalendarDay);}
      else {const next=new Date(year,month+(button.dataset.calendarAction==='prev' ? -1 : 1),1);year=next.getFullYear();month=next.getMonth();}
      renderCalendar();
    });
  });
})();

/* File search and chat use the same server capability; paths never become commands. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let token = '', items = [], busy = false, pending = null;
  const requestID = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join('');
  function message(text, error = false) {
    $('workFileStatus').textContent=text;
    $('workFileStatus').classList.toggle('work-error',error);
  }
  function lock() {
    $('workFileDialog').querySelectorAll('input,select,button').forEach(el => {
      if (el.dataset.filesAction === 'close') return;
      el.disabled=busy || (!!pending && el.dataset.filesAction !== 'retry');
    });
    $('workFileRetry').hidden=!pending;
  }
  async function connect() {
    const response=await fetch('/api/work',{cache:'no-store'}), data=await response.json();
    if (!response.ok || !data.action_token) throw Error('연결을 확인한 뒤 다시 시도하세요.');
    token=data.action_token;
  }
  async function search() {
    if (busy || pending) return;
    busy=true;items=[];$('workFileResults').innerHTML='';$('workFileCoverage').textContent='';lock();message('파일과 폴더 이름을 찾고 있어요…');
    try {
      if (!token) await connect();
      const response=await fetch('/api/files/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,query:$('workFileQuery').value.trim(),kind:$('workFileKind').value,limit:50})});
      const data=await response.json();
      if (!response.ok || data.error) {if(response.status===403)token='';throw Error(data.error || '파일 검색을 완료하지 못했습니다.');}
      items=Array.isArray(data.items) ? data.items : [];
      $('workFileResults').innerHTML=items.map((item,index)=>`<article class="work-file-result"><div><strong>${esc(item.name)}</strong><small>${esc(item.kind === 'folder' ? '폴더' : '파일')} · ${esc(item.source || '')}</small><p>${esc(item.path)}</p></div><div class="work-file-result-actions">${item.kind !== 'folder' && /\.(png|jpe?g|webp|gif|mp4|webm|txt|md|rst|csv|tsv|json|log|py|js|ts|tsx|jsx|css|html|xml|yaml|yml|toml|sql|java|kt|cs|go|rs|c|h|cpp|hpp|sh|pdf|docx|hwpx)$/i.test(item.name || item.path || '') ? `<button type="button" class="work-secondary" data-files-action="preview" data-files-index="${index}">미리보기</button>` : ''}<button type="button" class="work-secondary" data-files-action="reveal" data-files-index="${index}">위치 열기</button></div></article>`).join('');
      message(items.length ? `${items.length}개를 표시합니다.${data.truncated ? ' 검색 범위 또는 표시 개수에 제한이 있습니다.' : ''}` : '확인한 범위에서 일치하는 파일이나 폴더를 찾지 못했습니다.');
      const scopes=(data.scopes || []).map(scope=>typeof scope === 'string' ? scope : [scope.source || scope.name || scope.label,scope.path].filter(Boolean).join(' · '));
      $('workFileCoverage').textContent=['검색 범위: '+(scopes.join(' / ') || '서버가 확인한 자료'),...(data.warnings || []).map(String)].join('\n');
    } catch(error) {message(error.message,true);}
    finally {busy=false;lock();}
  }
  async function reveal(item) {
    if (busy) return;
    if (!pending) {
      if (!item?.id || !item.selection_token) {message('검색 결과를 새로 확인하세요.',true);return;}
      pending={id:item.id,selection_token:item.selection_token,request_id:requestID()};
    }
    busy=true;lock();message('탐색기에 위치 표시를 요청하고 있어요…');
    let responseReceived=false;
    try {
      if (!token) await connect();
      const response=await fetch('/api/files/reveal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,...pending})});
      const data=await response.json();responseReceived=true;
      if (!response.ok || data.error) {if(response.status===403)token='';throw Error(data.error || '위치를 열지 못했습니다. 다시 검색해 주세요.');}
      pending=null;message('탐색기에 위치 표시를 요청했습니다.');
    } catch(error) {
      if (responseReceived) pending=null;
      message(responseReceived ? error.message : '위치 열기 결과를 확인하지 못했습니다. 같은 요청 결과를 확인해 주세요.',true);
    } finally {busy=false;lock();}
  }
  function init() {
    const dialog=$('workFileDialog');if (!dialog) return;
    const fileClick=event=>{
      const button=event.target.closest('[data-files-action]');if (!button || button.disabled) return;
      const action=button.dataset.filesAction;
      if (action==='open') {if (document.body.dataset.activeView !== 'work') show('work');if(!dialog.open)dialog.showModal();$('workFileQuery').focus();return;}
      if (action==='close') {dialog.close();return;}
      if (action==='preview') {
        const item=items[Number(button.dataset.filesIndex)];if(!item || busy || pending)return;busy=true;lock();message('미리보기를 준비하고 있어요…');
        (async()=>{try{if(!token)await connect();const response=await fetch('/api/files/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,id:item.id,selection_token:item.selection_token})});const data=await response.json();if(!response.ok || data.error)throw Error(data.error || '미리보기를 열지 못했습니다.');document.dispatchEvent(new CustomEvent('work-stage-preview',{detail:{artifact:data.artifact}}));dialog.close();message('작업 화면에 미리보기를 열었습니다.');}catch(e){message(e.message,true);}finally{busy=false;lock();}})();return;
      }
      if (action==='reveal') {reveal(items[Number(button.dataset.filesIndex)]);return;}
      if (action==='retry' && pending) reveal();
    };
    $('work').addEventListener('click',fileClick);
    $('workUtilities')?.addEventListener('click',fileClick);
    $('workFileForm').addEventListener('submit',event=>{event.preventDefault();search();});
  }
  document.addEventListener('DOMContentLoaded',init);
})();
