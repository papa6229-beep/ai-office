/* Direct, selected incoming attachments. No native-window automation. */
(() => {
  'use strict';
  const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  let busy=false,timer=null,offset=0;
  async function api(query='',body){
    let options={cache:'no-store'};
    if(body){const work=await(await fetch('/api/work',{cache:'no-store'})).json();options={...options,method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,token:work.action_token})};}
    const response=await fetch('/api/work/messenger-attachments'+query,options),data=await response.json();
    if(!response.ok||data.error)throw Error(data.error||'첨부를 확인하지 못했어요.');
    return data;
  }
  function init(){
    const pane=document.getElementById('receivedNote')?.parentElement;if(!pane)return;
    const open=make('button','메신저 첨부 받기');open.type='button';open.className='messenger-attachment-open';pane.prepend(open);
    const dialog=make('dialog');dialog.className='received-dialog messenger-attachment-dialog';
    const header=make('header'),title=make('h3','메신저 첨부 받기'),close=make('button','닫기');close.type='button';header.append(title,close);
    const info=make('p','한비로 첨부를 화면 전환 없이 받아요. 내려받은 파일은 받은파일 목록에서 정리할 수 있어요.');
    const limitation=make('p','네이트온은 PC에 저장된 파일 정리를 지원해요. 대화방 첨부 직접 받기는 아직 연결되지 않았어요.');
    const form=make('form'),label=make('label','한비로 대화방 이름 또는 파일명'),search=make('input'),submit=make('button','첨부 찾기');
    search.required=true;search.maxLength=200;search.placeholder='예: 안용훈';submit.type='submit';label.append(search);form.append(label,submit);
    const note=make('p'),list=make('div'),pager=make('div'),previous=make('button','이전'),next=make('button','다음');
    previous.type=next.type='button';pager.append(previous,next);pager.hidden=true;
    dialog.append(header,info,limitation,form,note,list,pager);document.body.append(dialog);
    close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>{clearTimeout(timer);timer=null;});
    async function watch(id,statusNode,button){
      try{
        const job=await api('?job='+encodeURIComponent(id));
        const stop=statusNode.nextElementSibling;
        if(job.state==='downloaded'){if(stop?.dataset.job===id)stop.remove();statusNode.textContent='저장 완료 · '+job.path;button.disabled=true;button.textContent='저장 완료';return;}
        if(['error','interrupted','cancelled','file_unavailable'].includes(job.state)){if(stop?.dataset.job===id)stop.remove();statusNode.textContent=job.error||'중단됐어요. 현재 상태를 확인한 뒤 다시 요청해 주세요.';button.disabled=false;button.textContent='다시 받기';return;}
        statusNode.textContent=job.state==='cancel_requested'?'중지 요청 중':'백그라운드에서 받는 중';
        let cancel=statusNode.nextElementSibling;
        if(!cancel||cancel.dataset.job!==id){cancel=make('button','받기 중지');cancel.type='button';cancel.dataset.job=id;statusNode.after(cancel);cancel.onclick=async()=>{cancel.disabled=true;try{await api('',{action:'cancel',id});statusNode.textContent='중지 요청 중';}catch(error){statusNode.textContent=error.message;cancel.disabled=false;}};}
        if(dialog.open)timer=setTimeout(()=>watch(id,statusNode,button),1500);
      }catch(error){statusNode.textContent=error.message;button.disabled=false;}
    }
    async function load(){
      if(busy)return;busy=true;submit.disabled=true;clearTimeout(timer);
      try{
        note.textContent='한비로의 현재 첨부 목록을 확인하고 있어요.';
        const data=await api('?source=hanbiro&q='+encodeURIComponent(search.value)+'&offset='+offset);
        list.replaceChildren();note.textContent=data.total+'개 · 현재 PC 앱에 저장된 대화 이력 기준';pager.hidden=data.total<=60;previous.disabled=offset===0;next.disabled=offset+60>=data.total;
        for(const item of data.items){
          const card=make('section'),name=make('strong',item.name),details=make('p',item.room_title+' · '+Math.ceil(item.size/1024)+' KB'),button=make('button','백그라운드로 받기'),statusNode=make('p');button.type='button';card.append(name,details,button,statusNode);list.append(card);
          button.onclick=async()=>{button.disabled=true;try{const job=await api('',{action:'download',ref:item.ref});await watch(job.id,statusNode,button);}catch(error){statusNode.textContent=error.message;button.disabled=false;}};
        }
      }catch(error){note.textContent=error.message;}finally{busy=false;submit.disabled=false;}
    }
    form.onsubmit=event=>{event.preventDefault();offset=0;load();};previous.onclick=()=>{offset=Math.max(0,offset-60);load();};next.onclick=()=>{offset+=60;load();};
    open.onclick=()=>{dialog.showModal();search.focus();};
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
