# AI OFFICE·The Hand 공통 스킬 실행

기준: 2026-10-28 · 현재 요청 386dc519ec96436fa6ca3ba95afd2b31.

기존 모든 등록 부서·본부/총괄 작업창에서 현재 업무에 필요한 스킬을 능동적으로 선택한다. 스킬마다 새 모델이나 별도 작업창을 만들지 않는다. 설치 원문과 실제 도구를 연결하는 공통 진입점은 `skill_runtime.py`이며, 사용자 공용 `C:/Users/BNN/.agents/skills/office-skills/SKILL.md`가 사용법을 제공한다.

```powershell
$skillPython = 'C:/Users/BNN/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$skillEntry = 'C:/Users/BNN/AI_OFFICE_GitHub/skill_runtime.py'
& $skillPython $skillEntry list --query '현재 업무'
& $skillPython $skillEntry read '제공자:스킬명'
& $skillPython $skillEntry status '제공자:스킬명'
& $skillPython $skillEntry run 'local:firecrawl' -- search '조사 질문' --limit 3 --json
& $skillPython $skillEntry run 'codex:skill-creator' --script scripts/quick_validate.py --python-runtime office -- '스킬 폴더'
```

호출 작업 폴더는 그대로 둔다. `tools/office-skill.cmd`는 편의 런처다. 복잡한 인수는 Python/Node의 배열 호출 또는 위 PowerShell 직접 호출을 사용한다. Office ToolRegistry 경로의 `skill_list`, `skill_read`, `skill_runtime_status`, `skill_run`도 같은 코드를 쓴다. 도구 경로는 서버가 발행한 출력 작업공간을 쓰고 기존 효과 중복 방지 경계를 유지한다.

## 선택과 실행의 의미

| 종류 | 실제 수행 | 확인할 결과 |
| --- | --- | --- |
| 절차/판단 지침 | 원문을 읽고 현재 선택 모델이 현재 업무에 적용 | `instruction_ready` 이후 실제 업무 산출물 |
| CLI/스크립트 | 기존 Python/Node·Firecrawl/Ego/watch 또는 선택 스킬/현재 작업 폴더의 도우미 | 실제 exit/state, 출력 파일, `.office-skills/runs/<id>/receipt.json` |
| 공식 연결 도구 | 현재 대화에 제공되는 이미지·Notion·Drive 등의 공식 도구 | 도구 연결/권한과 실제 외부 저장·조회 결과 |

원문만 있다는 이유로 모든 외부 계정이 연결됐다고 간주하지 않는다. 전용 실행기나 일반 PATH 명령이 없으면 공통 Python 진입점을 먼저 사용한다. 현재 모델이 적용하는 지침에 별도의 실행 바이너리가 없다는 것은 장애가 아니다. 새 인증·구매·발신은 기존 사용자 승인/도구 경계를 따른다.

Python은 기존 primary와 Office 3.10 중 선택한다. 각 버전의 바이너리 패키지 경로를 섞지 않는다. 자식 Python은 UTF-8 모드다. Node ESM은 해당 스킬의 문서에 따라 기존 node_modules를 연결한다. 장기 서버의 lifecycle은 기존 native 실행 경로에서 관리한다. 공통 runner가 별도 모델 세션을 만들지는 않으며 도우미 자체의 외부 호출 여부는 해당 스킬/현재 업무에서 판단한다.

## 출처와 전달

- 사용자 `.agents/skills`, `.codex/skills`, `.claude/skills`, builtin, plugin cache, 호출 프로젝트의 스킬을 검색한다. 같은 제공자·이름은 사용자 원본 우선, 숫자 버전 최신순, 원본 상위 경로 순으로 선택하고 다른 후보도 보존한다. 서로 다른 제공자의 같은 별칭은 고유 ID로 선택한다. 백업은 legacy로 구분한다.
- 스킬 원문·상대 참조는 실제 선택 폴더에서 읽고, 결과는 호출 작업 폴더에 기록한다. 실패·timeout·출력 스트림 미확정은 자동 성공/자동 재실행으로 바꾸지 않는다.
- 기존 9개 등록 요원과 The Hand 총괄, 총 10개 창의 부서관리 증분 문맥에 같은 진입점을 연결한다. 정상 요청 전송 때만 전달 영수증을 확인 처리한다. 이 환경 구축을 이유로 진행 중인 대화를 중단하거나 별도 업무 메시지를 보내지 않는다. 진행 중인 모델 턴의 스킬 목록은 자동 갱신을 보장하지 않으며 다음 정상 입력에서 공통 안내를 받는다.
- 공용 gateway는 `tools/install-office-skills.ps1`로 설치한다. 자신의 이전 설치 해시와 일치하는 파일만 갱신하고 사용자 수정은 보존한다. 모델·권한 설정이나 전역 PATH는 변경하지 않는다.

## 확인 근거

`local_backups/shared-skills-20261028/runtime-observations.json`에 구문 확인, 현재 148개 고유 제공자 ID/190개 원문 발견, Office/D:/godo 동일 카탈로그, 10개 창 안내 생성, 실제 공통 runner의 스킬 형식 검사·Firecrawl 버전·Ego 버전·watch 도움말 exit0을 기록했다. 창별 실제 본업에서 자발적으로 스킬을 선택·완주한 결과나 모든 외부 서비스 인증을 이 점검으로 주장하지 않는다. 추가 테스트·유료 모델 호출은 하지 않았다.

실행 서버의 `skill_runtime_revision=2026-10-28-shared-skills-v1` 활성 상태를 확인했다. 등록 소스 6개와 별도 반영 영수증은 같은 폴더에 보존한다. 사용자가 앞서 승인한 서버만 재시작하는 범위로 반영했으며 네이티브 앱은 유지했다. 기존 project-panel 요청의 등록을 현재 worker가 유휴 broker로 자동 소비하지 않아, 기존 반영 helper를 사용했다. 정상 유휴 drain을 관찰한 것으로 기록하지 않는다. The Hand 실제 cwd인 D:/godo에서도 공통 run으로 Firecrawl 버전을 조회해 exit0과 해당 폴더의 영수증을 확인했다.


## 로컬 산출물 확인 경로 (2026-10-29)
로컬 HTML·이미지·영상·PDF는 웹 주소가 아니다. 공통 `local_artifact_inspect(path)` 또는 `artifact_review.py inspect --path 전체경로`가 파일 존재·크기·SHA256·HTML 요소 요약을 읽고 기존 파일 표시 도구 인수를 반환한다. 이 도구는 브라우저 탐색·서버 시작·스크립트/파일 실행을 하지 않는다. 파일 검사는 렌더링·육안 검증이 아니다.

공통 routine_runtime 안내에 같은 진입점을 포함하고, 부서관리의 다음 정상 입력에서 변경 메타데이터만 전달한다. 진행 중인 팀에 새 업무를 보내지 않는다. 앱 브라우저에 file://를 전달하거나 Ego 캡처 시간초과 뒤 같은 파일을 앱 브라우저로 여는 fallback을 사용하지 않는다. 이미 차단된 파일을 HTTP 서버로 재제공하거나 다른 브라우저/보안 설정 해제로 우회하지 않는다. 캡처 오류와 URL 정책 차단은 별도 실패로 기록한다. 허용된 파일 표시/기존 이미지 읽기만 사용하고 새 렌더링 증거가 없으면 시각 검증 미완으로 남긴다.
