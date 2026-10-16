import contextlib
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import file_workspace as files


class FileWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        patcher = patch.object(files, '_APP_ROOT', self.root / 'Application')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.desktop = self.root / 'Desktop'
        self.desktop.mkdir()
        self.opener = Mock()
        self.service = files.FileWorkspace(opener=self.opener)
        for target, value in [('desktop_workspace._desktop_roots', [self.desktop]),
                              ('file_workspace._standard_roots', []), ('scanner.FILES', [])]:
            patcher = patch(target, return_value=value) if target != 'scanner.FILES' else patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {'SystemRoot': str(self.root / 'Windows')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, name, parent=None):
        path = (parent or self.desktop) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
        return path

    def test_arbitrary_large_file_metadata_and_directories_without_reading(self):
        self.add('브랜드 최종.PSD')
        self.add('bundle.zip')
        path = self.add('photo.png')
        with path.open('r+b') as stream:
            stream.truncate(12 * 1024 * 1024)
        with patch.object(pathlib.Path, 'open', side_effect=AssertionError('no content reads')):
            result = self.service.search()
        self.assertEqual(result['total'], 4)
        item = next(row for row in result['items'] if row['name'] == 'photo.png')
        self.assertEqual(item['size'], 12 * 1024 * 1024)
        self.assertEqual(item['kind'], 'file')
        self.assertEqual(self.service.search('브랜드   최종')['total'], 1)
        self.assertEqual(self.service.search('PSD')['total'], 1)
        self.assertEqual(self.service.search(kind='folder')['total'], 1)
        self.assertEqual(self.service.search('bundle')['items'][0]['id'], self.service.search('bundle')['items'][0]['id'])

    def test_excludes_protected_names_paths_and_reparse(self):
        for name in ['settings.json', 'auth.txt', 'private.key', 'run.exe', '.hidden.png', 'AppData/a.txt', 'config/file.psd']:
            self.add(name)
        safe = self.add('normal.zip')
        result = self.service.search(kind='file')
        self.assertEqual([row['path'] for row in result['items']], [str(safe)])
        with patch('desktop_workspace._linked', return_value=True):
            self.assertEqual(self.service.search()['total'], 0)

    def test_app_repository_is_never_a_scope(self):
        self.service.workspace = files._APP_ROOT
        self.service.workspace.mkdir()
        result = self.service.search()
        self.assertTrue(all(scope['path'] != str(self.service.workspace) for scope in result['scopes']))

    def test_scanner_individual_file_and_directory_never_authorize_siblings(self):
        remote = self.root / 'Remote'
        selected = self.add('selected.psd', remote)
        self.add('sibling.psd', remote)
        folder = remote / 'ApprovedFolder'
        folder.mkdir()
        self.add('notapproved.png', folder)
        with patch('scanner.FILES', [{'path': str(selected)}, {'path': str(folder)}]):
            result = self.service.search()
        names = [row['name'] for row in result['items']]
        self.assertIn('selected.psd', names)
        self.assertIn('ApprovedFolder', names)
        self.assertNotIn('sibling.psd', names)
        self.assertNotIn('notapproved.png', names)

    def test_registered_resource_and_workspace_roots_and_missing_db(self):
        selected = self.add('registered.zip', self.root / 'Registered')
        other = self.add('work.psd', self.root / 'Workspace')
        db = self.root / 'fixture.db'
        with contextlib.closing(sqlite3.connect(db)) as connection:
            connection.execute('CREATE TABLE work_resources(path TEXT)')
            connection.execute('INSERT INTO work_resources VALUES (?)', (str(selected),))
            connection.commit()
        service = files.FileWorkspace(db, other.parent)
        result = service.search()
        self.assertTrue({'registered.zip', 'work.psd'}.issubset({row['name'] for row in result['items']}))
        missing = self.root / 'missing.db'
        files.FileWorkspace(missing).search()
        self.assertFalse(missing.exists())

    def test_depth_item_and_result_limits_are_truthful(self):
        self.add('one/two/three/four/five/deep.png')
        result = self.service.search()
        self.assertTrue(result['truncated'])
        self.assertNotIn('deep.png', [row['name'] for row in result['items']])
        self.add('a.psd')
        self.add('b.psd')
        result = self.service.search('psd', limit=1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['total'], 2)
        with patch.object(files, 'MAX_ITEMS', 1):
            self.assertTrue(self.service.search()['truncated'])
        with patch.object(files, 'MAX_SECONDS', 0):
            self.assertTrue(self.service.search()['truncated'])

    def test_reveal_dispatches_explorer_only_and_preserves_files(self):
        path = self.add('comma, report.psd')
        item = self.service.search('report')['items'][0]
        before = path.stat()
        result = self.service.reveal(item['id'], expected=item)
        self.assertTrue(result['dispatched'])
        self.assertIn('확인하지 않았습니다', result['message'])
        self.opener.assert_called_once_with([str(self.root / 'Windows' / 'explorer.exe'), '/select,' + str(path)], shell=False)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        folder = self.service.search(kind='folder')['items'][0]
        self.service.reveal(folder['id'], expected=folder)
        self.assertEqual(self.opener.call_args.args[0][1], str(self.desktop))

    def test_reveal_rejects_modified_deleted_or_revoked_item_and_forged_identity(self):
        path = self.add('selected.psd')
        item = self.service.search('selected')['items'][0]
        path.write_bytes(b'changed contents')
        with self.assertRaises(files.FileWorkspaceError):
            self.service.reveal(item['id'], expected=item)
        path.unlink()
        with self.assertRaises(files.FileWorkspaceError):
            self.service.reveal(item['id'], expected=item)
        path = self.add('external.zip', self.root / 'Outside')
        with patch('scanner.FILES', [{'path': str(path)}]):
            item = self.service.search('external')['items'][0]
        with self.assertRaises(files.FileWorkspaceError):
            self.service.reveal(item['id'], expected=item)
        self.opener.assert_not_called()

    def test_validation_and_opener_failure(self):
        for args in [('x', 'anything', 50), ('x', 'all', 0), ('x', 'all', True)]:
            with self.assertRaises(files.FileWorkspaceError):
                self.service.search(*args)
        for item_id in ['../bad', 'f' * 64, None]:
            with self.assertRaises(files.FileWorkspaceError):
                self.service.reveal(item_id)
        self.add('file.psd')
        item = self.service.search('file')['items'][0]
        self.opener.side_effect = OSError('private error')
        with self.assertRaisesRegex(files.FileWorkspaceError, '전달하지 못했습니다'):
            self.service.reveal(item['id'], expected=item)

    def test_changed_route_and_hardlink_are_not_revealed(self):
        path = self.add('source.psd')
        item = self.service.search('source')['items'][0]
        with patch('desktop_workspace._linked', return_value=True):
            with self.assertRaises(files.FileWorkspaceError):
                self.service.reveal(item['id'], expected=item)
        linked = self.desktop / 'linked.psd'
        try:
            os.link(path, linked)
        except OSError:
            self.skipTest('filesystem does not support hardlinks')
        self.assertEqual(self.service.search('psd')['total'], 0)
        self.opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
