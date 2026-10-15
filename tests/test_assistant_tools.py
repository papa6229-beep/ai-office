import pathlib
import tempfile
import unittest
from unittest.mock import patch
import windows_app_catalog
from assistant_tools import ToolRegistry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ToolRegistry(pathlib.Path(self.temp.name) / 'office.db')
        self.context = {'request_id': '1'*32, 'step_id': '2'*32, 'refs': {}}

    def test_korean_query_exposes_canonical_candidates_for_native_model(self):
        with patch('windows_app_catalog.catalog', return_value={'items': [{'title': 'Adobe Photoshop 2026', 'target': 'startapp:'+'a'*64}], 'available': True}):
            result = self.registry.execute('apps_find', {'query': '포토샵'}, self.context)
        self.assertEqual(result['data']['items'][0]['title'], 'Adobe Photoshop 2026')
        self.assertNotIn('startapp:', str(result))
        self.assertTrue(self.context['refs'])

    def test_unknown_parameters_and_fabricated_references_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.execute('apps_launch', {'ref': 'fake', 'command': 'calc.exe'}, self.context)
        with self.assertRaises(ValueError):
            self.registry.execute('apps_launch', {'ref': 'fake'}, self.context)

    def test_definitions_disallow_unknown_fields(self):
        definitions = self.registry.definitions()
        self.assertGreater(len(definitions), 25)
        for tool in definitions:
            self.assertIs(tool['function']['parameters']['additionalProperties'], False)
            self.assertIn(self.registry.effect(tool['function']['name']), {'read', 'write', 'external', 'approval'})

    def test_hold_preview_never_moves_without_approval(self):
        candidate = {'id':'a'*64, 'name':'empty.txt', 'reason':'empty'}
        with patch('desktop_judgment.status', return_value={'revision':'b'*32, 'candidates':[candidate], 'items':[candidate]}), patch('desktop_recovery.holdability', return_value={'a'*64:{'allowed':True}}), patch('desktop_recovery.hold', return_value={'held_count':1,'exceptions':[]}) as hold:
            result = self.registry.execute('desktop_analyze_results', {}, self.context)
            ref = result['data']['candidates'][0]['ref']
            preview = self.registry.execute('desktop_hold_preview', {'refs':[ref]}, self.context)
            hold.assert_not_called()
            self.assertEqual(preview['pending']['kind'], 'approval')
            self.assertEqual(preview['pending']['args']['revision'], 'b'*32)
            with self.assertRaises(ValueError):
                self.registry.resolve_pending(preview['pending'], 'forged', self.context)
            hold.assert_not_called()
            self.registry.resolve_pending(preview['pending'], 'approve', self.context)
            hold.assert_called_once_with('b'*32, ['a'*64], '2'*32)

    def test_old_codex_os_label_never_becomes_chatgpt_action_alias(self):
        windows_app_catalog.clear_cache()
        self.addCleanup(windows_app_catalog.clear_cache)
        with patch.object(windows_app_catalog, '_read_apps', return_value=[{'Name':'ChatGPT','AppID':'OpenAI.Codex_123!App'}]):
            item=windows_app_catalog.catalog()['items'][0]
            self.assertNotIn('ChatGPT', item.get('aliases', []))
            result=self.registry.execute('apps_find', {'query':'ChatGPT'}, self.context)
            self.assertEqual(result['data']['items'][0]['title'], 'Codex')
            self.assertNotIn('ChatGPT', str(result['data']['items']))

    def test_virtual_organize_explains_no_physical_move_and_replay(self):
        with patch('desktop_collections.auto_organize', return_value={}) as organize:
            first=self.registry.execute('desktop_auto_organize', {}, self.context)
            second=self.registry.execute('desktop_auto_organize', {}, self.context)
            self.assertEqual(first, second)
            organize.assert_called_once_with(True)
            self.assertIn('가상', first['message'])

    def test_app_add_duplicates_are_preserved_and_launch_durable(self):
        app={'title':'Adobe Photoshop 2026','target':'startapp:'+'a'*64}
        with patch('windows_app_catalog.catalog',return_value={'items':[app],'available':True}), patch('windows_app_catalog.resolve',return_value='Adobe.Photoshop'), patch('windows_app_catalog.launch') as launch:
            ref=self.registry.execute('apps_find',{'query':'포토샵'},self.context)['data']['items'][0]['ref']
            self.registry.execute('launcher_add',{'ref':ref},self.context)
            self.context['step_id']='3'*32
            self.registry.execute('launcher_add',{'ref':ref},self.context)
            self.assertEqual(len(self.registry.launcher.overview()['items']),1)
            self.context['step_id']='4'*32
            self.registry.execute('apps_launch',{'ref':ref},self.context)
            fresh=ToolRegistry(self.registry.db_path)
            fresh.execute('apps_launch',{'ref':ref},self.context)
            launch.assert_called_once_with(app['target'])

    def test_app_choice_private_state_survives_json_roundtrip(self):
        import json
        ref=self.registry._ref(self.context,'app',{'title':'App','target':'startapp:'+'a'*64})
        choice=self.registry.execute('apps_choose',{'refs':[ref],'purpose':'launch'},self.context)
        pending=json.loads(json.dumps(choice['pending']))
        context=json.loads(json.dumps(self.context))
        with patch('windows_app_catalog.launch') as launch:
            self.registry.resolve_pending(pending,ref,context)
            launch.assert_called_once()

    def test_work_create_status_and_note_use_real_records_with_refs(self):
        self.registry.execute('work_create',{'title':'행사 시안'},self.context)
        rows=self.registry.execute('work_list',{},self.context)['data']['tasks']
        self.assertEqual(len(rows),1)
        self.context['step_id']='3'*32
        self.registry.execute('work_status',{'ref':rows[0]['ref'],'status':'doing'},self.context)
        self.context['step_id']='4'*32
        self.registry.execute('work_note',{'ref':rows[0]['ref'],'text':'고객 확인 중'},self.context)
        rows=self.registry.execute('work_list',{},self.context)['data']['tasks']
        self.assertEqual(rows[0]['status'],'doing')
        self.assertEqual(rows[0]['notes'][0]['text'],'고객 확인 중')

    def test_unknown_schema_types_limits_and_effect_rejected(self):
        for name,args in [('apps_find',{'query':False}),('desktop_pin',{'ref':'x','enabled':'yes'}),('apps_choose',{'refs':['x','x'],'purpose':'launch'}),('apps_find',{'query':'x'*4001}),('unknown',{})]:
            with self.subTest(name=name,args=args):
                with self.assertRaises(ValueError):self.registry.execute(name,args,self.context)
        with self.assertRaises(ValueError):self.registry.effect('shell')

    def test_stale_or_ineligible_hold_keeps_originals(self):
        ref=self.registry._ref(self.context,'candidate',{'id':'a'*64,'revision':'b'*32,'name':'old.txt'})
        with patch('desktop_judgment.status',return_value={'revision':'c'*32,'candidates':[{'id':'a'*64}]}),patch('desktop_recovery.hold') as hold:
            with self.assertRaises(ValueError):self.registry.execute('desktop_hold_preview',{'refs':[ref]},self.context)
            hold.assert_not_called()
        with patch('desktop_judgment.status',return_value={'revision':'b'*32,'candidates':[{'id':'a'*64}]}),patch('desktop_recovery.holdability',return_value={'a'*64:{'allowed':False}}),patch('desktop_recovery.hold') as hold:
            with self.assertRaises(ValueError):self.registry.execute('desktop_hold_preview',{'refs':[ref]},self.context)
            hold.assert_not_called()

    def test_policy_preview_is_readonly_and_cannot_run(self):
        with patch('organizer_policy.preview',return_value={'items':[]}),patch('organizer_policy.run') as run:
            result=self.registry.execute('organizer_policy_preview',{},self.context)
            self.assertIn('아직 지원하지',result['message'])
            run.assert_not_called()
            self.assertEqual(self.registry.effect('organizer_policy_preview'),'write')
            with self.assertRaises(ValueError):self.registry.execute('organizer_policy_run',{},self.context)

    def test_native_effect_interruption_is_never_replayed(self):
        class Interrupted(BaseException):pass
        ref=self.registry._ref(self.context,'app',{'title':'App','target':'startapp:'+'a'*64})
        with patch('windows_app_catalog.launch',side_effect=Interrupted):
            with self.assertRaises(Interrupted):self.registry.execute('apps_launch',{'ref':ref},self.context)
        with patch('windows_app_catalog.launch') as launch:
            with self.assertRaises(ValueError):ToolRegistry(self.registry.db_path).execute('apps_launch',{'ref':ref},self.context)
            launch.assert_not_called()

    def test_filesystem_target_cannot_be_submitted_as_app_or_resource(self):
        for name,args in [('apps_launch',{'ref':r'C:\Windows\notepad.exe'}),('launcher_add',{'ref':'startapp:'+'a'*64}),('work_resources',{'ref':'a'*32,'resource_ref':r'C:\secret.txt'})]:
            self.context['step_id']=__import__('uuid').uuid4().hex
            with self.assertRaises(ValueError):self.registry.execute(name,args,self.context)

    def test_analysis_start_returns_real_failure_not_success(self):
        with patch('desktop_judgment.start',return_value={'phase':'error','running':False,'last_error':'설정 오류'}):
            with self.assertRaisesRegex(ValueError,'설정 오류'):
                self.registry.execute('desktop_analyze_start',{},self.context)

    def test_analysis_status_uses_real_progress_fields(self):
        state={'phase':'reading','running':True,'message':'읽는 중','total':10,'processed':4,'grouping_processed':2,'grouping_total':3}
        with patch('desktop_judgment.status',return_value=state):
            result=self.registry.execute('desktop_analyze_status',{},self.context)
        self.assertEqual(result['data']['processed'],4)
        self.assertIn('4/10',result['message'])

    def test_zero_auto_group_does_not_claim_new_group(self):
        with patch('desktop_collections.auto_organize',return_value={'automatic_created_count':0}):
            result=self.registry.execute('desktop_auto_organize',{},self.context)
        self.assertIn('새로 묶을',result['message'])

if __name__ == '__main__': unittest.main()
