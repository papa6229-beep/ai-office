import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
try:
    import file_executor as executor
except ImportError:
    executor = None


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(executor, 'Safe executor has not been implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.src, self.dst = self.root / 'source', self.root / 'destination'
        self.src.mkdir()
        self.dst.mkdir()
        self.journal = self.root / 'journal.json'

    def add(self, name='notes.txt', content=b'generated fixture'):
        p = self.src / name
        p.write_bytes(content)
        return p

    def plan(self):
        return executor.plan(str(self.src), str(self.dst), str(self.journal))

    def test_plan_execute_undo_preserves_bytes_and_is_idempotent(self):
        original = self.add()
        plan = self.plan()
        self.assertEqual(plan['status'], 'planned')
        self.assertTrue(original.exists())
        target = pathlib.Path(plan['items'][0]['destination'])
        self.assertEqual(executor.execute(self.journal)['status'], 'completed')
        self.assertFalse(original.exists())
        self.assertEqual(target.read_bytes(), b'generated fixture')
        self.assertEqual(executor.execute(self.journal)['status'], 'completed')
        self.assertEqual(executor.undo(self.journal)['status'], 'undone')
        self.assertEqual(original.read_bytes(), b'generated fixture')
        self.assertFalse(target.exists())
        self.assertEqual(executor.undo(self.journal)['status'], 'undone')

    def test_collision_never_overwrites_identical_content(self):
        original = self.add()
        plan = self.plan()
        target = pathlib.Path(plan['items'][0]['destination'])
        target.parent.mkdir()
        target.write_bytes(original.read_bytes())
        result = executor.execute(self.journal)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(original.exists())
        self.assertEqual(target.read_bytes(), b'generated fixture')
        self.assertEqual(result['items'][0]['status'], 'exception')

    def test_edited_source_is_retained(self):
        source = self.add()
        self.plan()
        source.write_bytes(b'edited after plan')
        self.assertEqual(executor.execute(self.journal)['items'][0]['status'], 'exception')
        self.assertEqual(source.read_bytes(), b'edited after plan')

    def test_edited_destination_is_not_undone(self):
        source = self.add()
        plan = self.plan()
        executor.execute(self.journal)
        target = pathlib.Path(plan['items'][0]['destination'])
        target.write_bytes(b'edited after move')
        self.assertEqual(executor.undo(self.journal)['status'], 'undo_partial')
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), b'edited after move')

    def test_undo_source_collision_is_retained(self):
        source = self.add()
        self.plan()
        executor.execute(self.journal)
        source.write_bytes(b'new source')
        self.assertEqual(executor.undo(self.journal)['status'], 'undo_partial')
        self.assertEqual(source.read_bytes(), b'new source')

    def test_crash_after_move_resumes_using_original_identity(self):
        source = self.add()
        self.plan()
        real_move = executor._move_no_replace
        def interrupted(src, dst):
            real_move(src, dst)
            raise KeyboardInterrupt('Simulated crash after move')
        with patch.object(executor, '_move_no_replace', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                executor.execute(self.journal)
        self.assertEqual(executor.load(self.journal)['items'][0]['status'], 'moving')
        self.assertFalse(source.exists())
        self.assertEqual(executor.execute(self.journal)['status'], 'completed')
        self.assertEqual(executor.execute(self.journal)['status'], 'completed')

    def test_crash_after_undo_resumes(self):
        source = self.add()
        self.plan()
        executor.execute(self.journal)
        real_move = executor._move_no_replace
        def interrupted(src, dst):
            real_move(src, dst)
            raise KeyboardInterrupt('Simulated crash after undo')
        with patch.object(executor, '_move_no_replace', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                executor.undo(self.journal)
        self.assertTrue(source.exists())
        self.assertEqual(executor.undo(self.journal)['status'], 'undone')

    def test_skips_unknown_scripts_adobe_and_subdirectories(self):
        for name in ['run.py', 'shortcut.lnk', 'source.ai', 'layer.psd', 'unknown.xyz']:
            self.add(name)
        (self.src / 'nested').mkdir()
        result = self.plan()
        self.assertEqual(len(result['items']), 6)
        self.assertTrue(all(item['status'] == 'skipped' for item in result['items']))
        executor.execute(self.journal)
        self.assertEqual(len(list(self.src.iterdir())), 6)

    def test_tampered_destination_and_fingerprint_are_rejected(self):
        self.add()
        plan = self.plan()
        for mutation in [lambda j: j['items'][0].update(destination=str(self.root / 'outside.txt')),
                         lambda j: j['items'][0]['fingerprint'].update(sha256='invalid')]:
            data = json.loads(json.dumps(plan))
            mutation(data)
            self.journal.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                executor.execute(self.journal)
        self.assertTrue((self.src / 'notes.txt').exists())

    def test_ancestor_roots_and_existing_journal_are_rejected(self):
        with self.assertRaises(ValueError):
            executor.plan(self.src, self.root, self.journal)
        self.add()
        self.plan()
        with self.assertRaises(ValueError):
            self.plan()

    def test_recorded_roots_must_stay_in_journal_run_directory(self):
        self.add()
        job = self.plan()
        outside = self.root.parent / ('outside-' + job['id'])
        # No external file/folder needs to be created to reject the escaped scope.
        job['source_dir'] = str(outside)
        job['items'][0]['source'] = str(outside / 'notes.txt')
        self.journal.write_text(json.dumps(job), encoding='utf-8')
        with self.assertRaises(ValueError):
            executor.load(self.journal)

    def test_interrupted_move_does_not_adopt_identical_copy(self):
        source = self.add()
        job = self.plan()
        item = job['items'][0]
        target = pathlib.Path(item['destination'])
        target.parent.mkdir()
        target.write_bytes(source.read_bytes())
        # Move original aside inside this test-only directory to simulate absence.
        source.rename(self.root / 'original-retained.txt')
        item['status'] = 'moving'
        self.journal.write_text(json.dumps(job), encoding='utf-8')
        result = executor.execute(self.journal)
        self.assertEqual(result['items'][0]['status'], 'moving')
        self.assertEqual(target.read_bytes(), b'generated fixture')

    def test_file_count_and_size_bounds(self):
        self.add('large.txt', b'12345')
        with patch.object(executor, 'MAX_FILE_BYTES', 4):
            self.assertEqual(self.plan()['items'][0]['status'], 'skipped')

    def test_journal_flush_failure_prevents_any_move(self):
        source = self.add()
        self.plan()
        with patch.object(executor.os, 'fsync', side_effect=OSError('Disk failure')):
            with self.assertRaises(OSError):
                executor.execute(self.journal)
        self.assertTrue(source.exists())
        self.assertEqual(executor.load(self.journal)['items'][0]['status'], 'planned')

    def test_actual_symlink_is_skipped_when_available(self):
        source = self.add()
        link = self.src / 'linked.txt'
        try:
            link.symlink_to(source)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable')
        job = self.plan()
        linked = next(i for i in job['items'] if i['name'] == 'linked.txt')
        self.assertEqual(linked['status'], 'skipped')
        executor.execute(self.journal)
        self.assertTrue(link.is_symlink())

    def test_case_insensitive_scope_rejects_ancestor(self):
        self.add()
        with self.assertRaises(ValueError):
            executor.plan(self.src, str(self.root).upper(), self.journal)

    def test_post_move_verification_failure_preserves_recovery_intent(self):
        self.add()
        self.plan()
        real_matches = executor._matches
        def uncertain(path, expected):
            if pathlib.Path(path).parent.name == '텍스트':
                return False
            return real_matches(path, expected)
        with patch.object(executor, '_matches', side_effect=uncertain):
            result = executor.execute(self.journal)
        self.assertEqual(result['items'][0]['status'], 'moving')
        self.assertEqual(executor.execute(self.journal)['status'], 'completed')

    def test_execute_refuses_interrupted_undo(self):
        self.add()
        job = self.plan()
        executor.execute(self.journal)
        job = executor.load(self.journal)
        job['items'][0]['status'] = 'undoing'
        self.journal.write_text(json.dumps(job), encoding='utf-8')
        with self.assertRaises(ValueError):
            executor.execute(self.journal)
        self.assertEqual(executor.undo(self.journal)['status'], 'undone')

    def test_hard_link_files_are_skipped(self):
        source = self.add()
        os.link(source, self.root / 'linked-outside.txt')
        self.assertEqual(self.plan()['items'][0]['status'], 'skipped')

    def test_final_journal_failure_resumes_without_second_move(self):
        self.add()
        self.plan()
        real_save = executor._save
        calls = 0
        def failing(journal, job, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Journal commit interrupted')
            return real_save(journal, job, *args, **kwargs)
        with patch.object(executor, '_save', side_effect=failing):
            with self.assertRaises(OSError):
                executor.execute(self.journal)
        self.assertEqual(executor.load(self.journal)['items'][0]['status'], 'moving')
        with patch.object(executor, '_move_no_replace', side_effect=AssertionError('Must recover, not move again')):
            self.assertEqual(executor.execute(self.journal)['status'], 'completed')

    def test_identical_replacement_preserving_mtime_cannot_be_undone(self):
        self.add()
        job = self.plan()
        executor.execute(self.journal)
        target = pathlib.Path(job['items'][0]['destination'])
        old = target.stat()
        target.rename(self.root / 'retained-original.txt')
        target.write_bytes(b'generated fixture')
        os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(executor.undo(self.journal)['status'], 'undo_partial')
        self.assertTrue(target.exists())

    def test_repeated_unreadable_recovery_preserves_intent_then_recovers(self):
        source = self.add()
        self.plan()
        real_move = executor._move_no_replace
        def interrupted(src, dst):
            real_move(src, dst)
            raise KeyboardInterrupt()
        with patch.object(executor, '_move_no_replace', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                executor.execute(self.journal)
        for _ in range(2):
            with patch.object(executor, '_matches', return_value=False):
                result = executor.execute(self.journal)
                self.assertEqual(result['items'][0]['status'], 'moving')
        self.assertFalse(source.exists())
        self.assertEqual(executor.execute(self.journal)['status'], 'completed')
        self.assertEqual(executor.undo(self.journal)['status'], 'undone')

    def test_execute_blocks_completed_undo_item_before_final_job_commit(self):
        self.add()
        self.plan()
        executor.execute(self.journal)
        real_save = executor._save
        def interrupted(journal, job, *args, **kwargs):
            # Crash after the per-item undone record, before the aggregate status.
            if job['items'][0]['status'] == 'undone':
                real_save(journal, job, *args, **kwargs)
                raise KeyboardInterrupt()
            return real_save(journal, job, *args, **kwargs)
        with patch.object(executor, '_save', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                executor.undo(self.journal)
        job = executor.load(self.journal)
        self.assertEqual(job['direction'], 'undo')
        with self.assertRaises(ValueError):
            executor.execute(self.journal)
        self.assertEqual(executor.undo(self.journal)['status'], 'undone')

    def test_undo_direction_is_saved_before_first_move(self):
        self.add()
        self.plan()
        executor.execute(self.journal)
        with patch.object(executor, '_move_no_replace', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                executor.undo(self.journal)
        self.assertEqual(executor.load(self.journal)['direction'], 'undo')

    def test_undo_before_forward_rename_keeps_original_without_move(self):
        source = self.add()
        self.plan()
        with patch.object(executor, '_move_no_replace', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                executor.execute(self.journal)
        self.assertEqual(executor.load(self.journal)['items'][0]['status'], 'moving')
        with patch.object(executor, '_move_no_replace', side_effect=AssertionError('Original already in place')):
            result = executor.undo(self.journal)
        self.assertEqual(result['status'], 'undone')
        self.assertEqual(source.read_bytes(), b'generated fixture')

    def test_adobe_original_skip_explains_dependency_check(self):
        self.add('design.ai')
        self.add('layer.psd')
        job = self.plan()
        self.assertTrue(all(i['reason'] == '외부 연결 자료 확인이 필요한 디자인 원본입니다.'
                            for i in job['items']))

    def test_busy_lock_rejects_new_work(self):
        self.add()
        with executor.LOCK:
            with self.assertRaises(ValueError):
                self.plan()

    def test_non_windows_executor_rejects_move(self):
        self.add()
        self.plan()
        with patch.object(executor.sys, 'platform', 'linux'):
            with self.assertRaises(ValueError):
                executor.execute(self.journal)
        self.assertTrue((self.src / 'notes.txt').exists())

    def test_reparse_attributes_are_rejected(self):
        source = self.add()
        self.plan()
        real_safe = executor._safe_path
        def unsafe(path, *args, **kwargs):
            if pathlib.Path(path) == source:
                raise ValueError('Reparse fixture')
            return real_safe(path, *args, **kwargs)
        with patch.object(executor, '_safe_path', side_effect=unsafe):
            result = executor.execute(self.journal)
        self.assertEqual(result['items'][0]['status'], 'exception')
        self.assertTrue(source.exists())


if __name__ == '__main__':
    unittest.main()
