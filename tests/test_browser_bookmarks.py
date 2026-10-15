import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import browser_bookmarks as bookmarks


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)

    def write(self, browser, profile, nodes):
        path = self.root / browser / profile / 'Bookmarks'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'roots': {'bookmark_bar': {'type': 'folder', 'name': '북마크바', 'children': nodes}}}), encoding='utf-8')
        return path

    def catalog(self):
        with patch.object(bookmarks, '_browser_roots', return_value=[('Chrome', self.root / 'Chrome'), ('Edge', self.root / 'Edge')]):
            return bookmarks.catalog()

    def test_multiple_profiles_folders_read_only_and_safe_urls(self):
        nodes = [{'type': 'folder', 'name': '업무', 'children': [{'type': 'url', 'name': '회사', 'url': 'https://example.com/work'}]},
                 *[{'type': 'url', 'name': 'bad', 'url': value} for value in ['javascript:alert(1)', 'file:///x', 'https://user:pass@example.com', 'https://bad host', 'https://example.com\n']]]
        a = self.write('Chrome', 'Default', nodes)
        self.write('Chrome', 'Profile 2', [{'type': 'url', 'name': '검색', 'url': 'https://example.org'}])
        self.write('Edge', 'Default', [{'type': 'url', 'name': '검색', 'url': 'http://example.net'}])
        before = a.read_bytes()
        result = self.catalog()
        self.assertEqual(result['total'], 3)
        self.assertEqual(a.read_bytes(), before)
        self.assertIn('북마크바 / 업무', [i['folder'] for i in result['items']])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_malformed_and_bounds_report_warnings(self):
        path = self.write('Chrome', 'Default', [])
        path.write_text('{bad', encoding='utf-8')
        self.assertTrue(self.catalog()['warnings'])
        self.write('Chrome', 'Default', [{'type': 'url', 'name': str(i), 'url': f'https://example.com/{i}'} for i in range(5)])
        with patch.object(bookmarks, 'MAX_ITEMS', 2):
            result = self.catalog()
        self.assertEqual(len(result['items']), 2)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['warnings'])

    def test_unsafe_routes_never_read(self):
        self.write('Chrome', 'Default', [])
        with patch.object(bookmarks, '_safe_route', return_value=False), patch.object(pathlib.Path, 'open') as read:
            self.assertEqual(self.catalog()['items'], [])
            read.assert_not_called()

    def test_oversized_file_and_deep_tree_are_bounded(self):
        self.write('Chrome', 'Default', [{'type': 'url', 'name': '회사', 'url': 'https://example.com'}])
        with patch.object(bookmarks, 'MAX_BYTES', 3):
            result = self.catalog()
        self.assertEqual(result['items'], [])
        self.assertTrue(result['warnings'])
        node = {'type': 'url', 'name': '회사', 'url': 'https://example.com'}
        for _ in range(5):
            node = {'type': 'folder', 'name': '하위', 'children': [node]}
        self.write('Chrome', 'Default', [node])
        with patch.object(bookmarks, 'MAX_DEPTH', 2):
            result = self.catalog()
        self.assertEqual(result['items'], [])
        self.assertTrue(result['truncated'])

    def test_unrelated_files_and_profiles_are_not_opened(self):
        self.write('Chrome', 'Default', [])
        self.write('Chrome', 'Guest Profile', [{'type': 'url', 'name': 'guest', 'url': 'https://example.com'}])
        for name in ['Cookies', 'History', 'Login Data', 'Preferences']:
            (self.root / 'Chrome' / 'Default' / name).write_text('private', encoding='utf-8')
        original = pathlib.Path.open
        opened = []
        def tracked(path, *args, **kwargs):
            opened.append(path.name)
            return original(path, *args, **kwargs)
        with patch.object(pathlib.Path, 'open', tracked):
            result = self.catalog()
        self.assertEqual(opened, ['Bookmarks'])
        self.assertEqual(result['total'], 0)
