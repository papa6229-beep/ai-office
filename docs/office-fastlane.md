# 에이메스 파일·웹 묶음 실행

## 실행 선택

사용자 요청을 한 번 해석해 정형 단계를 묶는다. 이미 연결된 기능을 다시 조사하거나 파일/필드마다 모델 호출을 반복하지 않는다. 새 절차는 실제 화면/경로를 한 번 확인한 뒤 코드를 구성한다. 기존 성공 효과는 재실행하지 않는다.

- 파일/폴더 생성·이동·이름 변경: `files_browse/locations`로 확인 → **files_batch** 한 번.
- 같은 폴더의 파일을 확장자/파일명으로 분류: **files_sort**. 규칙을 순서대로 한 번 적용하고 180파일씩 코드로 실행한다. 하위 폴더·미분류 항목은 보존한다. 단어 의미/이미지 내용 판단은 별도로 필요한 경우에만 AI가 맡는다.
- 수신 기록에 이미 확인된 파일은 기존 `received_files_organize`/메신저 다운로드 실행기를 재사용한다. 아직 받지 않은 첨부를 로컬 파일로 취급하지 않는다.
- 새 웹 업무: **ego_status → ego_batch**. `open/inspect`에서 실제 CSS와 상태를 좁혀 확인한 뒤 로그인·클릭·선택·대기·결과 확인을 하나의 steps에 묶는다. `space_ref`로 이 어댑터 소유 공간을 재사용한다.
- 기존 보고서/시연 등의 Chrome 전용 실행기는 여전히 별도다. Windows 기본값을 에고로 바꾼 사실이나 새 어댑터 추가를 모든 기존 코드의 에고 전환으로 설명하지 않는다. 실제 포팅 없이 기존 실행기를 새 브라우저에 억지로 연결하지 않는다.

## 에고 단계

`open`, `new_page`, `inspect`, `fill`, `select`, `click`, `press`, `wait_url`, `wait_selector`, `wait_any`, `read`, `expect`, `login`, `mark`, `otp_fill`.

- 기본 page는 `p1`, 직전 new_page 결과는 `$last`이다. 실제 반환 라벨만 재사용한다.
- `inspect(query,limit)`는 입력 값 없이 작은 컨트롤 목록을 반환한다. 동일 name/id가 여럿이면 selector를 추측하지 않고 더 좁혀 확인한다. iframe 수가 보이면 최상위 폼과 구별한다.
- `login`은 `login_accounts`의 account_ref와 최상위 username_selector/password_selector를 쓴다. 정확히 일치하는 HTTPS 사이트의 실제 password 필드에서만 보관 값을 메모리로 전달한다. 일반 fill로 password를 입력하지 않는다. 값은 모델·스크립트·명령행·영수증에 반환하지 않는다.
- `when_selector`가 보일 때만, `unless_selector`가 안 보일 때만 해당 단계를 실행한다. 로그인 화면이 없다면 로그인/클릭 단계를 건너뛸 수 있다. 조건은 실제 관찰한 최상위 CSS다.
- 비동기 로그인 뒤 `wait_any(selectors=[인증입력CSS,로그인완료CSS])`로 어느 실제 화면에 도착했는지 기다린다. 조건을 기다리지 않고 즉시 다음 폼을 찾지 않는다.
- `mark(name='challenge')`로 실제 인증 요청 클릭 직전 시각을 기록한다. `otp_fill(selector,after_mark='challenge',match=사이트나발신자문구,digits=실제자릿수)`가 최신 해당 알림을 코드로 기다려 입력한다. source로 앱도 한정할 수 있다. 기존 인증을 이어갈 때만 관찰한 since 시각을 명시한다. 재전송은 자동으로 하지 않는다. OTP 자체는 반환하지 않는다.
- 결과 확인은 `expect(selector,equals 또는 contains)`나 실제 목표 주소의 `wait_url`로 지정한다. 클릭 영수증만으로 저장 성공이라 말하지 않는다. 이후 쓰기 동작이 있으면 앞선 verified는 초기화된다. `read`는 구체적인 최상위 CSS에 한정하고 password 값은 읽지 않는다.
- `finish=true`일 때만 공식 finish를 호출한다. 계속 확인할 실제 결과 페이지가 있으면 keep에 명시한다. finish 후 라벨을 재사용하지 않는다. 사용자 소유/중지/제어권 오류에서 claim이나 다른 공간으로 우회하지 않는다.

## 네이티브 Codex

같은 Python 모듈을 직접 호출하거나 `python office_fastlane.py files|sort|ego PRIVATE_PLAN.json`을 사용한다. JSON은 비공개 local_backups 아래에 만들고 실제 사용자 요청ID를 root_request_id로 연결한다. 각 묶음의 request_id는 새 UUID hex이며 불명확한 결과를 새 ID로 자동 재전송하지 않는다. status 명령은 같은 요청의 영수증만 조회한다.

```python
import uuid
import file_operations as files
import office_fastlane

# 현재 사용자가 지정하고 확인한 실제 경로를 한 번 관찰한다.
source = files.observe(confirmed_source_path)
result = office_fastlane.sort_files(
    db_path, source,
    [{'folder': '이미지', 'extensions': ['jpg', 'jpeg', 'png']},
     {'folder': '문서', 'extensions': ['xlsx', 'pdf']}],
    uuid.uuid4().hex, root_request_id=current_office_request_id)
# 실제 state/classified/skipped_conflicts/unmatched와 개별 job 기록을 보고한다.
```

에고 직접 호출은 `ego_workspace.run(db,request_id,steps,space_id=기존반환값,root_request_id=현재요청ID)`이다. 이 경로의 login은 모델에 비밀값을 읽지 않고 `credential_vault.listing()`의 id를 account_id로 쓴다. common tool에서는 account_ref를 사용한다. 요청 스크립트에서 Ego/Playwright의 새 프로필을 만들거나 쿠키를 복사하지 않는다.

## 결과·중지·시간

- 각 실행 영수증의 `mode=code, model_calls=0`은 **해당 코드 묶음** 범위다. 요청 해석/최종 답변을 포함한 전체 대화가 무료라고 설명하지 않는다.
- 파일 묶음은 기존 실제 이동·복사 검증·충돌·복구/중지 기록을 사용한다. 삭제는 기존 files_plan의 명시적 삭제 경로로 분리한다. partial/stopped/error를 완료로 보고하지 않는다.
- 같은 ID의 중복/불명확 실행은 기록만 반환한다. partial 파일 작업의 명시적 재개는 기존 files_job_run이다. 웹 timeout/stop에는 진행 중 한 동작의 늦은 효과가 남을 수 있으므로 현재 화면/완료 단계부터 확인한다.
- 단순 같은 드라이브 이동은 기존 엔진의 이동을 사용하며 다른 드라이브의 복사·무결성 확인을 생략하지 않는다.
- 코드 묶음은 파일 계획/실행·분류, 에고 연결/운송·각 웹 단계 시간을 기록한다. 기존 실행 경로는 연결·지침 준비·전달·응답 대기를 나눠 기록한다. 응답 대기는 모델·도구·사용자 대기를 포함하므로 순수 추론 시간이나 크레딧으로 환산하지 않는다. 중첩 구간 시간을 더해 전체 시간으로 설명하지 않는다.
- `/api/work/fastlane-status`는 새 서버의 도구 등록/실행기 존재만 확인한다. 실제 이동·로그인·저장 성공을 검증하는 API가 아니다.

## 현재 확인 범위

사용자의 2026-10-24 속도 개선 승인으로 구현. Python/Node 구문·모듈 로드와 도구 등록, 공식 에고 API 도움말을 확인한다. 이 문서의 예시로 사용자 파일을 이동하거나 기존 시연·로그인·상품 변경을 실행하지 않는다. 실제 업무의 전후 속도 및 새 폼 호환성은 사용자 실무 확인 전이다.
