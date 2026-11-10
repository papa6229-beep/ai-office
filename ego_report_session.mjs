import {saveBannerWithDialogs,saveEventWithDialogs,saveProductWithDialogs} from './ego_report_dialogs.mjs';
const fs=await import('node:fs/promises'),http=await import('node:http');
const {fileURLToPath}=await import('node:url');
const cfg=JSON.parse(await fs.readFile(fileURLToPath(globalThis.egoReportConfig),'utf8'));
const task=await taskSpace(cfg.space_id??cfg.name),policies=new Map();
let queue=Promise.resolve(),finished=false;
const sel=c=>c.selector+(c.index===null||c.index===undefined?'':' >> nth='+c.index);
async function element(page,c,fn){
 const fnSource='c=>{const list=[...document.querySelectorAll(c.selector)];const i=c.index==null?0:c.index<0?list.length+c.index:c.index;const e=list[i];'+
   (c.index==null?'if(list.length!==1)throw new Error("report_element_not_unique");':'')+
   'if(!e)throw new Error("report_element_missing");return ('+fn+')(e);}';
 return page.evaluate(Function('return ('+fnSource+')')(),{selector:c.selector,index:c.index??null});
}
async function dialogs(page,receipt){
 if(policies.get(page.label)==='product_save')return;
 for(let i=0;i<4;i++){
  const d=i===0&&receipt?.dialog?receipt.dialog:(await page.info()).dialog;if(!d)return;
  if(policies.get(page.label)==='product_save'&&(d.type==='alert'||(d.type==='confirm'&&/변경|수정|저장/.test(d.message))))await page.acceptDialog();
  else{await page.dismissDialog();throw new Error('unexpected_report_dialog');}
 }
 throw new Error('report_dialog_limit');
}
async function dispatch(c){
 if(finished)throw new Error('report_session_finished');
 if(c.op==='pages')return (await task.tabs()).filter(p=>p.label).map(p=>({label:p.label,url:p.url}));
 if(c.op==='new_page'){const p=await task.newPage();return {label:p.label};}
 if(c.op==='finish'){await task.finish({keep:c.keep});finished=true;return {finished:true};}
 const page=task.page(c.page);
 if(c.op==='dialog_policy'){policies.set(page.label,c.policy);return true;}
 // Task tab metadata remains available while a synchronous modal blocks JS.
 if(c.op==='url'){const tab=(await task.tabs()).find(p=>p.label===page.label);if(!tab)throw new Error('report_tab_missing');return tab.url;}
 if(c.op==='goto'){await page.goto(c.url,{waitUntil:'domcontentloaded',timeout:c.timeout});await dialogs(page);return true;}
 if(c.op==='close'){await page.close();return true;}
 if(c.op==='count')return page.evaluate(s=>document.querySelectorAll(s).length,c.selector);
 if(c.op==='value')return element(page,c,'e=>e.value');
 if(c.op==='attribute')return element(page,c,'e=>e.getAttribute('+JSON.stringify(c.name)+')');
 if(c.op==='visible')return page.evaluate(c=>{const es=[...document.querySelectorAll(c.selector)],i=c.index==null?0:c.index<0?es.length+c.index:c.index;const e=es[i];return !!e&&!!(e.getClientRects().length)&&getComputedStyle(e).visibility!=='hidden';},{selector:c.selector,index:c.index??null});
 if(c.op==='fill'){await page.fill(sel(c),c.value);return true;}
 if(c.op==='click'){
  // Invoke this inspected editor button once through its own click handler.
  // High-level pointer scrolling timed out without persisting this form.
  if(policies.get(page.label)==='product_save'&&c.selector==='a[href="javascript:saveContent();"]'){
   return saveProductWithDialogs(page,()=>element(page,c,'e=>{e.click();return true;}'));
  }
  const receipt=await page.click(sel(c),{timeout:c.timeout,label:'Execute registered report action'});await dialogs(page,receipt);return true;
 }
 if(c.op==='publish_event'){
  const editor=new URL((await task.tabs()).find(tab=>tab.label===page.label)?.url);
  if(editor.origin!=='https://banana.bizhost.kr'||editor.pathname!=='/adminx/newdesign.php'||editor.searchParams.get('code')!=='editor')
   throw Error('event_editor_origin_mismatch');
  const button={selector:'.btn_save[data-form="#regiform"][data-savetype="save"] > a'};
  return saveEventWithDialogs(page,()=>element(page,button,'e=>{e.click();return true;}'));
 }
 if(c.op==='set_banner_file'||c.op==='submit_banner'){
  const path='/adminx/popups/nmain_data2.banana.php';
  const target=new URL((await task.tabs()).find(tab=>tab.label===page.label)?.url);
  if(target.origin!=='https://banana.bizhost.kr'||target.pathname!==path||target.searchParams.get('index_no')!==String(c.slot)
      ||![111,138,437,329,325,108,47,426,427].includes(c.slot))throw Error('banner_editor_target_mismatch');
  if(c.op==='set_banner_file'){
   await page.setInputFiles('form#search[name="regiform"] input[type="file"][name="file"]',[c.path]);
   return true;
  }
  const button={selector:'.btn_submit[data-form="#search"] > a'};
  return saveBannerWithDialogs(page,()=>element(page,button,'e=>{e.click();return true;}'),path);
 }
 if(c.op==='element_evaluate')return element(page,c,c.fn);
 if(c.op==='scroll')return element(page,c,'e=>{e.scrollIntoView({block:"nearest"});return true;}');
 if(c.op==='evaluate'){const fn=Function('return ('+c.fn+')')();const value=await page.evaluate(fn,c.arg??null);await dialogs(page);return value;}
 if(c.op==='wait_selector'){await page.waitForSelector(sel(c),{state:c.state,timeout:c.timeout});return true;}
 if(c.op==='wait_function'){const fn=Function('return ('+c.fn+')')();await page.waitForFunction(fn,undefined,{timeout:c.timeout});return true;}
 if(c.op==='wait_load'){await page.waitForLoadState(c.state,{timeout:c.timeout});await dialogs(page);return true;}
 if(c.op==='fetch'){
  const tab=(await task.tabs()).find(p=>p.label===page.label);if(!tab)throw new Error('report_tab_missing');
  const u=new URL(c.url),current=new URL(tab.url);
  const images=new Set(['cdn-banana.bizhost.kr','www.bananamall.co.kr','bananamall.co.kr']);
  if(c.method==='GET'&&u.protocol==='https:'&&images.has(u.hostname)){
   const r=await fetch(c.url,{redirect:'error',signal:AbortSignal.timeout(c.timeout)});
   if(Number(r.headers.get('content-length')||0)>24*1024*1024)throw new Error('report_image_too_large');
   const body=Buffer.from(await r.arrayBuffer());if(body.length>24*1024*1024)throw new Error('report_image_too_large');
   return {ok:r.ok,status:r.status,base64:body.toString('base64')};
  }
  if(u.origin!==current.origin)throw new Error('report_fetch_origin_mismatch');
  const options={method:c.method,timeout:c.timeout,headers:{'Cache-Control':'no-cache'}};
  if(c.form){options.headers['Content-Type']='application/x-www-form-urlencoded';options.body=new URLSearchParams(c.form).toString();}
  else if(c.data!==null&&c.data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(c.data);}
  const r=await page.fetch(c.url,options);
  return {ok:r.ok,status:r.status,body:r.body};
 }
 throw new Error('unsupported_report_operation');
}
const server=http.createServer((req,res)=>{
 if(req.method!=='POST'||req.url!=='/command'||req.headers.authorization!=='Bearer '+cfg.token){res.writeHead(403);res.end();return;}
 let raw='',tooBig=false;
 req.on('data',chunk=>{raw+=chunk;if(raw.length>1024*1024){tooBig=true;req.destroy();}});
 req.on('end',()=>{
  if(tooBig)return;
  const execute=async()=>{
   res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
   try{const value=await dispatch(JSON.parse(raw));res.end(JSON.stringify({ok:true,value}));}
   catch(error){res.end(JSON.stringify({ok:false,error:String(error.message).slice(0,240),error_type:error.name,
    error_code:error.name==='PageDialogOpenedError'?'dialog_open':String(error.message).includes('JSON-serializable')?'serialization_error':'command_failed',automatic_retry:false}));}
  };
  queue=queue.then(execute,execute);
 });
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
await fs.writeFile(cfg.ready,JSON.stringify({port:server.address().port,space_id:task.spaceId,adapter_version:1}),{flag:'wx'});
await new Promise(resolve=>{const timer=setInterval(()=>{if(finished){clearInterval(timer);server.close(resolve);}},100);});
