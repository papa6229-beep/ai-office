import pathlib
import tempfile
import unittest
import uuid
from unittest.mock import Mock,patch

import app
from assistant_tools import ToolRegistry
from work_assistant import WorkAssistant


class SettingsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=pathlib.Path(self.temp.name)/'office.db'
        self.registry=ToolRegistry(self.db)
        self.context={'request_id':'1'*32,'step_id':'2'*32,'refs':{}}
        self.status={'available':True,'sections':['default_apps'],'registered_apps':[
            {'id':'PRIVATE_APP_ID','name':'Google Chrome','scope':'machine'}],
            'web_associations':{'http':{'prog_id':'ChromeHTML','source':'current_user_userchoice'},
                                'https':{'prog_id':'ChromeHTML','source':'current_user_userchoice'}},
            'effective_default_verified':False}

    def test_status_issues_app_ref_without_claiming_setting_changed(self):
        with patch('system_settings.status',return_value=self.status):
            result=self.registry.execute('settings_status',{'query':'Chrome'},self.context)
        self.assertIn('ChromeHTML',result['message'])
        self.assertNotIn('PRIVATE_APP_ID',str(result))
        self.assertFalse(result['data']['effective_default_verified'])
        self.assertTrue(result['answer_complete'])

    def test_open_uses_server_app_ref_and_replays_once(self):
        with patch('system_settings.status',return_value=self.status):
            result=self.registry.execute('settings_status',{},self.context)
        ref=result['data']['apps'][0]['ref']
        args={'section':'default_apps','app_ref':ref}
        with patch('system_settings.open_section',return_value={'message':'설정 열기를 요청했습니다.',
                   'dispatched':True,'setting_changed':False,'requires_ui_verification':True}) as opener:
            opened=self.registry.execute('settings_open',args,self.context)
            self.assertEqual(self.registry.execute('settings_open',args,self.context),opened)
        opener.assert_called_once_with('default_apps','PRIVATE_APP_ID')
        self.assertFalse(opened['data']['setting_changed'])

    def test_forged_ref_and_arbitrary_uri_cannot_open(self):
        with patch('system_settings.open_section') as opener:
            for args in ({'section':'default_apps','app_ref':'fake'},
                         {'section':'ms-settings:anything'},
                         {'section':'default_apps','uri':'file:///private'}):
                with self.assertRaises(ValueError):self.registry.execute('settings_open',args,self.context)
        opener.assert_not_called()

    def test_false_permission_answer_recovers_to_real_registered_observation(self):
        outputs=[{'content':'권한이 없습니다. 직접 변경하세요.','tool_calls':[]},
                 {'content':'권한이 없어 변경할 수 없습니다.','tool_calls':[]}]
        intent=Mock(return_value={'decision':'execute','observation':{'name':'settings_status','arguments':{'query':'Chrome'}},'message':''})
        service=WorkAssistant(self.db,lambda:{},None,native_model=lambda *args:outputs.pop(0),
                              registry_factory=lambda:self.registry,intent_model=intent)
        with patch('system_settings.status',return_value=self.status) as status:
            result=service.perform('command',{'request_id':uuid.uuid4().hex,'message':'기본 브라우저를 크롬으로 변경해줘'})
        status.assert_called_once();intent.assert_called_once()
        self.assertIn('ChromeHTML',result['result']['message'])
        self.assertNotIn('권한이 없',result['result']['message'])

    def test_production_factory_wires_semantic_assessor(self):
        with patch.object(app,'DB',self.db),patch.object(app,'config',return_value={}),\
             patch.object(app,'call_assistant_model',return_value={'content':'불가능합니다.','tool_calls':[]}),\
             patch.object(app,'call_assistant_intent',return_value={'decision':'answer','observation':None,'message':'안녕하세요.'}) as intent:
            result=app.work_service().perform('command',{'request_id':uuid.uuid4().hex,'message':'안녕하세요'})
        intent.assert_called_once();self.assertEqual(result['result']['answer'],'안녕하세요.')


if __name__=='__main__':unittest.main()
