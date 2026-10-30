// Runs inside the installed ego-browser Node runtime. Only documented Ego APIs.
const fs = await import('node:fs/promises');
const urls = await import('node:url');
const plan = JSON.parse(await fs.readFile(urls.fileURLToPath(globalThis.officeEgoPlan), 'utf8'));
const result = {state:'running',steps:[],verified:false,finished:false};
const marks = new Map();
let task, lastPage='p1', index=-1;
const safeURL = value => {try {const u=new URL(value);return u.origin+u.pathname;} catch{return '';}};
const send = (kind,value) => console.log('OFFICE_EGO_'+kind+':'+JSON.stringify(value));
async function secret(action, page, data={}) {
  const response = await fetch(plan.broker,{method:'POST',headers:{Authorization:'Bearer '+plan.token,'Content-Type':'application/json'},body:JSON.stringify({action,index,url:action==='checkpoint'?null:await page.url(),...data})});
  const value=await response.json();
  if(!response.ok||value.error)throw new Error(value.error||'SecretBrokerUnavailable');
  return value;
}
function css(selector) {
  // Form/read helpers intentionally require observed top-level CSS. Native Ego
  // click/wait methods still accept refs and documented semantic selectors.
  if(typeof selector!=='string'||!selector.trim())throw new Error('SelectorRequired');
  return selector.replace(/^(?:loc=css:|css=)/,'');
}
async function field(page,selector) {
  return await page.evaluate(s=>{
    const nodes=[...document.querySelectorAll(s)];
    if(nodes.length!==1)throw new Error('AmbiguousField');
    const e=nodes[0],r=e.getBoundingClientRect();
    if(!r.width||!r.height||e.disabled)throw new Error('UnavailableField');
    return {origin:location.origin,type:e.type||'',tag:e.tagName.toLowerCase()};
  },css(selector));
}
try {
  task=await taskSpace(plan.space);result.space_id=task.spaceId;
  send('SPACE',{space_id:task.spaceId});
  for(index=0;index<plan.steps.length;index++) {
    const step=plan.steps[index],started=Date.now(),timeout=step.timeout_ms||15000;
    // Checking a finished/handed-off space is delegated to the official runtime;
    // an error stops the batch. No claim/takeover/recovery space is attempted.
    let page=task.page(step.page==='$last'?lastPage:(step.page||'p1'));
    await secret('checkpoint',page);
    let data={};
    if(step.when_selector||step.unless_selector) {
      const allowed=await page.evaluate(({when,unless})=>{
        const visible=s=>s&&[...document.querySelectorAll(s)].some(e=>{const r=e.getBoundingClientRect();return r.width&&r.height;});
        return (!when||visible(when))&&(!unless||!visible(unless));
      },{when:step.when_selector?css(step.when_selector):'',unless:step.unless_selector?css(step.unless_selector):''});
      if(!allowed){const row={index,op:step.op,page:page.label,state:'skipped',reason:'condition',elapsed_ms:Date.now()-started};result.steps.push(row);send('STEP',row);continue;}
    }
    if(['open','fill','select','click','press','login','otp_fill'].includes(step.op))result.verified=false;
    if(step.op==='new_page') {page=await task.newPage();lastPage=page.label;data.page=page.label;}
    else if(step.op==='open') {await page.goto(step.url,{waitUntil:'domcontentloaded',timeout});data.url=safeURL(await page.url());}
    else if(step.op==='mark') {marks.set(step.name||'challenge',Date.now()/1000);data.at=marks.get(step.name||'challenge');}
    else if(step.op==='inspect') {
      data=await page.evaluate(({query,limit})=>{
        const norm=s=>(s||'').replace(/\s+/g,' ').trim();
        const candidates=[...document.querySelectorAll('input,textarea,select,button,a[href]')];
        const controls=[];let matched=0;
        for(const e of candidates) {
          const r=e.getBoundingClientRect();if(!r.width||!r.height)continue;
          const label=norm(e.getAttribute('aria-label')||e.labels?.[0]?.textContent||e.textContent||e.getAttribute('placeholder'));
          if(query&&!`${label} ${e.name||''} ${e.id||''}`.toLowerCase().includes(query.toLowerCase()))continue;
          matched++;
          if(controls.length>=limit)continue;
          const tag=e.tagName.toLowerCase();
          let selector=e.id?'#'+CSS.escape(e.id):e.name?`${tag}[name=${JSON.stringify(e.name)}]`:null;
          if(selector&&document.querySelectorAll(selector).length!==1)selector=null;
          controls.push({tag,type:e.type||'',label:label.slice(0,160),selector,disabled:!!e.disabled});
        }
        return {controls,matched,truncated:matched>limit,frames:document.querySelectorAll('iframe').length};
      },{query:step.query||'',limit:Math.min(100,step.limit||50)});
      data.url=safeURL(await page.url());
    }
    else if(step.op==='login') {
      const password=await field(page,step.password_selector);
      if(password.type!=='password'||!password.origin.startsWith('https://'))throw new Error('ObservedHTTPSPasswordRequired');
      if(step.username_selector&&((await field(page,step.username_selector)).origin!==password.origin))throw new Error('OriginMismatch');
      const value=await secret('login',page);
      // Secrets stay in memory, and this write checks origin and fields together
      // in the document. It cannot fall through into a cross-origin iframe.
      await page.evaluate(({origin,username,password,userSelector,passwordSelector})=>{
        if(location.origin!==origin)throw new Error('OriginChanged');
        const pick=s=>{const all=document.querySelectorAll(s);if(all.length!==1)throw new Error('AmbiguousLoginField');return all[0];};
        const pass=pick(passwordSelector),user=userSelector?pick(userSelector):null;
        if(pass.type!=='password'||pass.disabled||(user&&user.disabled))throw new Error('InvalidLoginFields');
        for(const [e,text] of [[user,username],[pass,password]]) {
          if(!e)continue;
          const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          setter.call(e,text);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));
        }
      },{...value,userSelector:step.username_selector?css(step.username_selector):'',passwordSelector:css(step.password_selector)});
      data={filled:true,submitted:false};
    }
    else if(step.op==='otp_fill') {
      const target=await field(page,step.selector);
      if(!target.origin.startsWith('https://'))throw new Error('HTTPSRequired');
      const since=step.after_mark?marks.get(step.after_mark):step.since;
      if(!Number.isFinite(since))throw new Error('ChallengeMarkMissing');
      const value=await secret('otp',page,{since});
      await page.evaluate(({origin,selector,code})=>{
        if(location.origin!==origin)throw new Error('OriginChanged');
        const fields=document.querySelectorAll(selector);
        if(fields.length!==1||fields[0].disabled||fields[0].tagName!=='INPUT')throw new Error('InvalidCodeField');
        const e=fields[0];Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,code);
        e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));
      },{...value,selector:css(step.selector)});
      data={filled:true,submitted:false};
    }
    else if(step.op==='fill') {if((await field(page,step.selector)).type==='password')throw new Error('UseSavedLogin');await page.fill(step.selector,step.value);}
    else if(step.op==='select') {await page.selectOption(step.selector,step.label?{label:step.label}:step.value);}
    else if(step.op==='click') {await page.click(step.selector,{label:step.label||'Run requested web step'});}
    else if(step.op==='press') {await page.press(step.selector,step.value);}
    else if(step.op==='wait_url') {await page.waitForURL(step.url,{timeout});data.url=safeURL(await page.url());data.verified=true;}
    else if(step.op==='wait_selector') {await page.waitForSelector(step.selector,{state:step.state||'visible',timeout});}
    else if(step.op==='wait_any') {
      const selectors=step.selectors.map(css);
      await page.waitForFunction(items=>items.some(s=>[...document.querySelectorAll(s)].some(e=>{const r=e.getBoundingClientRect();return r.width&&r.height;})),selectors,{timeout});
      data.selector=await page.evaluate(items=>items.find(s=>[...document.querySelectorAll(s)].some(e=>{const r=e.getBoundingClientRect();return r.width&&r.height;}))||null,selectors);
    }
    else if(step.op==='read'||step.op==='expect') {
      if(step.state==='hidden'||step.state==='detached') {
        await page.waitForSelector(step.selector,{state:step.state,timeout});data={verified:true,state:step.state};
      } else {
        await page.waitForSelector(step.selector,{state:'visible',timeout});
        data=await page.evaluate(selector=>{
          const nodes=document.querySelectorAll(selector);if(nodes.length!==1)throw new Error('AmbiguousRead');
          const e=nodes[0];if(e.type==='password')throw new Error('PasswordReadDenied');
          const text=('value' in e?e.value:e.textContent||'').trim();return {text:text.slice(0,4000),truncated:text.length>4000};
        },css(step.selector));
        if(step.equals!==undefined&&data.text!==step.equals)throw new Error('ExpectedValueMismatch');
        if(step.contains!==undefined&&!data.text.includes(step.contains))throw new Error('ExpectedTextMissing');
        if(step.op==='expect')data.verified=true;
      }
    }
    const row={index,op:step.op,page:page.label,elapsed_ms:Date.now()-started,state:'done',...data};
    result.steps.push(row);send('STEP',row);
    if(data.verified)result.verified=true;
  }
  result.pages=(await task.pages()).map(p=>({page:p.label}));
  if(plan.finish){await task.finish({keep:plan.keep});result.finished=true;}
  result.state='completed';
} catch(error) {
  result.state='error';result.failed_step=index;
  // Site errors may echo input values. Return only the exception category.
  result.error_type=String(error?.name||'EgoError').slice(0,80);
  if(/^[A-Z][A-Za-z0-9_]{0,63}$/.test(error?.message||''))result.error_code=error.message;
} finally {send('RESULT',result);}
