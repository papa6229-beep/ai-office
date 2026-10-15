import os
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch
import organizer_policy as service
import file_executor as engine


class PolicyTests(unittest.TestCase):
    def setUp(self):
        service.stop_worker()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.source, self.dest = self.root / 'source', self.root / 'dest'
        self.source.mkdir()
        self.dest.mkdir()
        environment = patch.dict(os.environ, {'APPDATA': 'C:/policy-fixture-protected-roaming',
            'LOCALAPPDATA': 'C:/policy-fixture-protected-local'})
        environment.start()
        self.addCleanup(environment.stop)
        for name, value in [('STATE', self.root / 'policy.json'), ('RUNTIME', self.root / 'runtime'),
                            ('LAST_ERROR', '')]:
            p = patch.object(service, name, value)
            p.start()
            self.addCleanup(p.stop)

    def file(self, name='notes.txt'):
        path = self.source / name
        path.write_text('fixture contents', encoding='utf-8')
        os.utime(path, (time.time() - 30, time.time() - 30))
        return path

    def save(self, **kw):
        data = {'source_dir': str(self.source), 'destination_dir': str(self.dest),
                'exclusions': [], 'auto_enabled': False, 'authorized': True}
        data.update(kw)
        return service.save(data)

    def test_once_scope_authorization_and_readonly_preview(self):
        source = self.file()
        with self.assertRaises(ValueError):
            self.save(authorized=False)
        self.save()
        result = service.preview()
        self.assertTrue(source.exists())
        self.assertEqual(result['jobs'][0]['counts']['planned'], 1)
        self.assertEqual(service.preview()['jobs'][0]['id'], result['jobs'][0]['id'])

    def test_production_handle_move_undo_and_no_empty_job(self):
        source = self.file()
        self.save(auto_enabled=True)
        result = service.run()
        self.assertEqual(result['jobs'][0]['status'], 'completed')
        self.assertFalse(source.exists())
        job_id = result['jobs'][0]['id']
        self.assertEqual(len(service.run()['jobs']), 1)
        result = service.undo(job_id)
        self.assertTrue(source.exists())
        self.assertFalse(result['policy']['auto_enabled'])
        self.assertEqual(result['jobs'][0]['status'], 'undone')

    def test_protected_excluded_and_recent_files_remain(self):
        for name in ('settings.json', 'office.db', 'secrets.txt', '.hidden.txt', 'keep.txt', 'run.ps1'):
            self.file(name)
        recent = self.file('recent.txt')
        os.utime(recent, None)
        self.save(exclusions=['keep*'])
        result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['planned'], 0)
        self.assertEqual(len(list(self.source.iterdir())), 7)

    def test_collision_and_modified_undo_preserve_files(self):
        source = self.file()
        self.save()
        service.preview()
        category = self.dest / '텍스트'
        category.mkdir()
        target = category / source.name
        target.write_text('other')
        result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['exception'], 1)
        self.assertTrue(source.exists())
        self.assertEqual(target.read_text(), 'other')

    def test_policy_changes_blocked_while_pending_or_auto(self):
        self.file()
        self.save()
        service.preview()
        with self.assertRaises(ValueError):
            self.save(exclusions=['*.txt'])

    def test_job_id_traversal_and_untrusted_scope_rejected(self):
        self.file()
        self.save()
        result = service.preview()
        with self.assertRaises(ValueError):
            service.undo('../anything')
        journal = service.RUNTIME / (result['jobs'][0]['id'] + '.json')
        with self.assertRaises(ValueError):
            engine.execute(journal)

    def test_locked_source_refuses_move(self):
        source = self.file()
        self.save()
        service.preview()
        with source.open('rb'):
            result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['exception'], 1)
        self.assertTrue(source.exists())

    def test_live_edit_cannot_enter_held_move_window(self):
        source = self.file()
        self.save()
        service.preview()
        actual = engine._roots
        blocked = []
        def probe(*args, **kwargs):
            # _held_move is the only caller with journal embedded in job.
            import inspect
            if any(frame.function == '_held_move' for frame in inspect.stack()):
                try:
                    with source.open('wb') as stream:
                        stream.write(b'unsafe edit')
                except PermissionError:
                    blocked.append(True)
            return actual(*args, **kwargs)
        with patch.object(engine, '_roots', side_effect=probe):
            result = service.run()
        self.assertEqual(blocked, [True])
        self.assertEqual(result['jobs'][0]['counts']['moved'], 1)
        self.assertEqual((self.dest / '텍스트' / source.name).read_text(), 'fixture contents')

    def test_crash_after_handle_move_resume(self):
        source = self.file()
        self.save()
        result = service.preview()
        actual = engine._held_move
        def crash(*args):
            actual(*args)
            raise KeyboardInterrupt('fixture crash')
        with patch.object(engine, '_held_move', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                service.run()
        result = service.resume(result['jobs'][0]['id'])
        self.assertEqual(result['jobs'][0]['status'], 'completed')
        self.assertFalse(source.exists())

    def test_worker_starts_once_and_stops(self):
        service.ensure_worker()
        first = service.WORKER
        service.ensure_worker()
        self.assertIs(first, service.WORKER)
        service.stop_worker()
        self.assertFalse(first.is_alive())

    def test_edit_after_move_and_undo_source_collision_are_preserved(self):
        source = self.file()
        self.save()
        result = service.run()
        job_id = result['jobs'][0]['id']
        target = self.dest / '텍스트' / source.name
        target.write_text('subsequent edit')
        result = service.undo(job_id)
        self.assertEqual(result['jobs'][0]['status'], 'undo_partial')
        self.assertFalse(source.exists())
        self.assertEqual(target.read_text(), 'subsequent edit')

    def test_old_job_scope_undo_survives_policy_change(self):
        source = self.file()
        self.save()
        result = service.run()
        job_id = result['jobs'][0]['id']
        second = self.root / 'second-source'
        second.mkdir()
        self.save(source_dir=str(second))
        result = service.undo(job_id)
        self.assertTrue(source.exists())
        self.assertEqual(result['policy']['source_dir'], str(second))

    def test_existing_category_replacement_never_redirects_move(self):
        self.file()
        category = self.dest / '텍스트'
        category.mkdir()
        self.save()
        service.preview()
        category.rename(self.dest / 'old-category')
        category.mkdir()
        result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['exception'], 1)
        self.assertTrue((self.source / 'notes.txt').exists())

    def test_category_rename_blocked_inside_held_window(self):
        self.file()
        self.save()
        service.preview()
        actual = engine._held_move_pinned
        blocked = []
        def probe(*args):
            category = self.dest / '텍스트'
            try:
                category.rename(self.dest / 'swapped')
            except OSError:
                blocked.append(True)
            return actual(*args)
        with patch.object(engine, '_held_move_pinned', side_effect=probe):
            result = service.run()
        self.assertEqual(blocked, [True], result)
        self.assertEqual(result['jobs'][0]['counts']['moved'], 1)

    def test_directory_identity_change_rejects_execution(self):
        self.file()
        self.save()
        service.preview()
        self.dest.rename(self.root / 'old-dest')
        self.dest.mkdir()
        with self.assertRaises(ValueError):
            service.run()

    def test_malformed_settings_are_not_silently_overwritten(self):
        import json
        service.STATE.write_text(json.dumps({'policy': {'bad': True}, 'jobs': []}))
        with self.assertRaises(ValueError):
            service.status()
        self.assertIn('bad', service.STATE.read_text())

    def test_pending_undo_aggregate_is_resumed_before_new_job(self):
        self.file()
        self.save()
        result = service.run()
        job_id = result['jobs'][0]['id']
        actual = engine._save
        def crash_final(journal, job, **kw):
            if job['status'] == 'undone':
                raise KeyboardInterrupt('final aggregate fixture crash')
            return actual(journal, job, **kw)
        with patch.object(engine, '_save', side_effect=crash_final):
            with self.assertRaises(KeyboardInterrupt):
                service.undo(job_id)
        self.assertEqual(len(service.preview()['jobs']), 1)
        with self.assertRaises(ValueError):
            service.run()
        self.assertEqual(service.resume(job_id)['jobs'][0]['status'], 'undone')

    def test_multiple_batches_not_starved_by_unsupported_prefix(self):
        for index in range(105):
            self.file('a%03d.exe' % index)
            self.file('z%03d.txt' % index)
        self.save()
        result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['moved'], 100)
        result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['moved'], 5)
        self.assertEqual(len(list(self.source.glob('*.txt'))), 0)
        self.assertEqual(len(list(self.source.glob('*.exe'))), 105)
        self.assertEqual(len(service.run()['jobs']), 2)

    def test_explicit_resume_retries_a_released_lock(self):
        source = self.file()
        self.save()
        service.preview()
        with source.open('rb'):
            result = service.run()
        job_id = result['jobs'][0]['id']
        self.assertEqual(result['jobs'][0]['counts']['exception'], 1)
        result = service.resume(job_id)
        self.assertEqual(result['jobs'][0]['counts']['moved'], 1)

    def test_worker_tick_runs_enabled_policy_and_error_pauses(self):
        self.file()
        self.save(auto_enabled=True)
        with patch.object(service.STOP, 'wait', side_effect=[False, True]):
            service._worker()
        self.assertEqual(service.status()['jobs'][0]['counts']['moved'], 1)
        with patch.object(service.STOP, 'wait', side_effect=[False, True]), \
                patch.object(service, 'run', side_effect=ValueError('fixture failure')):
            service._worker()
        self.assertFalse(service.status()['policy']['auto_enabled'])
        self.assertEqual(service.status()['last_error'], 'fixture failure')

    def test_mixed_design_folder_blocks_reference_asset_moves(self):
        self.file('source.ai')
        asset = self.file('linked.png')
        self.save()
        with self.assertRaisesRegex(ValueError, '가상 정리'):
            service.preview()
        self.assertTrue(asset.exists())

    def test_new_design_dependency_blocks_approved_plan_but_not_undo(self):
        source = self.file()
        self.save()
        result = service.preview()
        marker = self.file('new.psd')
        result = service.run()
        self.assertEqual(result['jobs'][0]['counts']['exception'], 1)
        self.assertTrue(source.exists())
        marker.rename(self.root / 'fixture-outside.psd')
        result = service.resume(result['jobs'][0]['id'])
        self.assertEqual(result['jobs'][0]['counts']['moved'], 1)
        self.file('new.psd')
        self.assertEqual(service.undo(result['jobs'][0]['id'])['jobs'][0]['status'], 'undone')

    def test_replaced_scope_directory_needs_new_authorization_revision(self):
        self.save()
        self.dest.rename(self.root / 'previous-dest')
        self.dest.mkdir()
        with self.assertRaisesRegex(ValueError, '이동 허용'):
            self.save(authorized=False)
        self.assertEqual(self.save()['policy']['revision'], 2)

    def test_windows_case_cannot_bypass_protected_repository(self):
        with self.assertRaisesRegex(ValueError, 'AI OFFICE'):
            self.save(source_dir=str(service.ROOT).swapcase())
        self.assertTrue(engine._inside(str(service.ROOT).swapcase(), service.ROOT))

    def test_cancel_unexecuted_preview_allows_policy_change(self):
        source = self.file()
        self.save()
        result = service.preview()
        result = service.undo(result['jobs'][0]['id'])
        self.assertEqual(result['jobs'][0]['status'], 'undone')
        self.assertEqual(result['jobs'][0]['counts']['planned'], 0)
        self.assertTrue(source.exists())
        result = self.save(exclusions=['*.txt'])
        self.assertEqual(result['policy']['revision'], 2)


if __name__ == '__main__':
    unittest.main()
