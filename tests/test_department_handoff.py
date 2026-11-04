"""A native final alone cannot release dependent department work."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import department_tasks


class HandoffTest(unittest.TestCase):
    def test_requires_verified_result_and_detects_changed_result(self):
        parent = {'id': 'a' * 32, 'source_request_id': 'b' * 32, 'state': 'completed',
                  'title': 'research', 'result': 'artifact A'}
        child = {'source_request_id': parent['source_request_id'], 'depends_on': [parent['id']]}
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE office_department_tasks(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            def save():
                conn.execute('INSERT OR REPLACE INTO office_department_tasks VALUES (?,?)',
                             (parent['id'], json.dumps(parent)))
            save()
            self.assertEqual(department_tasks._handoff_context(conn, child)[0], [parent['id']])
            parent['handoff_acceptance'] = {'result_sha256': department_tasks._result_digest(parent),
                                            'evidence': 'independent artifact check'}
            save()
            blocked, ready = department_tasks._handoff_context(conn, child)
            self.assertEqual(blocked, [])
            self.assertEqual([r['result'] for r in ready], ['artifact A'])
            parent['result'] = 'artifact B'
            save()
            self.assertEqual(department_tasks._handoff_context(conn, child)[0], [parent['id']])


if __name__ == '__main__':
    unittest.main()
