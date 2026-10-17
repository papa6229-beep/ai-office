"""A private headed Chrome workspace; every Playwright handle belongs to one thread."""
import atexit
import base64
import datetime
import ipaddress
import os
import pathlib
import queue
import re
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit

from content_analysis import SECRET_ASSIGNMENT, SECRET_XML

RUNTIME = pathlib.Path(__file__).resolve().parent / 'browser_runtime'
MAX_TEXT = 60000
MAX_EXCERPT = 12000
_FAILURE = '브라우저 작업을 완료하지 못했습니다. 창과 연결 상태를 확인하세요.'


def _redact(text):
    text = SECRET_ASSIGNMENT.sub(r'\1[가림]', str(text))
    text = SECRET_XML.sub(r'\1[가림]\2', text)
    text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [가림]', text)
    return re.sub(r'(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)', '[비밀 키 가림]', text)


def _profile_path(path):
    path = pathlib.Path(path).absolute()
    local = pathlib.Path(os.environ.get('LOCALAPPDATA', str(pathlib.Path.home() / 'AppData' / 'Local')))
    for user_data in (local / 'Google' / 'Chrome' / 'User Data', local / 'Microsoft' / 'Edge' / 'User Data'):
        try:
            shared = os.path.commonpath([os.path.normcase(str(path)), os.path.normcase(str(user_data.absolute()))])
        except ValueError:  # Dedicated profiles may be on a different local drive.
            continue
        if shared == os.path.normcase(str(user_data.absolute())):
            raise BrowserWorkspaceError('기존 사용자 브라우저 프로필은 사용하지 않습니다.')
    try:
        from desktop_workspace import _linked
        for part in (path, *path.parents):
            if part.exists() and _linked(part):
                raise BrowserWorkspaceError('연결 폴더는 브라우저 프로필로 사용하지 않습니다.')
    except OSError:
        raise BrowserWorkspaceError('전용 브라우저 저장 위치를 확인할 수 없습니다.') from None
    return path


class BrowserWorkspaceError(ValueError):
    pass


def _is_target_closed_error(error):
    # Optional, pinned Playwright dependency. Never classify a timeout or a
    # navigation error as safe to replay just by matching its message text.
    try:
        from playwright._impl._errors import is_target_closed_error
    except ImportError:
        return False
    return is_target_closed_error(error)


def validate_url(url):
    try:
        if not isinstance(url, str) or not url or len(url) > 4096 or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
            raise ValueError()
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port in {8765, 11434} or host in {'metadata.google.internal', 'metadata.azure.internal'}:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r'[0-9.]+', host) or host.startswith('0x'):
                raise ValueError()
            encoded = host.encode('idna').decode('ascii')
            if len(encoded) > 253 or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part) for part in encoded.split('.')):
                raise ValueError()
        else:
            if address.is_link_local or address.is_unspecified or address.is_multicast:
                raise ValueError()
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped and address.ipv4_mapped.is_link_local:
                raise ValueError()
        return url
    except (ValueError, UnicodeError):
        raise BrowserWorkspaceError('사용할 수 있는 HTTP(S) 사이트 주소가 필요합니다.') from None


def public_url(url):
    try:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc.split('@')[-1], parsed.path, '', ''))[:4096]
    except ValueError:
        return ''


def read_excerpt(text, query='', offset=0):
    text = _redact(str(text)[:MAX_TEXT])
    if not isinstance(query, str) or len(query) > 500:
        raise BrowserWorkspaceError('검색어는 500자 이내로 입력하세요.')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > MAX_TEXT:
        raise BrowserWorkspaceError('읽기 위치를 확인하세요.')
    query = query.strip()
    # Case folding can expand a character (e.g. ß -> ss). Map matches back
    # to the original text rather than using expanded indices as offsets.
    folded_parts, positions = [], []
    folded_offset = 0
    for position, character in enumerate(text):
        folded_character = character.casefold()
        folded_parts.append(folded_character)
        positions.extend([position] * len(folded_character))
        if position < offset:
            folded_offset += len(folded_character)
    folded = ''.join(folded_parts)
    count = folded.count(query.casefold()) if query else 0
    # Query checks all bounded rendered text, not just the first preview.
    start = min(offset, len(text))
    if query:
        found = folded.find(query.casefold(), folded_offset)
        start = max(0, positions[found] - 600) if found >= 0 else start
    excerpt = text[start:start + MAX_EXCERPT]
    return {'text': excerpt, 'query': query, 'match_count': count,
            'offset': start, 'next_offset': start + len(excerpt),
            'total_characters': len(text), 'truncated': start > 0 or start + len(excerpt) < len(text)}


# This is fixed product code, never a model-provided script or selector.
_COLLECT = r"""() => {
 const skip = 'script,style,noscript,template,input,textarea,select,option,[contenteditable]:not([contenteditable="false"]),[hidden],[aria-hidden="true"]';
 const cache=new WeakMap();
 const visible = el => {
   if(!el)return false;
   if(cache.has(el))return cache.get(el);
   let depth=0,p=el;
   for (;p&&depth++<64;p=p.parentElement) {
     if(p.matches(skip)){cache.set(el,false);return false;}
     const s=getComputedStyle(p);
     if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse'||s.opacity==='0'){cache.set(el,false);return false;}
   }
   const result=!p&&el.getClientRects().length>0;
   cache.set(el,result);return result;
 };
 const walker=document.createTreeWalker(document.body||document.documentElement,NodeFilter.SHOW_TEXT);
 let n,visited=0,length=0,parts=[];
 while((n=walker.nextNode())&&visited++<30000&&length<60000) {
   if(visible(n.parentElement)){
     const t=n.textContent.slice(0,60000).replace(/\s+/g,' ').trim();
     if(t){parts.push(t);length+=t.length+1;}
   }
 }
 const links=[];
 let inspected=0,el;
 const elements=document.createTreeWalker(document.body||document.documentElement,NodeFilter.SHOW_ELEMENT);
 while((el=elements.nextNode())&&inspected++<30000){
   if(links.length>=80)break;
   if(el.tagName==='A'&&el.hasAttribute('href')&&visible(el))links.push({text:(el.innerText||el.getAttribute('aria-label')||'링크').slice(0,200),url:el.href});
 }
 return {text:parts.join('\n').slice(0,60000),links,node_limit:visited>=30000||length>=60000};
}"""


def require_background_web(process):
    """Prevent a web-tool fallback from taking the user's desktop browser."""
    if str(process).lower() in {'chrome.exe','msedge.exe','firefox.exe','brave.exe','opera.exe','vivaldi.exe'}:
        from windows_workspace import WindowsWorkspaceError
        raise WindowsWorkspaceError('background_browser')


class BrowserWorkspace:
    def __init__(self, profile_root=None, headless=False):
        self.profile_root = pathlib.Path(profile_root) if profile_root is not None else RUNTIME / 'chrome-profile'
        self.headless = bool(headless)
        self._queue = queue.Queue(maxsize=32)
        self._pages = {}
        self._snapshots = {}
        self._context = None
        self._playwright = None
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, name='AI-OFFICE-browser', daemon=True)
        self._thread.start()

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                self._cleanup()
                return
            method, args, reply, cancelled = item
            if cancelled.is_set():
                continue
            try:
                reply.put((True, self._execute(method, *args)))
            except BrowserWorkspaceError as error:
                reply.put((False, error))
            except Exception:
                reply.put((False, BrowserWorkspaceError(_FAILURE)))

    def _dispatch(self, method, *args):
        reply = queue.Queue(maxsize=1)
        cancelled = threading.Event()
        with self._lock:
            if self._closed:
                raise BrowserWorkspaceError('브라우저 작업 공간이 닫혔습니다.')
            try:
                self._queue.put_nowait((method, args, reply, cancelled))
            except queue.Full:
                raise BrowserWorkspaceError('브라우저 요청이 많습니다. 잠시 후 다시 요청하세요.') from None
        try:
            success, result = reply.get(timeout=90)
        except queue.Empty:
            cancelled.set()
            raise BrowserWorkspaceError('브라우저 응답 시간이 지났습니다. 실행 중인 요청은 완료될 수 있으므로 browser_tabs/inspect로 현재 상태를 먼저 확인하세요. 사용자 화면 조작으로 전환하지 마세요.') from None
        if not success:
            raise result
        return result

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Do not execute waiting navigation requests after shutdown.
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    _method, _args, reply, cancelled = item
                    cancelled.set()
                    reply.put_nowait((False, BrowserWorkspaceError('브라우저 작업 공간이 닫혔습니다.')))
            self._queue.put_nowait(None)
        self._thread.join(timeout=10)

    def _cleanup(self):
        for obj in (self._context, self._playwright):
            if obj:
                try:
                    obj.close() if obj is self._context else obj.stop()
                except Exception:
                    pass
        self._context = self._playwright = None

    def _ensure(self):
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserWorkspaceError('사이트를 읽으려면 Playwright와 Chrome 설치가 필요합니다.') from None
        try:
            self.profile_root = _profile_path(self.profile_root)
            self.profile_root.mkdir(parents=True, exist_ok=True)
            _profile_path(self.profile_root)
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_root), channel='chrome', headless=self.headless,
                viewport={'width': 1440, 'height': 1000} if self.headless else None,
                no_viewport=not self.headless, accept_downloads=False,
                service_workers='block')
            # Keep a real inspectable window on the taskbar. Minimize only its
            # initial blank page; later user restores are never overridden.
            if not self.headless:
                page=self._context.pages[0]
                session=self._context.new_cdp_session(page)
                try:
                    window=session.send('Browser.getWindowForTarget')
                    session.send('Browser.setWindowBounds',{'windowId':window['windowId'],'bounds':{'windowState':'minimized'}})
                finally:session.detach()
            self._context.set_default_timeout(15000)
            self._context.set_default_navigation_timeout(20000)
            self._context.route('**/*', self._route)
            context = self._context
            context.on('close', lambda *_args: self._on_context_closed(context))
        except Exception:
            self._cleanup()
            raise BrowserWorkspaceError('작업용 Chrome을 시작하지 못했습니다. 설치·프로필 연결 상태를 확인하세요. 사용자 화면 조작으로 자동 전환하지 마세요.') from None

    def _on_context_closed(self, context=None):
        if context is not None and self._context is not context:
            return
        self._context = None
        self._pages.clear()
        self._snapshots.clear()
        getattr(self,'_control_snapshots',{}).clear()

    def _route(self, route):
        try:
            validate_url(route.request.url)
            prior = route.request.redirected_from
            for _ in range(10):
                if prior is None:
                    break
                validate_url(prior.url)
                prior = prior.redirected_from
            if prior is not None:
                raise BrowserWorkspaceError('사이트 이동 횟수가 제한을 넘었습니다.')
            route.continue_()
        except Exception:
            route.abort()

    def _prune(self):
        for tab_id, page in list(self._pages.items()):
            if page.is_closed():
                self._pages.pop(tab_id, None)
                self._snapshots.pop(tab_id, None)
                getattr(self,'_control_snapshots',{}).pop(tab_id,None)
        # A person can navigate a new tab in this dedicated window as well.
        # Adopt only supported pages; never attach to any other Chrome window.
        if self._context is not None:
            for page in self._context.pages:
                if len(self._pages) >= 20:
                    break
                if any(known is page for known in self._pages.values()) or page.is_closed():
                    continue
                try:
                    validate_url(page.url)
                except BrowserWorkspaceError:
                    continue
                self._pages[uuid.uuid4().hex] = page

    def _page(self, tab_id):
        self._prune()
        if not isinstance(tab_id, str) or tab_id not in self._pages:
            raise BrowserWorkspaceError('열린 사이트 목록에서 탭을 다시 선택하세요.')
        page = self._pages[tab_id]
        validate_url(page.url)
        return page

    def _meta(self, tab_id, page):
        return {'tab_id': tab_id, 'title': _redact(page.title()[:300]), 'url': public_url(page.url),
                'background':True,'headless':self.headless,'visible_window':not self.headless,
                'session':'office_automation', 'shares_user_browser':False}

    def _execute(self, method, *args):
        return getattr(self, '_do_' + method)(*args)

    def open(self, url):
        return self._dispatch('open', url)

    def tabs(self):
        return self._dispatch('tabs')

    def read(self, tab_id, query='', offset=0):
        return self._dispatch('read', tab_id, query, offset)

    def follow(self, tab_id, link_id, snapshot_id):
        return self._dispatch('follow', tab_id, link_id, snapshot_id)

    def scroll(self, tab_id, direction='down'):
        return self._dispatch('scroll', tab_id, direction)

    def view(self, tab_id):
        return self._dispatch('view', tab_id)

    def inspect(self,tab_id):return self._dispatch('inspect',tab_id)

    def export(self,tab_id):return self._dispatch('export',tab_id)

    def _do_export(self,tab_id):
        page=self._page(tab_id);data=page.evaluate(_COLLECT);text=_redact(data.get('text',''))
        return dict(self._meta(tab_id,page),text=text,total_characters=len(text),truncated=bool(data.get('node_limit')),
                    scope='현재 페이지의 수집 가능한 본문 최대 60000자. 입력값·내장 프레임은 별도 관찰합니다.')

    def read_value(self,tab_id,snapshot_id,control_id):return self._dispatch('read_value',tab_id,snapshot_id,control_id)

    def _do_read_value(self,*args):
        import browser_controls
        return browser_controls.read_value(self,*args)

    def act(self,tab_id,snapshot_id,control_id,operation,value=None):
        return self._dispatch('act',tab_id,snapshot_id,control_id,operation,value)

    def _do_inspect(self,tab_id):
        import browser_controls
        return browser_controls.inspect(self,tab_id)

    def _do_act(self,*args):
        import browser_controls
        return browser_controls.act(self,*args)

    def _do_open(self, url):
        validate_url(url)
        for attempt in range(2):
            self._ensure()
            self._prune()
            context = self._context
            if context is None:
                if attempt == 0:
                    continue
                raise BrowserWorkspaceError('전용 Chrome 창이 닫혔습니다. 다시 열어주세요.')
            if len(self._pages) >= 20:
                raise BrowserWorkspaceError('전용 브라우저 탭은 최대 20개입니다. 사용하지 않는 탭을 닫아주세요.')
            try:
                if not self.headless and context.pages:
                    # Playwright new_page activates/restores a headed Chrome.
                    # Create a background target instead and observe its page.
                    session=context.new_cdp_session(context.pages[0])
                    try:
                        with context.expect_page(timeout=10000) as opened:
                            session.send('Target.createTarget',{'url':'about:blank','background':True})
                        page=opened.value
                    finally:session.detach()
                else:
                    page = context.new_page()
            except Exception as error:
                # While the worker is idle, sync Playwright cannot deliver a
                # manual Chrome-close event. new_page may be the first RPC to
                # receive it. Recreate only a definitively closed context and
                # only before any site navigation has been dispatched.
                closed = self._context is None or _is_target_closed_error(error)
                if closed:
                    self._on_context_closed(context)
                    if attempt == 0:
                        continue
                raise BrowserWorkspaceError('전용 Chrome 탭을 열 수 없습니다. 창 상태를 확인하세요.') from None
            break
        try:
            page.goto(url, wait_until='domcontentloaded')
            validate_url(page.url)
        except Exception:
            try:
                page.close()
            except Exception:
                pass
            raise BrowserWorkspaceError('사이트를 열지 못했습니다. 주소와 연결 상태를 확인하세요.') from None
        self._prune()
        tab_id = next((key for key, known in self._pages.items() if known is page), None)
        if tab_id is None:
            tab_id = uuid.uuid4().hex
            self._pages[tab_id] = page
        return self._meta(tab_id, page)

    def _do_tabs(self):
        self._prune()
        result = []
        for tab_id, page in self._pages.items():
            try:
                validate_url(page.url)
                result.append(self._meta(tab_id, page))
            except BrowserWorkspaceError:
                pass
        return result

    def _do_read(self, tab_id, query='', offset=0):
        page = self._page(tab_id)
        data = page.evaluate(_COLLECT)
        result = self._meta(tab_id, page)
        result.update(read_excerpt(data.get('text', ''), query, offset))
        snapshot_id = uuid.uuid4().hex
        links, targets = [], {}
        for raw in data.get('links', [])[:80]:
            try:
                target = validate_url(raw.get('url'))
            except BrowserWorkspaceError:
                continue
            link_id = uuid.uuid4().hex
            targets[link_id] = target
            links.append({'id': link_id, 'text': _redact(str(raw.get('text', ''))[:200]), 'url': public_url(target)})
        self._snapshots[tab_id] = {'id': snapshot_id, 'url': page.url, 'targets': targets}
        unread = max(0, len(page.frames) - 1)
        result.update(snapshot_id=snapshot_id, links=links, captured_at=datetime.datetime.now().astimezone().isoformat(),
                      unread_iframes=unread, scope='현재 탭의 메인 프레임에 렌더링된 텍스트',
                      collection_limited=bool(data.get('node_limit')))
        return result

    def _do_follow(self, tab_id, link_id, snapshot_id):
        page = self._page(tab_id)
        snapshot = self._snapshots.get(tab_id, {})
        if snapshot.get('id') != snapshot_id or snapshot.get('url') != page.url or link_id not in snapshot.get('targets', {}):
            raise BrowserWorkspaceError('화면이 바뀌었거나 만료된 링크입니다. 사이트 내용을 다시 읽어주세요.')
        target = validate_url(snapshot['targets'][link_id])
        self._snapshots.pop(tab_id, None)
        page.goto(target, wait_until='domcontentloaded')
        validate_url(page.url)
        return self._do_read(tab_id)

    def _do_scroll(self, tab_id, direction):
        if direction not in {'up', 'down', 'top', 'bottom'}:
            raise BrowserWorkspaceError('스크롤 방향을 확인하세요.')
        page = self._page(tab_id)
        page.evaluate("direction => {if(direction==='top')window.scrollTo(0,0);else if(direction==='bottom')window.scrollTo(0,document.documentElement.scrollHeight);else window.scrollBy(0,(direction==='down'?1:-1)*innerHeight*0.8);}", direction)
        page.wait_for_timeout(150)
        return self._do_read(tab_id)

    def _do_view(self, tab_id):
        page = self._page(tab_id)
        if self.headless:page.set_viewport_size({'width': 1440, 'height': 1000})
        size=page.evaluate('({width:innerWidth,height:innerHeight})')
        raw = page.screenshot(type='png', full_page=False,
                              mask=[page.locator('input,textarea,select,[contenteditable],iframe,frame')], mask_color='#202020')
        if len(raw) > 8 * 1024 * 1024:
            raise BrowserWorkspaceError('화면 이미지가 너무 큽니다. 텍스트 읽기를 사용하세요.')
        result = self._meta(tab_id, page)
        result.update(image=base64.b64encode(raw).decode('ascii'), mime_type='image/png',
                      width=size['width'], height=size['height'], captured_at=datetime.datetime.now().astimezone().isoformat(),
                      masked_inputs=True, masked_iframes=True, unread_iframes=max(0, len(page.frames) - 1),
                      scope='현재 탭의 보이는 화면 (입력란·내장 프레임 제외)')
        return result


_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def manager():
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None or _MANAGER._closed:
            _MANAGER = BrowserWorkspace()
        return _MANAGER


def _shutdown():
    if _MANAGER is not None:
        _MANAGER.close()


atexit.register(_shutdown)
