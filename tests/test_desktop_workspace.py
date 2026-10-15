import io
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import desktop_workspace as workspace


class DesktopWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.desktop = self.root / 'Desktop'
        self.desktop.mkdir()
        self.discovery = patch.object(workspace, '_desktop_roots', return_value=[self.desktop])
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def add(self, name, data=b'fixture'):
        path = self.desktop / name
        path.write_bytes(data)
        return path

    def item(self, name):
        return next(item for item in workspace.inventory()['items'] if item['name'] == name)

    def test_shallow_inventory_classifies_and_deduplicates_roots(self):
        folder = self.desktop / 'project'
        folder.mkdir()
        (folder / 'nested.txt').write_text('nested')
        self.add('design.psd')
        self.add('Chrome.lnk')
        self.add('app.url')
        self.add('run.exe')
        with patch.object(workspace, '_desktop_roots', return_value=[self.desktop, self.desktop]):
            result = workspace.inventory()
        self.assertEqual(result['total'], 5)
        self.assertEqual(len(result['roots']), 1)
        self.assertEqual(self.item('design.psd')['category'], '디자인 원본')
        self.assertEqual(self.item('Chrome.lnk')['kind'], 'app')
        self.assertTrue(self.item('app.url')['openable'])
        self.assertFalse(self.item('run.exe')['openable'])
        self.assertEqual(self.item('project')['size'], 0)

    def test_registry_desktop_discovery_expands_onedrive_and_includes_public(self):
        one_drive = self.root / 'OneDrive'
        desktop = one_drive / 'Desktop'
        desktop.mkdir(parents=True)
        public = self.root / 'Public'
        (public / 'Desktop').mkdir(parents=True)
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=MagicMock(),
            QueryValueEx=MagicMock(return_value=('%DESKTOP_TEST_ONEDRIVE%/Desktop', 2)))
        self.discovery.stop()
        with patch.dict(sys.modules, {'winreg': registry}), patch.object(workspace.sys, 'platform', 'win32'), \
                patch.dict(os.environ, {'DESKTOP_TEST_ONEDRIVE': str(one_drive), 'PUBLIC': str(public)}):
            roots = workspace._desktop_roots()
        self.discovery.start()
        self.assertEqual(roots, [desktop, public / 'Desktop'])
        registry.QueryValueEx.assert_called_once()

    def test_ids_are_stable_and_inventory_does_not_read_contents(self):
        path = self.add('report.txt')
        first = self.item(path.name)['id']
        self.add('aaa.txt')
        with patch.object(pathlib.Path, 'open', side_effect=AssertionError('No content reads')):
            self.assertEqual(self.item(path.name)['id'], first)

    def test_open_valid_shortcut_uses_literal_path_and_preserves_source(self):
        path = self.add('app & name.lnk')
        identifier = self.item(path.name)['id']
        with patch.object(workspace.sys, 'platform', 'win32'), patch.object(os, 'startfile', create=True) as start:
            self.assertTrue(workspace.open_item(identifier)['ok'])
        start.assert_called_once_with(str(path))
        self.assertEqual(path.read_bytes(), b'fixture')

    def test_arbitrary_deleted_and_protected_ids_never_launch(self):
        path = self.add('deleted.txt')
        deleted_id = self.item(path.name)['id']
        path.unlink()  # Only this test-owned fixture is removed.
        for name in ('secret.env', 'script.py', 'script.js', 'run.cmd', 'data.db'):
            self.add(name)
        with patch.object(workspace.sys, 'platform', 'win32'), patch.object(os, 'startfile', create=True) as start:
            for identifier in ('C:\\Windows\\notepad.exe', 'f' * 64, deleted_id,
                    *(self.item(name)['id'] for name in ('secret.env', 'script.py', 'script.js', 'run.cmd', 'data.db'))):
                with self.assertRaises(ValueError):
                    workspace.open_item(identifier)
        start.assert_not_called()

    def test_hidden_system_dotfiles_and_desktop_ini_are_not_listed(self):
        for name in ('visible.txt', 'hidden.txt', 'system.txt', '.private', 'desktop.ini'):
            self.add(name)
        original = pathlib.Path.lstat

        def file_info(path):
            info = original(path)
            if path.name in {'hidden.txt', 'system.txt'}:
                return types.SimpleNamespace(st_file_attributes=2 if path.name == 'hidden.txt' else 4)
            return info

        with patch.object(pathlib.Path, 'lstat', file_info):
            result = workspace.inventory()
        self.assertEqual([item['name'] for item in result['items']], ['visible.txt'])
        self.assertFalse(result['warnings'])

    def test_linked_item_is_not_openable(self):
        path = self.add('linked.txt')
        original = workspace._linked
        with patch.object(workspace, '_linked', side_effect=lambda candidate, info=None:
                candidate == path or original(candidate, info)):
            self.assertFalse(self.item(path.name)['openable'])
            with self.assertRaises(ValueError):
                workspace._selected(self.item(path.name)['id'])

    def test_reveal_passes_argument_vector_without_shell(self):
        path = self.add('a & b.txt')
        identifier = self.item(path.name)['id']
        with patch.object(workspace.sys, 'platform', 'win32'), patch.object(workspace.subprocess, 'Popen') as launch:
            workspace.reveal_item(identifier)
        self.assertEqual(launch.call_args.args[0][1], '/select,' + str(path))
        self.assertFalse(launch.call_args.kwargs['shell'])

    def test_inventory_bound(self):
        self.add('a.txt')
        self.add('b.txt')
        with patch.object(workspace, 'MAX_ITEMS', 1):
            result = workspace.inventory()
        self.assertEqual(result['total'], 1)
        self.assertTrue(result['warnings'])

    def test_thumbnail_is_small_clean_jpeg_and_source_unchanged(self):
        from PIL import Image
        path = self.desktop / 'image.png'
        Image.new('RGBA', (640, 400), (255, 0, 0, 128)).save(path)
        before = path.read_bytes()
        data = workspace.thumbnail(self.item(path.name)['id'])
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertLessEqual(image.width, 320)
            self.assertLessEqual(image.height, 220)
            self.assertFalse(image.getexif())
        self.assertEqual(path.read_bytes(), before)

    def test_thumbnail_rejects_extension_mismatch_and_size_limit(self):
        from PIL import Image
        path = self.desktop / 'fake.jpg'
        Image.new('RGB', (2, 2)).save(path, format='PNG')
        with self.assertRaisesRegex(ValueError, '확장자'):
            workspace.thumbnail(self.item(path.name)['id'])
        with patch.object(workspace, 'MAX_IMAGE_BYTES', 1):
            with self.assertRaisesRegex(ValueError, '8MB'):
                workspace.thumbnail(self.item(path.name)['id'])

    def test_non_windows_actions_are_actionable(self):
        with patch.object(workspace.sys, 'platform', 'linux'):
            for action in (workspace.open_item, workspace.reveal_item):
                with self.assertRaisesRegex(ValueError, 'Windows'):
                    action('f' * 64)


if __name__ == '__main__':
    unittest.main()
