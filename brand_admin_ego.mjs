// Same-origin sample updates through the installed official Ego API.
// Login material stays in the authenticated page; only five web-team fields can change.
const fs=await import('node:fs/promises');
const {fileURLToPath}=await import('node:url');
const cfg=JSON.parse(await fs.readFile(fileURLToPath(globalThis.brandAdminConfig),'utf8'));
const task=await taskSpace(cfg.space_id??cfg.name);
const page=task.page(cfg.page??'p1');
const output={space_id:task.spaceId,page:page.label,finished:false,items:[],mode:'code',model_calls:0};
const save=()=>fs.writeFile(cfg.result,JSON.stringify(output,null,2));
await save();
try{
  if(!String(await page.url()).startsWith('https://product.bbnn.kr/'))await page.goto('https://product.bbnn.kr/samples');
  const result=await page.evaluate(async cfg=>{
    if(location.origin!=='https://product.bbnn.kr')throw Error('admin_origin_mismatch');
    const token=localStorage.getItem('bananmall_token');
    if(!token)throw Error('admin_login_required');
    const fields=['registration_status','worker','memo','start_date','end_date'];
    const equal=(a,b)=>JSON.stringify(a??null)===JSON.stringify(b??null);
    const request=async(path,method='GET',body)=>{
      if(!/^\/api\/samples(?:\?tab=(?:cn|jp|kr)_(?:in_progress|done)|\/\d+)$/.test(path))throw Error('admin_path_rejected');
      if(method!=='GET'&&(method!=='PUT'||!/^\/api\/samples\/\d+$/.test(path)||!body||Object.keys(body).some(k=>!fields.includes(k))))throw Error('admin_fields_rejected');
      const response=await fetch(path,{method,cache:'no-store',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.timeout(12000)});
      if(!response.ok)throw Error('admin_http_'+response.status);
      return response.status===204?null:response.json();
    };
    const load=async(tab)=>{const value=await request('/api/samples?tab='+tab);return value.data??value;};
    const identity=(row,expected)=>row&&['supplier_name','warehouse_arrival_date','sample_group'].every(k=>equal(row[k],expected[k]))&&(!expected.memo_label||String(row.memo||'').split('\n').some(line=>line.trim()===expected.memo_label));
    const results=[];
    for(const plan of cfg.plans){
      for(const target of plan.targets){
        const item={event_id:plan.event_id,batch_id:plan.batch_id,sample_id:target.id};
        try{
          const tab=target.tab||cfg.tab;
          const current=(await load(tab)).find(r=>r.id===target.id);
          if(!identity(current,target.identity)){results.push({...item,state:'needs_binding'});continue;}
          const desired={registration_status:plan.status,worker:plan.worker||null};
          if(plan.remarks!==undefined){
            const lines=String(current.memo||'').split('\n').filter(line=>!line.startsWith('[웹팀] '));
            const note=String(plan.remarks||'').split('\n').filter(line=>line.trim()!==target.identity.memo_label).join('\n').trim();
            if(note)lines.push('[웹팀] '+note);
            const memo=lines.filter(Boolean).join('\n');
            // Empty Office remarks never erase someone else's existing memo.
            if(memo!==String(current.memo||''))desired.memo=memo||null;
          }
          if(plan.start_date&&!current.start_date)desired.start_date=plan.start_date;
          if(plan.end_date&&!current.end_date)desired.end_date=plan.end_date;
          if(plan.waiting){
            // Clearing dates requires an explicit initial reconciliation plan.
            if(plan.clear_waiting_dates){desired.start_date=null;desired.end_date=null;}
            else if(current.start_date||current.end_date){results.push({...item,state:'conflict',reason:'waiting_has_dates'});continue;}
          }
          const patch=Object.fromEntries(Object.entries(desired).filter(([k,v])=>!equal(current[k],v)));
          if(!Object.keys(patch).length){results.push({...item,state:'unchanged',values:Object.fromEntries(fields.map(k=>[k,current[k]??null]))});continue;}
          // A separate reader may have edited an owned field after the plan was made.
          if(target.expected&&Object.keys(patch).some(k=>!equal(current[k],target.expected[k])&&!equal(current[k],desired[k]))){results.push({...item,state:'conflict'});continue;}
          const before=current;
          let failure=null;
          try{await request('/api/samples/'+target.id,'PUT',patch);}catch(error){failure=error.message;}
          const after=(await load(tab)).find(r=>r.id===target.id);
          const verified=after&&Object.entries(patch).every(([k,v])=>equal(after[k],v));
          const preserved=after&&Object.keys(before).filter(k=>!fields.includes(k)).every(k=>equal(before[k],after[k]));
          results.push({...item,state:verified&&preserved?'verified':failure?'uncertain':'verification_failed',fields:Object.keys(patch),...(verified&&preserved?{values:Object.fromEntries(fields.map(k=>[k,after[k]??null]))}:{}),...(failure?{reason:failure}:{})});
        }catch(error){results.push({...item,state:'pending',reason:error.message});}
      }
    }
    return results;
  },{tab:cfg.tab,plans:cfg.plans});
  output.items=result;output.sync_complete=true;await save();
  if(cfg.refresh&&result.every(r=>['verified','unchanged'].includes(r.state))){
    try{
      await page.reload();
      await page.waitForFunction(()=>[...document.querySelectorAll('h1,h2')].some(n=>n.textContent.includes('신상 샘플 현황'))&&document.querySelector('tbody tr'),undefined,{timeout:15000});
      output.visible=await page.evaluate(()=>({rows:document.querySelectorAll('tbody tr').length,firstWorker:document.querySelector('tbody tr')?.cells[11]?.querySelector('input')?.value,firstStart:document.querySelector('tbody tr')?.cells[12]?.querySelector('input')?.value}));
    }catch(error){output.view_error=String(error.message).slice(0,160);}
    await save();
  }
  if(cfg.finish!==false&&!output.view_error&&result.every(r=>['verified','unchanged','needs_binding','conflict'].includes(r.state))){await task.finish({keep:[]});output.finished=true;await save();}
}catch(error){output.error=String(error.message).slice(0,160);await save();throw error;}
console.log(JSON.stringify(output));
