const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'workdesk.js'), 'utf8');
test('card description editor uses the same single-line 160 character limit as the server',()=>{
  const html=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.html'),'utf8');
  assert.match(html,/<input id="workLauncherDescription" type="text" maxlength="160"/);
});
async function surface({date, savePage, fullPage = false, imageIcon = false} = {}) {
  const elements = new Map(), ready = [], posts = [], controls = {};
  const state = {action_token:'fixture', page_size:15, pages:[{id:1,title:'1페이지'},{id:2,title:'2페이지'}], items:Array.from({length:fullPage ? 15 : 12},(_,i)=>({id:'item'+i,title:'앱'+i,kind:'app',target:'missing.exe',available:false,icon_image:imageIcon && i===0,page:(fullPage || i<9)?1:2}))};
  const element = id => {
    if (!elements.has(id)) {
      const handlers = new Map();
      elements.set(id, {id,value:'',dataset:{},disabled:false,hidden:false,open:false,innerHTML:'',textContent:'',classList:{toggle(){},add(){},remove(){}},
        addEventListener(name, fn){handlers.set(name,fn);}, fire(name,event){return handlers.get(name)?.(event);},
        contains(){return false;},querySelectorAll(){return [];},querySelector(){return {textContent:'',setAttribute(){}};},setAttribute(){},replaceChildren(){this.children=[];},append(node){(this.children ||= []).push(node);},focus(){},reset(){},
        showModal(){this.open=true;}, close(){this.open=false;}
      });
    }
    return elements.get(id);
  };
  const Clock=date ? class extends Date {constructor(...args){super(...(args.length ? args : [date]));}} : Date;
  const context={FileReader:class {readAsDataURL(file){this.result=file.data;this.onload?.();}},document:{createElement(tag){return {tag};},getElementById:element,addEventListener(name,fn){if(name==='DOMContentLoaded')ready.push(fn);},querySelectorAll(){return [];}},window:{},Date:Clock,Intl,Map,Uint8Array,URL,
    localStorage:{getItem(){return null;},setItem(){}},crypto:{getRandomValues(bytes){bytes.fill(2);return bytes;}},
    fetch:async(url,options)=>{let result; if(options?.method==='POST'){const body=JSON.parse(options.body);posts.push({url,body});if(controls.hold) await new Promise(resolve=>controls.release=resolve);if(controls.fail){controls.fail=false;throw Error('network');}if(controls.responseError){const error=controls.responseError;controls.responseError=null;return {ok:false,status:400,json:async()=>({error})};}if(url.endsWith('move-page')){state.items=state.items.map(item=>item.id===body.id?{...item,page:body.page}:item);}if(url.endsWith('reorder') || url.endsWith('board-order')){state.board_order=body.ids.slice();}if(url.endsWith('remove')){state.items=state.items.filter(item=>item.id!==body.id);}if(url.endsWith('add-page')){state.pages.push({id:3,title:'3페이지'});result={page:3};}else result={page:savePage||body.page||1};}return {ok:true,status:200,json:async()=>({...state,result})};}};
  vm.runInNewContext(source,context); ready[1](); ready[2](); await new Promise(setImmediate);
  const click=async(action,extra={})=>{const button={dataset:{launcherAction:action,...extra},disabled:false};await element('work').fire('click',{target:{closest:()=>button}});};
  return {element,click,posts,state,controls};
}
function edit(s,id='item0') {
  s.element('workLauncherGrid').fire('contextmenu',{target:{closest:()=>({dataset:{launcherId:id},disabled:false})},preventDefault(){}});
}
function drag(s,source='item0',destination='item2',after=true,{external=false,files=[],sameData=true}={}) {
  const data=new Map(), transfer={files,effectAllowed:'',clearData(){data.clear();},setData(k,v){data.set(k,v);},getData(k){return sameData ? data.get(k) || '' : 'https://external.example';}};
  const tile=id=>({dataset:{launcherId:id},disabled:false,classList:{add(){},remove(){}},getBoundingClientRect(){return {left:0,width:100};}});
  const event=(id,x)=>({target:{closest:()=>tile(id)},clientX:x,dataTransfer:transfer,preventDefault(){this.prevented=true;}});
  const start=event(source,0);if(!external)s.element('workLauncherGrid').fire('dragstart',start);
  return {start,transfer,over:()=>s.element('workLauncherGrid').fire('dragover',event(destination,after?80:20)),drop:()=>s.element('workLauncherGrid').fire('drop',event(destination,after?80:20)),end:()=>s.element('workLauncherGrid').fire('dragend',{})};
}
test('launcher merges every legacy page into one board',async()=>{
  const s=await surface();assert.equal((s.element('workLauncherGrid').innerHTML.match(/data-launcher-action="launch"/g)||[]).length,12);
  assert.match(s.element('workLauncherGrid').innerHTML,/앱0</);assert.match(s.element('workLauncherGrid').innerHTML,/앱11</);
  assert.equal((s.element('workLauncherGrid').innerHTML.match(/work-launcher-add-tile/g)||[]).length,1);
  assert.match(s.element('workLauncherCount').textContent,/12 CARDS/);assert.equal(s.posts.length,0);
});

test('right click opens full fields and appearance-only edits preserve broken target',async()=>{
  const s=await surface(); let prevented=false;
  const tile={dataset:{launcherId:'item0'},disabled:false};
  s.element('workLauncherGrid').fire('contextmenu',{target:{closest:()=>tile},preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(s.element('workLauncherDialog').open,true);assert.equal(s.posts.length,0);
  assert.equal(s.element('workLauncherTarget').required,true);assert.equal(s.element('workLauncherTargetWrap').hidden,false);assert.equal(s.element('workLauncherSuggestionsWrap').hidden,false);assert.equal(s.element('workLauncherKindWrap').hidden,false);s.element('workLauncherName').value='새 이름';
  await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
  assert.equal(s.posts[0].url,'/api/launcher/appearance');assert.equal(s.posts[0].body.title,'새 이름');assert.equal('target' in s.posts[0].body,false);
});
test('calendar renders local current month and year-boundary navigation',async()=>{
  const s=await surface(), today=new Date();assert.equal(s.element('workCalendarMonth').textContent,`${today.getFullYear()}년 ${today.getMonth()+1}월`);
  assert.match(s.element('workCalendarGrid').innerHTML,/aria-current="date"/);
  for(let i=0;i<today.getMonth()+1;i++) await s.element('workCalendar').fire('click',{target:{closest:()=>({dataset:{calendarAction:'prev'}})}});
  assert.equal(s.element('workCalendarMonth').textContent,`${today.getFullYear()-1}년 12월`);
});
test('new shortcut save is independent of hidden legacy pages',async()=>{
  const s=await surface();await s.click('add');s.element('workLauncherName').value='테스트';s.element('workLauncherKind').value='web';s.element('workLauncherTarget').value='https://example.com';
  await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
  assert.equal(s.posts[0].url,'/api/launcher/save');assert.equal('page' in s.posts[0].body,false);assert.equal(s.posts[0].body.card_description,'');
});

test('ordinary refresh retains every legacy-page item',async()=>{
  const s=await surface();await s.click('reload');assert.equal((s.element('workLauncherGrid').innerHTML.match(/data-launcher-action="launch"/g)||[]).length,12);
});

test('Shift F10 opens full editor without an executable request',async()=>{
  const s=await surface();let prevented=false;
  s.element('workLauncherGrid').fire('keydown',{key:'F10',shiftKey:true,target:{closest:()=>({dataset:{launcherId:'item0'},disabled:false})},preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(s.element('workLauncherDialog').open,true);assert.equal(s.posts.length,0);
});
test('calendar handles leap February and normal February using local dates',async()=>{
  for(const [date,days] of [[new Date(2024,1,15),29],[new Date(2025,1,15),28]]){
    const s=await surface({date});assert.equal((s.element('workCalendarGrid').innerHTML.match(/class="work-calendar-day/g)||[]).length,days);
  }
});
test('calendar controls remain enabled and work without launcher network',async()=>{
  const s=await surface({date:new Date(2024,11,31)});
  await s.element('workCalendar').fire('click',{target:{closest:()=>({dataset:{calendarAction:'next'}})}});
  assert.equal(s.element('workCalendarMonth').textContent,'2025년 1월');
  await s.element('workCalendar').fire('click',{target:{closest:()=>({dataset:{calendarAction:'today'}})}});
  assert.equal(s.element('workCalendarMonth').textContent,'2024년 12월');assert.equal(s.posts.length,0);
});


test('full legacy page still permits adding a board card',async()=>{
 const s=await surface({fullPage:true}), html=s.element('workLauncherGrid').innerHTML;
 assert.equal((html.match(/data-launcher-action="launch"/g)||[]).length,15);assert.match(html,/work-launcher-add-tile/);assert.doesNotMatch(html,/work-launcher-slot/);
});

test('launcher omits type captions and uses image badges while retaining unavailable warnings',async()=>{
  const s=await surface({imageIcon:true}), html=s.element('workLauncherGrid').innerHTML;
  assert.doesNotMatch(html,/work-launcher-kind|>프로그램<|>웹사이트<|>폴더</);
  assert.match(html,/class="work-launcher-icon is-image" aria-hidden="true"><img src="\/api\/launcher\/icon\?id=item0"/);
  assert.match(html,/aria-label="앱0 열기 · 경로 확인 필요"/);
  assert.equal((html.match(/work-launcher-icon is-image/g)||[]).length,1);
});
test('board retains all items plus one real add card',async()=>{
 const s=await surface(),html=s.element('workLauncherGrid').innerHTML;assert.equal((html.match(/work-launcher-add-tile/g)||[]).length,1);assert.equal((html.match(/data-launcher-id=/g)||[]).length,12);
});

test('changing target or kind uses validated full save while title-only uses appearance',async()=>{
  const s=await surface();edit(s);s.element('workLauncherKind').value='web';s.element('workLauncherTarget').value='https://changed.example';
  await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
  assert.equal(s.posts[0].url,'/api/launcher/save');assert.equal(s.posts[0].body.target,'https://changed.example');assert.equal(s.posts[0].body.id,'item0');
});
test('full editor removes original shortcut identity despite unsaved title changes',async()=>{
  const s=await surface();edit(s);s.element('workLauncherName').value='UNSAVED';
  await s.click('remove',{launcherId:s.element('workLauncherRemove').dataset.launcherId});
  assert.equal(s.posts[0].url,'/api/launcher/remove');assert.equal(s.posts[0].body.id,'item0');assert.equal(s.element('workLauncherDialog').open,false);
});
test('full editor provides keyboard reorder on original identity without saving drafts',async()=>{
  const s=await surface();edit(s,'item1');s.element('workLauncherName').value='UNSAVED';
  await s.click('up',{launcherId:s.element('workLauncherMoveUp').dataset.launcherId});
  assert.equal(s.posts[0].url,'/api/launcher/board-order');assert.deepEqual(s.posts[0].body.ids.slice(0,3),['item1','item0','item2']);assert.equal(s.element('workLauncherDialog').open,false);
});
test('drag before and after keeps every global card id once',async()=>{
  for(const after of [false,true]){
    const s=await surface(),before=s.state.items.map(x=>[x.id,x.page]);const d=drag(s,'item0','item2',after);d.over();await d.drop();
    assert.equal(s.posts[0].url,'/api/launcher/board-order');const ids=s.posts[0].body.ids;
    assert.deepEqual(ids.slice(0,4),after?['item1','item2','item0','item3']:['item1','item0','item2','item3']);
    assert.deepEqual(ids.slice(9),before.slice(9).map(x=>x[0]));assert.equal(new Set(ids).size,12);
    assert.deepEqual(s.state.items.map(x=>[x.id,x.page]).sort(),before.sort());
  }
});
test('external payload, files, mismatched data and same item cannot reorder',async()=>{
  for(const scenario of [{external:true},{files:[{}]},{sameData:false},{destination:'item0'}]){
    const s=await surface(),d=drag(s,'item0',scenario.destination||'item2',true,scenario);await d.drop();assert.equal(s.posts.length,0);
  }
});
test('drag rejects open editor and active requests, and suppresses launch after drag',async()=>{
  const s=await surface();edit(s);let d=drag(s);assert.equal(d.start.prevented,true);await d.drop();assert.equal(s.posts.length,0);
  await s.click('close');d=drag(s);await d.drop();await s.click('launch',{launcherId:'item0'});assert.equal(s.posts.length,1);
  const other=await surface();other.controls.hold=true;const request=other.click('launch',{launcherId:'item0'});d=drag(other);assert.equal(d.start.prevented,true);await d.drop();assert.equal(other.posts.length,1);other.controls.release();await request;
});
test('failed reorder retains old UI and rejects further drag until same-id retry',async()=>{
  const s=await surface(),before=s.element('workLauncherGrid').innerHTML;s.controls.fail=true;
  await drag(s).drop();assert.equal(s.element('workLauncherGrid').innerHTML,before);const request=s.posts[0].body.request_id;
  const d=drag(s);assert.equal(d.start.prevented,true);await d.drop();assert.equal(s.posts.length,1);
  await s.click('retry');assert.equal(s.posts[1].body.request_id,request);assert.equal(s.posts[1].url,'/api/launcher/board-order');
});
test('markup exposes a unified editor without an edit mode and labels assistant Aemeath',()=>{
  const html=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.html'),'utf8');
  assert.doesNotMatch(html,/data-launcher-action="manage"/);assert.match(html,/id="workAssistantTitle">Aemeath/);assert.match(html,/id="workLauncherItemActions"/);
});

test('drag crosses previous page boundaries in global board order',async()=>{
 const s=await surface();await drag(s,'item0','item10',true).drop();const ids=s.posts[0].body.ids;
 assert.deepEqual(ids.slice(8),['item9','item10','item0','item11']);assert.equal(new Set(ids).size,12);assert.equal(s.posts[0].url,'/api/launcher/board-order');
});

test('cancelled native drag suppresses launch and cannot later accept an external drop',async()=>{
  const s=await surface(),d=drag(s);d.end();await s.click('launch',{launcherId:'item0'});await d.drop();assert.equal(s.posts.length,0);
});

test('GIF upload keeps raw image data in preview and appearance payload',async()=>{
  const s=await surface();edit(s);const data='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  s.element('workLauncherImage').files=[{name:'animated.GIF',type:'image/gif',size:42,data}];
  s.element('workLauncherImage').fire('change');
  assert.equal(s.element('workLauncherIconPreview').children[0].src,data);
  await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
  assert.equal(s.posts[0].body.icon_data,data);assert.equal(s.posts[0].url,'/api/launcher/appearance');
});
test('GIF upload keeps the same 512KB boundary and rejects mismatched MIME',async()=>{
  for(const file of [{name:'large.gif',type:'image/gif',size:512*1024+1},{name:'script.gif',type:'application/javascript',size:10}]){
    const s=await surface();edit(s);s.element('workLauncherImage').files=[file];s.element('workLauncherImage').fire('change');
    assert.match(s.element('workLauncherFormNotice').textContent,/512KB.*GIF/);assert.equal(s.posts.length,0);
  }
  const html=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.html'),'utf8');assert.match(html,/accept="[^"]*image\/gif/);
});
test('card board has four columns and internal vertical scrolling',()=>{
 const css=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.css'),'utf8');
 assert.match(css,/work-launcher-grid\{[^}]*repeat\(4/);assert.match(css,/work-launcher-board\{[^}]*overflow-y:auto;overflow-x:hidden/);assert.doesNotMatch(source,/class="work-launcher-slot"/);
});

test('icon blob revisions refresh tile and editor URLs without a page reload',async()=>{
  const s=await surface({imageIcon:true});s.state.items[0].icon_revision='revision-one';await s.click('reload');
  assert.match(s.element('workLauncherGrid').innerHTML,/icon\?id=item0&amp;v=revision-one|icon\?id=item0&v=revision-one/);
  edit(s);assert.equal(s.element('workLauncherIconPreview').children[0].src,'/api/launcher/icon?id=item0&v=revision-one');await s.click('close');
  s.state.items[0].icon_revision='revision-two';await s.click('reload');
  assert.match(s.element('workLauncherGrid').innerHTML,/icon\?id=item0&v=revision-two/);assert.doesNotMatch(s.element('workLauncherGrid').innerHTML,/revision-one/);
  s.state.items[0].title='새 이름';await s.click('reload');assert.match(s.element('workLauncherGrid').innerHTML,/icon\?id=item0&v=revision-two/);
});

test('board renders server order with unknown items appended once',async()=>{
 const s=await surface();s.state.board_order=['item9','item0','unknown','item9'];await s.click('reload');const html=s.element('workLauncherGrid').innerHTML;
 assert.ok(html.indexOf('data-launcher-id="item9"')<html.indexOf('data-launcher-id="item0"'));assert.equal((html.match(/data-launcher-id="item9"/g)||[]).length,1);assert.equal((html.match(/data-launcher-action="launch"/g)||[]).length,12);
});

test('markup removes page navigation and page-move editor controls',()=>{
 const html=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.html'),'utf8');assert.doesNotMatch(html,/workLauncherPages|workLauncherMovePage|data-launcher-action="add-page"/);assert.match(html,/id="workLauncherBoard"[^>]*tabindex="0"/);
});

test('cover upload and description join one atomic appearance request',async()=>{
 const s=await surface();edit(s);const data='data:image/gif;base64,R0lGODlh';s.element('workLauncherCover').files=[{name:'cover.gif',type:'image/gif',size:600000,data}];s.element('workLauncherCover').fire('change');s.element('workLauncherDescription').value='작업 메모';
 assert.equal(s.element('workLauncherCoverPreview').children[0].src,data);await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
 assert.equal(s.posts.length,1);assert.equal(s.posts[0].body.card_cover_data,data);assert.equal(s.posts[0].body.card_description,'작업 메모');assert.equal(s.posts[0].url,'/api/launcher/appearance');assert.equal('icon_data' in s.posts[0].body,false);
});

test('cover clearing sends null while untouched cover is omitted',async()=>{
 const s=await surface();edit(s);await s.click('clear-cover');await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});assert.equal(s.posts[0].body.card_cover_data,null);
 const other=await surface();edit(other);await other.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});assert.equal('card_cover_data' in other.posts[0].body,false);
});

test('cover metadata renders only actual uploaded covers and escaped descriptions',async()=>{
 const s=await surface();s.state.items[0].card_cover=true;s.state.items[0].card_cover_revision='cover-two';s.state.items[0].card_description='<img src=x onerror=alert(1)>';await s.click('reload');
 const html=s.element('workLauncherGrid').innerHTML;assert.match(html,/cover\?id=item0&v=cover-two/);assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.equal((html.match(/class="work-launcher-cover"/g)||[]).length,1);edit(s);assert.equal(s.element('workLauncherCoverPreview').children[0].src,'/api/launcher/cover?id=item0&v=cover-two');
});
test('cover upload has a separate 4MB boundary and rejects non-image MIME',async()=>{
  for(const file of [{name:'cover.gif',type:'image/gif',size:4*1024*1024+1},{name:'bad.png',type:'application/javascript',size:30},{name:'icon.ico',type:'image/x-icon',size:30}]){
    const s=await surface();edit(s);s.element('workLauncherCover').files=[file];s.element('workLauncherCover').fire('change');assert.match(s.element('workLauncherFormNotice').textContent,/4MB.*GIF/);assert.equal(s.posts.length,0);
  }
});
test('uncertain atomic cover save retries identical content and id with a refreshed token',async()=>{
  const s=await surface();edit(s);const data='data:image/gif;base64,R0lGODlh';s.element('workLauncherCover').files=[{name:'cover.gif',type:'image/gif',size:50,data}];s.element('workLauncherCover').fire('change');s.element('workLauncherDescription').value='보존할 설명';
  s.controls.fail=true;await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});assert.equal(s.element('workLauncherDialog').open,true);const first=s.posts[0].body;s.state.action_token='renewed';await s.click('retry');
  const second=s.posts[1].body;assert.equal(second.request_id,first.request_id);assert.equal(second.card_cover_data,first.card_cover_data);assert.equal(second.card_description,first.card_description);assert.equal(second.token,'renewed');assert.equal(s.element('workLauncherDialog').open,false);
});
test('wide full-monitor layout uses real inbox as third column without CSS zoom',()=>{
  const css=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.css'),'utf8');assert.match(css,/@media\(min-width:1780px\)/);assert.match(css,/\.shell>\.main\{max-width:none\}/);assert.match(css,/work-rail\{display:contents\}/);assert.match(css,/work-inbox\{grid-column:3;grid-row:1/);assert.doesNotMatch(css,/(?:^|[;{])\s*zoom\s*:/);
});
test('masonry measures actual cards and reflows on image load and size changes without dense flow',()=>{
  const css=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.css'),'utf8');assert.match(css,/grid-auto-rows:8px;gap:10px/);assert.doesNotMatch(css,/grid-auto-flow:\s*dense/);
  assert.match(source,/Math\.ceil\(\(height\+10\)\/18\)/);assert.match(source,/new ResizeObserver\(scheduleBoardLayout\)/);assert.match(source,/addEventListener\('load',scheduleBoardLayout,true\)/);assert.match(source,/addEventListener\('resize',scheduleBoardLayout\)/);
});
test('masonry row spans follow each actual tile height, including the add card',()=>{
  const cells=[135,284,135].map((height,index)=>({style:{},matches:()=>index===2,querySelector(){return {getBoundingClientRect:()=>({height})};},getBoundingClientRect:()=>({height})}));
  const start=source.indexOf('  function measureBoard()'),end=source.indexOf('  function scheduleBoardLayout()',start);
  vm.runInNewContext(source.slice(start,end)+';measureBoard();',{boardLayoutQueued:true,$:()=>({querySelectorAll:()=>cells})});
  assert.deepEqual(cells.map(cell=>cell.style.gridRowEnd),['span 9','span 17','span 9']);
});
