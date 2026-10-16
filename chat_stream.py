"""Short-lived, provisional chat text; committed messages remain in office.db."""
from contextlib import contextmanager
import re
import threading
import time
import uuid

_lock = threading.Lock()
_rows = {}
_TTL = 15 * 60
_LIMIT = 64


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
            return {'request_id': request_id, 'phase': 'waiting', 'text': '', 'revision': 0}
        return {key: row[key] for key in ('request_id', 'phase', 'text', 'revision')}


@contextmanager
def capture(request_id):
    import codex_bridge
    request_id = _identifier(request_id)
    owner = uuid.uuid4().hex
    with _lock:
        prior = _rows.get(request_id)
        # A duplicate HTTP request must not clear an in-flight owner's draft.
        if prior and prior['phase'] not in {'done', 'error'}:
            owner = None
        else:
            _prune()
            _rows[request_id] = {'request_id': request_id, 'owner': owner,
                                 'phase': 'draft', 'text': '', 'revision': 1,
                                 'updated': time.monotonic()}

    def update(event):
        if not owner or not isinstance(event, dict):
            return
        text = event.get('text')
        if not isinstance(text, str):
            return
        with _lock:
            row = _rows.get(request_id)
            if row and row['owner'] == owner:
                row.update(text=text[:6000], phase='draft', updated=time.monotonic())
                row['revision'] += 1

    failed = False
    try:
        with codex_bridge.stream_scope(update):
            yield
    except BaseException:
        failed = True
        raise
    finally:
        if owner:
            with _lock:
                row = _rows.get(request_id)
                if row and row['owner'] == owner:
                    row.update(text='', phase='error' if failed else 'done', updated=time.monotonic())
                    row['revision'] += 1
