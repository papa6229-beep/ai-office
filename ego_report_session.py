"""Synchronous report adapter over the installed official Ego Page API.

Only the explicit methods used by the registered report algorithm are mapped.
No Playwright, desktop input, browser launch, or profile/session copying.
Commands and credential field values stay in memory on authenticated loopback.
"""
from contextlib import contextmanager
import base64
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

import ego_workspace
import office_fastlane
import task_control
import execution_receipts

ROOT=Path(__file__).resolve().parent
REPORT_ENGINE='ego'
ADAPTER_VERSION=1
RUNTIME_REVISION='2026-10-28-confirmed-save-v5'


class EgoCommandError(RuntimeError):
    def __init__(self,message,*,code,op):
        super().__init__(message)
        self.code=code;self.op=op;self.automatic_retry=False


class EgoResponseUncertain(EgoCommandError):
    pass


class Response:
    def __init__(self,data):self.data=data;self.ok=data['ok'];self.status=data['status']
    def json(self):
        value=self.data.get('body')
        return json.loads(value) if isinstance(value,str) else value
    def body(self):return base64.b64decode(self.data['base64'])


class Request:
    def __init__(self,page):self.page=page
    def get(self,url,timeout=20000):return Response(self.page.call('fetch',url=url,method='GET',timeout=timeout))
    def post(self,url,*,data=None,form=None,timeout=20000):
        return Response(self.page.call('fetch',url=url,method='POST',data=data,form=form,timeout=timeout))


class Locator:
    def __init__(self,page,selector,index=None):self.page=page;self.selector=selector;self.index=index
    def call(self,op,**args):return self.page.call(op,selector=self.selector,index=self.index,**args)
    def locator(self,selector):
        if self.index is not None:raise ValueError('Indexed nested locator is not supported')
        return Locator(self.page,self.selector+' '+selector)
    def nth(self,index):return Locator(self.page,self.selector,index)
    @property
    def last(self):return self.nth(-1)
    def count(self):return self.call('count')
    def input_value(self):return self.call('value')
    def get_attribute(self,name):return self.call('attribute',name=name)
    def fill(self,value):self.call('fill',value=value)
    def click(self,no_wait_after=False,timeout=12000):return self.call('click',timeout=timeout)
    def is_visible(self):return self.call('visible')
    def evaluate(self,fn):return self.call('element_evaluate',fn=fn)
    def scroll_into_view_if_needed(self,timeout=5000):self.call('scroll',timeout=timeout)
    def wait_for(self,state='visible',timeout=12000):self.call('wait_selector',state=state,timeout=timeout)


class Page:
    def __init__(self,session,label):self.session=session;self.label=label;self.closed=False;self.request=Request(self)
    def call(self,op,**args):return self.session.call(op,page=self.label,**args)
    @property
    def url(self):return self.call('url')
    def set_default_timeout(self,value):pass
    def locator(self,selector):return Locator(self,selector)
    def get_by_role(self,role,*,name,exact=False):
        if not exact:raise ValueError('Report role locator must be exact')
        escaped=name.replace('\\','\\\\').replace("'","\\'")
        return Locator(self,"loc=role:"+role+"[name='"+escaped+"']")
    def goto(self,url,wait_until='domcontentloaded',timeout=25000):return self.call('goto',url=url,timeout=timeout)
    def evaluate(self,fn,arg=None):return self.call('evaluate',fn=fn,arg=arg)
    def wait_for_function(self,fn,timeout=12000):return self.call('wait_function',fn=fn,timeout=timeout)
    def wait_for_timeout(self,value):
        deadline=time.monotonic()+value/1000
        while time.monotonic()<deadline:self.session.checkpoint();time.sleep(min(.1,max(0,deadline-time.monotonic())))
    def wait_for_load_state(self,state='domcontentloaded',timeout=20000):return self.call('wait_load',state=state,timeout=timeout)
    def on(self,event,callback):
        if event!='dialog':raise ValueError('Unsupported report event')
        self.call('dialog_policy',policy='product_save')
    @contextmanager
    def expect_navigation(self,wait_until='domcontentloaded',timeout=20000):
        yield
        self.wait_for_load_state(wait_until,timeout)
    def is_closed(self):return self.closed
    def close(self,run_before_unload=False):
        if not self.closed:self.call('close');self.closed=True


class Session:
    def __init__(self,db,request_id,folder,space_id=None):
        self.db=db;self.request_id=request_id;self.folder=Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(32);self.proc=None;self.pages=[];self.blank_available=True
        self.endpoint=None;self.space_id=space_id;self.finished=False
        self.monitor_stop=threading.Event()
        self.timings={};self.last_failure=None;self.reconciliation_required=False
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def checkpoint(self):
        if office_fastlane.stopped(self.db,self.request_id):raise task_control.Stopped()
    def __enter__(self):
        self.checkpoint()
        self.config=self.folder/'ego-session.json'
        ready=self.folder/'ego-ready.json'
        if ready.exists():raise ValueError('Existing session receipt; do not replay an uncertain report')
        self.config.write_text(json.dumps({'token':self.token,'ready':ready.as_posix(),'space_id':self.space_id,
            'name':'AI OFFICE report '+self.request_id}),encoding='utf-8')
        entry=self.folder/'ego-session.mjs'
        entry.write_text('globalThis.egoReportConfig='+json.dumps(self.config.as_uri())+';\nawait import('+json.dumps((ROOT/'ego_report_session.mjs').as_uri())+');',encoding='utf-8')
        self.stream=(self.folder/'ego-transport.log').open('wb')
        self.proc=subprocess.Popen([ego_workspace.executable(),'nodejs','-e',"await import('"+entry.as_uri()+"')"],
            stdin=subprocess.DEVNULL,stdout=self.stream,stderr=self.stream,cwd=str(ROOT),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def monitor():
            while not self.monitor_stop.wait(.2):
                try:stopped=office_fastlane.stopped(self.db,self.request_id)
                except Exception:stopped=True
                if stopped:
                    if self.proc.poll() is None:self.proc.terminate()
                    return
        threading.Thread(target=monitor,daemon=True).start()
        try:
            deadline=time.monotonic()+25
            while not ready.exists():
                self.checkpoint()
                if self.proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Ego session startup unconfirmed')
                time.sleep(.1)
            value=json.loads(ready.read_text(encoding='utf-8'))
            self.endpoint='http://127.0.0.1:'+str(value['port'])
            self.space_id=value['space_id']
            existing=self.call('pages')
            self.pages=[Page(self,p['label']) for p in existing]
            self.blank_available=len(existing)==1 and existing[0]['url'] in {'about:blank','chrome://newtab/','chrome://new-tab-page/'}
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise
    def call(self,op,**args):
        self.checkpoint()
        if self.reconciliation_required:
            raise EgoResponseUncertain('Existing Ego command effect needs reconciliation; no further commands dispatched',code='response_uncertain',op=op)
        payload=json.dumps({'op':op,**args},ensure_ascii=False).encode()
        request=urllib.request.Request(self.endpoint+'/command',data=payload,
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        started=time.monotonic()
        try:
            try:
                with self.opener.open(request,timeout=min(310,5+args.get('timeout',30000)/1000)) as response:
                    data=json.load(response)
            except (OSError,ValueError) as error:
                # The command may already have acted; never resend it.
                self.checkpoint()
                self.last_failure={'code':'response_uncertain','operation':op,'error_type':type(error).__name__,
                                   'automatic_retry':False,'next_step':'reconcile_effect'}
                self.reconciliation_required=True
                raise EgoResponseUncertain('Ego command response uncertain; reconcile existing effect before any new action',code='response_uncertain',op=op) from error
        finally:
            execution_receipts.observe(self.timings,'ego_tool.'+op,started)
        self.checkpoint()
        if not data.get('ok'):
            code=data.get('error_code','command_failed')
            if code not in {'dialog_open','serialization_error','command_failed'}:code='command_failed'
            self.last_failure={'code':code,'operation':op,'automatic_retry':False}
            raise EgoCommandError(data.get('error','Ego command failed'),code=code,op=op)
        return data.get('value')
    def new_page(self):
        if self.blank_available:self.blank_available=False;return self.pages[0]
        page=Page(self,self.call('new_page')['label']);self.pages.append(page);return page
    def finish(self):
        keep=[p.label for p in self.pages if not p.closed and p.url.startswith(('https://webdesign-daily-report.vercel.app/','https://banana.bizhost.kr/adminx/goods.php'))]
        self.call('finish',keep=keep);self.finished=True
    def __exit__(self,*unused):
        self.monitor_stop.set()
        if self.proc and self.proc.poll() is None:
            # Stop transport only; do not close, claim, or retry an uncertain Page.
            self.proc.terminate()
            try:self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=3)
        if hasattr(self,'stream'):self.stream.close()
        if hasattr(self,'config'):self.config.unlink(missing_ok=True)
        try:
            execution_receipts.write_json(self.folder/'execution-observation.json',
                {'runtime_revision':RUNTIME_REVISION,'stage_timings':self.timings,'failure':self.last_failure,
                 'model_calls':0,'approval_wait_ms':None,'model_ms':None})
        except OSError:
            # Diagnostic persistence cannot erase an already observed site result.
            pass
