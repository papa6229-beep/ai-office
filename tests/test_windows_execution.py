"""Common execution through the real native loop, registry and durable approvals."""
import json
import pathlib
import tempfile
import unittest
import uuid
from unittest.mock import patch

from assistant_tools import ToolRegistry
from work_assistant import WorkAssistant


class Windows:
    def __init__(self): self.value='before'; self.actions=[]; self.stale=False
    def windows(self,query=''): return [{'window_id':'private-window','title':'시험 편집기','process':'fixture.exe'}]
    def inspect(self,window):
        return {'window_id':window,'snapshot_id':'private-snapshot','title':'시험 편집기','process':'fixture.exe',
                'controls':[{'id':'private-control','name':'업무 입력','control_type':'Edit','enabled':True,
                             'patterns':['focus','set_value'],'value':self.value}], 'text':'업무 입력 '+self.value}
    def act(self,window,control,snapshot,operation,value=None):
        if self.stale:
            from windows_workspace import WindowsWorkspaceError
            raise WindowsWorkspaceError('stale')
        self.actions.append((window,control,snapshot,operation,value)); self.value=value
        return {'action_dispatched':True,'observation':self.inspect(window),
                'verification':{'goal_verified':True,'status':'readback_verified'}}


class WindowsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=pathlib.Path(self.temp.name)/'office.db';self.windows=Windows();self.model_calls=[]
        self.factory=lambda:ToolRegistry(self.db,windows_service=self.windows)
        self.service=WorkAssistant(self.db,lambda:{},None,native_model=self.model,registry_factory=self.factory)

    @staticmethod
    def tool(name,**args):
        return {'content':'','tool_calls':[{'id':uuid.uuid4().hex,'name':name,'arguments':args}]}

    def model(self,messages,tools):
        self.model_calls.append(messages)
        if messages[-1]['role']!='tool': return self.tool('windows_list',query='시험 편집기')
        data=json.loads(messages[-1]['content']).get('data',{})
        name=messages[-1]['tool_name']
        if name=='windows_list':return self.tool('windows_inspect',ref=data['items'][0]['ref'])
        if name=='windows_inspect':return self.tool('windows_action',ref=data['controls'][0]['ref'],operation='set_value',value='확인할 내용')
        return {'content':'새 화면의 업무 입력란에 확인할 내용이 표시됩니다.','tool_calls':[]}

    def command(self):
        return self.service.perform('command',{'request_id':uuid.uuid4().hex,'message':'시험 편집기에 확인할 내용이라고 입력해줘'})

    def approve(self,pending,option='approve',request_id=None):
        return self.service.perform('assistant-action',{'request_id':request_id or uuid.uuid4().hex,
            'pending_id':pending['id'],'option_id':option})

    def test_observe_approval_apply_readback_and_continue_once(self):
        first=self.command();self.assertEqual(self.windows.actions,[])
        self.assertIn('확인할 내용',first['assistant_actions'][0]['description'])
        request=uuid.uuid4().hex
        result=self.approve(first['assistant_actions'][0],request_id=request)
        self.assertEqual(len(self.windows.actions),1)
        self.assertIn('입력란에 확인할 내용',result['result']['answer'])
        self.assertEqual(self.approve(first['assistant_actions'][0],request_id=request),result)
        self.assertEqual(len(self.windows.actions),1)
        self.assertNotIn('private-window',json.dumps(result))
        self.assertNotIn('private-snapshot',json.dumps(result))
        observation=result['result']['tool_results'][0]['data']
        self.assertFalse(observation['verification']['goal_verified'])
        self.assertTrue(observation['verification']['control_state_verified'])

    def test_changed_screen_never_dispatches_or_resumes(self):
        pending=self.command()['assistant_actions'][0];self.windows.stale=True
        count=len(self.model_calls);result=self.approve(pending)
        self.assertEqual(self.windows.actions,[]);self.assertEqual(len(self.model_calls),count)
        self.assertIn('변경',result['result']['message'])

    def test_approval_chain_preserves_prior_mutations_and_signatures(self):
        def mixed(messages,tools):
            completed=[m for m in messages if m.get('role')=='tool' and m.get('tool_name')=='work_create']
            if not completed:return self.tool('work_create',title='중복되면 안 되는 업무')
            if messages[-1].get('tool_name')=='windows_action':
                # Deliberately repeat the already completed call: the logical
                # request's effect cache must stop a second database mutation.
                return self.tool('work_create',title='중복되면 안 되는 업무')
            if len(completed)>1:return {'content':'입력란과 업무 기록을 확인했습니다.','tool_calls':[]}
            if messages[-1].get('tool_name')=='work_create':return self.tool('windows_list',query='시험 편집기')
            return self.model(messages,tools)
        self.service.native_model=mixed
        first=self.command();self.assertEqual(len(first['tasks']),1)
        result=self.approve(first['assistant_actions'][0])
        self.assertEqual(len(result['tasks']),1)
        self.assertEqual(len(self.windows.actions),1)

    def test_cancel_does_not_dispatch_or_continue(self):
        pending=self.command()['assistant_actions'][0];count=len(self.model_calls)
        result=self.approve(pending,'cancel')
        self.assertEqual(self.windows.actions,[]);self.assertEqual(len(self.model_calls),count)
        self.assertIn('취소',result['result']['message'])

    def test_approval_stops_batch_without_unanswered_tool_calls_on_resume(self):
        unmatched=[]
        def batched(messages,tools):
            last=messages[-1]
            if last.get('tool_name')=='windows_inspect':
                data=json.loads(last['content'])['data']
                return {'content':'','tool_calls':[
                    {'id':'pending_call','name':'windows_action','arguments':{'ref':data['controls'][0]['ref'],'operation':'set_value','value':'확인할 내용'}},
                    {'id':'unexecuted_call','name':'work_create','arguments':{'title':'실행되면 안 되는 후속 작업'}}]}
            if last.get('tool_name')=='windows_action':
                answered={m['tool_call_id'] for m in messages if m.get('role')=='tool'}
                unmatched.extend(c['id'] for m in messages for c in m.get('tool_calls',[]) if c['id'] not in answered)
                return {'content':'새 입력값이 보입니다.','tool_calls':[]}
            return self.model(messages,tools)
        self.service.native_model=batched
        first=self.command();result=self.approve(first['assistant_actions'][0])
        self.assertEqual(unmatched,[])
        self.assertEqual(result['tasks'],[])

    def test_model_cannot_supply_approval_or_paths(self):
        registry=self.factory();context={'request_id':'1'*32,'step_id':'2'*32,'refs':{}}
        for args in ({'ref':'madeup','operation':'invoke','approved':True},
                     {'ref':'madeup','operation':'invoke','path':'C:/private'}):
            with self.assertRaises(ValueError):registry.execute('windows_action',args,context)
        self.assertEqual(self.windows.actions,[])

    def test_chrome_web_shortcut_facts_do_not_require_default_browser_change(self):
        class Browser:
            def open(self,url):return {'tab_id':'tab','title':'Site','url':'https://example.com'}
        with patch('assistant_tools.importlib.util.find_spec',return_value=object()):
            facts=ToolRegistry(self.db,browser_service=Browser()).runtime_context()
        self.assertEqual(facts['browser']['web_shortcuts_engine'],'Google Chrome')
        self.assertFalse(facts['browser']['uses_windows_default'])
        self.assertFalse(facts['browser']['default_browser_change_required_for_managed_chrome'])


if __name__=='__main__':unittest.main()
