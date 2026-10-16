const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'..','workdesk.js'),'utf8'),tick=()=>new Promise(setImmediate);
async function surface(data={}){
  const nodes=new Map(),ready=[],posts=[],controls={},base={action_token:'fixture',tasks:[],messages:[],events:[],counts:{},...data};
  function element(id){
    if(!nodes.has(id)){
      const handlers=new Map();const tab=id.match(/^workInbox(Pending|Records|Resources)Tab$/)?.[1]?.toLowerCase();
      nodes.set(id,{id,value:'',disabled:false,open:false,hidden:false,textContent:'',innerHTML:'',dataset:tab?{workInboxTab:tab}:{},attrs:{},scrollHeight:0,scrollTop:0,clientHeight:0,
        classList:{toggle(){}},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){handlers.set(k,fn);},fire(k,e={}){return handlers.get(k)?.(e);},
        querySelectorAll(){return id==='work'?[element('workInboxPendingTab'),element('workInboxRecordsTab'),element('workInboxResourcesTab'),element('actionFixture')]:[];},
        matches(selector){return selector==='[data-work-inbox-tab]' && !!this.dataset.workInboxTab;},closest(){return null;},focus(){},showModal(){this.open=true;},close(){this.open=false;},reset(){}});
    }return nodes.get(id);
  }
  const context={document:{getElementById:element,addEventListener(k,fn){if(k==='DOMContentLoaded')ready.push(fn);},dispatchEvent(){}},window:{},Intl,Date,Map,Set,Uint8Array,CustomEvent:class{},crypto:{getRandomValues(b){b.fill(4);return b;}},fetch:async(url,options)=>{
    if(url.startsWith('/api/work/task-history'))return {ok:true,json:async()=>({task_id:new URL('http://fixture'+url).searchParams.get('id'),events:[]})};
    if(options?.method==='POST'){posts.push({url,body:JSON.parse(options.body)});const response=await new Promise(resolve=>controls.release=resolve);if(response.network)throw Error('network');return {ok:true,status:200,json:async()=>({...base,...response.data})};}
    return {ok:true,status:200,json:async()=>base};
  }};
  vm.runInNewContext(source,context);ready[0]();await tick();
  const click=dataset=>element('work').fire('click',{target:{closest:()=>({dataset,disabled:false})}});
  return {element,posts,controls,base,click,async load(){await context.window.workLoad();},async respond(data={}){controls.release(data);await tick();}};
}
const task={id:'task1',title:'디자인 업무',status:'todo',resources:[{id:'resource-safe',name:'시안',path:'https://evil.invalid/never-open-raw',exists:true},{id:'resource-missing',name:'옛 자료',path:'C:/missing.txt',exists:false}]};
const pending={tasks:[task],messages:[{id:'message1',role:'assistant',content:'선택'}],assistant_actions:[{id:'approval1',message_id:'message1',title:'<img src=x onerror=evil()>',description:'<script>bad</script>',options:[{id:'approve',label:'실행',description:'검토'},{id:'cancel',label:'취소'}]}],launcher_choices:[{id:'choice1',message_id:'message1',name:'사진 앱',options:[{title:'사진',target:'startapp:photos'}]}]};
test('inbox renders real pending cards once, escapes text and ignores orphaned or empty cards',async()=>{
  const s=await surface({...pending,assistant_actions:[...pending.assistant_actions,...pending.assistant_actions,{id:'orphan',message_id:'missing',options:[{id:'x'}]},{id:'empty',message_id:'message1',options:[]}]});
  const html=s.element('workInboxPending').innerHTML;assert.equal(s.element('workInboxPendingTab').textContent,'확인 필요 2');assert.equal((html.match(/class="work-inbox-item"/g)||[]).length,2);assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;img/);assert.match(html,/data-work-launcher-choice="choice1"/);assert.equal(s.posts.length,0);
});
test('pending action dispatches one existing request while tabs remain usable during busy and uncertainty',async()=>{
  const s=await surface(pending);s.click({workAssistantAction:'approval1',workAssistantOption:'approve'});s.click({workAssistantAction:'approval1',workAssistantOption:'approve'});
  assert.equal(s.posts.length,1);assert.equal(s.posts[0].url,'/api/work/assistant-action');assert.equal(s.posts[0].body.pending_id,'approval1');assert.equal(s.posts[0].body.option_id,'approve');
  assert.equal(s.element('actionFixture').disabled,true);assert.equal(s.element('workInboxResourcesTab').disabled,false);s.click({workInboxTab:'resources'});assert.equal(s.element('workInboxResources').hidden,false);
  await s.respond({network:true});assert.equal(s.element('actionFixture').disabled,true);assert.equal(s.element('workInboxRecordsTab').disabled,false);s.click({workInboxTab:'records'});assert.equal(s.element('workInboxRecords').hidden,false);
});
test('launcher selection uses the existing choice request with exact registered target',async()=>{
  const s=await surface(pending);s.click({workLauncherChoice:'choice1',workLauncherTarget:'startapp:photos'});assert.equal(s.posts[0].url,'/api/work/choose-launcher');assert.equal(s.posts[0].body.target,'startapp:photos');await s.respond();
});
test('work records sort newest first, use actual task popup and bound long history',async()=>{
  const events=Array.from({length:35},(_,i)=>({task_id:'task1',kind:'note',text:'기록 '+i,created:new Date(2026,9,1,0,i).toISOString()}));
  const s=await surface({tasks:[task],events});const html=s.element('workInboxRecords').innerHTML;
  assert.ok(html.indexOf('기록 34')<html.indexOf('기록 33'));assert.doesNotMatch(html,/기록 0</);assert.equal((html.match(/class="work-inbox-item"/g)||[]).length,30);assert.equal(s.element('workInboxRecordsTab').textContent,'업무 기록 35');
  s.click({workTask:'task1'});await tick();assert.equal(s.element('workTaskDialog').open,true);assert.match(s.element('workTaskDetailPanel').innerHTML,/디자인 업무/);
});
test('linked resources use real server IDs, omit raw paths and give missing files no open action',async()=>{
  const s=await surface({tasks:[task]});const html=s.element('workInboxResources').innerHTML;
  assert.equal(s.element('workInboxResourcesTab').textContent,'연결 자료 2');assert.match(html,/data-work-resource="resource-safe"/);assert.doesNotMatch(html,/data-work-resource="resource-missing"|https:\/\/evil|C:\/missing/);assert.match(html,/경로 확인 필요/);
  s.click({workResource:'resource-safe'});assert.equal(s.posts[0].url,'/api/work/open-resource');assert.equal(s.posts[0].body.resource_id,'resource-safe');assert.equal('path' in s.posts[0].body,false);await s.respond();
});
test('empty inbox is accurate, selected tab survives refresh and no work is invented',async()=>{
  const s=await surface();assert.equal(s.element('workInboxPendingTab').textContent,'확인 필요 0');assert.match(s.element('workInboxPending').innerHTML,/확인할 요청이 없습니다/);assert.match(s.element('workInboxResources').innerHTML,/업무에 연결한/);
  s.click({workInboxTab:'resources'});await s.load();assert.equal(s.element('workInboxResources').hidden,false);assert.equal(s.element('workInboxResourcesTab').attrs['aria-pressed'],'true');assert.equal(s.posts.length,0);
});
test('calendar has a single instance below character and inbox replaces old rail location',()=>{
  const html=fs.readFileSync(path.join(__dirname,'..','workdesk.html'),'utf8');assert.equal((html.match(/id="workCalendar"/g)||[]).length,1);
  assert.ok(html.indexOf('id="workCompanion"')<html.indexOf('id="workCalendar"'));assert.ok(html.indexOf('id="workCalendar"')<html.indexOf('work-conversation-content'));assert.ok(html.indexOf('id="workInbox"')>html.indexOf('id="workLauncher"'));
});
