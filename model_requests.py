"""Bounded provider pools; local GPU calls remain serial and backgrounds yield."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading

_condition=threading.Condition()
_foreground_scopes=0
_waiting={}
_active={}
_pool=ContextVar('office_model_pool',default='default')
CAPACITY={'codex':2,'claude':1,'gemini':1,'local':1,'default':1}

@contextmanager
def provider_scope(provider):
    token=_pool.set(provider if provider in CAPACITY else 'default')
    try:yield
    finally:_pool.reset(token)

def is_idle():
    with _condition:return not any(_active.values()) and _foreground_scopes==0

@contextmanager
def foreground_scope():
    global _foreground_scopes
    with _condition:_foreground_scopes+=1
    try:yield
    finally:
        with _condition:
            _foreground_scopes-=1;_condition.notify_all()

@contextmanager
def request_slot(background=False):
    import task_control
    key=_pool.get()
    with _condition:
        if not background:_waiting[key]=_waiting.get(key,0)+1
        try:
            while _active.get(key,0)>=CAPACITY[key] or (background and (_foreground_scopes or any(_waiting.values()))):
                task_control.checkpoint();_condition.wait(.2)
            task_control.checkpoint();_active[key]=_active.get(key,0)+1
        finally:
            if not background:_waiting[key]-=1
            _condition.notify_all()
    try:yield
    finally:
        with _condition:
            _active[key]-=1;_condition.notify_all()
