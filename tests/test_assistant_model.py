import io
import json
import unittest
from unittest.mock import patch

import assistant_model


class AssistantModelTests(unittest.TestCase):
    def settings(self, kind='ollama'):
        return {'api_type': kind, 'gemma_url': 'http://localhost:11434',
                'gemma_model': 'gemma4:26b', 'timeout': 5, 'api_key': 'fixture-key'}

    def call(self, response, kind='ollama', messages=None):
        self.messages = messages or [{'role': 'user', 'content': '포토샵 좀 켜줄래?'}]
        with patch('assistant_model.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            result = assistant_model.call(self.messages, [], self.settings(kind))
            self.request = opener.return_value.open.call_args.args[0]
            return result

    def test_ollama_returns_native_tool_calls(self):
        result = self.call({'message': {'content': '', 'tool_calls': [
            {'function': {'name': 'apps_find', 'arguments': {'query': '포토샵'}}}]}})
        self.assertEqual(result['tool_calls'][0]['name'], 'apps_find')
        self.assertEqual(result['tool_calls'][0]['arguments'], {'query': '포토샵'})
        payload = json.loads(self.request.data)
        self.assertFalse(payload['stream'])
        self.assertFalse(payload['think'])
        self.assertNotIn('format', payload)
        self.assertEqual(self.request.get_header('Authorization'), 'Bearer fixture-key')

    def test_openai_compatible_arguments_are_decoded_and_history_encoded(self):
        messages = [{'role': 'user', 'content': '등록해줘'},
                    {'role': 'assistant', 'content': '', 'tool_calls': [
                        {'id': 'call_1', 'type': 'function', 'function': {'name': 'apps_find', 'arguments': {'query': '포토샵'}}}]},
                    {'role': 'tool', 'tool_call_id': 'call_1', 'tool_name': 'apps_find', 'content': '{}'}]
        result = self.call({'choices': [{'message': {'content': None, 'tool_calls': [
            {'id': 'call_2', 'function': {'name': 'launcher_add', 'arguments': '{"app_ref":"app_1"}'}}]}}]}, 'openai', messages)
        self.assertEqual(result['tool_calls'][0]['arguments'], {'app_ref': 'app_1'})
        payload = json.loads(self.request.data)
        self.assertIsInstance(payload['messages'][1]['tool_calls'][0]['function']['arguments'], str)
        self.assertNotIn('tool_name', payload['messages'][2])
        self.assertNotIn('think', payload)

    def test_malformed_tool_arguments_are_rejected(self):
        for arguments in ['{broken', '[1]', '"command"', {'x': 'a' * 40000}]:
            with self.subTest(arguments=type(arguments).__name__), self.assertRaises(ValueError):
                self.call({'message': {'tool_calls': [{'function': {'name': 'apps_find', 'arguments': arguments}}]}})

    def test_unsafe_endpoint_is_rejected_before_network(self):
        settings = self.settings()
        settings['gemma_url'] = 'http://user:private-password@localhost'
        with patch('assistant_model.urllib.request.build_opener') as opener:
            with self.assertRaises(ValueError) as error:
                assistant_model.call([{'role': 'user', 'content': 'hello'}], [], settings)
            opener.assert_not_called()
        self.assertNotIn('private-password', str(error.exception))

    def test_network_errors_do_not_expose_secrets(self):
        with patch('assistant_model.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = OSError('fixture-key private details')
            with self.assertRaises(ValueError) as error:
                assistant_model.call([{'role': 'user', 'content': 'hello'}], [], self.settings())
        self.assertNotIn('fixture-key', str(error.exception))


if __name__ == '__main__':
    unittest.main()
