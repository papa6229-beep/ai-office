import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app
import desktop_judgment
import desktop_preferences
import desktop_recovery
import desktop_workspace


class JudgmentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name);self.desktop=self.root/'Desktop';self.desktop.mkdir()
        self.file=self.desktop/'empty.txt';self.file.write_bytes(b' \n')
        self.addCleanup(patch.stopall)
        patch.object(app,'_AI_READY',False).start()
        patch.object(app,'start_ai_supervisor').start()
        patch.object(app,'config',return_value={'gemma_url':'http://localhost:11434','gemma_model':'gemma4:26b','api_type':'ollama','api_key':''}).start()
        self.model=patch.object(app,'call_gemma',return_value='{"groups":[]}').start()
        patch.object(app,'call_gemma_vision',return_value='{}').start()
        patch.object(desktop_workspace,'_desktop_roots',return_value=[self.desktop]).start()
        patch.object(desktop_judgment,'STATE',self.root/'ai.json').start()
        patch.object(desktop_recovery,'RUNTIME',self.root/'recovery').start()
        patch.object(desktop_preferences,'STATE',self.root/'preferences.json').start()
        desktop_judgment.stop();desktop_judgment._reset_runtime()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.stop)

    def stop(self):
        desktop_judgment.stop()
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def request(self,route='judgment',payload=None,headers=None):
        request=urllib.request.Request(self.base+'/api/desk/'+route,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
        with urllib.request.urlopen(request,timeout=5) as result:return json.load(result)

    def test_status_does_not_start_analysis_or_move_files(self):
        result=self.request();self.assertFalse(result['running'])
        self.assertEqual(result['candidates'],[]);self.model.assert_not_called()
        self.assertEqual(self.file.read_bytes(),b' \n')

    def test_all_actions_require_token_and_same_origin(self):
        for action in ('start','stop','retain','hold','restore'):
            for payload,headers in (({},{}),({'token':app.DESK_TOKEN},{'Origin':'https://outside.example'})):
                with self.subTest(action=action),self.assertRaises(urllib.error.HTTPError) as error:self.request('judgment-'+action,payload,headers)
                self.assertEqual(error.exception.code,403)
        self.assertEqual(self.file.read_bytes(),b' \n')

    def test_local_empty_candidates_appear_without_model_and_can_be_retained(self):
        self.request('judgment-start',{'token':app.DESK_TOKEN})
        until=time.monotonic()+3
        while desktop_judgment.status()['running'] and time.monotonic()<until:time.sleep(.01)
        result=self.request();self.assertEqual(result['empty_count'],1)
        candidate=result['candidates'][0];self.assertTrue(candidate['hold_allowed'])
        self.model.assert_not_called()
        result=self.request('judgment-retain',{'token':app.DESK_TOKEN,'item_ids':[candidate['id']]})
        self.assertEqual(result['candidates'],[]);self.assertEqual(self.file.read_bytes(),b' \n')

    def test_arbitrary_path_keys_and_foreign_host_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('judgment-hold',{'token':app.DESK_TOKEN,'path':str(self.file)})
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as error:self.request(headers={'Host':'outside.example'})
        self.assertEqual(error.exception.code,403)

    def test_only_successful_explicit_open_is_usage(self):
        self.request()
        selected=desktop_workspace.inventory()['items'][0]['id']
        with patch.object(desktop_workspace.os,'startfile',create=True):
            self.request('open',{'token':app.DESK_TOKEN,'id':selected})
        data=json.loads(desktop_judgment.STATE.read_text(encoding='utf-8'))
        self.assertEqual(data['usage'][selected]['opens'],1)
