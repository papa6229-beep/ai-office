/* Read-only feedback from the actual tab hosting AI OFFICE. No model scripts. */
(() => {
  'use strict';
  const build=JSON.parse(document.querySelector('meta[name="office-build"]')?.content||'null');
  if(!build)return;
  let client=sessionStorage.getItem('office-client-id');
  if(!/^[a-f0-9]{32}$/.test(client||'')){client=crypto.randomUUID().replaceAll('-','');sessionStorage.setItem('office-client-id',client);}
  // Duplicated tabs can inherit sessionStorage; only the newer tab rebinds.
  if(typeof BroadcastChannel==='function'){
    const channel=new BroadcastChannel('office-client-presence'),instance=Date.now()+':'+crypto.randomUUID();
    channel.onmessage=event=>{const value=event.data;if(value?.id!==client||value.instance===instance)return;
      if(instance>value.instance){client=crypto.randomUUID().replaceAll('-','');sessionStorage.setItem('office-client-id',client);}
      else channel.postMessage({id:client,instance});};
    channel.postMessage({id:client,instance});
    addEventListener('pagehide',()=>channel.close(),{once:true});
  }
  const errors=[];let token='',inFlight=false,timer=null;const seenStates=new Map();
  const short=(value,n=160)=>String(value||'').replace(/\s+/g,' ').trim().slice(0,n);
  const publicURL=value=>{try{const url=new URL(value,location.href);url.search='';url.hash='';url.username='';url.password='';return url.href.slice(0,500);}catch{return '';}};
  const visible=element=>{const r=element.getBoundingClientRect(),s=getComputedStyle(element);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&!element.closest('[hidden],.hide');};
  function selector(element){
    if(element.id)return '#'+CSS.escape(element.id);
    for(const key of ['data-launcher-id','data-work-action','data-launcher-action'])if(element.hasAttribute(key))return element.tagName.toLowerCase()+'['+key+'="'+CSS.escape(element.getAttribute(key))+'"]';
    if(element.classList.contains('nav'))return '.side>.nav:nth-child('+(Array.from(element.parentNode.children).indexOf(element)+1)+')';
    return element.tagName.toLowerCase();
  }
  function capture(){
    const elements=[];
    for(const element of document.querySelectorAll('.side .nav,main h1,main h2,main h3,main button,main a,[role=dialog]')){
      if(elements.length>=100)break;
      if(!visible(element)||element.closest('#workMessages,#workStageResultContent'))continue;
      elements.push({selector:selector(element),tag:element.tagName.toLowerCase(),role:element.getAttribute('role')||'',
        label:short(element.getAttribute('aria-label')||element.innerText),href:element.hasAttribute('href')?publicURL(element.href).slice(0,300):''});
    }
    return {id:client,version:build,url:publicURL(location.href),title:short(document.title),
      view:document.body.dataset.activeView||'',visible:!document.hidden,focused:document.hasFocus(),
      viewport:{width:innerWidth,height:innerHeight,scale:devicePixelRatio,x:screenX,y:screenY,outer_width:outerWidth,outer_height:outerHeight},
      elements,errors:errors.slice(-8)};
  }
  function evaluate(check){
    try{
      const nodes=Array.from(document.querySelectorAll(check.selector));
      const result={count:nodes.length};
      if(check.kind==='visible')result.visible=nodes.some(visible);
      if(check.kind==='text')result.text=short(nodes.map(x=>x.innerText||x.textContent||'').join('\n'),2500);
      if(check.kind==='attribute')result.attribute=nodes[0]?.getAttribute(check.attribute)??null;
      return result;
    }catch{return {error:'화면 요소 선택 조건을 읽지 못했습니다.'};}
  }
  async function session(){
    const response=await fetch('/api/work',{cache:'no-store'}),data=await response.json();
    if(!response.ok||!data.action_token)throw Error('화면 연결 대기');token=data.action_token;
  }
  async function send(report){
    if(!token)await session();
    const response=await fetch('/api/work/feedback',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token,client:capture(),...(report?{report}:{})})});
    if(response.status===403){token='';return null;}
    const result=await response.json();if(!response.ok)return null;
    if(result.accepted)document.dispatchEvent(new CustomEvent('work-stage-verification',{detail:result.accepted}));
    for(const state of result.states||[]){
      const previous=seenStates.get(state.request_id);seenStates.set(state.request_id,state.state);
      if(previous!==undefined&&previous!==state.state)document.dispatchEvent(new CustomEvent('work-stage-verification',{detail:state}));
    }
    if(result.needs_reload&&!document.hidden){
      const command=document.getElementById('workCommand');if(command?.value)sessionStorage.setItem('ai-office-command-draft',command.value);
      location.reload();
    }
    return result;
  }
  async function pulse(){
    clearTimeout(timer);if(inFlight)return;inFlight=true;
    try{
      const result=await send();
      for(const job of result?.jobs||[]){
        if(document.hidden)break;
        await send({request_id:job.request_id,nonce:job.nonce,actuals:job.checks.map(evaluate)});
      }
    }catch{/* A source restart can briefly close the connection. */}
    finally{inFlight=false;timer=setTimeout(pulse,document.hidden?5000:2000);}
  }
  window.officeUI={capture};
  addEventListener('error',event=>{errors.push(short(event.message,300));if(errors.length>8)errors.shift();});
  addEventListener('unhandledrejection',()=>{errors.push('처리되지 않은 화면 오류');if(errors.length>8)errors.shift();});
  document.addEventListener('visibilitychange',()=>{if(!inFlight)pulse();});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',pulse);else pulse();
})();
