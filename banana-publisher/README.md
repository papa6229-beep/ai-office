# 바나나몰 이벤트 게시 준비

이벤트 공개 주소와 이미지 목록을 받아 SFTP로 웹·모바일에 전송하고, 두 비즈호스트 편집 화면을 엽니다. 편집 화면의 HTML 수정과 게시 저장은 다음 작업 단계입니다.

## 로컬 실행

이 디렉터리에서 별도 Python 환경을 활성화한 후 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m waitress --listen=127.0.0.1:8877 --call app:create_app
```

브라우저에서 `http://127.0.0.1:8877`을 엽니다. 여러 팀원이 접속할 때는 서버의 인증된 내부 HTTPS 리버스 프록시를 통해 노출하고 원본 서비스는 루프백에 둡니다. 외부 주소는 `BANANA_PUBLISH_ORIGIN`, 서버 측 팀 웹 인증은 `BANANA_PUBLISH_WEB_USER`와 `BANANA_PUBLISH_WEB_PASSWORD`로 설정합니다. 프록시는 해당 주소의 Host 헤더를 전달해야 합니다.

## SFTP 서버 설정

| 환경 변수 | 값 |
| --- | --- |
| `BANANA_SFTP_HOST`, `BANANA_SFTP_USER` | 운영자가 승인한 기존 SFTP 서버와 계정 |
| `BANANA_SFTP_PORT` | 비워 두면 현재 확인된 연결과 같은 `20022` |
| `BANANA_SFTP_PASSWORD` 또는 `BANANA_SFTP_KEY` | 서비스의 비밀 설정으로 주입할 암호 또는 개인 키 경로 |
| `BANANA_SFTP_KNOWN_HOSTS` | 운영자가 대조한 서버 키가 들어 있는 OpenSSH known_hosts 파일. 기본값은 사용자 `.ssh/known_hosts` |
| `BANANA_SFTP_HOST_SHA256` | known_hosts 대신 사용할 수 있는, 운영자가 확인한 `SHA256:…` 서버 지문 |
| `BANANA_SFTP_WEB_ETC`, `BANANA_SFTP_MOBILE_ETC` | 두 스킨의 실제 원격 `/…/etc` 절대 경로 |

접속 정보는 서비스 실행 환경에 설정합니다. `known_hosts` 방식에는 비표준 포트를 포함한 `[호스트]:20022` 항목이 필요합니다. SHA-256 서버 지문을 설정하면 해당 지문으로 확인합니다.

## 작업 방식

- `/etc/evt_example.php`에서 이벤트 이름 `evt_example`과 편집 파일 `/etc/evt_example.htm`을 구합니다. 웹 스킨은 20, 모바일은 21입니다.
- 신규등록은 두 `etc` 아래 이벤트 폴더가 없으면 생성하고 첨부 파일 이름으로 올립니다. 수정은 공개 웹·모바일 페이지에 실제로 사용된 이미지 폴더를 찾아 내용 해시를 붙인 새 이름으로 올립니다. 두 화면이 같은 폴더를 쓰면 한 번만 업로드합니다.
- 서버에 올라간 파일은 원본과 크기 및 SHA-256을 대조합니다. 같은 내용으로 재시도하면 확인된 파일을 재사용하며 스킨별 진행 결과를 표시합니다.
- 편집 창은 요청자 브라우저에서 열립니다. 팝업이 차단된 경우 결과 카드의 웹·모바일 링크를 사용합니다. 비즈호스트 로그인은 각 브라우저에서 이어집니다.
