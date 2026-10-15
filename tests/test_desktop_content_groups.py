import pathlib
import tempfile
import unittest
from unittest.mock import patch

import desktop_collections as groups
import desktop_preferences


class ContentGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=pathlib.Path(self.temp.name)/'desktop.json'
        self.addCleanup(patch.stopall)
        patch.object(desktop_preferences,'STATE',self.state).start()
        self.ids=['a'*64,'b'*64,'c'*64]
        self.snapshot={'items':[{'id':item,'name':f'file{n}.txt','kind':'file','openable':True,'path':str(pathlib.Path(self.temp.name)/f'file{n}.txt'),'size':10,'modified':'2026-10-15T00:00:00'} for n,item in enumerate(self.ids)]}
        patch.object(groups.workspace,'inventory',return_value=self.snapshot).start()

    def proposal(self,ids=None):
        return [{'id':'f'*64,'label':'내용에서 확인한 행사 자료','item_ids':ids or self.ids[:2],'evidence':'두 문서가 같은 행사명과 일정에 관한 내용입니다.','confidence':.9}]

    def test_content_groups_apply_automatically_without_moving_files(self):
        result=groups.sync_content_groups(self.proposal(),self.snapshot)
        self.assertEqual(result['collections'][0]['source'],'content_ai')
        self.assertEqual(result['collections'][0]['item_ids'],self.ids[:2])
        before=self.state.read_bytes()
        groups.sync_content_groups(self.proposal(),self.snapshot)
        self.assertEqual(self.state.read_bytes(),before)

    def test_manual_groups_pins_and_user_edits_survive_sync(self):
        desktop_preferences.pin(self.ids[0],True)
        manual=groups.create('내가 만든 작업함',[self.ids[0]])['collections'][0]
        result=groups.sync_content_groups(self.proposal(),self.snapshot)
        self.assertEqual(result['collections'][0]['id'],manual['id'])
        self.assertEqual(desktop_preferences.load()['pinned_ids'],[self.ids[0]])
        self.assertEqual(len(result['collections']),1)

    def test_dissolving_ai_group_does_not_silently_recreate_it(self):
        result=groups.sync_content_groups(self.proposal(),self.snapshot)
        group_id=result['collections'][0]['id']
        groups.dissolve(group_id)
        self.assertEqual(groups.sync_content_groups(self.proposal(),self.snapshot)['collections'],[])

    def test_renamed_ai_group_becomes_user_owned(self):
        result=groups.sync_content_groups(self.proposal(),self.snapshot)
        groups.rename(result['collections'][0]['id'],'내 이름')
        result=groups.sync_content_groups(self.proposal(),self.snapshot)
        self.assertEqual(result['collections'][0]['label'],'내 이름')
        self.assertEqual(result['collections'][0]['source'],'manual')

    def test_unknown_ids_and_missing_content_evidence_cannot_make_groups(self):
        for proposals in (self.proposal(['a'*64,'d'*64]),[{**self.proposal()[0],'evidence':''}]):
            with self.subTest(proposals=proposals),self.assertRaises(ValueError):
                groups.sync_content_groups(proposals,self.snapshot)
