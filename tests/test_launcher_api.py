import base64
import io
import json
import os
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

import app


class LauncherApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(app, 'DB', self.root/'office.db').start()
        self.program = self.root/'시험 프로그램.exe'; self.program.write_bytes(b'MZ-fixture-only')
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, route='/api/launcher', data=None, headers=None):
        req=urllib.request.Request(self.base+route,data=json.dumps(data).encode() if data is not None else None,
                                  headers={'Content-Type':'application/json',**(headers or {})})
        with urllib.request.urlopen(req,timeout=8) as response: return json.load(response)

    def write(self, action, **payload):
        return self.request('/api/launcher/'+action,{'token':app.DESK_TOKEN,'request_id':uuid.uuid4().hex,**payload})

    def test_auth_and_foreign_host_are_rejected(self):
        self.assertEqual(self.request()['action_token'],app.DESK_TOKEN)
        for action in ('save','remove','reorder','launch','pick-program'):
            with self.subTest(action=action), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/launcher/'+action,{})
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(headers={'Host':'outside.example'})
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/launcher/save',{'token':app.DESK_TOKEN},headers={'Origin':'https://outside.example'})
        self.assertEqual(error.exception.code,403)

    def test_program_launch_requires_registered_id_and_explicit_action(self):
        with patch.object(app.os,'startfile',create=True) as opened:
            data=self.write('save',title='시험 프로그램',kind='app',target=str(self.program),icon='🧰')
            opened.assert_not_called()
            item=data['items'][0]
            self.write('launch',id=item['id'])
            opened.assert_called_once_with(str(self.program))
            with self.assertRaises(urllib.error.HTTPError): self.write('launch',id='f'*32)
            self.assertEqual(opened.call_count,1)

    def test_edit_remove_preserves_target_file(self):
        data=self.write('save',title='원래 이름',kind='app',target=str(self.program),icon='🧰')
        item_id=data['items'][0]['id']
        self.write('save',id=item_id,title='바꾼 이름',kind='app',target=str(self.program),icon='🎨')
        self.assertEqual(self.request()['items'][0]['title'],'바꾼 이름')
        self.write('remove',id=item_id)
        self.assertEqual(self.request()['items'],[])
        self.assertEqual(self.program.read_bytes(),b'MZ-fixture-only')

    def test_web_launch_and_unsafe_schemes(self):
        with patch('webbrowser.open',return_value=True) as opened:
            data=self.write('save',title='업무 웹',kind='web',target='https://example.com/work',icon='🌐')
            self.write('launch',id=data['items'][0]['id'])
            opened.assert_called_once_with('https://example.com/work')
        for target in ['javascript:alert(1)','file:///C:/Windows','https://name:secret@example.com']:
            with self.subTest(target=target),self.assertRaises(urllib.error.HTTPError):
                self.write('save',title='차단',kind='web',target=target)

    def test_large_valid_icon_round_trip(self):
        try: from PIL import Image
        except ImportError: self.skipTest('Pillow unavailable')
        output=io.BytesIO(); Image.frombytes('RGB',(300,300),os.urandom(300*300*3)).save(output,format='PNG')
        icon='data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
        self.assertGreater(len(icon),200000)
        data=self.write('save',title='아이콘 시험',kind='web',target='https://example.com',icon_data=icon,icon='🌐')
        item_id=data['items'][0]['id']
        with urllib.request.urlopen(self.base+'/api/launcher/icon?id='+item_id,timeout=5) as result:
            self.assertEqual(result.headers.get_content_type(),'image/png')
            self.assertTrue(result.read().startswith(b'\x89PNG'))

    def test_program_picker_is_explicit_and_authenticated(self):
        from types import SimpleNamespace
        with patch.object(app.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=str(self.program),stderr='')) as run:
            self.assertEqual(self.request('/api/launcher/pick-program',{'token':app.DESK_TOKEN})['path'],str(self.program))
            run.assert_called_once()


if __name__=='__main__': unittest.main()
