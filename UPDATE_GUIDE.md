# AI OFFICE 업데이트 안내

## 업데이트 실행

1. 현재 작업을 저장하고 AI OFFICE를 닫습니다.
2. `업데이트 후 실행.bat`을 실행합니다.
3. 프로그램이 GitHub 저장소와 `main` 브랜치를 확인합니다.
4. 새 버전이 있으면 `office.db`와 `settings.json`을 `local_backups`에 백업한 뒤 fast-forward 업데이트를 적용합니다.
5. 적용 성공 또는 이미 최신인 경우 AI OFFICE를 다시 실행합니다.

## 보호 동작

- 원격 저장소가 등록된 `papa6229-beep/ai-office`와 다르면 업데이트하지 않습니다.
- 로컬 tracked 소스 변경, staged 변경, 개인 데이터 추적, 갈라진 Git 이력이 발견되면 업데이트를 중단합니다.
- 오류가 나면 updater가 바꾼 파일과 커밋만 조건부 복구합니다. 작업 트리 전체 강제 초기화는 하지 않습니다.
- 데이터 복구 과정에서 다른 변경이 감지되면 자동 덮어쓰기를 멈추고 `update_status.json`에 상태를 기록합니다.

## 사용자 데이터

`office.db`, `settings.json`, SQLite WAL 파일, `startup.log`, 업데이트 상태 파일, 백업 폴더와 Python 캐시는 Git 추적에서 제외합니다. 업데이트 전 백업은 `local_backups\날짜_시간` 아래에 남습니다.

## 파일 내용 분석

일반 텍스트·코드와 DOCX는 읽기 전용으로 제한된 크기만 처리합니다. PDF 본문 추출에는 선택적 `pypdf` 패키지가 필요하며, 최대 파일 크기와 페이지 수를 제한합니다. PNG·JPEG·WebP는 선택한 이미지 최대 3개를 loopback Ollama의 비전 Gemma로 전달해 시각적 검토 후보를 표시합니다. 낮은 신뢰도 결과는 보류하며 자동 파일 이동·삭제는 하지 않습니다. PSD 합성 미리보기는 Pillow가 필요합니다. AI 파일은 Windows의 설치된 Illustrator로 첫 아트보드 미리보기를 임시 내보냅니다. 첫 아트보드만 내보내며, 임시 복사본을 사용하므로 외부 링크 이미지는 빠질 수 있습니다. 원본은 저장하지 않습니다.
