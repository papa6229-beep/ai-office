import pathlib
import tempfile
import unittest
import uuid

from work_assistant import WorkAssistant


class TaskHistoryTests(unittest.TestCase):
    def test_selected_task_history_is_not_lost_in_global_activity_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            service=WorkAssistant(pathlib.Path(folder)/'office.db',lambda:{},lambda *a,**k:'{}')
            first=service.perform('task',{'title':'오래된 업무','request_id':uuid.uuid4().hex})['result']['task_id']
            second=service.perform('task',{'title':'다른 업무','request_id':uuid.uuid4().hex})['result']['task_id']
            with service._connect() as c:
                for n in range(210):
                    c.execute('INSERT INTO work_events VALUES (?,?,?,?,?)',(uuid.uuid4().hex,second,'note','다른 업무 기록','2099-01-01T00:00:%02d'%(n%60)))
            result=service.task_history(first)
            self.assertEqual(result['task_id'],first)
            self.assertEqual(len(result['events']),1)
            self.assertEqual(result['events'][0]['kind'],'created')
            self.assertFalse(result['truncated'])
            with self.assertRaises(ValueError):service.task_history('f'*32)
