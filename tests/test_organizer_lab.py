import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import organizer_lab


class OrganizerLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(organizer_lab, 'ROOT', self.root / 'lab').start()

    def test_demo_moves_only_generated_files_and_can_be_undone(self):
        sentinel = self.root / 'my-work.txt'
        sentinel.write_bytes(b'user original')
        job = organizer_lab.create()
        self.assertEqual(job['status'], 'planned')
        self.assertEqual(organizer_lab.latest()['id'], job['id'])
        original = {p.name: p.read_bytes() for p in pathlib.Path(job['source_dir']).iterdir()}
        done = organizer_lab.execute(job['id'])
        self.assertEqual(sum(x['status'] == 'moved' for x in done['items']), 4)
        self.assertEqual(sum(x['status'] == 'skipped' for x in done['items']), 1)
        self.assertEqual(organizer_lab.execute(job['id']), done)
        undone = organizer_lab.undo(job['id'])
        self.assertEqual(undone['status'], 'undone')
        self.assertEqual({p.name: p.read_bytes() for p in pathlib.Path(job['source_dir']).iterdir()}, original)
        self.assertEqual(sentinel.read_bytes(), b'user original')

    def test_ids_and_tampered_roots_cannot_target_user_files(self):
        for value in ('../Desktop', '', 'a' * 31, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                organizer_lab.execute(value)
        job = organizer_lab.create()
        journal = organizer_lab.ROOT / job['id'] / 'journal.json'
        changed = json.loads(journal.read_text(encoding='utf-8'))
        changed['source_dir'] = str(self.root)
        journal.write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaises(ValueError):
            organizer_lab.execute(job['id'])

    def test_latest_read_does_not_create_demo_and_keeps_corrupt_history(self):
        self.assertIsNone(organizer_lab.latest())
        self.assertFalse(organizer_lab.ROOT.exists())
        job = organizer_lab.create()
        journal = organizer_lab.ROOT / job['id'] / 'journal.json'
        journal.write_bytes(b'{corrupt')
        with self.assertRaises(ValueError):
            organizer_lab.latest()
        self.assertEqual(journal.read_bytes(), b'{corrupt')


class OrganizerApiTests(OrganizerLabTests):
    def setUp(self):
        super().setUp()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, endpoint, payload=None, headers=None):
        req = urllib.request.Request(self.base + endpoint,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(req, timeout=4) as response:
            return json.load(response)

    def test_demo_api_requires_token_same_origin_and_trusted_host(self):
        for endpoint in ('organizer-create', 'organizer-execute', 'organizer-undo'):
            for payload, headers in (({}, {}), ({'token': app.DESK_TOKEN}, {'Origin': 'https://other.example'})):
                with self.subTest(endpoint=endpoint), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/api/desk/' + endpoint, payload, headers)
                self.assertEqual(error.exception.code, 403)
        self.assertFalse(organizer_lab.ROOT.exists())
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/desk/organizer', headers={'Host': 'other.example'})
        self.assertEqual(error.exception.code, 403)
        self.assertIsNone(self.request('/api/desk/organizer')['job'])
        job = self.request('/api/desk/organizer-create', {'token': app.DESK_TOKEN})['job']
        done = self.request('/api/desk/organizer-execute', {'id': job['id'], 'token': app.DESK_TOKEN})['job']
        self.assertEqual(sum(x['status'] == 'moved' for x in done['items']), 4)
        self.assertEqual(self.request('/api/desk/organizer')['job']['id'], job['id'])
        result = self.request('/api/desk/organizer-undo', {'id': job['id'], 'token': app.DESK_TOKEN})
        self.assertEqual(result['job']['status'], 'undone')

    def test_supplied_folder_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/desk/organizer-create', {'token': app.DESK_TOKEN, 'source_dir': str(self.root)})
        self.assertEqual(error.exception.code, 400)
        self.assertFalse(organizer_lab.ROOT.exists())
