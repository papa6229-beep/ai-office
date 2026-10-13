import tempfile, pathlib, unittest, sys, json
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import updater
class NoUpdateLaunchTests(unittest.TestCase):
 def test_windows_update_launcher_uses_backup_recovery_helper(self):
  script=(pathlib.Path(__file__).resolve().parents[1]/'업데이트 후 실행.bat').read_text(encoding='ascii')
  self.assertIn('updater.py apply',script);self.assertNotIn('git pull',script.lower())
 def test_no_update_launches_installed_app(self):
  with tempfile.TemporaryDirectory() as td:
   old=(updater.ROOT,updater.STATUS,updater.fetch,updater.launch);updater.ROOT=pathlib.Path(td);updater.STATUS=updater.ROOT/'update_status.json';calls=[]
   updater.fetch=lambda:{'phase':'current','message':'Already current'};updater.launch=lambda:calls.append(True)
   try:
    updater.apply();self.assertEqual(calls,[True]);self.assertEqual(json.loads(updater.STATUS.read_text(encoding='utf-8'))['phase'],'current')
   finally:updater.ROOT,updater.STATUS,updater.fetch,updater.launch=old
if __name__=='__main__':unittest.main()
