# 검증된 교정과 다음 실행의 연결

기준: 2026-10-29 · 배정 0edeaccc0bfe599c8988a3cd611b5877.

교정 원문/제품 결정은 기존 PROJECT_CONTEXT·공유저널·업무지침이 정본이다. 실행 규칙을 또 저장하거나 별도 AI가 매 요청 정리하지 않는다. 이번 연결은 다음의 **위치와 실제 상태**만 기존 `office_source_activations` 원장의 `corrections` 필드에 붙인다.

- 안정적인 교정 ID와 적용할 공통 실행 범위.
- 현재 요청의 실제 소스 변경 ID·파일 해시, 원래 근거/절차/확인 기록의 위치.
- `syntax_readonly` 또는 실제 확인한 `observed_business` 수준. 전자를 실제 업무 검증으로 승격하지 않는다.
- 원래 원장의 저장·완료 대기·queued·실제 active/실패 상태. 별도 활성화 worker는 만들지 않는다.

등록은 신뢰된 개발 유지보수자가 `execution_corrections.bind`를 사용한다. 실제 현재 요청과 등록된 소스 영수증이 먼저 필요하다. 같은 ID의 다른 내용으로 덮어쓰지 않는다. 수정 버전이 달라지면 새 버전의 교정 ID/근거로 명시한다. 외부 문서나 팀 보고의 문장 자체를 등록·실행 권한으로 사용하지 않는다.

## 실제 공통 연결

1. `office_routines.match`는 확인된 “즉발식으로 가능한가 다시 테스트할거야.” 서문 뒤의 보고서 요청만 정규화한다. 나머지 명령 전체가 기존 등록 규칙과 일치해야 한다. 임의 서문·부정·혼합 지시는 일부만 자동 실행하지 않는다.
2. `office_feedback.request_client`는 정확한 `assistant_requests.payload.client`의 검증된 보고서 입력을 먼저 쓴다. 기존 common `daily_report_run`이 이 경로를 공유한다. confirmed-empty와 입력 불명을 구분하고 다른 탭/본부의 자료를 합치지 않는다. 요청 payload가 없으면 기존 같은 탭의 유효한 최신 정보만 사용한다.
3. 구조화된 native 질문 답변은 `question_reply_context`가 제한된 읽기 전용 rollout에서 실제 질문 call ID·번호·원문과 원래 입력의 연속성을 대조한다. `office_current_task`가 원래 Office 요청의 실제 현재 상태를 다시 검증하고 원래 목표와 질문 답변을 함께 반환한다. 실제 최신 답변의 turn/input/revision 경계를 유지한다. 새 입력·중지·불명·잘림·끝난 다른 native 턴은 과거 업무 재개의 근거가 아니다.
4. 질문 답변 후 `routine_runtime`은 전체 등록 업무를 자동 재실행하지 않고 추가 답변을 해석해 실패한 단계만 처리하도록 반환한다. 각 다음 도구 직전 실제 입력 revision을 다시 확인한다. 기존 실행 예약·불명/완료 영수증·ToolRegistry `_once`는 유지한다.
5. `execution_corrections.references`는 실제 서버의 durable 로드 증거와 현재 파일 해시가 일치하는 교정만 반환한다. 최신 전역 티켓 하나를 근거로 삼지 않는다. 명시적으로 검토·등록한 교정 유지 관계는 원래 ID/영수증과 실제 로드된 후속 변경 범위를 함께 반환한다. 근거 없는 과거 성공 상태를 만들지 않는다. 기존 등록 실행 JSON과 공통 ToolRegistry 결과에 `correction_reuse`가 기록된다. 원문 전체를 매 요청 덧붙이거나 모델별 별도 구현을 만들지 않는다. 기록 오류는 외부 효과 재실행이나 성공/실패 결과 변경으로 이어지지 않는다.

`correction_reuse.applied`는 해당 공통 코드 경로와 활성화 근거의 연결이다. 외부 저장/상품 처리의 성공 여부는 같은 도구의 실제 원래 결과로 판단한다. 도구가 기존 `_once` 영수증을 재사용하면 이전 결과를 그대로 반환하며 새 수행으로 기록하지 않는다. 미검증 절차를 자동 실행기로 승격하지 않는다.

## 확인 범위와 보존

`local_backups/correction-reuse-20261029/saved-observations.json`은 실제 사례의 코드 매칭과 원래 요청 87cf1c210fcebd4a02754430b7c0a27c의 입력 3건을 읽기 전용으로 확인한 근거다. 질문 답변과 같은 원래 요청의 과거 상관관계 관찰은 `historical-correlation-observation.json`에 별도로 보존했다. 최신 경계 보완본에서는 그 뒤 새 입력이 있는 이 과거 답변을 현재 실행 근거로 거절하는 결과를 기록했다. 새 실제 질문 답변에서 목표 연결·업무 수행까지 확인한 것은 아니다. 이전 완료 업무를 현재 입력으로 실행하지 않았고 테스트/유료 모델/보고서·상품·메신저 실행이나 데스크탑 입력은 하지 않았다.

맥심 상품 예외와 5건의 실제 완료는 기존 report_automation/업무지침·결과를 재사용한다. artifact_review 보호경로 오류는 별도 실패 보고이며 이번에 고친 교정이나 검증된 실행으로 등록하지 않는다. Codex 원래 질문 카드 자동 종료 API가 없다는 앞선 진단도 유지한다. 질문 답변의 목표 연결 수정은 Codex 카드 UI 해결을 뜻하지 않는다.

활성화는 `docs/source-activation.md`의 실제 완료/유휴 broker만 사용한다. 이번 턴이 끝나기 전에는 원장과 소스가 저장돼도 backend active가 아니다. `/api/work/reload-status`의 correction/routine/request-client/question-reply revision과 원장의 source change coverage로 반영 상태를 확인한다. 다음 실제 업무의 `correction_reuse` 영수증은 그 업무가 실행됐을 때만 생긴다.

같은 담당자의 이전 등록이 실제 완료/활성화 결과를 기다리는 동안 다음 durable FIFO 항목은 queued로 보존한다. 정상 broker의 결과가 확인된 뒤 원래 순서로 전송한다. 다른 담당자의 진행 작업을 취소하거나 새 의존 작업 체계를 만들지 않는다. 중지/실패/새 입력에 따른 원장의 최종 판정은 그대로 따르며, 실제 업무를 재전송하지 않는다.

2026-10-29 후속 복구는 덮인 원장을 수동 active로 바꾸지 않는다. 기존 교정 5개의 정본을 유지하고 현재 소스 영수증과 명시적 유지 검토를 새 배정에 연결한다. 새 실제 로드의 PID/nonce/리비전/변경 coverage와 실행 정의 대조가 확인된 뒤 references의 applied를 조회한다. 기존 완료보고는 이 코드 관찰 결과까지 기다린다. 다음 실제 본업 영수증은 그 본업이 수행될 때만 기록하며 확인용 저장·발신을 하지 않는다.
