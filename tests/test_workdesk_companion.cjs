const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'..','workdesk.js'),'utf8');
const tick=()=>new Promise(setImmediate);
async function surface({offline=false}={}){
  const elements=new Map(),ready=[],requests=[],controls={offline},base={action_token:'fixture',tasks:[],messages:[],counts:{}};
  function element(id){
    if(!elements.has(id)){
      const handlers=new Map(),classes=new Set();let src='';
      const node={id,value:'',disabled:false,open:false,textContent:'',innerHTML:'',scrollHeight:0,scrollTop:0,clientHeight:0,attributes:{},srcWrites:0,
        classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);},contains(name){return classes.has(name);}},
        addEventListener(name,fn){handlers.set(name,fn);},fire(name,event={}){return handlers.get(name)?.(event);},setAttribute(k,v){this.attributes[k]=v;},
        querySelectorAll(){return [];},closest(){return null;},matches(){return false;},focus(){},reset(){},showModal(){this.open=true;},close(){this.open=false;},
        get src(){return src;},set src(value){src=value;this.srcWrites++;}};
      elements.set(id,node);
    }
    return elements.get(id);
  }
  const context={document:{getElementById:element,addEventListener(n,fn){if(n==='DOMContentLoaded')ready.push(fn);},dispatchEvent(){}},window:{},Intl,Date,Map,Uint8Array,CustomEvent:class{},crypto:{getRandomValues(b){b.fill(1);return b;}},
    fetch:async(url,options)=>{
      if(options?.method==='POST'){
        requests.push({url,body:JSON.parse(options.body)});const result=await new Promise(resolve=>controls.release=resolve);
        if(result.network)throw Error('network');return {ok:result.ok??true,status:result.status??200,json:async()=>({...base,...result.data})};
      }
      if(controls.offline)throw Error('offline');return {ok:true,status:200,json:async()=>({...base,...controls.data})};
    }};
  vm.runInNewContext(source,context);ready[0]();await tick();
  const state=()=>element('workCompanion').attributes['data-state'];
  const type=text=>{element('workCommand').value=text;element('workCommand').fire('input');};
  const submit=(id='workCommandForm')=>element('work').fire('submit',{target:element(id),preventDefault(){}});
  const click=dataset=>element('work').fire('click',{target:{closest:()=>({dataset,disabled:false})}});
  return {element,controls,requests,state,type,submit,click,load:context.window.workLoad,async respond(result={}){controls.release(result);await tick();}};
}
test('companion follows idle and draft/IME state without restarting the same animation',async()=>{
  const s=await surface();assert.equal(s.state(),'idle');s.type('한');assert.equal(s.state(),'typing');
  const writes=s.element('workCompanionAnimation').srcWrites;s.type('한글');s.element('workCommand').fire('compositionend');s.click({workAction:'clear-context'});
  assert.equal(s.element('workCompanionAnimation').srcWrites,writes);assert.equal(s.requests.length,0);
  s.type('');assert.equal(s.state(),'idle');
});
test('slow command stays working until actual response then shows answer, not task completion',async()=>{
  const s=await surface();s.type('열어줘');s.submit();assert.equal(s.state(),'working');await tick();assert.equal(s.state(),'working');
  const writes=s.element('workCompanionAnimation').srcWrites;s.click({workAction:'clear-context'});assert.equal(s.element('workCompanionAnimation').srcWrites,writes);
  await s.respond();assert.equal(s.state(),'answer');assert.equal(s.element('workCompanionStatus').textContent,'답변이 도착했어요');
  s.type('새 요청');assert.equal(s.state(),'typing');s.type('');assert.equal(s.state(),'idle');
});
test('confirmed record save shows agree and pending cards await selection instead',async()=>{
  const s=await surface();s.element('workTaskTitle').value='새 업무';s.submit('workTaskForm');await s.respond();assert.equal(s.state(),'agree');assert.equal(s.element('workCompanionStatus').textContent,'기록을 저장했어요');
  for(const field of ['assistant_actions','launcher_choices']){
    const pending=await surface();pending.type('등록');pending.submit();await pending.respond({data:{messages:[{id:'reply',role:'assistant',content:'골라주세요'}],[field]:[{id:'choice',message_id:'reply',options:[{id:'one',label:'선택',title:'선택'}]}]}});
    assert.equal(pending.state(),'idle');assert.equal(pending.element('workCompanionStatus').textContent,'선택을 기다리고 있어요');
  }
});
test('confirmed errors stay visible through input until an explicit new successful request',async()=>{
  const s=await surface();s.type('요청');s.submit();await s.respond({ok:false,status:400,data:{error:'실패'}});assert.equal(s.state(),'error');s.type('다시');assert.equal(s.state(),'error');
  s.submit();assert.equal(s.state(),'working');await s.respond();assert.equal(s.state(),'answer');
});
test('uncertain result remains error and same request retry can recover',async()=>{
  const s=await surface();s.type('요청');s.submit();await s.respond({network:true});assert.equal(s.state(),'error');assert.equal(s.element('workCompanionStatus').textContent,'결과 확인이 필요해요');
  s.click({workAction:'retry'});await tick();assert.equal(s.state(),'working');await s.respond();assert.equal(s.state(),'answer');assert.equal(s.requests[1].body.request_id,s.requests[0].body.request_id);
});
test('connection error recovers only on explicit reload and failed assets do not block chat',async()=>{
  const s=await surface({offline:true});assert.equal(s.state(),'error');s.controls.offline=false;await s.load();assert.equal(s.state(),'idle');
  s.element('workCompanionAnimation').fire('error');assert.equal(s.element('workCompanion').classList.contains('is-gif-unavailable'),true);
  s.element('workCompanionStill').fire('error');assert.equal(s.element('workCompanion').classList.contains('is-still-unavailable'),true);
  s.type('입력');s.submit();assert.equal(s.requests.length,1);await s.respond();assert.equal(s.state(),'answer');
});
test('assets and reduced motion use isolated companion rail with no character overlay',()=>{
  const html=fs.readFileSync(path.join(__dirname,'..','workdesk.html'),'utf8'),css=fs.readFileSync(path.join(__dirname,'..','workdesk.css'),'utf8');
  assert.match(html,/work-conversation-layout/);assert.match(html,/workCompanionStatus" role="status" aria-live="polite"/);assert.match(css,/prefers-reduced-motion:reduce/);assert.match(css,/work-conversation-layout\{display:grid;grid-template-columns:180px minmax\(0,1fr\)/);
});
