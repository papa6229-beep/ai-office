import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from unittest.mock import patch

import app


class WorkApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(app, 'DB', self.root / 'office.db').start()
        patch.object(app, 'config', return_value={'api_type':'ollama','gemma_url':'http://localhost:11434','gemma_model':'gemma4:26b'}).start()
        patch.object(app, 'call_gemma', return_value='{"action":"create_task","title":"시험 행사 준비","detail":"일정 확인"}').start()
        app.db().close()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, route='/api/work', data=None, headers=None):
        req = urllib.request.Request(self.base+route, data=json.dumps(data).encode() if data is not None else None,
                                     headers={'Content-Type':'application/json', **(headers or {})})
        with urllib.request.urlopen(req, timeout=5) as result:
            return json.load(result)

    def write(self, action, **data):
        import uuid
        return self.request('/api/work/'+action, {'token':app.DESK_TOKEN,'request_id':uuid.uuid4().hex,**data})

    def test_overview_has_token_and_preserves_existing_task(self):
        with closing(sqlite3.connect(app.DB)) as conn, conn:
            conn.execute('INSERT INTO tasks VALUES (?,?,?,?,?)', ('a'*32,'기존 업무','기존 설명','done','2024-01-01 10:00:00'))
        data = self.request()
        self.assertEqual(data['action_token'], app.DESK_TOKEN)
        self.assertEqual(data['tasks'][0]['title'], '기존 업무')
        with closing(sqlite3.connect(app.DB)) as conn:
            self.assertEqual(len(conn.execute('PRAGMA table_info(tasks)').fetchall()), 5)

    def test_new_and_legacy_mutations_require_authentication(self):
        for route in ['/api/work/task','/api/work/command','/api/work/report','/api/work/open-resource','/api/task','/api/task/status']:
            with self.subTest(route=route), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(route, {})
            self.assertEqual(error.exception.code, 403)
        for headers in [{'Host':'outside.example'},{'Origin':'https://outside.example'}]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/work/task', {'token':app.DESK_TOKEN,'title':'차단됨','request_id':'b'*32}, headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(headers={'Host':'outside.example'})
        self.assertEqual(error.exception.code, 403)

    def test_workflow_persists_notes_resources_and_report(self):
        data = self.write('task', title='시험 행사 준비', detail='협업 자료 확인')
        task_id = data['result']['task_id']
        folder = self.root/'행사 자료'; folder.mkdir()
        self.write('resource', task_id=task_id, path=str(folder))
        self.write('note', task_id=task_id, text='시안 검토를 요청했습니다.')
        self.write('status', task_id=task_id, status='doing')
        data = self.write('report')
        self.assertIn('시험 행사 준비', data['report']['body'])
        self.assertIn('시안 검토를 요청했습니다.', data['report']['body'])
        current = self.request()
        self.assertEqual(current['tasks'][0]['status'], 'doing')
        self.assertEqual(current['tasks'][0]['resources'][0]['name'], '행사 자료')
        self.assertEqual(current['report']['id'], data['report']['id'])

    def test_chat_idempotency_creates_one_task(self):
        payload = {'token':app.DESK_TOKEN,'request_id':'c'*32,'message':'시험 행사 준비를 업무로 등록해줘'}
        first = self.request('/api/work/command', payload)
        second = self.request('/api/work/command', payload)
        self.assertEqual(first['result']['task_id'], second['result']['task_id'])
        self.assertEqual(len(second['tasks']), 1)

    def test_legacy_task_and_status_use_the_same_event_history(self):
        created = self.request('/api/task', {'token':app.DESK_TOKEN,'request_id':'d'*32,'title':'구형 화면 등록','detail':'보존'})
        task_id = self.request()['tasks'][0]['id']
        self.request('/api/task/status', {'token':app.DESK_TOKEN,'request_id':'e'*32,'id':task_id,'status':'doing'})
        self.assertTrue(created.get('ok'))
        data = self.request()
        self.assertEqual(data['tasks'][0]['status'], 'doing')
        self.assertGreaterEqual(len(data['events']), 2)

    def test_only_registered_resource_can_be_opened(self):
        with patch.object(app.os, 'startfile', create=True) as opened:
            with self.assertRaises(urllib.error.HTTPError):
                self.write('open-resource', resource_id='f'*32)
            opened.assert_not_called()

    def test_new_home_is_served_with_workflow_fragment(self):
        with patch.object(app.organizer_policy, 'ensure_worker'), patch.object(app, 'start_ai_supervisor'):
            with urllib.request.urlopen(self.base+'/', timeout=5) as response:
                page = response.read().decode('utf-8')
        self.assertIn('id="work"', page)
        self.assertNotIn('<!-- WORK_DESK -->', page)
        self.assertIn('/workdesk.js', page)
        self.assertIn('id="desk" class="hide"', page)


if __name__ == '__main__':
    unittest.main()
