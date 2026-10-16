import base64
import contextlib
import io
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import Mock, patch

import app
import assistant_runtime
import assistant_tools
import file_workspace
import quick_launcher
from quick_launcher import QuickLauncher
import tests.test_launcher_api as launcher_api


def image_data(format='PNG', size=(20, 12), animate=False):
    from PIL import Image
    source = Image.new('RGB',size,'red')
    output = io.BytesIO()
    kwargs = {}
    if animate:
        kwargs = {'save_all':True,'append_images':[Image.new('RGB',size,'blue')], 'duration':[80,120], 'loop':0}
    source.save(output,format=format,**kwargs)
    raw = output.getvalue()
    return 'data:image/'+{'PNG':'png','JPEG':'jpeg','GIF':'gif','WEBP':'webp'}[format]+';base64,'+base64.b64encode(raw).decode(), raw


class LauncherCardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name)
        self.path=self.root/'office.db'
        for name,value in [('quick_launcher.windows_app_catalog.catalog',{'available':False,'items':[],'warnings':[]}), ('quick_launcher._suggestion_roots',[])]:
            p=patch(name,return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.service=QuickLauncher(self.path)

    def action(self,action,**payload):
        return self.service.perform(action,dict(request_id=uuid.uuid4().hex,**payload))

    def add(self,title='Item',**payload):
        return self.action('save',title=title,kind='web',target='https://example.com',**payload)['result']['id']

    def rows(self,table):
        with contextlib.closing(sqlite3.connect(self.path)) as c:
            return c.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()

    def test_migration_uses_page_order_without_rewriting_legacy_data(self):
        first,second=self.add('First'),self.add('Second')
        page=self.action('add-page')['result']['page']
        self.action('move-page',id=first,page=page)
        with contextlib.closing(sqlite3.connect(self.path)) as c,c:
            c.execute('DROP TABLE launcher_cards')
            c.execute('DROP TABLE launcher_board_order')
        tables=('launcher_items','launcher_item_pages','launcher_pages','launcher_requests')
        before={table:self.rows(table) for table in tables}
        state=QuickLauncher(self.path).overview()
        self.assertEqual(state['board_order'],[second,first])
        self.assertEqual({table:self.rows(table) for table in tables},before)
        self.assertTrue(all(item['card_description']=='' and not item['card_cover'] for item in state['items']))

    def test_all_items_remain_on_board_beyond_legacy_page_capacity(self):
        ids=[self.add(str(index)) for index in range(19)]
        state=self.service.overview()
        self.assertGreater(len(state['pages']),1)
        self.assertEqual(state['board_order'],ids)
        self.assertEqual(len(state['items']),19)

    def test_global_drag_order_persists_without_rewriting_original_positions_or_pages(self):
        ids=[self.add(str(index)) for index in range(4)]
        before=self.rows('launcher_items'),self.rows('launcher_item_pages')
        target=list(reversed(ids))
        self.action('board-order',ids=target)
        self.assertEqual(QuickLauncher(self.path).overview()['board_order'],target)
        self.assertEqual((self.rows('launcher_items'),self.rows('launcher_item_pages')),before)
        third=self.add('Appended')
        self.assertEqual(self.service.overview()['board_order'],target+[third])

    def test_legacy_reorder_updates_both_boards_and_rejects_partial_or_duplicate_ids(self):
        ids=[self.add(str(index)) for index in range(3)]
        self.action('reorder',ids=ids[::-1])
        state=self.service.overview()
        self.assertEqual(state['board_order'],ids[::-1])
        self.assertEqual([row['id'] for row in state['items']],ids[::-1])
        for target in (ids[:-1],[ids[0]]*3,ids+['f'*32]):
            with self.assertRaises(ValueError):self.action('board-order',ids=target)
        self.assertEqual(self.service.overview()['board_order'],ids[::-1])

    def test_cover_and_icon_are_independent_and_retained_by_existing_edits(self):
        icon,icon_raw=image_data('GIF',animate=True)
        cover,cover_raw=image_data('JPEG')
        item=self.add(icon_data=icon,card_description='Daily work',card_cover_data=cover)
        before=self.service.overview()['items'][0]
        self.action('appearance',id=item,title='Renamed',icon='★')
        self.action('save',id=item,title='Retargeted',kind='web',target='https://example.org')
        after=self.service.overview()['items'][0]
        self.assertEqual(after['card_description'],'Daily work')
        self.assertEqual(after['card_cover_revision'],before['card_cover_revision'])
        self.assertEqual(after['icon_revision'],before['icon_revision'])
        self.assertEqual(self.service.icon_bytes(item),(icon_raw,'image/gif'))
        self.assertEqual(self.service.cover_bytes(item),(cover_raw,'image/jpeg'))

    def test_cover_gif_original_frames_and_cache_revision_preserved(self):
        data,raw=image_data('GIF',animate=True)
        item=self.add(card_cover_data=data)
        first=self.service.overview()['items'][0]['card_cover_revision']
        self.assertEqual(self.service.cover_bytes(item),(raw,'image/gif'))
        self.action('card-style',id=item,description='Changed only text')
        self.assertEqual(self.service.overview()['items'][0]['card_cover_revision'],first)
        self.action('card-style',id=item,cover_data=image_data('PNG')[0])
        self.assertNotEqual(self.service.overview()['items'][0]['card_cover_revision'],first)

    def test_cover_clear_and_item_remove_cleanup_only_side_data(self):
        item=self.add(card_cover_data=image_data()[0],icon_data=image_data()[0])
        self.action('card-style',id=item,description='Retained',cover_data=None)
        row=self.service.overview()['items'][0]
        self.assertFalse(row['card_cover'])
        self.assertTrue(row['icon_image'])
        self.assertEqual(row['card_description'],'Retained')
        with self.assertRaises(ValueError):self.service.cover_bytes(item)
        self.action('remove',id=item)
        self.assertEqual(self.rows('launcher_cards'),[])
        self.assertEqual(self.rows('launcher_board_order'),[])

    def test_save_and_appearance_invalid_cover_roll_back_item_and_card_together(self):
        item=self.add(card_description='Old',card_cover_data=image_data()[0])
        before={table:self.rows(table) for table in ('launcher_items','launcher_cards','launcher_requests')}
        for action,payload in [('save',dict(id=item,title='Bad target edit',kind='web',target='https://example.org')),
                               ('appearance',dict(id=item,title='Bad name edit'))]:
            with self.assertRaises(ValueError):
                self.action(action,**payload,card_description='New text',card_cover_data='data:image/svg+xml;base64,AA==')
        self.assertEqual({table:self.rows(table) for table in before},before)
        with self.assertRaises(ValueError):self.add('Invalid new item',card_cover_data='invalid')
        self.assertEqual(len(self.service.overview()['items']),1)

    def test_metadata_is_json_safe_and_description_limits_are_enforced(self):
        item=self.add(card_cover_data=image_data()[0])
        json.dumps(self.service.overview())
        for bad in ('x'*161,'line\nbreak',3):
            with self.assertRaises(ValueError):self.action('card-style',id=item,description=bad)
        self.action('card-style',id=item,description='')
        self.assertEqual(self.service.overview()['items'][0]['card_description'],'')

    def test_cover_formats_correct_mime_and_no_svg_ico_mismatch_or_corruption(self):
        item=self.add()
        for format,mime in [('PNG','image/png'),('JPEG','image/jpeg'),('WEBP','image/webp'),('GIF','image/gif')]:
            data,raw=image_data(format)
            self.action('card-style',id=item,cover_data=data)
            self.assertEqual(self.service.cover_bytes(item),(raw,mime))
        png=image_data()[0]
        bad=[png.replace('image/png','image/jpeg'),png.replace('image/png','image/x-icon'),'',123,
             'data:image/svg+xml;base64,PHN2Zy8+']
        data,raw=image_data('GIF',animate=True)
        bad.append('data:image/gif;base64,'+base64.b64encode(raw[:-1]).decode())
        for value in bad:
            with self.assertRaises(ValueError):self.action('card-style',id=item,cover_data=value)

    def test_cover_large_dimensions_bytes_and_animation_budget_are_bounded(self):
        item=self.add()
        with self.assertRaises(ValueError):self.action('card-style',id=item,cover_data=image_data(size=(2049,1))[0])
        huge='data:image/png;base64,'+base64.b64encode(b'x'*(quick_launcher._MAX_COVER_BYTES+1)).decode()
        with self.assertRaises(ValueError):self.action('card-style',id=item,cover_data=huge)
        data=image_data('GIF',animate=True)[0]
        with patch('quick_launcher._MAX_GIF_FRAMES',1):
            with self.assertRaises(ValueError):self.action('card-style',id=item,cover_data=data)
        with patch('quick_launcher._MAX_GIF_PIXELS',1):
            with self.assertRaises(ValueError):self.action('card-style',id=item,cover_data=data)

    def test_board_order_and_style_idempotency(self):
        first,second=self.add('First'),self.add('Second')
        payload={'request_id':uuid.uuid4().hex,'ids':[second,first]}
        before=self.service.perform('board-order',payload)
        self.assertEqual(self.service.perform('board-order',payload),before)
        with self.assertRaises(ValueError):self.service.perform('board-order',dict(payload,ids=[first,second]))
        payload={'request_id':uuid.uuid4().hex,'id':first,'description':'Receipt'}
        before=self.service.perform('card-style',payload)
        self.assertEqual(self.service.perform('card-style',payload),before)

    def test_chat_list_tracks_board_order_and_card_edit_shared_service(self):
        first,second=self.add('First'),self.add('Second')
        self.action('board-order',ids=[second,first])
        registry=assistant_tools.ToolRegistry(self.path)
        context={'refs':{},'step_id':uuid.uuid4().hex}
        cards=registry.execute('launcher_list',{},context)['data']['items']
        self.assertEqual([card['title'] for card in cards],['Second','First'])
        registry.execute('launcher_card_edit',{'ref':cards[0]['ref'],'description':'Edited by chat'},context)
        row=next(row for row in self.service.overview()['items'] if row['id']==second)
        self.assertEqual(row['card_description'],'Edited by chat')

    def test_chat_cover_uses_issued_file_selection_and_rejects_changed_source(self):
        local=tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parents[1])
        self.addCleanup(local.cleanup)
        folder=pathlib.Path(local.name)
        data,raw=image_data('GIF',animate=True)
        path=folder/'cover.gif'
        path.write_bytes(raw)
        item=self.add(icon_data=image_data()[0])
        registry=assistant_tools.ToolRegistry(self.path)
        registry.capabilities.files=file_workspace.FileWorkspace(self.path,workspace=folder)
        context={'refs':{},'step_id':uuid.uuid4().hex}
        with patch('file_workspace._APP_ROOT',folder/'Application'),patch('file_workspace._standard_roots',return_value=[]),patch('desktop_workspace._desktop_roots',return_value=[]),patch('scanner.FILES',[]):
            selected=registry.execute('files_find',{'query':'cover.gif'},context)['data']['items'][0]
            card=registry.execute('launcher_list',{},context)['data']['items'][0]
            registry.execute('launcher_card_edit',{'ref':card['ref'],'cover_ref':selected['ref']},context)
            self.assertEqual(self.service.cover_bytes(item),(raw,'image/gif'))
            self.assertTrue(self.service.overview()['items'][0]['icon_image'])
            path.write_bytes(image_data('PNG')[1])
            context['step_id']=uuid.uuid4().hex
            with self.assertRaises(ValueError):
                registry.execute('launcher_card_edit',{'ref':card['ref'],'cover_ref':selected['ref']},context)
            context['step_id']=uuid.uuid4().hex
            registry.execute('launcher_card_edit',{'ref':card['ref'],'clear_cover':True},context)
            self.assertFalse(self.service.overview()['items'][0]['card_cover'])


class LauncherCardsApiTests(unittest.TestCase):
    setUp=launcher_api.LauncherApiTests.setUp
    close=launcher_api.LauncherApiTests.close
    request=launcher_api.LauncherApiTests.request
    write=launcher_api.LauncherApiTests.write

    def test_cover_get_mime_revision_and_host_guard(self):
        data,raw=image_data('GIF',animate=True)
        item=self.write('save',title='Card',kind='web',target='https://example.com',card_cover_data=data)['result']['id']
        state=self.request()
        url=self.base+'/api/launcher/cover?id='+item+'&v='+state['items'][0]['card_cover_revision']
        with urllib.request.urlopen(url) as response:
            self.assertEqual(response.headers['Content-Type'],'image/gif')
            self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
            self.assertEqual(response.read(),raw)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url,headers={'Host':'foreign.example'}))
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base+'/api/launcher/cover?id='+'0'*32)
        self.assertEqual(error.exception.code,404)

    def test_new_actions_require_auth_and_known_fields(self):
        for action in ('board-order','card-style'):
            with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/launcher/'+action,{})
            self.assertEqual(error.exception.code,403)
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/launcher/'+action,{'token':app.DESK_TOKEN,'request_id':uuid.uuid4().hex},headers={'Origin':'https://foreign.example'})
            self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:self.write('card-style',id='0'*32,path='arbitrary')
        self.assertEqual(error.exception.code,400)

    def test_cover_above_small_icon_limit_and_http_global_board_reorder(self):
        from PIL import Image
        output=io.BytesIO()
        Image.frombytes('RGB',(512,512),os.urandom(512*512*3)).save(output,format='PNG')
        raw=output.getvalue()
        self.assertGreater(len(raw),quick_launcher._MAX_ICON_BYTES)
        cover='data:image/png;base64,'+base64.b64encode(raw).decode()
        first=self.write('save',title='Large cover',kind='web',target='https://example.com',card_cover_data=cover)['result']['id']
        second=self.write('save',title='Second',kind='web',target='https://example.org')['result']['id']
        state=self.write('board-order',ids=[second,first])
        self.assertEqual(state['board_order'],[second,first])
        self.assertTrue(next(row for row in state['items'] if row['id']==first)['card_cover'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.write('appearance',id=first,title='Small icon blocked',icon_data=cover)
        self.assertEqual(error.exception.code,400)


if __name__=='__main__':unittest.main()
