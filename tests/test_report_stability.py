import sys
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path.cwd()))
import report_automation as reports
import office_routines
import task_control


class Field:
    def __init__(self,page,selector): self.page,self.selector=page,selector
    @property
    def last(self): return self
    def fill(self,value): self.page.fills.append(value)
    def click(self,**kwargs):
        self.page.clicks+=1
        return getattr(self.page,'click_receipt',{'save_completed':True,'product_post_seen':True,'product_post_ok':True})
    def wait_for(self,**kwargs): return None
    def input_value(self):
        if self.page.editor: raise AssertionError('Readback used the stale editor DOM')
        name=next(name for name in ('gname','account','daccount') if 'name="'+name+'"' in self.selector)
        return self.page.current[name]


class Page:
    def __init__(self,states=None,editor=False):
        self.states=states or [];self.editor=editor;self.current={};self.clicks=0
        self.fills=[];self.visits=0;self.closed=False;self.waits=[]
    def locator(self,selector): return Field(self,selector)
    def goto(self,url,**kwargs):
        self.visits+=1;self.current=self.states[min(self.visits-1,len(self.states)-1)]
    def wait_for_timeout(self,value): self.waits.append(value)
    def wait_for_load_state(self,*args,**kwargs): return None
    def is_closed(self): return self.closed
    def close(self,**kwargs): self.closed=True


class Context:
    reconciliation_required=False
    def __init__(self,verification): self.verification=verification;self.created=0
    def new_page(self): self.created+=1;return self.verification


class ProductSaveRegression(unittest.TestCase):
    before={'gname':'A군 상품','account':'1000','daccount':'1000'}
    expected={'gname':'[완료]A군 상품','account':'1000','daccount':'1000'}
    def invoke(self,states,original=None,target=None):
        edit=Page(editor=True);verification=Page(states);context=Context(verification)
        outcome=reports._save_and_verify_product(context,edit,'https://banana.bizhost.kr/adminx/goods.php?index_no=123&code=g1_2_4',original or self.before,target or self.expected['gname'])
        return outcome,edit,verification
    def test_reads_fresh_server_document_not_editor(self):
        outcome,edit,page=self.invoke([self.expected])
        self.assertTrue(outcome['verified']);self.assertEqual(edit.clicks,1)
        self.assertEqual(page.visits,1);self.assertTrue(page.closed)
    def test_delayed_readback_repeats_reads_not_saves(self):
        outcome,edit,page=self.invoke([self.before,self.expected])
        self.assertTrue(outcome['verified']);self.assertEqual(edit.clicks,1)
        self.assertEqual(page.visits,2)
    def test_same_value_save_keeps_single_prefix_and_prices(self):
        outcome,edit,page=self.invoke([self.expected],original=self.expected)
        self.assertTrue(outcome['verified']);self.assertEqual(edit.fills,['[완료]A군 상품'])
        self.assertEqual(page.current['account'],'1000')
    def test_price_conflict_does_not_resubmit(self):
        edit=Page(editor=True);page=Page([{**self.expected,'account':'2000'}])
        with self.assertRaisesRegex(ValueError,'저장|가격|불일치'):
            reports._save_and_verify_product(Context(page),edit,'https://banana.bizhost.kr/adminx/goods.php?index_no=123',self.before,self.expected['gname'])
        self.assertEqual(edit.clicks,1);self.assertTrue(page.closed)
    def test_unchanged_readback_is_bounded_and_never_resubmits(self):
        edit=Page(editor=True);page=Page([self.before])
        with self.assertRaises(ValueError):
            reports._save_and_verify_product(Context(page),edit,'https://banana.bizhost.kr/adminx/goods.php?index_no=123',self.before,self.expected['gname'])
        self.assertEqual(edit.clicks,1);self.assertLessEqual(page.visits,3)
    def test_completed_product_still_excluded_from_normal_run(self):
        self.assertTrue(reports._exclude('[완료]A군 상품','A군'))
    def test_same_value_cannot_pass_without_completed_save_receipt(self):
        edit=Page(editor=True);edit.click_receipt=None
        with self.assertRaises(ValueError):
            reports._save_and_verify_product(Context(Page([self.expected])),edit,
                'https://banana.bizhost.kr/adminx/goods.php?index_no=123',self.expected,self.expected['gname'])
        self.assertEqual(edit.clicks,1)


class CombinedReportRegression(unittest.TestCase):
    def test_combined_commands_route_to_code(self):
        for text in ['일일보고서랑 AI보고서 작업 진행해줘','오늘 일일업무보고서와 ai 보고서 작성해줘','AI보고서하고 일일보고서 작성하자']:
            with self.subTest(text=text):
                self.assertEqual(office_routines.match(text),{'id':'reports_all','args':{}})
    def test_questions_and_edit_instructions_are_not_executed(self):
        for text in ['일일보고서랑 AI보고서 작성할 수 있어?','일일보고서랑 AI보고서 지침 수정해줘','보고서 작성해줘 그리고 파일을 삭제해줘']:
            with self.subTest(text=text): self.assertIsNone(office_routines.match(text))
    def test_daily_failure_does_not_skip_authorized_ai_report(self):
        calls=[]
        def call(name,args):
            calls.append(name);return {'status':'error' if name=='daily_report_run' else 'ok','message':name}
        text=office_routines.execute({'id':'reports_all','args':{}},call)
        self.assertEqual(calls,['daily_report_run','ai_report_run'])
        self.assertIn('daily_report_run',text);self.assertIn('ai_report_run',text)
    def test_stop_prevents_the_second_report(self):
        calls=[]
        def call(name,args): calls.append(name);raise task_control.Stopped()
        with self.assertRaises(task_control.Stopped):
            office_routines.execute({'id':'reports_all','args':{}},call)
        self.assertEqual(calls,['daily_report_run'])


class AiReportReplayRegression(unittest.TestCase):
    def test_unchanged_ai_report_has_no_save_action(self):
        node=shutil.which('node')
        if not node: self.skipTest('Existing Node runtime is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            record={'done':[],'note':'same','next':[],'risk':'','capabilities':[]}
            day='2026-10-28'
            cfg={'day':day,'record':record,'folder':folder.as_posix(),'space_name':'unit-only','resume_pending_prompt':False}
            (folder/'input.json').write_text(json.dumps(cfg),encoding='utf-8')
            module=(Path.cwd()/'ai_report_ego.mjs').as_uri()
            script='''
globalThis.aiReportPlan=PLAN;
const before={plan:{},months:{'2026-10':{'2026-10-28':{done:[],note:'same',next:[],risk:'',capabilities:[]}}}};
const page={url:async()=> 'https://dailywork-phi.vercel.app/?edit=1',waitForFunction:async()=>{},
 fetch:async()=>({ok:true,body:JSON.stringify(before)}),
 evaluate:async()=>({day:'2026-10-28',sections:0,capabilities:0,save:'unchanged'}),
 click:async()=>{throw Error('UNCHANGED_REPORT_MUST_NOT_SAVE')}};
globalThis.taskSpace=async()=>({spaceId:123,page:()=>page,finish:async()=>{}});
await import(MODULE);
'''.replace('PLAN',json.dumps((folder/'input.json').as_uri())).replace('MODULE',json.dumps(module))
            (folder/'run.mjs').write_text(script,encoding='utf-8')
            result=subprocess.run([node,str(folder/'run.mjs')],capture_output=True,text=True,encoding='utf-8',timeout=10,check=True)
            row=next(line[len('AI_REPORT_RESULT:'):] for line in result.stdout.splitlines() if line.startswith('AI_REPORT_RESULT:'))
            data=json.loads(row)
            self.assertTrue(data['verified']);self.assertTrue(data['unchanged'])
            self.assertFalse(data['saved']);self.assertEqual(data['save_clicks'],0)


class ProductDialogRegression(unittest.TestCase):
    def test_dialog_race_completion_and_rejection(self):
        node=shutil.which('node')
        if not node:self.skipTest('Existing Node runtime unavailable')
        module=(Path.cwd()/'ego_report_dialogs.mjs').as_uri()
        script='''
import assert from 'node:assert/strict';
const {saveProductWithDialogs,beginAiSave,acceptObservedDialog,waitAiCommit}=await import(MODULE);
function fake(message='상품수정하시겠습니까?'){
 const state={events:[],accepted:0,dismissed:0,info:0,triggers:0};
 const page={events:async()=>state.events.splice(0),
 info:async()=>{state.info++;throw Error('Runtime must not run while a dialog opens')},
 cdp:async(method,args)=>{
  if(method==='Page.handleJavaScriptDialog'){
   if(args.accept){state.accepted++;state.events.push({method:'Page.javascriptDialogClosed',params:{result:true}});
    if(state.accepted===1)state.events.push(
     {method:'Network.requestWillBeSent',params:{requestId:'save1',type:'Document',request:{method:'POST',url:'https://banana.bizhost.kr/adminx/goods.php?code=g1_2_4'}}},
     {method:'Network.responseReceived',params:{requestId:'save1',response:{status:200}}},
     {method:'Page.javascriptDialogOpening',params:{type:'alert',message:'수정완료되었습니다.'}});
   }else state.dismissed++;
  }return {};
 }};
 return {state,page,trigger:async()=>{state.triggers++;state.events.push({method:'Page.javascriptDialogOpening',params:{type:'confirm',message}});return true}};
}
const good=fake();const receipt=await saveProductWithDialogs(good.page,good.trigger,{timeoutMs:100,pollMs:1});
assert.equal(receipt.save_completed,true);assert.equal(receipt.product_post_seen,true);
assert.equal(good.state.accepted,2);assert.equal(good.state.info,0);assert.equal(good.state.triggers,1);
const wrong=fake('상품삭제하시겠습니까?');await assert.rejects(()=>saveProductWithDialogs(wrong.page,wrong.trigger,{timeoutMs:50,pollMs:1}));
assert.equal(wrong.state.accepted,0);assert.equal(wrong.state.dismissed,1);

const aiState={events:[],clicks:0,accepted:0};
const ai={events:async()=>aiState.events.splice(0),cdp:async(method,args)=>{
 if(method==='Page.handleJavaScriptDialog'){
  assert.equal(args.promptText,'unit-test-only');aiState.accepted++;
  aiState.events.push({method:'Page.javascriptDialogClosed',params:{}},
   {method:'Network.requestWillBeSent',params:{requestId:'ai1',request:{method:'POST',url:'https://dailywork-phi.vercel.app/api/data'}}},
   {method:'Network.responseReceived',params:{requestId:'ai1',response:{status:200}}},
   {method:'Network.loadingFinished',params:{requestId:'ai1'}});
 }return {};
}};
const observation=await beginAiSave(ai,async()=>{
 aiState.clicks++;aiState.events.push({method:'Page.javascriptDialogOpening',params:{type:'prompt',message:'입력 비밀번호를 넣어주세요.'}});
 const error=new Error('renderer interrupted');error.name='PageDialogOpenedError';throw error;
},{timeoutMs:100,pollMs:1});
assert.equal(observation.dialog.type,'prompt');await acceptObservedDialog(ai,'unit-test-only');observation.dialog=null;
await waitAiCommit(ai,observation,{timeoutMs:100,pollMs:1});
assert.equal(aiState.clicks,1);assert.equal(aiState.accepted,1);assert.equal(observation.postOk,true);
console.log('DIALOG_REGRESSION_OK');

'''.replace('MODULE',json.dumps(module))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.mjs';path.write_text(script,encoding='utf-8')
            result=subprocess.run([node,str(path)],capture_output=True,text=True,encoding='utf-8',timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('DIALOG_REGRESSION_OK',result.stdout)


if __name__=='__main__': unittest.main(verbosity=2)
