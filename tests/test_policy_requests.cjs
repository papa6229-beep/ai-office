const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function surface() {
  const elements=new Map(), posts=[], replies=[];
  let seq=0;
  const scope={policy_id:'p',revision:1,source_dir:'C:/Inbox',destination_dir:'C:/Archive'};
  const state={policy:{...scope,authorized:true,auto_enabled:false},policy_scope:scope,job_scopes:[{id:'j',scope}],jobs:[],running:false,last_error:''};
  const element=id=>{
    if(!elements.has(id))elements.set(id,{id,value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,checked:false,addEventListener(){},classList:{contains(){return true;}}});
    return elements.get(id);
  };
  const context=vm.createContext({document:{getElementById:element,addEventListener(){}},deskData:{action_token:'token'},
    esc:String,crypto:{getRandomValues(bytes){bytes.fill(++seq);return bytes;}},Uint8Array,setInterval(){},
    fetch:async(url,options)=>{
      if(!options?.method)return {ok:true,json:async()=>state};
      posts.push({url,body:JSON.parse(options.body)});
      const reply=replies.shift();if(reply instanceof Error)throw reply;
      return {ok:reply?.ok ?? true,json:async()=>reply?.data || state};
    },
    deskRequest:async()=>state
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','desk-policy.js'),'utf8'),context);
  context.seed=state;vm.runInContext('policyData=seed',context);
  return {context,element,posts,replies,state,
    run(expression){return vm.runInContext(expression,context);},
    pending(){return JSON.parse(vm.runInContext('JSON.stringify(policyPending)',context));}
  };
}

test('policy UI binds requests to observed scope with an idempotency key',async()=>{
  const s=surface();await s.run('policyAction("run")');
  assert.equal(s.posts[0].url,'/api/desk/policy-run');assert.deepEqual(s.posts[0].body.expected_scope,s.state.policy_scope);assert.match(s.posts[0].body.request_id,/^[a-f0-9]{32}$/);assert.equal(s.pending(),null);
});

test('uncertain policy request blocks another move and retries original scope and id',async()=>{
  const s=surface();s.replies.push(new Error('connection lost'));await s.run('policyAction("run")');
  const first=s.posts[0].body;assert.equal(s.element('policyRetry').hidden,false);assert.equal(s.element('policyRun').disabled,true);
  s.context.changed={...s.state,policy_scope:{...s.state.policy_scope,revision:2}};s.run('policyData=changed');
  await s.run('policyAction("enabled",{enabled:true})');assert.equal(s.posts.length,1);
  await s.run('policyAction("run",{},true)');assert.deepEqual(s.posts[1].body,first);assert.equal(s.pending(),null);assert.equal(s.element('policyRetry').hidden,true);
});

test('undo is bound to the original observed job record',async()=>{
  const s=surface();await s.run('policyAction("undo",{id:"j"})');
  assert.deepEqual(s.posts[0].body.expected_job,s.state.job_scopes[0]);assert.equal('expected_scope' in s.posts[0].body,false);
});

test('definite server rejection clears pending and missing scope never starts a move',async()=>{
  const s=surface();s.replies.push({ok:false,data:{error:'정리 범위가 변경되었습니다.'}});await s.run('policyAction("run")');assert.equal(s.pending(),null);assert.match(s.element('policyMessage').textContent,/변경/);
  s.run('policyData={policy:null,jobs:[],running:false}');await s.run('policyAction("run")');assert.equal(s.posts.length,1);assert.match(s.element('policyMessage').textContent,/범위를 새로/);
});

test('save follow-up preview uses returned scope and retains its request if response is lost',async()=>{
  const s=surface();s.replies.push(new Error('preview response lost'));await s.run('policyAction("save",{})');assert.equal(s.posts[0].url,'/api/desk/policy-preview');assert.equal(s.pending().action,'preview');assert.deepEqual(s.posts[0].body.expected_scope,s.state.policy_scope);
});
