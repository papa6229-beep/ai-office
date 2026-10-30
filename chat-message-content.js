/* Safe inline chat content, shared by the main and project conversations. */
(() => {
  'use strict';
  const sheet=document.createElement('link');sheet.rel='stylesheet';sheet.href='/chat-message-content.css';document.head.append(sheet);
  const imageExtension=/\.(?:png|jpe?g|gif|webp|ico|bmp)$/i;
  const videoExtension=/\.(?:mp4|m4v|webm|ogv|mov)$/i;
  function url(value,image=false){
    if(typeof value!=='string'||/[\x00-\x20\x7f]/.test(value))return '';
    try{
      const u=new URL(value,location.href);
      if(!['http:','https:'].includes(u.protocol)||u.username||u.password)return '';
      if(image&&u.origin===location.origin){
        const reference=u.pathname==='/api/work/project-panel-file'&&/^[a-f0-9]{32}$/.test(u.searchParams.get('ref')||'');
        const media=['/api/work/attachment','/api/work/agent-artifact','/api/work/media'].includes(u.pathname)&&/^[a-f0-9]{32}$/.test(u.searchParams.get('id')||'');
        if(!reference&&!media)return '';
      }
      if(image&&/\.(?:html?|xhtml)$/i.test(u.pathname))return '';
      return u.href;
    }catch{return '';}
  }
  function fallback(text){
    // Older servers/streaming drafts can still show web links; local files
    // require server-issued parts, never a client-provided filesystem path.
    const result=[];
    String(text||'').split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g).forEach((piece,index)=>{
      if(index%2){result.push({kind:'code',text:piece});return;}
      const regex=/(!?)\[([^\]\n]{1,500})\]\((https?:\/\/[^\s<>]+)\)|(https?:\/\/[^\s<>`]+)/g;
      let match,pos=0;
      while((match=regex.exec(piece))){
        result.push({kind:'text',text:piece.slice(pos,match.index)});
        let href=match[3]||match[4],tail='';
        if(match[4]){href=href.replace(/[.,;!?]+$/,'');while(href.endsWith(')')&&href.split(')').length>href.split('(').length)href=href.slice(0,-1);tail=match[4].slice(href.length);}
        if(url(href))result.push({kind:'link',text:match[2]||href,href,image:!!match[1]});
        else result.push({kind:'text',text:match[0]});
        if(tail)result.push({kind:'text',text:tail});pos=regex.lastIndex;
      }
      result.push({kind:'text',text:piece.slice(pos)});
    });
    return result;
  }
  function imageButton(href,title,kind='image'){
    const source=url(href,true);if(!source)return null;
    const button=document.createElement('button');button.type='button';button.className='office-chat-image';
    const video=kind==='video',name=title||(video?'영상':'이미지');
    if(video){button.dataset.officeVideo=source;button.classList.add('office-chat-video');}
    else button.dataset.officeImage=source;
    button.dataset.imageTitle=name;button.dataset.independentWork='';
    button.setAttribute('aria-label',name+(video?' 재생':' 확대 보기'));
    if(!video&&new URL(source).origin===location.origin){const image=document.createElement('img');image.src=source;image.alt='';image.loading='lazy';button.append(image);}
    const label=document.createElement('span');label.textContent=(video?'▶ ':'')+name+(video?' · 재생':' · 확대 보기');button.append(label);
    return button;
  }
  function render(container,message){
    if(!container)return;
    const parts=Array.isArray(message.parts)?message.parts:fallback(message.content??message.text);
    const signature=JSON.stringify(parts);if(container.dataset.chatContent===signature)return;
    container.dataset.chatContent=signature;const fragment=document.createDocumentFragment();
    for(const part of parts){
      if(!['link','file'].includes(part.kind)){fragment.append(document.createTextNode(part.text||''));continue;}
      const href=url(part.href);if(!href){fragment.append(document.createTextNode(part.text||''));continue;}
      const pathname=new URL(part.path||href,location.href).pathname;
      const isVideo=part.video||videoExtension.test(pathname);
      const isImage=!isVideo&&(part.image||imageExtension.test(pathname));
      const image=isVideo||isImage?imageButton(href,part.text,isVideo?'video':'image'):null;
      if(image)fragment.append(image);
      else{
        const link=document.createElement('a');link.className='office-chat-link project-message-link';link.textContent=part.text||href;
        link.href=href;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';link.title=part.path||href;
        link.addEventListener('click',event=>event.stopPropagation());fragment.append(link);
      }
      const download=part.kind==='file'&&url(part.download);
      if(download){const link=document.createElement('a');link.className='office-chat-download project-message-download';link.href=download;link.download='';link.textContent='↓';link.title='파일 다운로드';link.setAttribute('aria-label',(part.text||'파일')+' 다운로드');link.addEventListener('click',event=>event.stopPropagation());fragment.append(link);}
    }
    container.replaceChildren(fragment);
  }
  function enhanceAttachments(root){
    for(const image of root?.querySelectorAll('.work-attachment-chip>img')||[]){
      const button=imageButton(image.getAttribute('src'),image.closest('.work-attachment-chip').querySelector('b')?.textContent||'첨부 이미지');
      if(button)image.replaceWith(button);
    }
  }
  function attachment(container,file){
    if(file.kind!=='image'||!/^[a-f0-9]{32}$/.test(file.id||''))return;
    const button=imageButton('/api/work/attachment?id='+file.id,file.name||'첨부 이미지');if(button)container.append(button);
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('button[data-office-image],button[data-office-video]');if(!button)return;
    const video=!!button.dataset.officeVideo;
    event.preventDefault();event.stopPropagation();const source=url(button.dataset.officeVideo||button.dataset.officeImage,true);
    if(!source)return;
    if(typeof window.officeStageExpand!=='function'){button.title='미디어 보기를 준비하고 있어요. 잠시 후 다시 눌러주세요.';return;}
    window.officeStageExpand({kind:video?'video':'image',url:source,title:button.dataset.imageTitle||(video?'영상':'이미지'),origin:'chat_link'});
  });
  window.officeChatContent={render,enhanceAttachments,attachment};
})();
