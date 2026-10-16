import base64
import io
import pathlib
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from PIL import Image

import assistant_extensions as extension
import assistant_tools
import capabilities
import file_workspace
import quick_launcher


class AssistantExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.desktop = self.root / 'Desktop'
        self.desktop.mkdir()
        for target, value in [('desktop_workspace._desktop_roots', [self.desktop]),
                              ('file_workspace._standard_roots', []), ('windows_app_catalog.catalog', {'available': True, 'items': []}),
                              ('scanner.FILES', [])]:
            patcher = patch(target, value) if target == 'scanner.FILES' else patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(file_workspace, '_APP_ROOT', self.root / 'Application')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = assistant_tools.ToolRegistry.__new__(assistant_tools.ToolRegistry)
        self.registry.db_path = str(self.root / 'fixture.db')
        self.registry.launcher = quick_launcher.QuickLauncher(self.registry.db_path)
        self.registry.capabilities = capabilities.CapabilityService(self.registry.db_path)
        self.context = {'refs': {}, 'step_id': uuid.uuid4().hex}
        saved = self.registry.launcher.perform('save', {'request_id': uuid.uuid4().hex, 'title': 'Original',
                 'kind': 'web', 'target': 'https://example.com', 'icon': '★'})
        self.item_id = saved['result']['id']
        self.ref = self.registry._ref(self.context, 'launcher', self.item_id)

    def run_tool(self, name, args=None):
        self.context['step_id'] = uuid.uuid4().hex
        return extension.execute(self.registry, name, args or {}, self.context)

    def select_file(self, path):
        result = self.registry.capabilities.search(path.name)
        item = next(item for item in result['items'] if item['path'] == str(path))
        ref = self.registry._ref(self.context, 'file_selection', {'id': item['id'], 'selection_token': item['selection_token']})
        return ref, item

    def gif(self):
        output = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(output, format='GIF', save_all=True,
            append_images=[Image.new('RGB', (8, 8), 'blue')], duration=[70, 130], loop=0)
        return output.getvalue()

    def test_pages_create_and_move_reuse_existing_service(self):
        pages = self.run_tool('launcher_pages')['data']['items']
        self.assertEqual(pages[0]['count'], 1)
        self.assertEqual(self.context['refs'][pages[0]['ref']]['kind'], 'launcher_page')
        self.run_tool('launcher_page_create')
        pages = self.run_tool('launcher_pages')['data']['items']
        result = self.run_tool('launcher_move_page', {'ref': self.ref, 'page_ref': pages[1]['ref']})
        self.assertEqual(result['data']['page'], 2)
        self.assertEqual(result['refresh'], ['launcher'])
        self.assertEqual(self.registry.launcher.overview()['items'][0]['title'], 'Original')

    def test_forged_page_ref_and_missing_launcher_rejected(self):
        with self.assertRaises(ValueError):
            self.run_tool('launcher_move_page', {'ref': self.ref, 'page_ref': 'page_fake'})
        self.registry.launcher.perform('remove', {'request_id': uuid.uuid4().hex, 'id': self.item_id})
        with self.assertRaises(ValueError):
            self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': self.ref})

    def test_target_change_preserves_name_icon_raw_gif_and_page(self):
        raw = self.gif()
        self.registry.launcher.perform('appearance', {'request_id': uuid.uuid4().hex, 'id': self.item_id,
            'title': 'Original', 'icon_data': 'data:image/gif;base64,' + base64.b64encode(raw).decode()})
        bookmark = self.registry._ref(self.context, 'bookmark', {'title': 'Other', 'target': 'https://other.example.com'})
        self.run_tool('launcher_change_target', {'ref': self.ref, 'target_ref': bookmark})
        row = self.registry.launcher.overview()['items'][0]
        self.assertEqual((row['title'], row['icon'], row['page']), ('Original', '★', 1))
        self.assertEqual(row['target'], 'https://other.example.com')
        self.assertEqual(self.registry.launcher.icon_bytes(self.item_id), (raw, 'image/gif'))

    def test_target_accepts_selected_folder_but_not_file_or_unissued_ref(self):
        folder = self.desktop / 'Project'
        folder.mkdir()
        ref, _ = self.select_file(folder)
        self.run_tool('launcher_change_target', {'ref': self.ref, 'target_ref': ref})
        row = self.registry.launcher.overview()['items'][0]
        self.assertEqual((row['kind'], row['target']), ('folder', str(folder)))
        file = self.desktop / 'ordinary.txt'
        file.write_text('fixture')
        ref, _ = self.select_file(file)
        with self.assertRaises(ValueError):
            self.run_tool('launcher_change_target', {'ref': self.ref, 'target_ref': ref})
        with self.assertRaises(ValueError):
            self.run_tool('launcher_change_target', {'ref': self.ref, 'target_ref': str(folder)})

    def test_image_gif_preserved_and_change_is_immediate_revision(self):
        path = self.desktop / 'avatar.gif'
        raw = self.gif()
        path.write_bytes(raw)
        ref, _ = self.select_file(path)
        self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': ref})
        self.assertEqual(self.registry.launcher.icon_bytes(self.item_id), (raw, 'image/gif'))
        self.assertTrue(self.registry.launcher.overview()['items'][0]['icon_revision'])

    def test_expired_changed_or_invalid_image_rejected(self):
        path = self.desktop / 'avatar.gif'
        path.write_bytes(self.gif())
        ref, item = self.select_file(path)
        with self.registry.capabilities._db() as connection:
            connection.execute('UPDATE capability_selections SET created=?', (time.time() - capabilities.SELECTION_TTL - 1,))
        with self.assertRaises(ValueError):
            self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': ref})
        ref, _ = self.select_file(path)
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': ref})
        ref, _ = self.select_file(path)
        with self.assertRaises(ValueError):
            self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': ref})
        self.assertFalse(self.registry.launcher.overview()['items'][0]['icon_image'])

    def test_revoked_and_oversized_image_selections_rejected(self):
        path = self.desktop / 'avatar.gif'
        path.write_bytes(self.gif())
        ref, _ = self.select_file(path)
        with self.registry.capabilities._db() as connection:
            connection.execute('DELETE FROM capability_selections')
        with self.assertRaises(ValueError):
            self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': ref})
        with path.open('r+b') as stream:
            stream.truncate(quick_launcher._MAX_ICON_BYTES + 1)
        ref, _ = self.select_file(path)
        with self.assertRaisesRegex(ValueError, '512KB'):
            self.run_tool('launcher_set_image', {'ref': self.ref, 'image_ref': ref})
        self.assertFalse(self.registry.launcher.overview()['items'][0]['icon_image'])

    def test_scan_reads_only_existing_safe_metadata(self):
        path = self.desktop / 'design.psd'
        path.write_bytes(b'fixture')
        private = self.desktop / 'credentials.txt'
        private.write_bytes(b'never read')
        sampled = {'total': 3, 'page': 0, 'items': [{'path': str(path)}, {'path': str(private)}, {'path': str(path), 'protected': True}]}
        with patch('scanner.sample', return_value=sampled) as sample, patch.object(pathlib.Path, 'open', side_effect=AssertionError('no reads')):
            result = self.run_tool('scan_results', {'query': 'design'})
        sample.assert_called_once_with(page=0, term='design')
        self.assertEqual(result['data']['visible_count'], 1)
        self.assertEqual(result['data']['scanned_match_count'], 3)
        self.assertEqual(result['data']['items'][0]['name'], 'design.psd')
        with patch('scanner.status', return_value={'status': 'done', 'message': 'privatepath',
            'summary': {'folders': ['privatepath'], 'total': 1, 'categories': {'이미지': 1}}}):
            result = self.run_tool('scan_status')
        self.assertNotIn('privatepath', str(result))

    def test_unknown_extension_rejected(self):
        with self.assertRaises(ValueError):
            self.run_tool('arbitrary_function')


if __name__ == '__main__':
    unittest.main()
