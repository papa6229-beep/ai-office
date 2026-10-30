/* Department composition and scoped references; all chat windows use project-panels. */
(() => {
  'use strict';
  const root=document.getElementById('department-manager');
  if(!root)return;
  const labels={instruction:'업무지침',skill:'스킬',document:'기술문서'};
  let data=null,selected='',tab='journal',catalog=[],busy=false,token='',loaded=false,dialog=null,reloadPending=false,tasks=[],tasksLoading=false;
  const dayFormat=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'});
  const timeFormat=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false});
  const dayKey=value=>value&&!Number.isNaN(new Date(value).getTime())?dayFormat.format(new Date(value)):'';
  const today=()=>dayKey(new Date());
  let journalTeam='',month=today().slice(0,7),date=today();
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const button=(text,fn,cls='')=>{const b=el('button',cls,text);b.type='button';b.onclick=fn;return b;};
  const team=()=>data?.teams.find(t=>t.id===selected);
  const link=(text,url)=>{const a=el('a','dm-link',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;};
  async function api(path,body){
    const options={cache:'no-store',signal:AbortSignal.timeout(path.includes('create-chat')?90000:25000)};
    if(body){if(!token){const r=await fetch('/api/work/jobs',{cache:'no-store'});token=(await r.json()).action_token||'';}
      options.method='POST';options.headers={'Content-Type':'application/json'};options.body=JSON.stringify({...body,token});}
    const r=await fetch(path,options),v=await r.json();if(!r.ok||v.error)throw Error(v.error||'목록을 읽지 못했어요. 다시 시도해 주세요.');return v;
  }
  function notice(text,error=false){const n=root.querySelector('.dm-notice');if(n){n.textContent=text;n.dataset.error=String(error);}}
  async function load(){
    if(busy){reloadPending=true;return;}busy=true;notice('부서를 불러오고 있어요.');
    try{data=await api('/api/work/departments');if(!team())selected=data.teams[0]?.id||'';loaded=true;render();
      try{catalog=(await api('/api/work/project-panels')).items||[];if(!dialog)render();}catch(e){notice('부서 자료는 불러왔어요. 대화 상태: '+e.message,true);}
    }catch(e){notice(e.message,true);}finally{busy=false;if(reloadPending){reloadPending=false;load();}}
  }
  function field(form,label,value='',type='text'){
    const wrap=el('label','dm-field'),name=el('span','',label),input=el(type==='textarea'?'textarea':'input');
    if(type!=='textarea')input.type=type;input.value=value;wrap.append(name,input);form.append(wrap);return input;
  }
  function selectField(form,label,items,value){
    const wrap=el('label','dm-field'),input=el('select');wrap.append(el('span','',label),input);
    for(const [id,name] of items){const o=el('option','',name);o.value=id;input.append(o);}input.value=value||'';form.append(wrap);return input;
  }
  function modal(title){
    if(dialog)dialog.remove();dialog=el('dialog','dm-dialog');dialog.setAttribute('aria-label',title);
    const form=el('form'),head=el('header');head.append(el('h3','',title),button('×',()=>dialog.close(),'dm-close'));form.append(head);
    const error=el('p','dm-form-error');error.setAttribute('role','alert');
    const footer=el('footer');footer.append(button('취소',()=>dialog.close(),'dm-secondary'));
    const submit=el('button','','저장');submit.type='submit';footer.append(submit);
    dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();dialog=null;});dialog.showModal();
    return {form,error,footer,submit,finish:()=>form.append(error,footer)};
  }
  function submitForm(m,payload){
    m.form.onsubmit=async e=>{e.preventDefault();m.submit.disabled=true;m.error.textContent='';
      try{const result=await api('/api/work/department-save',payload());selected=result.team_id;dialog.close();await load();notice('오피스에 저장했어요.');}
      catch(error){m.error.textContent=error.message;m.submit.disabled=false;}};
  }
  function editTeam(row){
    const m=modal(row?'부서 설정':'부서 추가'),name=field(m.form,'부서 이름',row?.name||''),purpose=field(m.form,'담당 업무',row?.purpose||'','textarea');name.required=true;name.maxLength=80;purpose.maxLength=2000;
    m.form.append(el('p','dm-hint','부서를 추가해도 모델 작업은 시작하지 않아요. 요원에서 실제 대화를 연결할 수 있어요.'));m.finish();
    submitForm(m,()=>({action:'team',...(row?{id:row.id}:{}),name:name.value,purpose:purpose.value}));
  }
  function editAgent(row){
    const t=team(),m=modal(row?'요원 설정':'요원 추가'),name=field(m.form,'요원 이름',row?.name||''),role=field(m.form,'담당 역할',row?.role||'','textarea');name.required=true;name.maxLength=80;role.maxLength=2000;
    const candidates=catalog.filter(r=>r.host==='local'&&r.cwd?.replaceAll('\\','/').toLowerCase()==='c:/users/bnn/ai_office_github'&&r.title.length<240);
    const options=[['','연결하지 않고 저장'],...candidates.map(r=>[r.id,r.title])];
    if(row?.thread_id&&!options.some(([id])=>id===row.thread_id))options.push([row.thread_id,row.name+' · 기존 연결']);
    const thread=selectField(m.form,'실제 Codex 대화',options,row?.thread_id);
    m.form.append(el('p','dm-hint','기존 대화를 연결하거나, 저장 후 ‘새 대화 만들기’를 눌러주세요. 다른 요원과 같은 대화를 중복 연결할 수는 없어요.'));m.finish();
    submitForm(m,()=>({action:'agent',team_id:t.id,...(row?{id:row.id}:{}),name:name.value,role:role.value,thread_id:thread.value}));
  }
  function editResource(row){
    const t=team(),kind=tab,m=modal(labels[kind]+(row?' 수정':' 추가'));
    const title=field(m.form,'제목',row?.title||'');title.required=true;title.maxLength=120;
    const scope=selectField(m.form,'적용 범위',[['','부서 공통'],...t.agents.map(a=>[a.id,a.name])],row?.agent_id);
    const state=selectField(m.form,'사용 상태',[['draft','초안 · 요원에게 전달하지 않음'],['active','사용 · 해당 요원 대화에 적용']],row?.state||'draft');
    const reference=field(m.form,kind==='skill'?'SKILL.md 경로 또는 원본 링크':'노션·원본 링크 또는 문서 경로',row?.reference||'');reference.maxLength=1200;
    const content=field(m.form,kind==='instruction'?'지침 내용':'설명·실행 절차',row?.content||'','textarea');content.maxLength=12000;
    m.form.append(el('p','dm-hint',kind==='skill'?'사용으로 설정한 스킬 경로와 절차를 요원에게 전달해요. Codex의 스킬 설치 상태를 바꾸지는 않아요.':'오피스에 저장됩니다. 노션 링크를 등록해 원본을 열 수 있으며, 본문을 노션과 자동 동기화하지는 않아요.'));m.finish();
    submitForm(m,()=>({action:'resource',team_id:t.id,...(row?{id:row.id}:{}),kind,title:title.value,agent_id:scope.value,state:state.value,reference:reference.value,content:content.value}));
  }
  function remove(kind,row){
    const t=team(),m=modal(kind==='agent'?'요원 연결 해제':'자료 삭제');
    m.form.append(el('p','',row.name||row.title),el('p','dm-hint',kind==='agent'?'오피스의 요원 연결만 해제합니다. Codex 대화와 기록은 유지돼요.':'오피스에 저장한 자료를 삭제합니다. 연결된 노션 원본은 유지돼요.'));m.submit.textContent=kind==='agent'?'연결 해제':'삭제';m.finish();
    submitForm(m,()=>({action:kind==='agent'?'remove-agent':'remove-resource',team_id:t.id,id:row.id}));
  }
  function openChat(agent){
    const row=catalog.find(r=>r.id===agent.thread_id)||{id:agent.thread_id,host:'local',kind:'codex',title:agent.name,project:'ai office'};
    try{if(!window.officeProjectOpen)throw Error('작업창 연결을 불러오지 못했어요. 새로고침해 주세요.');window.officeProjectOpen(row);}
    catch(e){notice(e.message,true);}
  }
  function createChat(agent){
    const t=team(),m=modal('요원의 새 Codex 대화 만들기');m.submit.textContent='만들기';
    m.form.append(el('p','',t.name+' · '+agent.name),el('p','dm-hint','현재 기본 모델로 같은 AI OFFICE 프로젝트에 대화를 만듭니다. 초기 준비 응답에 모델 사용량이 소모돼요. 실제 업무는 시작하지 않습니다.'));m.finish();
    const request=crypto.randomUUID().replaceAll('-','');
    m.form.onsubmit=async e=>{e.preventDefault();m.submit.disabled=true;
      try{const r=await api('/api/work/department-create-chat',{team_id:t.id,agent_id:agent.id,request_id:request});
        if(r.state!=='ready')throw Error('생성 결과가 확정되지 않았어요. 재생성하지 말고 Codex 목록에서 확인한 뒤 연결해 주세요.');
        dialog.close();await load();notice('새 대화를 만들었어요. 대화 열기에서 확인할 수 있어요.');
      }catch(error){m.error.textContent=error.message;/* uncertain creation is never resent */m.submit.hidden=true;}
    };
  }
  async function loadTasks(){
    if(tasksLoading||!team())return;tasksLoading=true;const id=selected;
    try{const r=await api('/api/work/department-tasks?team_id='+encodeURIComponent(id));if(selected===id){tasks=r.items||[];if(tab==='tasks'&&!dialog)render();}}
    catch(e){notice(e.message,true);}finally{tasksLoading=false;}
  }
  async function refreshJournal(force=false){
    if(busy)return;
    try{const fresh=await api('/api/work/departments');const changed=JSON.stringify(data.journal)!==JSON.stringify(fresh.journal);data=fresh;if(!dialog&&tab==='journal'&&(force||(changed&&!root.contains(document.activeElement))))render();}
    catch(e){notice(e.message,true);}
  }
  function moveMonth(offset){
    const [y,m]=month.split('-').map(Number),d=new Date(Date.UTC(y,m-1+offset,1));
    month=d.toISOString().slice(0,7);date='';render();
  }
  function renderJournal(detail){
    if(!data.journal){detail.append(el('p','dm-empty','업무일지 서버 반영 대기 중입니다. 기존 진행 업무는 배정업무 탭에서 확인할 수 있어요.'),button('반영 상태 다시 확인',()=>refreshJournal(true),'dm-secondary'));return;}
    const journal=data.journal||{},all=journal.items||[];
    const rows=all.filter(r=>!journalTeam||r.team_id===journalTeam),inMonth=rows.filter(r=>dayKey(r.work_at).startsWith(month));
    const toolbar=el('div','dm-journal-toolbar'),filters=el('label','dm-journal-filter'),select=el('select');
    filters.append(el('span','','부서'),select);select.setAttribute('aria-label','업무일지 부서');
    for(const [value,name] of [['','전체 부서'],...data.teams.map(t=>[t.id,t.name])]){const o=el('option','',name);o.value=value;select.append(o);}
    select.value=journalTeam;select.onchange=()=>{journalTeam=select.value;if(journalTeam)selected=journalTeam;render();};
    const tools=el('div','dm-actions');
    if(journal.calendar_url)tools.append(link('노션 업무 달력',journal.calendar_url));
    tools.append(button('기록 새로고침',()=>refreshJournal(true),'dm-secondary'));toolbar.append(filters,tools);detail.append(toolbar);
    const status=el('p','dm-journal-sync');status.setAttribute('role','status');
    status.textContent=journal.error||((journal.pending_count?`노션 반영 대기 ${journal.pending_count}건. 기존 배정·보고 업무에서 반영합니다.`:all.length?'현재 기록이 노션에 반영되어 있어요.':'아직 기록된 배정 업무가 없어요.')+' 서울 시간 · 업무일시 기준');
    if(journal.error)status.dataset.error='true';detail.append(status);
    const layout=el('div','dm-journal-layout'),calendar=el('section','dm-calendar'),list=el('section','dm-journal-list');
    calendar.setAttribute('aria-label','업무일지 월 달력');
    const head=el('header','dm-calendar-heading'),monthTitle=el('h4','',month.replace('-','년 ')+'월'),prev=button('‹',()=>moveMonth(-1),'dm-secondary'),next=button('›',()=>moveMonth(1),'dm-secondary');
    prev.setAttribute('aria-label','이전 달');next.setAttribute('aria-label','다음 달');
    const nav=el('div','dm-actions');nav.append(prev,next);head.append(monthTitle,nav);calendar.append(head);
    const weekdays=el('div','dm-calendar-weekdays');for(const name of ['일','월','화','수','목','금','토'])weekdays.append(el('span','',name));calendar.append(weekdays);
    const grid=el('div','dm-calendar-grid'),[year,mo]=month.split('-').map(Number),start=new Date(Date.UTC(year,mo-1,1)).getUTCDay(),days=new Date(Date.UTC(year,mo,0)).getUTCDate();
    for(let i=0;i<start;i++){const blank=el('span','dm-calendar-blank');blank.setAttribute('aria-hidden','true');grid.append(blank);}
    for(let d=1;d<=days;d++){
      const key=month+'-'+String(d).padStart(2,'0'),entries=inMonth.filter(r=>dayKey(r.work_at)===key);
      const b=button('',()=>{date=date===key?'':key;render();},'dm-calendar-day');
      b.setAttribute('aria-label',`${key} 업무 ${entries.length}건`);b.setAttribute('aria-pressed',String(date===key));
      if(key===today())b.setAttribute('aria-current','date');b.append(el('span','',String(d)));
      if(entries.length){b.append(el('small','',entries.length+'건'));b.dataset.hasWork='true';}
      grid.append(b);
    }
    calendar.append(grid);
    const calendarFoot=el('footer','dm-calendar-footer');calendarFoot.append(button('오늘',()=>{date=today();month=date.slice(0,7);render();},'dm-secondary'),button('이달 전체',()=>{date='';render();},'dm-text-button'));calendar.append(calendarFoot);
    const filtered=date?inMonth.filter(r=>dayKey(r.work_at)===date):inMonth;
    const listHead=el('header','dm-journal-list-heading');listHead.append(el('h4','',date?date.replaceAll('-','.'):month.replace('-','.')+' 전체'),el('span','',filtered.length+'건'));list.append(listHead);
    for(const r of filtered){
      const card=el(r.notion_url?'a':'article','dm-journal-card');
      if(r.notion_url){card.href=r.notion_url;card.target='_blank';card.rel='noopener noreferrer';card.setAttribute('aria-label',r.title+' · 노션 원문 열기');}
      card.dataset.status=r.status;
      const top=el('div','dm-journal-card-top'),label=el('span','dm-journal-team',r.team_name),state=el('span','dm-state',r.status);top.append(label,state);
      card.append(top,el('h4','',r.title));
      const meta=el('p','dm-journal-meta'),time=el('time','',r.work_at?dayKey(r.work_at).slice(5).replace('-','.')+' '+timeFormat.format(new Date(r.work_at)):'업무일시 미확인');
      if(r.work_at)time.dateTime=r.work_at;meta.append(time,el('span','',r.agent_name));card.append(meta,el('p','dm-journal-summary',r.summary));
      const foot=el('div','dm-journal-card-foot'),sync=el('span',r.sync_state==='synced'?'dm-sync-ok':'dm-sync-pending',r.sync_state==='synced'?'노션 반영됨':'노션 반영 대기');
      foot.append(sync,el('span','',r.notion_url?'원문 열기 ↗':'원문 연결 대기'));card.append(foot);
      if(r.sync_error)card.append(el('p','dm-hint',r.sync_error));
      if(r.last_event){const stamp=el('small','dm-journal-last',`${r.last_event.time_basis} ${timeFormat.format(new Date(r.last_event.at))} · ${r.last_event.kind==='work_note'?'작업 기록 갱신':r.last_event.text}`);card.append(stamp);}
      list.append(card);
    }
    if(!filtered.length)list.append(el('p','dm-empty',date?'이 날짜에 배정된 업무가 없어요. 달력에서 다른 날짜를 선택하거나 이달 전체를 보세요.':'이번 달에 배정된 업무가 없어요. 다른 달이나 부서를 선택해 보세요.'));
    layout.append(calendar,list);detail.append(layout,el('p','dm-hint','실제 배정·진행·결과와 중요한 일정·문서·결정만 기록합니다. 단순 질문·조회·잡담은 기록하지 않아요.'));
  }
  function render(){
    root.replaceChildren();const header=el('header','dm-heading'),title=el('div');title.append(el('h2','','에이메스 부서관리'),el('p','dm-subtitle','팀별 대화와 실행 자료를 한곳에서 관리하세요.'));
    const actions=el('div','dm-actions');actions.append(link('노션 문서함',data.notion_url),button('새로고침',load,'dm-secondary'),button('＋ 부서 추가',()=>editTeam()));header.append(title,actions);
    const note=el('p','dm-notice');note.setAttribute('role','status');root.append(header,note);
    const layout=el('div','dm-layout'),roster=el('nav','dm-roster');roster.setAttribute('aria-label','에이메스 부서');
    const overview=button('',()=>{tab='journal';journalTeam='';render();},'dm-team dm-overview');overview.setAttribute('aria-current',String(tab==='journal'&&!journalTeam));overview.append(el('strong','','전체 업무일지'),el('span','',data.journal?`기록 ${data.journal.items.length}건 · 서울 시간`:'서버 반영 대기'));roster.append(overview);
    for(const t of data.teams){const b=button('',()=>{selected=t.id;journalTeam=t.id;render();if(tab==='tasks')loadTasks();},'dm-team');b.setAttribute('aria-current',String(t.id===selected&&(tab!=='journal'||journalTeam===t.id)));b.append(el('strong','',t.name),el('span','',`요원 ${t.agents.length} · 사용 자료 ${t.resources.filter(r=>r.state==='active').length}`));roster.append(b);}
    const detail=el('article','dm-detail'),t=team();layout.append(roster,detail);root.append(layout);
    if(!t){detail.append(el('p','dm-empty','부서를 추가해서 시작하세요.'));return;}
    const head=el('header','dm-team-heading');const h=el('div');h.append(el('h3','',t.name),el('p','dm-subtitle',t.purpose||'담당 업무를 아직 정하지 않았어요.'));head.append(h,button('부서 설정',()=>editTeam(t),'dm-secondary'));detail.append(head);
    const tabs=el('nav','dm-tabs');tabs.setAttribute('aria-label','부서 자료');
    for(const [id,name] of [['journal','업무일지·달력'],['agents','대화·요원'],['tasks','배정업무'],...Object.entries(labels)]){const b=button(name,()=>{tab=id;render();if(tab==='tasks')loadTasks();});b.setAttribute('aria-pressed',String(tab===id));tabs.append(b);}detail.append(tabs);
    if(tab==='journal'){
      h.replaceChildren(el('h3','',journalTeam?(data.teams.find(t=>t.id===journalTeam)?.name||'부서 업무일지'):'전체 업무일지'),el('p','dm-subtitle','배정부터 보고까지, 날짜로 찾고 노션 원문으로 확인하세요.'));head.lastChild.hidden=true;
      renderJournal(detail);return;
    }
    const tools=el('div','dm-tools');tools.append(el('p','dm-hint',tab==='tasks'?'대표님의 요청을 실장이 배정하고, 부서 완료보고를 취합합니다. 완료 확인은 코드로 진행돼요.':tab==='agents'?'기존 대화와 기록을 그대로 사용합니다. 대화창을 열어도 새 작업은 시작하지 않아요.':'‘사용’ 자료만 해당 요원의 다음 오피스 전송에 전달돼요. 변경 내용은 한 번만 전달합니다.'),button(tab==='tasks'?'상태 새로고침':'＋ '+(tab==='agents'?'요원':labels[tab])+' 추가',()=>tab==='tasks'?loadTasks():tab==='agents'?editAgent():editResource()));detail.append(tools);
    if(tab==='agents'){
      for(const agent of t.agents){const card=el('section','dm-agent');const row=catalog.find(r=>r.id===agent.thread_id);
        const body=el('div');body.append(el('h4','',agent.name),el('p','',agent.role||'담당 역할 미설정'),el('small','',agent.thread_id?(row?.status||'대화 연결됨 · 상태 미조회'):'대화 연결 전'));
        const buttons=el('div','dm-actions');buttons.append(button(agent.thread_id?'대화 열기':'새 대화 만들기',()=>agent.thread_id?openChat(agent):createChat(agent)),button('설정',()=>editAgent(agent),'dm-secondary'),button('연결 해제',()=>remove('agent',agent),'dm-text-button'));card.append(body,buttons);detail.append(card);}
      if(!t.agents.length)detail.append(el('p','dm-empty','요원을 추가하고 기존 대화를 연결하거나 새 대화를 만들어주세요.'));
    }else if(tab==='tasks'){
      const names={queued:'배정 대기',dispatching:'전달 확인 중',working:'작업 중',waiting:'확인 필요',uncertain:'전달 여부 확인 필요',completed:'부서 응답 완료',failed:'부서 실행 실패',cancelled:'부서 실행 중단'};
      const reports={pending:'실장 보고 대기',sending:'실장 보고 전달 중',awaiting:'실장 정리 중',uncertain:'실장 전달 확인 필요',reported:'대표 보고 완료',failed:'실장 보고 미완료'};
      const rows=tasks.filter(r=>selected==='hq'||r.team_id===selected);
      for(const r of rows){const card=el('section','dm-resource'),heading=el('div','dm-resource-heading');heading.append(el('h4','',r.title),el('span','dm-state',names[r.state]||r.state));card.append(heading,el('small','',r.team_name+' · '+r.agent_name+' · '+(reports[r.report_state]||r.report_state)));
        if(r.hq_answer||r.result)card.append(el('p','dm-content',r.hq_answer||r.result));
        if(r.error)card.append(el('p','dm-hint',r.error));
        card.append(button('부서 대화 열기',()=>openChat({thread_id:r.thread_id,name:r.agent_name}),'dm-secondary'));detail.append(card);}
      if(!rows.length)detail.append(el('p','dm-empty','아직 배정된 업무가 없어요. 본부 채팅에서 업무를 말씀해 주세요.'));
    }else{
      const rows=t.resources.filter(r=>r.kind===tab);
      for(const r of rows){const card=el('section','dm-resource'),heading=el('div','dm-resource-heading');heading.append(el('h4','',r.title),el('span','dm-state',r.state==='active'?'사용':'초안'));card.append(heading,el('small','',r.agent_id?(t.agents.find(a=>a.id===r.agent_id)?.name||'요원 미연결'):'부서 공통'));
        if(r.content)card.append(el('p','dm-content',r.content));
        if(r.reference){if(/^https:\/\//i.test(r.reference))card.append(link('원본 문서 열기',r.reference));else card.append(el('p','dm-path',r.reference));}
        const buttons=el('div','dm-actions');buttons.append(button('수정',()=>editResource(r),'dm-secondary'),button('삭제',()=>remove('resource',r),'dm-text-button'));card.append(buttons);detail.append(card);}
      if(!rows.length)detail.append(el('p','dm-empty',labels[tab]+' 목록이 비어 있어요. 부서 공통 또는 요원별로 추가할 수 있어요.'));
    }
    const foot=el('footer','dm-footer');foot.append(link('운영팀 구상 원본',data.concept_url),el('span','','자료는 오피스에 저장 · 노션은 원본 링크 연결'));detail.append(foot);
  }
  setInterval(()=>{if(!document.hidden&&document.body.dataset.activeView==='department-manager'&&!dialog){if(tab==='tasks')loadTasks();else if(tab==='journal')refreshJournal();}},10000);
  root.append(el('p','dm-notice','부서관리를 불러올 준비가 됐어요.'));
  const activate=()=>{if(document.body.dataset.activeView==='department-manager'&&!loaded)load();};
  new MutationObserver(activate).observe(document.body,{attributes:true,attributeFilter:['data-active-view']});activate();
})();
