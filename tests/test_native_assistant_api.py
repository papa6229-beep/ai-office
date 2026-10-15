import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

import app
import windows_app_catalog


class NativeAssistantApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(app, 'DB', self.root / 'office.db').start()
        patch.object(app, 'config', return_value={}).start()
        self.old_model = patch.object(app, 'call_gemma', side_effect=AssertionError('legacy classifier used')).start()
        patch.object(windows_app_catalog, '_read_apps', return_value=[
            {'Name': 'Adobe Photoshop 2026', 'AppID': 'Adobe.Photoshop'},
            {'Name': 'Editor 2025', 'AppID': 'Editor.2025'},
            {'Name': 'Editor 2026', 'AppID': 'Editor.2026'}]).start()
        windows_app_catalog.clear_cache()
        self.addCleanup(windows_app_catalog.clear_cache)
        self.model = patch.object(app, 'call_assistant_model').start()
        app.db().close()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, route='/api/work', payload=None, headers=None):
        request = urllib.request.Request(self.base + route,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def write(self, action='command', **payload):
        return self.request('/api/work/' + action, {'token': app.DESK_TOKEN,
            'request_id': uuid.uuid4().hex, **payload})

    @staticmethod
    def tool(name, **arguments):
        return {'content': '', 'tool_calls': [{'id': 'call_' + uuid.uuid4().hex,
                'name': name, 'arguments': arguments}]}

    def sequence(self, action):
        def model(messages, tools, settings=None):
            if messages[-1]['role'] != 'tool':
                return self.tool('apps_find', query='포샵')
            if messages[-1].get('tool_name') == 'apps_find':
                data = json.loads(messages[-1]['content'])['data']
                ref = next(item['ref'] for item in data['items'] if item['title'].startswith('Adobe Photoshop'))
                return self.tool(action, ref=ref)
            return {'content': '실제로 실행하지 않은 삭제가 완료되었습니다.', 'tool_calls': []}
        self.model.side_effect = model

    def test_free_form_registration_uses_native_tools_and_refreshes_launcher(self):
        self.sequence('launcher_add')
        result = self.write(message='포샵을 내 실행판에 좀 올려줄래?')
        self.assertTrue(result['result']['launcher_changed'])
        self.assertEqual(result['tasks'], [])
        self.assertNotIn('삭제가 완료', result['result']['message'])
        self.assertEqual(self.request('/api/launcher')['items'][0]['title'], 'Adobe Photoshop 2026')
        self.old_model.assert_not_called()

    def test_natural_launch_replay_dispatches_once_without_registering_tile(self):
        self.sequence('apps_launch')
        payload = {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex,
                   'message': '포샵 작업 좀 해야겠어. 열어줄래?'}
        with patch.object(windows_app_catalog, 'launch') as launch:
            first = self.request('/api/work/command', payload)
            self.assertEqual(self.request('/api/work/command', payload), first)
            launch.assert_called_once()
        self.assertEqual(self.request('/api/launcher')['items'], [])
        self.assertIn('열기를 요청', first['result']['message'])

    def test_pending_app_choice_persists_and_uses_only_offered_options(self):
        def model(messages, tools, settings=None):
            if messages[-1]['role'] != 'tool':
                return self.tool('apps_find', query='Editor')
            rows = json.loads(messages[-1]['content'])['data']['items']
            return self.tool('apps_choose', refs=[row['ref'] for row in rows], purpose='add')
        self.model.side_effect = model
        first = self.write(message='편집기 하나 실행판에 넣고 싶은데 어떤 게 있어?')
        action = first['assistant_actions'][0]
        self.assertEqual(self.request()['assistant_actions'], first['assistant_actions'])
        with self.assertRaises(urllib.error.HTTPError):
            self.write('assistant-action', pending_id=action['id'], option_id='invented')
        chosen = self.write('assistant-action', pending_id=action['id'], option_id=action['options'][0]['id'])
        self.assertTrue(chosen['result']['launcher_changed'])
        self.assertEqual(chosen['assistant_actions'], [])
        self.assertEqual(len(self.request('/api/launcher')['items']), 1)

    def test_pending_action_rejects_foreign_requests(self):
        for payload, headers in [({}, {}), ({'token': app.DESK_TOKEN}, {'Origin': 'https://outside.example'})]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/work/assistant-action', payload, headers)
            self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
