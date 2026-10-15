"""Production HTTP/native-tool integration; no real browser or user documents."""
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import Mock, patch

import app


class BrowserDocumentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(app, 'DB', self.root / 'office.db').start()
        patch.object(app, 'config', return_value={}).start()
        self.legacy = patch.object(app, 'call_gemma', side_effect=AssertionError('legacy classifier invoked')).start()
        self.model = patch.object(app, 'call_assistant_model').start()
        self.vision = patch.object(app, 'call_gemma_vision', return_value='그래프의 막대는 초록색입니다.').start()
        self.browser = Mock()
        self.row = {'tab_id': 'PRIVATE_TAB', 'title': '업무 사이트', 'url': 'https://example.com/work'}
        self.page = dict(self.row, text='검증된 납기는 금요일 오후 3시입니다.', snapshot_id='PRIVATE_SNAPSHOT',
                         links=[{'id': 'PRIVATE_LINK', 'text': '납기 상세', 'url': 'https://example.com/detail'}],
                         query='', match_count=1, truncated=False)
        self.browser.open.return_value = self.row
        self.browser.tabs.return_value = [self.row]
        self.browser.read.return_value = self.page
        self.browser.follow.return_value = dict(self.page, text='상세 내용: 검수 담당은 김검수입니다.')
        self.browser.view.return_value = dict(self.row, image='PRIVATE_BASE64_SCREENSHOT',
                                              masked_inputs=True, captured_at='fixture-time')
        patch.object(app.browser_workspace, 'manager', return_value=self.browser).start()
        # Hide real Windows app inventories and filesystem suggestions from isolated requests.
        patch('quick_launcher.windows_app_catalog.catalog', return_value={
            'available': True, 'items': [], 'warnings': [], 'total': 0}).start()
        patch('quick_launcher._suggestion_roots', return_value=[]).start()
        self.catalog = patch('document_reader.catalog', return_value={'items': [{
            'id': 'fixture-doc', 'title': '납기 메모', 'name': '납기 메모.txt',
            'path': str(self.root / 'PRIVATE_FOLDER' / '납기 메모.txt'), 'extension': '.txt', 'source': '업무 자료'}],
            'total': 1, 'truncated': False, 'warnings': []}).start()
        self.document = patch('document_reader.read', return_value={
            'name': '납기 메모.txt', 'extension': '.txt', 'text': '계약 납기는 10월 22일 목요일입니다.',
            'total_characters': 24, 'matches': [{'text': '계약 납기는 10월 22일 목요일입니다.', 'position': 0}],
            'match_count': 1, 'note': '읽은 본문 범위입니다.', 'search_complete': True,
            'truncated': False, 'next_offset': None}).start()
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
        self.assertFalse(self.thread.is_alive())

    def request(self, route='/api/work/command', payload=None, headers=None):
        request = urllib.request.Request(self.base + route,
            data=json.dumps(payload).encode('utf-8') if payload is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.read()
            error.close()
            raise

    def payload(self, **extra):
        return {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex,
                'message': '사이트를 열어서 내용을 확인해 줘', **extra}

    @staticmethod
    def tool(name, **arguments):
        return {'content': '', 'tool_calls': [{'id': 'call_' + uuid.uuid4().hex,
                                              'name': name, 'arguments': arguments}]}

    @staticmethod
    def data(messages):
        return json.loads(messages[-1]['content'])['data']

    def test_browser_open_read_and_grounded_summary_survive_external_effect(self):
        def model(messages, tools, settings=None):
            if messages[-1]['role'] != 'tool':
                return self.tool('browser_open', url='https://example.com/work')
            if messages[-1]['tool_name'] == 'browser_open':
                return self.tool('browser_read', ref=self.data(messages)['tab']['ref'])
            return {'content': '사이트에서 확인한 납기는 금요일 오후 3시입니다.', 'tool_calls': []}
        self.model.side_effect = model
        payload = self.payload()
        result = self.request(payload=payload)
        self.assertIn('금요일 오후 3시', result['result']['answer'])
        self.assertIn('페이지 내용을 읽었습니다', result['result']['message'])
        self.assertEqual(self.request(payload=payload), result)
        self.browser.open.assert_called_once_with('https://example.com/work')
        self.browser.read.assert_called_once_with('PRIVATE_TAB', '', 0)
        self.legacy.assert_not_called()
        self.assertNotIn('PRIVATE_TAB', json.dumps(result))

    def test_follow_uses_only_observed_link_and_snapshot(self):
        def model(messages, tools, settings=None):
            if messages[-1]['role'] != 'tool':
                return self.tool('browser_tabs')
            name = messages[-1]['tool_name']
            if name == 'browser_tabs':
                return self.tool('browser_read', ref=self.data(messages)['items'][0]['ref'])
            if name == 'browser_read':
                return self.tool('browser_follow', ref=self.data(messages)['links'][0]['ref'])
            return {'content': '상세 페이지의 검수 담당은 김검수입니다.', 'tool_calls': []}
        self.model.side_effect = model
        result = self.request(payload=self.payload())
        self.browser.follow.assert_called_once_with('PRIVATE_TAB', 'PRIVATE_LINK', 'PRIVATE_SNAPSHOT')
        self.assertIn('김검수', result['result']['answer'])
        self.assertNotIn('PRIVATE_LINK', json.dumps(result))
        self.assertNotIn('PRIVATE_SNAPSHOT', json.dumps(result))

    def test_documents_find_read_returns_real_quote_without_private_path(self):
        def model(messages, tools, settings=None):
            if messages[-1]['role'] != 'tool':
                return self.tool('documents_find', query='납기')
            if messages[-1]['tool_name'] == 'documents_find':
                return self.tool('documents_read', ref=self.data(messages)['items'][0]['ref'], query='납기')
            text = self.data(messages)['text']
            return {'content': '문서 원문: ' + text, 'tool_calls': []}
        self.model.side_effect = model
        result = self.request(payload=self.payload(message='납기 메모에서 납기를 찾아줘'))
        self.assertIn('계약 납기는 10월 22일 목요일입니다.', result['result']['answer'])
        self.document.assert_called_once_with(self.catalog.return_value['items'][0]['path'], '납기', 0)
        self.assertNotIn(str(self.root), json.dumps(result, ensure_ascii=False))
        self.browser.open.assert_not_called()

    def test_foreign_origin_and_extra_http_fields_do_not_dispatch(self):
        for payload, headers, status in [
            (self.payload(), {'Origin': 'https://foreign.example'}, 403),
            (self.payload(path='C:/private.txt'), {}, 400),
            (self.payload(script='document.cookie'), {}, 400),
        ]:
            with self.subTest(status=status, headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(payload=payload, headers=headers)
            self.assertEqual(error.exception.code, status)
        self.model.assert_not_called()
        self.browser.open.assert_not_called()
        self.document.assert_not_called()

    def test_forged_tool_refs_extra_javascript_and_paths_never_dispatch(self):
        cases = [('browser_read', {'ref': 'forged'}),
                 ('browser_follow', {'ref': 'forged'}),
                 ('documents_read', {'ref': 'forged'}),
                 ('documents_read', {'ref': 'forged', 'path': 'C:/private.txt'}),
                 ('browser_open', {'url': 'https://example.com', 'javascript': 'document.cookie'})]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                self.model.side_effect = [self.tool(name, **arguments), {'content': '대상을 확인하지 못했습니다.', 'tool_calls': []}]
                result = self.request(payload=self.payload())
                self.assertTrue(any(row['status'] == 'error' for row in result['result']['tool_results']))
        self.browser.open.assert_not_called()
        self.browser.read.assert_not_called()
        self.browser.follow.assert_not_called()
        self.document.assert_not_called()

    def test_vision_uses_private_capture_but_exposes_only_observation(self):
        seen_model_messages = []
        def model(messages, tools, settings=None):
            seen_model_messages.append(json.dumps(messages, ensure_ascii=False))
            if messages[-1]['role'] != 'tool':
                return self.tool('browser_tabs')
            if messages[-1]['tool_name'] == 'browser_tabs':
                return self.tool('browser_view', ref=self.data(messages)['items'][0]['ref'], question='막대 색은?')
            return {'content': self.data(messages)['visual_observation'], 'tool_calls': []}
        self.model.side_effect = model
        result = self.request(payload=self.payload(message='현재 사이트 그래프 색을 봐줘'))
        self.vision.assert_called_once()
        self.assertEqual(self.vision.call_args.args[1], 'PRIVATE_BASE64_SCREENSHOT')
        self.assertIn('초록색', result['result']['answer'])
        self.assertNotIn('PRIVATE_BASE64_SCREENSHOT', json.dumps(result))
        self.assertNotIn('PRIVATE_BASE64_SCREENSHOT', ''.join(seen_model_messages))

    def test_chat_web_tile_launch_then_read_and_replay_opens_once(self):
        # Launcher has its own exact payload schema, without a chat message field.
        self.request('/api/launcher/save', {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex,
            'title': '업무 웹', 'kind': 'web', 'target': 'https://example.com/work', 'icon': '🌐'})
        def model(messages, tools, settings=None):
            if messages[-1]['role'] != 'tool':
                return self.tool('launcher_list')
            name = messages[-1]['tool_name']
            if name == 'launcher_list':
                return self.tool('launcher_launch', ref=self.data(messages)['items'][0]['ref'])
            if name == 'launcher_launch':
                return self.tool('browser_read', ref=self.data(messages)['tab']['ref'])
            return {'content': '업무 웹의 납기는 금요일 오후 3시입니다.', 'tool_calls': []}
        self.model.side_effect = model
        with patch.object(app, 'open_managed_web', side_effect=self.browser.open) as managed:
            payload = self.payload(message='업무 웹 열어서 납기 확인해줘')
            result = self.request(payload=payload)
            self.assertEqual(self.request(payload=payload), result)
            managed.assert_called_once_with('https://example.com/work')
        self.assertIn('금요일 오후 3시', result['result']['answer'])
        self.browser.open.assert_called_once()


if __name__ == '__main__':
    unittest.main()
