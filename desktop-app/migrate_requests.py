"""Copy one explicitly selected Office tab's last request list to a new host.

No browser profile, cookies, credentials, chat content or application settings
are read. Existing WebView local storage is never replaced by the host.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--client-id', required=True)
    args = parser.parse_args()
    with sqlite3.connect((ROOT / 'office.db').as_uri() + '?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT data,updated FROM office_clients WHERE id=?', (args.client_id,)).fetchone()
    if not row or time.time() - row[1] > 1800:
        raise ValueError('선택한 기존 탭의 최근 화면 기록이 필요합니다.')
    source = json.loads(row[0]).get('report_inputs', {})
    requests = source.get('requests')
    if not source.get('available') or not isinstance(requests, list) or len(requests) > 100:
        raise ValueError('선택한 탭의 요청 목록을 확인하지 못했습니다.')
    fields = {'team', 'status', 'content', 'start', 'due'}
    if any(not isinstance(item, dict) or set(item) - fields or
           any(not isinstance(value, str) for value in item.values()) for item in requests):
        raise ValueError('요청 목록 형태를 확인해 주세요.')
    data = json.dumps(requests, ensure_ascii=False)
    if len(data) > 16000:
        raise ValueError('요청 목록이 허용 크기를 넘었습니다.')
    folder = ROOT / 'local_backups' / 'desktop-app'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'other-requests-seed.json'
    with target.open('x', encoding='utf-8') as stream:
        stream.write(data)
    print(json.dumps({'copied_request_count': len(requests), 'source_client': args.client_id}))


if __name__ == '__main__':
    main()
