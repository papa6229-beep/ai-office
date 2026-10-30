# 등록 부서 공통 업무 흐름 (2026-10-29)

기존 `office_department_tasks`가 정본이다. 본부 배정은 기존 FIFO, 직접 산출물 업무는 실제 thread/turn/input ID로 같은 원장에 연결한다. 작업창의 성공한 전송, 등록 native 대화의 현재 입력 관찰, 현재 수행 모델의 선언이 같은 경로를 사용한다. 기존 배정·참고 보고·시작 시 완료된 과거 native 턴은 직접 업무로 재수집하지 않는다. 질문·잡담·조회도 새 업무가 아니다. 모호한 산출물 의도는 현재 수행 모델이 한 번 판단하며 별도 분류 모델을 부르지 않는다.

추가 지시와 정확한 질문 call ID의 답변은 같은 업무에 합친다. 명시한 중지/보류는 대기로 보존하고 자동 재개하지 않는다. native 최종 응답, 소스 저장, 실제 loaded proof, 실제 업무 효과, 사용자 확인을 구분한다. 최종 응답 수신은 독립적인 업무 성공 검증이 아니다.

`_put`의 동일 트랜잭션이 기존 journal·재생성 가능한 구조화 지도·고정 ID 완료 inbox를 갱신한다. 소스 업무는 기존 실제 활성화 gate를 공유한다. 본부가 바쁘면 다음 정상 입력의 요약 delta로 전달한다. 실제 context receipt는 `notified`, 본부 최종 답변은 `reported`이다. 기존 idle 보고도 유지하며 전송 불명은 재전송하지 않는다. 보고/이력은 새 권한이 아니다.

## 실행과 조회

기존 Python으로 `C:/Users/BNN/AI_OFFICE_GitHub/department_runtime.py`를 실행한다. 기본 DB도 Office 절대경로이므로 The Hand 등 다른 cwd에서 같은 원장을 사용한다.

```text
status
search --query 업무단어 --limit 5
inbox
route --query 현재목표 --context 관련기능
declare --request-id 실제현재ID --title 짧은업무명 --kind development
assign --request-id 현재본부ID --thread-id 조회한담당ID --title 업무명 --preserve 유지범위 --completion 완료기준 --context 관련기능
export-journal
ack-journal --receipt-file 공식재조회영수증JSON절대경로
```

공통 도구는 `department_work_search/inbox/route/assign/declare`이다. 검색·정렬은 코드, 의도/예외는 현재 본부 모델이 한 번 판단한다. 기존 등록 실행기가 일치하면 우선 반환한다. 이력 ID는 참고이며 실제 현재 본부 입력과 새 고정 ID로 기존 한 담당에게 비동기 접수한다. 본부는 접수 뒤 대화로 돌아온다. 단일 글자/일반 작업어만으로 실행하지 않으며 중요한 대상·완료 결과만 최소 확인한다.

업무지도는 팀·실제 담당·프로젝트·원래 목표·상태/결과·남은 일·소스/산출물·Notion 원문/revision·확인 수준을 보존한다. 확정 절차는 기존 활성 부서 자료/제품 설명서, 교정은 `execution_corrections.references`, 파일/활동은 `work_log_query`가 정본이다. 개별 결과를 공통 절차로 승격하거나 임베딩 DB/대화 전문 정본을 만들지 않는다.

## Notion·검증

기존 비공개 DB·카드·달력·수동 메모와 같은 업무ID의 페이지를 유지한다. 직접 업무도 동일 outbox로 들어간다. 정상 업무 턴에서 공식 Notion 저장 → fetch → 정확한 event/속성/revision acknowledge를 수행한다. 백그라운드 토큰/API·기록용 AI는 없다. 로컬 저장은 Notion 성공이 아니다. `status`와 검색에서 pending/synced 및 확인된 revision을 조회한다. 종료/활성화 뒤 새 이벤트는 다음 연결된 정상 턴까지 대기할 수 있다.

구문·기존 실제 기록/담당 조회·공식 Notion 재조회는 새 직접 업무의 시작→종료→본부 보고 완주와 다르다. 가짜 업무·종료 업무 재실행을 검증용으로 만들지 않는다. 실제 활성화와 코드 조회는 기존 완료 후 확인 결과에 별도로 기록한다.
