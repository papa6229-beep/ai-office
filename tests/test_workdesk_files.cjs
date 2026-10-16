const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(setImmediate);
const shipped = fs.readFileSync(path.join(__dirname, '..', 'workdesk.js'), 'utf8');

function surface() {
  const elements = new Map(), ready = [], posts = [], replies = [];
  let counter = 0;
  function element(id) {
    if (!elements.has(id)) {
      const listeners = new Map();
      elements.set(id, {id, value:'', textContent:'', innerHTML:'', hidden:false, disabled:false, open:false, dataset:{},
        classList:{toggle() {}}, focus() {}, showModal() {this.open=true;}, close() {this.open=false;},
        addEventListener(name, fn) {listeners.set(name,fn);},
        fire(name, event) {listeners.get(name)?.(event);},
        querySelectorAll() {return id === 'workFileDialog' ? [element('workFileQuery'),element('workFileKind'),element('workFileRetry')] : [];}
      });
    }
    return elements.get(id);
  }
  element('workFileKind').value='all';element('workFileRetry').dataset.filesAction='retry';
  const context = {document:{getElementById:element,addEventListener(name,fn){if(name==='DOMContentLoaded')ready.push(fn);}},
    Uint8Array, crypto:{getRandomValues(bytes){bytes.fill(++counter);return bytes;}},
    fetch:async(url,options)=>{
      if (options?.method !== 'POST') return {ok:true,status:200,json:async()=>({action_token:'local-token'})};
      posts.push({url,body:JSON.parse(options.body)});
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return {ok:reply?.status ? reply.status<400 : true,status:reply?.status || 200,json:async()=>reply?.data || {}};
    }
  };
  vm.runInNewContext(shipped.slice(shipped.indexOf('/* File search and chat use the same server capability;')),context);
  ready[0]();
  return {element,posts,replies,
    click(action,index) {const button={disabled:false,dataset:{filesAction:action,filesIndex:index}};element('work').fire('click',{target:{closest:()=>button}});},
    async search(query='시안') {element('workFileQuery').value=query;element('workFileForm').fire('submit',{preventDefault(){}});await tick();}
  };
}
const item = {id:'a'.repeat(64),selection_token:'ticket',name:'시안 <img>.psd',path:'C:/Work/<private>.psd',kind:'file',source:'작업 폴더'};

test('file dialog searches shared endpoint and escapes metadata while showing bounded coverage',async()=>{
  const s=surface();s.click('open');assert.equal(s.element('workFileDialog').open,true);
  s.replies.push({data:{items:[item],truncated:true,scopes:[{source:'작업 폴더',path:'C:/Work'}],warnings:['일부 범위 제외']}});
  await s.search();assert.equal(s.posts[0].url,'/api/files/search');assert.equal(s.posts[0].body.query,'시안');assert.equal(s.posts[0].body.token,'local-token');
  assert.match(s.element('workFileResults').innerHTML,/&lt;img&gt;|&lt;private&gt;/);assert.doesNotMatch(s.element('workFileResults').innerHTML,/<img>/);
  assert.match(s.element('workFileStatus').textContent,/제한/);assert.match(s.element('workFileCoverage').textContent,/C:\/Work/);
});

test('reveal sends issued selection and request id, never an editable path',async()=>{
  const s=surface();s.replies.push({data:{items:[item]}});await s.search();
  s.replies.push({data:{ok:true,dispatched:true}});s.click('reveal',0);await tick();
  const request=s.posts[1];assert.equal(request.url,'/api/files/reveal');assert.equal(request.body.id,item.id);assert.equal(request.body.selection_token,'ticket');assert.match(request.body.request_id,/^[a-f0-9]{32}$/);assert.equal('path' in request.body,false);
  assert.match(s.element('workFileStatus').textContent,/위치 표시를 요청/);assert.equal(s.element('workFileRetry').hidden,true);
});

test('uncertain reveal blocks new searches and retries exact request id',async()=>{
  const s=surface();s.replies.push({data:{items:[item]}});await s.search();
  s.replies.push(new Error('lost response'));s.click('reveal',0);await tick();
  const first=s.posts[1].body;assert.equal(s.element('workFileRetry').hidden,false);assert.equal(s.element('workFileQuery').disabled,true);
  await s.search('다른 파일');assert.equal(s.posts.length,2);
  s.click('close');assert.equal(s.element('workFileDialog').open,false);s.click('open');assert.equal(s.element('workFileRetry').hidden,false);
  s.replies.push({data:{ok:true}});s.click('retry');await tick();assert.deepEqual(s.posts[2].body,first);assert.equal(s.element('workFileQuery').disabled,false);
});

test('stale selection HTTP error reports failure and lets user search again',async()=>{
  const s=surface();s.replies.push({data:{items:[item]}});await s.search();
  s.replies.push({status:400,data:{error:'검색 이후 변경되었습니다.'}});s.click('reveal',0);await tick();
  assert.match(s.element('workFileStatus').textContent,/변경/);assert.equal(s.element('workFileRetry').hidden,true);assert.equal(s.element('workFileQuery').disabled,false);
});

test('empty or unsupported search never invents a result or reveals a guessed path',async()=>{
  const s=surface();s.replies.push({data:{items:[],scopes:['다운로드']}});await s.search('없는 파일');
  assert.match(s.element('workFileStatus').textContent,/확인한 범위/);assert.equal(s.element('workFileResults').innerHTML,'');s.click('reveal',0);await tick();assert.equal(s.posts.length,1);
});
