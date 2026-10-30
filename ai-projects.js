/* Project catalog: local code inventory, explicit cloud identities, user notes. */
(() => {
  'use strict';
  const root=document.getElementById('projects');if(!root)return;
  const css=document.createElement('link');css.rel='stylesheet';css.href='/ai-projects.css';document.head.append(css);
  const states={unclassified:'미분류',active:'진행 중',paused:'보류',archived:'완료·보관'};
  let data=null,selected=null,query='',filter='all',scope='owned',copies=false,external=false,limit=80,dirty=false,loading=false,timer=null,token='',lastListSignature='';
  const n=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=String(text);return el;};
  const time=value=>value?new Date(value*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'아직 조사 전';
  const button=(text,fn,cls='secondary')=>{const b=n('button',cls,text);b.type='button';b.addEventListener('click',fn);return b;};
  const title=p=>p.user.title||(p.introduction?.title_ko?(p.subdir==='.'?p.introduction.title_ko:p.introduction.title_ko+' / '+p.name):p.name);
  const sourceDate=p=>Math.max(0,...(p.profile?.locals||[]).map(r=>r.source_modified_at||0));
  const day=value=>value?new Date(value*1000).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}):'미확인';
  root.innerHTML=`<header class="ap-heading"><div><h2>AI 개발 프로젝트</h2><p>흩어진 코드와 저장소, 배포를 한곳에서 이어가세요.</p></div><div class="ap-actions"><button id="apRescan" type="button">폴더·GitHub 다시 조사</button><button id="apStop" type="button" class="secondary" hidden>조사 중지</button></div></header><p id="apNotice" role="status" aria-live="polite" hidden></p><div id="apProgress" class="ap-progress" role="status"></div><div id="apCounts" class="ap-counts"></div><div class="ap-toolbar"><label class="ap-search"><span>프로젝트 찾기</span><input id="apSearch" type="search" placeholder="프로젝트 이름, 폴더, 저장소, 메모" autocomplete="off"></label><label><span>진행 상태</span><select id="apFilter"><option value="all">전체</option>${Object.entries(states).map(([v,t])=>`<option value="${v}">${t}</option>`).join('')}<option value="cloud">로컬 위치 미확인</option></select></label><label class="ap-check"><input id="apCopies" type="checkbox">사본·이름 확인 후보</label></div><div class="ap-layout"><nav class="ap-list" id="apList" aria-label="개발 프로젝트 목록"></nav><article class="ap-detail" id="apDetail"><p class="ap-empty">프로젝트 목록을 불러오고 있어요.</p></article></div><details class="ap-coverage"><summary>조사 범위와 연결 상태</summary><div id="apCoverage"></div></details>`;
  root.querySelector('.ap-heading h2').textContent='프로젝트 목록';
  const list=root.querySelector('#apList'),detail=root.querySelector('#apDetail'),notice=root.querySelector('#apNotice');
  const scopeLabel=n('label'),scopeSelect=n('select');scopeLabel.append(n('span','','목록 범위'),scopeSelect);scopeSelect.id='apScope';
  for(const[value,text]of [['owned','내 GitHub 프로젝트'],['local','로컬만 확인된 후보'],['cloud','로컬 위치 미확인'],['modules','내 프로젝트 하위 모듈'],['all','전체 후보']]){const option=n('option','',text);option.value=value;scopeSelect.append(option);}root.querySelector('.ap-toolbar').insertBefore(scopeLabel,root.querySelector('.ap-toolbar').children[1]);
  scopeSelect.addEventListener('change',()=>{scope=scopeSelect.value;limit=80;renderList();});
  const externalLabel=n('label','ap-check'),externalInput=n('input');externalInput.type='checkbox';externalLabel.append(externalInput,document.createTextNode('외부 저장소 소스도 보기'));root.querySelector('.ap-toolbar').append(externalLabel);
  externalInput.addEventListener('change',()=>{external=externalInput.checked;renderList();});
  const rescan=root.querySelector('#apRescan'),stop=root.querySelector('#apStop');
  function message(text,error=false){notice.textContent=text;notice.hidden=!text;notice.classList.toggle('ap-error',error);}
  async function post(action,payload={}){
    if(!token){const r=await fetch('/api/work');if(!r.ok)throw Error('오피스 연결을 확인해 주세요.');token=(await r.json()).action_token;}
    const r=await fetch('/api/work/project-catalog-'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,token})});
    const value=await r.json();if(!r.ok)throw Error(value.error||'처리하지 못했어요.');return value;
  }
  function choose(id){if(id===selected)return;if(dirty&&!confirm('아직 저장하지 않은 메모가 있어요. 다른 프로젝트로 이동할까요?'))return;selected=id;dirty=false;renderList();renderDetail();}
  function matching(){return(data?.projects||[]).filter(p=>{
    if(scope==='owned'&&(!p.github||p.subdir!=='.'))return false;
    if(scope==='local'&&(p.github||!p.locals.length))return false;
    if(scope==='cloud'&&p.locals.length)return false;
    if(scope==='modules'&&(!p.github||p.subdir==='.'))return false;
    if(!external&&p.external&&!p.user.primary_path&&p.status==='unclassified')return false;
    if(filter==='cloud'&&p.locals.length)return false;
    if(filter!=='all'&&filter!=='cloud'&&p.status!==filter)return false;
    if(copies&&p.locals.length<2&&!p.similar_names.length)return false;
    return !query||[title(p),p.name,p.description,p.introduction?.summary,p.github_key,p.user.purpose,p.user.next_step,...p.locals.map(r=>r.path)].join(' ').toLowerCase().includes(query);
  });}
  function renderList(){
    list.replaceChildren();const rows=matching();
    const count=n('p','ap-list-count',`${rows.length.toLocaleString()}개 프로젝트 · 최근 확인 기록순`);list.append(count);
    for(const p of rows.slice(0,limit)){
      const b=button('',()=>choose(p.id),'ap-row'+(p.id===selected?' ap-selected':''));b.setAttribute('aria-current',String(p.id===selected));
      const head=n('span','ap-row-head');head.append(n('strong','',title(p)),n('span','ap-badge ap-'+p.status,states[p.status]));
      b.append(head,n('span','ap-row-desc',p.user.purpose||p.introduction?.summary||p.description||p.github_key||'설명을 적어두고 작업을 이어가세요.'));
      if(p.recommendation)b.append(n('span','ap-recommend-tag ap-rec-'+p.recommendation.level,p.recommendation.label));
      if(p.profile)b.append(n('span','ap-row-date',`소스 ${day(sourceDate(p))} · GitHub 커밋 ${day(p.profile.remote_commit?.at)}`));
      const tags=n('span','ap-row-tags');tags.textContent=[p.locals.length?`폴더 ${p.locals.length}`:'로컬 위치 미확인',p.github_key?'GitHub':'',p.vercel.length?'Vercel':'',p.locals.length>1?'사본 확인':''].filter(Boolean).join(' · ');b.append(tags);list.append(b);
    }
    if(rows.length>limit)list.append(button('더 보기',()=>{limit+=80;renderList();},'ap-more secondary'));
    if(!rows.length)list.append(n('p','ap-empty','검색 조건에 맞는 프로젝트가 없어요.'));
  }
  function link(text,url){try{const u=new URL(url);if(u.protocol!=='https:'||u.username||u.password)return null;const a=n('a','ap-link',text);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';return a;}catch{return null;}}
  function field(label,control){const wrap=n('label','ap-field');wrap.append(n('span','',label),control);return wrap;}
  function renderDetail(){
    const p=data?.projects.find(p=>p.id===selected);detail.replaceChildren();
    if(!p){detail.append(n('p','ap-empty','목록에서 프로젝트를 골라 주세요.'));return;}
    const heading=n('div','ap-detail-heading');heading.append(n('h3','',title(p)),n('span','ap-badge ap-'+p.status,states[p.status]));detail.append(heading);
    const source=n('p','ap-muted',p.github_key||'확인된 GitHub 연결 없음');if(p.subdir!=='.')source.append(document.createTextNode(' / '+p.subdir));detail.append(source);
    const quick=n('div','ap-actions ap-quick');
    const main=p.locals.find(r=>r.path===p.user.primary_path)||(p.locals.length===1?p.locals[0]:null);
    if(main)quick.append(button('작업 폴더 열기',async()=>{try{await post('open',{id:p.id,path:main.path});message('폴더를 열었어요.');}catch(e){message(e.message,true);}},''));
    if(p.github_key){const a=link('GitHub','https://github.com/'+p.github_key);if(a)quick.append(a);}
    if(p.vercel[0]?.domains?.[0]){const a=link('배포 화면','https://'+p.vercel[0].domains[0]);if(a)quick.append(a);}detail.append(quick);
    const brief=n('div','ap-brief');brief.append(n('p','',p.user.purpose||p.introduction?.summary||p.description||'프로젝트 설명을 남겨두세요.'),n('p','ap-muted','다음 할 일: '+(p.user.next_step||'아직 기록하지 않았어요.')));detail.append(brief);
    if(p.introduction?.stage)detail.append(n('p','ap-stage',p.introduction.stage));
    if(p.introduction?.stale)detail.append(n('p','ap-hint','소개를 작성한 뒤 코드·커밋 자료가 달라졌어요. 소개 내용은 다시 확인할 수 있어요.'));
    if(p.profile){const dates=n('dl','ap-date-grid');const deployment=Math.max(0,...p.vercel.map(v=>(v.deployment?.createdAt||0)/1000));
      for(const[label,value]of [['소스 파일 수정',sourceDate(p)],['GitHub 최근 커밋',p.profile.remote_commit?.at],['연결 배포',deployment]]){const box=n('div');box.append(n('dt','',label),n('dd','',day(value)));dates.append(box);}detail.append(dates,n('p','ap-muted','파일 수정일·커밋일·배포일은 서로 다른 기록이며 실제 마지막 사용일을 뜻하지 않아요.'));
    }
    if(p.recommendation){const rec=n('section','ap-recommend ap-rec-'+p.recommendation.level);rec.append(n('h4','',p.recommendation.label));
      const reasons=n('ul');for(const text of p.recommendation.reasons)reasons.append(n('li','',text));rec.append(reasons);if(p.introduction?.cleanup_hint)rec.append(n('p','ap-cleanup-hint',p.introduction.cleanup_hint));detail.append(rec);
    }
    if(p.preview?.status==='captured'){const figure=n('figure','ap-preview'),img=n('img');img.src='/api/work/project-catalog-preview?ref='+encodeURIComponent(p.preview.ref);img.alt=title(p)+' 실제 확인 화면';img.loading='lazy';figure.append(img,n('figcaption','ap-muted',`실제 화면 확인 ${time(p.preview.captured_at)} · ${p.preview.page_title||''}`));detail.append(figure);}
    else if(p.preview)detail.append(n('p','ap-muted','화면 미리보기: '+(p.preview.reason||'직접 확인하지 못했어요.')));
    if(p.introduction?.sources?.length){const evidence=n('details','ap-evidence');evidence.append(n('summary','','소개 근거 보기'));evidence.append(n('p','ap-muted',p.introduction.sources.join(' · ')),n('p','ap-muted','자료를 읽어 정리한 소개 초안이에요. 실제 기능의 완성도는 실행 확인과 구별해요.'));detail.append(evidence);}
    if(p.introduction?.related_repos?.length){const related=n('div','ap-related');related.append(n('p','ap-muted','비슷한 목적·관련 프로젝트'));
      for(const key of p.introduction.related_repos){const other=data.projects.find(q=>q.github_key==='papa6229-beep/'+key&&q.subdir==='.');if(other)related.append(button(title(other),()=>choose(other.id)));}detail.append(related);
    }
    if(p.locals.length>1)detail.append(n('p','ap-hint',`같은 저장소 연결의 폴더가 ${p.locals.length}곳이에요. 기준 폴더를 직접 고를 수 있어요. 파일 내용이 완전히 같다는 뜻은 아니에요.`));
    if(p.similar_names.length)detail.append(n('p','ap-hint','같은 이름의 다른 항목도 있어요. 연결 근거가 달라 자동으로 합치지 않았어요.'));
    const form=n('form','ap-form');
    const name=n('input');name.value=p.user.title||'';name.placeholder=p.introduction?.title_ko||p.name;name.maxLength=160;
    const status=n('select');for(const[v,t]of Object.entries(states)){const o=n('option','',t);o.value=v;status.append(o);}status.value=p.status;
    const purpose=n('textarea');purpose.rows=2;purpose.maxLength=2000;purpose.placeholder='무엇을 만드는 프로젝트인가요?';purpose.value=p.user.purpose||p.introduction?.summary||p.description||'';
    const next=n('textarea');next.rows=2;next.maxLength=2000;next.placeholder='다음에 이어서 할 일을 남겨두세요.';next.value=p.user.next_step||'';
    const primary=n('select');const none=n('option','','아직 정하지 않음');none.value='';primary.append(none);for(const r of p.locals){const o=n('option','',r.path);o.value=r.path;primary.append(o);}primary.value=p.user.primary_path||'';
    const row=n('div','ap-form-row');row.append(field('표시 이름',name),field('진행 상태',status));form.append(row,field('프로젝트 설명',purpose),field('다음 할 일',next),field('기준 폴더',primary));
    const save=n('button','','메모·상태 저장');save.type='submit';form.append(save);form.addEventListener('input',()=>{dirty=true;});form.addEventListener('change',()=>{dirty=true;});
    form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;try{await post('save',{id:p.id,title:name.value,status:status.value,purpose:purpose.value,next_step:next.value,primary_path:primary.value});dirty=false;message('프로젝트 메모와 상태를 저장했어요.');await load(true);}catch(e){message(e.message,true);}finally{save.disabled=false;}});
    const editor=n('details','ap-editor');editor.append(n('summary','','상태·메모 편집'),form);detail.append(editor);
    const folders=n('section','ap-folders');folders.append(n('h4','',`내 PC 폴더 ${p.locals.length}곳`));
    if(!p.locals.length)folders.append(n('p','ap-muted','이번 조사에서 연결된 로컬 폴더를 찾지 못했어요. 클라우드 원본은 아래에서 열 수 있어요.'));
    for(const r of p.locals){const item=n('div','ap-folder'),path=n('p','ap-path',r.path),actions=n('div','ap-actions');
      actions.append(button('폴더 열기',async()=>{try{await post('open',{id:p.id,path:r.path});message('폴더를 열었어요.');}catch(e){message(e.message,true);}}),button('경로 복사',async()=>{try{await navigator.clipboard.writeText(r.path);message('경로를 복사했어요.');}catch{message('경로를 선택해서 복사해 주세요.',true);}}));
      const observed=p.profile?.locals?.find(v=>v.path===r.path);
      item.append(path,n('p','ap-muted',[r.branch?'브랜치 '+r.branch:'',r.frameworks.join(', '),observed?'소스 수정 '+time(observed.source_modified_at):'프로젝트 표식 수정 '+time(r.modified_at),'로컬 커밋 '+day(r.commit_at)].filter(Boolean).join(' · ')));
      if(observed)item.append(n('p','ap-local-changes',observed.changes.checked?`추적 변경 ${observed.changes.tracked} · 미추적 항목 ${observed.changes.untracked}${observed.personal_data_candidates.length?' · 설정·DB 등 별도 자료 후보 있음':''}`:'Git 변경 상태 미확인'));
      item.append(actions);folders.append(item);
    }detail.append(folders);
    const cloud=n('section','ap-cloud');cloud.append(n('h4','','저장소와 배포'));
    if(p.github_key){const a=link('GitHub 저장소 열기','https://github.com/'+p.github_key);if(a)cloud.append(a);if(p.github)cloud.append(n('p','ap-muted',`${p.github.private?'비공개':'공개'} 저장소${p.github.archived?' · GitHub 보관됨':''} · 기본 브랜치 ${p.github.default_branch}`));}
    for(const v of p.vercel){const box=n('div','ap-deployment');box.append(n('strong','',v.name),n('span','ap-badge',v.deployment?.readyState||'상태 미확인'));
      for(const domain of(v.domains||[]).slice(0,2)){const a=link(domain,'https://'+domain);if(a)box.append(a);}if(v.deployment?.url){const a=link('최근 배포 열기','https://'+v.deployment.url);if(a)box.append(a);}cloud.append(box);}
    if(!p.github_key&&!p.vercel.length)cloud.append(n('p','ap-muted','확인된 클라우드 연결이 없어요. 이름이 비슷하다는 이유로 연결하지 않아요.'));detail.append(cloud);
  }
  function renderMeta(){
    const c=data.counts;root.querySelector('#apCounts').textContent=`프로젝트·코드 후보 ${c.projects.toLocaleString()} · 실제 폴더 ${c.folders.toLocaleString()} · GitHub ${c.github.toLocaleString()} · Vercel ${c.vercel.toLocaleString()}`;
    const s=data.scan,running=s.status==='running';rescan.disabled=running;stop.hidden=!running;
    root.querySelector('#apProgress').textContent=running?`${s.phase} · 폴더 ${(s.visited||0).toLocaleString()}곳 확인 · 후보 ${s.candidates||0}곳`:`${s.phase||'조사 대기'} · 로컬 목록 ${time(data.collected_at)}`;
    const coverage=root.querySelector('#apCoverage');coverage.replaceChildren();const v=data.coverage;
    coverage.append(n('p','',`탐색한 폴더 ${(v.visited||0).toLocaleString()}곳 · 최대 ${v.max_depth||14}단계. 시스템·의존성·모델·캐시·연결 폴더는 제외해요.`));
    if(v.limit_reached||v.depth_skipped)coverage.append(n('p','ap-hint',`조사 한도나 깊이로 일부가 남아 있어요. 남은 탐색 위치 ${v.remaining_directories||0}곳, 깊이 제외 ${v.depth_skipped||0}곳.`));
    if(v.denied_samples?.length)coverage.append(n('p','ap-hint',`접근하지 못한 경로가 있어요. 기록된 경로 ${v.denied_samples.length}곳.`));
    const paths=n('ul');for(const path of v.roots||[])paths.append(n('li','ap-path',path));coverage.append(paths);
    coverage.append(n('p','',`GitHub 목록 확인 ${time(data.cloud.github.collected_at)} · Vercel 목록 확인 ${time(data.cloud.vercel.collected_at)}`),n('p','ap-muted','다시 조사는 로컬 폴더와 GitHub 목록을 갱신해요. Vercel 목록 갱신은 에이메스에게 요청해 주세요. 폴더 이동·삭제·프로젝트 실행은 하지 않아요.'));
    if(data.cloud.github.error)coverage.append(n('p','ap-hint',data.cloud.github.error));
  }
  async function load(force=false){if(loading)return;loading=true;try{const r=await fetch('/api/work/project-catalog');if(!r.ok)throw Error('프로젝트 목록을 불러오지 못했어요.');data=await r.json();renderMeta();if(!selected&&data.projects.length)selected=data.projects[0].id;const signature=JSON.stringify(data.projects.map(p=>[p.id,p.updated,p.status,p.user,p.introduction,p.recommendation?.label]));if(force||signature!==lastListSignature){lastListSignature=signature;renderList();}if(!dirty&&(force||!detail.querySelector('form')))renderDetail();}catch(e){message(e.message,true);}finally{loading=false;}}
  root.querySelector('#apSearch').addEventListener('input',e=>{query=e.target.value.trim().toLowerCase();limit=80;renderList();});root.querySelector('#apFilter').addEventListener('change',e=>{filter=e.target.value;limit=80;renderList();});root.querySelector('#apCopies').addEventListener('change',e=>{copies=e.target.checked;renderList();});
  rescan.addEventListener('click',async()=>{if(dirty){message('작성 중인 메모를 먼저 저장해 주세요.');return;}rescan.disabled=true;try{await post('scan');message('파일을 옮기지 않고 폴더와 저장소 연결을 조사해요.');await load();}catch(e){message(e.message,true);rescan.disabled=false;}});
  stop.addEventListener('click',async()=>{try{await post('stop');message('중지를 요청했어요. 이미 저장된 목록은 유지돼요.');}catch(e){message(e.message,true);}});
  const visible=()=>!root.classList.contains('hide');new MutationObserver(()=>{if(visible())load();}).observe(root,{attributes:true,attributeFilter:['class']});
  timer=setInterval(()=>{if(visible())load();},5000);if(visible())load();
})();
