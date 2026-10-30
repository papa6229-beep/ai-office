/* Metadata-only intake. Moves require a human action or an explicitly enabled rule. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),node=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  const labels={pending:'받는 중',ready:'위치 지정',existing:'이전 파일',organizing:'정리 중',organized:'정리 완료',error:'확인 필요',sent:'전송 완료',unknown:'전송 확인 필요',dispatching:'전송 확인 중',awaiting_confirmation:'전송 확인 중'};
  const sources={hanbiro:'한비로톡',nateon:'네이트온',downloads:'다운로드 폴더'};
  const when=value=>value?new Date(value*1000).toLocaleString('ko-KR'):'';
  let direction='in',unified=false,historySelection=null,detailSequence=0,historyToken='';
  async function actionToken(){
    if(historyToken)return historyToken;
    const response=await fetch('/api/work/jobs',{cache:'no-store',signal:AbortSignal.timeout(5000)}),data=await response.json();
    if(!response.ok||!data.action_token)throw Error('오피스 연결을 다시 확인해 주세요.');
    historyToken=data.action_token;return historyToken;
  }
  async function history(query='',body){
    let options={cache:'no-store',signal:AbortSignal.timeout(20000)};
    if(body)options={...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,token:await actionToken()})};
    const response=await fetch('/api/work/file-history'+query,options),data=await response.json();
    if(response.status===403)historyToken='';
    if(!response.ok||data.error)throw Error(data.error||'파일 이력을 확인하지 못했어요.');
    if(data.action_token)historyToken=data.action_token;return data;
  }
  async function openHistory(item){
    const sequence=++detailSequence;
    const renderHistory=(row,events,initial=false)=>{
      if(sequence!==detailSequence||(!unified&&row.direction!==direction))return false;
      const intake=snapshot?.items.find(r=>r.id===row.intake_id);
      if(initial)open(intake||{id:row.intake_id||'',name:row.name,path:row.original_path,target:row.current_path,state:row.state,source:row.source,suggestions:[]});
      historySelection=row;
      for(const id of ['receivedHistoryMeta','receivedReveal','receivedTimeline'])$(id).hidden=false;
      $('receivedPath').textContent='현재 위치: '+row.current_path;
      $('receivedHistoryMeta').textContent=[row.person||'상대방 미확인',row.person_origin==='user'?'사용자 지정':row.person_origin==='messenger'?'메신저 확인':'',sources[row.source],when(row.timestamp),row.size<1024*1024?Math.ceil(row.size/1024)+' KB':(row.size/1024/1024).toFixed(1)+' MB'].filter(Boolean).join(' · ');
      $('receivedReveal').disabled=initial||!row.available;
      $('receivedReveal').textContent=row.available?'파일 위치 열기':row.changed?'파일 변경됨':'현재 위치 확인 필요';
      const list=$('receivedTimeline');list.replaceChildren();
      const eventNames={received:'받음',saved:'PC 저장 확인',sent:'보냄',send_unconfirmed:'전송 미확인',organized:'정리 완료',move:'위치 변경'};
      for(const event of events||[]){const li=node('li');li.append(node('strong',eventNames[event.kind]||event.kind),node('span',[when(event.at),event.person||'',event.path||''].filter(Boolean).join('\n')));list.append(li);}
      $('receivedPersonArea').hidden=row.person_origin==='messenger';$('receivedPersonName').value=row.person||'';
      const canOrganize=row.direction==='in'&&row.state!=='organized';
      for(const id of ['receivedRule','receivedDestination','receivedGrouping'])$(id).closest('label').hidden=!canOrganize;
      $('receivedOrganize').hidden=!canOrganize;$('receivedRuleDetails').hidden=row.direction!=='in';
      return true;
    };
    renderHistory(item,null,true);$('receivedModalNote').textContent='현재 위치를 확인하고 있어요…';
    try{
      const data=await history('?id='+encodeURIComponent(item.id));
      if(renderHistory(data.item,data.events)){
        renderConnections(data.connections||{},data.item);
        if(!posting)$('receivedModalNote').textContent='';
      }
    }catch(error){if(sequence===detailSequence){$('receivedModalNote').textContent=error.message;$('receivedReveal').disabled=true;}}
  }
  function renderConnections(data,row){
    const box=$('receivedConnections');box.replaceChildren();box.hidden=false;
    box.append(node('h4','대화 · 업무 · 관련 자료'));
    const conversation=data.conversation;
    if(conversation){
      const button=node('button','연결된 대화 보기 · '+conversation.title);button.type='button';
      button.title='오피스에 수집된 해당 대화를 열어요. 메신저 전체 과거 이력은 아니에요.';
      button.onclick=async()=>{
        if(posting)return;button.disabled=true;
        try{
          if(typeof window.workNotificationOpenConversation!=='function')throw Error('오피스를 새로고침해 주세요.');
          await window.workNotificationOpenConversation(conversation.id);$('receivedDialog').close();
        }catch(error){$('receivedModalNote').textContent=error.message;}finally{button.disabled=false;}
      };box.append(button);
    }else box.append(node('p',data.conversation_note||'확인된 원본 대화 연결이 없어요.'));
    const tasks=node('ul');
    for(const task of data.tasks||[])tasks.append(node('li',task.title+' · '+({todo:'대기',doing:'진행',review:'검토',done:'완료'}[task.status]||task.status)));
    box.append(node('strong','연결된 업무'),tasks);
    if(!(data.tasks||[]).length)box.append(node('p','아직 연결한 업무가 없어요.'));
    const options=data.task_options||[];
    if(options.length){
      const controls=node('div');controls.className='material-link-controls';
      const select=node('select');select.setAttribute('aria-label','자료를 연결할 업무');select.append(new Option('업무 선택',''));
      const linked=new Set((data.tasks||[]).map(t=>t.id));
      for(const task of options)if(!linked.has(task.id))select.append(new Option(task.title,task.id));
      const connect=node('button','이 업무에 연결');connect.type='button';connect.disabled=true;
      select.onchange=()=>{connect.disabled=!select.value||!row.available;};
      connect.onclick=async()=>{
        if(posting||!select.value)return;const sequence=detailSequence;posting=true;connect.disabled=true;
        try{await history('',{action:'link-task',id:row.id,task_id:select.value});if(sequence!==detailSequence)return;await openHistory(row);$('receivedModalNote').textContent='파일을 해당 업무에 연결했어요. 파일은 이동하지 않았어요.';}
        catch(error){$('receivedModalNote').textContent=error.message;}
        finally{posting=false;connect.disabled=!select.value||!row.available;}
      };controls.append(select,connect);box.append(controls);
    }
    for(const other of data.related_files||[]){
      const button=node('button',(other.evidence==='same_content'?'같은 내용 · ':'같은 정리 작업 · ')+other.name);button.type='button';
      button.onclick=()=>{unified=true;$('receivedAll').checked=true;openHistory(other);};box.append(button);
    }
  }
  let snapshot=null,selected=null,editing=null,ruleDraftId='',offset=0,loading=false,posting=false,signature='';
  async function request(body){
    let options={cache:'no-store'};
    if(body)options={...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,token:await actionToken()})};
    const url='/api/work/received-files'+(body?'':'?existing='+($('receivedExisting').checked?'1':'0')+'&q='+encodeURIComponent($('receivedSearch').value)+'&offset='+offset);
    const response=await fetch(url,options),data=await response.json();if(!response.ok||data.error)throw Error(data.error||'받은 파일을 확인하지 못했어요.');return data;
  }
  function note(text,error=false){$('receivedNote').textContent=text;$('receivedNote').classList.toggle('is-error',error);}
  function chosenRule(){return snapshot?.rules.find(r=>r.id===$('receivedRule').value);}
  function fillRule(rule){
    $('receivedDestination').value=rule?.destination||'';$('receivedGrouping').value=rule?.grouping||'plain';
    $('receivedRuleTitle').value=rule?.title||'';$('receivedExtensions').value=(rule?.extensions||[selected?.name.match(/\.[^.]+$/)?.[0]||'']).join(', ');
    $('receivedContains').value=rule?.contains||'';$('receivedRuleSource').value=rule?.source||selected?.source||'*';$('receivedAutomatic').checked=rule?.automatic===true;
  }
  function open(item){
    historySelection=null;
    if($('receivedConnections'))$('receivedConnections').hidden=true;
    for(const id of ['receivedHistoryMeta','receivedReveal','receivedTimeline','receivedPersonArea'])if($(id))$(id).hidden=true;
    selected=item;editing=null;ruleDraftId=crypto.randomUUID().replaceAll('-','');$('receivedTitle').textContent=item.name;$('receivedPath').textContent=item.path;
    $('receivedDetailState').textContent=[labels[item.state],item.error,item.target?'정리 위치: '+item.target:''].filter(Boolean).join('\n');
    const select=$('receivedRule');select.replaceChildren(new Option('정리 위치 직접 지정',''));
    for(const rule of snapshot?.rules||[])select.add(new Option(rule.title+(rule.automatic?' · 자동':' · 수동'),rule.id));
    select.value=item.suggestions.length===1?item.suggestions[0]:'';fillRule(chosenRule());
    $('receivedOrganize').hidden=item.state==='organized';$('receivedOrganize').disabled=!['ready','existing','error'].includes(item.state);
    $('receivedOrganize').textContent=item.state==='error'&&item.job?'이전 정리 재개':'이 위치로 정리';
    $('receivedRuleDetails').open=false;$('receivedModalNote').textContent='';
    for(const id of ['receivedRule','receivedDestination','receivedGrouping'])$(id).closest('label').hidden=false;
    $('receivedRuleDetails').hidden=false;
    if(!$('receivedDialog').open)$('receivedDialog').showModal();
  }
  async function poll(){
    if(loading)return;loading=true;
    const view=()=>[direction,unified,$('receivedSearch').value,offset,$('receivedExisting').checked,$('receivedPeriod').value,$('receivedKind').value].join('|'),requested=view();
    try{
      const data=await request();snapshot=data;$('receivedCount').textContent=data.ready?String(data.ready):'';
      const files=await history('?'+new URLSearchParams({direction:unified?'all':direction,q:$('receivedSearch').value,existing:$('receivedExisting').checked?'1':'0',offset,period:$('receivedPeriod').value,kind:$('receivedKind').value}));
      if(view()!==requested)return;
      $('receivedSummary').textContent=files.scope;
      const sig=JSON.stringify([files.items,data.rules,files.total,offset,direction]);if(sig!==signature){
        signature=sig;const root=$('receivedList'),top=root.scrollTop;root.replaceChildren();
        for(const item of files.items){
          const card=node('button');card.type='button';card.className='received-card';card.dataset.state=item.state;
          const title=node('strong',item.name),badge=node('small',(unified?(item.direction==='out'?'보냄 · ':'받음 · '):'')+(labels[item.state]||item.state)),meta=node('span',(item.person||'상대방 미확인')+' · '+(sources[item.source]||item.source)+' · '+when(item.timestamp));
          card.append(title,badge,meta);card.title=item.name;card.onclick=()=>openHistory(item);root.append(card);
        }
        if(!files.items.length)root.append(node('p',direction==='out'?'확인된 보낸 파일 기록이 없어요.':'아직 기록이 없어요. 이미 받은 자료는 ‘이전 파일’에서 볼 수 있어요.'));
        root.scrollTop=top;$('receivedPage').textContent=files.total?`${offset+1}–${Math.min(offset+60,files.total)} / ${files.total}`:'0개';
        $('receivedPrevious').disabled=offset===0;$('receivedNext').disabled=!files.has_more;
        const rules=$('receivedRuleList');rules.replaceChildren();
        for(const rule of data.rules){
          const button=node('button',rule.title+' · '+(rule.automatic?'자동':'수동 추천')+' · '+rule.uses+'회');button.type='button';
          button.onclick=()=>{open({id:'',name:'정리 규칙',path:rule.destination,source:rule.source,state:'organized',suggestions:[rule.id]});editing=rule;$('receivedRuleDetails').open=true;};rules.append(button);
        }
      }
      $('receivedScope').textContent=data.scope;
      if(selected?.id&&$('receivedDialog').open){const item=data.items.find(i=>i.id===selected.id);if(item){selected=item;$('receivedDetailState').textContent=[labels[item.state],item.error,item.target?'정리 위치: '+item.target:''].filter(Boolean).join('\n');$('receivedOrganize').disabled=!['ready','existing','error'].includes(item.state);if(item.state==='organized')$('receivedOrganize').hidden=true;}}
    }catch(error){note(error.message,true);}finally{loading=false;if(view()!==requested)poll();}
  }
  async function action(button,body,success){
    if(posting)return;posting=true;button.disabled=true;$('receivedModalNote').textContent='처리하고 있어요~';
    try{await request(body);$('receivedModalNote').textContent=success;signature='';await poll();}
    catch(error){$('receivedModalNote').textContent=error.message;}finally{posting=false;button.disabled=button.id==='receivedOrganize'&&!['ready','existing','error'].includes(selected?.state);}
  }
  function init(){
    if(!$('workStage'))return;
    const style=node('link');style.rel='stylesheet';style.href='/received-files.css';document.head.append(style);
    const tab=node('button');tab.id='workStageFilesTab';tab.type='button';tab.setAttribute('role','tab');tab.setAttribute('aria-selected','false');tab.setAttribute('aria-controls','workReceivedFiles');tab.tabIndex=-1;tab.append(node('span','받은파일'));
    const count=node('small');count.id='receivedCount';tab.append(count);tab.onclick=()=>{window.workStageSelect('files');};
    const sent=node('button','보낸파일');sent.id='workStageSentTab';sent.type='button';sent.setAttribute('role','tab');sent.setAttribute('aria-selected','false');sent.setAttribute('aria-controls','workReceivedFiles');sent.tabIndex=-1;sent.onclick=()=>window.workStageSelect('sent');
    document.querySelector('.work-stage-primary-tabs').prepend(tab,sent);
    const pane=node('section');pane.id='workReceivedFiles';pane.hidden=true;pane.setAttribute('role','tabpanel');pane.setAttribute('aria-labelledby',tab.id);
    pane.innerHTML='<div class="received-toolbar"><input id="receivedSearch" aria-label="받은 파일 검색" placeholder="파일명 검색"><label><input id="receivedExisting" type="checkbox"> 이전 파일</label></div><p id="receivedSummary"></p><div id="receivedList"></div><div class="received-pagination"><button id="receivedPrevious" type="button" aria-label="이전 받은 파일">‹</button><span id="receivedPage"></span><button id="receivedNext" type="button" aria-label="다음 받은 파일">›</button></div><p id="receivedNote" role="status"></p><details class="received-settings"><summary>수신 폴더·정리 규칙</summary><p id="receivedScope"></p><p>처음 연결한 폴더의 기존 자료는 자동으로 옮기지 않아요. 규칙은 수동 추천으로 시작하고, 자동 적용을 켠 조건만 새 파일에 사용해요.</p><form id="receivedSourceForm"><label>메신저<select id="receivedSource"><option value="nateon">네이트온</option><option value="hanbiro">한비로톡</option></select></label><label>실제 저장 폴더<input id="receivedSourceRoot" placeholder="C:\\Users\\...\\받은 파일" required></label><button type="submit">폴더 연결</button></form><div id="receivedRuleList"></div></details>';
    $('workStage').append(pane);
    const dialog=node('dialog');dialog.id='receivedDialog';dialog.className='work-dialog received-dialog';dialog.setAttribute('aria-labelledby','receivedTitle');
    dialog.innerHTML='<header><h3 id="receivedTitle"></h3><button id="receivedClose" type="button">닫기 ×</button></header><p id="receivedPath"></p><p id="receivedDetailState" role="status"></p><label>정리 방법<select id="receivedRule"></select></label><label>정리할 폴더<input id="receivedDestination" placeholder="D:\\웹디자인팀 관리\\작업용파일"></label><label>하위 폴더<select id="receivedGrouping"><option value="plain">지정한 폴더에 바로</option><option value="date_brand">날짜·브랜드별 폴더</option><option value="product_region">일본·중국·그외 / 날짜·브랜드</option></select></label><button id="receivedOrganize" type="button">이 위치로 정리</button><details id="receivedRuleDetails"><summary>이 정리 방법 기억하기</summary><label>규칙 이름<input id="receivedRuleTitle" maxlength="120"></label><label>메신저<select id="receivedRuleSource"><option value="*">모든 등록 메신저</option><option value="nateon">네이트온</option><option value="hanbiro">한비로톡</option></select></label><label>확장자 · 쉼표로 구분<input id="receivedExtensions" placeholder=".xlsx, .zip"></label><label>파일명에 포함된 말 · 선택<input id="receivedContains" maxlength="200"></label><label class="received-auto"><input id="receivedAutomatic" type="checkbox"> 이 조건의 새 파일 자동 정리</label><button id="receivedSaveRule" type="button">규칙 기억하기</button></details><p id="receivedModalNote" role="status"></p>';
    document.body.append(dialog);$('receivedClose').onclick=()=>dialog.close();$('receivedRule').onchange=()=>fillRule(chosenRule());
    const meta=node('p');meta.id='receivedHistoryMeta';const reveal=node('button','파일 위치 열기');reveal.id='receivedReveal';reveal.type='button';const timeline=node('ol');timeline.id='receivedTimeline';
    const person=node('div');person.id='receivedPersonArea';const personLabel=node('label','상대방 이름 · 사용자 지정'),personInput=node('input'),personSave=node('button','이름 기록');personInput.id='receivedPersonName';personInput.maxLength=120;personSave.type='button';personLabel.append(personInput);person.append(personLabel,personSave);
    $('receivedPath').before(meta);$('receivedDetailState').after(reveal,timeline,person);
    reveal.onclick=async()=>{if(!historySelection)return;const ident=historySelection.id,sequence=detailSequence;reveal.disabled=true;$('receivedModalNote').textContent='현재 파일 폴더를 열고 있어요…';try{const data=await history('',{action:'reveal',id:ident});if(sequence===detailSequence){$('receivedPath').textContent='현재 위치: '+data.path;$('receivedModalNote').textContent='현재 파일 폴더를 열었어요.';}}catch(error){if(sequence===detailSequence)$('receivedModalNote').textContent=error.message;}finally{if(sequence===detailSequence)reveal.disabled=!historySelection?.available;}};
    personSave.onclick=async()=>{if(!historySelection)return;personSave.disabled=true;try{await history('',{action:'person',id:historySelection.id,person:personInput.value});await openHistory(historySelection);signature='';poll();}catch(error){$('receivedModalNote').textContent=error.message;}finally{personSave.disabled=false;}};
    window.addEventListener('workspacetabchange',event=>{if(!['files','sent'].includes(event.detail))return;direction=event.detail==='sent'?'out':'in';offset=0;signature='';historySelection=null;detailSequence++;$('receivedList').replaceChildren(node('p','파일 이력을 확인하고 있어요.'));$('workReceivedFiles').setAttribute('aria-labelledby',direction==='out'?'workStageSentTab':'workStageFilesTab');$('receivedExisting').closest('label').hidden=direction==='out';document.querySelector('#workReceivedFiles .received-settings').hidden=direction==='out';$('workReceivedFiles').classList.toggle('is-sent',direction==='out');poll();});
    $('receivedSearch').placeholder='사람 · 파일명 · 위치, 여러 단어로 검색';$('receivedSearch').setAttribute('aria-label','자료 검색');
    const filters=node('div');filters.className='material-filters';
    const allLabel=node('label'),all=node('input');all.type='checkbox';all.id='receivedAll';allLabel.append(all,document.createTextNode('수발신 통합'));
    const period=node('select');period.id='receivedPeriod';period.setAttribute('aria-label','자료 수발신 기간');
    for(const [value,label] of [['all','전체 기간'],['today','오늘'],['yesterday','어제'],['last7','최근 7일'],['last_week','지난주'],['last30','최근 30일']])period.append(new Option(label,value));
    const kind=node('select');kind.id='receivedKind';kind.setAttribute('aria-label','자료 종류');
    for(const [value,label] of [['all','모든 종류'],['excel','엑셀'],['image','이미지'],['archive','압축파일'],['pdf','PDF'],['document','문서']])kind.append(new Option(label,value));
    const filterChanged=()=>{offset=0;signature='';poll();};all.onchange=()=>{unified=all.checked;filterChanged();};period.onchange=kind.onchange=filterChanged;
    filters.append(allLabel,period,kind);$('receivedSearch').closest('.received-toolbar').after(filters);
    const connections=node('section');connections.id='receivedConnections';connections.hidden=true;$('receivedTimeline').after(connections);
    window.addEventListener('workspacetabchange',event=>{if(['files','sent'].includes(event.detail)){unified=false;all.checked=false;}});
    const resetRule=()=>{$('receivedRule').value='';};$('receivedDestination').oninput=resetRule;$('receivedGrouping').onchange=resetRule;
    $('receivedOrganize').onclick=()=>{
      if(!selected?.id)return;
      const body=selected.state==='error'&&selected.job?{action:'resume',id:selected.id}:{action:'organize',ids:[selected.id],destination:$('receivedDestination').value,grouping:$('receivedGrouping').value,...($('receivedRule').value?{rule_id:$('receivedRule').value}:{})};
      action($('receivedOrganize'),body,'정리를 시작했어요. 실제 이동 결과는 목록에 표시돼요.');
    };
    $('receivedSaveRule').onclick=()=>action($('receivedSaveRule'),{action:'rule',id:editing?.id||ruleDraftId,title:$('receivedRuleTitle').value,source:$('receivedRuleSource').value,extensions:$('receivedExtensions').value.split(',').map(e=>e.trim()).filter(Boolean),contains:$('receivedContains').value,destination:$('receivedDestination').value,grouping:$('receivedGrouping').value,automatic:$('receivedAutomatic').checked},'정리 규칙을 기억했어요.');
    $('receivedSource').onchange=()=>{$('receivedSourceRoot').value=snapshot?.sources.find(s=>s.id===$('receivedSource').value)?.root||'';};
    $('receivedSourceForm').onsubmit=async event=>{event.preventDefault();try{await request({action:'source',id:$('receivedSource').value,root:$('receivedSourceRoot').value});note('수신 폴더를 연결했어요. 첫 목록을 준비하고 있어요.');await poll();}catch(error){note(error.message,true);}};
    $('receivedExisting').onchange=()=>{offset=0;signature='';poll();};let searchTimer;$('receivedSearch').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;poll();},300);};
    $('receivedPrevious').onclick=()=>{offset=Math.max(0,offset-60);poll();};$('receivedNext').onclick=()=>{offset+=60;poll();};
    window.workStageSelect('files');poll().then(()=>{$('receivedSource').onchange();});setInterval(()=>{if(!document.hidden&&!$('workReceivedFiles').hidden)poll();},6000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
