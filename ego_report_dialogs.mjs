// Read protocol events, never renderer metadata while a synchronous dialog opens.
// Retain only dialog text and POST status; discard headers, cookies and bodies.
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export const dialogInterrupted=error=>error?.name==='PageDialogOpenedError';
export async function beginObservation(page,origin,paths=['/adminx/goods.php','/api/data']){
 await page.cdp('Page.enable');await page.cdp('Network.enable');await page.events();
 return {origin,paths,dialog:null,posts:new Set(),postSeen:false,postOk:false,postFinished:false};
}
export async function observe(page,state){
 for(const event of await page.events()){
  const p=event.params||{};
  if(event.method==='Page.javascriptDialogOpening')state.dialog={type:p.type,message:p.message};
  if(event.method==='Page.javascriptDialogClosed')state.dialog=null;
  if(event.method==='Network.requestWillBeSent'&&p.request?.method==='POST'){
   const url=new URL(p.request.url);
   if(url.origin===state.origin&&state.paths.includes(url.pathname)){state.posts.add(p.requestId);state.postSeen=true;}
  }
  if(event.method==='Network.responseReceived'&&state.posts.has(p.requestId)){
   state.postOk=p.response.status>=200&&p.response.status<300;
   if(!state.postOk)throw Error('report_save_response_'+p.response.status);
  }
  if(event.method==='Network.loadingFinished'&&state.posts.has(p.requestId))state.postFinished=true;
  if(event.method==='Network.loadingFailed'&&state.posts.has(p.requestId))throw Error('report_save_network_failed');
 }
 return state;
}
export async function acceptObservedDialog(page,promptText){
 const params={accept:true};if(promptText!==undefined)params.promptText=promptText;
 await page.cdp('Page.handleJavaScriptDialog',params);
}
export async function saveProductWithDialogs(page,trigger,{timeoutMs=15000,pollMs=25}={}){
 const state=await beginObservation(page,'https://banana.bizhost.kr');
 try{await trigger();}catch(error){if(!dialogInterrupted(error))throw error;}
 const deadline=Date.now()+timeoutMs;let confirmed=false,completed=false,handled=0;
 while(Date.now()<deadline){
  await observe(page,state);
  const d=state.dialog;
  if(d){
   const message=String(d.message).replace(/\s+/g,'');
   const confirm=/^(?:상품(?:정보)?(?:를|을)?)?(?:수정|변경|저장)하시겠습니까[?？]?$/;
   const done=/^(?:정상적으로)?(?:상품(?:정보)?(?:를|가|이)?)?(?:수정|변경|저장)(?:이|가)?(?:완료되었습니다|완료하였습니다|되었습니다|하였습니다)[.!]?$/;
   if(d.type==='confirm'&&!confirmed&&confirm.test(message)){
    await acceptObservedDialog(page);confirmed=true;handled++;state.dialog=null;
   }else if(d.type==='alert'&&confirmed&&done.test(message)){
    await acceptObservedDialog(page);completed=true;handled++;state.dialog=null;
   }else{
    await page.cdp('Page.handleJavaScriptDialog',{accept:false});
    throw Error('unexpected_product_save_dialog');
   }
  }
  if(confirmed&&completed&&state.postSeen&&state.postOk)
   return {save_completed:true,product_post_seen:true,product_post_ok:true,dialogs_handled:handled};
  await pause(pollMs);
 }
 throw Error(!confirmed?'product_save_confirmation_missing':!state.postSeen?'product_save_post_missing':'product_save_completion_missing');
}
export async function saveEventWithDialogs(page,trigger){
 const state=await beginObservation(page,'https://banana.bizhost.kr',['/adminx/ajaxmo/']);
 try{await trigger();}catch(error){if(!dialogInterrupted(error))throw error;}
 const deadline=Date.now()+20000;
 let confirmed=false;
 while(Date.now()<deadline){
  await observe(page,state);
  const dialog=state.dialog;
  if(dialog){
   if(!confirmed&&dialog.type==='confirm'&&dialog.message==='저장하시겠습니까? (※연결사이트로 즉시반영됩니다.)'){
    await acceptObservedDialog(page);
    confirmed=true;
   }else if(confirmed&&dialog.type==='alert'&&state.postSeen){
    await acceptObservedDialog(page);
   }else{
    await page.cdp('Page.handleJavaScriptDialog',{accept:false});
    throw Error('unexpected_event_save_dialog');
   }
   state.dialog=null;
  }
  if(confirmed&&state.postOk&&state.postFinished)return true;
  await pause(50);
 }
 throw Error('event_save_response_missing');
}
export async function beginAiSave(page,trigger,{timeoutMs=15000,pollMs=25}={}){
 const state=await beginObservation(page,'https://dailywork-phi.vercel.app');
 try{const receipt=await trigger();if(receipt?.dialog)state.dialog=receipt.dialog;}
 catch(error){if(!dialogInterrupted(error))throw error;}
 const deadline=Date.now()+timeoutMs;
 while(Date.now()<deadline){
  await observe(page,state);
  if(state.dialog||state.postSeen)return state;
  await pause(pollMs);
 }
 throw Error('ai_report_save_not_started');
}
export async function waitAiCommit(page,state,{timeoutMs=25000,pollMs=25}={}){
 const deadline=Date.now()+timeoutMs;
 while(Date.now()<deadline){
  await observe(page,state);
  if(state.dialog)throw Error('unexpected_ai_report_dialog');
  if(state.postSeen&&state.postOk&&state.postFinished)return;
  await pause(pollMs);
 }
 throw Error('ai_report_save_response_missing');
}
