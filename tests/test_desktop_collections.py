import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import desktop_collections as collections
import desktop_preferences as preferences
import desktop_workspace as workspace


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.desktop = self.root / 'Desktop'
        self.desktop.mkdir()
        for target, attribute, value in [(workspace, '_desktop_roots', None), (preferences, 'STATE', self.root / 'state.json')]:
            replacement = patch.object(target, attribute, return_value=[self.desktop]) if value is None else patch.object(target, attribute, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def add(self, name, data=b'fixture'):
        path = self.desktop / name
        path.write_bytes(data)
        return path

    def item(self, name):
        return next(i for i in workspace.inventory()['items'] if i['name'] == name)

    def test_metadata_families_exclude_generic_series_and_keep_words(self):
        for name in ['현수막.ai', '현수막2.ai', '현수막3.ai', 'client_logo.png', 'client_logo (1).png',
                'client_logo.ai', '새 텍스트 문서.txt', '새 텍스트 문서 (1).txt', 'kakao_01.jpg',
                'kakao_02.jpg', 'abcdefgh12345678.jpg', 'abcdefgh12345678 (1).jpg',
                '20250101.png', '20250101 (1).png', 'copywriter.png', 'copywriter.ai']:
            self.add(name)
        with patch.object(pathlib.Path, 'open', side_effect=AssertionError('Metadata must not read source bytes')):
            result = collections.overview()
        self.assertEqual({g['label'] for g in result['suggestions']}, {'현수막', 'client_logo', 'copywriter'})
        self.assertEqual(len(next(g for g in result['suggestions'] if g['label'] == '현수막')['items']), 3)

    def test_hex_uuid_and_same_root_boundary(self):
        for name in ['aabbccddeeff0011.png', 'aabbccddeeff0011 (1).png',
                '09d08534-e2f3-4a03-9d52-b06e87fd82e3.png', '09d08534-e2f3-4a03-9d52-b06e87fd82e3 (1).png']:
            self.add(name)
        other = self.root / 'Public'
        other.mkdir()
        self.add('report.ai')
        (other / 'report.png').write_bytes(b'fixture')
        with patch.object(workspace, '_desktop_roots', return_value=[self.desktop, other]):
            self.assertEqual(collections.overview()['suggestions'], [])

    def test_migrate_old_state_preserve_pins_unknown_and_source(self):
        first = self.add('client_logo.png')
        self.add('client_logo.ai')
        item_id = self.item(first.name)['id']
        preferences.STATE.write_text(json.dumps({'pinned_ids': [item_id], 'future': {'enabled': True}}))
        result = collections.create('고객 로고', [i['id'] for i in workspace.inventory()['items']])
        self.assertEqual(result['collections'][0]['label'], '고객 로고')
        state = preferences.load()
        self.assertEqual(state['pinned_ids'], [item_id])
        self.assertEqual(state['future'], {'enabled': True})
        self.assertEqual(first.read_bytes(), b'fixture')
        self.assertEqual(result['suggestions'], [])
        preferences.pin(item_id, False)
        self.assertEqual(len(preferences.load()['collections']), 1)

    def test_mutations_dissolve_restores_suggestion_and_missing_member(self):
        first = self.add('report.ai')
        second = self.add('report.png')
        ids = [i['id'] for i in workspace.inventory()['items']]
        result = collections.create('report', ids)
        group = result['collections'][0]['id']
        self.assertEqual(collections.rename(group, '완료 업무')['collections'][0]['label'], '완료 업무')
        first.rename(self.desktop / 'changed.ai')  # Fixture only simulates a user change.
        self.assertEqual(collections.overview()['collections'][0]['missing_count'], 1)
        stale = next(i for i in ids if i != self.item(second.name)['id'])
        result = collections.remove_item(group, stale)
        self.assertEqual(result['collections'][0]['missing_count'], 0)
        self.assertEqual(collections.dissolve(group)['collections'], [])
        self.assertEqual(second.read_bytes(), b'fixture')

    def test_stale_overlap_invalid_and_capacity_rejected_without_state_changes(self):
        first = self.add('report.ai')
        self.add('report.png')
        ids = [i['id'] for i in workspace.inventory()['items']]
        collections.create('업무', [ids[0]])
        before = preferences.STATE.read_bytes()
        with self.assertRaises(ValueError):
            collections.create('겹침', ids)
        with self.assertRaises(ValueError):
            collections.create('없음', ['f' * 64])
        with self.assertRaises(ValueError):
            collections.create('경로', [str(first)])
        with self.assertRaises(ValueError):
            collections.create('x' * 121, [ids[1]])
        with patch.object(collections, 'MAX_COLLECTIONS', 1), self.assertRaises(ValueError):
            collections.create('더 추가', [ids[1]])
        self.assertEqual(preferences.STATE.read_bytes(), before)

    def test_dismiss_stable_and_membership_or_metadata_change_reappears(self):
        path = self.add('logo.ai')
        self.add('logo.png')
        suggestion = collections.overview()['suggestions'][0]
        self.assertEqual(collections.overview()['suggestions'][0]['id'], suggestion['id'])
        self.assertEqual(collections.dismiss(suggestion['id'])['suggestions'], [])
        path.write_bytes(b'changed bytes')
        changed = collections.overview()['suggestions'][0]
        self.assertNotEqual(changed['id'], suggestion['id'])
        with self.assertRaises(ValueError):
            collections.dismiss(suggestion['id'])
        collections.dismiss(changed['id'])
        self.add('logo (1).png')
        self.assertEqual(len(collections.overview()['suggestions']), 1)

    def test_same_size_change_within_display_second_reappears(self):
        path = self.add('logo.ai', b'first')
        self.add('logo.png')
        before = path.stat()
        base_ns = (before.st_mtime_ns // 1_000_000_000) * 1_000_000_000
        os.utime(path, ns=(before.st_atime_ns, base_ns + 100))
        suggestion = collections.overview()['suggestions'][0]
        collections.dismiss(suggestion['id'])
        path.write_bytes(b'other')
        os.utime(path, ns=(before.st_atime_ns, base_ns + 500))
        self.assertEqual(len(collections.overview()['suggestions']), 1)

    def test_malformed_state_preserved(self):
        preferences.STATE.write_text(json.dumps({'pinned_ids': [], 'collections': 'broken'}))
        before = preferences.STATE.read_bytes()
        with self.assertRaises(ValueError):
            collections.overview()
        with self.assertRaises(ValueError):
            collections.create('업무', ['a' * 64])
        self.assertEqual(before, preferences.STATE.read_bytes())

    def test_restore_dismissed_preserves_existing_collection_and_pins(self):
        self.add('logo.ai')
        self.add('logo.png')
        self.add('report.txt')
        report_id = self.item('report.txt')['id']
        preferences.pin(report_id, True)
        collections.create('별도 업무', [report_id])
        suggestion = collections.overview()['suggestions'][0]
        self.assertEqual(collections.dismiss(suggestion['id'])['dismissed_count'], 1)
        before = preferences.load()
        restored = collections.restore_dismissed()
        self.assertEqual(restored['dismissed_count'], 0)
        self.assertEqual(len(restored['suggestions']), 1)
        after = preferences.load()
        self.assertEqual(before['collections'], after['collections'])
        self.assertEqual(before['pinned_ids'], after['pinned_ids'])

    def test_automatic_grouping_idempotent_preserves_manual_pins_undo_and_resume(self):
        files = [self.add(name) for name in ['logo.ai', 'logo.png', 'report.txt']]
        report_id = self.item('report.txt')['id']
        preferences.pin(report_id, True)
        manual = collections.create('직접 만든 묶음', [report_id])['collections'][0]['id']
        self.assertTrue(collections.overview()['auto_organize_enabled'])
        automatic = collections.auto_organize()
        self.assertEqual(automatic['automatic_created_count'], 1)
        history = automatic['last_auto_group_ids']
        self.assertEqual(len(history), 1)
        self.assertEqual(len(automatic['collections']), 2)
        self.assertEqual(next(g for g in automatic['collections'] if g['id'] in history)['source'], 'automatic')
        again = collections.auto_organize()
        self.assertEqual(again['automatic_created_count'], 0)
        self.assertEqual(again['last_auto_group_ids'], history)
        paused = collections.auto_organize(False)
        self.assertFalse(paused['auto_organize_enabled'])
        self.assertEqual(paused['last_auto_group_ids'], history)
        self.assertEqual(len(paused['collections']), 2)
        undone = collections.undo_auto()
        self.assertFalse(undone['auto_organize_enabled'])
        self.assertEqual([g['id'] for g in undone['collections']], [manual])
        self.assertEqual(undone['last_auto_group_ids'], [])
        self.assertEqual(len(collections.overview()['suggestions']), 1)
        self.assertEqual(preferences.load()['pinned_ids'], [report_id])
        resumed = collections.auto_organize(True)
        self.assertTrue(resumed['auto_organize_enabled'])
        self.assertEqual(resumed['automatic_created_count'], 1)
        self.assertEqual([path.read_bytes() for path in files], [b'fixture'] * 3)

    def test_auto_capacity_leaves_suggestions_and_respects_dismissal(self):
        for name in ['logo.ai', 'logo.png', 'report.ai', 'report.png', 'manual.txt']:
            self.add(name)
        collections.create('manual', [self.item('manual.txt')['id']])
        logo = next(g for g in collections.overview()['suggestions'] if g['label'] == 'logo')
        collections.dismiss(logo['id'])
        with patch.object(collections, 'MAX_COLLECTIONS', 1):
            result = collections.auto_organize()
            self.assertEqual(result['automatic_created_count'], 0)
            self.assertEqual([g['label'] for g in result['suggestions']], ['report'])
        result = collections.auto_organize()
        self.assertEqual(result['automatic_created_count'], 1)
        self.assertEqual(result['dismissed_count'], 1)
        self.assertEqual(result['suggestions'], [])

    def test_undo_ignores_already_dissolved_auto_groups_and_keeps_new_manual_group(self):
        for name in ['logo.ai', 'logo.png', 'report.ai', 'report.png', 'manual.txt']:
            self.add(name)
        result = collections.auto_organize()
        self.assertEqual(result['automatic_created_count'], 2)
        collections.dissolve(result['last_auto_group_ids'][0])
        manual = collections.create('manual', [self.item('manual.txt')['id']])['collections'][-1]['id']
        undone = collections.undo_auto()
        self.assertEqual([g['id'] for g in undone['collections']], [manual])
        self.assertFalse(undone['auto_organize_enabled'])

    def test_duplicates_are_exact_read_only_and_exclude_apps_scripts_directories(self):
        source = self.add('source.txt', b'same')
        self.add('copy.dat', b'same')
        self.add('different.txt', b'else')
        for name in ['app.lnk', 'run.py', 'run.exe', 'page.html']:
            self.add(name, b'same')
        (self.desktop / 'folder').mkdir()
        before = {p.name: p.read_bytes() for p in self.desktop.iterdir() if p.is_file()}
        result = collections.duplicate_report()
        self.assertEqual(result['checked'], 3)
        self.assertEqual(result['total_bytes_read'], 12)
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual({i['name'] for i in result['groups'][0]['items']}, {'source.txt', 'copy.dat'})
        self.assertEqual(result['groups'][0]['duplicate_bytes'], 4)
        self.assertEqual({p.name: p.read_bytes() for p in self.desktop.iterdir() if p.is_file()}, before)
        self.assertEqual(source.read_bytes(), b'same')

    def test_hash_bounds_and_reparse_or_changed_source_excluded(self):
        self.add('a.txt', b'same')
        self.add('b.txt', b'same')
        with patch.object(collections, 'MAX_FILE_BYTES', 3):
            report = collections.duplicate_report()
            self.assertEqual(report['total_bytes_read'], 0)
            self.assertEqual(report['groups'], [])
        with patch.object(collections, 'MAX_TOTAL_BYTES', 4):
            report = collections.duplicate_report()
            self.assertEqual(report['total_bytes_read'], 4)
            self.assertEqual(report['groups'], [])
        original = collections._stamp
        calls = [0]
        def changed_stamp(info):
            calls[0] += 1
            stamp = original(info)
            return stamp + (calls[0],)
        with patch.object(collections, '_stamp', side_effect=changed_stamp):
            self.assertEqual(collections.duplicate_report()['groups'], [])
        original_route = workspace._safe_route
        with patch.object(workspace, '_safe_route', side_effect=lambda p: p.name != 'a.txt' and original_route(p)):
            report = collections.duplicate_report()
            self.assertEqual(report['groups'], [])
            self.assertLessEqual(report['total_bytes_read'], 4)

    def test_file_changed_during_hash_is_not_claimed_identical(self):
        first = self.add('a.txt', b'same')
        self.add('b.txt', b'same')
        real_fstat = os.fstat
        first_inode = first.stat().st_ino
        calls = [0]
        def changing_fstat(fd):
            info = real_fstat(fd)
            if info.st_ino == first_inode:
                calls[0] += 1
            if info.st_ino == first_inode and calls[0] >= 2:
                return types.SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns + 1, st_ctime_ns=info.st_ctime_ns)
            return info
        with patch.object(collections.os, 'fstat', side_effect=changing_fstat):
            report = collections.duplicate_report()
        self.assertEqual(report['groups'], [])
        self.assertEqual(report['checked'], 1)
        self.assertEqual(report['total_bytes_read'], 8)
        self.assertTrue(report['warnings'])

    def test_windows_path_and_fd_ctime_difference_does_not_hide_duplicates(self):
        self.add('a.txt', b'same')
        self.add('b.txt', b'same')
        real_fstat = os.fstat
        def descriptor_stamp(fd):
            info = real_fstat(fd)
            return types.SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns, st_ctime_ns=1234)
        with patch.object(collections.os, 'fstat', side_effect=descriptor_stamp):
            report = collections.duplicate_report()
        self.assertEqual(len(report['groups']), 1)
        self.assertEqual(report['checked'], 2)
        self.assertEqual(report['total_bytes_read'], 8)


if __name__ == '__main__':
    unittest.main()
