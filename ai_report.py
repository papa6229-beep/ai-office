"""Use the existing AI-report page and its own save button/credential prompt."""
import credential_vault

ORIGIN='https://dailywork-phi.vercel.app'


def save(page,day):
    """Retired Chrome entrypoint. Use the registered Ego report runner."""
    raise ValueError('AI 보고서는 ai_report_ego.run을 사용하세요. 예전 Chrome 저장 경로는 실행하지 않았어요.')
    if credential_vault.origin(page.url)!=ORIGIN:
        raise ValueError('AI 보고서의 실제 사이트를 확인해 주세요.')
    if page.locator('#dateSel').input_value()!=day:
        raise ValueError('저장할 보고서 날짜가 현재 화면과 달라요.')
    accounts=[row for row in credential_vault.listing(ORIGIN) if row['origin']==ORIGIN]
    if len(accounts)!=1:raise ValueError('AI 보고서 저장 계정을 하나로 확인하지 못했어요.')

    def respond(dialog):
        if (dialog.type=='prompt' and dialog.message=='입력 비밀번호를 넣어주세요.'
                and credential_vault.origin(page.url)==ORIGIN):
            _,secret=credential_vault.load_for_origin(accounts[0]['id'],page.url)
            try:dialog.accept(secret)
            finally:del secret
        else:dialog.dismiss()

    page.on('dialog',respond)
    try:
        with page.expect_response(lambda r:r.url==ORIGIN+'/api/data' and r.request.method=='POST',timeout=20000) as pending:
            page.locator('#btnSave').click()
        response=pending.value
        if not response.ok:
            raise ValueError('AI 보고서 저장을 사이트가 승인하지 않았어요: HTTP '+str(response.status))
        submitted=response.request.post_data_json
        page.wait_for_function('()=>document.querySelector("#saveState").textContent.startsWith("저장됨")',timeout=10000)
        check=page.request.get(ORIGIN+'/api/data',timeout=15000)
        if not check.ok:raise ValueError('AI 보고서 저장 후 재조회를 확인하지 못했어요.')
        actual=check.json()
        if any(actual.get(key)!=submitted.get(key) for key in ('plan','months')):
            raise ValueError('AI 보고서 저장 후 내용이 달라요. 다시 저장하지 않았어요.')
        return {'saved':True,'verified':True,'day':day,'updated_at':actual.get('updatedAt'),
                'mode':'code','model_calls':0}
    finally:page.remove_listener('dialog',respond)
