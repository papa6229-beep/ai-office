import base64
import concurrent.futures
import contextlib
import io
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from quick_launcher import QuickLauncher


class QuickLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.db = self.root / 'fixture.db'
        with contextlib.closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,title TEXT)')
            c.execute('INSERT INTO tasks VALUES (?,?)', ('old', '기존 업무'))
        self.addCleanup(patch.stopall)
        patch('quick_launcher._suggestion_roots', return_value=[]).start()
        patch('quick_launcher.windows_app_catalog.catalog', return_value={'available': False, 'items': [], 'warnings': [], 'total': 0}).start()
        self.service = QuickLauncher(self.db)

    def do(self, action, **payload):
        return self.service.perform(action, {'request_id': uuid.uuid4().hex, **payload})

    def save(self, title='검색', **extra):
        return self.do('save', title=title, kind='web', target='https://example.com', **extra)['result']['id']

    def test_crud_persists_without_changing_tasks_or_deleting_target(self):
        target = self.root / '프로그램.exe'
        target.write_bytes(b'not executable fixture')
        item_id = self.do('save', title='프로그램', kind='app', target=str(target), icon='🚀')['result']['id']
        self.assertRegex(item_id, r'^[0-9a-f]{32}$')
        self.do('save', id=item_id, title='새 이름', kind='app', target=str(target))
        item = QuickLauncher(self.db).overview()['items'][0]
        self.assertEqual((item['title'], item['icon'], item['available']), ('새 이름', '🚀', True))
        self.do('remove', id=item_id)
        self.assertTrue(target.exists())
        self.assertEqual(self.service.overview()['items'], [])
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT * FROM tasks').fetchall(), [('old', '기존 업무')])
            self.assertEqual([r[1] for r in c.execute('PRAGMA table_info(tasks)')], ['id', 'title'])

    def test_reorder_requires_each_registered_id_exactly_once(self):
        first, second = self.save('첫째'), self.save('둘째')
        result = self.do('reorder', ids=[second, first])
        self.assertEqual([i['id'] for i in result['items']], [second, first])
        for ids in [[first], [first, first], [first, uuid.uuid4().hex], 'bad']:
            with self.assertRaises(ValueError):
                self.do('reorder', ids=ids)
        self.assertEqual([i['position'] for i in self.service.overview()['items']], [0, 1])

    def test_idempotency_replays_and_rejects_changed_payload(self):
        payload = {'request_id': uuid.uuid4().hex, 'title': '한 번', 'kind': 'web', 'target': 'https://example.com'}
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.service.perform('save', payload), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(QuickLauncher(self.db).overview()['items']), 1)
        with self.assertRaises(ValueError):
            self.service.perform('save', {**payload, 'title': '다름'})

    def test_rejects_untrusted_urls_and_malformed_payloads(self):
        for target in ['javascript:alert(1)', 'data:text/plain,test', 'file:///C:/x', 'chrome://settings',
                       'https://user:pass@example.com', 'https://', 'https://bad host/', 'https://example.com\n/', 'https://example.com/\x7f']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.do('save', title='bad', kind='web', target=target)
        for payload in [{'title': '', 'kind': 'web', 'target': 'https://example.com'},
                        {'title': 'bad', 'kind': 'script', 'target': 'anything'},
                        {'title': 'bad', 'kind': 'web', 'target': 'https://example.com', 'icon': 'https://bad'},
                        {'title': 'bad', 'kind': 'web', 'target': 'https://example.com', 'extra': True}]:
            with self.assertRaises(ValueError):
                self.do('save', **payload)
        with self.assertRaises(ValueError):
            self.service.perform('save', {})

    def test_local_paths_restrict_program_types_and_revalidate_on_launch(self):
        for name in ['run.cmd', 'run.ps1', 'page.url', 'script.py', 'run.exe --unsafe']:
            path = self.root / name
            path.write_bytes(b'fixture')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.do('save', title='bad', kind='app', target=str(path))
        for target in ['relative.exe', r'\\server\share\run.exe', str(self.root / 'missing.exe')]:
            with self.assertRaises(ValueError):
                self.do('save', title='bad', kind='app', target=target)
        target = self.root / 'local.exe'
        target.write_bytes(b'fixture')
        item_id = self.do('save', title='local', kind='app', target=str(target))['result']['id']
        target.unlink()
        self.assertFalse(self.service.overview()['items'][0]['available'])
        with patch('quick_launcher.os.startfile', create=True) as launch:
            with self.assertRaises(ValueError):
                self.do('launch', id=item_id)
            launch.assert_not_called()
        with patch('quick_launcher._safe_route', return_value=False):
            with self.assertRaises(ValueError):
                self.do('save', title='folder', kind='folder', target=str(self.root))

    def test_launch_uses_explicit_saved_item_and_replay_never_opens_twice(self):
        item_id = self.save()
        payload = {'request_id': uuid.uuid4().hex, 'id': item_id}
        with patch('quick_launcher.webbrowser.open', return_value=True) as launch:
            first = self.service.perform('launch', payload)
            self.assertEqual(first, QuickLauncher(self.db).perform('launch', payload))
            self.assertEqual(first['result']['id'], item_id)
            launch.assert_called_once_with('https://example.com')
        with patch('quick_launcher.webbrowser.open', return_value=False):
            with self.assertRaises(ValueError):
                self.do('launch', id=item_id)

    def test_failed_launch_request_is_not_retried(self):
        item_id = self.save()
        payload = {'request_id': uuid.uuid4().hex, 'id': item_id}
        with patch('quick_launcher.webbrowser.open', side_effect=OSError('sensitive path')) as launch:
            for _ in range(2):
                with self.assertRaises(ValueError) as failure:
                    self.service.perform('launch', payload)
                self.assertNotIn('sensitive', str(failure.exception))
            launch.assert_called_once()

    def test_interrupted_launch_reservation_does_not_report_success_or_relaunch(self):
        item_id = self.save()
        payload = {'request_id': uuid.uuid4().hex, 'id': item_id}
        with patch('quick_launcher.webbrowser.open', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.service.perform('launch', payload)
        with patch('quick_launcher.webbrowser.open', return_value=True) as launch:
            with self.assertRaises(ValueError):
                QuickLauncher(self.db).perform('launch', payload)
            launch.assert_not_called()

    def test_invalid_json_value_types_fail_without_mutation(self):
        for payload in [{'title': [], 'kind': 'web', 'target': 'https://example.com'},
                        {'title': 'bad', 'kind': [], 'target': 'https://example.com'},
                        {'title': 'bad', 'kind': 'web', 'target': []},
                        {'title': 'bad', 'kind': 'web', 'target': 'https://example.com', 'icon_data': []},
                        {'id': [], 'title': 'bad', 'kind': 'web', 'target': 'https://example.com'}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.do('save', **payload)
        self.assertEqual(self.service.overview()['items'], [])

    def test_suggestions_never_scan_network_roots(self):
        with patch('quick_launcher._suggestion_roots', return_value=[r'\\server\share']), \
                patch('quick_launcher.pathlib.Path.is_dir', return_value=True), \
                patch('quick_launcher._safe_route', return_value=True), \
                patch('quick_launcher.os.scandir') as scan:
            self.assertEqual(self.service.overview()['suggestions'], [])
            scan.assert_not_called()

    def test_app_and_folder_launch_only_pass_the_registered_path(self):
        app = self.root / 'local app.exe'
        app.write_bytes(b'fixture')
        for kind, target in [('app', app), ('folder', self.root)]:
            item_id = self.do('save', title=kind, kind=kind, target=str(target))['result']['id']
            with patch('quick_launcher.os.startfile', create=True) as launch:
                self.do('launch', id=item_id)
                launch.assert_called_once_with(str(target))

    def test_uploaded_icons_are_validated_and_stored_in_db(self):
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (16, 16), 'red').save(out, format='PNG')
        data = 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()
        item_id = self.save(icon_data=data)
        raw, mime = QuickLauncher(self.db).icon_bytes(item_id)
        self.assertEqual(mime, 'image/png')
        self.assertEqual(Image.open(io.BytesIO(raw)).size, (16, 16))
        self.assertTrue(self.service.overview()['items'][0]['icon_image'])
        self.do('save', id=item_id, title='수정', kind='web', target='https://example.com')
        self.assertTrue(self.service.overview()['items'][0]['icon_image'])
        self.do('save', id=item_id, title='수정', kind='web', target='https://example.com', icon_data='')
        with self.assertRaises(ValueError):
            self.service.icon_bytes(item_id)
        for bad in ['data:image/svg+xml;base64,PHN2Zy8+', 'https://example.com/icon.png',
                    'data:image/png;base64,bm90YW5pbWFnZQ==', 'data:image/png;base64,' + 'A' * 800000]:
            with self.assertRaises(ValueError):
                self.save(icon_data=bad)
        out = io.BytesIO()
        Image.new('RGB', (1025, 1)).save(out, format='PNG')
        with self.assertRaises(ValueError):
            self.save(icon_data='data:image/png;base64,' + base64.b64encode(out.getvalue()).decode())

    def test_suggestions_are_bounded_read_only_and_do_not_read_shortcut_contents(self):
        for index in range(60):
            (self.root / f'App {index}.lnk').write_bytes(b'not parsed')
        (self.root / 'danger.cmd').write_bytes(b'fixture')
        with patch('quick_launcher._suggestion_roots', return_value=[self.root]):
            overview = self.service.overview()
        self.assertEqual(overview['items'], [])
        self.assertEqual(len(overview['suggestions']), 50)
        self.assertTrue(all(i['kind'] == 'app' and i['target'].endswith('.lnk') for i in overview['suggestions']))
        self.assertEqual(self.service.overview()['items'], [])

    def test_registered_start_app_references_are_validated_and_launched(self):
        target = 'startapp:' + 'a' * 64
        with patch('quick_launcher.windows_app_catalog.resolve', return_value='Trusted.App'), \
                patch('quick_launcher.windows_app_catalog.launch') as launch:
            item_id = self.do('save', title='ChatGPT', kind='app', target=target)['result']['id']
            self.do('launch', id=item_id)
            launch.assert_called_once_with(target)
        with patch('quick_launcher.windows_app_catalog.resolve', side_effect=ValueError('missing')):
            self.assertFalse(self.service.overview()['items'][0]['available'])
            with self.assertRaises(ValueError):
                self.do('save', title='Unknown', kind='app', target=target)

    def test_start_catalog_replaces_fallback_and_reports_source(self):
        entries = [{'title': f'App {i}', 'kind': 'app', 'target': 'startapp:' + str(i).zfill(64), 'icon': '🚀'} for i in range(100)]
        with patch('quick_launcher.windows_app_catalog.catalog', return_value={'available': True, 'items': entries, 'warnings': [], 'total': 100}), \
                patch('quick_launcher._suggestion_roots') as fallback:
            result = self.service.overview()
        self.assertEqual(len(result['suggestions']), 100)
        self.assertEqual(result['suggestion_source'], 'windows-start-menu')
        fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
