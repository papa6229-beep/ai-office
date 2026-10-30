# PC 독립 Office 사전 확인 도구

현재 제품은 그대로 보존한다. 이 폴더는 **이전 전 읽기 확인 도구**이며 클라우드 실행기나 배포 완료본이 아니다. 현재 회사PC의 서버/DB/연결을 바꾸거나 모델을 호출하지 않는다.

## 준비된 실제 경로

`preflight.py inventory`는 기존 DB를 `mode=ro`로 열고 실제 등록 본부·팀/요원의 식별자와 허용된 모델 설정만 private 보고서에 보존한다. 대화 본문, 로그인/토큰/pipe 경로는 내보내지 않는다. 이 보고서도 공개 Git에 올리지 않는다.

```powershell
python office_cloud/preflight.py inventory --root C:\Users\BNN\AI_OFFICE_GitHub --output local_backups/pc-independent-office-20261027/source-inventory.json
```

`preflight.py target`은 이후 승인된 호스트에서 공식 로그인/이력 handoff가 끝난 뒤 사용하는 읽기 전용 확인이다. 기존 CLI child만 띄우며 `initialize/account/read/thread/read/thread/turns/list`만 호출한다. 원래 ID를 찾지 못하면 실패하고 빈 대화를 만들지 않는다. 계정 이름/이메일이나 대화 본문을 보고서에 저장하지 않는다. 본부/팀을 생성·실행하거나 기존 로컬 앱을 종료하지 않는다.

```sh
python office_cloud/preflight.py target --manifest <private-source-inventory.json> --cwd <approved-office-root> --output <private-target-result.json>
```

위 target 명령은 현재 PC에서 검증용으로 실행하지 않았다. 모델·기존 이력·실제 작업의 의미 보존, 브라우저/도구 연속성, 집PC 왕복 검증은 별도 단계다. `ready_for_cutover`는 이 도구에서 항상 false다.

## 실제 계정 확인 결과 · 2026-10-27

- 사용자 명시 승인으로 Northflank 공식 GitHub 로그인, 읽기 전용 이메일 제공(`user:email`), 무료 개인 계정 생성·약관 동의 제출을 완료했다. 각 자동 승인 거절 뒤 해당 단계의 구체적인 승인을 받은 다음 진행했다.
- 계정의 Developer Sandbox 화면은 **첫 빌드·배포부터60분 실행, 이후 계속 실행하려면 카드 검증 필요**라고 안내한다. 무료 요금표의 월 자원 환산액만으로 무카드 상시 실행을 보장할 수 없었다.
- 카드 등록 금지 조건에 따라 Northflank는 현재 상시 호스트 후보에서 제외한다. 팀/프로젝트/서버를 생성하거나 빌드·배포하지 않았으므로 실행 창도 시작하지 않았다. 운영 소스·DB·인증자료 업로드 없음.
- 추가 후보의 현재 공식 조건도 설계 문서4.4절에 기록했다. 조건을 통과한 대체 호스트는 아직 없다. 비밀번호·쿠키·refresh token을 읽거나 복사하지 않았다.

## 남은 구현·활성화

조건을 충족하는 호스트와 기존 thread 이력 접근을 확인한 뒤에만 기존 desktop IPC를 대체할 runtime/팀 도구 연결을 확정할 수 있다. 현재 본부·팀 식별자와 history가 준비되지 않았는데 새 runtime이 자동 새 대화를 만들도록 하지 않는다.

집PC가 주 접속 환경이며 휴대폰은 보조다. 기존 Codex Cloud 입력의 PC독립 연결은 아직 확인되지 않았으므로 그 입구가 유지된다고 약속하지 않는다. 회사PC-off 상태에서도 본부·팀이 실행되는 실제 왕복이 최종 완료 조건이다.
