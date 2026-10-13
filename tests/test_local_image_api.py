import json
import pathlib
import tempfile
import threading
import urllib.error
import urllib.request
import unittest

import app


class LocalImageApiTests(unittest.TestCase):
    def test_visual_review_uses_only_scanned_image_and_loopback_model_stub(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow optional for test fixture')
        with tempfile.TemporaryDirectory() as td:
            image=pathlib.Path(td)/'design.png'
            Image.new('RGB',(20,20),'blue').save(image)
            old_files=app.scanner.FILES
            old_state=app.scanner.STATE.copy()
            old_config,old_model,old_log=app.config,app.call_gemma_vision,app.runlog
            app.scanner.FILES=[{'path':str(image),'name':image.name,'extension':'.png','protected':False}]
            app.scanner.STATE.update(status='done')
            app.config=lambda:{'gemma_url':'http://127.0.0.1:11434','gemma_model':'gemma4:26b','api_type':'ollama','timeout':1}
            app.call_gemma_vision=lambda prompt,data:json.dumps({'project':'Campaign','task':'Review banner','confidence':.9,'reason':'Blue banner artwork'})
            app.runlog=lambda *args,**kwargs:None
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:%s/api/analyze-workspace-images'%server.server_port
                req=urllib.request.Request(url,data=json.dumps({'paths':[str(image)]}).encode(),headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(req,timeout=3) as response:result=json.load(response)
                self.assertEqual(result['items'][0]['state'],'검토 후보')
                req=urllib.request.Request(url,data=json.dumps({'paths':[str(pathlib.Path(td)/'outside.png')]}).encode(),headers={'Content-Type':'application/json'},method='POST')
                with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req,timeout=3)
                self.assertEqual(err.exception.code,400)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)
                app.scanner.FILES=old_files;app.scanner.STATE.clear();app.scanner.STATE.update(old_state)
                app.config,app.call_gemma_vision,app.runlog=old_config,old_model,old_log


if __name__=='__main__':unittest.main()
