const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'workdesk.js'), 'utf8');
async function surface({date, savePage, fullPage = false, imageIcon = false} = {}) {
  const elements = new Map(), ready = [], posts = [], controls = {};
  const state = {action_token:'fixture', page_size:12, pages:[{id:1,title:'1페이지'},{id:2,title:'2페이지'}], items:Array.from({length:12},(_,i)=>({id:'item'+i,title:'앱'+i,kind:'app',target:'missing.exe',available:false,icon_image:imageIcon && i===0,page:(fullPage || i<9)?1:2}))};
  const element = id => {
    if (!elements.has(id)) {
      const handlers = new Map();
      elements.set(id, {id,value:'',dataset:{},disabled:false,hidden:false,open:false,innerHTML:'',textContent:'',classList:{toggle(){},add(){},remove(){}},
        addEventListener(name, fn){handlers.set(name,fn);}, fire(name,event){return handlers.get(name)?.(event);},
        contains(){return false;},querySelectorAll(){return [];},querySelector(){return {textContent:'',setAttribute(){}};},setAttribute(){},replaceChildren(){},focus(){},reset(){},
        showModal(){this.open=true;}, close(){this.open=false;}
      });
    }
    return elements.get(id);
  };
  const Clock=date ? class extends Date {constructor(...args){super(...(args.length ? args : [date]));}} : Date;
  const context={document:{getElementById:element,addEventListener(name,fn){if(name==='DOMContentLoaded')ready.push(fn);},querySelectorAll(){return [];}},window:{},Date:Clock,Intl,Map,Uint8Array,URL,
    localStorage:{getItem(){return null;},setItem(){}},crypto:{getRandomValues(bytes){bytes.fill(2);return bytes;}},
    fetch:async(url,options)=>{let result; if(options?.method==='POST'){const body=JSON.parse(options.body);posts.push({url,body});if(controls.hold) await new Promise(resolve=>controls.release=resolve);if(controls.fail){controls.fail=false;throw Error('network');}if(url.endsWith('reorder')){state.items=body.ids.map(id=>state.items.find(item=>item.id===id));}if(url.endsWith('remove')){state.items=state.items.filter(item=>item.id!==body.id);}if(url.endsWith('add-page')){state.pages.push({id:3,title:'3페이지'});result={page:3};}else result={page:savePage||body.page||1};}return {ok:true,status:200,json:async()=>({...state,result})};}};
  vm.runInNewContext(source,context); ready[1](); ready[2](); await new Promise(setImmediate);
  const click=async(action,extra={})=>{const button={dataset:{launcherAction:action,...extra},disabled:false};await element('work').fire('click',{target:{closest:()=>button}});};
  return {element,click,posts,state,controls};
}
test('launcher shows nine items and navigates persistent empty pages',async()=>{
  const s=await surface(); assert.equal((s.element('workLauncherGrid').innerHTML.match(/data-launcher-action="launch"/g)||[]).length,9);
  await s.click('page',{launcherPage:'2'}); assert.match(s.element('workLauncherGrid').innerHTML,/앱9/); assert.doesNotMatch(s.element('workLauncherGrid').innerHTML,/앱0</);
  await s.click('add-page'); assert.equal(s.posts[0].url,'/api/launcher/add-page'); assert.match(s.element('workLauncherPages').innerHTML,/aria-current="page"[^>]*>3페이지/);
  assert.equal((s.element('workLauncherGrid').innerHTML.match(/work-launcher-add-tile/g)||[]).length,1);
  assert.equal((s.element('workLauncherGrid').innerHTML.match(/class="work-launcher-slot" aria-hidden="true"/g)||[]).length,11);
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
test('new shortcut carries selected page and follows actual destination after save',async()=>{
  const s=await surface({savePage:1});await s.click('page',{launcherPage:'2'});await s.click('add');
  s.element('workLauncherName').value='테스트';s.element('workLauncherKind').value='web';s.element('workLauncherTarget').value='https://example.com';
  await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
  assert.equal(s.posts[0].url,'/api/launcher/save');assert.equal(s.posts[0].body.page,2);
  assert.match(s.element('workLauncherPages').innerHTML,/aria-current="page"[^>]*>1페이지/);
});
test('ordinary refresh preserves current page and all icons remain reachable',async()=>{
  const s=await surface();await s.click('page',{launcherPage:'2'});await s.click('reload');
  assert.match(s.element('workLauncherPages').innerHTML,/aria-current="page"[^>]*>2페이지/);
  assert.equal((s.element('workLauncherGrid').innerHTML.match(/data-launcher-action="launch"/g)||[]).length,3);
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


test('twelve-item page is fully reachable without an extra add tile', async()=>{
  const s=await surface({fullPage:true}); const html=s.element('workLauncherGrid').innerHTML;
  assert.equal((html.match(/data-launcher-action="launch"/g)||[]).length,12);
  assert.doesNotMatch(html,/work-launcher-add-tile|work-launcher-slot/);
});

test('launcher omits type captions and uses image badges while retaining unavailable warnings',async()=>{
  const s=await surface({imageIcon:true}), html=s.element('workLauncherGrid').innerHTML;
  assert.doesNotMatch(html,/work-launcher-kind|>프로그램<|>웹사이트<|>폴더</);
  assert.match(html,/class="work-launcher-icon is-image" aria-hidden="true"><img src="\/api\/launcher\/icon\?id=item0"/);
  assert.match(html,/aria-label="앱0 열기 · 경로 확인 필요"/);
  assert.equal((html.match(/work-launcher-icon is-image/g)||[]).length,1);
});
test('existing nine-item page retains items plus one real add tile and two decorative slots',async()=>{
  const s=await surface(), html=s.element('workLauncherGrid').innerHTML;
  assert.equal((html.match(/work-launcher-add-tile/g)||[]).length,1);
  assert.equal((html.match(/class="work-launcher-slot" aria-hidden="true"/g)||[]).length,2);
  await s.click('page',{launcherPage:'2'});await s.click('add');
  s.element('workLauncherName').value='새 도구';s.element('workLauncherKind').value='web';s.element('workLauncherTarget').value='https://example.com';
  await s.element('work').fire('submit',{target:{id:'workLauncherForm'},preventDefault(){}});
  assert.equal(s.posts[0].body.page,2);
});


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
  assert.equal(s.posts[0].url,'/api/launcher/reorder');assert.deepEqual(s.posts[0].body.ids.slice(0,3),['item1','item0','item2']);assert.equal(s.element('workLauncherDialog').open,false);
});
test('drag before and after reorders only current-page positions and keeps every id once',async()=>{
  for(const after of [false,true]){
    const s=await surface(),before=s.state.items.map(x=>[x.id,x.page]);const d=drag(s,'item0','item2',after);d.over();await d.drop();
    assert.equal(s.posts[0].url,'/api/launcher/reorder');const ids=s.posts[0].body.ids;
    assert.deepEqual(ids.slice(0,4),after?['item1','item2','item0','item3']:['item1','item0','item2','item3']);
    assert.deepEqual(ids.slice(9),before.slice(9).map(x=>x[0]));assert.equal(new Set(ids).size,12);
    assert.deepEqual(s.state.items.map(x=>[x.id,x.page]).sort(),before.sort());
  }
});
test('external payload, files, mismatched data, different page and same item cannot reorder',async()=>{
  for(const scenario of [{external:true},{files:[{}]},{sameData:false},{destination:'item10'},{destination:'item0'}]){
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
  await s.click('retry');assert.equal(s.posts[1].body.request_id,request);assert.equal(s.posts[1].url,'/api/launcher/reorder');
});
test('markup exposes a unified editor without an edit mode and labels assistant JARVIS',()=>{
  const html=fs.readFileSync(require('node:path').join(__dirname,'..','workdesk.html'),'utf8');
  assert.doesNotMatch(html,/data-launcher-action="manage"/);assert.match(html,/id="workAssistantTitle">JARVIS/);assert.match(html,/id="workLauncherItemActions"/);
});

test('reorder preserves interleaved other-page positions rather than flattening all pages',async()=>{
  const s=await surface(),all=s.state.items.slice();s.state.items.splice(0,s.state.items.length,all[0],all[9],all[1],all[10],all[2],...all.slice(3,9),all[11]);
  const before=s.state.items.map(x=>x.id);await drag(s,'item0','item2',true).drop();
  const ids=s.posts[0].body.ids;assert.equal(ids[1],before[1]);assert.equal(ids[3],before[3]);assert.equal(ids.at(-1),before.at(-1));assert.deepEqual(ids.slice(0,5),['item1','item9','item2','item10','item0']);
});
test('cancelled native drag suppresses launch and cannot later accept an external drop',async()=>{
  const s=await surface(),d=drag(s);d.end();await s.click('launch',{launcherId:'item0'});await d.drop();assert.equal(s.posts.length,0);
});
