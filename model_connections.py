"""Manual provider profiles and read-only readiness; never performs inference."""
import copy
import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import urllib.request

from local_content_review import validate_endpoint

LOCK = threading.RLock()
_observations = {}
PROFILE_FIELDS = ('api_type', 'gemma_url', 'gemma_model', 'vision_model',
                  'api_key', 'timeout', 'lmstudio_auto_start')
CATALOG = (
    ('codex', 'Codex', 'ChatGPT 구독', True),
    ('local', 'Gemma · 로컬 모델', '내 PC / 설정한 모델 서버', True),
    ('gemini', 'Gemini', 'Google 계정 · Gemini CLI', True),
    ('claude', 'Claude', 'Claude 계정 · Claude Code CLI', True),
)


def active_id(settings):
    return settings['api_type'] if settings.get('api_type') in {'codex','gemini','claude'} else 'local'


def require_runnable(settings):
    provider = settings.get('model_provider', active_id(settings))
    if provider not in {'codex', 'local','gemini','claude'} or provider != active_id(settings):
        raise ValueError('선택한 모델의 오피스 연결이 준비되지 않았어요. 연결 설정을 확인해줘.')
    if settings.get('api_type') not in {'codex', 'openai', 'ollama','gemini','claude'}:
        raise ValueError('지원하지 않는 모델 연결 방식이에요.')


def profiles(settings):
    result = {
        'codex': dict(api_type='codex', gemma_url='', gemma_model='gpt-6-luna',
                      vision_model='gpt-6-luna', api_key='', timeout=60, lmstudio_auto_start=False),
        'local': dict(api_type='openai', gemma_url='http://127.0.0.1:1234/v1',
                      gemma_model='google/gemma-4-e4b', vision_model='google/gemma-4-e4b',
                      api_key='', timeout=120, lmstudio_auto_start=True),
    }
    for provider,model in [('gemini',''),('claude','haiku')]:
        result[provider]=dict(api_type=provider,gemma_url='',gemma_model=model,vision_model='',api_key='',timeout=120,lmstudio_auto_start=False)
    saved = settings.get('model_profiles', {})
    if isinstance(saved, dict):
        for key in result:
            if isinstance(saved.get(key), dict):
                result[key].update({field: saved[key][field] for field in PROFILE_FIELDS if field in saved[key]})
    # The active legacy settings always take precedence over the stored profile.
    result[active_id(settings)].update({key: settings[key] for key in PROFILE_FIELDS if key in settings})
    return result


def public_settings(settings):
    result = {key: value for key, value in settings.items() if key != 'model_profiles'}
    result['api_key'] = '(설정됨)' if settings.get('api_key') else ''
    return result


def _busy(db):
    import model_requests
    if not model_requests.is_idle():
        return True
    with sqlite3.connect(str(db), timeout=5) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_requests'").fetchone():
            return False
        return bool(connection.execute("SELECT 1 FROM assistant_requests WHERE status='running' LIMIT 1").fetchone())


def save_settings(path, db, current, changes=None, *, provider=None):
    """Caller holds LOCK while reading current settings and writing profiles."""
    if _busy(db):
        raise ValueError('에이메스가 작업 중이야. 작업이 끝난 뒤 연결을 바꿔줘.')
    changes = dict(changes or {})
    if provider is not None and provider not in {'codex', 'local','gemini','claude'}:
        raise ValueError('이 연결은 오피스 실행 어댑터가 아직 준비되지 않았어요.')
    saved = profiles(current)
    target = provider or active_id(dict(current,**changes))
    result = copy.deepcopy(current)
    result.update(saved[target])
    result.update(changes)
    result['model_provider'] = target
    require_runnable(result)
    if target == 'codex':
        result.update(gemma_model='gpt-6-luna', vision_model='gpt-6-luna',
                      api_key='', lmstudio_auto_start=False)
    elif target in {'gemini','claude'}:
        import provider_cli
        provider_cli.command(target)
        if not isinstance(result.get('gemma_model'),str) or len(result['gemma_model'])>160:
            raise ValueError('사용할 모델 이름을 확인해줘.')
        result.update(api_key='',gemma_url='',vision_model='',lmstudio_auto_start=False)
    else:
        validate_endpoint(result['gemma_url'])
        if not isinstance(result.get('gemma_model'), str) or not result['gemma_model'].strip():
            raise ValueError('사용할 모델명을 입력해줘.')
    try:
        timeout = int(result['timeout'])
    except (ValueError, TypeError):
        raise ValueError('요청 대기시간은 5~600초로 입력해줘.') from None
    if not 5 <= timeout <= 600:
        raise ValueError('요청 대기시간은 5~600초로 입력해줘.')
    if type(result.get('lmstudio_auto_start')) is not bool or not isinstance(result.get('api_key'), str):
        raise ValueError('연결 설정 값을 확인해줘.')
    result.update(timeout=timeout, ai_on_demand_only=True)
    saved[target] = {key: result[key] for key in PROFILE_FIELDS}
    result['model_profiles'] = saved
    descriptor, temporary = tempfile.mkstemp(prefix='.model-settings-', dir=str(Path(path).parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    _observations.clear()
    return result


def _cli(name):
    import provider_cli
    try:return Path(provider_cli.command(name)[-1])
    except ValueError:return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def inspect_readiness(settings):
    """Explicit refresh only. No login, auto-load, tool execution or model calls."""
    with LOCK:
        stamp = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        try:
            import codex_bridge
            codex_bridge._cli()
            codex = '실행 파일 확인 · 로그인·잔여 한도는 별도 확인'
        except (OSError, ValueError):
            codex = 'Codex 실행 파일을 찾지 못했어요'
        observations = {'codex': {'text': codex}}
        local = profiles(settings)['local']
        try:
            base = validate_endpoint(local['gemma_url'])
            request = urllib.request.Request(base + ('/api/tags' if local['api_type'] == 'ollama' else '/models'))
            if local.get('api_key'):
                request.add_header('Authorization', 'Bearer ' + local['api_key'])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=3) as response:
                raw = response.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError()
            body = json.loads(raw)
            rows = body.get('models' if local['api_type'] == 'ollama' else 'data')
            if not isinstance(rows, list):
                raise ValueError()
            models = [str(row.get('name') or row.get('id') or '')[:160] for row in rows[:50] if isinstance(row, dict)]
            listed = local['gemma_model'] in models
            observations['local'] = {'text': '서버 응답 · 선택 모델 목록 확인' if listed else '서버 응답 · 선택 모델은 목록에 없음',
                                     'models': models, 'model_listed': listed}
        except (OSError, ValueError, TypeError):
            observations['local'] = {'text': '모델 서버 응답을 확인하지 못했어요', 'models': []}
        import provider_cli
        for key in ('gemini','claude'):
            observations[key]=provider_cli.readiness(key)
        _observations.clear()
        _observations.update(observations=observations, checked_at=stamp)
        return snapshot(settings)


def connection_facts(settings):
    native = active_id(settings) == 'codex'
    return {'provider': active_id(settings), 'native_agent': native,
            'transport': 'codex_app_server' if native else settings.get('api_type'),
            'automatic_fallback': False, 'inference_verified_by_readiness': False,
            'limits': [] if native else [
                'This provider uses the audited office function-tool loop rather than the Codex native runner.',
                'The same notification, work, browser, report and artifact tools remain available. Use workspace_list/read/write/run/image and agent_source_publish/output_publish for code and files.',
                'Local Python execution uses the current Windows user permissions, not an OS filesystem sandbox. Use it only for the current authorized task, never to bypass the office executor or browser control policy.',
                'Tool calling, image understanding and execution quality depend on the selected model and require actual checks.']}


def snapshot(settings):
    with LOCK:
        observed = _observations.get('observations', {})
        active = active_id(settings)
        cards = []
        for key, title, billing, runnable in CATALOG:
            native = key == 'codex'
            capabilities = [
                ['대화', '연결됨' if runnable else '어댑터 연결 필요'],
                ['오피스·브라우저·PC 도구', ('공통 도구 연결됨' if native else '공통 도구 경로 있음 · 모델 동작 미확인') if runnable else '공통 도구 연결 필요'],
                ['알림·업무·보고서', '같은 오피스 도구·기록 사용'],
                ['파일 수정·코드 실행', '공통 작업 사본·수정·실행·게시 연결됨'],
                ['이미지 이해', '이미지 입력 연결됨 · 실제 모델 지원 확인 필요' if key!='local' else '이미지 경로 연결됨 · 선택한 로컬 모델 지원 필요'],
            ]
            cards.append({'id': key, 'title': title, 'billing': billing,
                          'active': key == active, 'selectable': runnable,
                          'readiness': observed.get(key, {'text': '준비 상태 확인 전'}),
                          'capabilities': capabilities,
                          'login_available':key in {'gemini','claude'},
                          'note': ('낮은 추론 · Standard · 요청할 때만 호출' if native else
                                   '모델 목록 확인은 로드·응답·도구 성공 확인과 달라요.' if key == 'local' else
                                   '구독 CLI · 에이메스 공통 도구·이미지·작업 파일 연결됨 · 실제 모델의 수행 능력은 확인 필요')})
        return {'active': active, 'providers': cards, 'checked_at': _observations.get('checked_at'),
                'uses_ai': False, 'automatic_fallback': False,
                'local_model': profiles(settings)['local']['gemma_model']}
