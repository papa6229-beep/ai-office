(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const clock = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Seoul', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'});
  let lastWeather = 0;
  let loading = false;
  function tick() {
    const node = byId('workAmbientTime');
    if (!node || document.hidden) return;
    const now = new Date();
    node.textContent = clock.format(now);
    node.dateTime = now.toISOString();
    node.setAttribute('aria-label', '한국 시각 '+node.textContent);
  }
  function conditions(code, day) {
    if (code === 0) return [day ? '☀' : '☾', day ? '맑음' : '맑은 밤'];
    if (code <= 2) return [day ? '⛅' : '☾', '구름 조금'];
    if (code === 3) return ['☁', '흐림'];
    if ([45,48].includes(code)) return ['≋', '안개'];
    if ([51,53,55,56,57].includes(code)) return ['☂', '이슬비'];
    if ([61,63,65,66,67,80,81,82].includes(code)) return ['☂', '비'];
    if ([71,73,75,77,85,86].includes(code)) return ['❄', '눈'];
    if ([95,96,99].includes(code)) return ['ϟ', '뇌우'];
    return ['·', '날씨 정보'];
  }
  function footer(text) {
    const node = byId('workAmbientUpdated');
    node.replaceChildren(document.createTextNode(text+' · '));
    const source = document.createElement('a');
    source.href = 'https://open-meteo.com/';
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    source.textContent = 'Open-Meteo';
    node.appendChild(source);
  }
  async function weather() {
    if (!byId('workAmbient') || document.hidden || loading || Date.now()-lastWeather < 600000) return;
    loading = true;
    const control = new AbortController();
    const timeout = setTimeout(() => control.abort(), 8000);
    try {
      const response = await fetch('/api/work/weather', {signal:control.signal, cache:'no-store'});
      if (!response.ok) throw Error('Weather unavailable');
      const data = await response.json();
      if (!['ok','stale'].includes(data.status) || !Number.isFinite(data.temperature) || !Number.isInteger(data.weather_code) || typeof data.is_day !== 'boolean') throw Error('No weather measurement');
      const [glyph, label] = conditions(data.weather_code, data.is_day);
      byId('workAmbientTemperature').textContent = Math.round(data.temperature)+'°';
      byId('workAmbientWeatherIcon').textContent = glyph;
      byId('workAmbientWeather').textContent = '의정부 · '+label;
      const observed = typeof data.observed_at === 'string' ? data.observed_at.match(/T(\d{2}:\d{2})/) : null;
      footer((observed ? observed[1]+' 기준' : '날씨 정보')+(data.status === 'stale' ? ' · 이전 정보' : ''));
      byId('workAmbient').title = Number.isFinite(data.apparent_temperature) ? '체감 온도 '+Math.round(data.apparent_temperature)+'°C' : '';
      byId('workAmbient').dataset.weatherStatus = data.status;
    } catch (_) {
      const previous = byId('workAmbient').dataset.weatherStatus;
      if (previous === 'ok' || previous === 'stale') {
        footer('이전 정보 · 연결 대기');
        byId('workAmbient').dataset.weatherStatus = 'stale';
      } else {
        byId('workAmbientTemperature').textContent = '--°';
        byId('workAmbientWeatherIcon').textContent = '·';
        byId('workAmbientWeather').textContent = '의정부 · 연결 대기';
        footer('날씨 수신 대기');
      }
    } finally {
      clearTimeout(timeout);
      // A failed initial connection retries after one minute; fresh weather
      // stays on the normal ten-minute refresh interval.
      lastWeather = Date.now() - (byId('workAmbient').dataset.weatherStatus === 'ok' ? 0 : 540000);
      loading = false;
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    tick(); weather();
    setInterval(tick, 1000);
    setInterval(weather, 60000);
    document.addEventListener('visibilitychange', () => {tick(); weather();});
  });
})();
