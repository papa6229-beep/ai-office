/* Explicit independent work; the existing composer still steers its current task. */
(() => {
  'use strict';
  const state={items:[],selected:null,token:null,posting:false,signature:'',running:0,pending:new Set()};
  const $=id=>document.getElementById(id), id=()=>crypto.randomUUID().replaceAll('-','');
  const names={codex:'Codex',claude:'Claude',gemini:'Gemini',local:'로컬 모델'};
  const labels={running:'진행 중',done:'완료',error:'확인 필요',waiting:'선택 대기',cancelled:'중지',uncertain:'실행 기록 확인 필요'};
  let dialog,button,timer;
  function notice(text){$('independentNotice').textContent=text;}
  function selected(){return state.items.find(row=>row.id===state.selected);}
  function detail(){
    const row=selected();$('independentDetail').textContent=row?(row.answer||(row.status==='running'&&row.counts_as_work===false?'대화를 처리하고 있어요.':'이 업무를 진행하고 있어요. 상세 단계는 WORKSPACE에서 확인할 수 있어요.')):'업무를 선택하면 결과와 제어가 표시돼요.';
    $('independentStop').disabled=!row||row.status!=='running'||row.control==='native';
    $('independentFollow').disabled=!row||row.control==='native';
    if(row?.control==='native')$('independentDetail').textContent='연결된 '+row.title+' 작업이에요. '+(labels[row.status]||row.status)+' · 해당 작업창에서 상세 내용과 제어를 확인할 수 있어요.';
  }
  function render(){
    state.running=state.items.filter(row=>row.status==='running'&&row.counts_as_work!==false).length;
    button.textContent='새 업무 · 진행 '+state.running;
    button.title='에이메스 채팅·별도 업무로 직접 맡긴 진행 업무 수 · 프로젝트 작업창은 제외해요';
    document.dispatchEvent(new CustomEvent('work-independent-activity',{detail:{running:state.running}}));
    const signature=JSON.stringify(state.items.map(row=>[row.id,row.status,row.counts_as_work,row.control,row.answer,row.provider,row.execution?.model]));
    if(signature!==state.signature){
      state.signature=signature;const list=$('independentList');list.replaceChildren();
      for(const row of state.items.slice(0,10)){
        const card=document.createElement('button');card.type='button';card.className='independent-job';
        const title=document.createElement('strong'),meta=document.createElement('small');title.textContent=row.title;
        meta.textContent=(row.execution?.mode==='code'?'코드 실행 · AI 미호출':row.execution?.recovery?.length?'Codex · Sol로 이어받기':names[row.provider]||'기존 업무')+' · '+(row.status==='running'&&row.counts_as_work===false?'대화 처리 중':labels[row.status]||row.status);
        card.append(title,meta);card.addEventListener('click',()=>{state.selected=row.id;detail();if(row.control!=='native')document.dispatchEvent(new CustomEvent('work-stage-request',{detail:{requestId:row.id,message:row.title,phase:row.status==='running'?'start':'done'}}));});list.append(card);
      }
      if(!state.items.length)list.textContent='아직 기록된 요청이 없어요.';
    }
    detail();
  }
  async function refresh(){
    try{
      const response=await fetch('/api/work/jobs',{cache:'no-store'}),data=await response.json();
      if(!response.ok||data.error)throw Error(data.error||'업무 상태를 읽지 못했어요.');
      state.items=data.items||[];state.token=data.action_token;render();
      document.dispatchEvent(new CustomEvent('work-jobs-state',{detail:{items:state.items,token:state.token}}));
    }catch(error){if(dialog.open)notice(error.message);}
    clearTimeout(timer);timer=setTimeout(refresh,document.hidden?10000:1500);
  }
  async function command(message,options={}){
    if(state.posting)return;
    if(state.pending.has(message)){notice('같은 내용의 업무가 응답 대기 중이에요. 목록에서 확인해 주세요.');return;}
    state.pending.add(message);state.posting=true;$('independentStart').disabled=true;
    const requestId=id();
    try{
      await refresh();if(!state.token)throw Error('연결을 새로고침해 주세요.');
      if(state.items.filter(row=>row.status==='running'&&row.control!=='native').length>=2)throw Error('현재 두 업무가 진행 중이에요. 완료 후 새 업무를 시작해 주세요.');
      const body={request_id:requestId,message,token:state.token,...options,...(window.officeUI?{client:window.officeUI.capture()}:{})};
      notice('업무를 시작했어요. 창을 닫아도 작업은 계속돼요.');state.selected=requestId;
      document.dispatchEvent(new CustomEvent('work-stage-request',{detail:{requestId,message,phase:'start'}}));
      const pending=fetch('/api/work/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      state.posting=false;$('independentStart').disabled=false;
      const response=await pending,data=await response.json();
      if(!response.ok||data.error)throw Error(data.error||'요청 결과를 확인해 주세요.');
      if($('independentCommand').value.trim()===message)$('independentCommand').value='';
      notice(data.result?.message||'응답이 도착했어요.');
      document.dispatchEvent(new CustomEvent('work-stage-request',{detail:{requestId,message,phase:'done',result:data.result}}));
      document.dispatchEvent(new CustomEvent('work-independent-result',{detail:data}));
    }catch(error){notice(error.message+' 연결이 끊긴 경우 목록에서 원래 업무 상태를 확인해 주세요. 자동 재전송하지 않았어요.');}
    finally{state.pending.delete(message);state.posting=false;$('independentStart').disabled=false;refresh();}
  }
  async function control(kind){
    const row=selected();if(!row)return;
    const text=$('independentMessage').value.trim();if(kind==='message'&&!text)return;
    if(kind==='message'&&row.status!=='running'){
      if(row.channel==='office'){notice('기존 메인 업무의 후속 요청은 메인 채팅으로 보내 주세요.');return;}
      $('independentMessage').value='';command(text,{conversation_id:row.channel});return;
    }
    try{
      await refresh();
      const response=await fetch('/api/work/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:state.token,request_id:row.id,input_id:id(),kind,...(kind==='message'?{message:text}:{})})}),data=await response.json();
      if(!response.ok||data.error)throw Error(data.error||'제어 요청을 확인해 주세요.');
      notice(data.accepted?(kind==='stop'?'선택한 업무에 중지를 요청했어요. 이미 처리한 항목은 보존돼요.':'선택한 업무에 추가 지시를 보냈어요.'):(data.message||'업무 상태를 다시 확인해 주세요.'));
      if(data.accepted&&kind==='message')$('independentMessage').value='';
    }catch(error){notice(error.message);}finally{refresh();}
  }
  function init(){
    const send=$('workSend');if(!send)return;
    const holder=document.createElement('span');holder.dataset.independentWork='';
    button=document.createElement('button');button.type='button';button.className='work-secondary';button.textContent='새 업무';holder.append(button);send.before(holder);
    dialog=document.createElement('dialog');dialog.id='independentWorkDialog';dialog.innerHTML='<header><h3>에이메스 · 별도 업무</h3><button type="button" id="independentClose">닫기</button></header><p>기존 채팅은 현재 업무에 추가 지시합니다. 여기서는 독립된 업무를 최대 두 개까지 실행해요. 같은 화면·앱 소스·파일 실행은 차례를 기다릴 수 있어요.</p><form id="independentForm"><label for="independentProvider">업무 연결</label><select id="independentProvider"><option value="">현재 선택한 연결</option><option value="codex">Codex · 업무별 모델 선택</option><option value="claude">Claude · 저장된 구독 모델</option><option value="local">로컬 모델 · 저장된 Gemma 연결</option><option value="gemini">Gemini · 저장된 연결</option></select><label for="independentCommand">새 업무</label><textarea id="independentCommand" maxlength="4000" rows="3" required placeholder="기존 작업과 별도로 맡길 업무"></textarea><button id="independentStart" type="submit">별도 업무 시작</button></form><p id="independentNotice" role="status"></p><div id="independentList"></div><section><h4>선택한 업무</h4><pre id="independentDetail"></pre><textarea id="independentMessage" maxlength="4000" rows="2" aria-label="선택한 업무에 추가 지시" placeholder="선택한 업무에 추가로 알려줄 내용"></textarea><div><button type="button" id="independentFollow">선택 업무에 보내기</button><button type="button" id="independentStop">선택 업무 중지</button></div></section>';
    document.body.append(dialog);
    button.addEventListener('click',()=>{dialog.showModal();refresh();});$('independentClose').addEventListener('click',()=>dialog.close());
    $('independentStop').addEventListener('click',()=>control('stop'));$('independentFollow').addEventListener('click',()=>control('message'));
    $('independentForm').addEventListener('submit',event=>{event.preventDefault();if(state.posting)return;const message=$('independentCommand').value.trim();if(!message)return;const provider=$('independentProvider').value;command(message,{new_task:true,...(provider?{provider}:{})});});
    window.officeJobs={get runningCount(){return state.running;}};refresh();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
