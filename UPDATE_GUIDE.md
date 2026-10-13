# AI OFFICE 업데이트 구조

현재는 **로컬 Windows 프로그램**이며 Vercel처럼 원격 배포만으로 현재 실행 중인 앱의 Python 파일이 바뀌지는 않습니다.

## 1회 설치 (개발자가 수행)
1. GitHub 비공개 저장소를 생성하고 `app.py`, `index.html`, `scanner.py`, `workspace_insights.py`, `project_intelligence.py`, `start.bat` 등을 커밋합니다.
2. `office.db`, `settings.json`, `startup.log` 등 개인 데이터와 로그는 `.gitignore`로 제외합니다. **이미 Git에 추적 중이면 git rm --cached로 추적을 해제**해야 합니다.
3. 사용자 PC에 Git 설치 후 해당 저장소를 `git clone`하여 고정 위치에 둡니다.
4. 기존 사용자 데이터 2개를 클론 폴더로 복사하고 `업데이트 후 실행.bat` 바로가기를 바탕화면에 둡니다.

## 이후 배포
- 개발자가 새 코드를 GitHub에 push
- 사용자는 앱을 닫고 `업데이트 후 실행.bat` 실행
- `git pull --ff-only` 성공 시 새 코드 실행, 실패 시 기존 코드 실행 및 오류 표시

## 현재 상태
저장소 URL이 제공되지 않아 배포 연결은 **아직 미완료**입니다. 해당 BAT은 Git 미설정시 기존 실행기로 돌아갑니다. Git 변경/추적 상태가 불명확하면 자동 덮어쓰지 않습니다.

## 보안 주의
- GitHub 비공개 저장소 권장, 사용자 설정/DB/API 키 업로드 금지
- 외부에서 제공받은 임의의 업데이트 스크립트 실행 금지
- 중요한 버전 업데이트 전에는 DB와 settings 백업 권장
