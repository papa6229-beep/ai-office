import io
import json
import pathlib
import sys
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import app


class GemmaTransportTests(unittest.TestCase):
    def settings(self, **updates):
        values = dict(gemma_url='http://192.168.0.40:11434', gemma_model='gemma4:26b',
                      api_type='ollama', timeout=3, api_key='')
        values.update(updates)
        return values

    def test_configured_remote_ollama_uses_auth_for_text_and_vision(self):
        for callback, args in [(app.call_gemma, ('selected text',)), (app.call_gemma_vision, ('selected image', 'base64-image'))]:
            for key in ['', 'test-private-key']:
                settings = self.settings(); settings['api_key'] = key
                with self.subTest(callback=callback.__name__, key_present=bool(key)), patch.object(app, 'config', return_value=settings), patch('urllib.request.build_opener') as build:
                    build.return_value.open.return_value = io.BytesIO(b'{"message":{"content":"ok"}}')
                    self.assertEqual(callback(*args), 'ok')
                    req = build.return_value.open.call_args.args[0]
                    self.assertEqual(req.full_url, 'http://192.168.0.40:11434/api/chat')
                    self.assertEqual(req.get_header('Authorization'), 'Bearer ' + key if key else None)
                    payload = json.loads(req.data)
                    if callback is app.call_gemma_vision:
                        self.assertEqual(payload['messages'][0]['images'], ['base64-image'])

    def test_openai_text_compatibility_and_vision_rejection(self):
        settings = self.settings(); settings.update(gemma_url='https://gemma.internal/v1', api_type='openai', api_key='test-key')
        with patch.object(app, 'config', return_value=settings), patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
            self.assertEqual(app.call_gemma('selected text'), 'ok')
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, 'https://gemma.internal/v1/chat/completions')
            self.assertEqual(req.get_header('Authorization'), 'Bearer test-key')
            with self.assertRaises(ValueError):app.call_gemma_vision('image', 'payload')
            self.assertEqual(build.call_count, 1)

    def test_analysis_uses_its_initial_settings_snapshot(self):
        selected = self.settings(gemma_url='http://192.168.0.40:11434', api_key='selected-key')
        changed = self.settings(gemma_url='http://192.168.0.99:11434', api_key='changed-key')
        for callback, args in [(app.call_gemma, ('selected text', selected)),
                               (app.call_gemma_vision, ('selected image', 'base64-image', selected))]:
            with self.subTest(callback=callback.__name__), patch.object(app, 'config', return_value=changed), patch('urllib.request.build_opener') as build:
                build.return_value.open.return_value = io.BytesIO(b'{"message":{"content":"ok"}}')
                self.assertEqual(callback(*args), 'ok')
                req = build.return_value.open.call_args.args[0]
                self.assertEqual(req.full_url, 'http://192.168.0.40:11434/api/chat')
                self.assertEqual(req.get_header('Authorization'), 'Bearer selected-key')

    def test_judgment_options_are_scoped_to_call_snapshot(self):
        for api_type in ['ollama', 'openai']:
            selected = self.settings(api_type=api_type, _judgment_json=True, _judgment_max_tokens=1024, _judgment_think=False)
            original = dict(selected)
            reply = b'{"choices":[{"message":{"content":"ok"}}]}' if api_type=='openai' else b'{"message":{"content":"ok"}}'
            with self.subTest(api_type=api_type), patch('urllib.request.build_opener') as build:
                build.return_value.open.return_value = io.BytesIO(reply)
                self.assertEqual(app.call_gemma('JSON only', selected), 'ok')
                payload = json.loads(build.return_value.open.call_args.args[0].data)
                if api_type=='openai':
                    self.assertEqual(payload['response_format'], {'type':'json_object'})
                    self.assertEqual(payload['max_tokens'], 1024)
                    self.assertNotIn('think', payload)
                else:
                    self.assertEqual(payload['format'], 'json')
                    self.assertEqual(payload['options']['num_predict'], 1024)
                    self.assertIs(payload['think'], False)
                self.assertEqual(selected, original)

    def test_regular_calls_do_not_enable_judgment_options(self):
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(b'{"message":{"content":"ok"}}')
            app.call_gemma('ordinary chat', self.settings())
            payload = json.loads(build.return_value.open.call_args.args[0].data)
            self.assertNotIn('format', payload)
            self.assertNotIn('num_predict', payload['options'])
            self.assertNotIn('think', payload)

    def test_unsafe_endpoint_never_opens_connection_or_echoes_credentials(self):
        for url in ['file:///secret', 'http://user:secret@server', 'http://server:bad']:
            settings = self.settings(); settings['gemma_url'] = url
            with self.subTest(url=url), patch.object(app, 'config', return_value=settings), patch('urllib.request.build_opener') as build:
                for callback, args in [(app.call_gemma, ('private text',)), (app.call_gemma_vision, ('private image', 'payload'))]:
                    with self.assertRaises(ValueError) as error:callback(*args)
                    self.assertNotIn('secret', str(error.exception))
                build.assert_not_called()

    def test_redirects_never_reach_destination_for_text_or_vision(self):
        requests = []
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, self.headers.get('Authorization')))
                self.send_response(307); self.send_header('Location', '/collect'); self.end_headers()
            def log_message(self, *args):pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        settings = self.settings(); settings.update(gemma_url=f'http://127.0.0.1:{server.server_port}', api_key='test-key')
        try:
            with patch.object(app, 'config', return_value=settings):
                for callback, args in [(app.call_gemma, ('private text',)), (app.call_gemma_vision, ('private image', 'payload'))]:
                    with self.assertRaises(urllib.error.HTTPError):callback(*args)
            self.assertEqual(requests, [('/api/chat', 'Bearer test-key'), ('/api/chat', 'Bearer test-key')])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':unittest.main()
