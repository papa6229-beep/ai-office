import copy
import unittest

from windows_actions import WindowsActions, SPECS


class Registry:
    def _ref(self, context, kind, value):
        key=kind+'_'+str(len(context.setdefault('refs',{}))+1)
        context['refs'][key]={'kind':kind,'value':copy.deepcopy(value)}
        return key
    def _get(self, context, ref, kind):
        row=context.get('refs',{}).get(ref,{})
        if row.get('kind')!=kind: raise ValueError('invalid ref')
        return copy.deepcopy(row['value'])
    def _once(self, name, args, context, operation): return operation()


class Service:
    def __init__(self): self.calls=[]; self.fail=False
    def windows(self,query=''):
        return [{'window_id':'PRIVATE-WINDOW','title':'Example app','process':'example.exe'}]
    def inspect(self,window_id):
        return {'window_id':window_id,'title':'Example app','process':'example.exe','snapshot_id':'PRIVATE-SNAPSHOT',
                'controls':[{'id':'PRIVATE-CONTROL','name':'Save','control_type':'Button','enabled':True,'patterns':['Invoke']},
                            {'id':'TAB','name':'Details','control_type':'TabItem','enabled':True,'patterns':['SelectionItem']},
                            {'id':'INPUT','name':'Description','control_type':'Edit','enabled':True,'patterns':['Value'],'value':'old'}],
                'text':'Visible app text api_key="PRIVATE-SECRET"','captured_at':'now','truncated':False}
    def act(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        if self.fail: raise RuntimeError('PRIVATE-PROVIDER-ERROR')
        return {'action_dispatched':True,'observation':self.inspect(args[0]),'verification':{'goal_verified':False}}


class WindowsActionsTests(unittest.TestCase):
    def setUp(self):
        self.service=Service(); self.adapter=WindowsActions(Registry(),self.service)
        self.context={'refs':{},'user_message_id':'origin'}
    def controls(self):
        window=self.adapter.execute('windows_list',{},self.context)['data']['items'][0]['ref']
        return self.adapter.execute('windows_inspect',{'ref':window},self.context)['data']['controls']
    def test_specs_and_short_observed_refs(self):
        self.assertEqual(set(SPECS),{'windows_list','windows_inspect','windows_action'})
        controls=self.controls()
        self.assertIn('invoke',controls[0]['operations'])
        self.assertNotIn('PRIVATE-',str(controls))
        with self.assertRaises(ValueError): self.adapter.execute('windows_inspect',{'ref':'forged'},self.context)
    def test_invoke_requires_exact_approval_then_fresh_evidence(self):
        ref=self.controls()[0]['ref']
        pending=self.adapter.execute('windows_action',{'ref':ref,'operation':'invoke'},self.context)['pending']
        self.assertEqual(self.service.calls,[])
        self.assertEqual(pending['origin_user_message_id'],'origin')
        self.assertEqual(pending['args']['call'],{'ref':ref,'operation':'invoke'})
        result=self.adapter.resolve(pending,'approve',self.context)
        self.assertTrue(result['evidence']); self.assertTrue(result['continue_assistant'])
        self.assertFalse(result['data']['verification']['goal_verified'])
        self.assertNotIn('PRIVATE-',str(result))
        self.assertEqual(len(self.service.calls),1)
    def test_only_tabitem_selection_runs_directly(self):
        controls=self.controls()
        result=self.adapter.execute('windows_action',{'ref':controls[1]['ref'],'operation':'select'},self.context)
        self.assertEqual(result['status'],'ok'); self.assertEqual(len(self.service.calls),1)
    def test_cancel_and_provider_failure_do_not_resume(self):
        pending=self.adapter.execute('windows_action',{'ref':self.controls()[0]['ref'],'operation':'invoke'},self.context)['pending']
        result=self.adapter.resolve(pending,'cancel',self.context)
        self.assertNotIn('continue_assistant',result); self.assertEqual(self.service.calls,[])
        self.service.fail=True
        with self.assertRaisesRegex(ValueError,'^(?!.*PRIVATE).*$'): self.adapter.resolve(pending,'approve',self.context)
        self.assertEqual(len(self.service.calls),1)
    def test_validation_secret_values_and_unsupported_operations(self):
        controls=self.controls()
        for args in ({'ref':controls[0]['ref'],'operation':'invoke','approved':True},
                     {'ref':controls[0]['ref'],'operation':'set_value','value':'hello'},
                     {'ref':controls[2]['ref'],'operation':'set_value','value':'api_key="private-value"'},
                     {'ref':controls[2]['ref'],'operation':'invoke'},
                     {'ref':controls[0]['ref'],'operation':'invoke','value':'extra'}):
            with self.assertRaises(ValueError): self.adapter.execute('windows_action',args,self.context)
        self.assertEqual(self.service.calls,[])
    def test_value_approval_displays_current_and_proposed_state(self):
        ref=self.controls()[2]['ref']
        pending=self.adapter.execute('windows_action',{'ref':ref,'operation':'set_value','value':'new'},self.context)['pending']
        self.assertIn('old',pending['description']); self.assertIn('new',pending['description'])
        self.assertIn('Example app',pending['description']); self.assertIn('Description',pending['description'])
    def test_missing_fresh_observation_never_resumes(self):
        self.service.act=lambda *a,**k:{'action_dispatched':True,'verification':{'goal_verified':False}}
        ref=self.controls()[1]['ref']
        result=self.adapter.execute('windows_action',{'ref':ref,'operation':'select'},self.context)
        self.assertNotIn('continue_assistant',result); self.assertFalse(result.get('evidence',False))
    def test_native_operation_names_scroll_mapping_and_non_tab_select(self):
        original=self.service.inspect
        def inspect(window):
            obs=original(window)
            obs['controls'][0].update(control_type='ListItem',patterns=['select','focus','scroll'])
            return obs
        self.service.inspect=inspect
        control=self.controls()[0]
        pending=self.adapter.execute('windows_action',{'ref':control['ref'],'operation':'select'},self.context)
        self.assertEqual(pending['status'],'pending'); self.assertEqual(self.service.calls,[])
        self.adapter.execute('windows_action',{'ref':control['ref'],'operation':'scroll_down'},self.context)
        self.assertEqual(self.service.calls[0][0][-1],'scroll')
        self.assertEqual(self.service.calls[0][1],{'value':'down'})
    def test_secret_control_state_is_not_exposed(self):
        original=self.service.inspect
        def inspect(window):
            obs=original(window)
            obs['controls'][2].update(password=True,value='HIDDEN-PASSWORD',name='Secret input')
            return obs
        self.service.inspect=inspect
        controls=self.controls()
        self.assertNotIn('HIDDEN-PASSWORD',str(controls))
        self.assertNotIn('ref',controls[2])
    def test_approval_cannot_change_frozen_operation(self):
        pending=self.adapter.execute('windows_action',{'ref':self.controls()[0]['ref'],'operation':'invoke'},self.context)['pending']
        pending['args']['operation']='toggle'
        with self.assertRaises(ValueError): self.adapter.resolve(pending,'approve',self.context)
        self.assertEqual(self.service.calls,[])


if __name__=='__main__': unittest.main()
