"""Registered daily-report procedure. No model calls or generated selectors."""
import copy
from contextlib import closing
import datetime
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
import uuid

REPORT_ENGINE = 'ego'
ROOT = Path(__file__).resolve().parent
REPORT = 'https://webdesign-daily-report.vercel.app'
BIZ = 'https://banana.bizhost.kr'
LIST_URL = BIZ + '/adminx/goods.php?code=g1_2'
STATUS = {'todo':'예정','review':'검토중','doing':'진행중','done':'완료'}


def today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()


def _checkpoint():
    import task_control
    task_control.checkpoint()


def _number(value):
    value = str(value).strip().replace(',', '')
    if not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
        raise ValueError('숫자 값을 확인하지 못함')
    from decimal import Decimal
    return Decimal(value)


def _exclude(name, group):
    if name.startswith('[완료]'): return '이미 완료'
    if re.search(r'작업\s*중|리얼돌|매장\s*전용', name, re.I): return '제외 대상'
    if not re.match('^' + re.escape(group), name, re.I): return '다른 군'
    return ''


def _account(page, site, username, password):
    import credential_vault
    rows = [r for r in credential_vault.listing(site) if r['origin'] == site]
    if len(rows) != 1: raise ValueError('등록된 사이트 계정을 하나로 확인하지 못함')
    user, secret = credential_vault.load_for_origin(rows[0]['id'], page.url)
    if username: page.locator(username).fill(user)
    page.locator(password).fill(secret)
    del user, secret


def _get_page(context, prefix, url):
    existing = next((p for p in context.pages if p.url.startswith(prefix)), None)
    page = existing or context.new_page()
    if page.url != url: page.goto(url, wait_until='domcontentloaded', timeout=25000)
    page.set_default_timeout(12000)
    return page


def _login_report(context):
    page = _get_page(context, REPORT + '/studio-admin', REPORT + '/studio-admin')
    if page.locator('input[name=password]').count():
        _account(page, REPORT, None, 'input[name=password]')
        page.get_by_role('button', name='보고서 관리 열기', exact=True).click()
    page.locator('textarea[aria-label="팀장 업무 기록"]').wait_for(timeout=20000)
    return page


def _sms(db, since, used):
    try:
        with closing(sqlite3.connect('file:'+Path(db).resolve().as_posix()+'?mode=ro',uri=True,timeout=10)) as c:
            rows = c.execute('SELECT id,title,body,received,coalesce(ingested,received) FROM office_notifications '
                             'WHERE coalesce(ingested,received)>=? ORDER BY coalesce(ingested,received) DESC LIMIT 100', (since,)).fetchall()
    except sqlite3.OperationalError:
        # A standalone screen can still accept a manually supplied current code.
        return None
    for ident,title,body,received,ingested in rows:
        if ident in used or received < since - 15: continue
        if not re.search('비앤앤컴퍼니|비즈호스트', title + ' ' + body): continue
        matches = re.findall(r'(?:관리자허용\s*IP\s*)?인증번호(?:는|[\s:：])*[^\d]{0,15}(\d{4,8})', body)
        if matches: return ident, matches[-1]
    return None


def _login_biz(context, db, emit, *, sms_reader=None, new_page=False):
    page = (None if new_page else next((p for p in context.pages if p.url.startswith(BIZ+'/') and 'g1_2_4' not in p.url), None)) or context.new_page()
    if page.url == 'about:blank' or not page.url.startswith(BIZ):
        page.goto(LIST_URL, wait_until='domcontentloaded', timeout=25000)
    since = time.time()
    if page.locator('input[name=smsnumber]').count():
        # Resume a current challenge, without sending another SMS.
        since -= 300
    elif page.locator('input[name=passwd]').count():
        _account(page, BIZ, 'input[name=id]', 'input[name=passwd]')
        page.locator('button[type=submit]').click(no_wait_after=True)
        page.locator('#smsnumber').wait_for(timeout=15000)
    if page.locator('#smsnumber').count():
        emit('문자 인증번호 수신 대기 · 코드 실행 중 · AI 호출 없음')
        used=set(); deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            _checkpoint()
            incoming=(sms_reader() if sms_reader else None) or _sms(db,since,used)
            if not incoming:
                page.wait_for_timeout(750);continue
            ident,code=incoming;used.add(ident)
            response=page.request.post(BIZ+'/login/', form={'act':'loginajax','sub':'smschk','smsnumber':code,'fid':'','url':'/adminx/goods.php?code=g1_2'},timeout=20000)
            del code
            result=response.json();act=str(result.get('actCode'))
            if act=='2001':
                url=urljoin(BIZ+'/',result.get('url','/adminx/goods.php?code=g1_2'))
                if urlsplit(url).netloc!='banana.bizhost.kr':raise ValueError('인증 후 이동 사이트 불일치')
                page.goto(url,wait_until='domcontentloaded',timeout=25000);break
            if act=='2002':
                emit('최신 인증번호가 일치하지 않음 · 재전송하지 않고 새 수신 확인 중');continue
            raise ValueError('사이트 인증 실패: '+act)
        else:raise ValueError('5분 안에 유효한 인증문자가 확인되지 않음')
    page.goto(LIST_URL,wait_until='domcontentloaded',timeout=25000)
    page.locator('form[name=search] input[name=keyword]').wait_for()
    action=page.locator('form[name=search]').get_attribute('action') or ''
    if 'order.php' in action:raise ValueError('상품 검색 폼이 아님')
    return page


def _report(db, page, day, inputs, result, persist):
    if not isinstance(inputs,dict) or inputs.get('available') is not True:
        raise ValueError('현재 오피스 탭의 타부서 요청 목록이 아직 동기화되지 않음. 기존 보고서 요청을 임의로 삭제하지 않음')
    response=page.request.get(REPORT+'/api/report?'+urlencode({'date':day}),timeout=20000)
    if not response.ok:raise ValueError('현재 날짜 보고서를 읽지 못함')
    before=response.json();target=copy.deepcopy(before)
    if not isinstance(target.get('kpis'),dict):raise ValueError('보고서 데이터 형식이 변경됨')
    with closing(sqlite3.connect(str(db),timeout=10)) as c:
        rows=c.execute('SELECT id,title,detail,status FROM tasks WHERE substr(created,1,10)=? ORDER BY created,id',(day,)).fetchall()
    target['leadNote']='\n'.join('\n'.join(filter(None,[r[1]+' · '+STATUS.get(r[3],r[3]),r[2]])) for r in rows)
    old=before.get('departmentRequests',[]);requests=[]
    labels={'review':'검토 중','doing':'진행 중','done':'완료','todo':'검토 중'}
    for row in inputs['requests']:
        matching=[r for r in old if r.get('team')==row['team'] and r.get('content')==row['content']]
        entry=copy.deepcopy(matching[0]) if len(matching)==1 else {'uid':uuid.uuid4().hex,'imageIds':[]}
        entry.update(team=row['team'],content=row['content'],status=labels.get(row['status'],row['status']),startDate=row['start'],dueDate=row['due'])
        requests.append(entry)
    target['departmentRequests']=requests
    persist('report-before.json',before)
    # Optimistic read immediately before writing to avoid overwriting concurrent edits.
    current=page.request.get(REPORT+'/api/report?'+urlencode({'date':day}),timeout=20000).json()
    if current!=before:raise ValueError('보고서가 다른 곳에서 변경됨; 덮어쓰지 않음')
    changed=target!=before
    if changed:
        response=page.request.post(REPORT+'/api/report',data={'date':day,'report':target},timeout=25000)
        if not response.ok:raise ValueError('보고서 저장 응답 실패')
    after=page.request.get(REPORT+'/api/report?'+urlencode({'date':day}),timeout=20000).json()
    for key in before:
        if key in ('leadNote','departmentRequests','kpis'):continue
        if after.get(key)!=before[key]:raise ValueError('보고서의 다른 항목이 달라짐; 추가 실행 중단')
    if after.get('leadNote')!=target['leadNote'] or after.get('departmentRequests')!=target['departmentRequests']:
        raise ValueError('보고서 저장값 재조회 불일치')
    result['report']={'day':day,'tasks':len(rows),'requests':len(requests),'saved':changed,'verified':True,'verified_at':time.time()}
    page.goto(REPORT+'/studio-admin?'+urlencode({'date':day}),wait_until='domcontentloaded',timeout=25000)
    page.locator('input[aria-label="작성 날짜"]').fill(day)


COLLECT = r'''() => {
 const form=document.forms.listform;
 if(!form)throw Error('상품목록 폼 없음');
 const seen=new Set(),items=[];
 for(const a of form.querySelectorAll('a[href*="code=g1_2_4"]')){
  const name=a.textContent.trim(),url=new URL(a.getAttribute('href'),location.href);
  if(!name||seen.has(url.searchParams.get('index_no')))continue;
  const row=a.closest('tr'),table=row?.closest('table');
  const headers=Array.from(table?.querySelectorAll('thead th')||[]).map(x=>x.innerText.trim());
  const ix=headers.indexOf('재고'),cells=Array.from(row?.children||[]);
  const stock=ix>=0&&cells.length===headers.length?cells[ix].innerText.trim():null;
  const id=url.searchParams.get('index_no');if(!id||url.origin!==location.origin)continue;
  const thumbs=[...new Set([...row.querySelectorAll('a[href]')].filter(a=>a.querySelector('img'))
    .map(a=>new URL(a.getAttribute('href'),location.href))
    .filter(u=>u.pathname==='/shop/view.php'&&u.searchParams.get('index_no')===id).map(u=>u.href))];
  seen.add(id);items.push({id,name,url:url.href,stock,detail_url:thumbs.length===1?thumbs[0]:null});
 }
 const count=document.body.innerText.match(/상품은\s*총\s*([\d,]+)\s*개/);
 return {items,total:count?Number(count[1].replaceAll(',','')):null};
}'''


def _collect(page, group):
    items={};number=1;total=None
    while True:
        _checkpoint()
        query={'code':'g1_2','fid':'1','key':'gname','keyword':group,'numper':'40','page':number,'seasonall':'Y','showtype':'cates','sortcol':'regi_date','sortby':'desc'}
        page.goto(BIZ+'/adminx/goods.php?'+urlencode(query),wait_until='domcontentloaded',timeout=25000)
        page.locator('form[name=search] input[name=keyword]').wait_for()
        if page.locator('form[name=search] input[name=keyword]').input_value()!=group:raise ValueError('상품 검색어 반영 실패')
        data=page.evaluate(COLLECT)
        if data['total'] is None:raise ValueError('검색 결과 개수를 확인하지 못함')
        if total is None:total=data['total']
        if data['total']!=total:raise ValueError('조회 중 상품목록이 변경됨')
        prior=len(items)
        for row in data['items']:items[row['id']]=row
        if len(items)>=total:break
        if len(items)==prior:raise ValueError('검색 결과 페이지를 모두 수집하지 못함')
        number+=1
    return list(items.values())


def _inspect_detail(context, row, folder):
    import product_image_review, task_control
    detail = context.new_page(); detail.set_default_timeout(12000)
    try:
        url = row.get('detail_url')
        if not url: raise ValueError('동일 상품의 섬네일 상세 링크 확인 불가')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in {'banana.bizhost.kr', 'www.bananamall.co.kr', 'bananamall.co.kr'} or parsed.path != '/shop/view.php' or parse_qs(parsed.query).get('index_no') != [row['id']]:
            raise ValueError('섬네일 링크의 상품/사이트 불일치')
        detail.goto(url, wait_until='domcontentloaded', timeout=25000)
        # Product pages also contain a hidden login popup. Only a visible
        # password field means the site is actually asking us to sign in.
        if detail.locator('form[name=loginform] input[name=passwd]').is_visible():
            origin = urlsplit(detail.url)
            if origin.scheme != 'https' or origin.hostname not in {'www.bananamall.co.kr', 'bananamall.co.kr'}:
                raise ValueError('바나나몰 로그인 사이트 확인 불가')
            _account(detail, 'https://' + origin.netloc, 'form[name=loginform] input[name=id]', 'form[name=loginform] input[name=passwd]')
            with detail.expect_navigation(wait_until='domcontentloaded', timeout=20000):
                detail.locator('form[name=loginform] input[type=image]').click()
            # Re-enter the observed product URL with the shared authenticated context.
            detail.goto(url, wait_until='domcontentloaded', timeout=25000)
        if parse_qs(urlsplit(detail.url).query).get('index_no') != [row['id']]:
            raise ValueError('로그인 후 상세 상품 ID 불일치')
        return product_image_review.inspect(detail, row['name'], folder, _checkpoint)
    except task_control.Stopped:
        raise
    except Exception as error:
        return {'state':'needs_review','reasons':['이미지 검수: '+str(error).split('\n')[0][:180]],'external_model_calls':0}
    finally:
        if not detail.is_closed(): detail.close(run_before_unload=False)


def _md(edit):
    """Read the selected MD label, not account IDs or guessed name fragments."""
    field = edit.locator('form[name=writeform] select[name=md_idx]')
    if field.count() != 1: return '확인 불가', '담당MD 입력 항목 확인 불가'
    data = field.evaluate('(e)=>({value:e.value,label:e.selectedOptions[0]?.textContent.trim()||""})')
    if not data['value']: return '확인 불가', '담당MD 미지정'
    name = data['label'].split('(',1)[0].strip()
    if name in {'이석환','조혜연','조현진','안용훈'}: return name, None
    return name or '확인 불가', '담당MD가 지정된 팀원 4명 중 하나로 확인되지 않음'


def _inspection_report(page, day, result, persist):
    """Append only independently verified completions; preserve concurrent report edits."""
    _checkpoint()
    response = page.request.get(REPORT+'/api/report?'+urlencode({'date':day}), timeout=20000)
    if not response.ok: raise ValueError('검수 완료 기록용 보고서 조회 실패')
    before = response.json()
    if not isinstance(before.get('inspectionText'), str):
        raise ValueError('보고서 상세페이지 검수 완료 입력 항목 확인 불가')
    target = copy.deepcopy(before)
    prior = {line.strip() for line in before['inspectionText'].splitlines() if line.strip()}
    additions = []
    for row in result['completed']:
        name = row['name']
        if '\n' in name or '\r' in name: raise ValueError('전체 상품명에 줄바꿈이 있어 보고서 기록 확인 필요')
        if name not in prior:
            additions.append(name); prior.add(name)
    if additions:
        target['inspectionText'] = before['inspectionText'].rstrip('\r\n') + ('\n' if before['inspectionText'].rstrip('\r\n') else '') + '\n'.join(additions)
    persist('report-inspection-before.json', before)
    current = page.request.get(REPORT+'/api/report?'+urlencode({'date':day}), timeout=20000)
    if not current.ok or current.json() != before:
        raise ValueError('보고서가 다른 곳에서 변경됨; 검수 내역 덮어쓰기 중단')
    _checkpoint()
    if additions:
        response = page.request.post(REPORT+'/api/report', data={'date':day,'report':target}, timeout=25000)
        if not response.ok: raise ValueError('검수 완료 목록 최종 보고서 저장 실패; 상품 완료 내역은 실행 기록에 보존됨')
    response = page.request.get(REPORT+'/api/report?'+urlencode({'date':day}), timeout=20000)
    if not response.ok: raise ValueError('검수 완료 목록 최종 저장 재조회 실패')
    after = response.json()
    for key in set(before) | set(target):
        if key == 'kpis': continue  # The report service may recompute derived counts.
        if after.get(key) != target.get(key): raise ValueError('최종 보고서 저장값 또는 보존 항목 불일치')
    result['inspection_report'] = {'day':day,'added':len(additions),'completed':len(result['completed']),
                                   'saved':bool(additions),'verified':True}
    persist('result.json', result)
    # Reload the report only after the final independent readback, leaving it open.
    page.goto(REPORT+'/studio-admin?'+urlencode({'date':day}), wait_until='domcontentloaded', timeout=25000)
    page.locator('input[aria-label="작성 날짜"]').fill(day)


class ProductSaveUnverified(ValueError):
    """A dispatched save without independently matching server values."""


def _save_and_verify_product(context,edit,url,fields,target):
    expected_name=fields['gname'] if fields['gname'].startswith('[완료]') else '[완료]'+fields['gname']
    if target!=expected_name:
        raise ValueError('완료 상품명 범위 불일치')
    expected={'gname':target,'account':fields['account'],'daccount':fields['daccount']}
    started=time.monotonic()
    _checkpoint()
    edit.locator('form[name=writeform] [name=gname]').fill(target)
    action=edit.locator('a[href="javascript:saveContent();"]').last.click(no_wait_after=True,timeout=12000)
    if not isinstance(action,dict) or not action.get('save_completed') or not action.get('product_post_seen') or not action.get('product_post_ok'):
        raise ProductSaveUnverified('상품 저장 확인창·완료 알림·POST 확인 실패; 추가 제출하지 않음')
    verification=context.new_page()
    try:
        for attempt in range(3):
            _checkpoint()
            verification.goto(url,wait_until='domcontentloaded',timeout=25000)
            verification.locator('form[name=writeform] [name="gname"]').wait_for(state='attached',timeout=15000)
            actual={name:verification.locator('form[name=writeform] [name="'+name+'"]').input_value()
                    for name in ('gname','account','daccount')}
            if actual==expected:
                return {'verified':True,'readback_attempts':attempt+1,'save_action':action,
                        'elapsed_ms':round((time.monotonic()-started)*1000)}
            if any(actual[name]!=fields[name] for name in ('account','daccount')):
                raise ProductSaveUnverified('저장 후 가격 불일치; 추가 제출하지 않음')
            if actual['gname'] not in {fields['gname'],target}:
                raise ProductSaveUnverified('저장 후 다른 상품명 관찰; 추가 제출하지 않음')
            if attempt<2:
                edit.wait_for_timeout(250*(attempt+1))
        raise ProductSaveUnverified('저장 반영 확인 실패; 읽기만 재조회했고 추가 제출하지 않음')
    finally:
        if not getattr(context,'reconciliation_required',False) and not verification.is_closed():
            verification.close()


def _approved_product_exception(issue, reasons):
    # Deliberately limited to the exact product/state approved by the user.
    # Price, image and transport failures can never be waived here.
    if (issue.get('id')=='2499276'
        and issue.get('name')=='A군 - [맥심 코리아] 맥심 2026년 11월호'):
        waived=[reason for reason in reasons if reason in {'재고 0'}]
        if waived:
            issue['approved_exception']={'date':'2026-10-29','waived':waived,
                'reason':'사용자가 현재 맥심의 디자인 작업 완료 상태를 승인. 재고 입력·판매 오픈은 별도 담당 업무.'}
            return [reason for reason in reasons if reason not in waived]
    return reasons


def _products(context,page,result,emit,persist, *, observe=lambda event:None, product_ids=None):
    from ego_report_session import EgoResponseUncertain, EgoCommandError
    import task_control
    for group in ('A군','B군','C군'):
        emit(group+' 검색 결과 수집 중 · 코드 실행 · AI 호출 없음')
        rows=_collect(page,group);summary={'found':len(rows),'completed':0,'excluded':0,'issues':0};result['groups'][group]=summary
        persist(group+'-before.json',rows)
        observe({'type':'group','group':group,'found':len(rows)})
        for row in rows:
            if product_ids is not None and row['id'] not in product_ids:continue
            _checkpoint()
            if _exclude(row['name'],group):
                summary['excluded']+=1
                observe({'type':'excluded','group':group,'name':row['name'],'id':row['id'],'reason':_exclude(row['name'],group)})
                persist('result.json',result)
                continue
            issue=dict(group=group,id=row['id'],name=row['name'],url=row['url'],md='확인 불가')
            observe({'type':'product','group':group,'name':row['name'],'id':row['id']})
            reasons=[]
            emit(group+' · 이미지 검수 → 가격·재고·담당MD 확인 중 · 코드 실행')
            review=_inspect_detail(context,row,Path(result['run_folder'])/'images'/row['id'])
            issue['image_review']=review
            observe({'type':'image_review','state':review['state'],'reasons':review['reasons'],'gap':review.get('gap_css_px')})
            if review['state']!='pass':reasons.extend(review['reasons'] or ['이미지 검수 확인 불가'])
            try:
                stock=_number(row['stock'])
                issue['stock']=str(stock)
                if stock<=0:
                    reasons.append('재고 0' if stock==0 else '재고 음수')
            except ValueError:
                reasons.append('재고 확인 불가')
            edit=context.new_page();edit.set_default_timeout(12000)
            dialogs=[]
            def respond(dialog):
                dialogs.append({'type':dialog.type,'message':dialog.message[:300]})
                if dialog.type=='alert' or (dialog.type=='confirm' and re.search('변경|수정|저장',dialog.message)):
                    dialog.accept()
                else:dialog.dismiss()
            edit.on('dialog',respond)
            try:
                edit.goto(row['url'],wait_until='domcontentloaded',timeout=25000)
                issue['md'],md_issue=_md(edit)
                if md_issue:reasons.append(md_issue)
                fields={name:edit.locator('form[name=writeform] [name="'+name+'"]').input_value() for name in ('gname','account','daccount')}
                if fields['gname']!=row['name']:raise ValueError('조회 후 상품명이 변경됨')
                try:
                    price,wholesale=_number(fields['account']),_number(fields['daccount'])
                    issue.update(price=str(price),wholesale=str(wholesale))
                    if price!=wholesale:reasons.append('판매가·도매가 불일치')
                    if price<=0 or wholesale<=0:reasons.append('판매가·도매가가 0 이하')
                except ValueError:reasons.append('판매가·도매가 숫자 확인 불가')
                observe({'type':'fields','data':{k:issue.get(k) for k in ('price','wholesale','stock','md')}})
                reasons=_approved_product_exception(issue,reasons)
                if reasons:continue
                _checkpoint()
                target='[완료]'+fields['gname']
                result['pending_save']={'group':group,'id':row['id'],'target':target,
                    'state':'dispatching','started_at':time.time()}
                persist('result.json',result)
                started=time.monotonic()
                try:
                    issue['save_verification']=_save_and_verify_product(context,edit,row['url'],fields,target)
                finally:
                    import execution_receipts
                    execution_receipts.observe(result.setdefault('stage_timings',{}),'product_save_readback',started)
                result.setdefault('save_receipts',[]).append({'id':row['id'],**issue['save_verification']})
                result.pop('pending_save',None)
                summary['completed']+=1;result['completed'].append(dict(issue,name=target));emit(group+' · 완료 '+str(summary['completed'])+'건 / 예외 '+str(summary['issues'])+'건')
            except task_control.Stopped:raise
            except EgoResponseUncertain:
                reasons.append('Ego 응답 불명확 · 기존 창의 효과 확인 필요 · 자동 재전송 없음')
                raise
            except ProductSaveUnverified as error:
                result.setdefault('execution_issues',[]).append({'group':group,'id':row['id'],'code':'save_unverified'})
                if result.get('pending_save'):result['pending_save']['state']='unverified'
                reasons.append(str(error))
            except EgoCommandError as error:
                result.setdefault('execution_issues',[]).append({'group':group,'id':row['id'],
                    'operation':error.op,'code':error.code})
                reasons.append(str(error).split('\n')[0][:180])
            except Exception as error:
                # Record a bounded explanation; never retry an ambiguous save.
                reasons.append(str(error).split('\n')[0][:180])
            finally:
                if reasons:
                    issue.update(reasons=list(dict.fromkeys(reasons)),reason=' / '.join(dict.fromkeys(reasons)))
                    result['issues'].append(issue);summary['issues']+=1
                observe({'type':'product_result','group':group,'name':row['name'],'id':row['id'],
                         'state':'uncertain' if getattr(context,'reconciliation_required',False) else 'issue' if reasons else 'completed',
                         'md':issue['md'],'reasons':list(dict.fromkeys(reasons))})
                if not getattr(context,'reconciliation_required',False) and not edit.is_closed():edit.close(run_before_unload=False)
                persist('result.json',result)


def _replace_receipt(temporary,target):
    from execution_receipts import replace
    replace(temporary,target)


def _run_report(db, endpoint=None, *, inputs=None, day=None, products_only=False, emit=lambda text:None, request_id=None, product_ids=None):
    """Trusted entrypoint shared by direct execution and the no-model chat routine."""
    from ego_report_session import Session
    import task_control
    control=task_control.current()
    request_id=request_id or (control.request_id if control else None)
    if not request_id or not re.fullmatch('[0-9a-f]{32}',request_id):
        raise ValueError('현재 보고서 요청 ID가 필요해요. Chrome으로 대체하지 않았어요.')
    import office_fastlane
    office_fastlane.require_active_request(db,request_id)
    day=day or today();datetime.date.fromisoformat(day)
    folder=ROOT/'local_backups'/'report-automation'/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    def persist(name,data):
        temporary=folder/(name+'.tmp');temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');_replace_receipt(temporary,folder/name)
        if name=='result.json':
            import work_log
            for stage,label in [('report','업무보고서 저장'),('inspection_report','보고서 최종 저장')]:
                value=data.get(stage,{})
                if value.get('verified'):
                    if not value.get('saved',True):label=label.replace('저장','내용 확인')
                    work_log.safe_event(db,'report:'+folder.name+':'+stage,'report',label,value.get('verified_at') or time.time(),
                                        data=dict(report_day=day,status='verified',source='daily_report',stage=stage,time_precision='exact',saved=bool(value.get('saved',True))))
    result={'state':'running','browser':'ego','mode':'code','model_calls':0,'day':day,'groups':{},'completed':[],'issues':[],'started':time.time(),'run_folder':str(folder),'request_id':request_id}
    persist('result.json',result)
    try:
        import product_image_review
        product_image_review.available()
        with Session(db,request_id,folder) as context:
            result['space_id']=context.space_id
            persist('result.json',result)
            if not products_only:
                emit('보고서 데이터 반영 중 · 코드 실행 · AI 호출 없음')
                report=_login_report(context)
                confirmation_started=time.monotonic()
                try:_report(db,report,day,inputs,result,persist)
                finally:
                    import execution_receipts
                    execution_receipts.observe(result.setdefault('stage_timings',{}),'report_save_confirmation',confirmation_started)
                persist('result.json',result)
            else:
                report=_login_report(context)
            # Verify the final destination before modifying any commercial products.
            report.locator('textarea[aria-label="상세페이지 검수 완료 내용"]').wait_for(state='attached',timeout=15000)
            report_data=report.request.get(REPORT+'/api/report?'+urlencode({'date':day}),timeout=20000)
            if not report_data.ok or not isinstance(report_data.json().get('inspectionText'),str):
                raise ValueError('최종 보고서 검수 완료 입력 항목 확인 불가')
            page=_login_biz(context,db,emit)
            _products(context,page,result,emit,persist,product_ids=product_ids)
            emit('검수 완료 상품명 기록 및 보고서 최종 저장·재조회 중 · 코드 실행')
            _inspection_report(report,day,result,persist)
            result['inspection_report']['verified_at']=time.time();persist('result.json',result)
            context.finish()
            result['state']='partial' if result.get('execution_issues') else 'done'
    except Exception as error:
        import task_control
        result.update(state='cancelled' if isinstance(error,task_control.Stopped) else 'uncertain' if getattr(error,'code',None)=='response_uncertain' else 'error',error=str(error).split('\n')[0][:250],
                      failure={'code':getattr(error,'code','execution_error'),'error_type':type(error).__name__,
                               'operation':getattr(error,'op',None),'automatic_retry':False})
    finally:
        result['elapsed_seconds']=round(time.time()-result['started'],2);persist('result.json',result)
    return result


class ProductRunLock:
    """One product-changing run across Office and the standalone process."""
    def __enter__(self):
        import msvcrt
        folder=ROOT/'local_backups'/'report-automation';folder.mkdir(parents=True,exist_ok=True)
        self.handle=(folder/'product-run.lock').open('a+b')
        self.handle.seek(0,2)
        if self.handle.tell()==0:self.handle.write(b'0');self.handle.flush()
        self.handle.seek(0)
        try:msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.handle.close()
            raise ValueError('다른 상품 검수가 실행 중이에요. 해당 검수를 마친 뒤 시작해 주세요.') from None
        return self

    def __exit__(self,*args):
        import msvcrt
        try:self.handle.seek(0);msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
        finally:self.handle.close()


def run(db, endpoint=None, *, inputs=None, day=None, products_only=False, emit=lambda text:None, request_id=None, product_ids=None):
    if product_ids is not None:
        if not isinstance(product_ids,(list,tuple,set)) or not product_ids or not all(isinstance(i,str) and re.fullmatch(r'\d+',i) for i in product_ids):
            raise ValueError('재검수할 정확한 상품 ID 목록이 필요합니다.')
        product_ids=set(product_ids)
    with ProductRunLock():
        return _run_report(db,inputs=inputs,day=day,products_only=products_only,emit=emit,request_id=request_id,product_ids=product_ids)


def run_inspection(db, endpoint, *, emit=lambda text:None, observe=lambda event:None, sms_reader=None):
    """Standalone real inspection; does not open or write a daily report."""
    from playwright.sync_api import sync_playwright
    folder=ROOT/'local_backups'/'bizhost-inspector'/'runs'/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    result={'state':'running','mode':'code','model_calls':0,'day':today(),'groups':{},'completed':[],
            'issues':[],'started':time.time(),'run_folder':str(folder),'report_written':False}
    def persist(name,data):
        target=folder/name;temporary=target.with_suffix(target.suffix+'.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(target)
        if name=='result.json':observe({'type':'snapshot','result':copy.deepcopy(data)})
    persist('result.json',result)
    try:
        import product_image_review
        product_image_review.available()
        with ProductRunLock(),sync_playwright() as playwright:
            browser=playwright.chromium.connect_over_cdp(endpoint);context=browser.contexts[0]
            page=_login_biz(context,db,emit,sms_reader=sms_reader,new_page=True)
            try:_products(context,page,result,emit,persist,observe=observe)
            finally:
                if not page.is_closed():page.close(run_before_unload=False)
            result['state']='done'
    except Exception as error:
        import task_control
        result.update(state='cancelled' if isinstance(error,task_control.Stopped) else 'error',
                      error=str(error).split('\n')[0][:250])
    finally:
        result['elapsed_seconds']=round(time.time()-result['started'],2);persist('result.json',result)
    return result


def summary(result):
    lines=['AI 호출 없이 코드로 실행했어.']
    if result.get('report'):lines.append('오늘 업무·타부서 요청의 최초 보고서 반영·재조회 완료.')
    if result.get('inspection_report',{}).get('verified'):
        lines.append('상세페이지 검수 완료 상품명 '+str(result['inspection_report']['added'])+'건 추가 · 최종 보고서 저장·재조회 확인 완료.')
    for group,row in result['groups'].items():lines.append(f"{group}: 검색 {row['found']} · 완료 {row['completed']} · 제외 {row['excluded']} · 문제 {row['issues']}")
    if result['issues']:
        lines.append('완료 표시하지 않은 상품:')
        lines.extend('- '+row['name']+' — '+row['reason']+' — 담당MD: '+row.get('md','확인 불가') for row in result['issues'])
    if result.get('error'):lines.append('미완료: '+result['error'])
    lines.append('소요 '+str(result['elapsed_seconds'])+'초 · 모델 호출 0회')
    return '\n'.join(lines)
