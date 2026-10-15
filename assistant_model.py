"""Native tool messages for configured local or compatible model servers."""
import copy
import json
import re
import urllib.error
import urllib.request
import uuid

from local_content_review import validate_endpoint

_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_ARGUMENTS = 24000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _messages(messages, kind):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 80:
        raise ValueError('비서 대화의 크기를 확인하세요.')
    result = copy.deepcopy(messages)
    for message in result:
        if not isinstance(message, dict) or message.get('role') not in {'system', 'user', 'assistant', 'tool'}:
            raise ValueError('비서 대화 형식을 확인하세요.')
        content = message.get('content', '')
        if content is not None and (not isinstance(content, str) or len(content) > 120000):
            raise ValueError('비서 대화의 크기를 확인하세요.')
        for call in message.get('tool_calls', []):
            function = call.get('function', {})
            arguments = function.get('arguments', {})
            if kind == 'openai':
                function['arguments'] = json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else arguments
                call.setdefault('type', 'function')
            elif isinstance(arguments, str):
                function['arguments'] = json.loads(arguments)
        if message['role'] == 'tool':
            if kind == 'openai':
                message.pop('tool_name', None)
            else:
                message.pop('tool_call_id', None)
    return result


def call(messages, tools, settings):
    """Return normalized tool calls; this function never executes a tool."""
    try:
        base = validate_endpoint(settings.get('gemma_url', ''))
    except (ValueError, TypeError):
        raise ValueError('설정된 AI 서버 주소를 확인하세요.') from None
    kind = settings.get('api_type')
    if kind not in {'ollama', 'openai'}:
        raise ValueError('지원하는 AI 서버 형식을 선택하세요.')
    if not isinstance(tools, list) or len(tools) > 80:
        raise ValueError('비서 도구 목록을 확인하세요.')
    payload = {'model': settings.get('gemma_model'), 'messages': _messages(messages, kind),
               'tools': tools, 'stream': False}
    if kind == 'ollama':
        payload.update(think=False, options={'temperature': 0.1, 'num_predict': 2048})
        route = '/api/chat'
    else:
        payload.update(temperature=0.1, max_tokens=2048)
        route = '/chat/completions'
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(raw) > 768000:
        raise ValueError('비서 대화와 도구 목록이 너무 큽니다.')
    headers = {'Content-Type': 'application/json'}
    if settings.get('api_key'):
        headers['Authorization'] = 'Bearer ' + settings['api_key']
    request = urllib.request.Request(base + route, data=raw, headers=headers, method='POST')
    try:
        timeout = max(1, min(90, int(settings.get('timeout', 60))))
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            body = response.read(_MAX_RESPONSE + 1)
        if len(body) > _MAX_RESPONSE:
            raise ValueError()
        data = json.loads(body)
        message = data['message'] if kind == 'ollama' else data['choices'][0]['message']
        if not isinstance(message, dict):
            raise ValueError()
        content = message.get('content') or ''
        if not isinstance(content, str) or len(content) > 32000:
            raise ValueError()
        calls = message.get('tool_calls') or []
        if not isinstance(calls, list) or len(calls) > 12:
            raise ValueError()
        normalized = []
        for item in calls:
            function = item['function']
            name, arguments = function['name'], function.get('arguments', {})
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name):
                raise ValueError()
            if isinstance(arguments, str):
                if len(arguments) > _MAX_ARGUMENTS:
                    raise ValueError()
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict) or len(json.dumps(arguments)) > _MAX_ARGUMENTS:
                raise ValueError()
            call_id = item.get('id')
            if not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', call_id):
                call_id = 'call_' + uuid.uuid4().hex
            normalized.append({'id': call_id, 'name': name, 'arguments': arguments})
        return {'content': content, 'tool_calls': normalized}
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError, urllib.error.URLError):
        raise ValueError('AI 서버의 도구 응답을 확인하지 못했습니다. 실행 결과를 확인한 후 다시 요청하세요.') from None
