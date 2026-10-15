import pathlib
import concurrent.futures
import contextlib
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import windows_app_catalog as apps
from work_assistant import WorkAssistant
from quick_launcher import QuickLauncher


class ChatLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / 'office.db'
        apps.clear_cache()
        self.rows = [{'Name': 'ChatGPT', 'AppID': 'OpenAI.Codex_123!App'},
                     {'Name': 'Adobe Photoshop 2026', 'AppID': 'Photoshop.2026'},
                     {'Name': 'Adobe Illustrator 2026', 'AppID': 'Illustrator.2026'}]
        self.mock = patch.object(apps, '_read_apps', side_effect=lambda: self.rows)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.addCleanup(apps.clear_cache)
        self.model = lambda *a, **k: '{"action":"chat"}'
        self.work = WorkAssistant(self.db, lambda: {}, self.model)

    def command(self, message, **kw):
        return self.work.perform('command', {'request_id': uuid.uuid4().hex, 'message': message, **kw})

    def test_batch_add_aliases_no_model_no_launch_and_no_duplicates(self):
        self.work.text_model = lambda *a, **k: self.fail('launcher must not call model')
        with patch.object(apps, 'launch') as launch:
            p = {'request_id': uuid.uuid4().hex, 'message': '바로가기에 코덱스, 포토샵, 일러스트레이터 추가해줘'}
            result = self.work.perform('command', p)
            self.assertTrue(result['result']['launcher_changed'])
            self.assertEqual(result, self.work.perform('command', p))
            again = self.command('바로가기에 코덱스와 포토샵 그리고 일러스트 추가해줘')
            self.assertFalse(again['result']['launcher_changed'])
            self.assertIn('이미', again['result']['message'])
            launch.assert_not_called()
        self.assertEqual(len(QuickLauncher(self.db).overview()['items']), 3)
        self.assertIn('Codex', [item['title'] for item in QuickLauncher(self.db).overview()['items']])
        self.assertEqual(result['tasks'], [])

    def test_ambiguous_persists_and_requires_allowed_current_option(self):
        self.rows.append({'Name': 'Adobe Photoshop 2025', 'AppID': 'Photoshop.2025'})
        result = self.command('바로가기에 포토샵하고 없는앱 추가해줘')
        self.assertIn('없는앱', result['result']['message'])
        choice = WorkAssistant(self.db, lambda: {}, self.model).overview()['launcher_choices'][0]
        self.assertEqual(len(choice['options']), 2)
        payload = {'request_id': uuid.uuid4().hex, 'choice_id': choice['id'], 'target': 'startapp:' + '0' * 64}
        with self.assertRaises(ValueError): self.work.perform('choose-launcher', payload)
        payload.update(request_id=uuid.uuid4().hex, target=choice['options'][0]['target'])
        chosen = self.work.perform('choose-launcher', payload)
        self.assertTrue(chosen['result']['launcher_changed'])
        self.assertEqual(chosen['launcher_choices'], [])
        self.assertEqual(chosen, self.work.perform('choose-launcher', payload))
        stale = self.work.perform('choose-launcher', {**payload, 'request_id': uuid.uuid4().hex})
        self.assertFalse(stale['result']['launcher_changed'])

    def test_non_imperatives_never_mutate(self):
        for text in ['바로가기에 포토샵 추가하지 마', '바로가기에 포토샵 추가해줘?',
                     '"바로가기에 포토샵 추가해줘"라고 말하면 돼',
                     '나중에 바로가기에 포토샵 추가해줘', '바로가기에 포토샵 추가해줘 라는 업무를 등록해줘',
                     '바로가기에 포토샵 추가하면 좋겠네']:
            self.command(text)
        self.assertEqual(QuickLauncher(self.db).overview()['items'], [])

    def test_atomic_rollback_if_second_save_fails(self):
        original = QuickLauncher._apply
        calls = []
        def fail(service, c, action, payload):
            calls.append(payload)
            if len(calls) == 2: raise ValueError('failure')
            return original(service, c, action, payload)
        with patch.object(QuickLauncher, '_apply', fail):
            with self.assertRaises(ValueError): self.command('바로가기에 코덱스, 포토샵 추가해줘')
        self.assertEqual(QuickLauncher(self.db).overview()['items'], [])
        self.assertEqual(self.work.overview()['messages'], [])

    def test_uninstalled_choice_not_saved(self):
        self.rows.append({'Name': 'Adobe Photoshop 2025', 'AppID': 'Photoshop.2025'})
        choice = self.command('바로가기에 포토샵 추가해줘')['launcher_choices'][0]
        self.rows.clear()
        result = self.work.perform('choose-launcher', {'request_id': uuid.uuid4().hex,
                                  'choice_id': choice['id'], 'target': choice['options'][0]['target']})
        self.assertFalse(result['result']['launcher_changed'])
        self.assertEqual(result['launcher_choices'], [])
        self.assertEqual(QuickLauncher(self.db).overview()['items'], [])

    def test_reverse_word_order_and_connectors(self):
        result = self.command('포토샵이랑 일러스트를 바로가기에 추가해줘')
        self.assertTrue(result['result']['launcher_changed'])
        self.assertEqual(len(QuickLauncher(self.db).overview()['items']), 2)

    def test_same_title_different_identity_is_not_duplicate(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO launcher_items VALUES (?,?,?,?,?,?,?)',
                      (uuid.uuid4().hex, 'Adobe Photoshop 2026', 'web', 'https://example.com', '', None, 0))
        self.command('바로가기에 포토샵 추가해줘')
        self.assertEqual(len(QuickLauncher(self.db).overview()['items']), 2)

    def test_known_registered_exe_and_manual_target_share_identity(self):
        path = r'C:\Program Files\Adobe\Photoshop.exe'
        self.rows[:] = [{'Name': 'Photoshop', 'AppID': path}]
        with contextlib.closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO launcher_items VALUES (?,?,?,?,?,?,?)',
                      (uuid.uuid4().hex, '내 디자인 앱', 'app', path, '', None, 0))
        result = self.command('바로가기에 포토샵 추가해줘')
        self.assertFalse(result['result']['launcher_changed'])
        with contextlib.closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM launcher_items').fetchone()[0], 1)

    def test_fresh_concurrent_requests_do_not_duplicate(self):
        def add(_):
            work = WorkAssistant(self.db, lambda: {}, self.model)
            return work.perform('command', {'request_id': uuid.uuid4().hex, 'message': '바로가기에 코덱스 추가해줘'})
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            results = list(executor.map(add, range(2)))
        self.assertEqual(sum(r['result']['launcher_changed'] for r in results), 1)
        self.assertEqual(len(QuickLauncher(self.db).overview()['items']), 1)

    def test_bounded_and_provider_failure_do_not_add(self):
        self.command('바로가기에 ' + ','.join(['포토샵'] * 11) + ' 추가해줘')
        self.assertEqual(QuickLauncher(self.db).overview()['items'], [])
        with patch.object(apps, '_read_apps', side_effect=OSError()):
            result = self.command('바로가기에 코덱스 추가해줘')
            self.assertFalse(result['result']['launcher_changed'])
        with self.assertRaises(ValueError):
            self.work.perform('choose-launcher', {'request_id': uuid.uuid4().hex, 'choice_id': {}, 'target': 'bad'})


if __name__ == '__main__': unittest.main()
