import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import request_boundary as boundary
import department_tasks
import department_work
import office_context_hook as hook
from assistant_runtime import _compact_messages

THREAD='11111111-1111-7111-8111-111111111111'
REQUEST='a'*32

def user(ident,text):
    return {'id':ident,'type':'userMessage','content':[{'type':'text','text':text}]}

class RequestBoundaryTests(unittest.TestCase):
    def test_history_is_reference_and_current_input_stays_the_only_user(self):
        old=[{'id':'old','role':'user','content':'옛 업무: 파일을 복구해줘'},
             {'id':'answer','role':'assistant','content':'복구 완료'}]
        messages=boundary.history_messages(old)+[{'role':'user','content':'지금 질문: 오늘 날짜만 답해'}]
        self.assertEqual([x['content']for x in messages if x['role']=='user'],['지금 질문: 오늘 날짜만 답해'])
        self.assertIn('옛 업무',messages[0]['content'])
        self.assertFalse(json.loads(messages[0]['content'].split('\n',1)[1])['may_resume_work'])
    def test_history_is_bounded_without_mutating_stored_messages(self):
        rows=[{'id':str(i),'role':'user','content':'가'*4000}for i in range(100)]
        before=json.dumps(rows);result=boundary.history_messages(rows)
        self.assertLess(len(result[0]['content']),34000)
        self.assertEqual(before,json.dumps(rows))
    def test_compactor_preserves_current_goal_not_historical_user_role(self):
        messages=boundary.history_messages([{'role':'user','content':'옛 질문에 답해'}])+[{'role':'user','content':'현재 목표: 노란 별'}]
        for i in range(50):
            messages.extend([{'role':'assistant','content':'단계'},
                             {'role':'tool','tool_name':'read','content':json.dumps({'message':'기록'*1000})}])
        result=_compact_messages(messages)
        self.assertEqual([x['content']for x in result if x['role']=='user'],['현재 목표: 노란 별'])
    def test_answer_closes_automatically_without_user_done_message(self):
        row=boundary.seal_answer({'state':'done','input_revision':3})
        self.assertEqual(row['conversation_state'],'answered')
        self.assertFalse(row['auto_resume'])
        self.assertEqual(row['closed_input_revision'],3)
    def test_terminal_tasks_cannot_reopen_in_place(self):
        for state in ('completed','failed','cancelled'):
            with self.subTest(state=state),self.assertRaises(ValueError):
                boundary.task_lifecycle({'id':'same','state':'working'},{'id':'same','state':state})
    def test_waiting_and_unknown_effects_are_preserved(self):
        self.assertEqual(boundary.task_lifecycle({'state':'waiting'})['next_mode'],'await_input')
        self.assertEqual(boundary.task_lifecycle({'state':'uncertain'})['next_mode'],'reconcile_effects')
        self.assertFalse(boundary.task_lifecycle({'state':'uncertain'})['dispatch_eligible'])
    def test_received_final_is_not_business_success(self):
        r=boundary.task_lifecycle({'state':'completed','verification':{'business_success':'not_independently_verified'}})
        self.assertTrue(r['response_closed'])
        self.assertEqual(r['business_state'],'reported_unverified')
        self.assertEqual(boundary.task_lifecycle({'state':'completed','verification':{'business_success':True}})['business_state'],'verified_complete')
    def test_real_sqlite_terminal_guard_preserves_original_row(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'office.db'
            with closing(department_tasks._db(path))as c:
                old={'id':REQUEST,'state':'completed','result':'기존 성공 결과'}
                c.execute('INSERT INTO office_department_tasks VALUES(?,?,?)',(REQUEST,json.dumps(old),time.time()));c.commit()
                with self.assertRaises(ValueError):department_tasks._put(c,dict(old,state='working'))
                self.assertEqual(json.loads(c.execute('SELECT data FROM office_department_tasks').fetchone()[0]),old)
    def test_discovery_uses_latest_input_only(self):
        data={'thread':{'id':THREAD},'turns':[{'id':'turn','items':[user('old','지난 파일 복구해줘'),user('new','현재 색상만 알려줘')]}]}
        with patch('desktop_chat_bridge.configuration',return_value={'thread_id':THREAD}):refs=list(department_work._refs(data,Path('unused.db')))
        self.assertEqual([x[1]['id']for x in refs],['new'])
    def test_later_history_fetch_does_not_create_old_completed_work(self):
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder)/'office.db'
            with closing(department_tasks._db(path))as c:
                department_work.schema(c)
                c.execute('INSERT INTO office_department_native_cursors VALUES(?,?)',(THREAD,json.dumps({'seen':[],'initialized_at':200.0})));c.commit()
            data={'thread':{'id':THREAD},'turns':[{'id':'old-turn','status':'completed','startedAt':100.0,'items':[user('old','옛 파일 정리해줘')]}]}
            with patch.object(department_work,'registered',return_value={THREAD:{'registry_kind':'agent'}}),patch('desktop_chat_bridge.configuration',return_value={'thread_id':THREAD}):result=department_work.observe_data(path,data)
            self.assertEqual(result['created'],[])
            with closing(sqlite3.connect(path))as c:self.assertEqual(c.execute('SELECT count(*) FROM office_department_tasks').fetchone()[0],0)

class CompactRecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(hook,'DATA',self.base).start();patch.object(hook,'managed',return_value=True).start()
        self.checkpoint={'context_valid':True,'thread_id':THREAD,'request_id':REQUEST,'source':'office_relay','user_message':'当前目标只回答黄色星星','followups':[],'checkpoint_revision':'r1'}
    def compact(self):
        with patch.object(hook,'read_checkpoint',return_value=self.checkpoint):return hook.handle({'hook_event_name':'SessionStart','source':'compact','session_id':THREAD})
    def test_compact_restores_current_goal_and_requires_live_read(self):
        result=self.compact();self.assertIn(self.checkpoint['user_message'],result['hookSpecificOutput']['additionalContext'])
        self.assertTrue(hook._load(self.base/(THREAD+'.fence.json'))['pending'])
        denied=hook.handle({'hook_event_name':'PreToolUse','session_id':THREAD,'tool_name':'exec_command','tool_input':{'cmd':'echo old-task'}})
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'],'deny')
    def test_matching_live_ack_releases_only_current_fence(self):
        self.compact()
        self.assertTrue(hook.acknowledge_live_input({'thread_id':THREAD,'request_id':REQUEST,'current':True,'can_execute':True,'input_id':'new-input','turn_id':'new-turn'}))
        self.assertFalse(hook._load(self.base/(THREAD+'.fence.json'))['pending'])
    def test_stale_different_request_ack_does_not_release_fence(self):
        self.compact()
        released=hook.acknowledge_live_input({'thread_id':THREAD,'request_id':'b'*32,'current':True,'can_execute':True,'input_id':'old-input','turn_id':'old-turn'})
        self.assertFalse(released)
        self.assertTrue(hook._load(self.base/(THREAD+'.fence.json'))['pending'])
    def test_stopped_or_failed_live_read_keeps_fence(self):
        self.compact()
        for extra in ({'current':False},{'stopped':True},{'can_execute':False}):
            value={'thread_id':THREAD,'request_id':REQUEST,'current':True,'can_execute':True,'input_id':'new-input','turn_id':'new-turn',**extra}
            self.assertFalse(hook.acknowledge_live_input(value))
        self.assertTrue(hook._load(self.base/(THREAD+'.fence.json'))['pending'])

if __name__=='__main__':unittest.main()
