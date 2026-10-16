/* Real request steps and guarded result previews. No code or HTML is executed. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let active='',epoch=0,timer=null,attempts=0,failures=0,finished=false,preview=false,artifacts=[],tab='process',last='',pollSerial=0,terminal=null,userSelected=false,resultRevealed=false,requests=[],selectedRequest='',requestsLoaded=false,lastCards='';
  const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function notice(text='') {$('workStageNotice').textContent=text;}
  function select(next,manual=false) {
    if(manual)userSelected=true;
    tab=next;
    $('workStageProcess').hidden=tab!=='process';$('workStageResults').hidden=tab!=='results';
  }
  function mediaURL(value) {
    try {const url=new URL(value,location.origin);return url.origin===location.origin && ['/api/work/media','/api/work/attachment','/api/work/agent-artifact'].includes(url.pathname) && /^[a-f0-9-]{32,36}$/i.test(url.searchParams.get('id') || '') ? url.href : '';}
    catch {return '';}
  }
  function downloadURL(value) {
    try{const url=new URL(value,location.origin);return url.origin===location.origin && ['/api/work/attachment','/api/work/media','/api/work/agent-artifact'].includes(url.pathname) && /^[a-f0-9-]{32,36}$/i.test(url.searchParams.get('id') || '') && url.searchParams.get('download')==='1' ? url.href : '';}
    catch{return '';}
  }
  function content(artifact,expanded=false) {
    const area=document.createElement('div');area.className='work-stage-artifact-content';
    if (['image','video'].includes(artifact.kind)) {
      const url=mediaURL(artifact.url);
      if(!url){area.textContent='미리보기 주소를 확인할 수 없습니다.';return area;}
      const media=document.createElement(artifact.kind==='image' ? 'img' : 'video');media.src=url;
      if(artifact.kind==='image'){media.alt=artifact.title || '이미지 미리보기';media.loading='lazy';}
      else {media.controls=true;media.preload='metadata';media.playsInline=true;}
      media.addEventListener('error',()=>{notice('미리보기가 만료되었거나 지원하지 않는 형식입니다. 파일을 다시 선택해 주세요.');});
      area.append(media);
      if(!expanded && artifact.kind==='image')media.addEventListener('click',()=>expand(artifact));
    } else {
      const pre=document.createElement('pre');pre.className=artifact.kind==='code' ? 'work-stage-code' : 'work-stage-text';
      const code=document.createElement('code');code.textContent=String(artifact.text || '');pre.append(code);area.append(pre);
    }
    return area;
  }
  function renderArtifacts() {
    const root=$('workStageResultContent');if(!root)return;root.replaceChildren();
    if(!artifacts.length){const p=document.createElement('p');p.className='work-stage-empty';p.textContent='별도 결과 파일이 없습니다. 아래에서 실제 작업 내역을 확인할 수 있어요.';root.append(p);return;}
    for(const artifact of artifacts){
      const card=document.createElement('article');card.className='work-stage-artifact';
      const header=document.createElement('header'),title=document.createElement('strong'),badge=document.createElement('small');
      title.textContent=artifact.title || '작업 결과';
      const origin={source:'원본 자료',attachment:'첨부 원본',applied:'실제 적용된 소스 변경',draft:artifact.kind==='code' ? '코드 초안' : '텍스트 초안',summary:'AI 요약',ai:'AI 결과'}[artifact.origin] || (artifact.kind==='code' ? '코드 초안' : '작업 결과');
      badge.textContent=[origin,artifact.language,artifact.truncated ? '일부 표시' : ''].filter(Boolean).join(' · ');header.append(title,badge);card.append(header,content(artifact));
      const actions=document.createElement('div');actions.className='work-stage-artifact-actions';
      const enlarge=document.createElement('button');enlarge.type='button';enlarge.textContent='크게 보기';enlarge.addEventListener('click',()=>expand(artifact));actions.append(enlarge);
      if(['code','text'].includes(artifact.kind)){const copy=document.createElement('button');copy.type='button';copy.textContent='복사';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(String(artifact.text || ''));notice('내용을 복사했습니다.');}catch{notice('복사 권한을 확인해 주세요. 크게 보기에서 텍스트를 선택할 수 있습니다.');}});actions.append(copy);}
      const href=downloadURL(artifact.download_url);
      if(href){const link=document.createElement('a');link.href=href;link.download='';link.className='work-stage-download';link.textContent='다운로드';actions.append(link);}
      else if(['code','text'].includes(artifact.kind)){
        const save=document.createElement('button');save.type='button';save.textContent=artifact.truncated?'표시된 내용 저장':'다운로드';
        save.addEventListener('click',()=>{const extensions={diff:'diff',javascript:'js',python:'py',html:'html',css:'css',json:'json',markdown:'md'};const extension=extensions[artifact.language]||'txt';const name=String(artifact.title||'작업 결과').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,80);const url=URL.createObjectURL(new Blob([String(artifact.text||'')],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=name+'.'+extension;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);});actions.append(save);
      }
      card.append(actions);root.append(card);
    }
    // Results open only when the user chooses a task card or an explicit preview.
  }
  function expand(artifact) {
    const dialog=$('workStageDialog');$('workStageDialogTitle').textContent=artifact.title || '결과 미리보기';$('workStageExpanded').replaceChildren(content(artifact,true));
    if(!dialog.open)dialog.showModal();
  }
  function renderExecutionLog(data){
    const root=$('workStageResultContent');if(!root)return;
    const steps=Array.isArray(data.steps)?data.steps:[],diagnostics=Array.isArray(data.diagnostics)?data.diagnostics:[];
    if(!steps.length&&!diagnostics.length)return;
    const details=document.createElement('details');details.className='work-execution-log';
    details.open=!artifacts.length;
    const summary=document.createElement('summary');summary.textContent='작업 기록';details.append(summary);
    const list=document.createElement('ol');
    for(const step of steps){const item=document.createElement('li'),label=document.createElement('strong'),message=document.createElement('p');label.textContent=(step.label||'작업 처리')+' · '+({running:'진행 중',ok:'완료',error:'오류',pending:'대기',unknown:'확인 필요'}[step.status]||'확인 필요');message.textContent=step.message||'';item.dataset.status=step.status||'unknown';item.append(label,message);list.append(item);}
    const recorded=new Set(steps.map(step=>step.message));
    for(const entry of diagnostics){if(recorded.has(entry.message))continue;const item=document.createElement('li');item.dataset.status=entry.status;item.textContent=entry.message;list.append(item);}
    details.append(list);root.append(details);
  }
  function stop(){clearTimeout(timer);timer=null;}
  function renderRequestCards(){
    const root=$('workStageProcess');requests=requests.slice(0,30);
    const signature=JSON.stringify([selectedRequest,requests.map(item=>[item.request.id,item.summary,item.request.status])]);if(signature===lastCards)return;lastCards=signature;
    const focused=document.activeElement?.dataset?.requestId;root.replaceChildren();
    if(!requests.length){const p=document.createElement('p');p.className='work-stage-empty';p.textContent='요청한 작업이 여기에 표시됩니다.';root.append(p);return;}
    for(const item of requests){const card=document.createElement('button');card.type='button';card.className='work-request-card';card.dataset.requestId=item.request.id;card.dataset.status=item.request.status||'unknown';card.setAttribute('aria-pressed',String(item.request.id===selectedRequest));
      const summary=document.createElement('span');summary.textContent=item.summary||item.request.title||'작업 요청';const badge=document.createElement('small');badge.textContent={running:'진행 중',done:'완료',attention:'확인 필요'}[item.request.status]||'상태 확인 중';card.append(summary,badge);root.append(card);if(focused===item.request.id)card.focus({preventScroll:true});}
  }
  function render(data) {
    const request=data.request;if(!request)return;
    if(!data.workspace_eligible){
      requests=requests.filter(entry=>entry.request.id!==request.id);
      if(selectedRequest===request.id){selectedRequest='';last='';artifacts=[];$('workStageRequest').textContent='';$('workStageStatus').textContent='대기';$('workStageResultContent').replaceChildren();select('process');}
      renderRequestCards();return;
    }
    let item=requests.find(entry=>entry.request.id===request.id);
    const summary=String(request.title||'작업 요청');
    if(!item){item={request,summary,data};requests.unshift(item);}else{item.request=request;item.summary=summary;item.data=data;}
    if(!selectedRequest)selectedRequest=request.id;renderRequestCards();
    if(request.id!==selectedRequest)return;
    const signature=JSON.stringify(data);if(signature===last)return;last=signature;
    $('workStageRequest').textContent=item.summary;$('workStageStatus').textContent={running:'진행 중',done:'완료',attention:'확인 필요'}[request.status]||'상태 확인 중';$('workStage').dataset.status=request.status;
    artifacts=Array.isArray(data.artifacts)?data.artifacts.slice():[];renderArtifacts();
    if(request.summary){const scope=document.createElement('p');scope.className='work-task-scope';scope.textContent=request.summary;$('workStageResultContent').prepend(scope);}
    renderExecutionLog(data);
  }
  async function loadRequest(id){
    if(!id)return;selectedRequest=id;preview=false;userSelected=true;resultRevealed=false;last='';artifacts=[];renderArtifacts();renderRequestCards();notice();
    try{const response=await fetch('/api/work/stage?request_id='+encodeURIComponent(id),{cache:'no-store'});const data=await response.json();if(!response.ok||data.error)throw Error(data.error||'요청을 불러오지 못했습니다.');if(id!==selectedRequest||preview)return;render(data);
      if(data.request?.status==='running'&&finished){active=id;finished=false;terminal=null;attempts=0;failures=0;epoch++;}
      if(!finished)schedule();
    }catch(error){if(id===selectedRequest)notice(error.message);}
  }

  function schedule(delay=1100){stop();if(!document.hidden && !preview && (!finished || !requestsLoaded))timer=setTimeout(()=>poll(epoch),delay);}
  async function poll(version) {
    if(document.hidden || preview || version!==epoch)return;
    const serial=++pollSerial;
    try{
      const response=await fetch('/api/work/stage'+(active ? '?request_id='+encodeURIComponent(active) : ''),{cache:'no-store'}),data=await response.json();
      if(version!==epoch || preview || serial!==pollSerial)return;
      if(!response.ok || data.error)throw Error(data.error || '작업 화면 연결을 확인할 수 없습니다.');
      failures=0;attempts++;
      if(Array.isArray(data.requests)){
        requests=data.requests.filter(item=>item?.request?.id && item.data?.workspace_eligible).slice(0,30);requestsLoaded=true;renderRequestCards();
        if(!active && requests.length){selectedRequest=requests[0].request.id;active=selectedRequest;const chosen=requests[0];render(chosen.data);finished=['done','attention'].includes(chosen.request.status);}
        else if(!requests.length){finished=true;}
      }
      if(data.request && (!active || data.request.id===active)){
        if(!active)active=data.request.id;
        render(data);finished=['done','attention'].includes(data.request.status);notice();
      }else if(!active){finished=true;}
      else if(terminal && attempts>3){finished=true;}
      else if(attempts>30){finished=true;}
      schedule(attempts>12 ? 3000 : 1100);
    }catch(error){
      if(version!==epoch || serial!==pollSerial)return;failures++;notice(error.message);if(failures<5)schedule(Math.min(10000,1500*failures));
      else{$('workStageStatus').textContent='연결 확인 필요';}
    }
  }
  function start(detail) {
    if(!detail.requestId)return;
    if(detail.phase==='start'){
      const same=active===detail.requestId;active=detail.requestId;epoch++;stop();attempts=0;failures=0;finished=false;preview=false;terminal=null;
      // A chat submission is not evidence of work. Wait for the server's task eligibility.
      poll(epoch);return;
    }
    if(detail.requestId!==active || preview)return;
    if(detail.phase==='uncertain'){schedule(2000);}
    else if(detail.phase==='done' || detail.phase==='error'){
      terminal=detail;finished=false;attempts=0;poll(epoch);
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if(!$('workStage'))return;
    $('workStage').addEventListener('click',event=>{const card=event.target.closest('.work-request-card');if(card){select('results',true);loadRequest(card.dataset.requestId);return;}if(event.target.closest('[data-stage-action="back"]')){select('process',true);return;}});
    $('workStageDialog').querySelector('[data-stage-action="close"]').addEventListener('click',()=>$('workStageDialog').close());
    $('workStageDialog').addEventListener('close',()=>{$('workStageExpanded').querySelectorAll('video').forEach(video=>{video.pause();video.removeAttribute('src');video.load();});$('workStageExpanded').replaceChildren();});
    document.addEventListener('work-stage-request',event=>start(event.detail || {}));
    document.addEventListener('work-stage-notice',event=>{notice(event.detail?.message||'');});
    document.addEventListener('work-stage-preview',event=>{
      const artifact=event.detail?.artifact;if(!artifact)return;epoch++;stop();preview=true;finished=true;artifacts=[artifact];renderArtifacts();select('results');
      $('workStageRequest').textContent=artifact.title || '선택한 자료';$('workStageStatus').textContent='미리보기';$('workStage').dataset.status='preview';notice();$('workStage').focus({preventScroll:true});$('workStage').scrollIntoView({block:'nearest',behavior:'smooth'});
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(!finished && !preview){failures=0;poll(epoch);}});
    poll(epoch);
  });
})();
