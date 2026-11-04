"""Small registered routines. Full-request matching prevents partial execution."""
import re
import datetime

RUNTIME_REVISION='2026-11-04-nateon-file-send-v7'

CATALOG=[
    {'id':'nateon_file_send','example':'/네이트온전송 C:\\업무\\완료.xlsx | 김우영,오상수,이준민,전수아 (5명)','description':'원본 경로와 정확한 방 이름을 지정한 파일 한 개를 전용 기기로 보내고 새 발신 기록을 확인'},
    {'id': 'detail_materials', 'example': '네이트온 받은 파일에 자료가 있을거야 상세페이지 제작해줘', 'description': '수신 자료 후보를 즉시 안내하고 상품 선택을 질문. 선택 전 제작하지 않음'},
    {'id':'detail_demo','example':'상세페이지 시연해줘','description':'준비된 후이저우 시연을 Ego로 자료 준비·빈 상품·로그인·JSON 가져오기·이미지/썸네일 저장까지 코드 실행'},
    {'id':'reports_all','example':'일일보고서랑 AI보고서 작성해줘','description':'두 등록 보고서를 순서대로 코드 실행하며 모델 호출 없이 각 저장·검수를 확인'},
    {'id':'material_search','example':'자료 검색: 용훈 수정 /자료검색 샘플가격','description':'확인된 받은/보낸 자료를 여러 단어로 통합 검색. 실제 경로와 상세 연결 조회용ID 반환'},
    {'id':'work_log_query','example':'지난 1주일간 혜연이한테 받은 파일 리스트 알려줘 / 용훈이에게 최근 보낸 파일10개 보여줘','description':'같은 작업일지를 기간·상대방·종류·개수로 코드 조회. 문서 작성과 직접 메모도 지원'},
    {'id':'file_history_latest','example':'가장 최근에 용훈이에게 보낸 파일이 뭔지 알려주고 위치 열어줘','description':'확인된 수발신 이력에서 상대방의 최신 파일 조회·요청한 경우 탐색기 위치 열기'},
    {'id':'small_talk','example':'안녕~ / 고마워','description':'단독 인사·감사에 모델 호출 없이 짧게 응답'},
    {'id':'daily_report','example':'보고서 작성하자','description':'최초 보고서 저장→A/B/C군 이미지·가격·재고·담당MD 검수→완료목록 최종 보고서 저장·예외 보고까지 코드 실행'},
    {'id':'daily_products','example':'보고서 상품처리해줘','description':'A/B/C군 이미지·가격·재고·담당MD 검수와 정상 완료 표시 및 보고서 완료목록 기록을 코드 실행'},
    {'id':'work_list','example':'오늘 업무 목록 보여줘','description':'오피스 업무 기록 조회'},
    {'id':'notifications','example':'미확인 알림 보여줘','description':'오피스가 수신한 알림 목록 조회'},
    {'id':'files_find','example':'파일명 검색: 견적서','description':'기존 등록 범위에서 파일명 검색'},
    {'id':'report_draft','example':'오피스 기록으로 오늘 보고서 초안 만들어줘','description':'오피스 기록 기반 초안 저장. 외부 사이트 제출 아님'},
    {'id':'file_jobs','example':'파일 정리 작업 목록 보여줘','description':'기록된 정리 작업의 상태 조회'},
    {'id':'file_run','example':'/정리실행 작업ID','description':'지정한 기존 정리 계획을 코드로 실행/재개'},
    {'id':'file_status','example':'/정리상태 작업ID','description':'지정한 정리 작업의 항목별 진행 조회'},
]


def match(message):
    text=message.strip().rstrip('.!~').strip()
    send=re.fullmatch(r'/네이트온전송[ \t]+([^|\r\n]{1,600})\|([^|\r\n]{1,160})',text)
    if send:
        path,room=(part.strip() for part in send.groups())
        if path.startswith('"') and path.endswith('"'):path=path[1:-1]
        if re.fullmatch(r'[A-Za-z]:[\\/][^"|\r\n]+',path) and room:
            return {'id':'nateon_file_send','args':{'path':path,'room':room}}
    # Only the observed, non-command preface is optional. The entire remaining
    # request must still match; arbitrary leading instructions are not dropped.
    preface=re.fullmatch(r'즉발식으로\s*가능한가[?？]?\s*다시\s*테스트할거야[.!。\n]+\s*([\s\S]+)',text)
    if preface:
        route=match(preface[1])
        return route if route and route['id'] in {'reports_all','daily_report','ai_report'} else None
    # This route only lists candidates. It never imports or exports a product.
    if re.fullmatch(r'(?:일단\s*|그럼\s*)?네이트온[^\n]{0,180}?상세\s*페이지\s*(?:제작(?:해줘|해주세요)|만들어(?:줘|주세요))',text):
        return {'id':'detail_materials','args':{}}
    material=re.fullmatch(r'(?:/자료검색\s+|자료\s*검색\s*[:：]\s*)(.{1,200})',text,re.S)
    if material:return {'id':'material_search','args':{'query':material[1]}}
    if re.fullmatch(r'(?:/상세페이지시연|(?:후이저우\s*)?상세\s*페이지\s*(?:제작\s*)?(?:시연|데모|시연\s*테스트)\s*(?:해줘|하자|해보자|시작해줘|진행해줘|실행해줘))',text):
        return {'id':'detail_demo','args':{'phase':'all'}}
    import work_log
    log=work_log.match_command(text)
    if log:return log
    import file_history
    history=file_history.match_command(text)
    if history:return {'id':'file_history_latest','args':history}
    if re.fullmatch(r'(?:에이메스[야,~! ]*\s*)?(?:안녕(?:하세요)?|좋은\s*아침)\??',text):
        return {'id':'small_talk','args':{'reply':'안녕~ 오늘은 뭘 도와줄까?'}}
    if re.fullmatch(r'(?:고마워|감사해|감사합니다)',text):
        return {'id':'small_talk','args':{'reply':'응~ 필요하면 언제든 말해줘.'}}
    daily=r'(?:일일\s*(?:업무\s*)?보고서|업무\s*보고서|보고서)'
    ai=r'(?:AI|에이아이|a\.i)\s*보고서'
    join=r'\s*(?:랑|와|과|하고|및|그리고|[·,/+])\s*'
    action=r'(?:작성(?:해줘|해|하자|하기)|써줘|(?:작업\s*)?(?:진행|실행|처리)(?:해줘|하자))'
    combined=r'(?:'+daily+join+ai+'|'+ai+join+daily+r'|일일\s*[·/+]\s*'+ai+')'
    if re.fullmatch(r'(?:일단\s*)?(?:오늘(?:의|자)?\s*)?'+combined+r'\s*(?:검수까지\s*)?'+action,text,re.I):
        return {'id':'reports_all','args':{}}
    if re.fullmatch(r'(?:일단\s*)?(?:오늘(?:의|자)?\s*)?(?:AI|에이아이|a\.i)\s*보고서(?:부터)?\s*(?:작성(?:해줘|해|하자|하기)|써줘)',text,re.I):return {'id':'ai_report','args':{}}
    if re.fullmatch(r'(?:일단\s*)?(?:오늘(?:의|자)?\s*)?(?:일일\s*업무\s*보고서|일일\s*보고서|업무\s*보고서|보고서)(?:부터)?\s*(?:작성(?:해줘|해|하자|하기)|써줘)',text):return {'id':'daily_report','args':{}}
    if re.fullmatch(r'(?:/보고서상품처리|보고서\s*상품\s*처리(?:해줘|하자)|A군\s*[→·,]?\s*B군\s*[→·,]?\s*C군\s*(?:상품\s*)?처리해줘)',text,re.I):return {'id':'daily_products','args':{}}
    if re.fullmatch(r'(?:/(?:루틴|도움말)|(?:AI\s*없이|코드로)\s*(?:처리할\s*수\s*있는\s*)?업무\s*목록(?:\s*(?:보여줘|알려줘))?)',text,re.I):return {'id':'catalog','args':{}}
    if re.fullmatch(r'(?:/업무목록|(?:오늘(?:의)?\s*)?업무\s*목록\s*(?:보여줘|조회해줘|알려줘|확인해줘))',text):return {'id':'work_list','args':{'today':text.startswith('오늘')}}
    if re.fullmatch(r'(?:/알림|(?:미확인\s*)?알림(?:\s*목록)?\s*(?:보여줘|조회해줘|알려줘|확인해줘))',text):return {'id':'notifications','args':{'unread':text.startswith('미확인')}}
    if re.fullmatch(r'(?:/보고서초안|오피스\s*기록으로\s*오늘(?:의)?\s*(?:업무\s*)?보고서\s*초안\s*(?:만들어줘|작성해줘))',text):return {'id':'report_draft','args':{}}
    if re.fullmatch(r'(?:/정리작업|파일\s*정리\s*작업\s*목록\s*(?:보여줘|조회해줘|알려줘))',text):return {'id':'file_jobs','args':{}}
    search=re.fullmatch(r'(?:/파일검색\s+|파일명\s*검색\s*[:：]\s*)(.{1,200})',text,re.S)
    if search:return {'id':'files_find','args':{'query':search[1]}}
    job=re.fullmatch(r'/정리(실행|상태)\s+([0-9a-f]{32})',text)
    if job:return {'id':'file_run' if job[1]=='실행' else 'file_status','args':{'job_id':job[2]}}
    return None


def detail_materials_reply():
    """Bounded local listing; no browser, model, network or file writes."""
    import json
    from pathlib import Path
    incoming=Path.home()/'Documents'/'네이트온 받은 파일'
    extensions={'.zip','.xlsx','.xls','.csv','.psd','.ai','.pdf','.json'}
    rows=[]
    try:
        for path in incoming.iterdir():
            if not path.name.startswith('~$') and not path.is_symlink() and path.is_file() and path.suffix.lower() in extensions:
                try: rows.append((path.stat().st_mtime,path.name))
                except OSError: continue
    except OSError:
        return '네이트온 받은 폴더를 지금 읽지 못했어요. 상품명이나 파일명을 알려주시겠어요? 아직 제작은 시작하지 않았어요.'
    rows.sort(reverse=True)
    labels=[name for _,name in rows[:8]]
    manifest=Path(__file__).resolve().parent/'local_backups'/'detail-builder-demo'/'manifest.json'
    try:
        data=json.loads(manifest.read_text(encoding='utf-8'))
        destination=Path(data['destination'])
        if ((manifest.parent/data['template']).is_file() and Path(data['source_dir']).is_dir()):
            labels.append('후이저우 · 소프트 텅 리얼 페라 — 기존 정리된 준비자료')
    except (OSError,ValueError,KeyError,TypeError): pass
    if not labels:
        return '네이트온 받은 폴더에서 제작 자료 후보를 찾지 못했어요. 어떤 상품 자료인지 알려주시겠어요?'
    return '태준님, 이런 자료들이 있어요.\n'+'\n'.join('- '+name for name in labels)+'\n어떤 자료로 상세페이지를 만들까요?'


def execute(routine,call):
    name=routine['id'];args=routine['args']
    if name=='nateon_file_send':return call('messenger_nateon_send_file',args)['message']
    if name=='detail_materials':return detail_materials_reply()
    if name=='material_search':
        result=call('file_history_list',dict(direction='all',query=args['query'],include_existing=True))
        if result.get('status')!='ok':return result.get('message','자료 검색을 완료하지 못했어요.')
        data=result.get('data') or {};rows=data.get('items',[])
        return ('확인된 자료 '+str(data.get('total',len(rows)))+'건이에요. 오른쪽 받은파일의 검색에서도 같은 자료를 볼 수 있어요.\n'+
                ('\n'.join('- '+r['name']+' · '+(r.get('person') or '상대방 미확인')+'\n  '+r.get('current_path','') for r in rows[:20]) or '일치하는 기록이 없어요.')+
                ('\n일부 결과만 표시했어요. 검색어를 더 붙이면 범위를 줄일 수 있어요.' if len(rows)>20 or data.get('has_more') else '')+
                '\n현재 기록된 수발신 자료 기준이며 전체 드라이브를 검색한 결과는 아니에요.')
    if name in {'work_log_query','work_log_note','work_log_export'}:
        result=call(name,args)
        return result.get('message','작업일지를 처리하지 못했어요.')
    if name=='file_history_latest':
        result=call('file_history_latest',args)
        return result.get('message','파일 이력을 확인하지 못했어요.')
    if name=='detail_demo':
        result=call('detail_demo_run',{'phase':args.get('phase','all')})
        return result.get('message','상세페이지 시연 결과를 확인하지 못했어요.')
    if name=='small_talk':return args['reply']
    if name=='reports_all':
        import task_control
        messages=[]
        for tool,arguments in [('daily_report_run',{'scope':'all'}),('ai_report_run',{})]:
            task_control.checkpoint()
            try:
                result=call(tool,arguments)
                messages.append(result.get('message','실행 결과를 확인하지 못했어요.'))
            except task_control.Stopped:
                raise
            except Exception as error:
                messages.append(('일일보고서' if tool=='daily_report_run' else 'AI 보고서')+' 확인 필요: '+str(error).split('\n')[0][:180])
        return '\n'.join(messages)
    if name=='ai_report':
        result=call('ai_report_run',{})
        return result.get('message','AI 보고서 실행 결과를 확인하지 못했어요.')
    if name in {'daily_report','daily_products'}:
        result=call('daily_report_run',{'scope':'products' if name=='daily_products' else 'all'})
        return result.get('message','코드 실행 결과를 확인하지 못했어.')
    if name=='catalog':return 'AI 없이 처리하는 등록 업무:\n'+'\n'.join('- '+row['example'] for row in CATALOG)
    if name in {'file_run','file_status'}:
        selected=call('files_job_select',{'id':args['job_id']})
        if selected.get('status')!='ok':return selected.get('message','정리 작업을 찾지 못했어.')
        result=call('files_job_run' if name=='file_run' else 'files_job_status',{'ref':selected['data']['ref']})
        return result.get('message','실행 결과를 확인하지 못했어.')
    tool={'work_list':'work_list','notifications':'notifications_list','files_find':'files_find','report_draft':'work_report','file_jobs':'files_jobs'}[name]
    arguments={'query':args['query'],'kind':'all','limit':30} if name=='files_find' else {}
    result=call(tool,arguments)
    if result.get('status')!='ok':return result.get('message','실행 결과를 확인하지 못했어.')
    data=result.get('data') or {}
    if name=='work_list' and args.get('today'):
        today=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
        rows=[row for row in data.get('tasks',[]) if str(row.get('created','')).startswith(today) or row.get('due_date')==today]
        labels={'todo':'대기','review':'검토 중','doing':'진행 중','done':'완료'}
        return '오늘 등록했거나 오늘이 마감인 업무 목록이야.\n'+('\n'.join('- '+row['title']+' · '+labels.get(row['status'],row['status']) for row in rows[:40]) or '해당 업무가 없어.')
    if name=='files_find':
        rows=data.get('items',[])
        return result['message']+'\n'+('\n'.join('- '+str(row.get('name') or row.get('title') or '')+' · '+str(row.get('path') or row.get('location') or '') for row in rows[:30]) or '표시할 결과가 없어.')
    if name=='file_jobs':
        labels={'planned':'계획됨','running':'진행 중','done':'완료','completed':'완료','stopped':'중지','partial':'부분 처리','undone':'되돌림','undo_stopped':'복구 중지','undo_partial':'부분 복구','error':'오류'}
        return result['message']+'\n'+('\n'.join('- '+str(row.get('title',''))+' · '+labels.get(row.get('status'),str(row.get('status','')))+'\n  작업ID: '+str(row.get('id','')) for row in data.get('items',[])) or '등록된 작업이 없어.')
    if name=='notifications':
        rows=[row for row in data.get('items',[]) if not args.get('unread') or not row.get('confirmed')]
        return ('오피스가 수신한 '+('미확인 ' if args.get('unread') else '')+'알림 목록이야.\n'+
                ('\n'.join('- '+str(row.get('source',''))+' · '+str(row.get('title',''))[:200] for row in rows[:30]) or '현재 표시할 알림이 없어.')+
                '\n전체 내용은 오른쪽 알림 카드에서 확인할 수 있어.')
    if name=='report_draft':return result['message']+'\n\n외부 보고서 사이트에는 입력하거나 제출하지 않았어.'
    return result['message']
