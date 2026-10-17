"""Short-lived, provisional chat text; committed messages remain in office.db."""
from contextlib import contextmanager
from contextvars import ContextVar
import re
import threading
import time
import uuid

_lock = threading.Lock()
_rows = {}
_TTL = 15 * 60
_LIMIT = 64
_publisher = ContextVar('office_chat_publisher', default=None)


def publish(phase, label, text=None):
    """Publish visible prose/activity only, never tool arguments or reasoning."""
    callback = _publisher.get()
    if callback:
        callback(phase, label, text)


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{32}', value):
        raise ValueError('현재 대화 요청을 확인하세요.')
    return value


def _prune():
    now = time.monotonic()
    for key in list(_rows):
        if now - _rows[key]['updated'] > _TTL:
            del _rows[key]
    while len(_rows) >= _LIMIT:
        key = min(_rows, key=lambda item: _rows[item]['updated'])
        del _rows[key]


def snapshot(request_id):
    request_id = _identifier(request_id)
    with _lock:
        row = _rows.get(request_id)
        if not row or time.monotonic() - row['updated'] > _TTL:
            return {'request_id': request_id, 'phase': 'waiting', 'label': '', 'text': '', 'revision': 0}
        return {key: row[key] for key in ('request_id', 'phase', 'label', 'text', 'revision')}


@contextmanager
def capture(request_id):
    import codex_bridge
    request_id = _identifier(request_id)
    owner = uuid.uuid4().hex
    with _lock:
        prior = _rows.get(request_id)
        # A duplicate HTTP request must not clear an in-flight owner's draft.
        if prior and prior['phase'] not in {'done', 'error', 'cancelled'}:
            owner = None
        else:
            _prune()
            _rows[request_id] = {'request_id': request_id, 'owner': owner,
                                 'phase': 'thinking', 'label': '요청을 살펴보고 있어요', 'text': '', 'revision': 1,
                                 'updated': time.monotonic()}

    def activity(phase, label, text=None):
        if not owner or phase not in {'thinking', 'working', 'draft', 'waiting', 'done', 'error','cancelled'}:
            return
        if not isinstance(label, str) or (text is not None and not isinstance(text, str)):
            return
        with _lock:
            row = _rows.get(request_id)
            if row and row['owner'] == owner:
                row.update(phase=phase, label=label[:160], updated=time.monotonic())
                if text is not None:
                    row['text'] = text[:6000]
                row['revision'] += 1

    def update(event):
        if isinstance(event, dict) and isinstance(event.get('text'), str):
            activity('draft', '답변을 작성하고 있어요', event['text'])

    failed = False
    token = _publisher.set(activity)
    try:
        with codex_bridge.stream_scope(update):
            yield
    except BaseException:
        failed = True
        raise
    finally:
        _publisher.reset(token)
        if owner:
            with _lock:
                row = _rows.get(request_id)
                if row and row['owner'] == owner:
                    phase = 'error' if failed else row['phase'] if row['phase'] in {'done', 'error', 'waiting','cancelled'} else 'done'
                    row.update(text='', phase=phase, updated=time.monotonic())
                    row['revision'] += 1
