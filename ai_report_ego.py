"""AI report drafts saved by the official Ego browser and the site's own UI.

The credential is fetched only for the observed origin and prompt; it is not
included in the plan, command line, report, or execution receipt.
"""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time

import credential_vault
import ego_workspace
import office_fastlane
import task_control

ROOT = Path(__file__).resolve().parent
ORIGIN = 'https://dailywork-phi.vercel.app'
PROMPT = '입력 비밀번호를 넣어주세요.'


def draft_from_task(db, task_id):
    """Copy one user-prepared source. Do not reinterpret unrelated work."""
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro', uri=True)) as c:
        row = c.execute('SELECT title,detail,status FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not row or not row[1].strip():
        raise ValueError('지정한 업무 원문을 확인하지 못했어요.')
    title, detail, status = row
    detail = detail.replace('GODO', 'AI운영 시스템')
    parts = re.split(r'^■\s*', detail.strip(), flags=re.M)
    intro = [line.strip() for line in parts[0].splitlines() if line.strip()]
    state = '완료' if status == 'done' else '진행'
    done, capabilities = [], []
    for section in parts[1:]:
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        heading, body = lines[0], lines[1:]
        text = heading+'\n'+'\n'.join(line if line.startswith('-') else '- '+line for line in body)
        done.append({'who':'1', 'text':text, 'state':state})
        ai = 'AI 운영 시스템' in heading or 'AI운영 시스템' in heading
        features = [line[2:].strip() for line in body if line.startswith('- ')]
        capabilities.append({'name':heading, 'state':'개발 화면 확인' if ai else '개발몰 확인',
            'environment':'오늘 지정 업무 원문 기준 · '+('AI운영 시스템 로컬·비공개 개발 범위 · 전체 배포 미완료' if ai else '메두사 가운영 준비 단계 · 공개 판매 전 시험 운영'),
            'detail':' / '.join(features[:3])+(' 외 '+str(len(features)-3)+'개 항목' if len(features)>3 else '')})
    if not done:
        done = [{'who':'1', 'text':title+'\n'+'\n'.join('- '+s.lstrip('- ').strip() for s in intro), 'state':state}]
    return {'done':done, 'note':title+'\n'+'\n'.join('- '+s for s in intro),
            'next':[], 'risk':'', 'capabilities':capabilities}


def run(db, day, record, request_id, space_id=None, *, resume_pending_prompt=False, verification_save=False):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
        raise ValueError('보고서 날짜와 현재 요청 ID를 확인해 주세요.')
    if not isinstance(record, dict) or not {'done','note','next','risk','capabilities'} <= record.keys():
        raise ValueError('준비된 AI 보고서 양식을 확인해 주세요.')
    if space_id is not None and type(space_id) is not int:
        raise ValueError('기존 Ego 공간 ID를 확인해 주세요.')
    office_fastlane.require_active_request(db, request_id)
    accounts = [r for r in credential_vault.listing(ORIGIN) if r['origin'] == ORIGIN]
    if len(accounts) != 1:
        raise ValueError('AI 보고서 저장용 보관 계정을 하나로 확인하지 못했어요.')
    folder = ROOT/'local_backups'/'ai-report-ego'/request_id
    if verification_save:
        if resume_pending_prompt:raise ValueError('Verification cannot resume another save')
        folder=folder/'verification-save-v5'
    folder.mkdir(parents=True, exist_ok=True)
    receipt_path = folder/'result.json'
    resume_before = None
    if receipt_path.exists():
        previous=json.loads(receipt_path.read_text(encoding='utf-8'))
        if not resume_pending_prompt:return dict(previous,replayed=True)
        if previous.get('verified') or previous.get('state')!='uncertain' or previous.get('error_type')!='PageDialogOpenedError' or space_id!=previous.get('space_id'):
            raise ValueError('현재 저장 인증창의 이어가기 근거가 일치하지 않아요.')
        resume_before=(folder/'before.json').as_posix()
        folder=folder/'pending-prompt-resume-v2';folder.mkdir(exist_ok=True)
        receipt_path=folder/'result.json'
        if receipt_path.exists():return dict(json.loads(receipt_path.read_text(encoding='utf-8')),replayed=True)
    elif resume_pending_prompt:raise ValueError('이어서 처리할 인증창 기록이 없어요.')
    receipt = {'request_id':request_id, 'day':day, 'browser':'ego', 'state':'preparing',
               'saved':False, 'verified':False, 'mode':'code', 'model_calls':0}
    import execution_receipts
    execution_receipts.write_json(receipt_path,receipt)
    token = secrets.token_urlsafe(32)
    used = False
    secret_values = []
    def checkpoint():
        if office_fastlane.stopped(db, request_id):
            raise task_control.Stopped()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            nonlocal used
            try:
                checkpoint()
                if self.path != '/prompt' or not secrets.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token):
                    raise ValueError()
                n = int(self.headers.get('Content-Length','0'))
                if not 0 < n < 2000: raise ValueError()
                data = json.loads(self.rfile.read(n))
                if used or credential_vault.origin(data.get('url','')) != ORIGIN or data.get('prompt') != PROMPT:
                    raise ValueError()
                _, secret = credential_vault.load_for_origin(accounts[0]['id'], data['url'])
                used = True
                secret_values.append(secret)
                raw = json.dumps({'password':secret}).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.send_error(403)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    plan = {'force_write':bool(verification_save), 'day':day, 'record':record, 'space_id':space_id,
            'resume_pending_prompt':resume_pending_prompt,'resume_before':resume_before,
            'space_name':'AI report '+day+' '+request_id[:8],
            'folder':folder.as_posix(), 'broker':'http://127.0.0.1:'+str(server.server_port)+'/prompt', 'token':token}
    plan_path = folder/'input.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    entry = folder/'run.mjs'
    entry.write_text('globalThis.aiReportPlan='+json.dumps(plan_path.as_uri())+';\nawait import('+json.dumps((ROOT/'ai_report_ego.mjs').as_uri())+');\n', encoding='utf-8')
    started = time.monotonic()
    proc = None
    output = folder/'output.txt'
    try:
        checkpoint()
        with output.open('wb') as stream:
            proc = subprocess.Popen([ego_workspace.executable(), 'nodejs', '-e', "await import('"+entry.as_uri()+"')"],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, cwd=str(ROOT),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while proc.poll() is None:
                checkpoint()
                if time.monotonic()-started > 110: raise TimeoutError()
                time.sleep(.15)
        raw = output.read_text(encoding='utf-8', errors='replace')
        matches = [s[len('AI_REPORT_RESULT:'):] for s in raw.splitlines() if s.startswith('AI_REPORT_RESULT:')]
        if not matches: raise RuntimeError('Ego 결과 영수증을 확인하지 못했어요.')
        receipt.update(json.loads(matches[-1]))
    except Exception as error:
        receipt.update(state='stopped' if isinstance(error,task_control.Stopped) else 'uncertain',
                       error_type=type(error).__name__, automatic_retry=False)
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
        server.shutdown()
        server.server_close()
        if output.exists():
            raw=output.read_text(encoding='utf-8',errors='replace')
            for secret in secret_values:
                if secret:raw=raw.replace(secret,'[redacted]')
            output.write_text(raw,encoding='utf-8')
        plan_path.unlink(missing_ok=True)
        secret_values.clear()
        receipt['elapsed_ms']=round((time.monotonic()-started)*1000)
        execution_receipts.write_json(receipt_path,receipt)
    return receipt
