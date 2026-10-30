# 전 부서 과거 업무 재개 사고 분석과 방지 설계

기준: 2026-10-27. 범위: 본부·정보조사·기획·디자인·개발의 공통 요청 전달/복구/배정 경로. 실제 사고를 확인한 대화는 정보조사팀이다. 다른 팀의 동일 사고 각각을 전수 확인했다는 뜻은 아니다.

## 확인된 사고

정보조사팀 `01a17645-6451-7343-b8ce-e6373995a6cc`의 턴 `01a17ae7-cecf-7ab0-8ece-aad5e52d4da3`은 레드컨테이너 조사 요청으로 시작했다. Ego/상품자료 조사를 시작했지만, 문맥 축약 이벤트 `01a17ae9-1d4c-7393-8cd6-91a91b2a225b` 뒤에 새로운 배너 지시 없이 과거 배너 Excel 읽기와 배너 규격 답변으로 바뀌었다. 사용자가 완료된 일이라고 정정한 다음 턴에는 또 다른 완료 업무인 힉스필드를 선택했다. 두 번째 정정 뒤에 중지하고, 사용자의 원인 보고 후 조사 재개 지시에 따라 레드컨테이너를 이어갔다.

실제로 확인된 잘못된 동작은 과거 배너 자료 읽기와 잘못된 답변이다. 이 사고에서 쇼핑몰 상품 변경·발신·삭제가 발생했다는 증거는 없다. 그러나 같은 목표 선택 오류가 쓰기 도구로 이어질 수 있는 구조 위험은 있다.

## 원인과 한계

1. **직접 실행 오류:** 축약 뒤 과거의 완료된 요청을 현재 목표로 선택했다. 연구팀의 자체 조사 기록에 따르면 축약 요약에도 레드컨테이너가 현재이고 배너·힉스필드는 완료라고 적혀 있었다. 요약 텍스트의 존재만으로 올바른 실행이 강제되지 않는다. 플랫폼 내부가 왜 그 목표를 선택했는지는 확인되지 않았다.
2. **대화별 복구 결함:** `office_relay_context.read_current()`가 호출 부서 대신 설정된 본부 thread의 DB만 읽었다. 연구팀의 실제 조회는 본부의 자동 완료보고 ID `ac02e1aab9955bc5adc7238ec6fad922`, 원문 null, 실행 불가를 반환했다. 연구팀 현재 지시를 복구하는 기능이 아니었다.
3. **전달 경계 불완전:** 작업창 `project_panels.send`는 요청별 영수증을 보관하지만, 부서 입력 앞에 고유 요청/대상 대화와 복구 포인터를 붙이지 않았다. 전달은 실제 native 이력에서 userMessage 대신 codex_app의 functionCallOutput으로 나타난다. 플랫폼 내부 역할 처리와 축약 원인을 단정할 수는 없지만, 두 종류 입력을 모두 현재 경계로 처리해야 한다.
4. **별도의 재전송 위험:** 부서 실행기는 턴 종료 뒤 완료 표식이 없으면 최대 1회 원래 지시를 자동 재전송했다. 응답 누락은 효과 미실행의 증거가 아니다. 이번 배너 전환은 이 재전송으로 발생했다는 증거가 없으므로 별개 결함으로 분류한다.
5. **현재 업무 강제 장치 부재:** 기존 HTTP/토큰 보안은 접속 경계다. 모델의 임의 shell/Ego 명령이 최신 업무 범위에 맞는지 검증하는 장치가 아니다. 지침·ID·복구 함수만으로 임의 코드의 의미를 검증했다고 주장하면 안 된다.

앞선 패치는 답변 수집·표시·전달 크기·현재 요청 DB 조회·대기열을 부분 보강했다. 부서 전체의 실제 입력 경계 및 모든 실행 경로를 끝까지 보호한 것은 아니었다. 이 범위 차이를 완료 보고에서 더 분명히 밝혔어야 한다.

## 이번 최소 보강

- 복구 기본 대상은 실제 실행 환경 `CODEX_THREAD_ID`. ID가 없으면 본부로 추정하지 않는다.
- 실제 최신 native 턴의 마지막 사람 입력 또는 검증된 본부 전달 경계를 읽는다. 현재 턴 입력을 못 찾으면 옛 턴으로 내려가지 않는다. 직접 사용자 정정은 옛 Office DB보다 우선한다.
- 작업창·부서 배정·본부 relay의 정확한 ID와 대상 thread를 원장에 대조한다. 완료보고 원문 null/완료·중지·불명확 전달은 실행 가능으로 반환하지 않는다. 표식 없는 기존 전달은 원문이 정확히 하나 일치할 때만 복구하며, 제목/유사어로 매칭하지 않는다.
- 새 작업창 전달 앞에 고유 요청 ID/대상 thread/현재 사용자 입력 경계를 붙인다. 장황한 모든 지침 재첨부나 새 모델 호출은 하지 않는다.
- 완료 표식 누락은 확인 대기로 남기고 자동 재전송하지 않는다. 이전 버전이 이미 queued로 만든 자동 이어가기 역시 대기 처리한다. 정상 미전달 FIFO 업무와 실제 진행 중인 연구 작업은 취소하지 않는다.
- 이미 끝난 원래 본부 요청이나 원문 없는 자동 완료보고를 새 부서 배정의 근거로 쓰지 못하도록 원래 요청의 실제 running 상태와 원문을 대조한다.

**보호 수준:** 복구 조회와 앱 자동 재전송/배정 경계를 보강한다. native 임의 명령의 업무 적합성까지 강제하지 않는다. 기존 진행 중 턴에 새 지침이 자동 주입되는 것도 아니다.

## 전체 방지 완료에 필요한 다음 실행 경계

### A. 현재 업무를 명시적 객체로 유지

`thread_id + task_id + input_revision + native input_id/turn_id + source + status`를 하나의 업무 객체로 둔다. 자료/지침/이력/완료보고는 reference이고 새 업무 생성자가 아니다. 상태는 accepted→running→completed/blocked/stopped/superseded로 기록한다. completed/stopped/superseded는 새 사람 입력에 근거한 새 task_id 없이는 running으로 돌아가지 않는다. 같은 제목을 다시 시켜도 신규 요청과 과거 실행 영수증을 구분한다.

관련 업무가 여러 개면 task별 객체를 보존하되 현재 실행 포인터는 하나로 유지한다. 후속 지시가 추가/전환/취소인지 모호하면 추정 재실행 대신 대기한다. 본부가 이미 보고하고 대기한 사실은 승인된 하위 업무의 자동 취소도, 새 작업 허가도 아니다.

### B. 축약 직후 실제 원문 재주입 + 실패 시 실행 중지

공식 Codex Hooks 문서의 SessionStart(source=compact)는 축약 직후 즉시 이어지는 모델 요청에 context를 전달할 수 있다. 해당 시점에 **현재 대화의 최신 업무 객체만 짧게** 전달하고 조회 실패/중지/완료라면 자동 실행을 이어가지 않게 해야 한다. 매 질문 전체 지침을 다시 붙이는 방식은 사용하지 않는다.

공식 지원 여부·설치된 앱 연결 경로·관리 정책을 확인하고 정확한 hook 정의를 사용자가 검토/신뢰한 뒤 적용해야 한다. hook trust를 자가 승인하거나 bypass 옵션으로 우회하지 않는다. 이번 단계에서 hooks 설정을 몰래 설치/신뢰하거나 실제 compact 재현을 실행하지 않았다.

### C. 쓰기 직전 업무와 대상 검증

상품 저장·메신저 발신·파일 이동/삭제·배포는 등록 실행기에 task_id/input_revision/대상/원하는 변경/기존 상태를 전달한다. 실행기는 DB의 현재 상태를 원자적으로 대조하고, stopped/completed/superseded/기대 revision 불일치/미확인 대상이면 효과 없이 거절한다. 수신 확인이나 큐 접수와 실제 저장 성공을 별개로 기록한다.

목표를 벗어난 명령을 아예 막으려면 자유로운 native shell/Ego 코드도 이 실행 경계 안에서만 업무 쓰기를 수행하도록 해야 한다. 임의 Python/HTTP를 그대로 허용한 채 코드 문자열의 키워드만 검사해 완전한 보호라고 할 수 없다. 필요한 사용자 개발 권한 자체를 없애는 대신, 운영 업무 쓰기와 개발 명령의 실행 근거·대상을 구분한다. 개발 요청이 운영 대상 변경의 포괄 승인은 아니다.

공식 PreToolUse hook은 지원 도구를 실행 전에 거절할 수 있지만, 일부 전문 도구 경로/hosted tool/write_stdin에는 제한이 있다. hook 오류·미신뢰 skip도 확인해야 한다. 따라서 hook 하나를 전체 보안 경계로 삼지 말고 **실제 효과를 내는 실행기 내부 검증**과 함께 사용해야 한다.

### D. 효과 영수증과 재시도

`task_id + action + target + intended_change`로 효과 영수증/중복 키를 기록한다. confirmed-success는 반복하지 않는다. timeout/답변 누락/프로세스 재시작은 uncertain으로 보존하고 대상의 실제 상태부터 대조한다. 외부 서비스와 로컬 DB에 걸친 작업은 완전한 exactly-once를 가정하지 않고 pending→confirmed/uncertain 및 실제 사후 대조로 처리한다.

## 검증 기준 및 적용 순서

1. 모든 입력 경로 매핑: 본부 relay, 프로젝트 작업창, 본부 배정, native 직접 지시, 자동 완료보고, 재시작 복구. 같은 current-task API를 사용하며 타 부서/HQ fallback 0건.
2. 이미 완료된 업무 직후 새 목표를 주고, 축약 전/후/재시작 후 목표·revision이 유지되는지 확인. userMessage와 functionCallOutput 각각 확인. 모델 호출을 수반하는 재현은 사용자 요청 후 수행한다.
3. 완료보고만 도착, 오래된 request_id, stopped, superseded, 전달 timeout, 잘린 입력, 응답 누락에서 쓰기/재발신/재배정이 0회인지 실행기 경계에서 확인.
4. 가격/재고 대상 수정 직전 stop 또는 새 revision이 들어오는 경합과 저장 후 답변 누락을 검증. 실제 상품 대신 격리된 대상/가역적 더미 효과를 사용한다. 사용자 승인 없이 실제 쇼핑몰 작업을 재실행하지 않는다.
5. 실제 runtime revision·hook trusted/실행 이벤트·영수증·외부 대상 사후 상태가 일치해야 전체 방지 완료로 판정한다. AST/소스 저장/시뮬레이션을 실제 업무 안전성 검증으로 바꾸어 보고하지 않는다.

공식 근거: https://learn.chatgpt.com/docs/hooks (2026-10-27 조회). SessionStart compact, PreToolUse 범위, hook review/trust 및 제한을 확인했다. API에 문서화되지 않은 플랫폼 내부 원인은 미확인으로 유지한다.

## 이번 관찰 결과

4개 Python 파일 AST 구문 확인을 통과했다. 실제 읽기 전용 조회에서 본부는 이번 전 부서 사고 분석 요청을, 정보조사팀은 그 사이 사용자가 새로 지시한 비즈호스트 데이터 조사 요청을 각각 반환했다. 이전 본부 자동 완료보고 ID로 조회하면 can_execute=false와 실제 최신 입력을 반환했다. 다른 팀 각각의 실제 축약 후 실행과 UI는 재현하지 않았다.

조회 시 서버 PID21420의 department runtime은 2026-10-27-pending-work-v2였다. 새 v3 원본 저장은 완료됐으나 실행 서버 반영은 대기다. 기존 native 부서는 새 프로세스로 복구 함수를 호출할 때 새 소스를 읽는다. 진행 중 턴/서버에 모든 변경이 이미 주입됐다는 뜻은 아니다. 별도 테스트·모델 호출·상품 변경·재전송·임의 중지/재시작 없이 확인했다.


## 재발 및 실제 복구 훅 연결 보강

추가 요청0eff23fd 기준 정보조사팀 턴01a17b40은 현재 커뮤니티 평판 조사로 시작했으나 축약01a17b52 직후 이미 끝난 원인 분석/레드컨테이너 전달로 돌아갔다. 최신 전달에는 이미 정확한 작업창 요청ID05ae7e6b와 현재 원문/복구 포인터가 있었으므로 요청 표식만으로 막히지 않은 것이 확인됐다. 이후 호출한 read_current는 BridgeError/current=false/can_execute=false였는데도 과거 목표를 계속했다.

같은 현재 대화 읽기 코드가 기본 실행에서 BridgeError, 허용된 별도 실행에서 성공하는 차이를 실제 확인했다. 운영체제/플랫폼 내부의 상세 거절 원인을 그 차이만으로 단정하지 않는다. 복구가 앱 named-pipe 재연결에 의존하지 않도록 로컬 원장+신뢰된 훅의 최신 native 입력을 사용하는 경로를 추가했다. 일반 저장 원문만으로 임의 실행 허가를 주지 않으며, 설치/신뢰 전 관측되지 않은 옛 입력은 보류한다.

공식 Codex UserPromptSubmit/PreCompact/SessionStart(compact)/PreToolUse/Interrupt의5개 사용자 훅을 준비했다. 등록된 Office/연결 프로젝트10개 대화만 대상으로 하고, 다른 프로젝트는 동작하지 않는다. 축약 직후 최신 입력을 developer context로 전달하며 실패하면 명시적 중지 응답을 반환한다. pending 복구/중지 경계에서 지원되는 도구는 실행 전에 거절한다. 현재 지시가 확실하지 않다고 옛 목표로 대체하지 않는다. 새 Office 입력이 도구 전달로 올 때 이전 중지에 갇히지 않게 현재 원문을 제시하고 옛 도구 호출을1회 보류한다.

사용자가 터미널 처리를 맡기라고 한 후 공식 CLI의 /hooks 검토 화면을 화면·마우스 조작 없이 열었다. 앱 내장 CLI의 패키지 오류는 공식 안내대로 --no-daemon으로 재시도했다. 다섯 정의의 정확한 이벤트·사용자 설정 출처·코드 경로를 각각 확인한 뒤 공식 메뉴에서만 신뢰/활성을 처리했다. 관련 없는5개 훅은 검토 대기로 남겼고 trust-all/신뢰 저장소 수동 편집/우회 옵션은 쓰지 않았다. 토글1회는 자동 검토가 대상 불명확을 이유로 거절해 실행되지 않았으며, 다시 전체 메뉴/선택한 PreCompact의 정확 출처·명령·Trusted/비활성을 읽어 대상 확인 후 정상 메뉴에서 활성화했다. 추가 사용자 승인 우회가 아니다.

TUI의5개 활성 설정은 확인했으나 관찰 당시 실제 관리 대화의 hook callback 파일은 없었다. 따라서 기존 데스크탑 세션의 새 설정 로드·실제 축약 복구·재발 방지는 아직 검증 완료가 아니다. 훅 오류/timeout/미신뢰는 플랫폼에서 거절 효과가 없을 수 있고, 임의 shell/Ego 명령의 업무 의미 검증도 이 코드가 보장하지 않는다. 새 테스트·유료 모델 재현·과거 조사 재실행은 하지 않았다.

사용자의 후속 ‘과부하’ 질문은 PC가 아니라 AI 대화/요원 처리 부하를 뜻했다. PC CPU/메모리를 조사한 것은 질문을 잘못 읽은 것이다. 실제 확인은 async 질문 회신 대기, 현재 relay sent/revision1, 부서 작업 대기열0건, native 주요3팀 active/고도 본부 idle, 서버의 두 조회0~16ms와 Office 대화 기록 저장이다. 이로부터 모델 서버 혼잡·문맥 길이 영향·요원 간 자원 경합을 확정하지 않는다. 누락됐다고 지적한 native 안내는 이후 Office DB에서 실제 저장을 확인했고, 현재 상태 안내도 기존 announce/publish_progress로 직접 기록했다. 화면 표시시각/클릭과의 인과는 미확인이다.


## 본부 재발과 결과 연결 경계 보강 (2026-10-27 후속)

현재 목표는 전 부서의 과거 업무 재개 문제다. 크롬 광고·회사 PC 종료 업무는 보류한다. 본부가 현재 광고 요청 요약을 무시하고 과거 PC 종료 발언에 답한 실제 오류를 확인했다. Office 현재 원문은 정확했으나 모델이 복원 후 원문 조회를 생략했다.

별도 코드 빈틈도 확인했다. HQ response_for는 무표식 답변을 현재 입력의 결과로 수용했고, department_tasks._response는 입력 없이 답변 속 표식만으로 완료를 인정했다. 이 경로를 제거했다. 수동 답변 게시·소스 반영 등록·새 부서 배정에는 require_current로 실제 최신 입력 ID/실행 상태를 확인한다. 예전 checkpoint를 즉시 can_execute=true로 돌려주는 우선 경로도 제거했다.

실제 실패 턴01a17bbd-e4ec-77e0-8550-0f3cbf96bcc7을 새 상관 코드로 읽어 PC 종료 오답은 invalid_final=true, 직후 정상 정정 답변은 false로 판정했다. 본부 현재 ID와 정보조사팀의 별도 최신 ID를 구별했고 옛 크롬 ID는 can_execute=false로 거절했다. 기존 실기록 읽기 확인이며 모델 재실행·업무 쓰기 테스트가 아니다.

설치/CLI 신뢰와 데스크탑 실행은 별개다. 등록 대상10개, 본부 managed=true 및 runtime hooks=true를 확인했으나 capture-start/fence/native/last-event의 실제 실행 흔적은 없었다. 설치만으로 작동했다고 판정하지 않는다. 훅에 본문 없는 last-event 메타데이터를 추가하고 reload-status에 실제 로드한 current-task/department 버전과 콜백 흔적 수를 표시한다. 훅 정의·신뢰 저장소를 직접 변경하지 않았다. 데스크탑이 왜 콜백을 실행하지 않았는지는 미확정이다.

한계: ID 일치만으로 같은 ID 안의 잘못된 의미 판단은 검출할 수 없다. 임의 native shell/Ego 작업 전체를 강제 격리한 것은 아니다. 실제 desktop callback과 compaction 후 목표 유지가 관찰되기 전에는 전면 해결이라 하지 않는다. 소스5개는 기존 idle reload broker에 등록했고 실제 실행 버전 관찰 전에는 반영 완료가 아니다. 테스트·추가 모델 호출·상품 변경·메시지 발신·과거 업무 재실행은 하지 않았다.

추가 출처 경계: native 사용자가 붙여넣은 과거 요청 표식은 현재 native 원문으로 읽으며 예전 DB 업무로 치환하지 않는다. HQ/부서 완료의 관리 입력 경계는 실제 codex_app 전달에서만 찾고, 답변 표식이나 인용문을 입력으로 승격하지 않는다. 새 최종 소스로 기존 오답/정정/현재턴 판정을 재조회했다. 상세 읽기 결과와5소스 영수증 해시는 local_backups/current-task-guard-20261027/verification.json에 있다.


## 축약 후 옛 직접 입력 재선택 사고와 실제 읽기 경계 (2026-10-29)

본부의 실제 18:38 축약 replacement_history에는 user 역할의 과거 입력281개가 남고 마지막 입력은 오전의 복구 지시였다. 진행 중인 Office 요청404cc70e는 도구 전달이므로 해당 user 역할 목록에 없었고, 현재 목표는 요약과 SessionStart 추가 문맥에 있었다. 실제 live read_current는 올바른 메신저 연결 원문을 반환했지만 모델은 옛 복구 지시를 현재 목표로 해석하고 불필요한 질문을 했다. 따라서 원문 조회 성공·요청ID 검사는 업무 의미 준수의 완전한 증거가 아니다.

공통 office_context_hook v4는 retained user 블록의 마지막 위치를 최신 근거로 삼지 않도록 명시하고, SessionStart에서 문맥 전달만으로 fence를 해제하지 않는다. 복원 후 지원되는 도구는 실제 현재 입력 읽기 전까지 보류하며, 좁게 지정한 office_current_task.py --read-current 읽기만 허용한다. 성공한 live 조회의 request_id/input_id/turn_id·활성 상태를 확인한 뒤에만 복구 보류를 푼다. 질문 답변·heartbeat 등 참고 입력은 보류를 해제하지 않는다. 새 실제 사람 입력과 Interrupt 경계는 보존한다. 설정·훅 신뢰·권한을 변경하거나 이미 완료된 업무를 재실행하지 않는다.

이 장치는 지원되는 도구 호출의 복구 순서를 제한한다. 도구 전에 출력한 일반 답변과, 올바른 원문을 읽은 뒤의 의미 판단 전체는 강제하지 않는다. 공식 문서도 훅을 모든 실행 경로의 완전한 통제 경계로 보지 않는다. 실제 다음 desktop SessionStart/PreToolUse 콜백과 축약 후 목표 유지는 아직 미검증이며, 구문·실기록 조회를 전면 재발 방지 검증으로 보고하지 않는다.


## 2026-10-29 automatic closure and reference-only input
Common-provider and native-runner previous exchanges are bounded reference data, not live user-role messages. Current input stays separate and intact; stored chats/attachments and actual pending execution records are preserved. Desktop relays carry a current-boundary notice. Completed journal history remains explicitly searchable but is excluded from automatic Notion title injection.

Department discovery observes only the latest real input boundary per native turn and excludes completed turns predating its initial cursor, including after later history reads. Retained history cannot create new work. Models still use the existing declare tool for a real deliverable when a rapid follow-up arrived before discovery.

Every task write derives lifecycle: received final closes the response; independently verified business success is separate; waiting/uncertain work remains unfinished. Original instructions are not replayed. A terminal task cannot reopen in place; explicit rework uses a new task ID. Desktop final writes automatically mark the conversation answered/non-resumable. Existing Notion state names and official readback/ACK flow remain.

These are delivery/registry controls, not proof of semantic correctness of every model answer. Persistent native host transcript is not rewritten. Actual compaction callbacks, model selection of goals, and cross-provider business/visual behavior require separate observation. No extra classifier, summary agent, model call, global hook, or auto-trust setting was added.


## 2026-10-29 verification and request-bound ACK
Reproduction/regression suite: 19 checks passed. A stale successful lookup from a different request could previously release a compact fence; hook v5 now requires the expected request ID to match. Actual production PreToolUse v5 callback was observed. One ephemeral read-only Luna/low session completed two current-goal responses around a real contextCompaction item/turn. This does not replay the production Office SessionStart(compact) end-to-end. No actual business work was executed. Evidence: local_backups/current-task-guard-20261029/automatic-closure-verification-summary.json.
