import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from unittest.mock import patch

import app
import windows_app_catalog


class ChatLauncherApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(app, 'DB', self.root / 'office.db').start()
        patch.object(app, 'config', return_value={}).start()
        self.model = patch.object(app, 'call_gemma', return_value='{"action":"chat"}').start()
        # Preserve compatibility coverage for the earlier deterministic service.
        patch.object(app, 'work_service', side_effect=lambda: app.work_assistant.WorkAssistant(
            app.DB, app.config, app.call_gemma)).start()
        rows = [{'Name': 'Codex', 'AppID': 'OpenAI.Codex_abc!App'},
                {'Name': 'Adobe Photoshop 2026', 'AppID': 'Adobe.Photoshop'},
                {'Name': 'Adobe Illustrator 2026', 'AppID': 'Adobe.Illustrator'},
                {'Name': 'Editor', 'AppID': 'Editor.2025'},
                {'Name': 'Editor', 'AppID': 'Editor.2026'},
                {'Name': '메모장', 'AppID': 'Microsoft.WindowsNotepad_abc!App'}]
        patch.object(windows_app_catalog, '_read_apps', return_value=rows).start()
        windows_app_catalog.clear_cache()
        self.addCleanup(windows_app_catalog.clear_cache)
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

    def request(self, route, payload=None, headers=None):
        request = urllib.request.Request(self.base + route,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.load(response)

    def write(self, action, **payload):
        return self.request('/api/work/' + action,
            {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex, **payload})

    def test_chat_adds_multiple_registered_apps_and_replays_without_duplicates(self):
        payload = {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex,
                   'message': '바로가기에 코덱스, 포토샵, 일러스트레이터 추가해줘'}
        with patch.object(app.os, 'startfile', create=True) as opened:
            first = self.request('/api/work/command', payload)
            self.assertTrue(first['result']['launcher_changed'])
            self.assertEqual(first['tasks'], [])
            self.assertEqual(self.request('/api/work/command', payload), first)
            repeated = self.write('command', message=payload['message'])
            self.assertFalse(repeated['result']['launcher_changed'])
            opened.assert_not_called()
        self.model.assert_not_called()
        tiles = self.request('/api/launcher')['items']
        self.assertEqual(len(tiles), 3)
        self.assertEqual(len({item['target'] for item in tiles}), 3)
        self.assertTrue(all(item['target'].startswith('startapp:') for item in tiles))

    def test_ambiguous_choice_survives_reload_and_rejects_unoffered_targets(self):
        result = self.write('command', message='바로가기에 Editor 추가해줘')
        choice = result['launcher_choices'][0]
        self.assertEqual(len(choice['options']), 2)
        self.assertEqual(len({option.get('detail', '') for option in choice['options']}), 2)
        self.assertEqual(self.request('/api/work')['launcher_choices'], result['launcher_choices'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.write('choose-launcher', choice_id=choice['id'], target='startapp:' + '0' * 64)
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request('/api/launcher')['items'], [])
        result = self.write('choose-launcher', choice_id=choice['id'], target=choice['options'][1]['target'])
        self.assertTrue(result['result']['launcher_changed'])
        self.assertEqual(result['launcher_choices'], [])
        self.assertEqual(len(self.request('/api/launcher')['items']), 1)

    def test_missing_app_is_not_registered_and_request_is_not_a_task(self):
        result = self.write('command', message='바로가기에 없는시험프로그램 추가해줘')
        self.assertFalse(result['result']['launcher_changed'])
        self.assertEqual(result['tasks'], [])
        self.assertEqual(self.request('/api/launcher')['items'], [])

    def test_korean_registered_name_remains_searchable_with_english_alias(self):
        result = self.write('command', message='바로가기에 메모장 추가해줘')
        self.assertTrue(result['result']['launcher_changed'])
        self.assertEqual(self.request('/api/launcher')['items'][0]['title'], '메모장')

    def test_choice_requires_local_authentication(self):
        for payload, headers in [({}, {}),
                ({'token': app.DESK_TOKEN}, {'Origin': 'https://outside.example'}),
                ({'token': app.DESK_TOKEN}, {'Host': 'outside.example'})]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/work/choose-launcher', payload, headers)
            self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
