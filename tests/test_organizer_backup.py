import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import updater


class OrganizerBackupTests(unittest.TestCase):
    def test_content_judgment_and_recovery_records_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);history=root/'desktop_recovery';history.mkdir()
            state=root/'desktop_ai_state.json';state.write_text('{"enabled":true,"records":{}}',encoding='utf-8')
            journal=history/'fixture.json';journal.write_text('old',encoding='utf-8')
            with patch.object(updater,'ROOT',root),patch.object(updater,'BACKUPS',root/'backups'):
                saved=updater.backup()
                state.write_text('{"enabled":true,"records":{"new":"observation"}}',encoding='utf-8')
                journal.write_text('new recovery result',encoding='utf-8')
                updater.restore(saved)
            current=json.loads(state.read_text(encoding='utf-8'))
            self.assertFalse(current['enabled']);self.assertEqual(current['records'],{'new':'observation'})
            self.assertEqual(journal.read_text(encoding='utf-8'),'new recovery result')

    def test_update_waits_for_server_before_taking_backup(self):
        order=[]
        def stopped():
            order.append('server stopped');return False
        def backup():
            order.append('backup');raise RuntimeError('fixture stop before code update')
        with patch.object(updater,'fetch',return_value={'phase':'available'}),patch.object(updater,'git',return_value=SimpleNamespace(stdout='before',returncode=0)),patch.object(updater,'port_open',side_effect=stopped),patch.object(updater,'backup',side_effect=backup),patch.object(updater,'write'),patch.object(updater,'launch'):
            updater.apply()
        self.assertEqual(order,['server stopped','backup'])

    def test_rollback_pauses_automation_and_preserves_newer_job_authority(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            runtime=root/'organizer_runtime'
            runtime.mkdir()
            original={'policy':{'auto_enabled':True,'source_dir':'approved-inbox'},
                      'jobs':[{'id':'a'*32,'scope':{'revision':1}}],'fingerprint':'before'}
            state=root/'organizer_policy.json'
            state.write_text(json.dumps(original),encoding='utf-8')
            journal=runtime/('a'*32+'.json')
            journal.write_text('before',encoding='utf-8')
            with patch.object(updater,'ROOT',root),patch.object(updater,'BACKUPS',root/'backups'):
                backup=updater.backup()
                current=dict(original,jobs=original['jobs']+[{'id':'b'*32,'scope':{'revision':2}}])
                state.write_text(json.dumps(current),encoding='utf-8')
                journal.write_text('newer movement result',encoding='utf-8')
                updater.restore(backup)
            restored=json.loads(state.read_text(encoding='utf-8'))
            self.assertFalse(restored['policy']['auto_enabled'])
            self.assertEqual([x['id'] for x in restored['jobs']],['a'*32,'b'*32])
            self.assertEqual(journal.read_text(encoding='utf-8'),'newer movement result')

    def test_missing_journal_is_recovered_and_corrupt_current_state_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)
            backup=root/'backup'
            (backup/'organizer_runtime').mkdir(parents=True)
            (backup/'organizer_runtime'/'job.json').write_text('saved journal',encoding='utf-8')
            state=root/'organizer_policy.json'
            state.write_bytes(b'{invalid')
            (backup/'organizer_policy.json').write_text('{"policy":null,"jobs":[]}',encoding='utf-8')
            with patch.object(updater,'ROOT',root):
                with self.assertRaises(ValueError):
                    updater.restore(backup)
            self.assertEqual(state.read_bytes(),b'{invalid')
