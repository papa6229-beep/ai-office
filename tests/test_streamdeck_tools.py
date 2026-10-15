import copy
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from assistant_tools import ToolRegistry


class StreamDeckToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ToolRegistry(pathlib.Path(self.temp.name) / 'office.db')
        self.context = {'request_id': '1'*32, 'step_id': '2'*32, 'refs': {}}
        self.rows = [
            {'id': 'a'*64, 'title': 'Example', 'kind': 'web', 'target': 'https://example.com/work?q=private', 'icon': '🌐'},
            {'id': 'b'*64, 'title': 'Other', 'kind': 'web', 'target': 'https://other.example/', 'icon': '🌐'}]
        self.source = {'source': 'g-clicker', 'items': self.rows, 'total': 2, 'profiles': 1,
                       'skipped': {'unsupported': 1}, 'warnings': [], 'truncated': False}

    def source_patch(self):
        return patch('streamdock_import.catalog', side_effect=lambda: copy.deepcopy(self.source))

    def test_list_keeps_targets_and_images_private_and_batch_import_replays(self):
        with self.source_patch():
            listing = self.registry.execute('streamdeck_shortcuts', {}, self.context)
            self.assertNotIn('https://', str(listing))
            ref = listing['data']['collection_ref']
            self.context['step_id'] = '3'*32
            first = self.registry.execute('streamdeck_import', {'ref': ref}, self.context)
            self.assertEqual(first['data']['counts'], {'added': 2, 'existing': 0, 'error': 0})
            self.assertIn('launcher', first['refresh'])
            self.assertEqual(self.registry.execute('streamdeck_import', {'ref': ref}, self.context), first)
            self.assertEqual(len(self.registry.launcher.overview()['items']), 2)
            self.context['step_id'] = '4'*32
            repeated = self.registry.execute('streamdeck_import', {'ref': ref}, self.context)
            self.assertEqual(repeated['data']['counts'], {'added': 0, 'existing': 2, 'error': 0})

    def test_single_selection_and_changed_source_are_bounded(self):
        with self.source_patch():
            listing = self.registry.execute('streamdeck_shortcuts', {}, self.context)
            one = listing['data']['items'][0]['ref']
            self.context['step_id'] = '3'*32
            result = self.registry.execute('streamdeck_import', {'ref': one}, self.context)
            self.assertEqual(result['data']['counts']['added'], 1)
            self.source['items'] = []
            self.context['step_id'] = '4'*32
            stale = self.registry.execute('streamdeck_import', {'ref': listing['data']['collection_ref']}, self.context)
            self.assertEqual(stale['data']['counts']['error'], 2)
            self.assertEqual(len(self.registry.launcher.overview()['items']), 1)

    def test_forged_reference_and_arbitrary_source_paths_rejected(self):
        for args in ({'ref': 'invented'}, {'ref': 'x', 'path': 'C:\\private.json'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.registry.execute('streamdeck_import', args, self.context)

    def test_source_read_failure_has_no_registration(self):
        with patch('streamdock_import.catalog', side_effect=OSError('private-source-path')):
            with self.assertRaises(ValueError) as error:
                self.registry.execute('streamdeck_shortcuts', {}, self.context)
            self.assertNotIn('private-source-path', str(error.exception))
        self.assertEqual(self.registry.launcher.overview()['items'], [])

    def test_large_catalog_keeps_model_output_bounded_and_reports_limit(self):
        self.source['items'] = [dict(self.rows[0], id=str(i), title='Entry '+str(i), target='https://example.com/'+str(i)) for i in range(501)]
        with self.source_patch():
            listing = self.registry.execute('streamdeck_shortcuts', {}, self.context)
        self.assertEqual(listing['data']['total'], 500)
        self.assertEqual(len(listing['data']['items']), 100)
        self.assertTrue(listing['data']['truncated'])
        self.assertEqual(len(self.context['refs'][listing['data']['collection_ref']]['value']), 500)

    def test_individual_failure_does_not_lose_successful_import_receipts(self):
        self.source['items'][1]['target'] = 'javascript:unsafe'
        self.source['skipped'] = {'unsupported': 0, 'invalid': 0, 'duplicate': 0}
        with self.source_patch():
            listing = self.registry.execute('streamdeck_shortcuts', {}, self.context)
            self.context['step_id'] = '3'*32
            result = self.registry.execute('streamdeck_import', {'ref': listing['data']['collection_ref']}, self.context)
        self.assertEqual(result['data']['counts'], {'added': 1, 'existing': 0, 'error': 1})
        self.assertNotIn('원본 항목은 제외', result['message'])

    def test_database_item_failure_keeps_prior_success_and_refresh(self):
        save = self.registry._save
        def flaky(item, context):
            if item['title'] == 'Other':
                raise sqlite3.OperationalError('database is locked')
            return save(item, context)
        with self.source_patch():
            listing = self.registry.execute('streamdeck_shortcuts', {}, self.context)
            self.context['step_id'] = '3'*32
            with patch.object(self.registry, '_save', side_effect=flaky):
                result = self.registry.execute('streamdeck_import', {'ref': listing['data']['collection_ref']}, self.context)
        self.assertEqual(result['data']['counts'], {'added': 1, 'existing': 0, 'error': 1})
        self.assertIn('launcher', result['refresh'])
        self.assertEqual(len(self.registry.launcher.overview()['items']), 1)
        self.assertNotIn('database is locked', str(result))


if __name__ == '__main__':
    unittest.main()
