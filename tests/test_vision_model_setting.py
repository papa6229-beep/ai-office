import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import app


class VisionModelSettingTests(unittest.TestCase):
    def settings(self):
        return {'gemma_url': 'http://127.0.0.1:11434', 'gemma_model': 'text-only',
                'api_type': 'ollama', 'timeout': 5, 'api_key': 'fixture-private-key'}

    def payload(self, settings, vision=True):
        with patch('app.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(
                b'{"message":{"content":"fixture answer"}}')
            if vision:
                self.assertEqual(app.call_gemma_vision('describe', 'fixture-image', settings), 'fixture answer')
            else:
                app.call_gemma('hello', settings)
            return json.loads(opener.return_value.open.call_args.args[0].data)

    def test_explicit_vision_model_preserves_main_model_and_image(self):
        settings = {**self.settings(), 'vision_model': 'gemma4:26b'}
        before = dict(settings)
        payload = self.payload(settings)
        self.assertEqual(payload['model'], 'gemma4:26b')
        self.assertEqual(payload['messages'][0]['images'], ['fixture-image'])
        self.assertEqual(settings, before)
        self.assertEqual(self.payload(settings, vision=False)['model'], 'text-only')

    def test_missing_blank_or_whitespace_override_uses_main_model(self):
        for override in (None, '', '   '):
            with self.subTest(override=override):
                settings = self.settings()
                if override is not None:
                    settings['vision_model'] = override
                self.assertEqual(self.payload(settings)['model'], 'text-only')

    def test_ollama_text_defaults_to_nonthinking_with_explicit_override(self):
        self.assertFalse(self.payload(self.settings(), vision=False)['think'])
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                settings = {**self.settings(), '_judgment_think': enabled}
                self.assertIs(self.payload(settings, vision=False)['think'], enabled)

    def test_settings_update_preserves_credentials_and_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = pathlib.Path(directory) / 'fixture-settings.json'
            original = {**self.settings(), 'workspace': 'fixture-workspace', 'custom': {'keep': True}}
            cfg.write_text(json.dumps(original), encoding='utf-8')
            handler = object.__new__(app.Handler)
            handler.path = '/api/settings'
            handler.body = Mock(return_value={'vision_model': 'gemma4:26b'})
            handler.reply = Mock()
            with patch.object(app, 'CFG', cfg):
                app.Handler.do_POST(handler)
                saved = app.config()
            handler.reply.assert_called_once_with({'ok': True})
            self.assertEqual(saved['vision_model'], 'gemma4:26b')
            for key, value in original.items():
                self.assertEqual(saved[key], value)
            self.assertNotIn(original['api_key'], str(handler.reply.call_args))


if __name__ == '__main__':
    unittest.main()
