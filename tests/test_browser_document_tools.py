import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from assistant_tools import ToolRegistry


class Browser:
    def __init__(self): self.opened=[]
    def open(self,url):
        self.opened.append(url)
        return {'tab_id':'private-tab-id','title':'동적 페이지','url':'https://example.com/page'}
    def tabs(self): return [{'tab_id':'private-tab-id','title':'동적 페이지','url':'https://example.com/page'}]
    def read(self,tab,query='',offset=0):
        return {'tab_id':tab,'title':'동적 페이지','url':'https://example.com/page','text':'현재 납기는 금요일입니다.',
                'query':query,'match_count':1,'snapshot_id':'private-snapshot',
                'links':[{'id':'private-link','text':'상세 내용','url':'https://example.com/detail'}]}
    def follow(self,tab,link,snapshot):
        assert (link,snapshot)==('private-link','private-snapshot')
        return self.read(tab)
    def scroll(self,tab,direction): return self.read(tab)
    def view(self,tab): return {'tab_id':tab,'title':'동적 페이지','url':'https://example.com/page',
        'image':'private-screenshot','captured_at':'now','masked_inputs':True}


class BrowserDocumentToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.browser=Browser(); self.vision=Mock(return_value='배너는 초록색입니다. api_key="do-not-echo"')
        self.registry=ToolRegistry(pathlib.Path(self.temp.name)/'office.db',browser_service=self.browser,vision_model=self.vision)
        self.context={'request_id':'1'*32,'step_id':'2'*32,'refs':{}}

    def test_browser_observed_refs_and_read_evidence(self):
        opened=self.registry.execute('browser_open',{'url':'https://example.com'},self.context)
        self.assertNotIn('private-tab-id',str(opened))
        ref=opened['data']['tab']['ref']
        read=self.registry.execute('browser_read',{'ref':ref,'query':'납기'},self.context)
        self.assertTrue(read['evidence']); self.assertIn('금요일',read['data']['text'])
        self.assertNotIn('private-link',str(read)); self.assertNotIn('private-snapshot',str(read))
        self.context['step_id']='3'*32
        followed=self.registry.execute('browser_follow',{'ref':read['data']['links'][0]['ref']},self.context)
        self.assertTrue(followed['evidence'])

    def test_screen_image_is_private_and_requires_real_vision(self):
        ref=self.registry.execute('browser_tabs',{},self.context)['data']['items'][0]['ref']
        result=self.registry.execute('browser_view',{'ref':ref,'question':'배너 색은?'},self.context)
        self.vision.assert_called_once()
        self.assertEqual(self.vision.call_args.args[1],'private-screenshot')
        self.assertNotIn('private-screenshot',str(result)); self.assertNotIn('do-not-echo',str(result))
        self.assertIn('초록색',result['data']['visual_observation'])
        self.registry.vision_model=None
        with self.assertRaises(ValueError): self.registry.execute('browser_view',{'ref':ref},self.context)

    def test_registered_web_tile_launch_stays_in_managed_browser(self):
        saved=self.registry.launcher.perform('save',{'request_id':'3'*32,'title':'웹','kind':'web','target':'https://example.com','icon':'🌐'})
        ref=self.registry.execute('launcher_list',{},self.context)['data']['items'][0]['ref']
        self.context['step_id']='4'*32
        result=self.registry.execute('launcher_launch',{'ref':ref},self.context)
        self.assertEqual(self.browser.opened,['https://example.com'])
        self.assertIn('tab',result['data'])

    def test_document_paths_are_private_and_ref_is_revalidated(self):
        source={'items':[{'id':'doc-id','name':'계약서.txt','title':'계약서','path':str(pathlib.Path(self.temp.name)/'계약서.txt'),
                          'extension':'.txt','source':'업무 자료'}],'truncated':False,'warnings':[]}
        with patch('document_reader.catalog',return_value=source), patch('document_reader.read',return_value={
            'name':'계약서.txt','text':'납기는 금요일','match_count':1,'note':'읽은 범위','search_complete':True}) as reader:
            found=self.registry.execute('documents_find',{'query':'계약서'},self.context)
            self.assertNotIn(self.temp.name,str(found))
            ref=found['data']['items'][0]['ref']
            result=self.registry.execute('documents_read',{'ref':ref,'query':'납기'},self.context)
            self.assertTrue(result['evidence']); self.assertIn('금요일',result['data']['text'])
            reader.assert_called_once_with(source['items'][0]['path'],'납기',0)
            source['items']=[]
            with self.assertRaises(ValueError): self.registry.execute('documents_read',{'ref':ref},self.context)

    def test_unknown_refs_paths_scripts_and_invalid_offsets_rejected(self):
        ref=self.registry.execute('browser_tabs',{},self.context)['data']['items'][0]['ref']
        for name,args in [('browser_read',{'ref':'fake'}),('documents_read',{'ref':'fake','path':'C:\\secret.txt'}),
                          ('browser_read',{'ref':ref,'offset':True}),('browser_read',{'ref':ref,'offset':60001}),
                          ('browser_scroll',{'ref':ref,'direction':'run-script'}),('browser_open',{'url':'https://example.com','script':'anything'})]:
            with self.subTest(name=name,args=args),self.assertRaises(ValueError): self.registry.execute(name,args,self.context)

    def test_vision_failure_does_not_echo_provider_data(self):
        ref=self.registry.execute('browser_tabs',{},self.context)['data']['items'][0]['ref']
        self.vision.side_effect=RuntimeError('secret-api-key')
        with self.assertRaises(ValueError) as error:self.registry.execute('browser_view',{'ref':ref},self.context)
        self.assertNotIn('secret-api-key',str(error.exception))


if __name__=='__main__': unittest.main()
