import copy
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


class StreamDeckApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.db = pathlib.Path(self.temp.name) / 'office.db'
        patch.object(app, 'DB', self.db).start()
        patch.object(app, 'config', return_value={}).start()
        self.source = {'source': 'g-clicker', 'items': [
            {'id': 'a'*64, 'title': 'Example', 'kind': 'web', 'target': 'https://example.com/?private=yes', 'icon': '🌐'},
            {'id': 'b'*64, 'title': 'Other', 'kind': 'web', 'target': 'https://other.example/', 'icon': '🌐'}],
            'total': 2, 'profiles': 1, 'skipped': {'unsupported': 1}, 'warnings': [], 'truncated': False}
        self.reader = patch('streamdock_import.catalog', side_effect=lambda: copy.deepcopy(self.source)).start()
        self.inputs = []
        self.mode = 'import'
        def model(messages, tools, settings=None):
            self.inputs.append(copy.deepcopy(messages))
            if messages[-1]['role'] != 'tool':
                name, args = 'streamdeck_shortcuts', {}
            elif messages[-1]['tool_name'] == 'streamdeck_shortcuts' and self.mode == 'import':
                name, args = 'streamdeck_import', {'ref': json.loads(messages[-1]['content'])['data']['collection_ref']}
            else:
                return {'content': '목록 중 21개만 유효합니다.', 'tool_calls': []}
            return {'content': '', 'tool_calls': [{'id': 'call_'+uuid.uuid4().hex, 'name': name, 'arguments': args}]}
        patch.object(app, 'call_assistant_model', side_effect=model).start()
        app.db().close()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, route, payload=None, headers=None):
        request = urllib.request.Request(self.base+route,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def payload(self, message='내 지클리커 즐겨찾기를 전부 가져와'):
        return {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex, 'message': message}

    def test_production_native_import_and_duplicate_request_preserve_existing(self):
        payload = self.payload()
        first = self.request('/api/work/command', payload)
        self.assertTrue(first['result']['launcher_changed'])
        self.assertEqual(len(self.request('/api/launcher')['items']), 2)
        calls = self.reader.call_count
        self.assertEqual(self.request('/api/work/command', payload), first)
        self.assertEqual(self.reader.call_count, calls)
        repeated = self.request('/api/work/command', self.payload())
        self.assertIn('0개 추가, 2개 기존 유지', repeated['result']['message'])
        self.assertEqual(len(self.request('/api/launcher')['items']), 2)
        tool_outputs = [m['content'] for messages in self.inputs for m in messages if m['role'] == 'tool']
        self.assertNotIn('https://', str(tool_outputs))
        self.assertNotIn('private=yes', str(tool_outputs))

    def test_read_request_does_not_import(self):
        self.mode = 'read'
        result = self.request('/api/work/command', self.payload('스트림덱에 등록된 바로가기 뭐 있어?'))
        self.assertFalse(result['result']['launcher_changed'])
        self.assertEqual(self.request('/api/launcher')['items'], [])
        self.assertIn('Example', result['result']['message'])
        self.assertEqual(result['result']['answer'], '')
        self.assertNotIn('21개', result['result']['message'])

    def test_foreign_origin_cannot_start_import(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/work/command', self.payload(), {'Origin': 'https://foreign.example'})
        self.assertEqual(error.exception.code, 403)
        self.reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
