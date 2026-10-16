import base64
import concurrent.futures
import contextlib
import io
import json
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

    def test_legacy_items_are_partitioned_without_item_schema_or_value_changes(self):
        with sqlite3.connect(self.db) as c:
            rows = [(uuid.uuid4().hex, f'항목 {i}', 'web', 'https://example.com', '🌐', b'old-image', i) for i in range(29)]
            c.executemany('INSERT INTO launcher_items VALUES (?,?,?,?,?,?,?)', rows)
        overview = QuickLauncher(self.db).overview()
        self.assertEqual([item['page'] for item in overview['items']], [i // 15 + 1 for i in range(29)])
        self.assertEqual(overview['pages'], [{'id': i, 'title': f'{i}페이지'} for i in range(1, 3)])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT * FROM launcher_items ORDER BY position').fetchall(), rows)
            self.assertEqual(len(c.execute('PRAGMA table_info(launcher_items)').fetchall()), 7)

    def test_existing_nine_item_page_assignments_remain_unchanged(self):
        with patch('quick_launcher._PAGE_SIZE', 9):
            ids = [self.save(str(i)) for i in range(14)]
            before = {item['id']: item['page'] for item in self.service.overview()['items']}
        service = QuickLauncher(self.db)
        after = service.overview()
        self.assertEqual(after['page_size'], 15)
        self.assertEqual({item['id']: item['page'] for item in after['items']}, before)
        new_id = service.perform('save', {'request_id': uuid.uuid4().hex, 'title': '추가', 'kind': 'web', 'target': 'https://example.com', 'page': 1})['result']['id']
        self.assertEqual(next(item['page'] for item in service.overview()['items'] if item['id'] == new_id), 1)

    def test_empty_pages_persist_add_page_replays_and_targeted_save_uses_page(self):
        payload = {'request_id': uuid.uuid4().hex}
        first = self.service.perform('add-page', payload)
        self.assertEqual(first, QuickLauncher(self.db).perform('add-page', payload))
        self.assertEqual(first['result']['page'], 2)
        item_id = self.save(page=2)
        self.assertEqual(self.service.overview()['items'][0]['page'], 2)
        self.do('save', id=item_id, title='편집', kind='web', target='https://example.com')
        self.assertEqual(self.service.overview()['items'][0]['page'], 2)
        self.do('remove', id=item_id)
        self.assertEqual(len(QuickLauncher(self.db).overview()['pages']), 2)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT * FROM launcher_item_pages').fetchall(), [])

    def test_existing_twelve_item_pages_are_not_reflowed(self):
        with patch('quick_launcher._PAGE_SIZE', 12):
            for index in range(29):
                self.save(str(index))
            before = {item['id']: item['page'] for item in self.service.overview()['items']}
        after = QuickLauncher(self.db).overview()
        self.assertEqual(after['page_size'], 15)
        self.assertEqual({item['id']: item['page'] for item in after['items']}, before)

    def test_legacy_request_receipt_remains_exact_on_replay(self):
        payload = {'request_id': uuid.uuid4().hex, 'title': '과거', 'kind': 'web', 'target': 'https://example.com'}
        receipt = {'items': [], 'result': {'message': '과거 응답'}}
        signature = json.dumps({'action': 'save', 'payload': payload}, ensure_ascii=False, sort_keys=True)
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT INTO launcher_requests VALUES (?,?,?)', (payload['request_id'], signature, json.dumps(receipt)))
        self.assertEqual(QuickLauncher(self.db).perform('save', payload), receipt)
        self.assertEqual(self.service.overview()['items'], [])

    def test_full_page_spills_without_hidden_items_and_reordering_keeps_mapping(self):
        ids = [self.save(str(i), page=1) for i in range(17)]
        overview = self.service.overview()
        self.assertEqual([i['page'] for i in overview['items']], [1] * 15 + [2] * 2)
        mapping = {item['id']: item['page'] for item in overview['items']}
        updated = self.do('reorder', ids=ids[::-1])
        self.assertEqual([item['id'] for item in updated['items']], ids[::-1])
        self.assertEqual({item['id']: item['page'] for item in updated['items']}, mapping)
        for bad in (0, -1, 3, '1', True, None, [], {}):
            with self.subTest(page=bad), self.assertRaises(ValueError):
                self.save(page=bad)
        self.assertEqual(len(self.service.overview()['items']), 17)

    def test_appearance_preserves_unavailable_target_and_other_values_with_replay(self):
        target = self.root / 'vanished.exe'
        target.write_bytes(b'fixture')
        item_id = self.do('save', title='원본', kind='app', target=str(target), icon='🚀')['result']['id']
        target.unlink()
        before = self.service.overview()['items'][0]
        payload = {'request_id': uuid.uuid4().hex, 'id': item_id, 'title': '새 이름', 'icon': '🎨'}
        response = self.service.perform('appearance', payload)
        self.assertEqual(response, QuickLauncher(self.db).perform('appearance', payload))
        item = response['items'][0]
        for key in ('id', 'kind', 'target', 'position', 'page', 'available'):
            self.assertEqual(item[key], before[key])
        self.assertEqual((item['title'], item['icon']), ('새 이름', '🎨'))
        self.assertFalse(item['available'])
        for extra in ({'target': 'https://evil.example'}, {'page': 2}, {'kind': 'web'}, {'title': ''}, {'icon': 'toolongicon'}, {'icon_data': 'bad'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.do('appearance', **{'id': item_id, 'title': '이름', **extra})
        with self.assertRaises(ValueError):
            self.do('add-page', title='unknown')

    def test_appearance_image_updates_and_removes_without_changing_launch_target(self):
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (16, 16), 'red').save(out, format='PNG')
        data = 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()
        item_id = self.save()
        self.do('appearance', id=item_id, title='이미지', icon_data=data)
        original = self.service.icon_bytes(item_id)
        self.do('appearance', id=item_id, title='이름만')
        self.assertEqual(self.service.icon_bytes(item_id), original)
        self.do('appearance', id=item_id, title='제거', icon_data='')
        with self.assertRaises(ValueError):
            self.service.icon_bytes(item_id)
        self.assertEqual(self.service.overview()['items'][0]['target'], 'https://example.com')

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

    def gif_bytes(self, count=2, size=(16, 16)):
        from PIL import Image
        frames = [Image.new('P', size, index % 2 + 1) for index in range(count)]
        palette = [0, 0, 0, 255, 0, 0, 0, 255, 0] + [0] * (768 - 9)
        for frame in frames:
            frame.putpalette(palette)
        out = io.BytesIO()
        frames[0].save(out, format='GIF', save_all=True, append_images=frames[1:],
                       duration=[80 + index % 2 * 90 for index in range(count)],
                       loop=3, transparency=0, disposal=2, optimize=False)
        return out.getvalue()

    def test_animated_gif_preserves_original_frames_metadata_and_edits(self):
        from PIL import Image
        raw = self.gif_bytes()
        data = 'data:image/gif;base64,' + base64.b64encode(raw).decode()
        payload = {'request_id': uuid.uuid4().hex, 'title': '움직이는 아이콘', 'kind': 'web',
                   'target': 'https://example.com', 'icon_data': data}
        response = self.service.perform('save', payload)
        self.assertEqual(QuickLauncher(self.db).perform('save', payload), response)
        item_id = response['result']['id']
        self.assertEqual(QuickLauncher(self.db).icon_bytes(item_id), (raw, 'image/gif'))
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual((image.n_frames, image.info['loop'], image.info['transparency']), (2, 3, 0))
            colors, durations = [], []
            for index in range(image.n_frames):
                image.seek(index)
                colors.append(image.convert('RGB').getpixel((0, 0)))
                durations.append(image.info['duration'])
            self.assertNotEqual(colors[0], colors[1])
            self.assertEqual(durations, [80, 170])
        self.do('appearance', id=item_id, title='이름 수정')
        self.do('save', id=item_id, title='주소 수정', kind='web', target='https://example.com/new')
        self.assertEqual(self.service.icon_bytes(item_id), (raw, 'image/gif'))
        self.do('appearance', id=item_id, title='재등록', icon_data=data)
        self.assertEqual(self.service.icon_bytes(item_id), (raw, 'image/gif'))
        self.do('appearance', id=item_id, title='제거', icon_data='')
        with self.assertRaises(ValueError):
            self.service.icon_bytes(item_id)

    def test_static_gif_is_preserved_and_wrong_mime_or_truncation_rejected(self):
        from PIL import Image
        raw = self.gif_bytes(count=1)
        item_id = self.save(icon_data='data:image/gif;base64,' + base64.b64encode(raw).decode())
        self.assertEqual(self.service.icon_bytes(item_id), (raw, 'image/gif'))
        png = io.BytesIO()
        Image.new('RGB', (8, 8)).save(png, format='PNG')
        invalid = [('gif', png.getvalue()), ('png', raw), ('gif', b'GIF89a-corrupt;'),
                   ('gif', raw[:-1]), ('gif', self.gif_bytes()[:-25] + b';'),
                   ('gif', self.gif_bytes(size=(1025, 1))), ('gif', b'A' * (512 * 1024 + 1))]
        for mime, value in invalid:
            with self.subTest(mime=mime, size=len(value)), self.assertRaises(ValueError):
                self.save(icon_data=f'data:image/{mime};base64,' + base64.b64encode(value).decode())

    def test_gif_frame_and_decoded_pixel_limits_are_enforced(self):
        for raw in (self.gif_bytes(count=301), self.gif_bytes(count=31, size=(1024, 1024))):
            self.assertLess(len(raw), 512 * 1024)
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                self.save(icon_data='data:image/gif;base64,' + base64.b64encode(raw).decode())

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
