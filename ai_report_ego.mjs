import {beginObservation,beginAiSave,acceptObservedDialog,waitAiCommit} from './ego_report_dialogs.mjs';
const fs=await import('node:fs/promises');
const {fileURLToPath}=await import('node:url');
const cfg=JSON.parse(await fs.readFile(fileURLToPath(globalThis.aiReportPlan),'utf8'));
const origin='https://dailywork-phi.vercel.app';
const task=await taskSpace(cfg.space_id??cfg.space_name),page=task.page('p1');
const result={space_id:task.spaceId,day:cfg.day,browser:'ego',saved:false,verified:false,save_clicks:0,state:'running',stage_timings:{},model_ms:null,approval_wait_ms:null};
const canonical=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
const same=(a,b)=>canonical(a)===canonical(b);
async function getData(){const r=await page.fetch('/api/data',{headers:{'Cache-Control':'no-cache'},timeout:15000});if(!r.ok)throw new Error('report_read_'+r.status);const d=typeof r.body==='string'?JSON.parse(r.body):r.body;if(!d?.plan||!d?.months)throw new Error('report_shape');return d;}
try{
 let before,saveUrl,pending;
 if(cfg.resume_pending_prompt){
  // URL metadata is available through CDP while Runtime.evaluate is blocked by a prompt.
  const tab=(await task.tabs()).find(t=>t.label===page.label);if(!tab)throw new Error('report_tab_missing');saveUrl=tab.url;
  if(new URL(saveUrl).origin!==origin)throw new Error('report_origin');
  // Original receipt observed this exact modal; no action has dismissed it.
  // Browser tab metadata avoids renderer calls that hang while it is open.
  pending={type:'prompt',message:'입력 비밀번호를 넣어주세요.'};
  before=JSON.parse(await fs.readFile(cfg.resume_before,'utf8'));
 }else{
  if(await page.url()!==origin+'/?edit=1')await page.goto(origin+'/?edit=1',{waitUntil:'domcontentloaded',timeout:25000});
  await page.waitForFunction(()=>typeof LOADED!=='undefined'&&LOADED===true,undefined,{timeout:20000});
  saveUrl=await page.url();if(new URL(saveUrl).origin!==origin)throw new Error('report_origin');
  before=await getData();await fs.writeFile(cfg.folder+'/before.json',JSON.stringify(before,null,2),{encoding:'utf8',flag:'wx'});
 }
 const month=cfg.day.slice(0,7),record=structuredClone(cfg.record),previous=before.months[month]?.[cfg.day];
 if(previous){if(!record.next?.length)record.next=previous.next??[];if(!record.risk)record.risk=previous.risk??'';}
 const after=structuredClone(before);after.months[month]??={};after.months[month][cfg.day]={...(previous??{}),...record};
 if(!cfg.force_write&&!cfg.resume_pending_prompt&&same({plan:before.plan,months:before.months},{plan:after.plan,months:after.months})){
  result.saved=false;result.verified=true;result.state='done';result.unchanged=true;result.other_dates_preserved=true;result.plan_preserved=true;
 }else{
  let dialog=pending,observation;
  if(cfg.resume_pending_prompt)observation=await beginObservation(page,origin);
  if(!cfg.resume_pending_prompt){
   const now=await getData();if(!same({plan:before.plan,months:before.months},{plan:now.plan,months:now.months}))throw new Error('concurrent_report_change');
   await page.evaluate(({dayValue,monthValue,recordValue})=>{
    if(!EDIT||!LOADED)throw new Error('report_not_editable');if(saveTimer)throw new Error('existing_unsaved_timer');
    MONTHDATA[monthValue]??={};MONTHDATA[monthValue][dayValue]=recordValue;sel=dayValue;calCursor=monthValue;
    document.querySelector('#dateSel').value=dayValue;renderDay();buildCal();recalc();renderRecap();scheduleGrow();
   },{dayValue:cfg.day,monthValue:month,recordValue:after.months[month][cfg.day]});
   result.save_clicks++;
   observation=await beginAiSave(page,()=>page.click('#btnSave',{label:'Save today AI report'}));dialog=observation.dialog;
  }
  if(dialog){
   if(dialog.type!=='prompt'||dialog.message!=='입력 비밀번호를 넣어주세요.')throw new Error('unexpected_report_dialog');
   const r=await fetch(cfg.broker,{method:'POST',headers:{Authorization:'Bearer '+cfg.token,'Content-Type':'application/json'},body:JSON.stringify({url:saveUrl,prompt:dialog.message})});
   if(!r.ok)throw new Error('credential_broker');const value=await r.json();
   try{await acceptObservedDialog(page,value.password);observation.dialog=null;}finally{value.password='';}
  }
  const confirmationStarted=performance.now();
  await waitAiCommit(page,observation);result.save_response_verified=true;
  await page.waitForFunction(()=>document.querySelector('#saveState')?.textContent.startsWith('저장됨'),undefined,{timeout:25000});
  const actual=await getData();if(!same({plan:actual.plan,months:actual.months},{plan:after.plan,months:after.months}))throw new Error('report_readback_mismatch');
  await fs.writeFile(cfg.folder+'/after.json',JSON.stringify(actual,null,2),{encoding:'utf8',flag:'wx'});
  result.saved=true;result.verified=true;result.state='done';result.updated_at=actual.updatedAt;result.other_dates_preserved=true;result.plan_preserved=true;
  result.stage_timings.save_confirmation={calls:1,elapsed_ms:Math.round(performance.now()-confirmationStarted)};
  result.resumed_pending_prompt=!!cfg.resume_pending_prompt;
 }
 result.sections=record.done.length;result.capabilities=record.capabilities.length;
 result.rendered=await page.evaluate(()=>({day:document.querySelector('#dateSel').value,sections:document.querySelectorAll('#done .row').length,capabilities:document.querySelectorAll('.capability-card').length,save:document.querySelector('#saveState').textContent}),null);
 await task.finish({keep:[]});result.finished=true;
}catch(error){result.state=result.verified?'done':'uncertain';result.post_verification_error=!!result.verified;result.error_type=error.name;result.error=String(error.message).slice(0,200);result.automatic_retry=false;}
console.log('AI_REPORT_RESULT:'+JSON.stringify(result));
