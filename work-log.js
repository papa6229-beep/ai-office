/* A daily ledger of actual receipts. Rendering and filters do not call AI. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id),make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  let data=null,busy=false,revision=0,offset=0,signature='',noteId=crypto.randomUUID().replaceAll('-','');
  const date=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  async function api(query='',body){
    let options={cache:'no-store',signal:AbortSignal.timeout(25000)};
    if(body){const s=await(await fetch('/api/work',{cache:'no-store'})).json();options={...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,token:s.action_token})};}
    const response=await fetch('/api/work/work-log'+query,options),result=await response.json();
    if(!response.ok||result.error)throw Error(result.error||'작업일지를 확인하지 못했어요.');return result;
  }
  function filters(){return {start_date:$('logFrom').value,end_date:$('logTo').value,person:$('logPerson').value.trim(),kind:$('logKind').value,source:$('logSource').value,search:$('logSearch').value.trim()};}
  function status(text,error=false){$('logStatus').textContent=text;$('logStatus').classList.toggle('is-error',error);}
  function eventCard(row){
    const card=make('article');card.className='log-entry';card.dataset.kind=row.kind;
    const time=make('time',row.time);time.dateTime=new Date(row.at*1000).toISOString();
    const content=make('div'),head=make('div'),tag=make('span',row.label),title=make('strong',row.title);head.className='log-entry-heading';tag.className='log-category';head.append(tag,title);content.append(head);
    const d=row.data||{},context=[row.person||(['received','sent'].includes(row.kind)?'상대방 미확인':''),({hanbiro:'한비로톡',nateon:'네이트온',downloads:'다운로드 폴더',local:'PC',codex:'Codex',launcher:'바로가기',user:'직접 기록',daily_report:'일일업무보고서',office:'에이메스'})[d.source]||'',d.application||'',d.project||''];
    if(['partial','cancelled'].includes(d.status))head.append(make('span',d.status==='partial'?'일부 완료':'중지'));
    if(context.some(Boolean))content.append(make('p',context.filter(Boolean).join(' · ')));
    if(d.text)content.append(make('p',d.text));
    if(d.from_path||d.current_path){
      const detail=make('details'),summary=make('summary','파일 위치·이동 경로');detail.append(summary);
      if(d.from_path)detail.append(make('p','이전 위치 · '+d.from_path));
      if(d.current_path)detail.append(make('p','기록된 위치 · '+d.current_path));content.append(detail);
      if(row.kind!=='folder'){
        const open=make('button','파일 위치 열기');open.type='button';open.className='log-location';
        open.onclick=async()=>{open.disabled=true;try{await api('',{action:'reveal',id:row.id});status('탐색기에 파일 위치 표시를 요청했어요.');}catch(e){status(e.message,true);}finally{open.disabled=false;}};content.append(open);
      }
    }
    if(d.time_precision==='execution')content.append(make('small','과거 실행 기록 기준 · 정확한 개별 처리 시각은 기록되지 않았어요.'));
    card.append(time,content);return card;
  }
  function render(){
    const root=$('logTimeline'),opened=new Set([...root.querySelectorAll('.log-batch[open]')].map(e=>e.dataset.batch));root.replaceChildren();
    $('logRangeTitle').textContent=data.start_date===data.end_date?data.start_date:data.start_date+' — '+data.end_date;
    const counts=data.counts||{};$('logSummary').textContent=`파일 수발신 ${(counts.received||0)+(counts.sent||0)} · 파일 정리 ${counts.move||0} · 업무 활동 ${(counts.report||0)+(counts.launch||0)+(counts.project||0)+(counts.note||0)+(counts.activity||0)}`;
    const days=new Map();for(const row of data.items){if(!days.has(row.day))days.set(row.day,[]);days.get(row.day).push(row);}
    for(const [day,rows] of days){
      const section=make('section'),heading=make('h3',day);section.className='log-day';section.append(heading);
      const groups=new Map();
      for(const row of rows){const key=['move','folder'].includes(row.kind)&&row.data.job_id?'job:'+row.data.job_id:row.id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
      for(const [key,batch] of groups){
        if(batch.length===1){section.append(eventCard(batch[0]));continue;}
        const detail=make('details'),summary=make('summary');detail.className='log-batch';
        detail.dataset.batch=key;detail.open=opened.has(key);
        summary.append(make('time',batch[0].time),make('strong',(batch.every(r=>r.kind==='folder')?'폴더 정리':'파일·폴더 정리')+' · '+batch.length+'개'),make('span','개별 기록 보기'));detail.append(summary);
        for(const row of batch)detail.append(eventCard(row));section.append(detail);
      }
      root.append(section);
    }
    if(!data.items.length){const empty=make('div');empty.className='log-empty';empty.append(make('strong','이 날짜에는 확인된 기록이 없어요.'),make('p','파일을 주고받거나 정리하면 여기에 남아요. 따로 한 업무는 직접 적어 주세요.'));root.append(empty);}
    $('logPage').textContent=data.total?`${offset+1}–${offset+data.items.length} / ${data.total}건`:'0건';$('logPrevious').disabled=offset===0;$('logNext').disabled=!data.has_more;
  }
  async function load(){
    if(busy)return;busy=true;const current=revision;
    try{const p=new URLSearchParams({...filters(),limit:200,offset});data=await api('?'+p);if(current===revision){const next=JSON.stringify([data.items,data.total,data.counts,offset]);if(next!==signature){signature=next;render();}status(data.scope);}}
    catch(e){status(e.message,true);}finally{busy=false;if(current!==revision)load();}
  }
  function changed(){offset=0;revision++;load();}
  function shift(days){const d=new Date($('logFrom').value+'T12:00:00+09:00');d.setUTCDate(d.getUTCDate()+days);const value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);$('logFrom').value=$('logTo').value=value;changed();}
  function init(){
    const home=$('home');if(!home)return;
    const style=make('link');style.rel='stylesheet';style.href='/work-log.css';document.head.append(style);
    const legacy=make('div');legacy.hidden=true;legacy.id='logLegacyHome';while(home.firstChild)legacy.append(home.firstChild);home.append(legacy);
    for(const nav of document.querySelectorAll('.side .nav')){
      const action=nav.getAttribute('onclick')||'';
      if(action.includes("show('home'"))nav.textContent='◷ 작업일지';
      if(['tasks','inventory','files','history'].some(key=>action.includes("show('"+key+"'")))nav.hidden=true;
    }
    for(const button of document.querySelectorAll('.side button[onclick]')){
      const action=button.getAttribute('onclick')||'';
      if(['tasks','inventory','files','history'].some(key=>action.includes("show('"+key+"'")))button.hidden=true;
    }
    const root=make('div');root.className='work-log';root.innerHTML=`
      <header class="log-header"><div><h2>우리의 작업일지</h2><p>함께 처리한 일을 날짜와 시간으로 차곡차곡.</p></div><button id="logNoteToggle" type="button">+ 직접 기록</button></header>
      <section class="log-top"><div class="log-date-heading"><button id="logDayBack" type="button" aria-label="전날">‹</button><strong id="logRangeTitle"></strong><button id="logDayForward" type="button" aria-label="다음날">›</button></div><p id="logSummary"></p><div class="log-presets"><button data-log-days="0">오늘</button><button data-log-days="1">어제</button><button data-log-days="6">최근 1주일</button><button data-log-days="29">최근 30일</button></div></section>
      <form id="logFilters" class="log-filters"><label>시작일<input id="logFrom" type="date" required></label><label>종료일<input id="logTo" type="date" required></label><label>누구와<input id="logPerson" placeholder="예: 혜연"></label><label>어떤 일<select id="logKind"><option value="">모든 기록</option><option value="received">받은 파일</option><option value="sent">보낸 파일</option><option value="move">파일 정리</option><option value="folder">폴더 정리</option><option value="report">보고서</option><option value="launch">프로그램·사이트</option><option value="project">프로젝트</option><option value="note">직접 기록</option></select></label><label class="log-search">찾는 내용<input id="logSearch" placeholder="파일명이나 작업 내용"></label><button type="submit">찾기</button><button id="logExport" type="button">문서로 받기</button></form>
      <form id="logNoteForm" class="log-note" hidden><label>이 날짜에 남길 업무<textarea id="logNoteText" rows="3" maxlength="5000" placeholder="예: 바나나스튜디오에서 행사 배너 수정 완료" required></textarea></label><button type="submit">일지에 추가</button><p>오늘이 아닌 날짜에 시간을 지정하지 않으면 ‘시간 미지정’으로 남겨요.</p></form>
      <p id="logStatus" role="status"></p><div id="logTimeline"></div><footer class="log-footer"><button id="logPrevious" type="button">이전</button><span id="logPage"></span><button id="logNext" type="button">다음</button></footer>`;home.append(root);
    const sourceLabel=make('label','어디서'),source=make('select');source.id='logSource';
    $('logKind').add(new Option('업무 활동','activity'));
    for(const [value,text] of [['','모든 출처'],['hanbiro','한비로톡'],['nateon','네이트온'],['codex','Codex'],['launcher','바로가기'],['daily_report','보고서'],['office','에이메스'],['user','직접 기록'],['downloads','다운로드 폴더'],['local','PC 파일 작업']])source.add(new Option(text,value));
    sourceLabel.append(source);$('logKind').closest('label').after(sourceLabel);
    $('logFrom').value=$('logTo').value=date();
    $('logFilters').onsubmit=e=>{e.preventDefault();changed();};
    for(const id of ['logFrom','logTo','logKind','logSource'])$(id).onchange=changed;
    $('logDayBack').onclick=()=>shift(-1);$('logDayForward').onclick=()=>shift(1);
    for(const button of root.querySelectorAll('[data-log-days]'))button.onclick=()=>{const n=Number(button.dataset.logDays);$('logTo').value=date();const d=new Date(date()+'T12:00:00+09:00');d.setUTCDate(d.getUTCDate()-n);$('logFrom').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);if(n===1)$('logTo').value=$('logFrom').value;changed();};
    $('logPrevious').onclick=()=>{offset=Math.max(0,offset-200);revision++;load();};$('logNext').onclick=()=>{offset+=200;revision++;load();};
    $('logNoteToggle').onclick=()=>{$('logNoteForm').hidden=!$('logNoteForm').hidden;if(!$('logNoteForm').hidden)$('logNoteText').focus();};
    $('logNoteText').oninput=()=>{noteId=crypto.randomUUID().replaceAll('-','');};
    $('logNoteForm').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await api('',{action:'note',text:$('logNoteText').value,day:$('logFrom').value,request_id:noteId});$('logNoteText').value='';noteId=crypto.randomUUID().replaceAll('-','');$('logNoteForm').hidden=true;changed();}catch(err){status(err.message,true);}finally{button.disabled=false;}};
    $('logExport').onclick=async()=>{const button=$('logExport');button.disabled=true;try{const result=await api('',{action:'export',filters:filters()});const link=make('a','작업일지 Word 문서 다운로드');link.href=result.url;link.download='작업일지.docx';$('logStatus').replaceChildren(link);}catch(e){status(e.message,true);}finally{button.disabled=false;}};
    const onView=()=>{if(document.body.dataset.activeView==='home'){$('pageTitle').textContent='작업일지';load();}};
    new MutationObserver(onView).observe(document.body,{attributes:true,attributeFilter:['data-active-view']});
    setInterval(()=>{if(!document.hidden&&document.body.dataset.activeView==='home')load();},15000);onView();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
