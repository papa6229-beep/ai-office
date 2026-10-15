const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the shipped work surface, with HTTP and DOM boundaries replaced.
async function surface() {
  const elements = new Map(), ready = [], requests = [];
  function element(id) {
    if (!elements.has(id)) {
      const listeners = new Map();
      elements.set(id, {
        id, value: '', disabled: false, open: false,
        scrollHeight: 0, scrollTop: 0, clientHeight: 0,
        classList: {toggle() {}},
        addEventListener(name, fn) { listeners.set(name, fn); },
        fire(name, event = {}) { listeners.get(name)?.(event); },
        querySelectorAll() { return id === 'work' ? [element('workCommand'), element('workSend')] : []; },
        closest() { return null; }, matches() { return false; },
        requestSubmit() { element('work').fire('submit', {target: this, preventDefault() {}}); }
      });
    }
    return elements.get(id);
  }
  const overview = {action_token: 'test-token', tasks: [], messages: [], counts: {}};
  let release;
  const context = {
    document: {getElementById: element, addEventListener(name, fn) { if (name === 'DOMContentLoaded') ready.push(fn); }},
    window: {}, Intl, Date, Map, Uint8Array,
    crypto: {getRandomValues(bytes) { bytes.fill(3); return bytes; }},
    fetch: async (url, options) => {
      if (options?.method === 'POST') {
        requests.push(JSON.parse(options.body));
        await new Promise(resolve => { release = resolve; });
      }
      return {ok: true, status: 200, json: async () => overview};
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'workdesk.js'), 'utf8'), context);
  ready[0]();
  await new Promise(setImmediate);
  return {
    input: element('workCommand'), requests,
    key(options = {}) {
      const event = {key: 'Enter', preventDefault() { this.defaultPrevented = true; }, ...options};
      element('workCommand').fire('keydown', event);
      return event;
    },
    async finish() { release?.(); await new Promise(setImmediate); }
  };
}

test('Enter sends through the existing form once; processing blocks repeats', async () => {
  const s = await surface(); s.input.value = '챗지피티 앱을 바로가기에 추가해줘';
  assert.equal(s.key().defaultPrevented, true);
  assert.equal(s.requests.length, 1);
  assert.equal(s.requests[0].message, s.input.value);
  assert.match(s.requests[0].request_id, /^[a-f0-9]{32}$/);
  s.key(); assert.equal(s.requests.length, 1);
  await s.finish(); assert.equal(s.input.value, '');
});

test('Shift+Enter keeps native newline behavior; modified Enter does not send', async () => {
  const s = await surface(); s.input.value = '두 줄로 요청';
  for (const modifier of ['shiftKey', 'ctrlKey', 'altKey', 'metaKey']) {
    assert.equal(s.key({[modifier]: true}).defaultPrevented, undefined);
  }
  assert.equal(s.requests.length, 0);
});

test('Korean composition Enter is not sent, including Windows keyCode 229', async () => {
  const s = await surface(); s.input.value = '한글 입력 중';
  s.input.fire('compositionstart'); s.key();
  assert.equal(s.requests.length, 0);
  s.input.fire('compositionend');
  s.key({isComposing: true}); s.key({keyCode: 229});
  assert.equal(s.requests.length, 0);
  s.key(); assert.equal(s.requests.length, 1);
  await s.finish();
});

test('Empty input, held Enter, other keys and a disabled input never submit', async () => {
  const s = await surface(); s.input.value = '  '; s.key();
  s.input.value = '요청'; s.key({repeat: true}); s.key({key: 'a'});
  s.input.disabled = true; s.key();
  assert.equal(s.requests.length, 0);
});
