import pathlib, tempfile, unittest, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import local_content_review
class LocalReviewTests(unittest.TestCase):
 def test_local_model_returns_bounded_review_items(self):
  files=[{'name':'brief.txt','path':'x','extension':'.txt'}]
  def reader(_):return {'preview':'Quarterly client launch'}
  def model(prompt):return '{"items":[{"id":"1","name":"brief.txt","project":"Client launch","task":"Prepare report","confidence":0.92,"reason":"The text describes a launch."}]}'
  result=local_content_review.analyze(files,{'gemma_url':'http://127.0.0.1:11434'},reader,model)
  self.assertEqual(result['items'][0]['state'],'검토 후보')
 def test_configured_lan_server_reads_selected_file_without_changes(self):
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'brief.txt';path.write_text('launch project',encoding='utf-8');before=path.read_bytes()
   files=[{'name':path.name,'path':str(path),'extension':'.txt'}];calls=[]
   result=local_content_review.analyze(files,{'gemma_url':'http://192.168.0.40:11434'},lambda p:{'preview':pathlib.Path(p).read_text()},lambda prompt:calls.append(prompt) or '{"items":[]}')
   self.assertEqual(len(calls),1);self.assertIn('launch project',calls[0]);self.assertEqual(path.read_bytes(),before)
 def test_unsafe_endpoint_is_rejected_before_read_or_model(self):
  for url in ['file:///x','ftp://server','http://user:secret@server:11434','http://user@server','http://server:bad','http://server:65536','http://server:','http:///missing','http://server/path?token=secret','http://server/#fragment','http://[::1]evil','http://server\\path','http://server/\n']:
   with self.subTest(url=url),self.assertRaises(ValueError):
    local_content_review.analyze([{'name':'a.txt','extension':'.txt'}],{'gemma_url':url},lambda _: self.fail('reader called'),lambda _:self.fail('model called'))
 def test_valid_configured_endpoint_forms(self):
  for url in ['http://127.0.0.1:11434','http://localhost:11434','http://[::1]:11434','http://192.168.0.40:11434','https://gemma.internal/v1']:
   with self.subTest(url=url):self.assertEqual(local_content_review.validate_endpoint(url),url)
 def test_low_confidence_is_deferred_and_unknown_files_are_ignored(self):
  files=[{'name':'a.txt','path':'a','extension':'.txt'},{'name':'b.txt','path':'b','extension':'.txt'}]
  def model(_):return '{"items":[{"id":"1","name":"a.txt","project":"","task":"","confidence":0.3,"reason":"Not enough context."},{"id":"x","name":"outside.txt","project":"x","task":"x","confidence":1,"reason":"x"}]}'
  result=local_content_review.analyze(files,{'gemma_url':'http://localhost:11434'},lambda _: {'preview':'content'},model)
  self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['state'],'보류')

 def test_local_model_redirect_is_not_followed(self):
  from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
  import threading,urllib.error,app
  requests=[]
  class Redirect(BaseHTTPRequestHandler):
   def do_POST(self):
    requests.append(self.path);self.send_response(307);self.send_header('Location','http://example.invalid/collect');self.end_headers()
   def log_message(self,*args):pass
  server=ThreadingHTTPServer(('127.0.0.1',0),Redirect);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  old=app.config;app.config=lambda:{'gemma_url':f'http://127.0.0.1:{server.server_port}','timeout':3,'gemma_model':'test','api_type':'openai','api_key':''}
  try:
   with self.assertRaises(urllib.error.HTTPError):app.call_gemma('private document text')
   self.assertEqual(requests,['/chat/completions'])
  finally:app.config=old;server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
