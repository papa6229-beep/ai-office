# 업무 비서 첫 실행 흐름 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement each owned unit. User approved execution in the current conversation.

**Goal:** 채팅 업무 등록 → 자료 연결 → 메모·진행 기록 → 근거가 있는 일일 보고서 초안을 실제로 사용할 수 있게 연결한다.

**Architecture:** 기존 SQLite tasks 컬럼은 보존하고 업무 이력·자료·대화·보고서·요청 중복 방지 테이블만 추가한다. 업무 변경은 하나의 서비스에서 처리하고 기존 등록·상태 경로도 같은 서비스를 사용한다. 모델은 제한된 업무 의도를 해석하며 파일 이동·전송·실행 권한을 갖지 않는다.

**Tech Stack:** Python 표준 라이브러리, SQLite, 기존 Gemma 연결, HTML/CSS/JavaScript.

**Spec:** [승인된 제품 방향](../../../AUTOMATION_DIRECTION.md)

## Global Constraints

- 사용자 DB·설정·파일 보존. API 키·업무 기록 Git 제외. 테스트는 생성한 임시 DB와 파일만 사용.
- 기존 tasks 5개 컬럼과 기존 기능 유지. 추가 라이브러리·계정 로그인·상품화 작업 없음.
- 테스트 선행. 중복 요청은 한번만 반영. 모델 호출 중 DB 쓰기 트랜잭션 유지 금지.
- 변경 경로 Host·Origin·토큰 검증. 모델이 만든 경로와 임의 업무 ID로 작업하지 않음.

## Review Focus

- 기존 업무와 완료 시점을 새로 생성한 것으로 오인하지 않는다.
- 같은 요청 재전송·동시 요청에서 중복 업무나 이력이 생기지 않는다.
- 동일 이름 업무·선택 업무와 지시의 충돌은 질문하고 임의 변경하지 않는다.
- 자료 연결은 메타데이터 등록이며 실행·내용 전송·이동을 발생시키지 않는다.
- 보고서에는 근거가 있는 활동과 현재 상태만 포함하며 과거 완료 시점을 추정하지 않는다.

## Owned units

- [x] Backend coder: `work_assistant.py`, `tests/test_work_assistant.py`. WorkAssistant.overview/perform/resource_path. 임시 DB의 기존 행 보존부터 검증하고 등록·메모·상태·자료·보고서·채팅·중복·경로 보호를 테스트한다.
- [x] Frontend coder: `workdesk.html`, `workdesk.js`, `workdesk.css`. 오늘 업무·지시창·선택 업무 자료와 메모·보고서. 실제 응답 후 결과 표시, 실패 재시도 요청 ID 유지, 빈 화면 안내와 모바일 지원.
- [x] Root: `app.py`, `index.html`, `updater.py`, `tests/test_work_api.py`, 안내 문서. 새 API와 구형 업무 변경을 서비스에 연결하고 기본 화면을 업무 데스크로 전환한다. 생성한 파일로 화면에서 등록 → 연결 → 기록 → 보고서 → 새로고침 유지까지 검증한다.

## Acceptance

- [x] 기존 및 새 자동 테스트 통과. 실제 Gemma 명령 해석은 생성한 업무 DB에서 확인.
- [x] 시험용 화면에서 한 업무의 생성·자료 연결·메모·상태 변경·보고서 저장·재조회 성공.
- [x] 실제 앱에서 기존 업무·파일·설정 보존과 새 기본 화면 확인. 일반 Chrome 실행.
- [x] 소스·안내만 커밋하고 GitHub 반영. 메신저와 PC 자율 실행은 이번 구현 완료로 표시하지 않음.
