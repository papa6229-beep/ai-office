"""Live browser dialogs and bounded, body-free network observations."""
from collections import deque
import uuid


def install(workspace, context):
    from browser_workspace import _redact, public_url
    workspace._dialogs = {}
    workspace._browser_events = deque(maxlen=30)

    def opened(dialog):
        page = dialog.page
        if page is None:
            return
        ident = uuid.uuid4().hex
        workspace._dialogs[ident] = {'handle': dialog, 'page': page,
            'id': ident, 'type': dialog.type, 'message': _redact(dialog.message)[:1500]}

    def closed(dialog):
        for ident, entry in list(workspace._dialogs.items()):
            if entry['handle'] is dialog:
                workspace._dialogs.pop(ident, None)

    def response(receipt):
        request = receipt.request
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            workspace._browser_events.append({'kind': 'response', 'method': request.method,
                'url': public_url(receipt.url), 'status': receipt.status,
                'note': 'HTTP 응답 상태이며 저장 완료의 증거는 아닙니다.'})

    def failed(request):
        workspace._browser_events.append({'kind': 'request_failed', 'method': request.method,
            'url': public_url(request.url), 'failure': _redact(request.failure or '')[:200]})

    context.on('dialog', opened)
    context.on('dialogclosed', closed)
    context.on('response', response)
    context.on('requestfailed', failed)


def pending(workspace, page):
    return [{key: entry[key] for key in ('id', 'type', 'message')}
            for entry in getattr(workspace, '_dialogs', {}).values() if entry['page'] is page]


def resolve(workspace, ident, action):
    from browser_workspace import BrowserWorkspaceError
    if action not in {'accept', 'dismiss'}:
        raise BrowserWorkspaceError('관찰한 대화상자에 확인 또는 취소로 응답하세요.')
    entry = getattr(workspace, '_dialogs', {}).get(ident)
    if entry is None or entry['page'].is_closed():
        raise BrowserWorkspaceError('대화상자가 이미 닫혔습니다. browser_tabs로 현재 상태를 다시 확인하세요.')
    if entry['type'] == 'prompt' and action == 'accept':
        raise BrowserWorkspaceError('텍스트 입력을 요구하는 대화상자는 자동 확인하지 않습니다. 직접 입력하거나 취소하세요.')
    page = entry['page']
    tab_id = next((key for key, value in workspace._pages.items() if value is page), None)
    if tab_id is None:
        raise BrowserWorkspaceError('대화상자의 원래 탭을 확인하지 못했습니다. browser_tabs로 다시 관찰하세요.')
    try:
        getattr(entry['handle'], action)()
        workspace._dialogs.pop(ident, None)
        page.wait_for_timeout(300)
    except Exception:
        raise BrowserWorkspaceError('대화상자 응답 결과를 확정하지 못했습니다. 같은 응답을 반복하지 말고 현재 탭을 확인하세요.') from None
    if page.is_closed():
        return {'tab_closed': True, 'dialog_response': action,
                'note': '대화상자 응답 후 탭이 닫혔습니다. 저장 여부는 원래 목록에서 별도로 확인하세요.'}
    result = workspace._meta(tab_id, page)
    result.update(dialog_response=action,
        note='관찰한 대화상자에 응답했습니다. 실제 저장 결과를 다시 읽어 확인하세요.')
    return result


def status(workspace):
    workspace._prune()
    return {'connected': workspace._context is not None,
        'tabs': len(workspace._pages), 'idle_event_processing': True,
        'dialogs': sum(len(pending(workspace, page)) for page in workspace._pages.values()),
        'network': list(getattr(workspace, '_browser_events', [])),
        'scope': '현재 브라우저 연결 이후의 최근 응답/통신 실패. 입력값·쿠키·요청/응답 본문은 수집하지 않습니다.'}
