"""Process-level restart exercise using synthetic report responses."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import design_team


INSTANT = dt.datetime(2026, 11, 2, 10, 0, tzinfo=design_team.SEOUL)


def worker(root, interruption):
    db = root / 'pilot.db'
    urls = {member: 'https://example.invalid/member/' + member
            for member, _ in design_team.MEMBERS}
    original_store = design_team.store

    def fetch(url):
        parts = urlsplit(url)
        member, kind = parts.path.split('/')[-2:]
        with (root / 'fetches.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps([member, kind]) + '\n')
        if kind == 'report':
            return {'member': {'id': member, 'name': dict(design_team.MEMBERS)[member]},
                    'report': {'status': 'submitted' if member != design_team.MEMBERS[-1][0] else 'draft',
                               'workEntries': [], 'brandUpdates': []}}
        return {'month': parse_qs(parts.query)['month'][0], 'days': []}

    def store(*args, **kwargs):
        original_store(*args, **kwargs)
        if interruption == 'after_item':
            os._exit(37)
        if interruption == 'hold_after_item':
            (root / 'worker.pid').write_text(str(os.getpid()), encoding='ascii')
            time.sleep(120)

    original_save = design_team.save_schedule

    def save(db, state):
        original_save(db, state)
        if interruption == 'after_complete' and state.get('success'):
            os._exit(38)

    with patch.object(design_team, 'sources', return_value=urls), \
         patch.object(design_team, 'attendance', return_value={'codex_running': True, 'phone_connected': True}), \
         patch.object(design_team, 'fetch', fetch), patch.object(design_team, 'store', store), \
         patch.object(design_team, 'save_schedule', save), \
         patch.object(design_team, 'ThreadPoolExecutor', lambda **_: ThreadPoolExecutor(max_workers=1)), \
         patch.dict(sys.modules, {'desktop_chat_mirror': SimpleNamespace(announce=lambda *args: None)}):
        instant = INSTANT + dt.timedelta(days=1) if interruption == 'next_day' else INSTANT
        design_team.run_scheduled(db, instant, retry_seconds=0)


def evidence(root):
    db = root / 'pilot.db'
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT member,kind,period,data FROM design_team_snapshots ORDER BY member,kind").fetchall()
        logs = conn.execute("SELECT count(*) FROM work_log_events WHERE source_key='design-team:2026-11-02'").fetchone()[0]
    fetches = [tuple(json.loads(line)) for line in (root / 'fetches.jsonl').read_text().splitlines()]
    return {'reports': {member: json.loads(data)['report']['status'] for member, kind, _, data in rows if kind == 'report'},
            'reference_days': sorted({period for _, kind, period, _ in rows if kind == 'report'}),
            'snapshot_count': len(rows), 'fetch_count': len(fetches), 'distinct_fetches': len(set(fetches)),
            'completion_count': logs, 'mode': design_team.schedule(db)['mode'],
            'model_calls': design_team.schedule(db)['model_calls']}


class ScheduleRecoveryTest(unittest.TestCase):
    def test_restart_and_repeat(self):
        scenarios = [('after_item', 37, 'normal'), ('after_complete', 38, 'normal'),
                     ('after_item', 37, 'next_day')]
        for interruption, exit_code, restart in scenarios:
            with self.subTest(interruption=interruption, restart=restart), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                command = [sys.executable, str(Path(__file__).resolve()), '--worker', tmp]
                for phase, expected in [(interruption, exit_code), (restart, 0), ('normal', 0)]:
                    result = subprocess.run([*command, phase], capture_output=True, timeout=45)
                    self.assertEqual(result.returncode, expected, result.stderr.decode(errors='replace'))
                result = evidence(root)
                self.assertEqual(result['reference_days'], ['2026-10-30'])
                self.assertEqual(sorted(result['reports'].values()), ['draft', 'submitted', 'submitted', 'submitted'])
                self.assertEqual((result['snapshot_count'], result['fetch_count'], result['distinct_fetches']), (8, 8, 8))
                self.assertEqual((result['completion_count'], result['mode'], result['model_calls']), (1, 'code', 0))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) > 1 and sys.argv[1] == '--evidence':
        print(json.dumps(evidence(Path(sys.argv[2])), sort_keys=True))
    else:
        unittest.main()
