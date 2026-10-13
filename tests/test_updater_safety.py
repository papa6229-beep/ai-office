import pathlib, tempfile, unittest, sys, json, sqlite3, subprocess, zipfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import updater, content_analysis

class UpdaterSafetyTests(unittest.TestCase):
    def test_restore_refuses_separate_worktree_edits(self):
        old=updater.git
        def fake(*args,**kwargs):
            class Result:returncode=0;stdout=' M app.py';stderr=''
            return Result()
        updater.git=fake
        try:
            with self.assertRaises(RuntimeError):updater.restore_code('before',['app.py'])
        finally:updater.git=old
    def test_targeted_restore_rewinds_update_without_touching_untracked_data(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);old=updater.ROOT;updater.ROOT=root
            try:
                updater.git('init','-b','main')
                updater.git('config','user.email','test@example.invalid');updater.git('config','user.name','Test')
                (root/'.gitignore').write_text('office.db\n');updater.git('add','.gitignore');updater.git('commit','-m','ignore user data')
                (root/'app.py').write_text('old');updater.git('add','app.py');updater.git('commit','-m','base');before=updater.git('rev-parse','HEAD').stdout.strip()
                (root/'app.py').write_text('new');updater.git('commit','-am','update');after=updater.git('rev-parse','HEAD').stdout.strip()
                (root/'office.db').write_bytes(b'user data')
                updater.restore_code(before,['app.py'],expected_head=after)
                self.assertEqual(updater.git('rev-parse','HEAD').stdout.strip(),before)
                self.assertEqual((root/'app.py').read_text(),'old')
                self.assertEqual((root/'office.db').read_bytes(),b'user data')
                self.assertFalse(updater.git('status','--porcelain').stdout.strip())
            finally:updater.ROOT=old

    def _run_update_fixture(self, broken=False):
        with tempfile.TemporaryDirectory() as td:
            base=pathlib.Path(td);source=base/'source';source.mkdir();remote=base/'origin.git';work=base/'work'
            def git_at(cwd,*args):return subprocess.run(['git','-C',str(cwd),*args],capture_output=True,text=True)
            git_at(source,'init','-b','main');git_at(source,'config','user.email','test@example.invalid');git_at(source,'config','user.name','Test')
            (source/'.gitignore').write_text('office.db\nsettings.json\nstartup.log\nupdate_status.json\nlocal_backups/\n')
            for name in ('app.py','scanner.py','project_intelligence.py','workspace_insights.py','updater.py'):(source/name).write_text('value = 1\n')
            git_at(source,'add','.');git_at(source,'commit','-m','base');git_at(base,'init','--bare',str(remote));git_at(source,'remote','add','origin',str(remote));git_at(source,'push','-u','origin','main')
            subprocess.run(['git','clone','--branch','main',str(remote),str(work)],capture_output=True,check=True)
            git_at(source,'config','user.email','test@example.invalid');git_at(source,'config','user.name','Test')
            (source/'app.py').write_text('def broken(:\n' if broken else 'value = 2\n');git_at(source,'commit','-am','update');git_at(source,'push','origin','main')
            c=sqlite3.connect(work/'office.db');c.execute('CREATE TABLE keep(v)');c.execute('INSERT INTO keep VALUES (42)');c.commit();c.close();(work/'settings.json').write_text('{"api_key":"test-only"}')
            old=(updater.ROOT,updater.BACKUPS,updater.STATUS,updater.check,updater.launch,updater.port_open)
            updater.ROOT=work;updater.BACKUPS=work/'local_backups';updater.STATUS=work/'update_status.json';updater.check=lambda:{'phase':'ready'};updater.launch=lambda:None;updater.port_open=lambda:False
            try:
                updater.apply()
                state=json.loads(updater.STATUS.read_text(encoding='utf-8'))
                c=sqlite3.connect(work/'office.db');dbval=c.execute('SELECT v FROM keep').fetchone()[0];c.close()
                return state,(work/'app.py').read_text(encoding='utf-8'),dbval,json.loads((work/'settings.json').read_text(encoding='utf-8')),git_at(work,'rev-parse','HEAD').stdout.strip(),git_at(work,'status','--porcelain').stdout
            finally:updater.ROOT,updater.BACKUPS,updater.STATUS,updater.check,updater.launch,updater.port_open=old
    def test_local_remote_update_succeeds_and_preserves_user_data(self):
        state,code,db,settings,head,status=self._run_update_fixture()
        self.assertEqual(state['phase'],'done');self.assertEqual(code,'value = 2\n');self.assertEqual(db,42);self.assertEqual(settings['api_key'],'test-only');self.assertFalse(status)
    def test_local_remote_code_validation_failure_rolls_back_commit_and_data(self):
        state,code,db,settings,head,status=self._run_update_fixture(broken=True)
        self.assertEqual(state['phase'],'error');self.assertEqual(code,'value = 1\n');self.assertEqual(db,42);self.assertEqual(settings['api_key'],'test-only');self.assertFalse(status)

    def test_backup_preserves_db_and_settings_in_isolated_root(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);(root/'local_backups').mkdir()
            c=sqlite3.connect(root/'office.db')
            try:c.execute('CREATE TABLE t(v)');c.execute('INSERT INTO t VALUES (7)');c.commit()
            finally:c.close()
            (root/'settings.json').write_text('{"api_key":"test-only"}')
            old_root,old_backups=updater.ROOT,updater.BACKUPS;updater.ROOT=root;updater.BACKUPS=root/'local_backups'
            try:
                folder=updater.backup()
                c=sqlite3.connect(folder/'office.db')
                try:self.assertEqual(c.execute('SELECT v FROM t').fetchone()[0],7)
                finally:c.close()
                self.assertEqual(json.loads((folder/'settings.json').read_text())['api_key'],'test-only')
            finally:updater.ROOT,updater.BACKUPS=old_root,old_backups
    def test_check_rejects_tracked_private_data(self):
        old=updater.git
        def fake(*args,**kwargs):
            class Result:returncode=0;stdout='';stderr=''
            r=Result()
            if args[:2]==('remote','get-url'):r.stdout='https://github.com/papa6229-beep/ai-office.git'
            elif args[:2]==('branch','--show-current'):r.stdout='main'
            elif args and args[0]=='ls-files':r.stdout='office.db'
            return r
        updater.git=fake
        try:self.assertEqual(updater.check()['phase'],'error')
        finally:updater.git=old

class ContentAnalysisTests(unittest.TestCase):
    def test_reads_text_and_code_without_modifying_files(self):
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'task.py';p.write_text('def deliver_report():\n    return "invoice"\n')
            before=p.stat().st_mtime_ns;r=content_analysis.analyze_file(str(p))
            self.assertIn('deliver_report',r['preview']);self.assertEqual(before,p.stat().st_mtime_ns)
            secret=pathlib.Path(td)/'config.json';secret.write_text('{"api_key":"do-not-show","ok":true}')
            self.assertNotIn('do-not-show',content_analysis.analyze_file(str(secret))['preview'])
            xml=pathlib.Path(td)/'service.xml';xml.write_text('<config><api_key>hidden-value</api_key></config>')
            self.assertNotIn('hidden-value',content_analysis.analyze_file(str(xml))['preview'])
    def test_extracts_docx_text_with_zip_safety_limits(self):
        import xml.sax.saxutils
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'brief.docx'
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Project delivery plan</w:t></w:r></w:p></w:body></w:document>'
            with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml',xml)
            result=content_analysis.analyze_file(str(p))
            self.assertIn('Project delivery plan',result['preview'])
    def test_extracts_pdf_pages_when_reader_is_available(self):
        try:from pypdf import PdfWriter
        except ImportError:self.skipTest('pypdf optional')
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'brief.pdf';writer=PdfWriter();writer.add_blank_page(width=72,height=72)
            with p.open('wb') as stream:writer.write(stream)
            result=content_analysis.analyze_file(str(p))
            self.assertTrue(result['preview'].strip().endswith('1'))
    def test_rejects_secrets_and_binary(self):
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'.env';p.write_text('API_KEY=secret')
            with self.assertRaises(ValueError):content_analysis.analyze_file(str(p))
    def test_enforces_size_limit(self):
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'large.txt';p.write_bytes(b'a'*(content_analysis.MAX_BYTES+1))
            with self.assertRaises(ValueError):content_analysis.analyze_file(str(p))

class AnalyzeApiTests(unittest.TestCase):
    def test_local_review_api_only_uses_scanned_files_and_loopback(self):
        import app, threading, urllib.request, urllib.error
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'brief.txt';p.write_text('Project launch')
            old_files=app.scanner.FILES;old_config=app.config;old_model=app.call_gemma;old_log=app.runlog
            app.scanner.FILES=[{'name':p.name,'path':str(p),'extension':'.txt','protected':False}]
            app.config=lambda:{'gemma_url':'http://127.0.0.1:11434'}
            app.call_gemma=lambda prompt,settings=None:'{"items":[{"id":"1","project":"Launch","task":"Prepare","confidence":0.9,"reason":"Content evidence"}]}'
            app.runlog=lambda *args,**kwargs:None
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:%s/api/analyze-workspace-content'%server.server_port
                req=urllib.request.Request(url,data=json.dumps({'paths':[str(p)]}).encode(),headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(req,timeout=3) as response:result=json.load(response)
                self.assertEqual(result['items'][0]['state'],'검토 후보')
                req=urllib.request.Request(url,data=json.dumps({'paths':[str(pathlib.Path(td)/'outside.txt')]}).encode(),headers={'Content-Type':'application/json'},method='POST')
                with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req,timeout=3)
                self.assertEqual(err.exception.code,400)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)
                app.scanner.FILES=old_files;app.config=old_config;app.call_gemma=old_model;app.runlog=old_log
    def test_http_endpoint_returns_preview_without_secret(self):
        import app, threading, urllib.request, urllib.parse
        with tempfile.TemporaryDirectory() as td:
            p=pathlib.Path(td)/'brief.txt';p.write_text('API_KEY=private-value\nProject plan')
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                url='http://127.0.0.1:%s/api/analyze-file?%s'%(server.server_port,urllib.parse.urlencode({'path':str(p)}))
                with urllib.request.urlopen(url,timeout=3) as response:data=json.load(response)
                self.assertIn('Project plan',data['preview']);self.assertNotIn('private-value',data['preview'])
            finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
