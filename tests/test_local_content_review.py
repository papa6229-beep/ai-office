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
 def test_remote_model_endpoint_is_rejected(self):
  with self.assertRaises(ValueError):local_content_review.analyze([{'name':'a.txt','extension':'.txt'}],{'gemma_url':'https://example.com'},lambda _: {'preview':'secret'},lambda _: '')
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
