import json
import os
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import Mock, patch

import app
import capabilities
import file_workspace
import organizer_policy
from assistant_tools import ToolRegistry


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.db = self.root / 'office.db'
        self.files = Mock()
        self.item = {'id': 'a' * 64, 'name': 'design.psd', 'path': 'server-only-path',
                     'kind': 'file', 'identity': {'dev': 1, 'ino': 2}}
        self.files.search.return_value = {'items': [self.item], 'total': 1, 'truncated': False,
                                         'scopes': ['fixture'], 'warnings': []}
        self.files.reveal.return_value = {'ok': True, 'dispatched': True}
        self.service = capabilities.CapabilityService(self.db, files=self.files)
        self.scope = {'policy_id': 'b'*32, 'revision': 1, 'source_dir': 'source',
                      'destination_dir': 'dest', 'source_identity': {'dev': 1, 'ino': 3},
                      'destination_identity': {'dev': 1, 'ino': 4}, 'exclusions': []}

    def search(self):
        return self.service.search('design')['items'][0]

    def test_search_issues_ticket_without_mutating_workspace_result(self):
        item = self.search()
        self.assertEqual(len(item['selection_token']), 32)
        self.assertNotIn('selection_token', self.item)
        self.assertEqual(self.service.search()['scopes'], ['fixture'])

    def test_shared_reveal_revalidates_server_snapshot_and_replays_once(self):
        item = self.search()
        request = uuid.uuid4().hex
        first = self.service.reveal(item['id'], item['selection_token'], request)
        other_instance = capabilities.CapabilityService(self.db, files=self.files)
        self.assertEqual(other_instance.reveal(item['id'], item['selection_token'], request), first)
        self.files.reveal.assert_called_once_with(item['id'], expected=self.item)

    def test_file_ticket_cannot_select_other_id_or_unknown_ticket(self):
        item = self.search()
        for identifier, ticket in [('c'*64,item['selection_token']), (item['id'],'fabricated')]:
            with self.assertRaises(ValueError):
                self.service.reveal(identifier, ticket, uuid.uuid4().hex)
        self.files.reveal.assert_not_called()

    def test_expired_file_selection_requires_new_search(self):
        with patch('capabilities.time.time', return_value=time.time() - 2000):
            item = self.search()
        with self.assertRaises(ValueError):
            self.service.reveal(item['id'], item['selection_token'], uuid.uuid4().hex)
        self.files.reveal.assert_not_called()

    def test_request_id_cannot_be_reused_for_different_payload(self):
        first, second = self.search(), self.search()
        request = uuid.uuid4().hex
        self.service.reveal(first['id'],first['selection_token'],request)
        with self.assertRaises(ValueError):
            self.service.reveal(second['id'],second['selection_token'],request)

    def test_failed_and_interrupted_effects_are_not_blindly_replayed(self):
        item = self.search()
        request = uuid.uuid4().hex
        self.files.reveal.side_effect = OSError('fixture failure')
        with self.assertRaises(OSError):
            self.service.reveal(item['id'],item['selection_token'],request)
        self.files.reveal.side_effect = None
        with self.assertRaises(ValueError):
            self.service.reveal(item['id'],item['selection_token'],request)
        self.files.reveal.assert_called_once()
        class Interrupted(BaseException): pass
        request = uuid.uuid4().hex
        self.files.reveal.side_effect = Interrupted()
        with self.assertRaises(Interrupted):
            self.service.reveal(item['id'],item['selection_token'],request)
        with self.assertRaises(ValueError):
            self.service.reveal(item['id'],item['selection_token'],request)

    def test_policy_requires_current_scope_under_lock_and_no_extra_fields(self):
        saved = {'policy': dict(self.scope,authorized=True,auto_enabled=False), 'jobs': []}
        with patch('organizer_policy._read',return_value=saved), patch('organizer_policy.run') as run:
            with self.assertRaises(ValueError):
                self.service.policy('run',{},uuid.uuid4().hex,expected_scope=dict(self.scope,revision=2))
            with self.assertRaises(ValueError):
                self.service.policy('run',{'path':'anything'},uuid.uuid4().hex)
            run.assert_not_called()

    def test_policy_replay_preserves_actual_partial_status_and_calls_once(self):
        saved = {'policy': dict(self.scope,authorized=True,auto_enabled=False), 'jobs': []}
        result = {'jobs':[{'id':'d'*32,'status':'partial','counts':{'moved':2,'exception':1}}]}
        request = uuid.uuid4().hex
        with patch('organizer_policy._read',return_value=saved), patch('organizer_policy.run',return_value=result) as run, patch('organizer_policy.ensure_worker'):
            first=self.service.policy('run',{},request,expected_scope=self.scope)
            self.assertEqual(first['jobs'],result['jobs'])
            self.assertEqual(first['policy_scope'],self.scope)
            self.assertEqual(self.service.policy('run',{},request,expected_scope=self.scope),first)
            run.assert_called_once()

    def test_policy_replay_keeps_old_display_bound_to_old_scope_after_change(self):
        saved = {'policy':dict(self.scope,authorized=True,auto_enabled=False),'jobs':[]}
        request=uuid.uuid4().hex
        with patch('organizer_policy._read',return_value=saved), patch('organizer_policy.status',side_effect=lambda: {'policy':dict(saved['policy']),'jobs':[]}), patch('organizer_policy.run',side_effect=lambda: {'policy':dict(saved['policy']),'jobs':[]}) as run, patch('organizer_policy.ensure_worker'):
            first=self.service.policy('run',{},request,expected_scope=self.scope)
            saved['policy']['revision']=2
            saved['policy']['source_dir']='another source'
            replay=self.service.policy('run',{},request,expected_scope=self.scope)
            self.assertEqual(replay,first)
            self.assertEqual(replay['policy']['revision'],1)
            self.assertEqual(replay['policy_scope']['revision'],1)
            with self.assertRaises(ValueError):
                self.service.policy('run',{},uuid.uuid4().hex,expected_scope=replay['policy_scope'])
            run.assert_called_once()

    def test_policy_job_snapshot_is_checked_and_undo_uses_saved_record(self):
        record = {'id':'e'*32,'scope':self.scope}
        with patch('organizer_policy._read',return_value={'policy':None,'jobs':[record]}), patch('organizer_policy.undo',return_value={'jobs':[]}) as undo, patch('organizer_policy.ensure_worker'):
            with self.assertRaises(ValueError):
                self.service.policy('undo',{'id':record['id']},uuid.uuid4().hex,expected_job=dict(record,scope={}))
            self.service.policy('undo',{'id':record['id']},uuid.uuid4().hex,expected_job=record)
            undo.assert_called_once_with(record['id'])

    def test_manifest_exposes_gaps_instead_of_assuming_login_grants_adapter(self):
        registry = ToolRegistry(self.db)
        with patch.object(registry.capabilities,'policy_snapshot',return_value={'availability':'needs_setup'}):
            result = registry.execute('office_capabilities', {'category':'messenger'}, {'refs':{}})
        item = result['data']['items'][0]
        self.assertEqual(item['status'],'unsupported')
        self.assertIn('HanbiroTalk',item['description'])
        self.assertFalse(result['data']['all_features_connected'])

    def test_full_manifest_retains_gaps_within_runtime_public_data_limit(self):
        import assistant_runtime
        import assistant_tools
        registry=ToolRegistry(self.db)
        with patch.object(registry.capabilities,'policy_snapshot',return_value={'availability':'connected'}):
            manifest=registry.capabilities.manifest(assistant_tools._SPECS)
        self.assertLessEqual(len(json.dumps(manifest,ensure_ascii=False,sort_keys=True,allow_nan=False)),24000)
        self.assertEqual(assistant_runtime._public_data(manifest),manifest)
        items={row['name']:row for row in manifest['items']}
        for name in ('messenger_receive_save_forward','browser_form_submit','manuals_execute','organizer_policy_save','model_settings'):
            self.assertEqual(items[name]['status'],'unsupported')
        for name in ('launcher_change_target','launcher_set_image','launcher_card_edit','scan_status','scan_results'):
            self.assertEqual(items[name]['channels'],['chat','click'])
        for name in ('launcher_pages','launcher_page_create','launcher_move_page'):
            self.assertEqual(items[name]['channels'],['chat'])
            self.assertTrue(items[name]['legacy_only'])
        self.assertEqual(items['scan_status']['category'],'files')
        self.assertNotIn('launcher_full_edit',items)
        self.assertNotIn('office_capabilities',items)
        self.assertEqual(manifest['items'][0]['status'],'unsupported')

    def test_chat_search_and_reveal_use_the_same_service_and_issued_ticket(self):
        registry = ToolRegistry(self.db)
        registry.capabilities = self.service
        context = {'refs':{},'step_id':uuid.uuid4().hex}
        result = registry.execute('files_find',{'query':'design'},context)
        row = result['data']['items'][0]
        self.assertNotIn('selection_token',row)
        registry.execute('files_reveal',{'ref':row['ref']},context)
        self.files.reveal.assert_called_once_with(self.item['id'],expected=self.item)
        with self.assertRaises(ValueError):
            registry.execute('files_reveal',{'ref':'fake'},{'refs':{},'step_id':uuid.uuid4().hex})

    def test_chat_policy_status_binds_scope_and_job_references(self):
        registry = ToolRegistry(self.db)
        registry.capabilities = self.service
        record = {'id':'f'*32,'scope':self.scope}
        state = {'policy':dict(self.scope,authorized=True,auto_enabled=False),'jobs':[{'id':record['id']}],
                 'policy_scope':self.scope,'job_scopes':[record],'availability':'connected'}
        context = {'refs':{},'step_id':uuid.uuid4().hex}
        with patch.object(self.service,'policy_snapshot',return_value=state):
            data = registry.execute('organizer_policy_status',{},context)['data']
        with patch.object(self.service,'policy',return_value={'jobs':[]}) as operation:
            registry.execute('organizer_policy_run',{'ref':data['policy_ref']},context)
        self.assertEqual(operation.call_args.kwargs['expected_scope'],self.scope)

    def test_chat_real_temporary_policy_run_undo_and_disable(self):
        source, destination = self.root/'inbox', self.root/'archive'
        source.mkdir()
        destination.mkdir()
        original = source/'brief.txt'
        original.write_text('isolated fixture',encoding='utf-8')
        os.utime(original,(time.time()-30,time.time()-30))
        with patch.dict(os.environ,{'APPDATA':'C:/capability-fixture-roaming','LOCALAPPDATA':'C:/capability-fixture-local'}), patch('organizer_policy.STATE',self.root/'policy.json'), patch('organizer_policy.RUNTIME',self.root/'runtime'), patch('organizer_policy.ensure_worker'):
            organizer_policy.save({'source_dir':str(source),'destination_dir':str(destination),
                                   'exclusions':[],'authorized':True,'auto_enabled':True})
            registry = ToolRegistry(self.db)
            context = {'refs':{},'step_id':uuid.uuid4().hex}
            status = registry.execute('organizer_policy_status',{},context)['data']
            preview = registry.execute('organizer_policy_preview',{'ref':status['policy_ref']},context)
            self.assertTrue(original.exists())
            self.assertEqual(preview['data']['jobs'][0]['counts']['planned'],1)
            context['step_id'] = uuid.uuid4().hex
            result = registry.execute('organizer_policy_run',{'ref':status['policy_ref']},context)
            self.assertFalse(original.exists())
            self.assertEqual(result['data']['jobs'][0]['counts']['moved'],1)
            status = registry.execute('organizer_policy_status',{},context)['data']
            context['step_id'] = uuid.uuid4().hex
            result = registry.execute('organizer_policy_undo',{'ref':status['jobs'][0]['ref']},context)
            self.assertTrue(original.exists())
            self.assertFalse(result['data']['policy']['auto_enabled'])
            self.assertEqual(result['data']['jobs'][0]['status'],'undone')

    def test_actual_temporary_file_search_and_snapshot_rejection(self):
        local_temp = tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parents[1])
        self.addCleanup(local_temp.cleanup)
        folder = pathlib.Path(local_temp.name) / 'fixture'
        folder.mkdir()
        target = folder / 'image.psd'
        target.write_bytes(b'fixture')
        opener = Mock()
        files = file_workspace.FileWorkspace(self.db,workspace=folder,opener=opener)
        service = capabilities.CapabilityService(self.db,files=files)
        with patch('file_workspace._APP_ROOT',folder/'Application'), patch('file_workspace._standard_roots',return_value=[]), patch('desktop_workspace._desktop_roots',return_value=[]), patch('scanner.FILES',[]):
            row = service.search('image.psd')['items'][0]
            target.unlink()
            with self.assertRaises(ValueError):
                service.reveal(row['id'],row['selection_token'],uuid.uuid4().hex)
            opener.assert_not_called()


class FileCapabilityHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.service = capabilities.CapabilityService(self.root/'office.db',files=Mock())
        self.service.files.search.return_value = {'items':[{'id':'a'*64,'path':'fixture','kind':'file','identity':{'dev':1,'ino':2}}], 'total':1}
        self.service.files.reveal.return_value = {'ok':True,'dispatched':True}
        p = patch.object(app,'capability_service',return_value=self.service)
        p.start()
        self.addCleanup(p.stop)
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self,path,payload,headers=None):
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json',**(headers or {})})
        with urllib.request.urlopen(request,timeout=5) as response:
            return json.load(response)

    def test_http_search_reveal_share_server_selection_and_replay_receipt(self):
        row = self.post('/api/files/search',{'token':app.DESK_TOKEN,'query':'fixture'})['items'][0]
        payload = {'token':app.DESK_TOKEN,'id':row['id'],'selection_token':row['selection_token'],'request_id':uuid.uuid4().hex}
        first = self.post('/api/files/reveal',payload)
        self.assertEqual(self.post('/api/files/reveal',payload),first)
        self.service.files.reveal.assert_called_once()

    def test_http_rejects_bad_token_origin_host_and_arbitrary_path(self):
        cases = [({'token':'bad'},{}), ({'token':app.DESK_TOKEN},{'Origin':'https://untrusted.example'}),
                 ({'token':app.DESK_TOKEN},{'Host':'untrusted.example'})]
        for payload,headers in cases:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post('/api/files/search',payload,headers)
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/files/reveal',{'token':app.DESK_TOKEN,'path':'arbitrary','request_id':uuid.uuid4().hex})
        self.assertEqual(error.exception.code,400)
        self.service.files.reveal.assert_not_called()

    def test_http_policy_and_chat_use_the_same_registered_policy_handler(self):
        snapshot={'policy_scope':{'revision':1},'job_scopes':[],'availability':'connected'}
        with patch.object(self.service,'policy',return_value={'jobs':[]}) as policy, patch.object(self.service,'policy_snapshot',return_value=snapshot), patch('organizer_policy.ensure_worker'):
            request = uuid.uuid4().hex
            self.post('/api/desk/policy-enabled',{'token':app.DESK_TOKEN,'enabled':False,'request_id':request,'expected_scope':snapshot['policy_scope']})
            policy.assert_called_once_with('enabled',{'enabled':False},request,expected_scope=snapshot['policy_scope'],expected_job=None)

    def test_http_new_policy_request_requires_observed_scope(self):
        with patch.object(self.service,'policy') as policy:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post('/api/desk/policy-run',{'token':app.DESK_TOKEN,'request_id':uuid.uuid4().hex})
            self.assertEqual(error.exception.code,400)
            policy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
