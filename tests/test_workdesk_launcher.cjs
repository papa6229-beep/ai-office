const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '..', 'workdesk.js'), 'utf8');
async function surface({date, savePage} = {}) {
  const elements = new Map(), ready = [], posts = [];
  const state = {action_token:'fixture', pages:[{id:1,title:'1페이지'},{id:2,title:'2페이지'}], items:Array.from({length:12},(_,i)=>({id:'item'+i,title:'앱'+i,kind:'app',target:'missing.exe',available:false,page:i<9?1:2}))};
  const element = id => {
    if (!elements.has(id)) {
      const handlers = new Map();
      elements.set(id, {id,value:'',dataset:{},disabled:false,hidden:false,open:false,innerHTML:'',textContent:'',classList:{toggle(){}},
        addEventListener(name, fn){handlers.set(name,fn);}, fire(name,event){return handlers.get(name)?.(event);},
        querySelectorAll(){return [];},querySelector(){return {textContent:'',setAttribute(){}};},setAttribute(){},replaceChildren(){},focus(){},reset(){},
        showModal(){this.open=true;}, close(){this.open=false;}
      });
    }
    return elements.get(id);
  };
  const Clock=date ? class extends Date {constructor(...args){super(...(args.length ? args : [date]));}} : Date;
  const context={document:{getElementById:element,addEventListener(name,fn){if(name==='DOMContentLoaded')ready.push(fn);},querySelectorAll(){return [];}},window:{},Date:Clock,Intl,Map,Uint8Array,URL,
    localStorage:{getItem(){return null;},setItem(){}},crypto:{getRandomValues(bytes){bytes.fill(2);return bytes;}},
    fetch:async(url,options)=>{let result; if(options?.method==='POST'){const body=JSON.parse(options.body);posts.push({url,body});if(url.endsWith('add-page')){state.pages.push({id:3,title:'3페이지'});result={page:3};}else result={page:savePage||body.page||1};}return {ok:true,status:200,json:async()=>({...state,result})};}};
  vm.runInNewContext(source,context); ready[1](); ready[2](); await new Promise(setImmediate);
  const click=async(action,extra={})=>{const button={dataset:{launcherAction:action,...extra},disabled:false};await element('work').fire('click',{target:{closest:()=>button}});};
  return {element,click,posts,state};
}
test('launcher shows nine items and navigates persistent empty pages',async()=>{
  const s=await surface(); assert.equal((s.element('workLauncherGrid').innerHTML.match(/data-launcher-action="launch"/g)||[]).length,9);
  await s.click('page',{launcherPage:'2'}); assert.match(s.element('workLauncherGrid').innerHTML,/앱9/); assert.doesNotMatch(s.element('workLauncherGrid').innerHTML,/앱0</);
  await s.click('add-page'); assert.equal(s.posts[0].url,'/api/launcher/add-page'); assert.match(s.element('workLauncherPages').innerHTML,/aria-current="page"[^>]*>3페이지/);
});
test('right click edits appearance without launching or validating broken target',async()=>{
  const s=await surface(); let prevented=false;
  const tile={dataset:{launcherId:'item0'},disabled:false};
  s.element('workLauncherGrid').fire('contextmenu',{target:{closest:()=>tile},preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(s.element('workLauncherDialog').open,true);assert.equal(s.posts.length,0);
  assert.equal(s.element('workLauncherTarget').required,false);s.element('workLauncherName').value='새 이름';
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
test('Shift F10 opens compact editor without an executable request',async()=>{
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
