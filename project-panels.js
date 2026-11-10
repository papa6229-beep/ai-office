/* Existing Codex conversations, without opening external windows. */
(() => {
  'use strict';
  const panels=new Map(),$=id=>document.getElementById(id);
  let catalog=[],picker=null,dock=null,layer=null,z=600,catalogLoading=false,browseMode='departments',pickerMode='threads',catalogError='',actionToken='';
  let departments=[],departmentLoading=false,departmentError='',departmentRegistryAt=0,departmentNext=0,departmentChatRoutes=false;
  let supervisorRoutes={},mainRouting=null;
  const departmentStates=new Map();
  const questionStates=new Map(),questionSeen=new Set();let questionAlerts=null,questionBusy=false,questionNext=0,questionAlertSignature='';
  try{for(const id of JSON.parse(localStorage.getItem('office-project-questions-seen-v1')||'[]').slice(-200))if(typeof id==='string')questionSeen.add(id);}catch(_){}
  const departmentSeen=new Map(),departmentSeenKey='office-department-completions-seen-v1';
  const departmentMotion=matchMedia('(prefers-reduced-motion: reduce)');
  const dockVisibilityKey='office-project-dock-collapsed-v1';let dockCollapsed=true;
  try{dockCollapsed=localStorage.getItem(dockVisibilityKey)!=='false';}catch(_){}
  function loadDepartmentSeen(raw){
    try{const data=JSON.parse(raw||'{}');if(!data||Array.isArray(data)||typeof data!=='object')return;
      departmentSeen.clear();for(const [id,completion] of Object.entries(data).slice(-200))if(id.length<300&&typeof completion==='string'&&completion.length<1000)departmentSeen.set(id,completion);
    }catch(_){}
  }
  try{loadDepartmentSeen(localStorage.getItem(departmentSeenKey));}catch(_){}
  const provider=p=>p?.embedded&&p.title?p.title:'Codex';
  const sourceRows=()=>catalog.filter(row=>(row.kind||'codex')==='codex');
  const key=row=>(row.host||'local')+'|'+row.id;
  const departmentWindow=row=>!!row.department||departments.some(item=>key(item)===key(row));
  const routedWindow=p=>p.host==='local'&&(p.mainComposer?p.id===supervisorRoutes.main:
    departmentChatRoutes&&(departmentWindow(p)||supervisorRoutes.coordinators?.includes(p.id)));
  const gptId=/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  const gptKey=p=>'office-department-gpt-v1:'+key(p);
  function windowLimit(row){
    const department=departmentWindow(row),count=[...panels.values()].filter(p=>!p.embedded&&departmentWindow(p)===department).length;
    return count>=(department?8:4)?(department?'부서 작업창은 여덟 개까지 열 수 있어요. 아래 작업창에서 접힌 창을 펼치거나 ×로 닫아 주세요.':'일반 프로젝트 작업창은 네 개까지 열 수 있어요. 아래 작업창에서 접힌 창을 펼치거나 ×로 닫아 주세요. 입력과 첨부는 보관돼요.'):'';
  }
  const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  const clamp=(v,low,high)=>Math.min(Math.max(v,low),Math.max(low,high));
  const uid=()=>crypto.randomUUID().replaceAll('-','');
  function save(){
    try{localStorage.setItem('office-project-panels',JSON.stringify([...panels.values()].filter(p=>!p.embedded).map(p=>({id:p.id,host:p.host,kind:p.kind||'codex',title:p.title,project:p.project,department:departments.find(row=>key(row)===key(p))?.department||p.department||'',minimized:p.minimized,completion:p.completion,watchPrimed:p.watchPrimed,unseen:p.unseen,x:p.el.hidden?parseFloat(p.el.style.left):p.el.offsetLeft,y:p.el.hidden?parseFloat(p.el.style.top):p.el.offsetTop,width:p.el.hidden?parseFloat(p.el.style.width):p.el.offsetWidth,height:p.el.hidden?parseFloat(p.el.style.height):p.el.offsetHeight}))));}catch(_){}
  }
  function draftKey(p){return 'office-project-draft:'+key(p);}
  function persistOwn(p){try{sessionStorage.setItem(draftKey(p)+':own',JSON.stringify(p.own.slice(-40)));}catch(_){} }
  function reconcileOwn(p){
    const matched=new Set(p.own.map(m=>m.sourceId).filter(Boolean));let changed=false;
    for(const local of p.own){
      if(local.sourceId)continue;
      const anchor=p.messages.findIndex(m=>m.id===local.anchor);
      if(local.anchor&&anchor<0)continue;
      const source=p.messages.find((m,index)=>index>anchor&&m.role==='user'&&m.text.trim()===local.text.trim()&&!matched.has(m.id)&&
        (anchor>=0||!local.created_at||!m.turn_started_at||m.turn_started_at>=local.created_at-2));
      if(!source)continue;
      matched.add(source.id);local.sourceId=source.id;changed=true;
      for(const next of p.own)if(next.anchor===local.id)next.anchor=source.id;
    }
    if(changed)persistOwn(p);
  }
  function displayMessages(p){
    const rows=[...p.messages];
    for(const local of p.own){
      if(rows.some(m=>m.id===local.id))continue;
      if(local.sourceId&&rows.some(m=>m.id===local.sourceId))continue;
      const anchor=rows.findIndex(m=>m.id===local.anchor);
      // Keep delivery state, but show an old acknowledged echo only once its
      // source history is loaded. Pending/uncertain sends remain visible.
      if(local.delivery==='sent'&&((!p.ready&&!p.messages.length)||
        (anchor<0&&Number.isFinite(p.historyStart)&&Number.isFinite(local.created_at)&&local.created_at<p.historyStart-2)))continue;
      let index=anchor+1;
      if(anchor<0){
        // An older relay can be absent from the source's short live page.
        // Never pin those old delivery records after every newer live reply.
        index=local.created_at?rows.findIndex(m=>Number(m.turn_started_at)>=local.created_at-1):-1;
        if(index<0)index=local.created_at?rows.length:0;
      }
      rows.splice(index,0,local);
    }
    return rows;
  }
  function orderDeliveredMessages(messages){
    const delivered=m=>m.role==='user'&&m.id.startsWith('local:')&&Number.isFinite(m.turn_started_at);
    const receipts=messages.filter(delivered).sort((a,b)=>(a.delivered_at??a.turn_started_at)-(b.delivered_at??b.turn_started_at));
    if(!receipts.length)return messages;
    const rows=messages.filter(m=>!delivered(m));
    for(const receipt of receipts){
      const anchor=receipt.anchor?rows.findIndex(m=>m.id===receipt.anchor):-1;
      let at=-1;
      const sameTurn=receipt.turn_id&&Number.isFinite(receipt.position)?rows.map((m,index)=>({m,index})).filter(({m})=>!delivered(m)&&m.turn_id===receipt.turn_id):[];
      if(sameTurn.length){
        at=sameTurn.find(({m})=>Number.isFinite(m.position)&&m.position>receipt.position)?.index??sameTurn.at(-1).index+1;
      }else at=anchor>=0?anchor+1:rows.findIndex(m=>!delivered(m)&&Number.isFinite(m.turn_started_at)&&m.turn_started_at>=Math.floor(receipt.turn_started_at));
      if(at<0)at=rows.length;
      while(at<rows.length&&delivered(rows[at])&&rows[at].turn_started_at<=receipt.turn_started_at)at++;
      rows.splice(at,0,receipt);
    }
    return rows;
  }
  function mergeSource(p,incoming,older){
    const byId=new Map(p.messages.map(m=>[m.id,m])),order=p.messages.map(m=>m.id);
    if(older){
      const ids=[];
      for(const m of incoming){byId.set(m.id,{...byId.get(m.id),...m});if(!order.includes(m.id))ids.push(m.id);}
      order.unshift(...ids);
    }else{
      for(let n=0;n<incoming.length;n++){
        const m=incoming[n];byId.set(m.id,{...byId.get(m.id),...m});
        if(order.includes(m.id))continue;
        let index=-1;
        for(let j=n-1;j>=0;j--){const found=order.indexOf(incoming[j].id);if(found>=0){index=found+1;break;}}
        if(index<0)for(let j=n+1;j<incoming.length;j++){const found=order.indexOf(incoming[j].id);if(found>=0){index=found;break;}}
        order.splice(index<0?order.length:index,0,m.id);
      }
    }
    const seen=new Set();p.messages=orderDeliveredMessages(order.filter(id=>{if(seen.has(id))return false;seen.add(id);return true;}).map(id=>byId.get(id)));
  }
  function settleConfirmedSend(p,pending){
    if(panels.get(key(p))!==p||!pending||p.pending?.request_id!==pending.request_id)return false;
    if(p.input.value===pending.message)p.input.value='';
    const sentFiles=new Set(pending.attachment_ids||[]);
    p.files=p.files.filter(file=>!sentFiles.has(file.id));
    p.pending=null;if(!p.files.length)p.uploadGroup=uid();
    if(p.routeMode)p.routeMode.value='auto';
    try{sessionStorage.removeItem(draftKey(p)+':pending');}catch(_){}
    persistFiles(p);drawFiles(p);persistDraft(p);return true;
  }
  function reconcileReceipts(p,receipts=[]){
    p.receipts=receipts;
    let changed=false;
    for(const row of receipts){
      const id='local:'+row.request_id,local=p.own.find(m=>m.id===id);
      if(local){
        if(local.created_at!==row.created_at){local.created_at=row.created_at;changed=true;}
        if(row.state==='sent'&&local.delivery!=='sent'){local.delivery='sent';changed=true;}
      }
      const source=p.messages.find(m=>m.id===id);
      if(row.state==='sent'&&source)source.delivery='sent';
    }
    if(changed)persistOwn(p);
    // A late HTTP acknowledgement can arrive through history polling instead.
    // Match its original request ID; never clear a newer draft or resend here.
    const pending=p.pending;
    if(!p.posting&&pending&&(receipts.some(row=>row.request_id===pending.request_id&&row.state==='sent')||
      p.messages.some(m=>m.id==='local:'+pending.request_id&&m.delivery==='sent'))&&settleConfirmedSend(p,pending)){
      notice(p,'기존 대화에 전달된 것을 확인했어요.');
      return true;
    }
    return false;
  }
  function ownMessage(p,message,state){
    const id='local:'+p.pending.request_id;
    let row=p.own.find(m=>m.id===id);
    if(!row){row={id,role:'user',text:message||'첨부 파일',attachments:p.files.map(f=>({...f})),anchor:displayMessages(p).at(-1)?.id||'',created_at:Date.now()/1000,delivery:state};p.own.push(row);}
    row.delivery=state;p.own=p.own.slice(-40);persistOwn(p);drawMessages(p,true);
  }
  function persistDraft(p){try{sessionStorage.setItem(draftKey(p),p.input.value);}catch(_){}sendLabel(p);}
  function sendLabel(p){
    p.send.textContent=p.posting?'전달 중…':p.pending?.message===p.input.value?'전달 상태 확인':'보내기';
    p.send.disabled=p.posting||p.uploading||!p.ready||p.files.some(f=>!f.id||f.error)||(!p.input.value.trim()&&!p.files.length);
    if(p.attach)p.attach.disabled=p.posting||p.uploading||p.host!=='local'||!!p.pending?.attachment_ids?.length||!!p.pending?.route_mode;
    p.input.readOnly=!!p.pending?.route_mode;
    if(p.routeMode){p.routeMode.disabled=p.posting||!!p.pending;p.gptBind.disabled=p.posting||!!p.pending;}
  }
  function persistFiles(p){try{sessionStorage.setItem(draftKey(p)+':files',JSON.stringify({request_id:p.uploadGroup,items:p.files}));}catch(_){} }
  function attachmentCards(container,files){
    container.replaceChildren();container.hidden=!files?.length;
    for(const file of files||[]){
      window.officeChatContent?.attachment(container,file);
      const link=node('a','project-attachment',file.name||'첨부 파일');
      if(/^[a-f0-9]{32}$/.test(file.id||'')){link.href='/api/work/attachment?id='+file.id+'&download=1';link.target='_blank';link.rel='noopener noreferrer';}
      container.append(link);
    }
  }
  function drawFiles(p){
    p.filesNode.replaceChildren();p.filesNode.hidden=!p.files.length;
    for(const file of p.files){
      const row=node('span','project-file-draft'),name=node('span','',file.name+(file.error?' · 업로드 실패':!file.id?' · 올리는 중…':'')),remove=node('button','','×');
      remove.type='button';remove.setAttribute('aria-label',file.name+' 첨부 제거');remove.disabled=p.posting||p.uploading||!!p.pending?.attachment_ids?.length;
      remove.onclick=async()=>{try{if(file.id)await request('upload-remove',{request_id:p.uploadGroup,attachment_id:file.id});p.files=p.files.filter(f=>f!==file);persistFiles(p);drawFiles(p);}catch(error){notice(p,error.message,true);}};
      row.append(name,remove);p.filesNode.append(row);
    }
    sendLabel(p);
  }
  async function addFiles(p,files){
    const incoming=Array.from(files||[]);if(!incoming.length||p.posting||p.uploading)return;
    if(p.host!=='local'){notice(p,'원격 작업창 첨부는 해당 PC의 Codex에서 올려 주세요.',true);return;}
    if(p.pending?.attachment_ids?.length||p.pending?.route_mode){notice(p,'이전 전송의 상태를 먼저 확인해 주세요. 첨부는 아직 추가하지 않았어요.',true);return;}
    if(p.files.length+incoming.length>6||p.files.reduce((s,f)=>s+f.size,0)+incoming.reduce((s,f)=>s+f.size,0)>30*1024*1024){notice(p,'첨부는 최대6개·합계30MB까지 가능해요.',true);return;}
    if(incoming.some(f=>!f.size||f.size>10*1024*1024)){notice(p,'파일은 하나당10MB 이하로 올려 주세요.',true);return;}
    p.uploading=true;notice(p,'첨부를 올리고 있어요.');
    try{
      for(const file of incoming){
        const item={upload_id:uid(),name:file.name,size:file.size};p.files.push(item);drawFiles(p);
        try{
          const dataURL=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('파일을 읽지 못했어요.'));reader.readAsDataURL(file);});
          const data=await request('upload',{request_id:p.uploadGroup,upload_id:item.upload_id,name:file.name,data_url:dataURL});
          if(!data.attachment?.id)throw Error('첨부 저장 결과를 확인하지 못했어요.');Object.assign(item,data.attachment);
        }catch(error){item.error=error.message;notice(p,error.message,true);}
        persistFiles(p);
      }
    }finally{p.uploading=false;drawFiles(p);if(p.files.every(f=>f.id&&!f.error))notice(p,'첨부 준비가 끝났어요. 보내기를 누르면 함께 전달돼요.');}
  }
  function notice(p,text,error=false){p.note.textContent=text;p.note.classList.toggle('is-error',error);}
  function questionDraftKey(p,call){return draftKey(p)+':question:'+call;}
  function rememberQuestionAlert(id){
    questionSeen.add(id);while(questionSeen.size>200)questionSeen.delete(questionSeen.values().next().value);
    try{localStorage.setItem('office-project-questions-seen-v1',JSON.stringify([...questionSeen]));}catch(_){}
    renderQuestionAlerts();
  }
  function renderQuestionAlerts(){
    if(!questionAlerts)return;
    const rows=[];
    for(const row of questionStates.values())for(const group of [...(row.questions||[]),...(row.native_approval?[{id:row.native_approval.id,questions:[{text:'Codex 실행 승인 요청 · '+row.native_approval.text}]}]:[])]){
      const id=key(row)+'|'+group.id;
      if(!group.delivery&&!questionSeen.has(id))rows.push({row,group,id});
    }
    const signature=JSON.stringify(rows.map(x=>[x.id,x.group.questions]));if(signature===questionAlertSignature)return;questionAlertSignature=signature;
    questionAlerts.replaceChildren();questionAlerts.hidden=!rows.length;
    if(!rows.length)return;
    questionAlerts.append(node('strong','','답변이 필요한 질문 '+rows.length+'건'));
    for(const {row,group,id} of rows.slice(0,8)){
      const card=node('div','project-question-alert'),show=node('button','project-question-alert-open'),dismiss=node('button','project-question-alert-dismiss','×');show.type=dismiss.type='button';
      show.append(node('strong','',row.title),node('span','',group.questions[0]?.text||'새 질문'));
      show.onclick=()=>{try{window.officeProjectOpen(row);if(panels.has(key(row)))rememberQuestionAlert(id);}catch(error){show.title=error.message;}};
      dismiss.setAttribute('aria-label',row.title+'의 질문 알림 접기');dismiss.onclick=()=>rememberQuestionAlert(id);card.append(show,dismiss);questionAlerts.append(card);
    }
  }
  function drawQuestions(p,groups=[]){
    if(!p.promptArea)return;
    const nativeApproval=p.nativeApproval;
    const signature=JSON.stringify([groups,nativeApproval]);if(p.questionSignature===signature)return;p.questionSignature=signature;
    const active=p.promptArea.contains(document.activeElement)?document.activeElement:null;
    const focused=active?.dataset.questionField,selection=active&&'selectionStart' in active?[active.selectionStart,active.selectionEnd]:null;
    p.promptArea.replaceChildren();p.promptArea.hidden=!groups.length&&!nativeApproval;
    for(const group of groups){
      const form=node('form','project-question-card');form.append(node('strong','','질문 · '+p.title));if(group.created)form.append(node('small','',new Date(group.created).toLocaleString('ko-KR')));
      const storageKey=questionDraftKey(p,group.id);let saved={};try{saved=JSON.parse(sessionStorage.getItem(storageKey)||'{}');}catch(_){}
      const fields=[];let pending=saved.pending?.id===p.id&&saved.pending?.host===p.host&&saved.pending?.question_id===group.id?saved.pending:null;
      const persist=()=>{try{sessionStorage.setItem(storageKey,JSON.stringify({answers:fields.map(x=>x.value),pending}));}catch(_){};};
      for(const [index,q] of group.questions.entries()){
        const fieldset=node('fieldset'),legend=node('legend','',q.text),input=node('textarea');input.rows=2;input.maxLength=3000;input.placeholder='여기에 답변을 입력하세요';input.setAttribute('aria-label',q.text);input.dataset.questionField=group.id+':'+index;input.value=saved.answers?.[index]||'';
        input.disabled=!!group.delivery||!!pending;input.oninput=persist;fieldset.append(legend);
        if(q.options?.length){const choices=node('div','project-question-choices');for(const label of q.options){const choice=node('button','',label);choice.type='button';choice.disabled=!!group.delivery||!!pending;choice.onclick=()=>{input.value=label;persist();input.focus({preventScroll:true});};choices.append(choice);}fieldset.append(choices);}
        fields.push(input);fieldset.append(input);form.append(fieldset);
      }
      const note=node('small','',group.delivery?'답변 전달 여부를 확인 중입니다. 같은 답변을 자동으로 다시 보내지 않습니다.':group.replyable?'답변은 이 요원의 원래 대화로 전달됩니다.':group.transport==='chat'?'질문 수신 상태를 갱신 중입니다.':'원래 Codex 화면에서 응답해야 하는 요청입니다.');form.append(note);
      const submit=node('button','project-question-submit',group.delivery?'전달 확인 중':pending?'전달 상태 확인':'답변 보내기');submit.type='submit';submit.disabled=!group.replyable;form.append(submit);
      form.onsubmit=async event=>{
        event.preventDefault();if(submit.disabled)return;
        const answers=fields.map(x=>x.value.trim());if(answers.some(x=>!x)){note.textContent='각 질문에 답변을 입력하거나 선택해 주세요.';return;}
        if(!pending)pending={id:p.id,host:p.host,question_id:group.id,request_id:uid(),answers};persist();submit.disabled=true;submit.textContent='전달 중…';for(const field of fields)field.disabled=true;
        try{
          const data=await request('project-panel-question-answer',pending);
          if(data.state==='sent'){try{sessionStorage.removeItem(storageKey);}catch(_){}notice(p,'질문 답변을 원래 대화에 전달했어요.');questionNext=0;await pollQuestions();await refresh(p);}
          else{note.textContent='전달 여부를 확인 중입니다. 같은 답변을 자동으로 다시 보내지 않습니다.';submit.textContent='전달 확인 중';for(const f of fields)f.disabled=true;}
        }catch(error){note.textContent=error.message;submit.disabled=false;submit.textContent='전달 상태 확인';}
      };
      p.promptArea.append(form);
    }
    if(nativeApproval){
      const card=node('section','project-question-card is-native-approval');card.append(node('strong','','Codex 실행 승인 대기'),node('p','',nativeApproval.text),node('p','','이 승인은 Codex의 실제 승인 화면에서 처리해야 합니다.'));
      const open=node('button','project-question-submit','원래 승인 요청 열기');open.type='button';open.onclick=()=>p.native.click();card.append(open);p.promptArea.append(card);
    }
    if(focused){const next=[...p.promptArea.querySelectorAll('textarea')].find(e=>e.dataset.questionField===focused);if(next){next.focus({preventScroll:true});if(selection)next.setSelectionRange(...selection);}}
  }
  function receiveQuestions(row,data){
    if(!Array.isArray(data.questions))return;
    const prior=questionStates.get(key(row));
    const groups=data.available===false||data.question_source_available===false?(prior?.questions||[]):data.questions;
    const native=Object.hasOwn(data,'native_approval')?data.native_approval:prior?.native_approval;
    const changed=JSON.stringify([prior?.questions,prior?.native_approval])!==JSON.stringify([groups,native]);
    questionStates.set(key(row),{...prior,...row,questions:groups,native_approval:native});
    const panel=panels.get(key(row));if(panel){panel.nativeApproval=native;drawQuestions(panel,groups);}renderQuestionAlerts();
    return changed;
  }
  async function pollQuestions(){
    if(questionBusy||document.hidden)return;questionBusy=true;
    try{const data=await request('project-panel-questions');let changed=false;for(const row of data.items||[])changed=receiveQuestions(row,row)||changed;questionNext=Date.now()+2500;if(changed)sectionRender();}
    catch(_){questionNext=Date.now()+15000;}
    finally{questionBusy=false;}
  }
  async function request(path,body){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),body?25000:15000);
    try{
      let options={cache:'no-store',signal:controller.signal};
      if(body){if(!actionToken){const session=await(await fetch('/api/work/jobs',{cache:'no-store',signal:controller.signal})).json();actionToken=session.action_token||'';}if(!actionToken)throw Error('오피스 연결을 확인해 주세요.');options={...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,token:actionToken})};}
      const response=await fetch('/api/work/'+path,options),data=await response.json();
      if(response.status===403)actionToken='';if(!response.ok||data.error)throw Error(data.error||'프로젝트 연결을 확인해 주세요.');return data;
    }catch(error){if(error.name==='AbortError')throw Error('대화 조회가 지연됐어요. 입력은 유지하며 다시 확인할게요.');throw error;}
    finally{clearTimeout(timeout);}
  }
  function focus(p){if(p.embedded)p.el.style.removeProperty('z-index');else p.el.style.zIndex=String(++z);for(const row of panels.values())row.el.classList.toggle('is-focused',row===p);}
  function dockRender(){
    const focusToggle=document.activeElement===dock.querySelector('.project-dock-toggle');
    const items=[...panels.values()].filter(p=>!p.embedded),unread=items.filter(p=>p.unseen).length;
    dock.replaceChildren();dock.hidden=!items.length;dock.classList.toggle('is-collapsed',dockCollapsed);
    const toggle=node('button','project-dock-toggle');toggle.type='button';toggle.setAttribute('aria-expanded',String(!dockCollapsed));toggle.setAttribute('aria-controls','projectWindowDockItems');
    toggle.title=dockCollapsed?'작업창 목록 펼치기':'작업창 목록 숨기기';
    const arrow=node('span','project-dock-toggle-arrow',dockCollapsed?'▴':'▾');arrow.setAttribute('aria-hidden','true');
    toggle.append(arrow,node('span','',dockCollapsed?'작업창 '+items.length+'개':'작업창 목록 접기'));
    if(unread)toggle.append(node('span','project-dock-unread-count','새 완료 '+unread));
    toggle.onclick=()=>{dockCollapsed=!dockCollapsed;try{localStorage.setItem(dockVisibilityKey,String(dockCollapsed));}catch(_){}dockRender();};
    const tray=node('div','project-dock-items');tray.id='projectWindowDockItems';tray.hidden=dockCollapsed;
    dock.append(toggle,tray);
    for(const p of items){
      const button=node('button','project-dock-item',provider(p)+' · '+p.title+(p.unseen?' · 새 완료':''));button.type='button';button.title=p.title+(p.unseen?' · 확인하지 않은 완료 답변':'')+(p.minimized?' · 펼치기':' · 접기');button.setAttribute('aria-label',button.title);
      button.classList.toggle('is-open',!p.minimized);button.classList.toggle('is-running',p.status==='진행 중');
      button.classList.toggle('is-unread',!!p.unseen);
      button.onclick=()=>{p.unseen=false;if(p.minimized)restore(p);else{p.minimized=true;p.viewIntent=false;p.el.hidden=true;save();dockRender();}};tray.append(button);
    }
    const add=node('button','project-dock-add','＋');add.type='button';add.title='프로젝트 작업창 추가';add.onclick=showPicker;tray.append(add);sectionRender();
    if(focusToggle&&!dock.hidden)toggle.focus({preventScroll:true});
    document.dispatchEvent(new CustomEvent('project-panels-state',{detail:{items:[...panels.values()].map(p=>({id:p.id,host:p.host,status:p.status}))}}));
  }
  function restore(p){if(p.embedded){window.officeSupervisorSelect?.(p.id);return;}p.minimized=false;p.unseen=false;p.viewIntent=true;p.el.hidden=false;focus(p);fit(p);save();dockRender();refresh(p);requestAnimationFrame(()=>markDisplayedCompletion(p));}
  function acknowledge(p){p.viewIntent=true;if(p.unseen){p.unseen=false;save();dockRender();}requestAnimationFrame(()=>markDisplayedCompletion(p));}
  function confirmDisplayedCompletion(row,log,messages,completion){
    if(document.hidden||!completion||departmentStates.get(key(row))?.completion!==completion||departmentSeen.get(key(row))===completion)return false;
    const message=messages.find(m=>m.role==='assistant'&&['final','final_answer'].includes(m.phase)&&(completion===m.turn_id+':'+m.id||completion===m.turn_id+':final'));
    if(!message)return false;
    const item=[...log.querySelectorAll('[data-message-id]')].find(n=>n.dataset.messageId===message.id);if(!item)return false;
    const r=item.getBoundingClientRect(),view=log.getBoundingClientRect();
    if(Math.min(r.bottom,view.bottom,innerHeight)-Math.max(r.top,view.top,0)<16||Math.min(r.right,view.right,innerWidth)-Math.max(r.left,view.left,0)<16)return false;
    departmentSeen.set(key(row),completion);
    try{localStorage.setItem(departmentSeenKey,JSON.stringify(Object.fromEntries([...departmentSeen].slice(-200))));}catch(_){}
    dispatchEvent(new CustomEvent('office-project-completion-seen',{detail:{id:row.id,host:row.host||'local',completion}}));sectionRender();return true;
  }
  function markDisplayedCompletion(p){
    if(!p.viewIntent||!p.ready||p.minimized||document.hidden||!p.el.classList.contains('is-focused')||(!p.embedded&&!departmentWindow(p)))return;
    confirmDisplayedCompletion(p,p.log,p.messages,departmentStates.get(key(p))?.completion);
  }
  function observeCompletion(p,data){
    if(data.completion){
      if(p.watchPrimed&&p.completion!==data.completion){
        p.unseen=true;p.viewIntent=false;
        if(p.minimized&&!p.embedded){p.minimized=false;p.el.hidden=false;focus(p);fit(p);}
      }
      p.completion=data.completion;
    }
    p.watchPrimed=true;
    save();
  }
  async function watch(p){
    if(p.loading||!p.minimized||!panels.has(key(p)))return;
    p.loading=true;
    try{
      const data=await request('project-panel-state?id='+encodeURIComponent(p.id)+'&host='+encodeURIComponent(p.host));
      if(!panels.has(key(p)))return;
      p.status=data.status;p.statusNode.textContent=data.status;p.failures=0;p.next=Date.now()+8000;
      rememberDepartmentState(p,data);
      observeCompletion(p,data);dockRender();
    }catch(error){p.failures=(p.failures||0)+1;p.next=Date.now()+Math.min(60000,8000*Math.pow(2,p.failures));p.statusNode.textContent='연결 확인 필요';}
    finally{p.loading=false;if(!p.minimized)refresh(p);}
  }
  function fit(p){
    if(p.embedded)return;
    const width=Math.min(p.el.offsetWidth||640,innerWidth-16),height=Math.min(p.el.offsetHeight||650,innerHeight-68);
    p.el.style.width=width+'px';p.el.style.height=height+'px';
    p.el.style.left=clamp(parseFloat(p.el.style.left)||8,8,innerWidth-width-8)+'px';
    p.el.style.top=clamp(parseFloat(p.el.style.top)||8,8,innerHeight-height-52)+'px';
  }
  function drag(p,bar){
    bar.addEventListener('pointerdown',event=>{
      if(event.button!==0||event.target.closest('button'))return;
      focus(p);const x=event.clientX,y=event.clientY,left=p.el.offsetLeft,top=p.el.offsetTop;
      bar.setPointerCapture(event.pointerId);bar.classList.add('is-dragging');event.preventDefault();
      const move=e=>{p.el.style.left=clamp(left+e.clientX-x,8,innerWidth-p.el.offsetWidth-8)+'px';p.el.style.top=clamp(top+e.clientY-y,8,innerHeight-p.el.offsetHeight-52)+'px';};
      const end=()=>{bar.classList.remove('is-dragging');bar.removeEventListener('pointermove',move);bar.removeEventListener('pointerup',end);bar.removeEventListener('pointercancel',end);save();};
      bar.addEventListener('pointermove',move);bar.addEventListener('pointerup',end);bar.addEventListener('pointercancel',end);
    });
  }
  function renderMessages(p,data,older=false){
    const atBottom=p.log.scrollHeight-p.log.scrollTop-p.log.clientHeight<85,oldHeight=p.log.scrollHeight,oldTop=p.log.scrollTop;
    const start=data.history_window?.oldest_started_at;
    if(Number.isFinite(start))p.historyStart=Math.min(p.historyStart??start,start);
    // Live reads can omit an earlier user item while the same turn is running.
    // Update by source item ID, retaining already observed visible messages.
    mergeSource(p,data.messages||[],older);reconcileReceipts(p,data.delivery_receipts);
    if(older||!p.historyLoaded){p.cursor=data.cursor;p.hasMore=data.has_more;}
    reconcileOwn(p);drawMessages(p,false,{older,atBottom,oldHeight,oldTop});
    p.older.hidden=!p.hasMore;
  }
  function drawMessages(p,forceBottom=false,scroll={}){
    const {older=false,atBottom=p.log.scrollHeight-p.log.scrollTop-p.log.clientHeight<85,oldHeight=p.log.scrollHeight,oldTop=p.log.scrollTop}=scroll;
    const messages=displayMessages(p),signature=JSON.stringify(messages);
    if(signature!==p.signature){
      p.signature=signature;p.log.replaceChildren();
      for(const message of messages){
        const row=node('article','project-chat-message '+(message.role==='user'?'is-user':'is-assistant'));
        row.dataset.messageId=message.id;
        const delivery={sending:' · 전달 중',sent:' · 전달됨',unknown:' · 전달 확인 필요'}[message.delivery]||'';
        row.append(node('strong','',message.role==='user'?'나'+delivery:provider(p)+(message.phase==='commentary'?' · 진행':'')));
        const content=node('div','project-chat-text');
        if(window.officeChatContent)window.officeChatContent.render(content,message);
        else for(const part of message.parts||[{kind:'text',text:message.text}]){
          if(!['link','file'].includes(part.kind)){content.append(document.createTextNode(part.text||''));continue;}
          let url;
          try{url=new URL(part.href,location.href);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error();}
          catch(_){content.append(document.createTextNode(part.text||''));continue;}
          const link=node('a','project-message-link',part.text||url.href);
          link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';
          link.title=part.path||url.href;
          link.addEventListener('click',event=>event.stopPropagation());content.append(link);
          if(part.kind==='file'&&part.download){
            const download=node('a','project-message-download','↓');
            download.href=url.href+(url.search?'&':'?')+'download=1';download.download='';
            download.title='파일 다운로드';download.setAttribute('aria-label',(part.text||'파일')+' 다운로드');
            download.addEventListener('click',event=>event.stopPropagation());content.append(download);
          }
        }
        row.append(content);
        if(message.attachments?.length){const files=node('div','project-message-files');attachmentCards(files,message.attachments);row.append(files);}
        if(message.truncated)row.append(node('small','','일부 내용 표시 · 전체 내용은 원래 앱에서 확인할 수 있어요.'));
        p.log.append(row);
      }
      if(!messages.length)p.log.append(node('p','project-empty','표시할 대화가 아직 없어요. 아래에서 작업을 이어갈 수 있어요.'));
      if(p.embedded&&p.resumeScroll!==undefined){if(!p.minimized&&p.log.clientHeight>0){p.log.scrollTop=p.resumeScroll===null?p.log.scrollHeight:p.resumeScroll;delete p.resumeScroll;}}else if(older)p.log.scrollTop=p.log.scrollHeight-oldHeight+oldTop;else if(forceBottom||atBottom||!p.ready)p.log.scrollTop=p.log.scrollHeight;
    }
  }
  async function warmHistory(p){
    if(p.historyWarming||p.historyWarmed||p.minimized||!panels.has(key(p)))return;
    p.historyWarming=true;
    try{
      // Preserve single-turn reads for large design conversations. Only the
      // first visible load fills recent context; live polls stay one page.
      for(let page=0;page<5&&p.hasMore&&p.cursor&&!p.minimized&&panels.has(key(p));page++){
        const cursor=p.cursor;
        if(!await refresh(p,true,true)||p.cursor===cursor)break;
      }
      p.historyWarmed=!p.minimized;
    }finally{p.historyWarming=false;}
  }
  async function refresh(p,older=false,warming=false){
    if(p.loading||(p.historyWarming&&!warming)||p.minimized||panels.get(key(p))!==p)return false;
    p.loading=true;p.older.disabled=true;
    let loaded=false;
    try{
      const data=await request('project-panel?id='+encodeURIComponent(p.id)+'&host='+encodeURIComponent(p.host)+(older&&p.cursor?'&cursor='+encodeURIComponent(p.cursor):''));
      if(panels.get(key(p))!==p)return;
      p.kind=data.kind||p.kind||'codex';windowLabels(p);renderMessages(p,data,older);if(p.embedded&&!p.minimized&&p.log.clientHeight>0&&p.resumeScroll!==undefined){p.log.scrollTop=p.resumeScroll===null?p.log.scrollHeight:p.resumeScroll;delete p.resumeScroll;}if(older)p.historyLoaded=true;
      if(!older){p.status=data.status;p.statusNode.textContent=[data.status,data.activity].filter(Boolean).join(' · ');receiveQuestions(p,data);observeCompletion(p,data);rememberDepartmentState(p,data);}p.failures=0;p.next=Date.now()+2500;
      if(!p.ready)notice(p,'');p.ready=true;loaded=true;dockRender();requestAnimationFrame(()=>markDisplayedCompletion(p));
    }catch(error){p.failures=(p.failures||0)+1;p.next=Date.now()+Math.min(20000,2500*Math.pow(2,p.failures));p.statusNode.textContent='연결 확인 필요';notice(p,error.message,true);}
    finally{p.loading=false;p.older.disabled=false;sendLabel(p);}
    if(loaded&&!older&&!p.historyWarmed)void warmHistory(p);
    return loaded;
  }
  function gptBinding(p){
    try{const row=JSON.parse(localStorage.getItem(gptKey(p))||'null');if(gptId.test(row?.id||'')&&typeof row.title==='string')return row;}catch(_){}
    return null;
  }
  function routingLabel(p){
    if(!p.routeGroup)return;
    p.routeGroup.hidden=!routedWindow(p);
    p.gptBind.textContent=p.gpt?'GPT 연결 변경':'GPT 연결';
    p.gptBind.title=p.gpt?'연결된 대화 · '+p.gpt.title:'이 입력창에서 사용할 GPT 일반 채팅을 선택';
    p.gptLabel.textContent=p.gpt?p.gpt.title:'';
    p.gptLabel.hidden=!p.gpt;
  }
  function buildRouting(p){
    p.gpt=gptBinding(p);p.routeGroup=node('div','project-route-group');p.routeMode=node('select','project-route-select');p.routeMode.setAttribute('aria-label','이번 메시지의 처리 경로');
    for(const [value,label] of [['auto','자동'],['gpt','GPT로 대화'],['codex','코덱스로 실행']]){const option=node('option','',label);option.value=value;p.routeMode.append(option);}
    if(['auto','gpt','codex'].includes(p.pending?.route_mode))p.routeMode.value=p.pending.route_mode;
    p.gptBind=routeButton('GPT 연결',()=>showGptPicker(p));p.gptLabel=node('span','project-gpt-label');
    p.routeGroup.append(p.routeMode,p.gptBind,p.gptLabel);p.routeActions=node('div','project-route-buttons');routingLabel(p);
  }
  function routeButton(label,click){const button=node('button','project-native',label);button.type='button';button.onclick=click;return button;}
  function showGptOpen(p,row){
    p.routeActions.replaceChildren(routeButton('GPT 대화 열기',async()=>{
      try{await request('project-panel-native',{id:row.id,host:'local',kind:'chatgpt'});}
      catch(error){notice(p,error.message,true);}
    }));
  }
  async function showGptPicker(p){
    if(p.posting||p.pending)return;
    if(p.gptPicker){p.gptPicker.querySelector('select')?.focus();return;}
    const box=node('div','project-gpt-picker');p.gptPicker=box;
    const list=node('select','project-route-select');list.setAttribute('aria-label','기존 GPT 대화');
    const placeholder=node('option','','기존 GPT 대화 선택');placeholder.value='';list.append(placeholder);
    const input=node('input');input.type='text';input.placeholder='또는 chatgpt.com/c/… 주소';input.setAttribute('aria-label','기존 GPT 대화 주소');
    const status=node('small','','일반 채팅에서 쓸 방을 골라 주세요. 이 연결은 현재 브라우저에 저장돼요.');status.setAttribute('role','status');
    let cursor=null;
    const close=()=>{box.remove();p.gptPicker=null;};
    const load=async()=>{
      more.disabled=true;
      try{
        const data=await request('project-panel-gpt'+(cursor?'?cursor='+encodeURIComponent(cursor):''));
        const existing=new Set([...list.options].map(option=>option.value));
        for(const row of data.items||[])if(gptId.test(row.id)&&!existing.has(row.id)){
          const option=node('option','',row.title);option.value=row.id;list.append(option);existing.add(row.id);
        }
        cursor=data.cursor||null;more.hidden=!cursor;
      }catch(error){status.textContent=error.message;more.hidden=false;more.textContent='목록 다시 보기';}
      finally{more.disabled=false;}
    };
    const more=routeButton('이전 대화 더 보기',load);
    const save=routeButton('이 채팅에 연결',async()=>{
      const pasted=input.value.trim(),match=pasted.match(/^https:\/\/chatgpt\.com\/(?:g\/[^/]+\/)?c\/([0-9a-f-]+)(?:[/?#].*)?$/);
      const id=pasted?(match?.[1]||''):list.value;
      if(!gptId.test(id)){status.textContent='기존 일반 GPT 대화를 선택하거나 대화 주소를 붙여넣어 주세요.';return;}
      save.disabled=true;
      try{
        const row=await request('project-panel-gpt?id='+encodeURIComponent(id));
        localStorage.setItem(gptKey(p),JSON.stringify(row));p.gpt=row;routingLabel(p);showGptOpen(p,row);
        notice(p,'이 채팅의 GPT 대화를 연결했어요. 입력한 메시지는 보내기를 누르면 전달돼요.');close();
      }catch(error){status.textContent=error.message;}
      finally{save.disabled=false;}
    });
    const disconnect=routeButton('연결 해제',()=>{
      try{localStorage.removeItem(gptKey(p));p.gpt=null;routingLabel(p);p.routeActions.replaceChildren();close();}
      catch(error){status.textContent=error.message;}
    });disconnect.hidden=!p.gpt;
    const buttons=node('div','project-route-buttons');buttons.append(more,save,disconnect,routeButton('닫기',close));
    box.append(status,list,input,buttons);p.routeGroup.after(box);list.focus();await load();
  }
  function mountMainRouting(row,input,refreshLocks){
    const p={...row,mainComposer:true,input};
    const storageKey=draftKey(p)+':main-route';
    try{
      const saved=JSON.parse(sessionStorage.getItem(storageKey)||'null');
      if(saved?.id===p.id&&saved.host===p.host&&saved.main_composer===true&&['auto','gpt'].includes(saved.route_mode)&&/^[a-f0-9]{32}$/.test(saved.request_id)&&typeof saved.message==='string'){
        p.pending=saved;input.value=saved.message;
      }
    }catch(_){}
    buildRouting(p);p.note=node('p','project-window-note project-main-route-note');p.note.setAttribute('role','status');
    input.after(p.routeGroup,p.note,p.routeActions);
    const clearPending=()=>{p.pending=null;try{sessionStorage.removeItem(storageKey);}catch(_){}};
    const complete=()=>{clearPending();input.value='';p.routeMode.value='auto';};
    return {
      refresh:()=>routingLabel(p),
      posting:()=>!!p.posting,
      pending:()=>!!p.pending,
      lock(blocked,controlled){
        p.routeMode.disabled=p.gptBind.disabled=blocked||controlled||!!p.pending;
        if(p.gptPicker){p.gptPicker.inert=blocked||controlled||!!p.pending;}
        input.readOnly=!!p.pending;
      },
      async submit(message,hasFiles,run){
        if(p.posting)return;
        if(!p.pending){
          notice(p,'');p.routeActions.replaceChildren();
          const mode=routedWindow(p)?p.routeMode.value:'auto';
          if(mode==='codex'||(mode==='auto'&&(!p.gpt||hasFiles))){run(mode==='codex');p.routeMode.value='auto';return;}
          if(hasFiles){notice(p,'GPT 대화에는 이 입력창의 파일을 보낼 수 없어요. 코덱스로 실행을 선택해 주세요.',true);return;}
          if(!p.gpt){notice(p,'이 채팅에서 사용할 GPT 대화를 먼저 연결해 주세요.');showGptPicker(p);return;}
          p.pending={id:p.id,host:p.host,message,request_id:uid(),route_mode:mode,gpt_id:p.gpt.id,main_composer:true};
          try{sessionStorage.setItem(storageKey,JSON.stringify(p.pending));}catch(_){}
        }
        p.posting=true;refreshLocks();notice(p,'처리 경로를 확인하고 있어요.');p.routeActions.replaceChildren();
        try{
          const result=await request('project-panel-send',p.pending);
          if(result.state==='route_required'){
            clearPending();
            if(result.route==='main_routine'||result.route==='main_codex'){
              notice(p,'');run(result.route==='main_codex');p.routeMode.value='auto';
            }else{
              const labels={choose:'이번 메시지는 GPT로 대화 또는 코덱스로 실행을 선택해 주세요.',gpt_binding:'이 채팅에서 사용할 GPT 대화를 다시 연결해 주세요.',gpt_busy:'연결된 GPT 대화가 진행 중이에요. 입력은 보관했어요.'};
              notice(p,labels[result.route]||'이번 메시지의 처리 경로를 확인해 주세요.',true);
              if(result.route==='gpt_binding')p.routeActions.append(routeButton('GPT 대화 선택',()=>showGptPicker(p)));
              if(result.route==='gpt_busy'&&p.gpt)showGptOpen(p,p.gpt);
            }
            return;
          }
          if(result.route!=='gpt')throw Error('GPT 전달 상태를 확인하지 못했어요.');
          showGptOpen(p,{id:result.destination,title:result.destination_title});
          if(result.state==='sent'){
            complete();notice(p,'GPT로 전달 · '+result.destination_title+' · 답변은 GPT 대화에서 확인해 주세요.');
          }else{
            notice(p,'GPT 전달 확인 필요 · 원래 GPT 대화에서 확인해 주세요.',true);
            p.routeActions.append(routeButton('새 메시지 작성',()=>{complete();notice(p,'새 메시지를 입력해 주세요.');refreshLocks();input.focus();}));
          }
        }catch(error){notice(p,error.message+' · 같은 요청의 전달 상태를 다시 확인할 수 있어요.',true);}
        finally{p.posting=false;refreshLocks();}
      }
    };
  }
  function routeRequired(p,route,message){
    p.pending=null;
    try{sessionStorage.removeItem(draftKey(p)+':pending');}catch(_){}
    p.routeActions.replaceChildren();
    if(route==='routine'){
      notice(p,'등록 실행기로 처리할 수 있는 요청이에요. 에이메스 입력창으로 가져와 실행할 수 있어요.');
      p.routeActions.append(routeButton('에이메스 입력창에 가져오기',()=>{
        const main=$('workCommand'),state=window.officeWorkSession?.state();
        if(!main||!state?.ready||main.disabled||main.value.trim()||state.attachments||state.uploading||p.input.value!==message){
          notice(p,'에이메스 입력창이 비어 있고 현재 입력이 그대로일 때 가져올 수 있어요.',true);return;
        }
        main.value=message;main.dispatchEvent(new Event('input',{bubbles:true}));
        p.input.value='';persistDraft(p);p.routeActions.replaceChildren();
        main.scrollIntoView({block:'nearest'});main.focus();notice(p,'에이메스 입력창에 가져왔어요.');
      }));
    }else if(route==='gpt_binding'){
      notice(p,'GPT로 처리할 요청이에요. 이 부서의 일반 GPT 대화를 먼저 연결해 주세요.');
      p.routeActions.append(routeButton('GPT 대화 선택',()=>showGptPicker(p)));
    }else if(route==='gpt_attachments'){
      notice(p,'GPT 대화에는 글만 전달할 수 있어요. 첨부까지 보내려면 GPT 원래 대화에서 직접 올리거나 코덱스를 선택해 주세요.');
      if(p.gpt)showGptOpen(p,p.gpt);
    }else if(route==='gpt_busy'){
      notice(p,'연결한 GPT 대화에서 작업 중이에요. 입력을 보관했어요.');
      if(p.gpt)showGptOpen(p,p.gpt);
    }else notice(p,'어느 쪽에 맡길지 골라 주세요. 대화나 조사는 GPT, 실제 변경은 코덱스로 보내면 돼요.');
  }
  async function send(p,event){
    event.preventDefault();if(p.posting||p.uploading||!p.ready||p.files.some(f=>!f.id||f.error)||(!p.input.value.trim()&&!p.files.length))return;
    const message=p.input.value;
    const routed=routedWindow(p);
    if(p.pending?.route_mode&&p.pending.message!==message){notice(p,'이전 메시지의 전달 상태부터 확인해 주세요. 원래 입력을 복원하면 같은 전송 번호로 확인할 수 있어요.',true);return;}
    if(p.pending?.attachment_ids?.length&&p.pending.message!==message){notice(p,'이전 첨부의 전달 상태를 먼저 확인해 주세요. 원래 입력은 보관돼 있어요.',true);return;}
    if(!p.pending||p.pending.message!==message){
      const mode=p.routeMode.value;
      p.pending={id:p.id,host:p.host,message,request_id:uid(),...(p.files.length?{attachment_ids:p.files.map(f=>f.id),attachment_request_id:p.uploadGroup}:{}),
        ...(routed?{route_mode:mode,...(mode!=='codex'&&p.gpt?{gpt_id:p.gpt.id}:{})}:{})};
    }
    const routing=!!p.pending.route_mode;
    try{sessionStorage.setItem(draftKey(p)+':pending',JSON.stringify(p.pending));}catch(_){}
    p.routeActions.replaceChildren();p.posting=true;sendLabel(p);if(!routing)ownMessage(p,message,'sending');notice(p,'선택한 처리 경로를 확인하고 있어요.');
    try{
      const result=await request('project-panel-send',p.pending);
      if(panels.get(key(p))!==p)return;
      if(result.state==='route_required'){routeRequired(p,result.route,message);return;}
      if(result.route==='gpt'){
        showGptOpen(p,{id:result.destination,title:result.destination_title});
        if(result.state==='sent'){
          settleConfirmedSend(p,p.pending);notice(p,'GPT로 전달 · '+result.destination_title+' · 답변은 GPT 대화에서 확인해 주세요.');
        }else{
          notice(p,'GPT 전달 확인 필요 · 원래 GPT 대화에서 확인해 주세요.',true);
          p.routeActions.append(routeButton('새 메시지 작성',()=>{
            if(settleConfirmedSend(p,p.pending)){
              notice(p,'이전 전송 확인 기록을 보관했어요. 새 메시지를 입력해 주세요.');p.input.focus();
            }
          }));
        }
        return;
      }
      if(result.state==='sent'){
        ownMessage(p,message,'sent');
        settleConfirmedSend(p,p.pending);
        notice(p,'코덱스로 전달했어요. 진행 상황은 이 창에서 이어서 확인할 수 있어요.');refresh(p);
      }else{ownMessage(p,message,'unknown');notice(p,'전달 여부를 확정하지 못했어요. 자동으로 다시 보내지 않았어요. 원래 앱에서 확인해 주세요.',true);}
    }catch(error){if(panels.get(key(p))!==p)return;if(!routing)ownMessage(p,message,'unknown');notice(p,error.message+' · 같은 전송 번호로 상태만 다시 확인해요.',true);}
    finally{p.posting=false;if(panels.get(key(p))===p&&reconcileReceipts(p,p.receipts||[]))drawMessages(p);sendLabel(p);}
  }
  // Team identity is presentational; retain the existing native thread and send path.
  function setWindowTeam(p){
    const registered=departments.find(row=>key(row)===key(p));
    const project=registered?.project_id||p.project_id;
    p.el.dataset.officeTeam=project==='b3649dae-5b9f-4dfa-bce4-6ea7a4b090ed'||p.id==='01a0c670-7a18-7243-a9af-9ae8cb0a2548'?'hand':registered||p.department?'aemeath':'';
    p.el.dataset.frameState=departmentStatus(p).kind;
    routingLabel(p);
  }
  function windowLabels(p){
    p.providerNode.textContent='Codex';p.native.textContent='Codex에서 열기';
    p.help.textContent=p.host==='local'?'첨부·이미지 붙여넣기 가능 · 질문은 여기서 답변 · 실행 권한 승인·터미널은 Codex에서 확인해요.':'원격 PC의 첨부·승인·터미널은 해당 Codex에서 확인해요.';
  }
  function open(row,layout={}){
    if(row.kind&&row.kind!=='codex')return;
    const existing=panels.get(key(row));if(existing){restore(existing);return;}
    const limit=layout.embedded?'':windowLimit(row);if(limit){catalogError=departmentError=limit;sectionRender();if($('projectPickerNote'))$('projectPickerNote').textContent=limit;return;}
    catalogError=departmentError='';
    const p={...row,embedded:layout.embedded===true,host:row.host||'local',messages:[],own:[],files:[],uploadGroup:uid(),uploading:false,minimized:false,ready:false,viewIntent:false,next:0,completion:typeof layout.completion==='string'?layout.completion:null,watchPrimed:layout.watchPrimed===true,unseen:layout.unseen===true};
    p.el=node('section','project-work-panel');p.el.dataset.threadId=p.id;setWindowTeam(p);p.el.setAttribute('role','dialog');p.el.setAttribute('aria-modal','false');p.el.setAttribute('aria-label',p.title+' 작업창');
    const bar=node('header','project-window-bar');p.providerNode=node('small','project-provider-tag',provider(p));bar.append(node('span','project-window-mark','◈'),p.providerNode,node('h3','',p.title));
    const minimize=node('button','','—'),close=node('button','','×');minimize.type=close.type='button';minimize.title='접어두기 · 작업은 계속돼요';minimize.setAttribute('aria-label','작업창 접기');close.title='창만 닫기 · 작업은 계속돼요';close.setAttribute('aria-label','작업창 닫기');bar.append(minimize,close);
    const meta=node('div','project-window-meta');p.statusNode=node('span','','대화 연결 중…');meta.append(node('span','project-location',p.project||'기존 프로젝트'),p.statusNode);
    const body=node('div','project-window-body');p.older=node('button','project-older','이전 대화 더 보기');p.older.type='button';p.older.hidden=true;p.older.onclick=()=>refresh(p,true);
    p.log=node('div','project-chat-log');p.log.setAttribute('role','log');p.log.setAttribute('aria-label','프로젝트 대화');p.log.append(node('p','project-empty','기존 대화를 불러오고 있어요.'));
    p.promptArea=node('section','project-prompt-area');p.promptArea.setAttribute('aria-label','이 요원의 확인 질문');p.promptArea.hidden=true;body.append(p.older,p.log,p.promptArea);
    const form=node('form','project-window-form');p.input=node('textarea');p.input.rows=2;p.input.maxLength=12000;p.input.placeholder='이 프로젝트에서 이어서 할 작업';p.input.setAttribute('aria-label',p.title+'에 보낼 메시지');
    try{p.input.value=sessionStorage.getItem(draftKey(p))||'';const pending=JSON.parse(sessionStorage.getItem(draftKey(p)+':pending')||'null');if(pending?.id===p.id&&pending.host===p.host)p.pending=pending;}catch(_){}
    try{const files=JSON.parse(sessionStorage.getItem(draftKey(p)+':files')||'null');if(/^[a-f0-9]{32}$/.test(files?.request_id||'')&&Array.isArray(files.items)){p.uploadGroup=files.request_id;p.files=files.items.filter(f=>f&&/^[a-f0-9]{32}$/.test(f.id||'')&&typeof f.name==='string'&&Number.isFinite(f.size)).slice(0,6);}}catch(_){}
    try{const saved=JSON.parse(sessionStorage.getItem(draftKey(p)+':own')||'[]');if(Array.isArray(saved))p.own=saved.filter(m=>m&&/^local:[0-9a-f]{32}$/.test(m.id)&&typeof m.text==='string'&&m.text.length<=12000&&typeof m.anchor==='string'&&['sending','sent','unknown'].includes(m.delivery)).slice(-40).map(m=>({...m,role:'user',delivery:m.delivery==='sending'?'unknown':m.delivery}));}catch(_){}
    p.input.oninput=()=>persistDraft(p);p.input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();if(!p.send.disabled)form.requestSubmit();}});
    const actions=node('div','project-window-actions');p.native=node('button','project-native');p.native.type='button';p.native.onclick=async()=>{try{await request('project-panel-native',{id:p.id,host:p.host});}catch(error){notice(p,error.message,true);}};
    buildRouting(p);
    p.attach=node('button','project-native','＋ 첨부');p.attach.type='button';p.attach.onclick=()=>p.fileInput.click();
    p.fileInput=node('input');p.fileInput.type='file';p.fileInput.multiple=true;p.fileInput.hidden=true;p.fileInput.accept='.gif,.png,.jpg,.jpeg,.webp,.ico,.pdf,.docx,.txt,.md,.csv,.tsv,.json,.py,.js,.ts,.jsx,.tsx,.html,.css,.xml,.yaml,.yml,.sql,.log,.rst,.toml,.zip';
    p.fileInput.onchange=()=>{addFiles(p,p.fileInput.files);p.fileInput.value='';};p.filesNode=node('div','project-draft-files');p.filesNode.hidden=true;
    p.input.addEventListener('paste',event=>{const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==='file').map(item=>item.getAsFile()).filter(Boolean);if(files.length){event.preventDefault();addFiles(p,files);}});
    form.addEventListener('dragover',event=>{if(Array.from(event.dataTransfer?.types||[]).includes('Files'))event.preventDefault();});
    form.addEventListener('drop',event=>{if(event.dataTransfer?.files.length){event.preventDefault();addFiles(p,event.dataTransfer.files);}});
    p.send=node('button','project-send','보내기');p.send.type='submit';p.send.disabled=true;actions.append(p.native,p.attach,node('small','','Enter 전송 · Shift+Enter 줄바꿈'),p.send);
    p.note=node('p','project-window-note');p.note.setAttribute('role','status');p.help=node('small','project-window-help');windowLabels(p);form.append(p.input,p.fileInput,p.filesNode,p.routeGroup,actions,p.note,p.routeActions,p.help);form.onsubmit=event=>send(p,event);drawFiles(p);
    p.el.append(bar,meta,body,form);panels.set(key(p),p);layer.append(p.el);
    const offset=(panels.size-1)*34;
    p.el.style.cssText=`left:${Number.isFinite(layout.x)?layout.x:Math.max(16,innerWidth/2-350)+offset}px;top:${Number.isFinite(layout.y)?layout.y:65+offset}px;width:${Number.isFinite(layout.width)?layout.width:700}px;height:${Number.isFinite(layout.height)?layout.height:Math.min(720,innerHeight-125)}px`;
    minimize.onclick=()=>{p.minimized=true;p.viewIntent=false;p.el.hidden=true;save();dockRender();};
    close.onclick=()=>{persistDraft(p);p.resizeObserver?.disconnect();p.el.remove();panels.delete(key(p));save();dockRender();};
    p.el.addEventListener('pointerdown',event=>{if(!p.embedded||event.isTrusted)acknowledge(p);focus(p);});p.el.addEventListener('keydown',event=>{if(!p.embedded||event.isTrusted)acknowledge(p);if(event.key==='Escape'&&!p.embedded){event.stopPropagation();minimize.click();}});drag(p,bar);
    p.log.addEventListener('scroll',()=>markDisplayedCompletion(p),{passive:true});
    p.log.addEventListener('wheel',event=>{if(p.embedded&&!event.isTrusted)return;p.viewIntent=true;requestAnimationFrame(()=>markDisplayedCompletion(p));},{passive:true});
    if(typeof ResizeObserver==='function'){p.resizeObserver=new ResizeObserver(()=>{clearTimeout(p.resizeTimer);p.resizeTimer=setTimeout(save,250);});p.resizeObserver.observe(p.el);}
    fit(p);focus(p);picker?.close();dockRender();if(p.own.length)drawMessages(p,true);
    if(layout.minimized){p.minimized=true;p.el.hidden=true;dockRender();watch(p);}else refresh(p);
    save();
  }
  function threadCard(row){
    const card=node('button','project-picker-row');card.type='button';card.dataset.threadId=row.id;
    const current=panels.get(key(row)),state=current?(current.unseen?'새 답변':current.minimized?'접힌 작업창':'열린 작업창'):row.status;
    card.title=row.title+' · '+state;card.setAttribute('aria-label',row.title+' · '+state);
    card.classList.toggle('has-window',!!current);card.classList.toggle('is-running',(current?.status||row.status)==='진행 중');card.classList.toggle('is-unread',!!current?.unseen);
    const dot=node('span','project-thread-dot');dot.setAttribute('aria-hidden','true');
    card.append(dot,node('strong','',row.title));card.onclick=()=>{open(row);const p=panels.get(key(row));if(p){p.viewIntent=true;requestAnimationFrame(()=>markDisplayedCompletion(p));}};return card;
  }
  function rememberDepartmentState(row,data){
    if(data.id!==row.id||typeof data.status!=='string')throw Error('실제 대화 상태를 확인하지 못했어요.');
    departmentStates.set(key(row),{...data,checkedAt:Date.now(),error:''});receiveQuestions(row,data);
    const panel=panels.get(key(row));if(panel)setWindowTeam(panel);
    dispatchEvent(new CustomEvent('office-project-thread-state',{detail:{id:row.id,host:row.host||'local',...data}}));
    if(browseMode==='departments')sectionRender();
  }
  function departmentStatus(row){
    const questions=questionStates.get(key(row))?.questions||[];
    if(questions.some(q=>!q.delivery))return {kind:'attention',label:'질문 '+questions.length+'건',detail:'작업창에서 질문 내용을 확인하고 답변하세요.'};
    const state=departmentStates.get(key(row));
    if(!state)return {kind:'unknown',label:'상태 확인 중',detail:'기존 Codex 대화 상태를 조회하고 있어요.'};
    const checked=new Date(state.checkedAt).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'});
    if(state.error)return {kind:'unknown',label:'조회 실패',detail:state.error+' · 마지막 확인 '+checked};
    if(Date.now()-state.checkedAt>45000)return {kind:'unknown',label:'갱신 지연',detail:'현재 상태를 다시 확인해야 해요. 마지막 확인 '+checked};
    if(state.status.includes('승인 대기'))return {kind:'attention',label:'승인 대기',detail:'카드를 열고 Codex에서 열기로 원래 승인 요청을 확인하세요.'};
    if(state.status.includes('입력 대기'))return {kind:'attention',label:'입력 대기',detail:'원래 Codex 대화에서 입력 요청을 확인하세요.'};
    if(state.status==='진행 중')return {kind:'working',label:'작업 중',detail:'실제 Codex 대화에서 실행 중 · '+checked+' 확인'};
    if(state.status==='확인 필요')return {kind:'attention',label:'확인 필요',detail:'실제 대화의 오류·실행 상태를 확인하세요.'};
    if(state.completion&&departmentSeen.get(key(row))!==state.completion)return {kind:'completed',label:'작업 완료',detail:'확인하지 않은 최신 완료 답변이 있어요 · 결과 검수 성공과는 별도 · '+checked+' 확인'};
    return {kind:'idle',label:'대기 중',detail:'실제 Codex 대화에서 현재 실행 없음 · '+checked+' 확인'};
  }
  const godoDepartmentProject='b3649dae-5b9f-4dfa-bce4-6ea7a4b090ed';
  const departmentGroup=row=>row.project_id||'office';
  const departmentGroupLabel=row=>row.project_id===godoDepartmentProject?'The Hand':row.project_id?'프로젝트 전담':'AI OFFICE';
  const departmentAgentLabel=row=>row.project_id===godoDepartmentProject?row.agent.replace(/^고도·메두사\s*/, ''):row.agent===row.department?'기본 요원':row.agent;
  function departmentSprite(row){const team=['planning','design','development'].includes(row.team_id)?row.team_id:row.department.includes('조사')?'research':'';return team&&row.project_id===godoDepartmentProject?'godo-'+team:team;}
  function renderDepartmentSprite(card,row,state){
    const team=departmentSprite(row),animation=['working','completed'].includes(state.kind)?state.kind:'idle',art=card.querySelector('.department-project-art');
    const asset=window.officeDepartmentSprites?.[team]?.[animation],still=departmentMotion.matches||document.hidden;
    art.dataset.animationState=animation;card.dataset.spriteTeam=team;
    const badge=art.querySelector('.department-project-alert');badge.textContent=state.label;badge.hidden=!(state.kind==='attention'||(state.kind==='unknown'&&state.label!=='상태 확인 중'));
    if(!asset)return;
    const image=art.querySelector('img'),signature=team+'|'+animation+'|'+still;if(image.dataset.sprite===signature)return;
    image.dataset.sprite=signature;image.onload=()=>{image.hidden=false;art.querySelector('.department-project-icon').hidden=true;};image.onerror=()=>{image.hidden=true;art.querySelector('.department-project-icon').hidden=false;};image.src=still?asset.still:asset.gif;
  }
  function renderDepartments(root){
    root.classList.add('is-departments');
    const signature=JSON.stringify(departments.map(r=>[key(r),r.department,r.agent,r.agent_id,r.project_id,r.team_id]));
    if(root.dataset.departments!==signature){
      root.dataset.departments=signature;root.replaceChildren();
      const groups=new Map();
      for(const row of departments){
        const groupKey=departmentGroup(row);
        if(!groups.has(groupKey)){const group=node('section','department-agent-group');group.dataset.agentGroup=groupKey;group.setAttribute('aria-label',departmentGroupLabel(row)+' 요원');const heading=node('h4','department-agent-group-heading',departmentGroupLabel(row)),list=node('div','department-agent-list');group.append(heading,list);root.append(group);groups.set(groupKey,list);}
        const card=node('button','department-project-card');card.type='button';card.dataset.threadId=row.id;card.dataset.host=row.host;card.dataset.agentId=row.agent_id;card.dataset.projectId=row.project_id||'';
        const art=node('span','department-project-art');art.setAttribute('aria-hidden','true');
        const glyph={hq:'✦',planning:'▤',design:'◇',development:'⌘',research:'◎'};
        const mark=node('span','department-project-icon',glyph[row.team_id]||(row.department.includes('조사')?'◎':'◈'));art.append(mark);
        const image=node('img','department-project-sprite');image.alt='';image.hidden=true;image.decoding='async';const badge=node('span','department-project-alert');badge.hidden=true;art.append(image,badge);
        const content=node('span','department-project-content');
        content.append(node('strong','',row.department.split(/\s*·\s*/)[0]),node('span','department-project-agent',departmentAgentLabel(row)),node('span','department-project-status','상태 확인 중'));
        card.append(art,content);
        card.onclick=()=>{try{departmentError='';const known=sourceRows().find(r=>key(r)===key(row));window.officeProjectOpen({...row,...known});}catch(error){departmentError=error.message;}sectionRender();};
        groups.get(groupKey).append(card);
      }
      if(!departments.length)root.append(node('p','project-empty',departmentLoading?'연결된 부서를 불러오는 중…':'연결된 부서 대화가 없어요. 부서관리의 기존 대화 연결을 확인하세요.'));
    }
    for(const card of root.querySelectorAll('.department-project-card')){
      const row=departments.find(r=>r.id===card.dataset.threadId&&r.host===card.dataset.host),state=departmentStatus(row);
      card.dataset.state=state.kind;card.querySelector('.department-project-status').textContent=state.label;
      const identity=departmentGroupLabel(row)+' · '+row.department+' · '+row.agent;card.title=identity+' · '+state.detail;card.setAttribute('aria-label',identity+' · '+state.label+' · 기존 대화 열기');
      card.classList.toggle('has-window',panels.has(key(row)));
      renderDepartmentSprite(card,row,state);
    }
  }
  async function loadDepartments(force=false){
    if(departmentLoading)return;departmentLoading=true;sectionRender();
    try{
      if(force||!departmentRegistryAt||Date.now()-departmentRegistryAt>60000){
        const registry=await request('departments');
        departmentChatRoutes=registry.department_chat_routes===1;
        supervisorRoutes=registry.supervisor_chat_routes||{};
        // HQ already owns the main conversation; the right board shows the other teams.
        departments=(registry.teams||[]).filter(team=>team.id!=='hq').flatMap(team=>(team.agents||[]).filter(a=>a.thread_id).map(agent=>({id:agent.thread_id,host:agent.host||'local',kind:'codex',title:agent.name||team.name,project:agent.project_id===godoDepartmentProject?'고도·메두사':agent.project_id?'프로젝트 전담':'AI OFFICE',team_id:team.id,department:team.name,agent:agent.name||team.name,agent_id:agent.id,project_id:agent.project_id||'',project_root:agent.project_root||''})));
        departmentRegistryAt=Date.now();departmentError='';for(const panel of panels.values())routingLabel(panel);mainRouting?.refresh();sectionRender();
      }
      for(const row of departments){
        if(document.hidden||browseMode!=='departments')break;
        const prior=departmentStates.get(key(row));
        if(prior&&!prior.error&&Date.now()-prior.checkedAt<12000)continue;
        try{rememberDepartmentState(row,await request('project-panel-state?id='+encodeURIComponent(row.id)+'&host='+encodeURIComponent(row.host)));}
        catch(error){departmentStates.set(key(row),{...prior,checkedAt:prior?.checkedAt||Date.now(),error:error.message});sectionRender();}
      }
    }catch(error){departmentError=error.message;}
    finally{departmentLoading=false;departmentNext=Date.now()+15000;sectionRender();}
  }
  function listRender(root,rows,grouped=false){
    root.replaceChildren();
    if(grouped){
      const groups=new Map();
      for(const row of rows){const id=row.project_key||((row.kind||'codex')+'|'+(row.host||'local')+'|'+(row.cwd||''));if(!groups.has(id))groups.set(id,[]);groups.get(id).push(row);}
      for(const group of groups.values()){
        const details=node('details','project-thread-group'),summary=node('summary','',group[0].project||'프로젝트');
        details.dataset.projectKey=group[0].project_key||group[0].cwd||'general';summary.title=group[0].project||'프로젝트';summary.append(node('small','',String(group.length)));
        details.append(summary);for(const row of group)details.append(threadCard(row));root.append(details);
      }
    }else for(const row of rows)root.append(threadCard(row));
    if(!rows.length)root.append(node('p','project-empty',catalogError?'목록을 다시 불러와 주세요.':'표시할 Codex 스레드가 없어요.'));
  }
  function sectionRender(){
    for(const p of panels.values())setWindowTeam(p);
    if(!$('projectSectionList'))return;
    if(browseMode==='departments'){
      renderDepartments($('projectSectionList'));
      $('projectSectionNote').textContent=departmentError||'';
      for(const button of $('projectSectionTabs').children)button.setAttribute('aria-pressed',String(button.dataset.mode===browseMode));
      return;
    }
    const available=sourceRows(),rows=browseMode==='pinned'?available.filter(row=>row.pinned):browseMode==='recent'?[...available].sort((a,b)=>Number(b.updated||0)-Number(a.updated||0)):available;
    const root=$('projectSectionList'),top=root.scrollTop;
    root.classList.remove('is-departments');delete root.dataset.departments;
    // Updating window status must not close an expanded project group.
    const expanded=new Set([...root.querySelectorAll('details[open]')].map(el=>el.dataset.projectKey));
    listRender(root,rows,browseMode==='projects');
    for(const el of root.querySelectorAll('details'))el.open=expanded.has(el.dataset.projectKey);
    root.scrollTop=top;
    $('projectSectionNote').textContent=catalogError||(catalogLoading&&!catalog.length?'스레드를 불러오는 중…':'');
    for(const button of $('projectSectionTabs').children)button.setAttribute('aria-pressed',String(button.dataset.mode===browseMode));
  }
  function pickerRender(){
    const query=$('projectPickerSearch').value.trim().toLowerCase();let rows=sourceRows().filter(row=>(row.title+' '+row.project+' '+row.cwd).toLowerCase().includes(query));
    if(pickerMode==='pinned')rows=rows.filter(row=>row.pinned);
    if(pickerMode==='recent')rows=[...rows].sort((a,b)=>Number(b.updated||0)-Number(a.updated||0));
    listRender($('projectPickerList'),rows,pickerMode==='projects');
    for(const button of $('projectPickerTabs').children)button.setAttribute('aria-pressed',String(button.dataset.mode===pickerMode));
  }
  async function loadCatalog(){
    if(catalogLoading)return;catalogLoading=true;catalogError='';sectionRender();$('projectPickerNote').textContent='스레드를 불러오는 중…';
    try{const data=await request('project-panels');catalog=data.items;pickerRender();$('projectPickerNote').textContent='스레드를 누르면 작업창이 열려요.';}
    catch(error){catalogError=error.message;$('projectPickerNote').textContent=error.message;}
    finally{catalogLoading=false;sectionRender();}
  }
  function showPicker(){if(!picker.open)picker.showModal();loadCatalog();$('projectPickerSearch').focus();}
  function mountRightBoard(column,stage){
    column.classList.add('is-unified');column.setAttribute('aria-label','부서작업창과 알림');
    const materials=node('details','office-materials');materials.id='officeMaterials';
    const summary=node('summary');summary.append(node('strong','','자료·기록'),node('small','','작업내역 · 받은파일 · 보낸파일'));materials.append(summary);
    // Stay inside workStage so its existing delegated result/task clicks survive.
    const body=node('div','office-materials-body');materials.append(body);stage.append(materials);
    const tabs=stage.querySelector('.work-stage-primary-tabs'),notificationsTab=$('workStageNotificationsTab');
    tabs.setAttribute('aria-label','자료·기록 보기');body.append(tabs);
    $('workStageTasksTab').hidden=false;
    for(const id of ['workStageStatus','workStageTasks','workReceivedFiles'])if($(id))body.append($(id));
    const heading=stage.querySelector('.work-panel-heading'),actions=node('div','office-notification-tools');
    // Keep the existing unread node and click listener; only move the button.
    notificationsTab.removeAttribute('role');notificationsTab.removeAttribute('aria-controls');notificationsTab.tabIndex=0;
    if(notificationsTab.firstChild?.nodeType===Node.TEXT_NODE)notificationsTab.firstChild.textContent='미확인 ';
    const expand=node('button','office-notifications-expand','펼치기');expand.type='button';expand.id='officeNotificationsExpand';expand.setAttribute('aria-expanded','false');
    actions.append(notificationsTab,expand);heading.append(actions);
    let selected='notifications',materialMode='files';
    function expanded(value){column.classList.toggle('is-notifications-expanded',value);expand.textContent=value?'접기':'펼치기';expand.setAttribute('aria-expanded',String(value));}
    expand.onclick=()=>expanded(!column.classList.contains('is-notifications-expanded'));
    window.officeRightBoard={select(mode){selected=mode;if(mode!=='notifications'){materialMode=mode;expanded(false);materials.open=true;}else if(materials.open)materials.open=false;}};
    materials.addEventListener('toggle',()=>{if(materials.open){if(selected==='notifications')window.workStageSelect?.(materialMode);}else if(selected!=='notifications')window.workStageSelect?.('notifications');});
    window.workStageSelect?.('notifications');
  }
  function init(){
    if(!$('workStage'))return;
    const style=node('link');style.rel='stylesheet';style.href='/project-panels.css';document.head.append(style);
    addEventListener('office-department-sprites-ready',sectionRender);
    const sprites=node('script');sprites.src='/department-sprites.js';sprites.onload=sectionRender;document.head.append(sprites);
    departmentMotion.addEventListener('change',sectionRender);
    addEventListener('storage',event=>{if(event.key===departmentSeenKey){loadDepartmentSeen(event.newValue);sectionRender();}if(event.key===dockVisibilityKey){dockCollapsed=event.newValue!=='false';dockRender();}});
    const column=node('aside','work-project-column');column.setAttribute('aria-label','프로젝트와 워크스페이스');
    const stage=$('workStage');stage.before(column);
    const section=node('section','project-section');section.id='projectSection';section.setAttribute('aria-labelledby','projectSectionTitle');
    section.innerHTML='<header class="project-section-heading"><h3 id="projectSectionTitle">워크스페이스</h3><div><button type="button" id="projectSectionReload" title="부서 상태 새로고침" aria-label="부서 상태 새로고침">↻</button><button type="button" id="projectPanelsNav">다른 작업창 ↗</button></div></header><nav id="projectSectionTabs" class="project-browse-tabs" aria-label="프로젝트 목록 보기" hidden><button type="button" data-mode="departments" aria-pressed="true">부서작업창</button></nav><p id="projectSectionNote" role="status"></p><div id="projectSectionList" class="project-section-list"></div>';
    column.append(section,stage);$('projectPanelsNav').onclick=showPicker;$('projectSectionReload').onclick=()=>{loadCatalog();loadDepartments(true);};
    for(const button of $('projectSectionTabs').children)button.onclick=()=>{browseMode=button.dataset.mode;sectionRender();if(browseMode==='departments')loadDepartments();};
    dock=node('nav','project-window-dock');dock.id='projectWindowDock';dock.setAttribute('aria-label','열린 프로젝트 작업창');dock.hidden=true;
    layer=node('div','project-window-layer');questionAlerts=node('aside','project-question-alerts');questionAlerts.hidden=true;questionAlerts.setAttribute('aria-label','요원의 새 질문 알림');questionAlerts.setAttribute('aria-live','polite');document.body.append(layer,dock,questionAlerts);
    picker=node('dialog','project-picker');picker.id='projectPicker';picker.innerHTML='<header><div><h2>다른 작업창</h2><p>기존 고정·최근 대화와 프로젝트를 선택하세요.</p></div><button type="button" id="projectPickerClose" aria-label="프로젝트 목록 닫기">×</button></header><nav id="projectPickerTabs" class="project-browse-tabs" aria-label="작업창 목록 보기"><button type="button" data-mode="pinned" aria-pressed="false">고정</button><button type="button" data-mode="recent" aria-pressed="true">최근</button><button type="button" data-mode="projects" aria-pressed="false">프로젝트</button></nav><div class="project-picker-search"><input id="projectPickerSearch" placeholder="필요하면 이름으로 좁히기" aria-label="프로젝트 검색"><button type="button" id="projectPickerReload">새로고침</button></div><p id="projectPickerNote" role="status"></p><div id="projectPickerList"></div><footer>일반 프로젝트 네 개와 부서 작업창 여덟 개를 열 수 있어요. 접힌 창도 포함되며 같은 대화는 재사용해요. ×는 창만 닫고 입력·첨부는 보관해요.</footer>';
    pickerMode='recent';
    document.body.append(picker);$('projectPickerClose').onclick=()=>picker.close();$('projectPickerSearch').oninput=pickerRender;$('projectPickerReload').onclick=loadCatalog;
    for(const button of $('projectPickerTabs').children)button.onclick=()=>{pickerMode=button.dataset.mode;pickerRender();};
    mountRightBoard(column,stage);
    try{const saved=JSON.parse(localStorage.getItem('office-project-panels')||'[]');if(Array.isArray(saved))for(const row of saved.filter(row=>row.kind!=='chatgpt').slice(0,12))if(typeof row.id==='string'&&typeof row.host==='string'&&typeof row.title==='string')open(row,row);}catch(_){}
    const poll=()=>{if(document.hidden)return;for(const p of panels.values())if(Date.now()>=p.next){if(p.minimized)watch(p);else refresh(p);}if(browseMode==='departments'&&Date.now()>=departmentNext)loadDepartments();if(Date.now()>=questionNext)pollQuestions();};
    setInterval(poll,1000);
    document.addEventListener('visibilitychange',()=>{sectionRender();if(!document.hidden){for(const p of panels.values())p.next=0;poll();}});
    addEventListener('resize',()=>{for(const p of panels.values())if(!p.minimized)fit(p);save();});
    addEventListener('pagehide',()=>{for(const p of panels.values())persistDraft(p);save();});
    loadCatalog();loadDepartments();
    const supervisors=node('script');supervisors.src='/supervisor-switch.js';document.head.append(supervisors);
  }
  window.officeProjectThreadViews={
    mountMainRouting(row,input,refreshLocks){mainRouting=mountMainRouting(row,input,refreshLocks);return mainRouting;},
    mount(row,container){
      let p=panels.get(key(row));if(!p){open(row,{embedded:true});p=panels.get(key(row));}if(!p)throw Error('기존 총괄 대화를 연결하지 못했어요.');
      const priorScroll=p.ready?p.log.scrollTop:null;
      p.embedded=true;p.el.style.removeProperty('z-index');p.el.classList.add('is-supervisor-embedded');p.el.dataset.independentWork='true';p.el.removeAttribute('role');p.el.removeAttribute('aria-modal');
      p.minimized=true;p.viewIntent=false;p.el.hidden=true;container.append(p.el);dockRender();save();
      return {id:p.id,host:p.host,element:p.el,log:p.log,input:p.input,scroll:priorScroll,refresh:()=>refresh(p),setVisible(value,intent=false,scrollTarget){
        if(!value&&!p.minimized&&p.log.clientHeight>0)p.supervisorScroll=p.log.scrollTop;
        if(value)p.resumeScroll=scrollTarget===null?null:Number.isFinite(scrollTarget)?Math.max(0,scrollTarget):(p.supervisorScroll??null);
        p.minimized=!value;p.el.hidden=!value;p.el.classList.toggle('is-focused',value);p.viewIntent=value&&intent;
        if(value){refresh(p);requestAnimationFrame(()=>{
          // Apply after layout, but retain the target until the in-flight
          // history render consumes it. Hidden containers must not save zero.
          if(!p.minimized&&p.resumeScroll!==undefined)p.log.scrollTop=p.resumeScroll===null?p.log.scrollHeight:p.resumeScroll;
          markDisplayedCompletion(p);
        });}else{persistDraft(p);persistFiles(p);}dockRender();
      },acknowledge(){acknowledge(p);}};
    },
    async readState(row){const data=await request('project-panel-state?id='+encodeURIComponent(row.id)+'&host='+encodeURIComponent(row.host||'local'));rememberDepartmentState(row,data);return data;},
    async readHistory(row){const data=await request('project-panel?id='+encodeURIComponent(row.id)+'&host='+encodeURIComponent(row.host||'local'));rememberDepartmentState(row,data);return data;},
    seen(row){return departmentSeen.get(key(row))||null;},
    confirm(row,log,messages,completion,intent){return intent===true&&confirmDisplayedCompletion(row,log,messages,completion);}
  };
  window.officeProjectOpen=row=>{
    if(!row||typeof row.id!=='string'||typeof row.title!=='string'||!/^([0-9a-f]{8}-)([0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(row.id))throw Error('연결된 대화를 다시 선택해 주세요.');
    open({...row,kind:'codex',host:row.host||'local'});
    const p=panels.get(key(row));if(p){p.viewIntent=true;requestAnimationFrame(()=>markDisplayedCompletion(p));}
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
