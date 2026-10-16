"""Shared, registered actions used by HTTP controls and assistant tools.

Only server-issued file selections and saved organizer scopes grant authority.
This module intentionally has no dynamic function, command or path dispatcher.
"""
import contextlib
import copy
import json
import re
import sqlite3
import threading
import time
import uuid

import file_workspace
import organizer_policy


LOCK = threading.RLock()
SELECTION_TTL = 15 * 60


class CapabilityService:
    def __init__(self, db_path, workspace=None, files=None):
        self.db_path = str(db_path)
        self.files = files or file_workspace.FileWorkspace(db_path=db_path, workspace=workspace)
        with self._db() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS capability_selections (
                    token TEXT PRIMARY KEY,item_id TEXT NOT NULL,snapshot TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS capability_effects (
                    id TEXT PRIMARY KEY,signature TEXT NOT NULL,response TEXT);
            ''')

    @contextlib.contextmanager
    def _db(self):
        with contextlib.closing(sqlite3.connect(self.db_path, timeout=20)) as c:
            with c:
                yield c

    def _once(self, action, payload, request_id, operation):
        if not isinstance(request_id, str) or not re.fullmatch('[0-9a-f]{32}', request_id):
            raise ValueError('유효한 요청 ID가 필요합니다.')
        signature = json.dumps([action, payload], ensure_ascii=False, sort_keys=True)
        with LOCK:
            with self._db() as c:
                c.execute('BEGIN IMMEDIATE')
                prior = c.execute('SELECT signature,response FROM capability_effects WHERE id=?', (request_id,)).fetchone()
                if prior:
                    if prior[0] != signature:
                        raise ValueError('같은 요청 ID로 다른 작업을 실행할 수 없습니다.')
                    if prior[1] is None:
                        raise ValueError('이전 실행 결과가 불명확합니다. 현재 상태를 먼저 확인하세요.')
                    result = json.loads(prior[1])
                    if result.get('_error'):
                        raise ValueError(result['_error'])
                    return result
                c.execute('INSERT INTO capability_effects VALUES (?,?,NULL)', (request_id, signature))
            try:
                result = operation()
            except Exception:
                # Never retry an operation whose effect may already have occurred.
                with self._db() as c:
                    c.execute('UPDATE capability_effects SET response=? WHERE id=?',
                              (json.dumps({'_error': '이전 실행을 완료하지 못했습니다. 현재 상태를 확인하고 새 요청을 보내세요.'}), request_id))
                raise
            with self._db() as c:
                c.execute('UPDATE capability_effects SET response=? WHERE id=?',
                          (json.dumps(result, ensure_ascii=False), request_id))
            return result

    def search(self, query='', kind='all', limit=50):
        result = copy.deepcopy(self.files.search(query=query, kind=kind, limit=limit))
        now = time.time()
        with self._db() as c:
            c.execute('DELETE FROM capability_selections WHERE created<?', (now - SELECTION_TTL,))
            for item in result.get('items', []):
                token = uuid.uuid4().hex
                c.execute('INSERT INTO capability_selections VALUES (?,?,?,?)',
                          (token, item['id'], json.dumps(item, ensure_ascii=False), now))
                item['selection_token'] = token
            # A bounded cache; removed tickets simply require a fresh search.
            c.execute('DELETE FROM capability_selections WHERE token NOT IN '
                      '(SELECT token FROM capability_selections ORDER BY created DESC LIMIT 2000)')
        return result

    def reveal(self, item_id, selection_token, request_id):
        if not isinstance(item_id, str) or not isinstance(selection_token, str):
            raise ValueError('검색 결과에서 파일을 다시 선택하세요.')
        def operation():
            with self._db() as c:
                row = c.execute('SELECT item_id,snapshot,created FROM capability_selections WHERE token=?',
                                (selection_token,)).fetchone()
            if not row or row[0] != item_id or row[2] < time.time() - SELECTION_TTL:
                raise ValueError('파일 선택이 만료되었거나 변경되었습니다. 다시 검색하세요.')
            # FileWorkspace rechecks source membership, identity and safe paths.
            return self.files.reveal(item_id, expected=json.loads(row[1]))
        return self._once('files_reveal', {'id': item_id, 'selection_token': selection_token}, request_id, operation)

    def preview(self, item_id, selection_token):
        if not isinstance(item_id,str) or not isinstance(selection_token,str):
            raise ValueError('검색 결과에서 파일을 다시 선택하세요.')
        with self._db() as c:
            row=c.execute('SELECT item_id,snapshot,created FROM capability_selections WHERE token=?',(selection_token,)).fetchone()
        if not row or row[0]!=item_id or row[2]<time.time()-SELECTION_TTL:
            raise ValueError('파일 선택이 만료되었거나 변경되었습니다. 다시 검색하세요.')
        import work_stage
        return work_stage.preview(self.db_path,self.files,json.loads(row[1]))

    def policy_snapshot(self):
        with organizer_policy.LOCK:
            saved = organizer_policy._read()
            state = organizer_policy.status()
            state['policy_scope'] = organizer_policy._scope(saved['policy']) if saved['policy'] else None
            state['job_scopes'] = copy.deepcopy(saved['jobs'])
            state['availability'] = 'connected' if saved['policy'] and saved['policy'].get('authorized') is True else 'needs_setup'
            return state

    def policy(self, action, payload=None, request_id=None, expected_scope=None, expected_job=None):
        payload = {} if payload is None else payload
        allowed = {'status': set(), 'preview': set(), 'run': set(), 'enabled': {'enabled'},
                   'undo': {'id'}, 'resume': {'id'}}
        if action not in allowed or not isinstance(payload, dict) or set(payload) - allowed[action]:
            raise ValueError('등록된 정리 작업만 실행할 수 있습니다.')
        if action == 'status':
            return self.policy_snapshot()
        def operation():
            with organizer_policy.LOCK:
                saved = organizer_policy._read()
                if action in {'undo', 'resume'}:
                    record = organizer_policy._record(saved, payload.get('id'))
                    if expected_job is not None and record != expected_job:
                        raise ValueError('정리 작업 범위가 변경되었습니다. 상태를 다시 조회하세요.')
                else:
                    policy = saved['policy']
                    if not policy or policy.get('authorized') is not True:
                        raise ValueError('정리할 폴더와 이동 허용 범위를 먼저 설정하세요.')
                    if expected_scope is not None and organizer_policy._scope(policy) != expected_scope:
                        raise ValueError('정리 범위가 변경되었습니다. 상태를 다시 조회하세요.')
                if action == 'preview':
                    result = organizer_policy.preview()
                elif action == 'run':
                    result = organizer_policy.run()
                elif action == 'enabled':
                    result = organizer_policy.set_enabled(payload.get('enabled'))
                elif action == 'undo':
                    result = organizer_policy.undo(payload.get('id'))
                else:
                    result = organizer_policy.resume(payload.get('id'))
                organizer_policy.ensure_worker()
                # Journal the result and its exact authority together. A replay
                # must not attach a newer policy's scope to the old display.
                snapshot = self.policy_snapshot()
                result = dict(result)
                result.update({key: snapshot[key] for key in ('policy_scope', 'job_scopes', 'availability')})
                return result
        # Older installed UI versions did not send request IDs. Each incoming
        # legacy request remains explicit; all new UI/chat clients supply one.
        return self._once('organizer_policy_' + action,
                          {'payload': payload, 'scope': expected_scope, 'job': expected_job},
                          request_id if request_id is not None else uuid.uuid4().hex, operation)

    def manifest(self, specs, category='all'):
        categories = {'all', 'files', 'organizer', 'work', 'launcher', 'browser', 'windows', 'messenger', 'manuals', 'desktop', 'settings', 'projects'}
        if category not in categories:
            raise ValueError('지원하는 기능 분류를 선택하세요.')
        items = []
        for name, spec in specs.items():
            if name == 'office_capabilities':
                continue
            prefix = name.split('_')[0]
            group = ('organizer' if prefix == 'organizer' else 'launcher' if prefix in {'apps', 'launcher', 'bookmarks', 'bookmark', 'streamdeck'}
                     else 'windows' if prefix in {'windows', 'settings'} else 'desktop' if prefix in {'desktop', 'collections'}
                     else 'files' if prefix in {'files', 'documents', 'scan'} else prefix)
            items.append({'name': name, 'category': group, 'description': spec[1][:80], 'effect': spec[0],
                          'status': 'connected', 'channels': ['chat']})
        click_connected = {'files_find', 'files_reveal', 'files_preview', 'apps_list', 'apps_find', 'launcher_list',
                           'launcher_add', 'launcher_add_web', 'launcher_edit', 'launcher_remove',
                           'launcher_reorder', 'launcher_launch', 'bookmarks_list', 'bookmark_add',
                           'work_list', 'work_create', 'work_status', 'work_note', 'work_resources', 'work_report',
                           'launcher_change_target',
                           'launcher_set_image', 'launcher_card_edit', 'scan_status', 'scan_results', 'scan_start'}
        click_connected.update(name for name in specs if name.startswith(('desktop_', 'collections_')))
        for name in click_connected:
            for item in items:
                if item['name'] == name:
                    item['channels'].append('click')
        try:
            ready = self.policy_snapshot()['availability']
        except (OSError, ValueError):
            ready = 'needs_setup'
        for item in items:
            if item['name'] in {'launcher_pages', 'launcher_page_create', 'launcher_move_page'}:
                item['legacy_only'] = True
            if item['category'] == 'organizer':
                item['channels'].append('click')
                if item['name'] != 'organizer_policy_status':
                    item['status'] = ready
        items.extend([
            {'name': 'messenger_receive_save_forward', 'category': 'messenger', 'effect': 'external',
             'description': '네이트온(NateOn)·HanbiroTalk 수신 파일 확인·보관·수신자 확인 후 전달',
             'status': 'unsupported', 'channels': [], 'reason': '메신저별 수신·파일·전송 어댑터가 아직 연결되지 않았습니다. 로그인 상태와 기능 연결은 별개입니다.',
             'observation': '일반 windows_list/inspect로 HanbiroTalk의 노출된 화면 텍스트를 읽을 수 있으나 실시간 수신 피드·파일 저장·전송과는 별개입니다. NateOn의 대화 내용 노출은 확인되지 않았습니다.'},
            {'name': 'browser_form_submit', 'category': 'browser', 'effect': 'external',
             'description': '웹 양식 입력·업로드·제출', 'status': 'unsupported', 'channels': [],
             'reason': '전용 브라우저의 일반 양식 입력·제출 어댑터 미연결'},
            {'name': 'manuals_execute', 'category': 'manuals', 'effect': 'external',
             'description': '저장된 반복 업무 매뉴얼을 검색하고 실행', 'status': 'unsupported', 'channels': [],
             'reason': '업무 매뉴얼 저장·버전·실행 연결은 설계 단계'},
        ])
        # Explicitly retain known click-only coverage instead of implying that
        # every UI action is already a callable assistant tool.
        for name, group, description in [
            ('organizer_policy_save', 'organizer', '새 정리 폴더·제외 규칙·허용 범위 설정'),
            ('launcher_manual_target', 'launcher', '파일 선택창으로 임의 새 실행 경로 선택·작은 아이콘 이미지 제거'),
            ('pc_scan', 'files', 'PC 자료 조사 시작·중복 조사'),
            ('project_archives', 'projects', '프로젝트 버전·압축 자료 비교'),
            ('model_settings', 'settings', '모델 연결·API·업무 폴더 설정'),
            ('app_update', 'settings', '앱 업데이트 확인·적용'),
        ]:
            items.append({'name': name, 'category': group, 'effect': 'write',
                          'description': description, 'status': 'unsupported', 'channels': ['click'],
                          'reason': '기존 화면 기능은 있으나 해당 전체 작업의 채팅 도구는 아직 연결되지 않았습니다.'})
        items.sort(key=lambda item: item['status'] == 'connected')
        return {'items': [i for i in items if category == 'all' or i['category'] == category],
                'execution': 'observe -> registered action -> verify',
                'all_features_connected': False,
                'availability_note': 'connected는 등록된 실행 경로이며 실제 환경·대상 지원은 실행 결과로 확인합니다. unsupported+click은 화면에만 연결된 기능입니다.',
                'file_time_note': 'modified는 파일 수정 시각이며 메신저 수신 시각이 아닙니다.'}
