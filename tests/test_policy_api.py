import json
import os
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import organizer_policy


class PolicyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.source = self.root / 'inbox'
        self.destination = self.root / 'archive'
        self.source.mkdir()
        self.destination.mkdir()
        self.original = self.source / 'brief.txt'
        self.original.write_bytes(b'generated fixture')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,{'APPDATA':'C:/policy-api-fixture-protected-roaming','LOCALAPPDATA':'C:/policy-api-fixture-protected-local'}).start()
        patch.object(organizer_policy, 'STATE', self.root / 'policy.json').start()
        patch.object(organizer_policy, 'RUNTIME', self.root / 'runtime').start()
        patch.object(organizer_policy, 'ensure_worker').start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, name='policy', data=None, headers=None):
        request = urllib.request.Request(self.base + '/api/desk/' + name,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_policy_get_is_read_only_and_host_guarded(self):
        self.assertIsNone(self.request()['policy'])
        self.assertFalse(organizer_policy.STATE.exists())
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(headers={'Host': 'external.example'})
        self.assertEqual(error.exception.code, 403)

    def test_all_mutations_require_token_and_same_origin(self):
        for name in ('policy-save','policy-preview','policy-run','policy-enabled','policy-undo','policy-resume'):
            for data, headers in (({},{}),({'token':app.DESK_TOKEN},{'Origin':'https://external.example'})):
                with self.subTest(name=name), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(name, data, headers)
                self.assertEqual(error.exception.code, 403)
        self.assertFalse(organizer_policy.STATE.exists())
        self.assertEqual(self.original.read_bytes(), b'generated fixture')

    def test_save_preview_and_pause_do_not_move_files(self):
        result = self.request('policy-save', {'token':app.DESK_TOKEN,
            'source_dir':str(self.source),'destination_dir':str(self.destination),
            'exclusions':[], 'auto_enabled':False, 'authorized':True})
        self.assertFalse(result['policy']['auto_enabled'])
        result = self.request('policy-preview', {'token':app.DESK_TOKEN})
        self.assertTrue(result['jobs'])
        self.assertEqual(self.original.read_bytes(), b'generated fixture')
        paused = self.request('policy-enabled', {'token':app.DESK_TOKEN,'enabled':False})
        self.assertFalse(paused['policy']['auto_enabled'])

    def test_malformed_request_cannot_inject_journal_paths(self):
        for name in ('policy-run','policy-undo','policy-resume'):
            with self.subTest(name=name), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(name, {'token':app.DESK_TOKEN,'id':'../Desktop','journal':str(self.original)})
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.original.read_bytes(), b'generated fixture')

    def test_legacy_execution_cannot_bypass_new_scope(self):
        request = urllib.request.Request(self.base + '/api/execute-files',
            data=json.dumps({'token':'legacy'}).encode(), headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=4)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.original.read_bytes(), b'generated fixture')
