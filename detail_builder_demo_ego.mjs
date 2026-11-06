// Registered rehearsal: approved JSON is read by the builder's file.text()
// handler and saved to IndexedDB. No upload endpoint or AI feature is called.
const fs=await import('node:fs/promises'),{createHash}=await import('node:crypto');
const {fileURLToPath}=await import('node:url');
const cfg=JSON.parse(await fs.readFile(fileURLToPath(globalThis.detailDemoPlan),'utf8'));
const receipt=cfg.folder+'/ego-result.json';
const result=JSON.parse(await fs.readFile(receipt,'utf8'));
const save=()=>fs.writeFile(receipt,JSON.stringify(result,null,2));
const canonical=v=>v===null||typeof v!=='object'?JSON.stringify(v):Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
const compact=v=>typeof v==='string'&&v.startsWith('data:image/')?{image_sha256:createHash('sha256').update(v).digest('hex')}:Array.isArray(v)?v.map(compact):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,compact(x)])):v;
async function documentData(page){return page.evaluate(async()=>{
 const db=await new Promise((ok,no)=>{const r=indexedDB.open('detail-page-builder-workspace',1);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
 const read=(s,k)=>new Promise((ok,no)=>{const r=db.transaction(s,'readonly').objectStore(s).get(k);r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
 try{const a=await read('workspace','active');if(!a)throw Error('No active project');
  const [meta,doc]=await Promise.all([read('headers',a.id),read('documents',a.id)]);
  const walk=async v=>{if(typeof v==='string'&&v.startsWith('data:image/')){const h=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v));return {image_sha256:[...new Uint8Array(h)].map(x=>x.toString(16).padStart(2,'0')).join('')}};
   if(Array.isArray(v))return Promise.all(v.map(walk));if(v&&typeof v==='object')return Object.fromEntries(await Promise.all(Object.entries(v).map(async([k,x])=>[k,await walk(x)])));return v};
  return {meta,data:await walk(doc.data)};
 }finally{db.close()}
});}
async function saved(page){await page.waitForFunction(()=>[...document.querySelectorAll('[role=status]')].some(e=>e.textContent.includes('저장됨')),undefined,{timeout:25000});}
async function step(name,operation){
 if(result.steps[name])return result.steps[name];
 if(result.pending)throw Error('Previous action needs reconciliation: '+result.pending);
 result.pending=name;await save();const started=performance.now();
 const value=await operation();result.steps[name]={...value,elapsed_ms:Math.round(performance.now()-started)};
 delete result.pending;await save();return result.steps[name];
}
try{
 const task=await taskSpace(result.space_id??cfg.space_name),page=task.page('p1');
 result.space_id=task.spaceId;await save();
 if(cfg.action==='setup'){
  await step('empty_editor',async()=>{
   await page.goto(cfg.origin,{waitUntil:'domcontentloaded',timeout:30000});
   await page.waitForFunction(()=>!document.body.innerText.includes('작업을 불러오는 중'),undefined,{timeout:20000});
   if(await page.evaluate(()=>!!document.querySelector('aside'))){
    await saved(page);await page.click("loc=role:button[name='작업 홈']");
   }
   await page.click("loc=role:button[name='새 상품 작업']");
   await page.waitForSelector('aside',{state:'visible',timeout:20000});await saved(page);
   const current=await documentData(page);
   if(current.data.productNameKr)throw Error('New project is not empty');
   return {project_id:current.meta.id,empty_editor:true};
  });result.state='prepared';await save();
 }else if(cfg.action==='build'){
  const expected=compact(JSON.parse(await fs.readFile(cfg.template,'utf8')).data);
  await step('project',async()=>{
   await page.click("loc=role:button[name='불러오기']");
   await page.waitForSelector('input[aria-label="작업 파일 가져오기"]',{state:'attached',timeout:15000});
   await page.setInputFiles('input[aria-label="작업 파일 가져오기"]',[cfg.import_file]);
   await page.waitForSelector('dialog[aria-label="작업 보관함"]',{state:'hidden',timeout:60000});await saved(page);
   const actual=await documentData(page);
   if(canonical(actual.data)!==canonical(expected))throw Error('Imported document mismatch');
   return {project_id:actual.meta.id,verified:true};
  });
  const current=await documentData(page);
  if(current.meta.id!==result.steps.project.project_id||canonical(current.data)!==canonical(expected))throw Error('User changed the project; preserved');
  for(const [stage,button,target,timeout] of [['export','전체 이미지 저장',cfg.detail_zip,90000],['thumbnails','썸네일 저장',cfg.thumbnails_zip,60000]]){
   await step(stage,async()=>{
    try{await fs.access(target);throw Error('Existing output needs reconciliation');}catch(e){if(e.code!=='ENOENT')throw e;}
    const waiting=page.waitForEvent('download',{timeout});
    await page.click("loc=role:button[name='"+button+"']");const download=await waiting;
    if(!download.suggestedFilename().endsWith('.zip'))throw Error('Expected ZIP output');
    await download.saveAs(target);if(await download.failure())throw Error('Download failed');
    return {path:target,bytes:(await fs.stat(target)).size};
   });
  }
  result.state='outputs_saved';await save();
 }
 if(cfg.action==='finish'){
  await task.finish({keep:[page.label]});
  result.finished=true;await save();
 }
 console.log('DETAIL_DEMO_RESULT:'+JSON.stringify({state:result.state,space_id:result.space_id,steps:Object.keys(result.steps),finished:result.finished}));
}catch(error){result.state='uncertain';result.error_type=error.name;result.error=String(error.message).slice(0,240);result.automatic_retry=false;await save();throw error;}
