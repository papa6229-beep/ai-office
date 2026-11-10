# 바나나몰 이벤트 게시 준비

신규 이벤트에 이미지와 MP4를 첨부 순서대로 등록하고, 기존 이벤트의 이미지 교체·삭제를 처리합니다. 비즈호스트 편집기에 저장한 뒤 공개 페이지 반영을 확인합니다.

## 로컬 실행

이 디렉터리에서 별도 Python 환경을 활성화한 후 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m waitress --listen=127.0.0.1:8877 --call app:create_app
```

브라우저에서 `http://127.0.0.1:8877`을 엽니다. 여러 팀원이 접속할 때는 서버의 인증된 내부 HTTPS 리버스 프록시를 통해 노출하고 원본 서비스는 루프백에 둡니다. 외부 주소는 `BANANA_PUBLISH_ORIGIN`, 서버 측 팀 웹 인증은 `BANANA_PUBLISH_WEB_USER`와 `BANANA_PUBLISH_WEB_PASSWORD`로 설정합니다. 프록시는 해당 주소의 Host 헤더를 전달해야 합니다.

자동 게시는 AI OFFICE와 Ego가 설치된 로컬 운영 PC에서 사용합니다. `BANANA_PUBLISH_OFFICE_PYTHON`에 기존 Office Python 실행 파일의 절대 경로를 설정하세요. 게시 기록은 사용자 홈의 `.banana-publisher/replacements.sqlite3`에 보관하며 `BANANA_PUBLISH_SELECTION_DB`로 위치를 지정할 수 있습니다.

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
- 신규등록은 웹 스킨의 `etc` 아래에 공용 이벤트 폴더를 만들고 첨부 파일을 한 번 올립니다. 수정은 공개 웹·모바일 페이지에서 실제 이미지 경로를 확인한 다음 내용 해시를 붙인 새 이름으로 올립니다. 두 화면이 같은 폴더를 쓰면 한 번만 업로드합니다.
- 서버에 올라간 파일은 원본과 크기 및 SHA-256을 대조합니다. 같은 내용으로 재시도하면 확인된 파일을 재사용하며 스킨별 진행 결과를 표시합니다.
- 신규등록은 비즈호스트에서 할당한 페이지 주소, 행사명, 검색 설명과 첨부 파일을 받습니다. 최대 16개이며 이미지당 20MB, MP4당 80MB, 요청 전체 100MB 미만입니다. 목록의 위로·아래로 버튼으로 게시 순서를 조정할 수 있습니다.
- 신규 등록 미리보기에서 **등록하기**를 누르면 우머나이저 기준 페이지의 PC·모바일 소스를 각각 복사하고, SEO와 모바일 화면 제목을 채운 뒤 첨부 목록에서 본문 미디어 블록을 구성합니다. MP4에는 공용 CDN 주소를 사용하고, 영상이 없는 경우 영상 블록을 만들지 않습니다. 등록 직전 대상 두 스킨의 기존 HTM·JS·CSS는 비공개 `local_backups/banana-publisher/restore`에 저장됩니다.
- **이미지 교체**는 새 이미지 한 장을 업로드하고 기존 이미지를 미리보기로 표시합니다. **이미지 삭제**는 이벤트 주소만 사용하며 선택한 HTML 이미지 태그를 제거합니다. 원격 이미지 파일은 보관됩니다.
- **교체 후 게시** 또는 **삭제 후 게시**를 누르면 기존 Office 비즈호스트 인증을 사용해 선택한 주소가 있는 스킨을 저장합니다. 같은 스킨에서 주소가 여러 번 등장하거나 반응형·지연 로딩 주소를 함께 쓰면 자동 저장을 중단합니다. 저장 응답이 불확실한 경우에는 공개 페이지를 확인한 뒤 후속 작업을 진행하세요.
