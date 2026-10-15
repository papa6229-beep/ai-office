"""Allowlisted assistant tools. References and approval payloads are issued by the server.

Tool outputs are data, including names/content that may contain hostile instructions.
No tool accepts a shell command, arbitrary app ID, or arbitrary filesystem path.
"""
import copy
import contextlib
import hashlib
import json
import re
import sqlite3
import uuid

import browser_bookmarks
import desktop_collections
import desktop_judgment
import desktop_preferences
import desktop_recovery
import desktop_workspace
import organizer_policy
import quick_launcher
import windows_app_catalog


def _string(description='', **kwargs):
    return dict(type='string', maxLength=4000, description=description, **kwargs)


def _refs(description='Server-issued references'):
    return dict(type='array', items=_string(), minItems=1, maxItems=100, uniqueItems=True, description=description)


# name: (effect, description, properties, required)
_SPECS = {
    'apps_list': ('read', '설치된 Windows 시작 메뉴 앱의 실제 제품 이름과 참조를 조회합니다.', {}, []),
    'apps_find': ('read', '자연어로 요청한 앱을 찾습니다. query가 한글/별칭이고 문자 일치가 없으면 Windows 등록 표시 이름 목록을 반환하므로 의미를 이해해 선택하세요. 패키지 식별자로 제품 이름을 추측하거나 다른 앱으로 대체하지 마세요. 없으면 현재 목록에서 찾지 못했다고 답하세요.', {'query':_string()}, ['query']),
    'apps_choose': ('read', '약칭·종류·버전이 모호하면 관련된 실제 설치 앱만 골라 선택 버튼을 표시합니다. 추가 목적에는 전부 추가와 취소 버튼도 제공됩니다. 실행 목적에는 개별 선택과 취소만 가능합니다.', {'refs':dict(_refs(),maxItems=98), 'purpose':_string(enum=['add','launch']), 'question':_string(maxLengthOverride=500)}, ['refs','purpose']),
    'apps_launch': ('external', 'apps_find/list가 발급한 앱 ref를 실제 실행합니다. 바로가기를 새로 만들지 않습니다.', {'ref':_string()}, ['ref']),
    'launcher_list': ('read', '등록된 바로가기 목록과 ref를 조회합니다.', {}, []),
    'launcher_add': ('write', '설치 앱 또는 북마크/바탕화면 폴더 ref로 바로가기를 등록합니다. 같은 대상은 중복 등록하지 않습니다.', {'ref':_string(),'title':_string(maxLengthOverride=120)}, ['ref']),
    'launcher_add_web': ('write', '사용자가 지정한 HTTP/HTTPS 주소를 웹 바로가기로 등록합니다.', {'title':_string(),'url':_string()}, ['title','url']),
    'launcher_edit': ('write', '등록된 바로가기 표시 이름/문자 아이콘을 수정합니다.', {'ref':_string(),'title':_string(),'icon':_string()}, ['ref']),
    'launcher_remove': ('write', '바로가기 타일만 제거합니다. 프로그램이나 원본 파일은 삭제하지 않습니다.', {'ref':_string()}, ['ref']),
    'launcher_reorder': ('write', '모든 등록 바로가기 ref를 원하는 순서로 나열합니다.', {'refs':_refs()}, ['refs']),
    'launcher_launch': ('external', '이미 등록된 앱/웹/폴더 바로가기를 엽니다.', {'ref':_string()}, ['ref']),
    'bookmarks_list': ('read', '사용자가 북마크/웹사이트 사용을 요청한 경우 Chrome/Edge 북마크 이름과 URL을 읽기 전용으로 찾습니다.', {'query':_string()}, []),
    'bookmark_add': ('write', '조회한 북마크 ref로 바로가기를 추가합니다.', {'ref':_string()}, ['ref']),
    'bookmark_open': ('external', '조회한 북마크 ref의 HTTP/HTTPS 웹사이트를 엽니다.', {'ref':_string()}, ['ref']),
    'work_list': ('read', '업무 상태, 메모, 연결 자료, 보고 초안을 조회합니다.', {}, []),
    'work_create': ('write', '사용자가 요청한 업무를 기록합니다.', {'title':_string(),'detail':_string(),'due_date':_string()}, ['title']),
    'work_status': ('write', '선택한 업무 상태를 변경합니다.', {'ref':_string(),'status':_string(enum=['todo','doing','done'])}, ['ref','status']),
    'work_note': ('write', '선택한 업무의 진행 메모를 추가합니다.', {'ref':_string(),'text':_string()}, ['ref','text']),
    'work_resources': ('write', '현재 바탕화면에서 조회한 안전한 자료 ref를 업무와 연결합니다.', {'ref':_string(),'resource_ref':_string()}, ['ref','resource_ref']),
    'work_report': ('write', '오늘의 업무 기록으로 보고 초안을 작성합니다.', {}, []),
    'desktop_inventory': ('read', '현재 바탕화면의 실제 파일/폴더, 가상 작업함을 조회합니다. 다른 디스크 전체를 임의로 탐색하지 않습니다.', {'query':_string()}, []),
    'desktop_open': ('external', '조회한 바탕화면 항목 ref를 기본 앱으로 엽니다.', {'ref':_string()}, ['ref']),
    'desktop_reveal': ('external', '조회한 바탕화면 항목 ref의 위치를 탐색기로 표시합니다.', {'ref':_string()}, ['ref']),
    'desktop_pin': ('write', '바탕화면 항목의 즐겨찾기 표시를 변경합니다.', {'ref':_string(),'enabled':{'type':'boolean'}}, ['ref','enabled']),
    'collections_list': ('read', '파일을 이동하지 않는 가상 작업함 목록을 조회합니다.', {}, []),
    'collections_create': ('write', '자료 ref를 가상 작업함으로 묶습니다. 실제 폴더 생성이나 파일 이동은 없습니다.', {'title':_string(),'refs':_refs()}, ['title','refs']),
    'collections_rename': ('write', '가상 작업함 이름을 변경합니다.', {'ref':_string(),'title':_string()}, ['ref','title']),
    'collections_remove_member': ('write', '가상 작업함에서 항목을 뺍니다. 원본 파일은 유지됩니다.', {'ref':_string(),'item_ref':_string()}, ['ref','item_ref']),
    'collections_dissolve': ('write', '가상 작업함을 해제합니다. 원본 파일은 유지됩니다.', {'ref':_string()}, ['ref']),
    'desktop_auto_organize': ('write', '관련 자료를 가상 작업함으로 자동 분류합니다. 실제 파일은 이동하지 않습니다.', {}, []),
    'desktop_auto_undo': ('write', '최근 자동 가상 작업함 분류를 되돌립니다. 실제 파일 이동은 없습니다.', {}, []),
    'desktop_duplicates': ('read', '읽기 전용으로 동일 내용 중복 후보를 조사합니다. 삭제/이동은 하지 않습니다.', {}, []),
    'desktop_analyze_start': ('write', '설정된 Gemma로 바탕화면 내용/이미지 분석을 시작합니다. 완료 전에는 결과를 지어내지 마세요.', {'force':{'type':'boolean'}}, []),
    'desktop_analyze_stop': ('write', '진행 중인 내용 분석을 중지합니다.', {}, []),
    'desktop_analyze_status': ('read', '내용 분석의 진행 상태를 확인합니다.', {}, []),
    'desktop_analyze_results': ('read', '완료된 실제 내용 분석 결과와 보관 검토 후보 ref를 조회합니다.', {}, []),
    'desktop_retain': ('write', '선택한 분석 후보를 보존 대상으로 표시합니다.', {'refs':_refs()}, ['refs']),
    'desktop_hold_preview': ('approval', '분석 후보를 복구 보관함으로 이동할 고정 목록을 준비하고 사용자 승인 버튼을 표시합니다. 영구 삭제는 하지 않습니다.', {'refs':_refs()}, ['refs']),
    'desktop_recovery_list': ('read', '기존 복구 보관 작업과 복원 가능한 ref를 조회합니다.', {}, []),
    'desktop_restore_preview': ('approval', '선택한 복구 보관 작업을 원래 위치로 복원할 사용자 승인을 요청합니다.', {'ref':_string()}, ['ref']),
    'organizer_policy_status': ('read', '기존 실제 폴더 자동정리 정책의 설정/진행 상태를 조회합니다.', {}, []),
    'organizer_policy_preview': ('write', '정책의 실제 이동 후보를 읽기 전용으로 미리 봅니다. 채팅에서 정책 실행은 아직 지원하지 않습니다.', {}, []),
}
# Avoid a second source of truth for string length limits.
for _spec in _SPECS.values():
    for _field in _spec[2].values():
        if 'maxLengthOverride' in _field:
            _field['maxLength'] = _field.pop('maxLengthOverride')


def _check(value, schema):
    kind = schema['type']
    if kind == 'string':
        good = isinstance(value,str) and len(value) <= schema.get('maxLength',4000) and '\x00' not in value
    elif kind == 'boolean':
        good = isinstance(value,bool)
    elif kind == 'array':
        good = isinstance(value,list) and schema.get('minItems',0) <= len(value) <= schema.get('maxItems',100)
        if good:
            for item in value: _check(item,schema['items'])
            if schema.get('uniqueItems') and len(set(value)) != len(value): good=False
    else: good=False
    if not good or ('enum' in schema and value not in schema['enum']):
        raise ValueError('도구 요청 값이 올바르지 않습니다.')


def _ok(message, data=None, refresh=None):
    result={'status':'ok','message':message}
    if data is not None: result['data']=data
    if refresh: result['refresh']=refresh
    return result


class ToolRegistry:
    def __init__(self, db_path, work_factory=None, configure_ai=None):
        self.db_path=str(db_path)
        self.launcher=quick_launcher.QuickLauncher(db_path)
        self.work_factory=work_factory
        self.configure_ai=configure_ai
        with contextlib.closing(sqlite3.connect(self.db_path)) as c, c:
            c.execute('CREATE TABLE IF NOT EXISTS assistant_tool_effects (id TEXT PRIMARY KEY,signature TEXT NOT NULL,response TEXT)')

    def definitions(self):
        return [{'type':'function','function':{'name':name,'description':spec[1], 'parameters':{'type':'object','properties':copy.deepcopy(spec[2]),'required':spec[3][:],'additionalProperties':False}}} for name,spec in _SPECS.items()]

    def effect(self,name):
        if name not in _SPECS: raise ValueError('지원하지 않는 도구입니다.')
        return _SPECS[name][0]

    def _work(self):
        if self.work_factory: return self.work_factory()
        from work_assistant import WorkAssistant
        return WorkAssistant(self.db_path,lambda:{},None)

    def _ref(self,context,kind,value):
        row={'kind':kind,'value':copy.deepcopy(value)}
        refs=context.setdefault('refs',{})
        # Short handles are easier for the model to copy. Only the server-owned
        # run map grants meaning; saved legacy handles remain valid unchanged.
        for key,existing in refs.items():
            if existing==row: return key
        number=len(refs)+1
        key=kind+'_'+str(number)
        while key in refs:
            number+=1
            key=kind+'_'+str(number)
        refs[key]=row
        return key

    def _get(self,context,ref,kind):
        row=context.get('refs',{}).get(ref) if isinstance(ref,str) else None
        allowed={kind} if isinstance(kind,str) else set(kind)
        if not isinstance(row,dict) or row.get('kind') not in allowed:
            raise ValueError('먼저 목록을 조회하고 그 결과의 참조를 선택하세요.')
        return copy.deepcopy(row['value'])

    def _once(self,name,args,context,operation):
        key=context.get('step_id')
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):
            raise ValueError('유효한 작업 단계 ID가 필요합니다.')
        signature=json.dumps({'name':name,'args':args},ensure_ascii=False,sort_keys=True)
        # Reserve before any side effect. An interrupted side effect must not be retried blindly.
        with self.launcher._lock, contextlib.closing(sqlite3.connect(self.db_path,timeout=20)) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT signature,response FROM assistant_tool_effects WHERE id=?',(key,)).fetchone()
            if row:
                if row[0] != signature: raise ValueError('작업 단계가 다른 요청에 사용되었습니다.')
                if row[1] is None: raise ValueError('이전 작업의 완료 여부를 확인한 후 새 요청을 보내세요.')
                result=json.loads(row[1])
                if result.get('status')=='error': raise ValueError(result['message'])
                return result
            c.execute('INSERT INTO assistant_tool_effects VALUES (?,?,NULL)',(key,signature))
        try: result=operation()
        except Exception:
            with contextlib.closing(sqlite3.connect(self.db_path)) as c, c:
                c.execute('UPDATE assistant_tool_effects SET response=? WHERE id=?',(json.dumps({'status':'error','message':'작업을 완료하지 못했습니다. 현재 상태를 확인하고 다시 요청하세요.'}),key))
            raise
        with contextlib.closing(sqlite3.connect(self.db_path)) as c, c:
            c.execute('UPDATE assistant_tool_effects SET response=? WHERE id=?',(json.dumps(result,ensure_ascii=False),key))
        return result

    def execute(self,name,args,context):
        effect=self.effect(name)
        if not isinstance(args,dict) or set(args)-set(_SPECS[name][2]) or any(k not in args for k in _SPECS[name][3]):
            raise ValueError('지원하지 않는 도구 요청 항목입니다.')
        for k,v in args.items(): _check(v,_SPECS[name][2][k])
        if effect in {'write','external'}:
            return self._once(name,args,context,lambda:self._execute(name,args,context))
        return self._execute(name,args,context)

    def _apps(self,context,query=''):
        catalog=windows_app_catalog.catalog()
        rows=catalog.get('items',[])[:500]
        query=query.casefold().strip()
        matched=[r for r in rows if query and query in r['title'].casefold()]
        # Native model interprets Korean/colloquial product names against the actual inventory.
        # Registered display names remain authoritative; package IDs do not name products.
        chosen=matched if matched else rows
        cards=[{'ref':self._ref(context,'app',{'target':r['target'],'title':r['title']}),'title':r['title']} for r in chosen]
        return _ok('설치된 앱 '+str(len(cards))+'개를 확인했습니다.'+(' '+', '.join(r['title'] for r in cards[:5]) if matched else ''),
                   {'items':cards,'lexical_match':bool(matched),'available':catalog.get('available',False),'truncated':catalog.get('total',len(rows))>500,
                    'hint':'문자 일치가 없으면 Windows 등록 표시 이름 목록에서 의미로 판단하세요. 패키지 식별자로 제품 이름을 추측하거나 없는 제품을 다른 앱으로 대체하지 마세요. 없으면 현재 목록에서 찾지 못했다고 답하세요.'})

    def _launcher_cards(self,context):
        return [{'ref':self._ref(context,'launcher',r['id']),'title':r['title'],'kind':r['kind'],'available':r.get('available',True)} for r in self.launcher.overview()['items']]

    def _save(self,item,context):
        for old in self.launcher.overview()['items']:
            if old['kind']==item['kind'] and old['target']==item['target']:
                return _ok('이미 등록된 바로가기를 유지했습니다: '+old['title'],{'added':False},refresh=['launcher'])
        result=self.launcher.perform('save',dict(item,request_id=context['step_id']))
        return _ok(item['title']+' 바로가기를 저장했습니다.',{'added':True},refresh=['launcher'])

    def _execute(self,name,a,c):
        if name in {'apps_list','apps_find'}: return self._apps(c,a.get('query',''))
        if name=='apps_choose':
            options=[]
            for ref in a['refs']:
                app=self._get(c,ref,'app')
                options.append({'id':ref,'label':app['title']})
            names='\n'.join(o['label'] for o in options)
            if a['purpose']=='add': options.append({'id':'all','label':'전부 추가'})
            options.append({'id':'cancel','label':'취소'})
            question=a.get('question') or '어떤 프로그램을 말씀하셨나요?'
            description=question+'\n실제 설치된 후보:\n'+names
            return {'status':'pending','message':description,'pending':{'kind':'choice','title':'프로그램 선택','description':description,'options':options,'operation':'app_choice','args':{'purpose':a['purpose'],'refs':a['refs']}}}
        if name=='apps_launch':
            app=self._get(c,a['ref'],'app')
            windows_app_catalog.launch(app['target'])
            return _ok(app['title']+' 열기를 요청했습니다.')
        if name=='launcher_list':
            cards=self._launcher_cards(c)
            return _ok('바로가기 '+str(len(cards))+'개: '+(', '.join(r['title'] for r in cards[:15]) or '없음'),{'items':cards})
        if name in {'launcher_add','bookmark_add'}:
            item=self._get(c,a['ref'],{'app','bookmark','desktop'})
            kind=c['refs'][a['ref']]['kind']
            if kind=='desktop':
                live,_=desktop_workspace._selected(item)
                if live['kind']!='folder': raise ValueError('폴더 바로가기에는 폴더를 선택하세요.')
                item={'title':live['name'],'target':live['path'],'kind':'folder','icon':'📁'}
            else: item=dict(item,kind='app' if kind=='app' else 'web',icon='🚀' if kind=='app' else '🌐')
            if a.get('title'): item['title']=a['title']
            return self._save(item,c)
        if name=='launcher_add_web': return self._save({'title':a['title'],'kind':'web','target':a['url'],'icon':'🌐'},c)
        if name in {'launcher_edit','launcher_remove','launcher_launch','launcher_reorder'}:
            if name=='launcher_reorder':
                result=self.launcher.perform('reorder',{'request_id':c['step_id'],'ids':[self._get(c,r,'launcher') for r in a['refs']]})
            else:
                item_id=self._get(c,a['ref'],'launcher')
                current=next((i for i in self.launcher.overview()['items'] if i['id']==item_id),None)
                if not current: raise ValueError('바로가기를 다시 조회하세요.')
                if name=='launcher_edit':
                    item=next((i for i in self.launcher.overview()['items'] if i['id']==item_id),None)
                    if not item: raise ValueError('바로가기를 다시 조회하세요.')
                    payload={k:item[k] for k in ('title','kind','target','icon')}
                    payload.update({k:a[k] for k in ('title','icon') if k in a},id=item_id,request_id=c['step_id'])
                    result=self.launcher.perform('save',payload)
                else: result=self.launcher.perform('remove' if name=='launcher_remove' else 'launch',{'request_id':c['step_id'],'id':item_id})
            message=result['result']['message']
            if name!='launcher_reorder':
                message=(a.get('title',current['title']) if name=='launcher_edit' else current['title'])+': '+message
            return _ok(message,refresh=['launcher'])
        if name=='bookmarks_list':
            data=browser_bookmarks.catalog()
            query=a.get('query','').casefold()
            rows=[r for r in data['items'] if not query or query in (r['title']+' '+r['target']).casefold()][:500]
            cards=[{'ref':self._ref(c,'bookmark',{'title':r['title'],'target':r['target']}),'title':r['title'],'url':r['target'],'browser':r['browser'],'folder':r['folder']} for r in rows]
            return _ok('북마크 '+str(len(cards))+'개를 찾았습니다: '+(', '.join(r['title'] for r in cards[:10]) or '검색 결과 없음'),{'items':cards,'total':data['total'],'warnings':data['warnings']})
        if name=='bookmark_open':
            item=self._get(c,a['ref'],'bookmark')
            target=quick_launcher._target('web',item['target'])
            if not quick_launcher.webbrowser.open(target): raise ValueError('브라우저 열기를 요청하지 못했습니다.')
            return _ok(item['title']+' 열기를 요청했습니다.')
        if name=='work_list':
            data=self._work().overview()
            tasks=[]
            for task in data['tasks']:
                row={k:task.get(k) for k in ('title','detail','status','due_date','notes')}
                row['ref']=self._ref(c,'task',task['id'])
                row['selected']=task['id']==c.get('task_id')
                row['resources']=[{'name':r['name'],'kind':r['kind'],'exists':r['exists']} for r in task.get('resources',[])]
                tasks.append(row)
            return _ok('업무 '+str(len(tasks))+'개: '+(', '.join(r['title']+' ('+{'todo':'대기','doing':'진행 중','done':'완료'}.get(r['status'],str(r['status']))+')' for r in tasks[:15]) or '없음'),{'tasks':tasks,'counts':data['counts'],'report':data.get('report')})
        if name.startswith('work_'):
            actions={'work_create':'task','work_status':'status','work_note':'note','work_resources':'resource','work_report':'report'}
            payload={k:v for k,v in a.items() if k not in {'ref','resource_ref'}}
            if 'ref' in a: payload['task_id']=self._get(c,a['ref'],'task')
            if 'resource_ref' in a:
                item,_=desktop_workspace._selected(self._get(c,a['resource_ref'],'desktop'))
                payload['path']=item['path']
            result=self._work().perform(actions[name],dict(payload,request_id=c['step_id']))
            if name=='work_report':
                report=result.get('report') or {}
                return _ok(result['result']['message']+'\n\n'+report.get('body',''),{'report':report},refresh=['work'])
            return _ok(result['result']['message'],refresh=['work'])
        if name=='desktop_inventory':
            data=desktop_workspace.inventory()
            query=a.get('query','').casefold()
            rows=[r for r in data['items'] if not query or query in r['name'].casefold()]
            return _ok('바탕화면 자료 '+str(len(rows))+'개: '+(', '.join(r['name'] for r in rows[:10]) or '없음'),{'items':[self._desktop_card(c,r) for r in rows],'total':data['total'],'warnings':data['warnings']})
        if name in {'desktop_open','desktop_reveal','desktop_pin'}:
            item_id=self._get(c,a['ref'],'desktop')
            desktop_workspace._selected(item_id)
            if name=='desktop_pin':
                desktop_preferences.pin(item_id,a['enabled'])
                return _ok('즐겨찾기 표시를 변경했습니다.',refresh=['desk'])
            method=desktop_workspace.open_item if name=='desktop_open' else desktop_workspace.reveal_item
            result=method(item_id)
            return _ok('파일 열기를 요청했습니다.' if name=='desktop_open' else '위치 표시를 요청했습니다.',result)
        if name=='collections_list':
            data=desktop_collections.overview()
            rows=[{'ref':self._ref(c,'collection',r['id']),'title':r['label'],'items':[self._desktop_card(c,i) for i in r['items']]} for r in data['collections']]
            return _ok('가상 작업함 '+str(len(rows))+'개: '+(', '.join(r['title'] for r in rows[:10]) or '없음')+'. 실제 파일 위치는 유지됩니다.',{'collections':rows})
        if name in {'collections_create','collections_rename','collections_remove_member','collections_dissolve'}:
            if name=='collections_create': result=desktop_collections.create(a['title'],[self._get(c,r,'desktop') for r in a['refs']])
            else:
                collection=self._get(c,a['ref'],'collection')
                if name=='collections_rename': result=desktop_collections.rename(collection,a['title'])
                elif name=='collections_remove_member': result=desktop_collections.remove_item(collection,self._get(c,a['item_ref'],'desktop'))
                else: result=desktop_collections.dissolve(collection)
            return _ok('가상 작업함을 변경했습니다. 실제 파일 위치는 유지됩니다.',refresh=['desk'])
        if name in {'desktop_auto_organize','desktop_auto_undo'}:
            if name=='desktop_auto_organize':
                result=desktop_collections.auto_organize(True)
                count=result.get('automatic_created_count',0)
                message=('가상 작업함 '+str(count)+'개를 만들었습니다.' if count else '가상 작업함으로 새로 묶을 자료가 없습니다.')+' 실제 파일 위치는 유지됩니다.'
            else:
                desktop_collections.undo_auto()
                message='가상 작업함 자동 분류를 되돌렸습니다.'
            return _ok(message,refresh=['desk'])
        if name=='desktop_duplicates':
            data=desktop_collections.duplicate_report()
            groups=[{'items':[self._desktop_card(c,i) for i in g['items']], 'duplicate_bytes':g['duplicate_bytes'], 'reason':g['reason']} for g in data['groups']]
            return _ok('중복 후보 '+str(len(groups))+'묶음을 확인했습니다. 삭제나 이동은 하지 않았습니다.',{'groups':groups,'warnings':data['warnings'],'checked':data['checked']})
        if name=='desktop_analyze_start':
            if self.configure_ai: self.configure_ai()
            state=desktop_judgment.start(force=a.get('force',False))
            if state.get('phase')=='error' or state.get('last_error'):
                raise ValueError(state.get('last_error') or '내용 분석을 시작하지 못했습니다.')
            return _ok('내용 분석 시작을 요청했습니다. 진행 상태를 확인할 수 있습니다.',refresh=['desk'])
        if name=='desktop_analyze_stop':
            desktop_judgment.stop()
            return _ok('내용 분석 중지를 요청했습니다.',refresh=['desk'])
        if name in {'desktop_analyze_status','desktop_analyze_results'}:
            state=desktop_judgment.status()
            data={k:state.get(k) for k in ('running','enabled','analyzed','empty_count','image_count','deferred_count','updated','last_error','phase','message','total','processed','grouping_processed','grouping_total')}
            if name=='desktop_analyze_results':
                data['items']=[self._analysis_card(c,r,state.get('revision')) for r in state.get('items',[])]
                data['candidates']=[self._analysis_card(c,r,state.get('revision')) for r in state.get('candidates',[])]
                data['groups']=state.get('groups',[])
            return _ok(('분석 진행 중 '+str(state.get('processed',0))+'/'+str(state.get('total',0)) if state.get('running') else '분석 대기/완료')+' · 내용 확인 '+str(state.get('analyzed',0))+'개 · 보관 검토 후보 '+str(len(state.get('candidates',[])))+'개.'+((' 오류: '+state['last_error']) if state.get('last_error') else ''),data)
        if name=='desktop_retain':
            desktop_judgment.retain([self._get(c,r,'candidate')['id'] for r in a['refs']])
            return _ok('선택한 자료를 보존 대상으로 표시했습니다.',refresh=['desk'])
        if name=='desktop_hold_preview':
            rows=[self._get(c,r,'candidate') for r in a['refs']]
            revisions={r['revision'] for r in rows}
            state=desktop_judgment.status()
            ids=[r['id'] for r in rows]
            if revisions != {state.get('revision')} or not set(ids)<=set(r['id'] for r in state.get('candidates',[])):
                raise ValueError('후보가 변경되었습니다. 최신 결과를 다시 확인하세요.')
            eligible=desktop_recovery.holdability(ids)
            if any(not eligible.get(i,{}).get('allowed') for i in ids):
                raise ValueError('보존해야 하거나 현재 보관할 수 없는 파일이 포함되어 있습니다.')
            names=[r['name'] for r in rows]
            return {'status':'pending','message':'복구 보관할 파일을 확인하고 승인하세요. 아직 파일을 이동하지 않았습니다.', 'pending':{'kind':'approval','title':'복구 보관 승인','description':'다음 파일을 바탕화면의 복구 보관함으로 이동합니다. 영구 삭제하지 않으며 실행 기록에서 복원할 수 있습니다.\n'+'\n'.join(names),'options':[{'id':'approve','label':str(len(rows))+'개 복구 보관'},{'id':'cancel','label':'취소'}],'operation':'hold','args':{'revision':state['revision'],'candidate_ids':ids}}}
        if name=='desktop_recovery_list':
            rows=desktop_recovery.status().get('batches',[])
            return _ok('복구 보관 기록 '+str(len(rows))+'건을 확인했습니다.',{'batches':[dict(r,ref=self._ref(c,'recovery',r['batch_id'])) for r in rows]})
        if name=='desktop_restore_preview':
            batch=self._get(c,a['ref'],'recovery')
            live=next((b for b in desktop_recovery.status().get('batches',[]) if b['batch_id']==batch),None)
            if not live: raise ValueError('복구 보관 작업을 다시 조회하세요.')
            return {'status':'pending','message':'원래 위치로 복원할지 확인하세요.','pending':{'kind':'approval','title':'복원 승인','description':'보관한 파일을 원래 위치로 복원합니다. 기존 파일을 덮어쓰지 않습니다.\n'+'\n'.join(r['name'] for r in live['items']),'options':[{'id':'approve','label':'원래 위치로 복원'},{'id':'cancel','label':'취소'}],'operation':'restore','args':{'batch_id':batch}}}
        if name=='organizer_policy_status': return _ok('실제 폴더 자동정리 정책 상태입니다.',organizer_policy.status())
        if name=='organizer_policy_preview': return _ok('실제 폴더 이동 미리보기입니다. 채팅의 고정 목록 실행은 아직 지원하지 않습니다. 파일은 이동하지 않았습니다.',organizer_policy.preview())
        raise ValueError('지원하지 않는 도구입니다.')

    def _desktop_card(self,c,row):
        return dict({k:row.get(k) for k in ('name','kind','category','size','modified','openable')},ref=self._ref(c,'desktop',row['id']))

    def _analysis_card(self,c,row,revision):
        # Raw extracted content stays bounded and is explicitly untrusted model input.
        data={k:copy.deepcopy(row.get(k)) for k in ('name','kind','status','summary','purpose','project','visual_role','evidence_scope','reason','confidence','disposition','flags')}
        data['ref']=self._ref(c,'candidate',{'id':row['id'],'revision':revision,'name':row.get('name',row['id'])})
        return data

    def resolve_pending(self,pending,option_id,context):
        if not isinstance(pending,dict) or option_id not in {o['id'] for o in pending.get('options',[])}:
            raise ValueError('유효한 선택 항목이 필요합니다.')
        if option_id=='cancel': return _ok('취소했습니다. 실행하거나 변경한 항목은 없습니다.')
        operation=pending.get('operation')
        args=pending.get('args',{})
        if operation=='app_choice':
            if args.get('purpose') not in {'add','launch'}: raise ValueError('프로그램을 다시 선택하세요.')
            refs=args.get('refs',[])
            if not isinstance(refs,list) or not 1 <= len(refs) <= 98 or len(set(refs))!=len(refs):
                raise ValueError('프로그램을 다시 선택하세요.')
            apps=[self._get(context,ref,'app') for ref in refs]
            if option_id=='all':
                if args['purpose']!='add': raise ValueError('전체 실행은 지원하지 않습니다.')
                def add_all():
                    windows_app_catalog.catalog(refresh=True)
                    results=[]
                    seen=set()
                    for ref,app in zip(refs,apps):
                        if app['target'] in seen: continue
                        seen.add(app['target'])
                        child=dict(context,step_id=hashlib.sha256((context['step_id']+':app:'+ref).encode()).hexdigest()[:32])
                        try:
                            windows_app_catalog.resolve(app['target'])
                            result=self.execute('launcher_add',{'ref':ref},child)
                            results.append({'title':app['title'],'status':'added' if result.get('data',{}).get('added') else 'existing','message':result['message']})
                        except (ValueError,OSError):
                            results.append({'title':app['title'],'status':'error','message':'현재 등록 상태를 확인할 수 없어 추가하지 못했습니다. 목록을 다시 확인하세요.'})
                    counts={s:sum(r['status']==s for r in results) for s in ('added','existing','error')}
                    message='바로가기 처리 결과: '+str(counts['added'])+'개 추가, '+str(counts['existing'])+'개 기존 유지, '+str(counts['error'])+'개 실패.'
                    return _ok(message,{'items':results,'counts':counts},refresh=['launcher'])
                return self._once('app_choice_all',args,context,add_all)
            if option_id not in args.get('refs',[]): raise ValueError('프로그램을 다시 선택하세요.')
            windows_app_catalog.resolve(self._get(context,option_id,'app')['target'],refresh=True)
            name='launcher_add' if args['purpose']=='add' else 'apps_launch'
            return self.execute(name,{'ref':option_id},context)
        if operation not in {'hold','restore'} or option_id!='approve': raise ValueError('승인 항목을 다시 확인하세요.')
        def apply():
            if operation=='hold':
                if set(args)!={'revision','candidate_ids'}: raise ValueError('승인 항목이 올바르지 않습니다.')
                data=desktop_recovery.hold(args['revision'],args['candidate_ids'],context['step_id'])
                message='복구 보관 결과: '+str(data.get('held_count',0))+'개 보관, '+str(len(data.get('exceptions',[])))+'개 예외.'
            else:
                if set(args)!={'batch_id'}: raise ValueError('승인 항목이 올바르지 않습니다.')
                data=desktop_recovery.restore(args['batch_id'])
                message='원래 위치 복원 결과: '+str(data.get('restored_count',0))+'개 복원, '+str(len(data.get('exceptions',[])))+'개 예외.'
            return _ok(message,data,refresh=['desk'])
        return self._once('approved_'+operation,args,context,apply)
