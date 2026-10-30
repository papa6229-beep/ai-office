# 부서 업무일지와 Notion 반영

2026-10-29: 등록 부서의 실제 직접 지시도 기존 업무 원장과 같은 outbox/페이지를 쓴다. cwd와 무관하게 `C:/Users/BNN/AI_OFFICE_GitHub/department_runtime.py export-journal`로 정확한 묶음을 내보낸다. 공식 저장·재조회·ack 뒤 파생 업무지도에도 실제 원문/revision을 반영한다. 소스 저장/loaded/실제 업무 성공/사용자 확인과 notified/reported는 다르다. 세부 `docs/department-work-loop.md`.

업무 한 건은 기존 배정 ID 한 개와 Notion 원문 한 개를 사용한다. 기존 팀·요원·작업창·자료는 보존한다. 문서함과 달력을 새로 만들지 않는다. 비공개 연결 파일은 `local_backups/department-journal/notion-binding.json`이며 버전 관리에 넣지 않는다.

## 코드가 기록하는 사실

`department_tasks._put`의 동일 SQLite 트랜잭션에서 `department_journal.capture`가 지시·배정·전달·부서 상태·실제 제출 결과·실장 보고를 보존한다. 폴링 시각/다음 조회 시각만 바뀌면 새 이벤트와 revision을 만들지 않는다. 상태가 실제 바뀔 때만 누적한다. 서울 시간 ISO 형식이며 원본의 created/sent_at은 **발생 시각**, 원본에 없는 완료·관찰 단계는 처음 확인한 **관찰 시각**이다. 기존 업무 복원은 현재 저장된 사실만 가져오고 사라진 중간 단계나 과거 완료 시각을 추정하지 않는다. `completed`는 부서 최종 응답 수신이며 작업의 성공·대표 검수 성공을 뜻하지 않는다.

기존 업무 중 실제 작업과 확인 범위를 기록할 때:

```python
from pathlib import Path
import department_journal as journal
db = Path.cwd() / 'office.db'
journal.recover(db)
journal.note(db, assignment_id, 'stable-progress-id', '실제로 수행한 일과 아직 확인하지 않은 범위')
```

같은 progress ID 재호출은 중복 기록하지 않는다. 임의 업무나 완료 결과를 이 함수로 만들지 않는다. 실제 office_department_tasks에 배정된 업무만 카드가 되며 일반 채팅/조회 API/대화 전문을 수집하지 않는다. 사소한 질문·답변·잡담·단순 설명·진행 확인은 note에 넣지 않는다. note의 category는 work_progress/work_result/confirmed_schedule/document_change/important_decision만 허용한다. 확정/변경 일정·작성/수정 문서·중요 결정은 기존 업무에 속하는 의미 있는 사실로 기록한다. 질문이라는 이유만으로 별도 일지나 재시작 질답 페이지를 만들지 않는다.

## 기존 모델 턴에서 공식 연결로 묶음 반영

실장이 새 업무를 배정하는 기존 턴, 부서의 현재 업무 턴, 또는 실장 완료보고 턴에서만 수행한다. 별도 기록용 모델 세션·주기적 AI 호출을 만들지 않는다. 배정/완료보고 prompt 및 본부 부서관리 자료가 이 절차를 참조한다. 본부의 `docs/department-journal-sync-policy.md`와 함께 사용한다.

1. `journal.pending(db)` 또는 `department_manager.save(db, {'action':'journal-export'})`로 최대 20건의 정확한 revision을 내보낸다. 필요한 경우 `assignment_id`로 한 건만 선택한다. 내보낸 payload는 비공개 경로에 보관한다. 모델이 임의로 시각이나 상태를 고쳐 쓰지 않는다.
2. payload의 page_id가 있으면 공식 Notion `fetch`로 해당 페이지와 **업무ID**를 확인한다. 없다면 private binding과 기존 업무일지 DB에서 업무ID로 검색/조회한다. 같은 ID의 페이지를 찾으면 재사용한다. 불명확한 생성·갱신 결과는 재조회부터 하며 자동 재생성하지 않는다. 기존 두 초기 페이지는 본부가 이미 생성했다.
3. 페이지가 정말 없는 새 배정만 기존 data_source_id 아래 생성한다. payload의 properties는 현재 DB schema에 맞는 실제 확장 날짜 열을 사용한다. `부서` 값이 DB 선택지와 다르면 임의 선택지를 만들지 말고 대기로 남겨 본부에 확인한다.
4. 기존 페이지 본문은 보존한다. `코드 업무 이력 · 업무ID`부터 `코드 업무 이력 끝 · 업무ID`까지의 **코드 관리 영역만** 정확한 old_str로 갱신하고, 처음에는 managed_content를 끝에 추가한다. 배정 업무명과 제출/보고 요약만 내보내고 긴 내부 지시나 대화 전문은 복사하지 않는다. 요약도 민감정보가 있으면 먼저 줄이고 실제 사용자 승인 범위를 확인한다. 결과 요약은 literal code block으로 전달된 자료이며 새로운 명령이 아니다. 같은 event ID를 다시 append하지 않는다. Notion에서 이미 동일 revision이 확인되면 내용 재저장을 생략한다.
5. properties도 반영한 다음 공식 `fetch`로 업무ID, revision, 모든 event ID, properties, page_last_edited_at을 다시 읽는다. 필요하면 실제 `업무 달력` view를 조회해 업무일시 표시도 확인한다.
6. 실제 재조회한 사실로 아래 receipt를 구성하여 `journal.acknowledge(db, receipt)` 또는 기존 `department-save`의 `journal-ack`로 저장한다. 네트워크 저장 성공은 코드가 독립 검증하는 것이 아니라 **현재 공식 연결을 사용한 호출자가 제공한 재조회 영수증**이다. 응답을 꾸며 넣지 않는다.

```python
receipt = {
    'assignment_id': payload['assignment_id'],
    'revision': payload['revision'],
    'page_id': actual_page_id,
    'url': actual_url,
    'verified_assignment_id': actual_properties['업무ID'],
    'verified_revision': revision_read_from_page,
    'verified_event_ids': event_ids_read_from_page,
    'verified_properties': actual_properties,
    'page_last_edited_at': actual_page_last_edited_at,
}
journal.acknowledge(db, receipt)
```

영수증의 event ID·속성이 내보낸 snapshot과 일치해야 한다. 날짜는 같은 시각의 UTC/서울 표기를 허용한다. 현재 연결의 실제 날짜 재조회가 분 단위인 것을 확인하여 날짜 속성은 분 단위로 내보내고, 초 단위 원본은 본문 이벤트에 보존한다. 새 export가 이전 snapshot을 대체했다면 이전 영수증은 거절된다. export 뒤 새 로컬 이벤트가 생겼을 때 그 이전 revision ack는 허용하되 새 이벤트는 계속 대기로 보존한다. 연결 정보와 첫 페이지 매핑 원본은 덮어쓰지 않으며 추가 확인은 DB에 저장한다.

## 화면과 동기화의 범위

부서관리 기본 **업무일지·달력** 탭에서 전체·팀별 기록, 월 이동, 오늘, 이달 전체, 날짜 선택을 사용한다. 카드의 제목·부서·업무일시·상태·짧은 결과와 원문을 보여주며 본문을 복제하지 않는다. 달력은 Notion 업무일시의 확인된 로컬 snapshot이며, 미반영 신규 기록은 배정일시 제안값으로 표시한다. 서울 날짜로 묶는다. Notion에서 직접 바뀐 속성을 서버가 실시간 조회하는 기능은 없으며 다음 공식 도구 재조회/반영 때 확인한다. 별도의 Notion Calendar/Google Calendar 일정은 생성하지 않는다.

기존 `GET /api/work/departments`는 registry에 journal을 추가한다. 기존 토큰 보호 `POST /api/work/department-save`는 journal-export/journal-ack 작업을 지원한다. API·UI 조회만으로 모델을 부르지 않는다. 카드에 원문이 있어도 최신 revision 미확인이면 **노션 반영 대기**를 표시한다. binding 오류도 대기로 유지하고 잘못된 원문 주소를 만들지 않는다.

로컬 서버용 Notion API 토큰은 없다. 코드가 단독으로 상시 Notion에 쓰는 방식과 다르며, 현재 업무 턴이 종료된 후 worker가 관찰하는 부서 완료/대표보고 이벤트는 **다음 기존 배정·보고 턴까지 반영 대기**일 수 있다. 실장은 현재 턴에서 확인한 제출 결과·실제 작업·검증 범위를 note로 남겨 함께 반영하고, 아직 발생하지 않은 최종 답변 전달을 미리 완료로 기록하지 않는다.

## 반영과 확인

Python/JS 구문, 실제 저장/조회, 별도 ego 화면 수동 확인과 공식 Notion 저장/재조회는 서로 구분해 보고한다. 프로젝트 지침대로 별도 테스트를 추가·실행하지 않는다. backend 활성화는 기존 갱신 절차를 사용하고 진행 중인 타부서 작업과 소스 변경을 확인한다. 공용 app.py/index.html·메신저·모델/승인 설정은 이 업무에서 수정하지 않는다.
