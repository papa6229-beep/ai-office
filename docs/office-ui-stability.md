# Office 화면·연결·반영 경계 (2026-10-29)

일시 API 실패는 WebView 실패가 아니다. native CheckRecovery는 연결 상태만 표시하고 회복됐다는 이유로 Reload하지 않는다. 실제 navigation/renderer/browser 실패는 별도로 보존하며 사용자가 화면 복구를 누를 때 한 번 처리한다. renderer는 Reload, 실제 browser process 실패는 같은 프로필의 WebView만 재생성한다. 원본 메신저/Office 서버를 종료하지 않는다. 열린 native 메신저 패널/owned 미리보기 창은 복구·수동 버전 적용 전 native에서 다시 확인한다. NativeMessengerHost/한비로 v5는 변경하지 않았다.

work-observer와 workdesk의 소스 반영 관찰은 페이지를 바꾸지 않는다. 새 UI는 비모달 안내와 사용자 버튼으로 적용한다. 입력/첨부/진행 작업/열린 작업창은 가드하며, native 앱은 실제 창 상태 확인 응답까지 필요하다. 초안과 업로드 완료 참조는 해당 탭의 사적 sessionStorage에 보관하며 진단·일지·서버로 수집하지 않는다. 입력 복원만 하며 보내기·파일 이동·이전 업무를 자동 재실행하지 않는다. 실제 process 실패 시 복구 범위는 별도로 사용자 확인하며 메모리상 미완료 파일 전송 성공을 추정하지 않는다.

## 제공 UI와 개발 소스

office_ui_assets는 실제 서버 bind 시 제공 버전을 고정한다. 후속 boot는 마지막 공개 버전에 현재 티켓에서 검증한 UI 변경만 합친다. frontend-only 반영도 같은 기존 broker에서 소스 확인 뒤 공개하며 열려 있는 화면은 그대로 둔다. HTML의 script/style URL은 동일 generation에 고정하고 이전 generation도 자체 해시 검증 뒤 제공한다. 루트의 개발 파일을 편집하는 것과 공개 버전·열려 있는 화면의 버전은 다르다. backend BOOT만 달라져도 UI를 새로고침하던 비교는 UI 버전 비교와 분리했다. DOM 검증은 실제 공개 소스 및 원래 client/nonce를 확인하며 실제 사용자 화면과 독립 성공으로 승격하지 않는다.

## 서버 작업과 유휴 반영

queued는 서비스 중단이 아니다. action은 같은 lock과 in-flight 카운트를 유지하고 실제 closing에만 잠시 거절한다. app.py의 기존 본부 POST admission 수정·_local_access·action_authorized를 보존한다. 서버 내부 요청/모델/정리/스캐너 작업은 정상 종료를 기다리지만, 무관한 모든 native 대화가 active라는 이유로 전체 서비스를 계속 대기시키지 않는다. 실제 발행 소유자의 최신 thread/turn/input/중지와 소스 해시는 직전에 다시 대조한다. 같은 담당자의 다음 FIFO는 발행 결과 전까지 유지한다. 프로그램을 강제 종료하지 않는다.

로드 증거는 실제 serving PID/nonce를 사용한다. source hash·함수 정의·공개 literal 상수 검사를 유지하고, 함수가 global로 대입하는 내부 상태(_AI_READY/_AI_THREAD)는 초기값 상수와 구분해 이름·유형·판단 근거만 남긴다. 후속 app-only boot도 변경 없는 활성 모듈을 실제 새 프로세스에서 다시 대조한다. 새 serving proof가 있으면 과거 PID 증거로 대체하지 않는다. 수정된 모듈은 명시한 유지 검토 및 새 source receipt/actual proof로 연결한다. 과거 load_mismatch 티켓은 수동 성공 처리하거나 삭제하지 않는다.

## 확인된 최소 렌더/목록 경계

동일 대화/선택 상세의 전체 DOM 재생성은 내용 키가 같으면 생략하고 상세 입력에 포커스가 있으면 보존한다. feedback 버전 계산은 제공 버전의 고정 해시를 재사용한다. 이 경계들의 실제 종합 지연 기여도는 별도이며 PC 과부하를 단정하지 않는다.

런처 최초 GET은 bounded timeout/읽기 전용 backoff와 명시한 재연결 버튼을 갖는다. 기존 카드가 있으면 실패 시 지우지 않는다. launch/save/refresh POST는 자동 재전송하지 않는다. 실제 조회에서 카드29개가 응답했으나 저장된 JS·제공된 JS·실제 열린 화면 버전이 다른 상태를 확인했다. 이전 client/goal을 지워 성공으로 꾸미지 않는다.

## 적용/검증 구분

구문 확인, 현재 실제 API/원장 조회, next-launch native 설치, 실제 backend loaded proof, 공개 UI generation, 실제 GUI 확인은 별개다. 본부가 C# 컴파일·다음 실행 설치를 맡고 개발은 backend/frontend 등록·실제 반영 근거를 맡는다. 새로운 테스트/유료 모델/가짜 업무/업무 재실행/화면 입력은 사용하지 않는다. 현재 결과와 미확인은 기존 업무일지와 local_backups/office-ux-stability-20261029에 기록한다.

현재 서버가 오래된 복구 parser인 경우, 표식 없는 본부 자료가 종료 확인을 가릴 수 있다. 현재 원문이 있는 동일 턴에서만 참고 자료를 건너뛰며 과거 턴으로 돌아가지 않는다. 기존 서버와의 제어 확인 자료는 첫 줄에 현재 [AI OFFICE 요청 ID]를 유지한다. 이는 원래 업무를 재전송·재실행하거나 새 권한을 만드는 것이 아니다.
