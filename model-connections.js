/* Readiness never generates a response or starts/loads a local model. */
(() => {
  let pending = false;
  const panel = document.getElementById('modelConnections');
  if (!panel) return;
  const safe = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const message = text => { document.getElementById('modelConnectionStatus').textContent = text; };
  async function request(path, data) {
    if (data === undefined) {
      const response = await fetch('/api/work/' + path, {cache:'no-store'});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || '연결 상태를 불러오지 못했어요.');
      return result;
    }
    const session = await (await fetch('/api/work', {cache:'no-store'})).json();
    const response = await fetch('/api/work/' + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...data, token:session.action_token})});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '연결 요청을 처리하지 못했어요.');
    return result;
  }
  function render(data) {
    document.getElementById('modelProviderCards').innerHTML = data.providers.map(provider => `
      <article class="model-provider ${provider.active ? 'is-active' : ''}">
        <div class="model-provider-heading"><h4>${safe(provider.title)}</h4><span class="pill">${provider.active ? '현재 사용 중' : provider.selectable ? '전환 가능' : '연결 준비 중'}</span></div>
        <p class="model-provider-billing">${safe(provider.billing)}</p>
        <p class="model-provider-readiness">${safe(provider.readiness.text)}</p>
        <dl>${provider.capabilities.map(([label, value]) => `<div><dt>${safe(label)}</dt><dd>${safe(value)}</dd></div>`).join('')}</dl>
        ${provider.id === 'local' ? `<p class="model-provider-note">선택 모델: ${safe(data.local_model)}</p>` : ''}
        <p class="model-provider-note">${safe(provider.note)}</p>
        ${provider.login_available ? `<button type="button" data-provider-login="${safe(provider.id)}" ${pending ? 'disabled' : ''}>계정 로그인</button>` : ''}
        <button type="button" data-provider="${safe(provider.id)}" ${!provider.selectable || provider.active || pending ? 'disabled' : ''}>${provider.active ? '사용 중' : provider.selectable ? '이 연결 사용' : '어댑터 연결 필요'}</button>
      </article>`).join('');
    document.getElementById('modelReadinessRefresh').disabled = pending;
    document.getElementById('modelCheckedAt').textContent = data.checked_at ? '준비 상태 확인: ' + new Date(data.checked_at).toLocaleString('ko-KR') : '아직 준비 상태를 확인하지 않았어요.';
  }
  async function load() {
    if (pending) return;
    try { render(await request('model-connections')); }
    catch (error) { message(error.message); }
  }
  document.getElementById('modelReadinessRefresh').addEventListener('click', async () => {
    if (pending) return;
    pending = true;
    document.getElementById('modelReadinessRefresh').disabled = true;
    panel.querySelectorAll('[data-provider]').forEach(button => { button.disabled = true; });
    message('설치·로그인·서버 응답을 확인하고 있어요. 모델은 호출하지 않아요.');
    try {
      const data = await request('model-connections-refresh', {});
      pending = false; render(data);
      message('준비 상태를 확인했어요. 실제 답변·도구 실행·남은 한도는 별도 확인이 필요해요.');
    } catch (error) { pending = false; await load(); message(error.message); }
  });
  panel.addEventListener('click', async event => {
    const login=event.target.closest('[data-provider-login]');
    if(login&&!pending){
      login.disabled=true;
      try{const result=await request('model-login',{provider:login.dataset.providerLogin});message(result.message);}
      catch(error){message(error.message);}finally{login.disabled=false;}
      return;
    }
    const button = event.target.closest('[data-provider]');
    if (!button || button.disabled || pending) return;
    pending = true;
    panel.querySelectorAll('button').forEach(item => { item.disabled = true; });
    message('연결 설정을 전환하고 있어요.');
    try {
      const data = await request('model-select', {provider:button.dataset.provider});
      pending = false; render(data);
      document.getElementById('api_key').value = '';
      await refresh();
      message('전환했어요. 다음 요청부터 선택한 연결을 사용해요. 실제 모델 응답은 아직 확인하지 않았어요.');
    } catch (error) { pending = false; await load(); message(error.message); }
  });
  window.modelConnectionsLoad = load;
})();
