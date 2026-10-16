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
        patch('quick_launcher.windows_app_catalog.catalog', return_value={
            'available': False, 'items': [], 'warnings': [], 'total': 0}).start()
        patch('quick_launcher._suggestion_roots', return_value=[]).start()
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
        for action in ('save','remove','reorder','launch','pick-program','bookmarks','refresh','add-page','appearance','move-page'):
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

    def test_move_page_http_round_trip_and_authentication(self):
        self.write('add-page')
        initial = self.write('save', title='옮길 항목', kind='web', target='https://example.com', page=2)['items'][0]
        payload = {'token': app.DESK_TOKEN, 'request_id': uuid.uuid4().hex, 'id': initial['id'], 'page': 1}
        for headers in ({'Origin': 'https://outside.example'}, {'Host': 'outside.example'}):
            with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/launcher/move-page', payload, headers)
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.request()['items'][0]['page'], 2)
        moved = self.request('/api/launcher/move-page', payload)
        self.assertEqual(moved['result']['page'], 1)
        self.assertEqual(moved['result']['item_id'], initial['id'])
        self.assertEqual(moved, self.request('/api/launcher/move-page', payload))
        self.assertEqual(moved['items'][0]['target'], initial['target'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.write('move-page', id=initial['id'], page=True)
        self.assertEqual(error.exception.code, 400)

    def test_web_launch_and_unsafe_schemes(self):
        with patch.object(app,'open_managed_web',return_value={'tab_id':'fixture-tab','title':'업무 웹','url':'https://example.com/work'}) as opened:
            data=self.write('save',title='업무 웹',kind='web',target='https://example.com/work',icon='🌐')
            launched=self.write('launch',id=data['items'][0]['id'])
            opened.assert_called_once_with('https://example.com/work')
            self.assertEqual(launched['result']['browser']['tab_id'],'fixture-tab')
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
        page = self.write('add-page')['result']['page']
        self.assertEqual(page, 2)
        self.write('appearance', id=item_id, title='우클릭 수정', icon_data=icon)
        changed = self.request()['items'][0]
        self.assertEqual((changed['title'], changed['target'], changed['page']), ('우클릭 수정', 'https://example.com', 1))
        for action in ('add-page', 'appearance'):
            for headers in ({'Origin': 'https://outside.example'}, {'Host': 'outside.example'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/api/launcher/'+action, {'token': app.DESK_TOKEN}, headers)
                self.assertEqual(error.exception.code, 403)

    def test_program_picker_is_explicit_and_authenticated(self):
        from types import SimpleNamespace
        with patch.object(app.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=str(self.program),stderr='')) as run:
            self.assertEqual(self.request('/api/launcher/pick-program',{'token':app.DESK_TOKEN})['path'],str(self.program))
            run.assert_called_once()

    def test_animated_gif_http_round_trip_keeps_animation_and_mime(self):
        from PIL import Image
        frames = [Image.new('RGBA', (16, 16), color) for color in ('red', 'blue')]
        output = io.BytesIO()
        frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:],
                       duration=[80, 150], loop=2)
        raw = output.getvalue()
        icon = 'data:image/gif;base64,' + base64.b64encode(raw).decode()
        data = self.write('save', title='GIF 아이콘', kind='web', target='https://example.com', icon_data=icon)
        self.assertEqual(data['page_size'], 15)
        item_id = data['items'][0]['id']
        self.write('appearance', id=item_id, title='이름 수정')
        with urllib.request.urlopen(self.base + '/api/launcher/icon?id=' + item_id, timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), 'image/gif')
            returned = response.read()
        self.assertEqual(returned, raw)
        with Image.open(io.BytesIO(returned)) as image:
            self.assertEqual((image.n_frames, image.info['loop']), (2, 2))
            self.assertEqual(image.info['duration'], 80)
            image.seek(1)
            self.assertEqual(image.info['duration'], 150)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.write('appearance', id=item_id, title='잘못된 GIF',
                       icon_data='data:image/gif;base64,' + base64.b64encode(raw[:-1]).decode())
        self.assertEqual(error.exception.code, 400)

    def test_icon_revision_changes_without_exposing_image_bytes(self):
        from PIL import Image
        def png(color):
            stream = io.BytesIO()
            Image.new('RGB', (8, 8), color).save(stream, format='PNG')
            return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()
        initial = self.write('save', title='아이콘', kind='web', target='https://example.com', icon_data=png('red'))['items'][0]
        self.assertRegex(initial['icon_revision'], r'^[a-f0-9]{16}$')
        self.assertNotIn('image', initial)
        renamed = self.write('appearance', id=initial['id'], title='새 이름')['items'][0]
        self.assertEqual(renamed['icon_revision'], initial['icon_revision'])
        changed = self.write('appearance', id=initial['id'], title='새 이미지', icon_data=png('blue'))['items'][0]
        self.assertNotEqual(changed['icon_revision'], initial['icon_revision'])
        self.assertEqual(self.request()['items'][0]['icon_revision'], changed['icon_revision'])
        cleared = self.write('appearance', id=initial['id'], title='이미지 제거', icon_data='')['items'][0]
        self.assertIsNone(cleared['icon_revision'])
        self.assertFalse(cleared['icon_image'])

    def test_bookmarks_are_read_only_and_require_explicit_local_request(self):
        expected = {'items': [{'title': '업무 사이트', 'kind': 'web',
                              'target': 'https://example.com/work', 'browser': 'Chrome',
                              'profile': 'Default', 'folder': '북마크바'}],
                    'warnings': [], 'total': 1, 'truncated': False}
        with patch.object(app.browser_bookmarks, 'catalog', return_value=expected) as read:
            self.assertNotIn('bookmarks', self.request())
            read.assert_not_called()
            for headers in ({'Origin': 'https://outside.example'}, {'Host': 'outside.example'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/api/launcher/bookmarks', {'token': app.DESK_TOKEN}, headers)
                self.assertEqual(error.exception.code, 403)
            read.assert_not_called()
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/launcher/bookmarks', {'token': app.DESK_TOKEN, 'path': 'C:/private'})
            self.assertEqual(error.exception.code, 400)
            read.assert_not_called()
            self.assertEqual(self.request('/api/launcher/bookmarks', {'token': app.DESK_TOKEN}), expected)
            read.assert_called_once_with()

    def test_catalog_refresh_is_explicit_and_authenticated(self):
        with patch.object(app.quick_launcher.windows_app_catalog, 'catalog', return_value={
                'available': True, 'items': [], 'warnings': [], 'total': 0}) as catalog:
            self.request('/api/launcher/refresh', {'token': app.DESK_TOKEN})
            self.assertIn(unittest.mock.call(refresh=True), catalog.call_args_list)
            self.assertEqual(self.request()['suggestion_source'], 'windows-start-menu')


if __name__=='__main__': unittest.main()
