/* Real request steps and guarded result previews. No code or HTML is executed. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let active='',epoch=0,timer=null,attempts=0,failures=0,finished=false,preview=false,artifacts=[],tab='process',last='',pollSerial=0,terminal=null,userSelected=false,resultRevealed=false;
  const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function notice(text='') {$('workStageNotice').textContent=text;}
  function select(next,manual=false) {
    if(manual)userSelected=true;
    tab=next;
    $('workStageProcess').hidden=tab!=='process';$('workStageResults').hidden=tab!=='results';
    $('workStage').querySelectorAll('[data-stage-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.stageTab===tab)));
  }
  function mediaURL(value) {
    try {const url=new URL(value,location.origin);return url.origin===location.origin && url.pathname==='/api/work/media' && url.searchParams.has('id') ? url.href : '';}
    catch {return '';}
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
    const root=$('workStageResults');root.replaceChildren();$('workStageCount').textContent=String(artifacts.length);
    if(!artifacts.length){const p=document.createElement('p');p.className='work-stage-empty';p.textContent='파일 찾기의 ‘미리보기’나 업무에 연결된 자료를 열어 확인하세요.';root.append(p);return;}
    for(const artifact of artifacts){
      const card=document.createElement('article');card.className='work-stage-artifact';
      const header=document.createElement('header'),title=document.createElement('strong'),badge=document.createElement('small');
      title.textContent=artifact.title || '작업 결과';
      const origin={source:'원본 자료',applied:'실제 적용된 소스 변경',draft:artifact.kind==='code' ? '코드 초안' : '텍스트 초안',summary:'AI 요약',ai:'AI 결과'}[artifact.origin] || (artifact.kind==='code' ? '코드 초안' : '작업 결과');
      badge.textContent=[origin,artifact.language,artifact.truncated ? '일부 표시' : ''].filter(Boolean).join(' · ');header.append(title,badge);card.append(header,content(artifact));
      const actions=document.createElement('div');actions.className='work-stage-artifact-actions';
      const enlarge=document.createElement('button');enlarge.type='button';enlarge.textContent='크게 보기';enlarge.addEventListener('click',()=>expand(artifact));actions.append(enlarge);
      if(['code','text'].includes(artifact.kind)){const copy=document.createElement('button');copy.type='button';copy.textContent='복사';copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(String(artifact.text || ''));notice('내용을 복사했습니다.');}catch{notice('복사 권한을 확인해 주세요. 크게 보기에서 텍스트를 선택할 수 있습니다.');}});actions.append(copy);}
      card.append(actions);root.append(card);
    }
    if(artifacts.length && !resultRevealed){resultRevealed=true;if(!userSelected)select('results');}
  }
  function expand(artifact) {
    const dialog=$('workStageDialog');$('workStageDialogTitle').textContent=artifact.title || '결과 미리보기';$('workStageExpanded').replaceChildren(content(artifact,true));
    if(!dialog.open)dialog.showModal();
  }
  function stop(){clearTimeout(timer);timer=null;}
  function render(data) {
    const signature=JSON.stringify(data);if(signature===last)return;last=signature;
    const request=data.request;
    if(request){$('workStageRequest').textContent=request.title || '요청 처리';$('workStageStatus').textContent={running:'진행중',done:'처리 종료',attention:'확인 필요'}[request.status] || '상태 확인 중';$('workStage').dataset.status=request.status;}
    const steps=Array.isArray(data.steps) ? data.steps : [];
    $('workStageProcess').innerHTML=steps.length ? '<ol>'+steps.map((step,index)=>`<li data-status="${esc(step.status)}"><div class="work-stage-step-number">${String(index+1).padStart(2,'0')}</div><div><div class="work-stage-step-heading"><strong>${esc(step.label || step.name || '처리 단계')}</strong><span>${esc({running:'진행중',ok:'완료',error:'오류',pending:'대기',unknown:'확인 필요'}[step.status] || '확인 필요')}</span></div>${step.message ? `<pre>${esc(step.message)}</pre>` : ''}</div></li>`).join('')+'</ol>' : '<p class="work-stage-empty">'+(request?.status==='running' ? '요청을 받았습니다. 실제 처리 단계를 기다리고 있어요.' : '표시할 처리 단계가 없습니다.')+'</p>';
    artifacts=Array.isArray(data.artifacts) ? data.artifacts.slice() : [];
    renderArtifacts();
  }
  function schedule(delay=1100){stop();if(!document.hidden && !preview && !finished)timer=setTimeout(()=>poll(epoch),delay);}
  async function poll(version) {
    if(document.hidden || preview || version!==epoch)return;
    const serial=++pollSerial;
    try{
      const response=await fetch('/api/work/stage'+(active ? '?request_id='+encodeURIComponent(active) : ''),{cache:'no-store'}),data=await response.json();
      if(version!==epoch || preview || serial!==pollSerial)return;
      if(!response.ok || data.error)throw Error(data.error || '작업 화면 연결을 확인할 수 없습니다.');
      failures=0;attempts++;
      if(data.request && (!active || data.request.id===active)){
        if(!active)active=data.request.id;
        render(data);finished=['done','attention'].includes(data.request.status);notice();
      }else if(!active){finished=true;}
      else if(terminal && attempts>3){
        finished=true;$('workStageStatus').textContent=terminal.phase==='done' ? '대기' : '확인 필요';$('workStage').dataset.status=terminal.phase==='done' ? 'done' : 'attention';
        artifacts=[];renderArtifacts();
        notice('이 요청에는 별도의 작업 결과물이 없습니다. 답변은 채팅에서 확인하세요.');
      }
      else if(attempts>30){finished=true;$('workStageStatus').textContent='상태 확인 필요';notice('처리 단계가 도착하지 않았습니다. 채팅에서 요청 결과를 확인해 주세요.');}
      else if(attempts>12){notice('처리 단계가 아직 도착하지 않았습니다. 요청 결과를 기다립니다.');}
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
      if(!same){userSelected=false;resultRevealed=false;}
      if(!same){last='';artifacts=[];renderArtifacts();$('workStageProcess').innerHTML='<p class="work-stage-empty">요청을 받았습니다. 실제 처리 단계를 기다리고 있어요.</p>';}
      $('workStageRequest').textContent=detail.message || '요청 처리';$('workStageStatus').textContent='진행중';$('workStage').dataset.status='running';notice();if(!same)select('process');poll(epoch);return;
    }
    if(detail.requestId!==active || preview)return;
    if(detail.phase==='uncertain'){notice('응답이 불확실합니다. 같은 요청의 진행 상태를 확인하고 있어요.');schedule(2000);}
    else if(detail.phase==='done' || detail.phase==='error'){
      terminal=detail;finished=false;attempts=0;poll(epoch);
      if(detail.phase==='error'){notice(detail.result?.message || '요청을 확인해 주세요.');$('workStageStatus').textContent='확인 필요';}
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if(!$('workStage'))return;
    $('workStage').addEventListener('click',event=>{const button=event.target.closest('[data-stage-tab]');if(button)select(button.dataset.stageTab,true);});
    $('workStageDialog').querySelector('[data-stage-action="close"]').addEventListener('click',()=>$('workStageDialog').close());
    $('workStageDialog').addEventListener('close',()=>{$('workStageExpanded').querySelectorAll('video').forEach(video=>{video.pause();video.removeAttribute('src');video.load();});$('workStageExpanded').replaceChildren();});
    document.addEventListener('work-stage-request',event=>start(event.detail || {}));
    document.addEventListener('work-stage-preview',event=>{
      const artifact=event.detail?.artifact;if(!artifact)return;epoch++;stop();preview=true;finished=true;artifacts=[artifact];renderArtifacts();select('results');
      $('workStageRequest').textContent=artifact.title || '선택한 자료';$('workStageStatus').textContent='미리보기';$('workStage').dataset.status='preview';notice();$('workStage').focus({preventScroll:true});$('workStage').scrollIntoView({block:'nearest',behavior:'smooth'});
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else if(!finished && !preview){failures=0;poll(epoch);}});
    poll(epoch);
  });
})();
