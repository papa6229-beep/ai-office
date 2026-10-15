import base64
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import streamdock_import as reader


class StreamDockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.target = 'startapp:' + hashlib.sha256(b'actual.app').hexdigest()
        self.catalog_patch = patch.object(reader.windows_app_catalog, 'catalog', return_value={
            'items': [{'title': 'Actual App', 'target': self.target, 'kind': 'app'}]})
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.resolve_patch = patch.object(reader.windows_app_catalog, 'resolve', return_value='actual.app')
        self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)

    def write(self, actions, relative='one.sdProfile'):
        path = self.root / relative / 'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'Actions': actions}), encoding='utf-8')
        return path

    def web(self, url='https://example.com', name='Website'):
        return {'UUID': reader.WEBSITE, 'Name': name, 'Settings': {'path': url}}

    def read(self):
        with patch.object(reader, '_profile_root', return_value=self.root):
            return reader.catalog()

    def test_only_selected_application_and_source_unchanged(self):
        action = {'UUID': reader.OPEN_APP, 'Settings': {'appId': 1, 'applications': [
            {'name': 'Not selected', 'path': 'unknown'}, {'name': 'Actual App', 'path': 'actual.app'}]}}
        path = self.write({'0': action})
        before = path.read_bytes()
        result = self.read()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['target'], self.target)
        self.assertEqual(result['items'][0]['title'], 'Actual App')
        self.assertEqual(path.read_bytes(), before)

    def test_live_nested_profiles_deduplicate_and_ignore_backups(self):
        self.write({'0': self.web()})
        self.write({'0': self.web(), '1': self.web('https://example.org')}, 'one.sdProfile/profiles/two.sdProfile')
        self.write({'0': self.web('https://backup.invalid')}, 'backup/three.sdProfile')
        result = self.read()
        self.assertEqual(result['profiles'], 2)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['skipped']['duplicate'], 1)

    def test_excluded_actions_never_descend_or_expose_settings(self):
        secret = {'UUID': 'password', 'Settings': {'password': 'secret-value'}, 'Actions': {'0': self.web()}}
        composite = {'UUID': 'multiActions', 'Settings': {'Actions': [self.web()]}, 'States': [{'Actions': {'0': self.web()}}]}
        self.write({'0': secret, '1': composite})
        result = self.read()
        self.assertEqual(result['items'], [])
        self.assertNotIn('secret-value', json.dumps(result))
        self.assertEqual(result['skipped']['unsupported'], 2)

    def test_invalid_urls_indices_and_unknown_apps(self):
        actions = {str(i): self.web(url) for i, url in enumerate(['javascript:foo', 'https://user:pass@example.com', 'file:///tmp/x'])}
        for i, index in enumerate([-1, True, '0', 3]):
            actions['app' + str(i)] = {'UUID': reader.OPEN_APP, 'Settings': {'appId': index, 'applications': [{'path': 'actual.app'}]}}
        actions['unknown'] = {'UUID': reader.OPEN_APP, 'Settings': {'appId': 0, 'applications': [{'path': 'unknown.id'}]}}
        self.write(actions)
        self.assertEqual(self.read()['items'], [])

    def test_bounds_corrupt_json_and_unsafe_routes(self):
        path = self.write({'0': self.web()})
        with patch.object(reader, 'MAX_BYTES', 3):
            self.assertTrue(self.read()['warnings'])
        path.write_text('{bad', encoding='utf-8')
        self.assertTrue(self.read()['warnings'])
        with patch.object(reader, '_safe_route', return_value=False), patch.object(pathlib.Path, 'open') as opening:
            self.assertEqual(self.read()['items'], [])
            opening.assert_not_called()

    def test_item_and_directory_bounds(self):
        self.write({str(i): self.web('https://example.com/' + str(i)) for i in range(5)})
        with patch.object(reader, 'MAX_ITEMS', 2):
            result = self.read()
        self.assertEqual(result['total'], 2)
        self.assertTrue(result['truncated'])
        with patch.object(reader, 'MAX_ENTRIES', 0):
            self.assertTrue(self.read()['truncated'])

    def test_stable_ids_revision_changes_with_source(self):
        self.write({'0': self.web()})
        first = self.read()
        self.assertEqual(first, self.read())
        self.write({'0': self.web(name='Renamed')})
        second = self.read()
        self.assertEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertNotEqual(first['revision'], second['revision'])

    def test_safe_raster_icon_and_traversal_rejected(self):
        from PIL import Image
        data = io.BytesIO()
        Image.new('RGBA', (4, 4), 'red').save(data, format='PNG')
        action = self.web()
        action['States'] = [{'Image': 'icon.png'}]
        path = self.write({'0': action})
        (path.parent / 'icon.png').write_bytes(data.getvalue())
        result = self.read()
        self.assertTrue(result['items'][0]['icon_data'].startswith('data:image/png;base64,'))
        action['States'] = [{'Image': '../outside.png'}]
        self.write({'0': action})
        self.assertNotIn('icon_data', self.read()['items'][0])

    def test_generic_title_uses_hostname_without_private_url_parts(self):
        self.write({'0': self.web('https://example.com/private?q=secret'), '1': self.web('https://other.example.com', 'My Site')})
        self.assertEqual([i['title'] for i in self.read()['items']], ['example.com', 'My Site'])

    def test_images_folder_gif_and_relative_windows_path(self):
        from PIL import Image
        action = self.web()
        action['States'] = [{'Image': 'Images\\icon.gif'}]
        path = self.write({'0': action})
        folder = path.parent / 'Images'
        folder.mkdir()
        Image.new('RGB', (4, 4), 'blue').save(folder / 'icon.gif', format='GIF')
        self.assertIn('icon_data', self.read()['items'][0])
        action['States'][0]['Image'] = 'icon.gif'
        self.write({'0': action})
        self.assertIn('icon_data', self.read()['items'][0])

    def test_aggregate_files_bytes_and_nodes_are_bounded(self):
        self.write({'0': self.web()})
        self.write({'0': self.web('https://example.org')}, 'two.sdProfile')
        with patch.object(reader, 'MAX_FILES', 1):
            result = self.read()
            self.assertEqual(result['profiles'], 1)
            self.assertTrue(result['truncated'])
        with patch.object(reader, 'MAX_TOTAL_BYTES', 3):
            self.assertTrue(self.read()['truncated'])
        with patch.object(reader, 'MAX_NODES', 0):
            self.assertTrue(self.read()['truncated'])
            self.assertEqual(self.read()['items'], [])

    def test_malformed_uuid_containers_do_not_abort_valid_actions(self):
        self.write({'0': {'UUID': [], 'Settings': {'password': 'private-value'}, 'Actions': {'0': self.web('https://excluded.example.com')}},
                    '1': {'UUID': {}, 'Settings': {'password': 'private-value'}},
                    '2': self.web()})
        result = self.read()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['skipped']['unsupported'], 2)
        self.assertNotIn('private-value', json.dumps(result))

    def test_lone_surrogate_titles_are_sanitized_without_losing_valid_actions(self):
        self.write({'0': self.web(name='Good\ud800 title'),
                    '1': self.web('https://other.example.com', '\udfff'),
                    '2': {'UUID': reader.OPEN_APP, 'Settings': {'appId': 0, 'applications': [
                        {'name': 'App\udfff', 'path': 'actual.app'}]}}})
        result = self.read()
        self.assertEqual(result['total'], 3)
        self.assertEqual([i['title'] for i in result['items']], ['Good title', 'other.example.com', 'App'])
        json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.assertEqual(len(result['revision']), 64)


if __name__ == '__main__':
    unittest.main()
