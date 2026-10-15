import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import desktop_preferences
import desktop_workspace
import updater


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.desktop = self.root / 'Desktop'
        self.desktop.mkdir()
        self.file = self.desktop / 'brief.txt'
        self.file.write_text('work brief', encoding='utf-8')
        self.launcher = self.desktop / 'Design tool.lnk'
        self.launcher.write_bytes(b'fixture shortcut')
        self.preferences = self.root / 'desktop_state.json'
        self.addCleanup(patch.stopall)
        patch.object(desktop_workspace, '_desktop_roots', return_value=[self.desktop]).start()
        patch.object(desktop_preferences, 'STATE', self.preferences).start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path='/api/desk', data=None, headers=None):
        req = urllib.request.Request(self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_inventory_is_read_only_and_pins_persist(self):
        original = self.file.read_bytes()
        data = self.request()
        self.assertEqual(data['total'], 2)
        self.assertFalse(self.preferences.exists())
        selected = next(x for x in data['items'] if x['name'] == 'brief.txt')
        pins = self.request('/api/desk/pin', {'id': selected['id'], 'enabled': True, 'token': data['action_token']})
        self.assertEqual(pins['pinned_ids'], [selected['id']])
        self.assertEqual(self.request()['pinned_ids'], [selected['id']])
        self.assertEqual(self.file.read_bytes(), original)
        self.assertFalse(self.preferences.with_suffix('.json.tmp').exists())

    def test_open_requires_action_token_and_same_origin(self):
        data = self.request()
        selected = next(x for x in data['items'] if x['kind'] == 'app')
        with patch.object(desktop_workspace.os, 'startfile', create=True) as launch:
            for payload, headers in [({'id': selected['id']}, {}),
                    ({'id': selected['id'], 'token': data['action_token']}, {'Origin': 'https://external.example'})]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/api/desk/open', payload, headers)
                self.assertEqual(error.exception.code, 403)
            launch.assert_not_called()
            self.request('/api/desk/open', {'id': selected['id'], 'token': data['action_token']}, {'Origin': self.base})
            launch.assert_called_once_with(str(self.launcher))

    def test_inventory_rejects_foreign_host_and_pin_rejects_unknown_item(self):
        for endpoint in ['/api/desk','/api/desk/thumbnail?id=unknown']:
            with self.subTest(endpoint=endpoint), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(endpoint, headers={'Host': 'external.example'})
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/desk/pin', {'id': 'f' * 64, 'enabled': True, 'token': app.DESK_TOKEN})
        self.assertEqual(error.exception.code, 400)
        self.assertFalse(self.preferences.exists())

    def test_corrupt_preferences_are_never_overwritten(self):
        original = b'{invalid preferences'
        self.preferences.write_bytes(original)
        with self.assertRaises(ValueError):
            desktop_preferences.pin('a' * 64, True)
        self.assertEqual(self.preferences.read_bytes(), original)

    def test_preferences_participate_in_update_backup_and_recovery(self):
        original = '{"pinned_ids":["favorite"]}'
        self.preferences.write_text(original, encoding='utf-8')
        with patch.object(updater, 'ROOT', self.root), patch.object(updater, 'BACKUPS', self.root / 'backups'):
            folder = updater.backup()
            self.assertEqual((folder / 'desktop_state.json').read_text(encoding='utf-8'), original)
            self.preferences.write_text('{"pinned_ids":[]}', encoding='utf-8')
            updater.restore(folder)
            self.assertEqual(self.preferences.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
