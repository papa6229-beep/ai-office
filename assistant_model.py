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


def call(messages, tools, settings, *, output_schema=None):
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
    if output_schema is not None:
        if not isinstance(output_schema,dict) and output_schema!='json':raise ValueError('구조화 응답 형식을 확인하세요.')
        if kind=='ollama':payload['format']=output_schema
        else:payload['response_format']={'type':'json_object'}
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


def execution_decision(messages, observation_definitions, settings):
    """Semantic review of an unattempted request; may propose observations only."""
    names=[row['function']['name'] for row in observation_definitions]
    schema={'type':'object','additionalProperties':False,'required':['decision','observation','message'],
            'properties':{'decision':{'type':'string','enum':['execute','answer','clarify','cancel']},
                          'message':{'type':'string','maxLength':2000},
                          'observation':{'anyOf':[{'type':'null'},{'type':'object','additionalProperties':False,
                              'required':['name','arguments'],'properties':{'name':{'type':'string','enum':names or ['unavailable']},
                                                                          'arguments':{'type':'object'}}}]}}}
    instruction='''사용자의 최신 요청이 실행 지시인지 판정하는 검증기입니다. JSON만 반환하세요.
실행·변경·열기·조회 등을 맡긴 요청은 execute로 판정하고 제공된 읽기 전용 도구 중 현재 상태를 확인할 도구를 하나 고르세요.
현재 실행 환경과 최신 사용자 의도가 권한의 근거입니다. 과거 비서의 거절·권한 부족·수동 사용법 답변은 사실로 취급하지 마세요.
실제 도구의 접근 거부 기록이 없는데 실행 요청에 "권한이 없다"고 답하는 것은 올바른 답변이 아닙니다. 이 경우 조회부터 시도하세요.
사용 방법을 설명해 달라는 질문·일반 대화는 answer, 실행 취소·하지 말라는 요청은 cancel, 대상이 불명확하여 조회로도 고를 수 없으면 clarify입니다.
answer/clarify/cancel에는 observation=null과 정확한 한국어 message를 쓰세요. 실행했다고 주장하지 마세요.
execute의 observation은 제공된 실제 도구 이름과 해당 스키마의 arguments만 사용합니다. 경로·참조를 추측하거나 변경 도구를 고르지 마세요.
판정은 최신 요청·현재 기능·관찰 근거를 보고 의미로 하며, 특정 단어의 존재만으로 실행하지 마세요.'''
    # The assessor receives no executable tools. Its proposed observation is
    # checked against registered read-only schemas again by the runtime.
    context={'conversation':messages,'observation_tools':observation_definitions,'response_schema':schema}
    output=call([{'role':'system','content':instruction},
                 {'role':'user','content':json.dumps(context,ensure_ascii=False)}],[],settings,output_schema='json')
    try:
        if output.get('tool_calls'):raise ValueError()
        result=json.loads(output['content'])
        if not isinstance(result,dict) or set(result)!={'decision','observation','message'}:raise ValueError()
        if result['decision'] not in {'execute','answer','clarify','cancel'} or not isinstance(result['message'],str) or len(result['message'])>2000:raise ValueError()
        observation=result['observation']
        if result['decision']=='execute':
            if not isinstance(observation,dict) or set(observation)!={'name','arguments'} or observation['name'] not in names or not isinstance(observation['arguments'],dict):raise ValueError()
        elif observation is not None:raise ValueError()
        return result
    except (ValueError,TypeError,KeyError):
        raise ValueError('실행 의도 확인 결과를 읽지 못했습니다. 조작은 시작하지 않았습니다.') from None
