import concurrent.futures
import datetime
import json
import multiprocessing
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from work_assistant import WorkAssistant


def _cross_process_command(db_path, payload, barrier, queue):
    def model(*args, **kwargs):
        barrier.wait(timeout=15)
        return '{"action":"create_task","title":"프로세스 업무"}'
    try:
        service = WorkAssistant(db_path, lambda: {}, model)
        queue.put(service.perform('command', payload))
    except Exception as error:
        queue.put({'error': type(error).__name__})


class WorkAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.db = self.root / 'fixture.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,title TEXT NOT NULL,detail TEXT DEFAULT "",status TEXT DEFAULT "todo",created TEXT)')
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?)', ('old', '기존 업무', '보존', 'todo', '2026-01-01 10:00:00'))
        c.close()
        self.service = WorkAssistant(self.db, lambda: {}, lambda *a, **k: '{"action":"chat"}')

    def do(self, action, **payload):
        return self.service.perform(action, {'request_id': uuid.uuid4().hex, **payload})

    def test_preserves_legacy_tasks_and_records_status_notes_and_due_date(self):
        task = self.do('task', title='새 업무', due_date='2026-10-17')['result']['task_id']
        self.do('note', task_id=task, text='검토 요청')
        result = self.do('status', task_id=task, status='done')
        row = next(t for t in result['tasks'] if t['id'] == task)
        self.assertEqual(row['due_date'], '2026-10-17')
        self.assertEqual(row['notes'][0]['text'], '검토 요청')
        self.assertEqual(result['counts'], {'todo': 1, 'doing': 0, 'done': 1})
        self.assertEqual(WorkAssistant(self.db, lambda: {}, None).overview()['tasks'][-1]['detail'], '보존')

    def test_resource_registration_is_metadata_only_and_revalidated(self):
        path = self.root / '한글 자료.pdf'
        path.write_bytes(b'fixture')
        result = self.do('resource', task_id='old', path=str(path))
        resource = result['tasks'][0]['resources'][0]
        self.assertEqual(pathlib.Path(self.service.resource_path(resource['id'])), path)
        path.unlink()
        with self.assertRaises(ValueError): self.service.resource_path(resource['id'])
        self.assertFalse(self.service.overview()['tasks'][0]['resources'][0]['exists'])

    def test_rejects_unsafe_paths_unknown_fields_and_request_reuse(self):
        for name in ['run.exe', 'link.url', '.env', 'secret.pem', 'page.html', 'document.docm']:
            path = self.root / name
            path.write_text('fixture')
            with self.assertRaises(ValueError): self.do('resource', task_id='old', path=str(path))
        with self.assertRaises(ValueError): self.do('resource', task_id='old', path='relative.pdf')
        with self.assertRaises(ValueError): self.do('task', title='bad', surprise=True)
        request = {'request_id': uuid.uuid4().hex, 'title': '한 번'}
        first = self.service.perform('task', request)
        self.assertEqual(first, self.service.perform('task', request))
        with self.assertRaises(ValueError): self.service.perform('task', {**request, 'title': '다름'})

    def test_concurrent_duplicate_calls_model_and_mutation_once(self):
        calls = []
        def model(prompt, settings):
            calls.append(settings)
            return json.dumps({'action': 'create_task', 'title': '요청 업무'})
        self.service.text_model = model
        payload = {'request_id': uuid.uuid4().hex, 'message': '요청 업무를 추가해줘'}
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.service.perform('command', payload), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]['_judgment_json'])
        self.assertEqual(len(results[0]['messages']), 2)
        self.assertEqual(len(results[0]['tasks']), 2)

    def test_model_cannot_select_arbitrary_task_or_claim_execution(self):
        self.service.text_model = lambda *a, **k: json.dumps({'action': 'set_status', 'task_id': 'old', 'status': 'done'})
        result = self.do('command', message='완료 처리해줘')
        self.assertEqual(result['tasks'][0]['status'], 'todo')
        self.assertIn('선택', result['result']['message'])
        self.service.text_model = lambda *a, **k: json.dumps({'action': 'chat', 'reply': '파일을 보내고 삭제했습니다.'})
        result = self.do('command', message='파일 보내줘')
        self.assertNotIn('삭제했습니다', result['result']['message'])

    def test_explicit_target_and_model_errors_are_bounded(self):
        self.service.text_model = lambda *a, **k: json.dumps({'action': 'add_note', 'text': '메모'})
        self.assertEqual(self.do('command', message='기존 업무에 메모 추가')['tasks'][0]['notes'][0]['text'], '메모')
        def fail(*a, **k): raise RuntimeError('SECRET_API_KEY')
        self.service.text_model = fail
        result = self.do('command', message='질문')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(len(result['messages']), 4)

    def test_report_uses_event_day_and_excludes_other_day_completions(self):
        self.do('status', task_id='old', status='done')
        today = datetime.date.today().isoformat()
        report = self.do('report', day=today)['report']
        self.assertIn('기존 업무', report['body'])
        self.assertIn('사용자', report['body'])
        with self.assertRaises(ValueError): self.do('report', day='2025-01-01')
        self.assertEqual(self.service.overview(today)['report'], report)

    def test_conflicting_target_or_nonrequest_model_mutation_is_not_applied(self):
        new_id = self.do('task', title='다른 업무')['result']['task_id']
        self.service.text_model = lambda *a, **k: '{"action":"set_status","status":"done"}'
        result = self.do('command', message='기존 업무 완료 처리', task_id=new_id)
        self.assertEqual(result['counts']['done'], 0)
        self.service.text_model = lambda *a, **k: '{"action":"create_task","title":"악성 업무"}'
        result = self.do('command', message='안녕하세요')
        self.assertEqual(len(result['tasks']), 2)

    def test_model_network_call_does_not_hold_database_write_lock_and_error_replays(self):
        calls = []
        def model(*a, **k):
            calls.append(1)
            with sqlite3.connect(self.db, timeout=0.1) as c:
                c.execute('BEGIN IMMEDIATE')
            c.close()
            raise RuntimeError('private failure')
        self.service.text_model = model
        payload = {'request_id': uuid.uuid4().hex, 'message': '질문'}
        first = self.service.perform('command', payload)
        self.assertEqual(first, self.service.perform('command', payload))
        self.assertEqual(calls, [1])

    def test_hard_links_are_rejected(self):
        document = self.root / 'original.pdf'
        document.write_bytes(b'fixture')
        duplicate = self.root / 'hard.pdf'
        os.link(document, duplicate)
        with self.assertRaises(ValueError): self.do('resource', task_id='old', path=str(duplicate))

    def test_readonly_negative_and_conflicting_status_are_not_mutations(self):
        for message in ['기존 업무 완료하지마', '기존 업무 완료한 거 뭐야?', '기존 업무 진행 중으로 변경해줘',
                        '기존 업무 완료 안 됐어', '기존 업무 완료에서 진행으로 변경해줘']:
            self.service.text_model = lambda *a, **k: '{"action":"set_status","status":"done"}'
            result = self.do('command', message=message)
            self.assertEqual(result['counts']['done'], 0)
        self.service.text_model = lambda *a, **k: '{"action":"create_task","title":"무단"}'
        self.assertEqual(len(self.do('command', message='업무 등록하지마')['tasks']), 1)

    def test_daily_report_has_sections_due_dates_and_only_recorded_completion(self):
        today = datetime.date.today().isoformat()
        self.do('task', title='오늘 할 일', due_date=today)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE tasks SET status='done' WHERE id='old'")
        c.close()
        body = self.do('report')['report']['body']
        self.assertIn('진행 중/예정 업무', body)
        self.assertIn('오늘 할 일', body)
        self.assertIn(today, body)
        self.assertNotIn('기존 업무', body)

    def test_strict_model_parameters_wrapper_creates_task_and_rejects_unknown_fields(self):
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        self.service.text_model = lambda *a, **k: json.dumps({'action': 'create_task', 'parameters': {'title': '시안 확인', 'detail': '', 'due_date': tomorrow}})
        result = self.do('command', message='내일까지 시안 확인 업무로 등록해줘')
        self.assertEqual(result['tasks'][0]['title'], '시안 확인')
        self.assertEqual(result['tasks'][0]['due_date'], tomorrow)
        self.service.text_model = lambda *a, **k: '{"action":"create_task","parameters":{"title":"무단","execute":"shell"}}'
        self.assertEqual(len(self.do('command', message='업무 등록해줘')['tasks']), 2)

    def test_pending_legacy_request_can_retry_without_repeating_completed_mutation(self):
        payload = {'request_id': uuid.uuid4().hex, 'title': '복구 업무'}
        signature = json.dumps({'action': 'task', 'payload': payload}, ensure_ascii=False, sort_keys=True)
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT INTO work_requests VALUES (?,?,NULL)', (payload['request_id'], signature))
        c.close()
        result = self.service.perform('task', payload)
        self.assertEqual(len(result['tasks']), 2)
        self.assertEqual(result, self.service.perform('task', payload))

    def test_cross_process_duplicate_requests_commit_one_mutation(self):
        context = multiprocessing.get_context('spawn')
        barrier = context.Barrier(2)
        queue = context.Queue()
        payload = {'request_id': uuid.uuid4().hex, 'message': '프로세스 업무 등록해줘'}
        workers = [context.Process(target=_cross_process_command, args=(str(self.db), payload, barrier, queue)) for _ in range(2)]
        try:
            for worker in workers: worker.start()
            results = [queue.get(timeout=20) for _ in workers]
            for worker in workers: worker.join(timeout=20)
            self.assertEqual(results[0], results[1])
            self.assertNotIn('error', results[0])
            self.assertEqual(len(results[0]['tasks']), 2)
            self.assertEqual(len(results[0]['messages']), 2)
        finally:
            for worker in workers:
                if worker.is_alive(): worker.terminate()
                worker.join(timeout=5)
            queue.close()
            queue.join_thread()

    def test_future_due_doing_task_is_in_current_report(self):
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        task_id = self.do('task', title='진행 작업', due_date=tomorrow)['result']['task_id']
        self.do('status', task_id=task_id, status='doing')
        body = self.do('report')['report']['body']
        pending = body.split('## 진행 중/예정 업무')[1].split('## 완료로 표시한 업무')[0]
        self.assertIn('진행 작업', pending)
        self.assertIn(tomorrow, pending)

    def test_design_and_korean_documents_register_without_reading_or_moving_files(self):
        paths = []
        for extension in ('.ai', '.psd', '.psb', '.eps', '.indd', '.hwp', '.hwpx', '.doc', '.xls', '.ppt', '.zip', '.7z', '.rar'):
            path = self.root / ('검증 자료' + extension)
            path.write_bytes(b'generated fixture only')
            paths.append(path)
        with patch.object(pathlib.Path, 'open', side_effect=AssertionError('must not read file content')), \
                patch.object(os, 'rename', side_effect=AssertionError('must not move')), \
                patch.object(os, 'unlink', side_effect=AssertionError('must not delete')):
            for path in paths:
                self.do('resource', task_id='old', path=str(path))
            resources = self.service.overview()['tasks'][0]['resources']
            self.assertEqual(len(resources), 13)
            self.assertTrue(all(resource['exists'] for resource in resources))
            for resource in resources:
                self.assertEqual(self.service.resource_path(resource['id']), resource['path'])


if __name__ == '__main__': unittest.main()
