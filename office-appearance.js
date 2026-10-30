/* One shared UI, two selectable skins. No AI call or backend preference write. */
(() => {
  'use strict';
  const key='aemeath.appearance.theme.v1';
  const themes={console:'기존 콘솔',silver:'실버 레트로'};
  let current='console';
  try{const saved=localStorage.getItem(key);if(Object.hasOwn(themes,saved))current=saved;}catch{/* Default theme works without browser storage. */}
  document.documentElement.dataset.officeTheme=current;

  function render(){
    document.querySelectorAll('[data-office-theme-option]').forEach(button=>{
      const active=button.dataset.officeThemeOption===current;
      button.setAttribute('aria-checked',String(active));button.tabIndex=active?0:-1;
      const state=button.querySelector('.appearance-choice-state');if(state)state.textContent=active?'사용 중':'선택';
    });
  }
  function select(theme,{persist=true,announce=true}={}){
    if(!Object.hasOwn(themes,theme))return false;
    current=theme;document.documentElement.dataset.officeTheme=theme;
    let saved=true;
    if(persist)try{localStorage.setItem(key,theme);}catch{saved=false;}
    render();
    const status=document.getElementById('officeAppearanceStatus');
    if(status&&announce)status.textContent=themes[theme]+' 테마를 적용했어요.'+(persist&&!saved?' 이번 화면에만 적용돼요.':'');
    document.dispatchEvent(new CustomEvent('office-theme-change',{detail:{theme}}));
    return true;
  }
  window.officeAppearance={get current(){return current;},select};
  function mount(){
    const settings=document.getElementById('aemeath-settings');
    if(!settings||document.getElementById('officeAppearance'))return;
    const section=document.createElement('article');section.className='card office-appearance';section.id='officeAppearance';
    section.innerHTML=`<div class="appearance-heading"><div><h2>화면 테마</h2><p>에이메스의 옷을 골라주세요. 선택한 테마는 다음 실행에도 유지돼요.</p></div><span class="appearance-tag">APPEARANCE</span></div>
      <div class="appearance-choices" role="radiogroup" aria-label="화면 테마 선택">
        <button type="button" class="appearance-choice" data-office-theme-option="console" role="radio" aria-checked="false">
          <span class="appearance-sample appearance-sample-console" aria-hidden="true"><span class="appearance-sample-side"></span><span class="appearance-sample-main"><b>AI OFFICE</b><span class="appearance-sample-grid"><i></i><i></i><i></i></span><em></em></span></span>
          <span class="appearance-choice-heading"><strong>기존 콘솔</strong><span class="appearance-choice-state"></span></span><span class="appearance-choice-description">차콜 · 라임 · 익숙한 작업 공간</span>
        </button>
        <button type="button" class="appearance-choice" data-office-theme-option="silver" role="radio" aria-checked="false">
          <span class="appearance-sample appearance-sample-silver" aria-hidden="true"><span class="appearance-sample-side"></span><span class="appearance-sample-main"><b>AI OFFICE</b><span class="appearance-sample-grid"><i></i><i></i><i></i></span><em></em></span></span>
          <span class="appearance-choice-heading"><strong>실버 레트로</strong><span class="appearance-choice-state"></span></span><span class="appearance-choice-description">실버 그레이 · 오렌지 · 픽셀 타이포</span>
        </button>
      </div><p id="officeAppearanceStatus" class="appearance-status" role="status" aria-live="polite">화면 배치·업무·캐릭터 설정은 그대로 사용할 수 있어요.</p>`;
    settings.prepend(section);
    section.addEventListener('click',event=>{const button=event.target.closest('[data-office-theme-option]');if(button)select(button.dataset.officeThemeOption);});
    section.addEventListener('keydown',event=>{
      if(!event.target.matches('[data-office-theme-option]')||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();
      const options=[...section.querySelectorAll('[data-office-theme-option]')],index=options.indexOf(event.target);
      const next=event.key==='Home'?0:event.key==='End'?options.length-1:(index+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)+options.length)%options.length;
      select(options[next].dataset.officeThemeOption);options[next].focus();
    });
    render();
  }
  window.addEventListener('storage',event=>{if(event.key===key||event.key===null)select(Object.hasOwn(themes,event.newValue)?event.newValue:'console',{persist:false,announce:false});});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
