"""Process-local model request admission, with priority for active chats.

A foreground scope spans the whole conversation, including gaps between model
calls. An already admitted request finishes normally before another can start.
"""
from contextlib import contextmanager
import threading


_condition = threading.Condition()
_foreground_scopes = 0
_foreground_waiting = 0
_request_active = False


@contextmanager
def foreground_scope():
    """Keep background requests waiting until this conversation finishes."""
    global _foreground_scopes
    with _condition:
        _foreground_scopes += 1
    try:
        yield
    finally:
        with _condition:
            _foreground_scopes -= 1
            _condition.notify_all()


@contextmanager
def request_slot(background=False):
    """Serialize HTTP requests, admitting waiting foreground work first."""
    global _foreground_waiting, _request_active
    with _condition:
        if not background:
            _foreground_waiting += 1
        try:
            while _request_active or (background and (_foreground_scopes or _foreground_waiting)):
                _condition.wait()
            _request_active = True
        finally:
            if not background:
                _foreground_waiting -= 1
                _condition.notify_all()
    try:
        yield
    finally:
        with _condition:
            _request_active = False
            _condition.notify_all()
