# 부서 상태 GIF · 2026-10-26 v1

대표가 첨부한 두320px GIF의 큰 픽셀·단순한 얼굴/적은색 느낌을 참고해 내장 image_gen으로 팀마다 같은 캐릭터의3상태 시트를 생성했다. 원본 참조 GIF나 메인 에이메스의 캐릭터 선택은 변경하지 않았다.

| 폴더 | 캐릭터 | idle / working / completed |
|---|---|---|
| planning | 노란 모자의 기획자 | 눈 깜빡임 / 연필 / 완료 인사 |
| design | 보라 베레모의 디자이너 | 눈 깜빡임 / 붓 / 완료 인사 |
| development | 청록 눈의 회색 로봇 | 눈 깜빡임 / 키보드 / 완료 인사 |
| research | 초록 후드·안경의 조사자 | 눈 깜빡임 / 돋보기 / 완료 인사 |

각 폴더에320×320 무한반복 GIF3개와 움직임 줄이기용 투명 PNG3개가 있다. 같은 캐릭터·색상·몸통 기준선을 유지하고32px 논리 격자와 공통15색+투명 팔레트로 인코딩했다. idle은 느린 깜빡임, working은 짧은 반복 동작, completed는 작은 인사다.

제작/프롬프트/원본 시트/프레임 정보: `local_backups/department-gifs-20261026/`의 prompts.json, build_assets.py, asset-manifest.json. 실제 속도를 합성한 비교 미리보기는 department-states-preview.gif다. 제작 도구는 내장 image_gen4회이며 GIF 조립·색상/격자/기준선 정리에는 Pillow를 사용했다.

`department-sprites.js`는 이 최종 GIF/PNG를 data URL로 묶은 정적 manifest다. 기존 관리JS 제공 경로로 로딩하므로 새 백엔드 파일 읽기 API나 앱 재시작이 필요 없다. 원본 GIF를 수정할 때 manifest도 다시 묶어야 한다. 원래 메인 에이메스의 자산 registry·선택·파일은 재사용/교체하지 않는다.
