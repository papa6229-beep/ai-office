// Disconnect the CDP client without closing the user's Electron application.
const fs=require('fs');
async function evaluate(url,expression){
  const ws=new WebSocket(url);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{ws.close();reject(Error());},15000);
    ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error());});
    ws.addEventListener('open',()=>ws.send(JSON.stringify({id:1,method:'Runtime.evaluate',params:{expression,returnByValue:true,awaitPromise:true}})));
    ws.addEventListener('message',event=>{const value=JSON.parse(event.data);if(value.id!==1)return;clearTimeout(timer);ws.close();if(value.error||value.result?.exceptionDetails)reject(Error());else resolve(value.result?.result?.value);});
  });
}
async function roomState(url,p){
  return evaluate(url,`(async()=>{
    if(!(await indexedDB.databases()).some(d=>d.name==='Talk2DB'))throw Error();
    const db=await new Promise((resolve,reject)=>{const q=indexedDB.open('Talk2DB');q.onsuccess=()=>resolve(q.result);q.onerror=reject;});
    try{return await new Promise((resolve,reject)=>{
      const q=db.transaction('Table_talk2','readonly').objectStore('Table_talk2').get('recoil-atomAllRooms');
      q.onerror=reject;q.onsuccess=()=>{const room=q.result?.data?.rooms?.[${JSON.stringify(p.roomKey)}];
        if(!room||room.leftRoom||!room.members?.[${JSON.stringify(p.userKey)}]){reject(Error());return;}
        resolve({unread:Number(room.unreadMessageServer||0),read:Number(room.iRead||0),
          lastTime:String(room.lastTime||''),lastNumber:String(room.lastMsgNumber||'')});};
    });}finally{db.close();}
  })()`);
}
(async()=>{
  const input=JSON.parse(fs.readFileSync(0,'utf8'));
  try {
    if(!Number.isInteger(input.port)||input.port<1024||input.port>65535)throw Error();
    const pages=await (await fetch(`http://127.0.0.1:${input.port}/json/list`,{signal:AbortSignal.timeout(5000)})).json();
    if(pages.some(p=>p.url.includes('unlock.html'))){console.log(JSON.stringify({ok:false,error:'한비로 잠금을 먼저 해제해 주세요.'}));return;}
    const candidates=pages.filter(p=>p.type==='page'&&p.url.replaceAll('\\','/').toLowerCase().includes('/hanbiro-talk/resources/app.asar/dist/renderer/index.html')&&p.url.includes('#/main'));
    if(candidates.length!==1)throw Error();
    const page=candidates[0];
    const ready=await evaluate(page.webSocketDebuggerUrl,'!!(window.electron?.ipcRenderer?.sendMessage&&window.electron?.messengerCenter?.checkSocketState())');
    if(!ready){console.log(JSON.stringify({ok:false,error:'한비로 로그인 연결을 기다려 주세요.'}));return;}
    if(input.action==='read_preflight'||input.action==='read_room'){
      const p=input.payload;
      if(!p||!/^\w{16,64}$/.test(p.roomKey||'')||typeof p.userKey!=='string'||!p.userKey)throw Error();
      const before=await roomState(page.webSocketDebuggerUrl,p);
      if(input.action==='read_preflight'){console.log(JSON.stringify({ok:true,state:'observed',...before}));return;}
      if(!Number.isFinite(p.through)||before.lastNumber!==p.lastNumber||before.lastTime!==p.lastTime||Number(before.lastTime)/1000>p.through+.002){console.log(JSON.stringify({ok:true,state:'newer_messages'}));return;}
      if(before.unread===0&&before.read===1){console.log(JSON.stringify({ok:true,state:'confirmed',before:0,after:0}));return;}
      await evaluate(page.webSocketDebuggerUrl,`window.electron.ipcRenderer.sendMessage('SEND_SOCKET_API_EVENT',4,${JSON.stringify({roomKey:p.roomKey,userKey:p.userKey})})`);
      const until=Date.now()+4000;
      while(Date.now()<until){
        const after=await roomState(page.webSocketDebuggerUrl,p);
        if(after.lastNumber!==before.lastNumber){console.log(JSON.stringify({ok:true,state:'newer_messages'}));return;}
        if(after.unread===0&&after.read===1){console.log(JSON.stringify({ok:true,state:'confirmed',before:before.unread,after:0}));return;}
        await new Promise(resolve=>setTimeout(resolve,250));
      }
      console.log(JSON.stringify({ok:true,state:'requested'}));return;
    }else if(input.action==='send'){
      await evaluate(page.webSocketDebuggerUrl,`window.electron.ipcRenderer.sendMessage('SEND_SOCKET_API_EVENT',38,${JSON.stringify(input.payload)})`);
    }else if(input.action==='send_text'){
      const p=input.payload;
      if(!p||typeof p.roomKey!=='string'||typeof p.userKey!=='string'||typeof p.clientId!=='string'||typeof p.message!=='string'||!p.message.trim()||p.message.length>2000)throw Error();
      await evaluate(page.webSocketDebuggerUrl,`window.electron.ipcRenderer.sendMessage('SEND_SOCKET_API_EVENT',5,${JSON.stringify(p)})`);
    }else if(input.action!=='preflight')throw Error();
    console.log(JSON.stringify({ok:true,action:input.action}));
  }catch{console.log(JSON.stringify({ok:false,error:'한비로 기본 창과 전송 연결을 확인해 주세요.'}));}
})().finally(()=>{process.exitCode=0;});
