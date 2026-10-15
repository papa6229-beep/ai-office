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

    def test_issued_handles_are_short_stable_and_distinguish_same_name_apps(self):
        value={'title':'Editor','target':'startapp:'+'a'*64}
        ref=self.registry._ref(self.context,'app',value)
        self.assertEqual(ref,'app_1')
        self.assertEqual(self.registry._ref(self.context,'app',dict(reversed(list(value.items())))),ref)
        other=self.registry._ref(self.context,'app',dict(value,target='startapp:'+'b'*64))
        self.assertEqual(other,'app_2')
        self.assertEqual(self.registry._get(self.context,ref,'app'),value)
        value['title']='Changed caller data'
        self.assertEqual(self.registry._get(self.context,ref,'app')['title'],'Editor')

    def test_handles_require_current_context_and_exact_issued_kind(self):
        ref=self.registry._ref(self.context,'app',{'title':'Editor','target':'startapp:'+'a'*64})
        for context,handle,kind in [({'refs':{}},ref,'app'),(self.context,'app_99','app'),(self.context,ref,'bookmark')]:
            with self.assertRaises(ValueError): self.registry._get(context,handle,kind)

    def test_persisted_legacy_handles_remain_valid_without_overwriting(self):
        import json
        legacy='a'*32
        value={'title':'Legacy app','target':'startapp:'+'b'*64}
        self.context['refs']={legacy:{'kind':'app','value':value},'app_3':{'kind':'app','value':{'title':'Existing'}}}
        restored=json.loads(json.dumps(self.context))
        self.assertEqual(self.registry._get(restored,legacy,'app'),value)
        self.assertEqual(self.registry._ref(restored,'app',value),legacy)
        new=self.registry._ref(restored,'app',{'title':'New app'})
        self.assertEqual(new,'app_4')
        self.assertEqual(restored['refs']['app_3']['value'],{'title':'Existing'})

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

    def test_chatgpt_request_uses_registered_name_despite_package_namespace(self):
        windows_app_catalog.clear_cache()
        self.addCleanup(windows_app_catalog.clear_cache)
        with patch.object(windows_app_catalog, '_read_apps', return_value=[{'Name':'ChatGPT','AppID':'OpenAI.Codex_123!App'}]):
            item=windows_app_catalog.catalog()['items'][0]
            self.assertEqual(item['title'], 'ChatGPT')
            result=self.registry.execute('apps_find', {'query':'ChatGPT'}, self.context)
            self.assertTrue(result['data']['lexical_match'])
            self.assertEqual(result['data']['items'][0]['title'], 'ChatGPT')
            korean=self.registry.execute('apps_find', {'query':'챗지피티'}, self.context)
            self.assertFalse(korean['data']['lexical_match'])
            self.assertEqual(korean['data']['items'][0]['title'], 'ChatGPT')
            ref=korean['data']['items'][0]['ref']
            self.registry.execute('launcher_add', {'ref':ref}, self.context)
            saved=self.registry.launcher.overview()['items'][0]
            self.assertEqual(saved['title'], 'ChatGPT')
            self.assertEqual(saved['target'], item['target'])

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
        with patch('windows_app_catalog.launch') as launch, patch('windows_app_catalog.resolve',return_value='App'):
            self.registry.resolve_pending(pending,ref,context)
            launch.assert_called_once()

    def _choice_fixture(self, purpose='add'):
        self.app_rows=[{'Name':'Adobe Illustrator 2026','AppID':'Adobe.Illustrator'},
                       {'Name':'AI Studio','AppID':'Other.AIStudio'}]
        windows_app_catalog.clear_cache()
        self.addCleanup(windows_app_catalog.clear_cache)
        reader=patch.object(windows_app_catalog,'_read_apps',side_effect=lambda:self.app_rows)
        reader.start()
        self.addCleanup(reader.stop)
        refs=[r['ref'] for r in self.registry.execute('apps_list',{},self.context)['data']['items']]
        choice=self.registry.execute('apps_choose',{'refs':refs,'purpose':purpose,'question':'a.i는 어떤 앱인가요?'},self.context)
        import json
        return refs,json.loads(json.dumps(choice['pending'])),choice

    def test_app_choice_all_is_durable_deduplicated_and_reports_real_results(self):
        refs,pending,choice=self._choice_fixture()
        self.assertIn('Adobe Illustrator 2026',choice['message'])
        self.assertIn('AI Studio',choice['message'])
        self.assertEqual([o['id'] for o in pending['options']],refs+['all','cancel'])
        first=self.registry.resolve_pending(pending,'all',self.context)
        self.assertEqual(first['data']['counts'],{'added':2,'existing':0,'error':0})
        fresh=ToolRegistry(self.registry.db_path)
        self.assertEqual(fresh.resolve_pending(pending,'all',self.context),first)
        self.assertEqual(len(fresh.launcher.overview()['items']),2)
        self.context['step_id']='3'*32
        again=fresh.resolve_pending(pending,'all',self.context)
        self.assertEqual(again['data']['counts'],{'added':0,'existing':2,'error':0})

    def test_app_choice_single_cancel_and_launch_never_all(self):
        refs,pending,_=self._choice_fixture('launch')
        self.assertNotIn('all',[o['id'] for o in pending['options']])
        with patch('windows_app_catalog.launch') as launch:
            self.registry.resolve_pending(pending,'cancel',self.context)
            launch.assert_not_called()
            with self.assertRaises(ValueError): self.registry.resolve_pending(pending,'all',self.context)
            self.registry.resolve_pending(pending,refs[0],self.context)
            launch.assert_called_once()
        self.assertEqual(self.registry.launcher.overview()['items'],[])

    def test_app_choice_stale_and_forged_refs_do_not_execute(self):
        refs,pending,_=self._choice_fixture()
        with self.assertRaises(ValueError): self.registry.resolve_pending(pending,'forged',self.context)
        self.app_rows.pop()
        result=self.registry.resolve_pending(pending,'all',self.context)
        self.assertEqual(result['data']['counts'],{'added':1,'existing':0,'error':1})
        self.assertEqual(len(self.registry.launcher.overview()['items']),1)
        self.context['step_id']='3'*32
        with self.assertRaises(ValueError): self.registry.resolve_pending(pending,refs[1],self.context)
        self.assertEqual(len(self.registry.launcher.overview()['items']),1)

    def test_app_choice_options_limit_includes_server_actions(self):
        schema=next(t['function']['parameters'] for t in self.registry.definitions() if t['function']['name']=='apps_choose')
        self.assertEqual(schema['properties']['refs']['maxItems'],98)
        with self.assertRaises(ValueError): self.registry.execute('apps_choose',{'refs':['x'+str(i) for i in range(99)],'purpose':'add'},self.context)

    def test_app_choice_single_add_and_forged_pending_reference(self):
        refs,pending,_=self._choice_fixture()
        import copy,json
        forged=copy.deepcopy(pending)
        forged['args']['refs'].append('forged')
        with self.assertRaises(ValueError): self.registry.resolve_pending(forged,'all',self.context)
        self.assertEqual(self.registry.launcher.overview()['items'],[])
        context=json.loads(json.dumps(self.context))
        self.registry.resolve_pending(pending,refs[0],context)
        self.assertEqual([i['title'] for i in self.registry.launcher.overview()['items']],['Adobe Illustrator 2026'])

    def test_app_choice_all_deduplicates_same_target_with_different_refs(self):
        refs,_,_=self._choice_fixture()
        same=self.registry._get(self.context,refs[0],'app')
        same['title']='Alternate display name'
        duplicate=self.registry._ref(self.context,'app',same)
        pending=self.registry.execute('apps_choose',{'refs':[refs[0],duplicate],'purpose':'add'},self.context)['pending']
        result=self.registry.resolve_pending(pending,'all',self.context)
        self.assertEqual(result['data']['counts'],{'added':1,'existing':0,'error':0})
        self.assertEqual(len(self.registry.launcher.overview()['items']),1)

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
