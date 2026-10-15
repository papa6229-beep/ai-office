import contextlib
import json
import datetime
import pathlib
import sqlite3
import tempfile
import unittest
import uuid

from assistant_runtime import AssistantRuntime
from work_assistant import WorkAssistant


def call(name, arguments=None):
    return {'id': uuid.uuid4().hex, 'name': name, 'arguments': arguments or {}}


class Registry:
    def __init__(self):
        self.calls = []
    def definitions(self):
        return [{'type': 'function', 'function': {'name': n, 'parameters': {'type': 'object'}}}
                for n in ['apps', 'add', 'launch', 'choice', 'approval', 'crash']]
    def effect(self, name):
        return {'apps': 'read', 'add': 'write', 'launch': 'external',
                'choice': 'read', 'approval': 'approval', 'crash': 'external'}[name]
    def execute(self, name, args, context):
        self.calls.append((name, args, context['step_id']))
        if name == 'crash':
            raise SystemExit('simulated process termination')
        if name == 'apps':
            context['refs']['app:1'] = {'target': 'registered'}
            return {'status': 'ok', 'message': '앱 1개를 찾았습니다.', 'data': {'ref': 'app:1'}}
        if name in ('choice', 'approval'):
            return {'status': 'pending', 'message': '선택해 주세요.', 'pending': {
                'kind': 'choice' if name == 'choice' else 'approval', 'title': '앱 선택',
                'description': '확인', 'options': [{'id': 'yes', 'label': '확인'}],
                'refs': context['refs'], 'private_target': 'trusted'}}
        if args.get('ref') not in context['refs']:
            raise ValueError('알 수 없는 참조입니다.')
        return {'status': 'ok', 'message': '실제 등록 완료' if name == 'add' else '열기를 요청했습니다.',
                'refresh': ['launcher']}
    def resolve_pending(self, pending, option_id, context):
        self.calls.append(('resolve', option_id, context['step_id']))
        assert pending['private_target'] == 'trusted'
        return {'status': 'ok', 'message': '선택한 앱을 추가했습니다.', 'refresh': ['launcher']}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = pathlib.Path(self.temp.name) / 'office.db'
        WorkAssistant(self.db, lambda: {}, None)
        self.registry = Registry()
    def runtime(self, rounds):
        self.model_inputs = []
        def model(messages, tools):
            self.model_inputs.append(json.loads(json.dumps(messages)))
            return rounds.pop(0)
        return AssistantRuntime(self.db, model, lambda: self.registry)
    def command(self, runtime, message='챗지피티 추가해줘', request_id=None):
        return runtime.perform('command', {'request_id': request_id or uuid.uuid4().hex, 'message': message})
    def test_native_read_then_write_and_receipts_override_false_model_claim(self):
        runtime = self.runtime([{'tool_calls': [call('apps')]},
            {'tool_calls': [call('add', {'ref': 'app:1'})]}, {'content': '삭제했습니다.'}])
        response = self.command(runtime)
        self.assertTrue(response['result']['launcher_changed'])
        self.assertIn('실제 등록 완료', response['result']['message'])
        self.assertNotIn('삭제했습니다', response['result']['message'])
        self.assertEqual([x[0] for x in self.registry.calls], ['apps', 'add'])
        self.assertEqual(self.model_inputs[1][-1]['role'], 'tool')
    def test_unknown_reference_is_not_executed_successfully(self):
        response = self.command(self.runtime([{'tool_calls': [call('add', {'ref': 'made-up'})]}, {'content': '추가 완료'}]))
        self.assertFalse(response['result']['launcher_changed'])
        self.assertIn('알 수 없는 참조', response['result']['message'])
    def test_no_tools_does_not_report_completion(self):
        response = self.command(self.runtime([{'content': '등록했습니다.'}]))
        self.assertIn('실행한 작업은 없습니다', response['result']['message'])
        self.assertNotIn('등록했습니다', response['result']['message'])
        self.assertEqual(self.registry.calls, [])

    def test_execution_decision_recovers_unattempted_permission_refusal(self):
        runtime = self.runtime([{'content': '시스템을 변경할 권한이 없습니다.'},
                                {'tool_calls': [call('launch', {'ref': 'app:1'})]}, {}])
        decisions = []
        def decide(messages, definitions):
            decisions.append((messages, definitions))
            return {'decision': 'execute', 'observation': {'name': 'apps', 'arguments': {}}, 'message': ''}
        runtime.intent_call = decide
        response = self.command(runtime, message='기본 실행 웹브라우저를 크롬으로 변경해줘')
        self.assertEqual([row[0] for row in self.registry.calls], ['apps', 'launch'])
        self.assertNotIn('권한이 없습니다', response['result']['message'])
        self.assertEqual(len(decisions), 1)
        self.assertEqual([d['function']['name'] for d in decisions[0][1]], ['apps', 'choice'])
        self.assertEqual(self.model_inputs[1][-1]['role'], 'tool')

    def test_execution_decision_information_cancel_and_clarify_have_no_side_effect(self):
        for decision, message in [('answer', '기본 브라우저는 링크를 여는 앱입니다.'),
                                  ('cancel', '실행하지 않겠습니다.'), ('clarify', '어떤 앱을 뜻하시나요?')]:
            with self.subTest(decision=decision):
                runtime = self.runtime([{'content': '권한이 없습니다.'}])
                runtime.intent_call = lambda m, t: {'decision': decision, 'message': message, 'observation': None}
                result = self.command(runtime, message='이전 작업은 하지 마. 기본 브라우저가 뭔지 설명해줘')
                self.assertEqual(result['result']['answer'], message)
                self.assertEqual(self.registry.calls, [])

    def test_execution_decision_cannot_force_effect_or_forged_observation(self):
        for name in ['launch', 'shell_exec']:
            with self.subTest(name=name):
                runtime = self.runtime([{'content': '권한이 없습니다.'}])
                runtime.intent_call = lambda m, t: {'decision': 'execute', 'message': '',
                    'observation': {'name': name, 'arguments': {}}}
                response = self.command(runtime)
                self.assertEqual(self.registry.calls, [])
                self.assertNotIn('권한이 없습니다', response['result']['message'])
                self.assertIn('실행', response['result']['message'])

    def test_execution_decision_not_repeated_after_existing_tool_or_approval(self):
        runtime = self.runtime([{'tool_calls': [call('apps')]}, {'content': '현재 앱 목록입니다.'}])
        runtime.intent_call = lambda *a: self.fail('already attempted; do not reclassify')
        self.command(runtime)
        self.assertEqual([row[0] for row in self.registry.calls], ['apps'])

    def test_execution_decision_constructor_and_audit_replay(self):
        rounds = [{'content': '직접 설정하는 방법을 안내합니다.'},
                  {'tool_calls': [call('add', {'ref': 'app:1'})]}, {}]
        decisions = []
        def decide(messages, definitions):
            decisions.append(messages[-1]['content'])
            return {'decision': 'execute', 'message': '',
                    'observation': {'name': 'apps', 'arguments': {}}}
        runtime = AssistantRuntime(self.db, lambda *args: rounds.pop(0), lambda: self.registry,
                                   intent_call=decide)
        key = uuid.uuid4().hex
        first = self.command(runtime, message='자주 쓰는 그림 도구를 실행 공간에 넣어줘', request_id=key)
        self.assertEqual(first, self.command(runtime,
            message='자주 쓰는 그림 도구를 실행 공간에 넣어줘', request_id=key))
        self.assertEqual(len(decisions), 1)
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            rows = c.execute('SELECT name,effect,status FROM assistant_steps WHERE request_id=? ORDER BY created',
                             (key,)).fetchall()
        self.assertEqual(rows, [('apps', 'read', 'done'), ('add', 'write', 'done')])

    def test_execution_decision_failure_never_echoes_provider_secrets(self):
        def fail(*args):
            raise RuntimeError('fixture-secret-do-not-expose')
        runtime = self.runtime([{'content': '권한이 없습니다.'}])
        runtime.intent_call = fail
        response = self.command(runtime)
        self.assertIn('아직 시도하지 않았습니다', response['result']['message'])
        self.assertNotIn('fixture-secret', json.dumps(response))
        self.assertNotIn('권한이 없습니다', response['result']['message'])
        self.assertEqual(self.registry.calls, [])

    def test_execution_decision_rejects_invalid_or_effectful_non_execution_plan(self):
        for decision in [None, {'decision': 'unexpected', 'message': ''},
                {'decision': 'answer', 'message': '안내', 'observation': {'name': 'launch', 'arguments': {}}},
                {'decision': 'execute', 'message': '', 'observation': {'name': 'apps', 'arguments': 'bad'}}]:
            with self.subTest(decision=decision):
                runtime = self.runtime([{'content': '권한이 없습니다.'}])
                runtime.intent_call = lambda *a: decision
                response = self.command(runtime)
                self.assertEqual(self.registry.calls, [])
                self.assertIn('아직 시도하지 않았습니다', response['result']['message'])

    def test_complete_inventory_does_not_suppress_other_read_explanations(self):
        original=self.registry.execute
        def execute(name,args,context):
            result=original(name,args,context)
            if len(self.registry.calls)==1: result['answer_complete']=True
            return result
        self.registry.execute=execute
        response=self.command(self.runtime([{'tool_calls':[call('apps')]},
            {'tool_calls':[call('apps')]},{'content':'두 조회 결과를 함께 설명합니다.'}]))
        self.assertEqual(response['result']['answer'],'두 조회 결과를 함께 설명합니다.')

    def test_open_then_read_evidence_keeps_grounded_summary(self):
        original=self.registry.execute
        def execute(name,args,context):
            result=original(name,args,context)
            if name=='apps' and len(self.registry.calls)>2: result['evidence']=True
            return result
        self.registry.execute=execute
        response=self.command(self.runtime([{'tool_calls':[call('apps')]},
            {'tool_calls':[call('launch',{'ref':'app:1'})]},
            {'tool_calls':[call('apps')]},{'content':'읽은 본문의 납기는 금요일입니다.'}]))
        self.assertIn('금요일',response['result']['answer'])
        self.assertIn('열기를 요청',response['result']['message'])

    def test_relative_due_dates_have_current_local_date_context(self):
        self.command(self.runtime([{'content': '어떤 업무인지 알려주세요.'}]), message='내일까지 할 일 추가해줘')
        self.assertIn(datetime.datetime.now().astimezone().date().isoformat(), self.model_inputs[0][0]['content'])

    def test_current_runtime_capabilities_are_system_facts_before_history(self):
        self.registry.runtime_context=lambda:{'browser':{'engine':'Google Chrome','uses_windows_default':False},
                                               'windows':{'available':True,'native_screenshots':False}}
        self.command(self.runtime([{'content':'열 대상을 확인하겠습니다.'}]),message='이 사이트는 크롬으로 열어줘')
        facts=[m for m in self.model_inputs[0] if m['role']=='system']
        combined=' '.join(m['content'] for m in facts)
        self.assertIn('uses_windows_default',combined)
        self.assertIn('Google Chrome',combined)
        self.assertIn('native_screenshots',combined)

    def test_native_approval_resumes_original_goal_from_fresh_observation(self):
        original_definitions=self.registry.definitions
        self.registry.definitions=lambda:original_definitions()+[{'type':'function','function':{
            'name':'windows_action','parameters':{'type':'object'}}}]
        original_effect=self.registry.effect
        self.registry.effect=lambda name:'external' if name=='windows_action' else original_effect(name)
        def execute(name,args,context):
            context['refs']['control_1']={'kind':'native_control','value':'private-target'}
            return {'status':'pending','message':'이 입력란을 변경합니다.','pending':{
                'kind':'approval','title':'화면 조작 확인','description':'시험 앱 입력란',
                'operation':'windows_action','origin_user_message_id':context['user_message_id'],
                'args':{'call':dict(args)},'options':[{'id':'approve','label':'실행'},{'id':'cancel','label':'취소'}]}}
        def resolve(pending,option,context):
            context['refs']['control_2']={'kind':'native_control','value':'fresh-target'}
            return {'status':'ok','message':'입력 후 새 화면을 읽었습니다.', 'evidence':True,
                    'continue_assistant':True,'data':{'observation':{'text':'확인할 내용','ref':'control_2'},
                                                     'verification':{'goal_verified':False}}}
        self.registry.execute=execute
        self.registry.resolve_pending=resolve
        runtime=self.runtime([{'tool_calls':[call('windows_action',{'ref':'control_1','operation':'set_value','value':'확인할 내용'})]},
                              {'content':'새 화면에서 입력한 내용이 보입니다.'}])
        first=self.command(runtime,message='시험 앱 입력란에 확인할 내용이라고 입력해줘')
        pending=first['assistant_actions'][0]
        result=runtime.perform('assistant-action',{'request_id':uuid.uuid4().hex,'pending_id':pending['id'],'option_id':'approve'})
        self.assertEqual(len(self.model_inputs),2)
        messages=self.model_inputs[1]
        self.assertEqual(messages[-1]['role'],'tool')
        self.assertEqual(messages[-1]['tool_name'],'windows_action')
        self.assertIn('control_2',messages[-1]['content'])
        self.assertTrue(any(m['role']=='user' and '시험 앱 입력란' in m['content'] for m in messages))
        self.assertIn('입력한 내용이 보입니다',result['result']['answer'])
    def test_no_tool_help_answer_is_retained_without_claiming_execution(self):
        response = self.command(self.runtime([{'content': '앱 실행, 바로가기 등록, 업무 기록을 도와드릴 수 있습니다. 무엇을 해드릴까요?'}]), message='무슨 기능이 있어?')
        self.assertIn('무엇을 해드릴까요', response['result']['answer'])
        self.assertIn('앱 실행', response['messages'][-1]['content'])
        self.assertFalse(response['result']['launcher_changed'])
        self.assertEqual(self.registry.calls, [])
    def test_no_tool_negation_confirmation_is_retained(self):
        response = self.command(self.runtime([{'content': '알겠습니다. 프로그램을 실행하지 않았습니다.'}]), message='프로그램 실행하지 마')
        self.assertIn('실행하지 않았습니다', response['result']['answer'])
        self.assertEqual(self.registry.calls, [])
    def test_replay_and_conflicting_id(self):
        runtime = self.runtime([{'tool_calls': [call('apps'), call('add', {'ref': 'app:1'})]}, {}])
        key = uuid.uuid4().hex
        first = self.command(runtime, request_id=key)
        self.assertEqual(first, self.command(runtime, request_id=key))
        self.assertEqual(len(self.registry.calls), 2)
        with self.assertRaises(ValueError):
            self.command(runtime, message='다른 요청', request_id=key)

    def test_model_repeated_external_call_is_dispatched_once_per_request(self):
        runtime = self.runtime([{'tool_calls': [call('apps')]},
            {'tool_calls': [call('launch', {'ref': 'app:1'})]},
            {'tool_calls': [call('launch', {'ref': 'app:1'})]}, {}])
        self.command(runtime)
        self.assertEqual(sum(name == 'launch' for name, _, _ in self.registry.calls), 1)
    def test_external_crash_never_replays(self):
        runtime = self.runtime([{'tool_calls': [call('crash')]}])
        key = uuid.uuid4().hex
        with self.assertRaises(SystemExit): self.command(runtime, request_id=key)
        with self.assertRaisesRegex(ValueError, '처리|확인'):
            self.command(runtime, request_id=key)
        self.assertEqual(len(self.registry.calls), 1)
    def test_pending_survives_reload_and_accepts_only_saved_options(self):
        runtime = self.runtime([{'tool_calls': [call('approval')]}])
        response = self.command(runtime)
        pending = response['assistant_actions'][0]
        self.assertEqual(pending['message_id'], response['messages'][-1]['id'])
        self.assertNotIn('private_target', pending)
        self.assertEqual(WorkAssistant(self.db, lambda: {}, None).overview()['assistant_actions'], response['assistant_actions'])
        with self.assertRaises(ValueError):
            runtime.perform('assistant-action', {'request_id': uuid.uuid4().hex, 'pending_id': pending['id'], 'option_id': 'forged'})
        resolved = runtime.perform('assistant-action', {'request_id': uuid.uuid4().hex, 'pending_id': pending['id'], 'option_id': 'yes'})
        self.assertEqual(resolved['assistant_actions'], [])
        self.assertTrue(resolved['result']['launcher_changed'])
    def test_native_integration_does_not_fall_back_to_old_classifier(self):
        work = WorkAssistant(self.db, lambda: {}, lambda *a: self.fail('legacy classifier called'),
            native_model=lambda m, t: {'tool_calls': [call('choice')]}, registry_factory=lambda: self.registry)
        self.assertEqual(len(self.command(work)['assistant_actions']), 1)
    def test_round_limit_is_bounded(self):
        response = self.command(self.runtime([{'tool_calls': [call('apps')]}] * 10))
        self.assertLessEqual(len(self.registry.calls), 6)
        self.assertIn('한도', response['result']['message'])
    def test_model_io_holds_no_database_write_transaction(self):
        # An independent writer is able to write during inference.
        def valid_model(messages, tools):
            with contextlib.closing(sqlite3.connect(self.db, timeout=0.1)) as c, c:
                c.execute('INSERT INTO work_notes VALUES (?,?,?,?)', ('note', 'old', '동시 기록', 'time'))
            return {}
        self.command(AssistantRuntime(self.db, valid_model, lambda: self.registry))
        with contextlib.closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM work_notes').fetchone()[0], 1)
    def test_provider_failure_has_no_legacy_execution_and_no_secret_echo(self):
        def fail(*args): raise RuntimeError('secret-api-key')
        service = WorkAssistant(self.db, lambda: {}, lambda *a: self.fail('legacy fallback'),
            native_model=fail, registry_factory=lambda: self.registry)
        response = self.command(service)
        self.assertIn('실패', response['result']['message'])
        self.assertNotIn('secret-api-key', json.dumps(response))
        self.assertEqual(self.registry.calls, [])
    def test_forged_tool_is_rejected_and_reported(self):
        response = self.command(self.runtime([{'tool_calls': [call('shell_exec', {'command': 'anything'})]}, {}]))
        self.assertIn('지원하지 않는 기능', response['result']['message'])
        self.assertEqual(self.registry.calls, [])
    def test_pending_invalid_types_are_rejected_before_database(self):
        runtime = self.runtime([])
        with self.assertRaises(ValueError):
            runtime.perform('assistant-action', {'request_id': uuid.uuid4().hex, 'pending_id': {}, 'option_id': []})
    def test_read_only_final_explanation_and_actual_data_are_retained(self):
        response = self.command(self.runtime([{'tool_calls': [call('apps')]},
            {'content': '설치된 프로그램 목록에서 챗지피티는 보이지 않습니다.'}]))
        self.assertIn('챗지피티는 보이지 않습니다', response['result']['answer'])
        self.assertIn('설명', response['messages'][-1]['content'])
        self.assertEqual(response['result']['tool_results'][0]['data']['ref'], 'app:1')
    def test_read_only_false_mutation_claim_is_suppressed(self):
        response = self.command(self.runtime([{'tool_calls': [call('apps')]}, {'content': '챗지피티를 등록했습니다.'}]))
        self.assertNotIn('등록했습니다', response['result']['message'])
        self.assertFalse(response['result'].get('answer'))
    def test_successful_write_suppresses_read_receipt_in_chat_but_keeps_audit(self):
        response = self.command(self.runtime([{'tool_calls': [call('apps'), call('add', {'ref': 'app:1'})]}, {}]))
        self.assertNotIn('앱 1개를 찾았습니다', response['result']['message'])
        self.assertEqual(len(response['result']['tool_results']), 2)
    def test_history_uses_insertion_order_and_current_user_is_last(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO work_messages VALUES (?,?,?,?)', ('legacy', 'user', '오래된 지역시간 대화', '2099-01-01T12:00:00+09:00'))
        runtime = self.runtime([{}])
        response = self.command(runtime, message='지금 최신 사용자 요청')
        self.assertEqual(self.model_inputs[0][-1]['content'], '지금 최신 사용자 요청')
        self.assertEqual(response['messages'][-2]['content'], '지금 최신 사용자 요청')
        self.assertEqual(response['messages'][-1]['role'], 'assistant')


if __name__ == '__main__': unittest.main()
