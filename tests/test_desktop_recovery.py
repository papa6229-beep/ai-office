import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import desktop_recovery as recovery
import desktop_workspace
import file_executor


@unittest.skipUnless(sys.platform == 'win32', 'Windows pinned rename verification')
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.root = self.base / 'Desktop'
        self.root.mkdir()
        self.runtime = self.base / 'runtime'
        self.rows, self.items = [], []
        self.revision = 'a' * 32
        for target, name, value in [
            (recovery, 'RUNTIME', self.runtime),
            (desktop_workspace, '_desktop_roots', lambda: [self.root]),
            (desktop_workspace, 'inventory', lambda: {'roots': [str(self.root)], 'items': self.items}),
            (recovery.desktop_judgment, 'status', lambda: {'revision': self.revision, 'candidates': self.rows}),
            (recovery.desktop_judgment, 'inspect_candidates', lambda ids: [r for r in self.rows if r['id'] in ids]),
        ]:
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def add(self, name='empty.txt', content=b''):
        path = self.root / name
        path.write_bytes(content)
        item_id = desktop_workspace._identifier(path)
        self.items.append({'id': item_id, 'path': str(path), 'name': name, 'kind': 'file'})
        self.rows.append({'id': item_id, 'stamp': file_executor._fingerprint(path)})
        return path, item_id

    def hold(self, ids=None, request='b' * 32):
        return recovery.hold(self.revision, ids or [r['id'] for r in self.rows], request)

    def test_hold_restore_and_idempotency(self):
        path, item_id = self.add()
        result = self.hold()
        self.assertEqual(result['held_count'], 1)
        self.assertFalse(path.exists())
        self.assertEqual(self.hold()['batch_id'], result['batch_id'])
        restored = recovery.restore(result['batch_id'])
        self.assertEqual(restored['restored_count'], 1)
        self.assertEqual(path.read_bytes(), b'')
        self.assertEqual(recovery.restore(result['batch_id'])['restored_count'], 1)

    def test_request_reuse_payload_rejected(self):
        self.add()
        self.hold()
        with self.assertRaises(ValueError):
            recovery.hold('c' * 32, [r['id'] for r in self.rows], 'b' * 32)

    def test_revision_mismatch_does_not_create_holding(self):
        path, item_id = self.add()
        with self.assertRaises(ValueError):
            recovery.hold('c' * 32, [item_id], 'b' * 32)
        self.assertTrue(path.exists())
        self.assertFalse((self.root / recovery.HOLDING).exists())

    def test_source_changed_after_analysis_is_preserved(self):
        path, _ = self.add()
        path.write_bytes(b'new meaning')
        with self.assertRaises(ValueError):
            self.hold()
        self.assertEqual(path.read_bytes(), b'new meaning')

    def test_secret_text_is_preserved_even_if_candidate_is_forged(self):
        path, _ = self.add(content=b'password=fixture-secret')
        with self.assertRaises(ValueError):
            self.hold()
        self.assertTrue(path.exists())

    def test_locked_source_is_preserved(self):
        import ctypes
        from ctypes import wintypes
        path, _ = self.add()
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        handle = create(str(path), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            result = self.hold()
            self.assertEqual(result['held_count'], 0)
            self.assertTrue(path.exists())
        finally:
            close(handle)

    def test_junction_holding_is_rejected(self):
        import subprocess
        path, _ = self.add()
        outside = self.base / 'outside'
        outside.mkdir()
        junction = self.root / recovery.HOLDING
        completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                   capture_output=True)
        if completed.returncode:
            self.skipTest('Junction unavailable')
        try:
            with self.assertRaises(ValueError):
                self.hold()
            self.assertTrue(path.exists())
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            os.rmdir(junction)

    def test_image_without_design_marker_can_be_held(self):
        path, _ = self.add('generated.png', b'fixture image bytes')
        self.assertEqual(self.hold()['held_count'], 1)
        self.assertFalse(path.exists())

    def test_image_with_design_marker_is_preserved(self):
        path, item_id = self.add('linked.png', b'fixture image')
        (self.root / 'design.ai').write_bytes(b'design fixture')
        self.assertFalse(recovery.holdability([item_id])[item_id]['allowed'])
        with self.assertRaises(ValueError):
            self.hold()
        self.assertTrue(path.exists())

    def test_empty_text_allowed_in_design_folder(self):
        _, item_id = self.add()
        (self.root / 'design.ai').write_bytes(b'design fixture')
        self.assertTrue(recovery.holdability([item_id])[item_id]['allowed'])
        self.assertEqual(self.hold()['held_count'], 1)

    def test_hard_link_rejected(self):
        path, _ = self.add()
        os.link(path, self.root / 'alias.txt')
        with self.assertRaises(ValueError):
            self.hold()
        self.assertTrue(path.exists())

    def test_new_original_never_overwritten_on_restore(self):
        path, _ = self.add()
        result = self.hold()
        path.write_bytes(b'new original')
        restored = recovery.restore(result['batch_id'])
        self.assertEqual(restored['restored_count'], 0)
        self.assertEqual(path.read_bytes(), b'new original')
        self.assertEqual(restored['held_count'], 1)

    def test_modified_held_file_is_preserved(self):
        self.add()
        result = self.hold()
        batch = recovery._load(result['batch_id'])
        held = pathlib.Path(batch['items'][0]['holding'])
        held.write_bytes(b'edited holding')
        restored = recovery.restore(result['batch_id'])
        self.assertEqual(restored['restored_count'], 0)
        self.assertEqual(held.read_bytes(), b'edited holding')
        self.assertEqual(len(restored['exceptions']), 1)

    def test_crash_after_rename_is_reconciled_once(self):
        path, _ = self.add()
        real = recovery._reconcile_row
        with patch.object(recovery, '_reconcile_row', side_effect=RuntimeError('simulated stop')):
            with self.assertRaises(RuntimeError):
                self.hold()
        self.assertFalse(path.exists())
        result = recovery.reconcile()
        self.assertEqual(result['batches'][0]['held_count'], 1)
        self.assertEqual(recovery.reconcile()['batches'][0]['held_count'], 1)
        recovery.restore(result['batches'][0]['batch_id'])
        self.assertTrue(path.exists())

    def test_forged_path_in_journal_rejected(self):
        path, _ = self.add()
        result = self.hold()
        journal = self.runtime / (result['batch_id'] + '.json')
        data = json.loads(journal.read_text(encoding='utf-8'))
        data['items'][0]['name'] = '../other.txt'
        journal.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            recovery.restore(result['batch_id'])
        self.assertFalse(path.exists())

    def test_missing_journal_fields_are_unavailable_without_breaking_status(self):
        self.add()
        result = self.hold()
        journal = self.runtime / (result['batch_id'] + '.json')
        original = json.loads(journal.read_text(encoding='utf-8'))
        for key in ('holding_identity', 'root_identity', 'created_at'):
            with self.subTest(key=key):
                data = json.loads(json.dumps(original))
                del (data['items'][0] if key == 'holding_identity' else data)[key]
                journal.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    recovery._load(result['batch_id'])
                self.assertEqual(recovery.status()['batches'][0]['status'], 'unavailable')
                with patch.object(recovery, '_move', side_effect=AssertionError('must not move')):
                    self.assertEqual(recovery.reconcile()['batches'][0]['status'], 'unavailable')
                self.assertTrue(pathlib.Path(original['items'][0]['holding']).exists())

    def test_invalid_journal_field_shapes_are_unavailable(self):
        self.add()
        result = self.hold()
        journal = self.runtime / (result['batch_id'] + '.json')
        original = json.loads(journal.read_text(encoding='utf-8'))
        for key, value in [('root_identity', []), ('created_at', 1), ('created_at', 'invalid')]:
            with self.subTest(key=key, value=value):
                data = json.loads(json.dumps(original))
                data[key] = value
                journal.write_text(json.dumps(data), encoding='utf-8')
                self.assertEqual(recovery.status()['batches'][0]['status'], 'unavailable')

    def test_destination_collision_does_not_overwrite(self):
        path, _ = self.add()
        real = recovery._move
        def collide(batch, row, restoring=False):
            pathlib.Path(row['holding']).write_bytes(b'collision')
            return real(batch, row, restoring)
        with patch.object(recovery, '_move', collide):
            result = self.hold()
        self.assertTrue(path.exists())
        batch = recovery._load(result['batch_id'])
        self.assertEqual(pathlib.Path(batch['items'][0]['holding']).read_bytes(), b'collision')
        self.assertEqual(result['held_count'], 0)


if __name__ == '__main__':
    unittest.main()
