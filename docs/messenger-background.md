# Background attachment intake

## Unified Office popup (2026-10-26)

The user means the existing native PC-app popup in `NativeMessengerHost`, not
a new HTML notification viewer. Both launcher and messenger notifications use
`officeMessenger.open`; it translates the unified launcher to a supported PC
source (`between`, `hanbiro`, `nateon`, `kakao`) before calling the native bridge. This
also works with the original running bridge1, which rejects the newer
`messengers` command. Messenger failures no longer call the individual recorded
notification dialog. Other app notification dialogs are unchanged.

The user confirmed the native popup works and requested only the PC-app screen.
The host now removes the collected-record/support-state panel and its expand
button, including its reserved height and full-frame toggle. Notification IDs
remain receipt metadata; native failures show an error without displaying the
record body. Original app switching, positioning, resizing and restoration stay
unchanged. Kakao now uses the same native path: the exact installed KakaoTalk
process and EVA_Window_Dblclk windows, with the main KakaoTalk screen ranked
above separate conversations even when minimized. Media/helper windows are
excluded. Automatic original-room selection remains unsupported. The native
bridge reports record_shown=false; the frontend accepts native_ready for the
PC-only popup and does not confirm a multi-room notification from app opening
alone. The replacement executable is installed for the next user launch;
compilation and installation do not prove the running UI has changed.

Inspect `local_backups/messenger-entry-20261026/running-image.json` for the actual
mapped executable rather than hashing only the current install path. The new
host writes `local_backups/desktop-app/native-messenger-bridge.json` with bridge,
frontend build, PID, request ID/source and phase; no message body is recorded.
Actual native click success must be checked after the user's safe program
relaunch. Separate-browser checks without a native bridge are not that evidence.

The launcher card uses the fixed `office:messengers` entry point through
`officeMessenger.open('messengers')`, then the selected PC source through
`officeNativeMessenger.open(source)`; it does not navigate to an external
browser. Native bridge version2 accepts a bounded selected notification ID,
room flag, title, collected content and current support description. The host
uses the selected ID for receipts and no longer renders those collected fields.
Confirmation uses the existing selected-item/selected-room endpoint; opening the
launcher or switching apps does not confirm other notifications.

Between has a single original conversation. Hanbiro and NateOn show their
original app without claiming automatic selection of the original room. Kakao
does the same through its existing main window, app cache and restore path.
Compilation, installed executable hashes and read-only window metadata are
verified; user-relaunched Kakao rendering/input/switch/restore remain unchecked.
No new message/file is sent by this route.

Native candidate ranking uses the normal placement rectangle for minimized
windows, consistently with candidate filtering. Otherwise Hanbiro's minimized
main window can rank below a hidden helper window. Native readiness checks
visibility, non-minimized state and fitted bounds, not compositor content or
message delivery. Small popup styling, cached app sessions, resizing and
non-activating window placement are retained.

NateOn follow-up: the user reports a first conversation click failing to appear
until the unified popup is closed and reopened. Read-only metadata confirmed
the minimized main window and a visible separate MessageWnd; the old ranking
preferred that separate window. NateOn's main window now ranks first, and new
chat attachment in Observe runs the initial connection's no-activation
presentation/redraw pass. A 55-second observation began after the user's click
and only captured the already-attached chat, so it did not establish the first
failure's duration or complete cause. Compilation/next-launch installation are
verified; actual first-click improvement awaits user relaunch/confirmation.

Initial deployment receipts are under `local_backups/messenger-unified-20261026/`;
the follow-up is under `local_backups/messenger-entry-20261026/`. The backend is
active in the later shared journal and live status response; the old deferred
receipt alone is not current truth. Frontend server bytes and next-launch native
installation were checked; actual user relaunch/native behaviour remains to be
confirmed.

## Explicit PC read acknowledgement

Hanbiro navigation follow-up (2026-10-27): user screenshots show the original
main app with a left conversation list, and a separate person-chat window
occupying the popup without that list. The host preserves the earlier window
while hiding it. Hanbiro's back action is now labeled 대화 목록 and restores the
first preserved list window directly, including after multiple person chats.
Returning and newly attaching a Hanbiro chat perform the existing no-activation
presentation/redraw pass. First-entry readiness also checks relative z-order,
and recognizes the Office process's own menu windows and already-owned client
windows as Office interaction. No forced focus, global topmost, app termination
or message send is added. Compilation/next-launch installation are verified;
first display and list return in the new running executable remain unchecked.

### Hanbiro first-notification blank body (2026-10-28)

Request `8baf3864caa84ce68f35176f10690638`, continued by the user's actual
display observations, showed an empty body inside an opened native popup. The
original v3 bridge reported native_ready after 1082 ms, but the user's screenshot
was blank. The user confirmed that switching to another messenger and back made
the body appear. Native visibility/bounds/z-order are not proof of painted content.

The first attach path called ShowWindow(HIDE), then wrote a saved style containing
WS_VISIBLE while clearing WS_MINIMIZE/WS_MAXIMIZE manually. Fit could consequently
skip its real ShowWindow call. A warm return did not repeat that style write and
did call ShowWindow, matching the reported difference. Revision
`20261028-hanbiro-native-show-v4` takes Hanbiro's actual style **after hiding**, changes
only the frame bits, and preserves its hidden/minimized state until Fit's existing
non-activating ShowWindow restore. This fix is scoped to Hanbiro; independent native
person popups and the other messenger paths are preserved. Microsoft's
[ShowWindow reference](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow)
documents the normal display behavior without activation for SW_SHOWNOACTIVATE.

The frontend also stops waiting for a collected transcript that the PC-only host
does not display. If a clicked item needs an inbox refresh, it now joins that
refresh and continues opening the same ID within the original click, rather than
requiring a second click. It never substitutes another room or confirms a multi-room
notification merely because the app was opened.

The JS syntax/current served bytes and C# compilation/next-launch installed hash
were checked. Native C# is outside the managed-source extension list, so its source,
compiled executable and deployment hashes are recorded as native receipts rather
than widening that list. The user performs the native app relaunch; no app was
terminated or started by the agent. Actual first-click rendering in v4 is pending
that relaunch and user confirmation. The existing native_readiness flag alone is
not sufficient to mark this fix verified. Evidence:
`local_backups/hanbiro-alert-first-open-20261028/`.

Office conversation confirmation now also uses Hanbiro's original ALLREAD action
for a room already linked by message evidence. This calls the existing logged-in
client without focusing it or changing the mouse. It never sets the Windows badge
directly. The inspected client version is2.1.257; other versions require review.

The exact native last-message receipt must already belong to Office's confirmed
range. Live IndexedDB room identity, membership and last-message fields are checked
again before dispatch. A newer or unmatched message prevents the action. This is a
room-wide server read API, so a message arriving at the server after the final local
check remains an unavoidable race of the original client's read action.

`messenger_read.py` records per-room results separately from phone notification
removal. Confirmed means the original client's room cache reported unread0/read1
after the action. It does not claim the other messengers have PC read support.
Unavailable connections and newer messages are shown separately and can be retried
by explicit confirmation. Other rooms are never cleared as a badge workaround.

## Current support

| Source | Already saved PC files | Selected chat attachments |
| --- | --- | --- |
| HanbiroTalk2 | received-files organizer | Direct HTTPS receive from the currently logged-in app's cached incoming attachments |
| NateOn | received-files organizer | Not connected; no received-attachment enumeration/download method in the installed public ActiveX interfaces |

This feature does not activate native windows, send input, send messages, restart
the messengers, alter their profiles, or export authentication values to models.
Hanbiro's cache is not a complete server history. Missing older files may need a
separate supported history integration. Only the current company origin receives
its login key; cross-origin attachment hosts and HTTP redirects are rejected.
TLS certificate validation stays enabled.

Before decoding IndexedDB values, the worker selects the latest raw LevelDB
sequence per user key (including deletion state) and normalizes SST key trailers.
Obsolete external-blob references must not make current attachment reads fail.
This is an in-memory view; the messenger's database and blobs are never changed.

## Usage

- Common tools: `messenger_attachments_status`, `messenger_attachments_list`,
  `messenger_attachment_download`, `messenger_attachment_job`, and
  `messenger_attachment_cancel`.
- HTTP: GET `/api/work/messenger-attachments` for support, `?source=hanbiro&q=...`
  for attachment metadata, `?job=...` for a download receipt. POST download/cancel
  requires the existing local action token and same-origin checks.
- UI: WORKSPACE → 받은파일 → 메신저 첨부 받기. Search a room/name, choose one file,
  wait for saved status. Received-file intake independently picks up that file.
- Actual filing remains `received_files_organize`; downloading does not authorize
  a new automatic filing rule. Completed downloads have exact-size and SHA256
  receipts. Existing organized receipts prevent unnecessary redownloads.
- Cancel is cooperative at network-read boundaries (socket timeout 20 seconds).
  Restart-interrupted jobs are reported interrupted; never silently called done.

## Private dependency/runtime setup

The server stays on its existing Python. The worker uses the installed Codex
primary Python runtime (currently Python 3.12) with `CREATE_NO_WINDOW`. The
Chromium reader lives only under ignored `.provider_runtime/messenger/python`.
It is a read-only third-party parser, not a browser/profile replacement.

Installed during implementation:

- [CCL Chromium Reader](https://github.com/cclgroupltd/ccl_chromium_reader), 0.3.18.
  Source archive SHA256:
  `ab71e19aae7e85ff8fd7f29f9dc87476fb8593b3a02508863d13f94beba6e077`.
- ccl_simplesnappy commit `3d085230baa8c46cf2090ebba29bf6e8eab31087`.
- Brotli 1.2.0; zstd 1.5.7.2 (worker interpreter-specific wheels).

Do not install this interpreter's binary wheels into the Python 3.10 server.
Dependencies and user profile/cache/session data must never be committed.

NateOn's [official API contact](https://nateonweb.nate.com/helpdesk/main/index.php)
is a possible route for obtaining a supported receive API. No inquiry is sent by
this feature, and this link is not evidence that a receive API is offered.

### NateOn filebox observation (2026-10-28)

The user opened the installed 7.0.40.0 client's existing **파일함**. A read-only
window capture showed the actual loaded file list, including `数据包.rar` displayed
as 145.0 MB. This was a screen observation, not a new download or an exact-size/SHA
receipt. Already handled files were not downloaded again.

In that running session, the filebox had no Win32 child controls. UI Automation
exposed the outer window and system controls, but no file rows or receive action;
the MSAA client object also reported zero children. These observations do not
establish an unattended download path. The existing NateOn capability therefore
remains `direct_download=False` / `saved_files_only`. No window activation,
keyboard/mouse input, profile changes, or guessed private window messages were
used. Local observation artifacts are under
`local_backups/nateon-auto-intake-20261028/` and are not upload material.

NateOn's [official filebox help](https://helpdesk.nate.com/web/faq-detail?faqId=KNOW0000000780&nodeId=NODE0000000075&pageNo=3&subNodeId=NODE0000000078)
documents native listing and manual receiving. Its
[official accessibility help](https://helpdesk.nate.com/web/faq-detail?faqId=KNOW0000000943&nodeId=NODE0000000075&pageNo=1&subNodeId=NODE0000000090)
says to start the screen reader before starting NateOn. It does not promise that
filebox rows or receiving actions become accessible to background automation.
The user was asked to establish that supported accessibility session themselves;
no screen reader, global accessibility flag, or app restart was changed by code.
That connection and actual automatic receiving remain unverified. Notification
collection alone must not be reported as completing the automatic-intake request.

## Explicit Hanbiro file sending

`messenger_send.py` and `messenger_send_bridge.cjs` use the existing authenticated
Hanbiro Electron client through a loopback CDP connection. Establishing that
connection requires an explicitly approved app restart; a normal later launch
without the debugging flag requires reconnection. No automatic restart is added.
The bridge does not focus windows, move the mouse, or close the user's client.

Worker actions `send_prepare`, `send_dispatch`, `send_status`, `send_confirm`
bind a private durable request ID to an exact two-member room, recipient, file
size/hash, current account, and installed app version. Persist dispatch intent
before the single IPC call. An ambiguous transport result must be reconciled
against the original outgoing file record, never automatically resent.
These worker actions are not yet exposed as public Office tools or UI buttons.

## Hanbiro read acknowledgements

`messenger_read.py` uses the original ALLREAD IPC only for a proven linked room
and the exact native message boundary already confirmed in Office. Reading the
Office conversation or a newly confirmed phone-removal range queues that scope
in `notification_pc_read_queue`; the existing PC collector drains one room at a
time. It never sweeps historical confirmed rooms or changes badge cosmetics.
Unavailable/pending results have at most three attempts, 30 seconds apart. Newer
native messages are not read. Already-read rooms require no IPC. Office handling,
phone notification removal, PC acknowledgement, and recipient delivery remain
distinct states. New automatic roundtrip behaviour needs a real future message
observation; manually opening the PC room is not evidence of auto-sync success.

Explicit text sending is also available through `prepare_text`, `dispatch_text`,
and `confirm_text` in `messenger_send.py`. The installed client's text event was
inspected before use. A private durable record binds one requested message to
the exact two-member room, account, and app version. Dispatch intent is persisted
before the single IPC call. Confirmation requires a non-pending outgoing text
with a server message ID in that original room; transport failure never permits
automatic resending. This is a local execution interface, not a new public UI.

An explicitly authorized real Excel send to a team member was confirmed in the
original personal room with `isSending=false`, `isPending=false`, and server file
metadata. A read-only HTTPS retrieval of that outgoing file also matched the
local source size and SHA256. This confirms stored file content, not recipient
read status. No extra caption or test message was sent. Receipt/profile/session
data and the loopback connection configuration remain private.


## 한비로 알림의 이전 개인창 재사용·목록 복귀 수정 (2026-10-27)

사용자 요청17b4f6e3b92d4191b435e457d24cf7b0(직전5134630b...의 세 첨부와 연결): 안용훈 알림을 클릭했지만 이전 이준민 개인창이 열리고 목록으로 돌아갈 수 없으며, 작업표시줄의 한비로 아이콘을 눌러야 목록이 나타남.

확인한 결함은 NativeMessengerHost.OpenCore가 알림 상대와 무관하게 warm stack의 마지막 창을 다시 표시하고, 초기 후보도 개인창/목록 구분 없이 크기·visible 순위로 선택한 것이다. Back은 보존 창2개 이상일 때만 표시돼 처음부터 개인창이 잡히면 목록 복귀가 없었다. 실제 PID44760의 매핑 이미지는50B0C04B... 구버전이었으며 앞서 설치한197ED8F5... 목록 버튼 수정도 실행 상태가 아니었다.

변경: 한비로는 현재 캐시의 멤버·방 표시 이름과 실제 허용 PID의 Chrome 최상위 창을 대조해 목록 후보가 하나일 때만 선택한다. 추정 회사명·고정 HWND·가장 큰 창 순위로 개인창을 목록으로 대체하지 않는다. 목록창을 스택 바닥에 보존하고 알림마다 이전 개인창을 복원·분리한다. 알림 제목과 정확히 같은 기존 개인창이 하나 있으면 연결하며, 없거나 모호하면 목록을 보여주고 상단에 해당 상대를 선택하라는 안내를 표시한다. 새 개인 대화를 자동 생성하지 않는다. 한비로 대화 목록 버튼은 스택 개수와 관계없이 항상 표시되고 목록으로 돌아간다. 이름 매칭을 실제 방ID 검증으로 승격하지 않아 기존 conversation_selected/read-ack는 확대하지 않았다.

목록 식별기는 기존 messenger_attachments._runtime와 같은 이미 설치된 번들 Python/캐시 reader를 쓴다. 처음 확인한 backend Pythonw에서는 캐시 식별이 실패해 그 경로를 수정했고, 최종 동일 런타임에서 실제 목록HWND67590을 읽기 전용으로 식별했다. 새 설치나 네트워크/모델 호출은 없다. 메신저 프로필·대화/초안·로그인 상태를 수정하거나 메시지를 발신하지 않았다.

저장 소스: desktop-app/NativeMessengerHost.cs 및 messenger_window_target.py. Python은 self_edit 영수증b4a0188dc2a945088977f0020997e8b6; .cs는 live_project 지원 확장자가 아니어서 네이티브 전용 before/after/hash/diff로 기록했다. relay 변경 등록은 project-panel 요청이라 거절됐으며 다른 요청에 붙이지 않았다. helper는 native exe가 직접 호출하므로 backend 재시작은 필요하지 않다.

검증: C# 컴파일, 실제 reader runtime의 읽기 전용 창 식별, 컴파일 소스 일치, 다음 실행 경로 SHA256 1EF455D533139C4346C04055C400B465CA44BD49E71D1C96A74D71D71AE8280C 일치를 확인했다. 네이티브 revision은20261027-hanbiro-notification-list-v2이며 다음 실행의 native-messenger-state.json에서 관측할 수 있다. 실행 중 앱을 종료하지 않았으므로 새 화면/실제 알림/목록 복귀는 아직 미검증이다. 사용자가 계획한 재부팅 후 에이메스 재실행으로 반영된다. 테스트·유료 재현·foreground 조작·메신저 재시작 없음. 근거: local_backups/hanbiro-notification-return-20261027/.


## 한비로 일반 실행 연결 옵션 보존 (2026-10-27)

사용자가 연결 보완 후 대기중 클라이막스 파일도 처리하라고 지시했다. 실제 시작메뉴와 작업표시줄 HanbiroTalk2.lnk는 기존 설치exe를 가리키지만 Arguments가 비어 있었고, 실행프로세스에도 debugging port가 없었다. 두 shortcut을 local_backups/hanbiro-launch-aeb056c6/에 백업하고 기존 대상/아이콘/다른 속성을 유지하며 --remote-debugging-port=59492 --remote-debugging-address=127.0.0.1 실행 옵션만 저장했다. Windows 자동시작 항목을 새로 만들거나 제3자app.asar를 바꾸지 않았다.

현재 사용자 지시 범위로 한비로만1회 재시작했다. preflight 성공과 실제127.0.0.1:59492/PID34904 listener, 같은 기존 조현진2인 개인방의 실제 Excel sent 기록을 확인했다. 한비로 바로가기2곳 Arguments도 재조회했다. 수정한 두 정상실행 진입점의 설정 보강이며, 앱업데이트/새shortcut/설치exe 직접실행까지 영구 통제한다고 주장하지 않는다. Office 서버/native host를 재시작하지 않았고 새 sender를 만들지 않았다. 미확인 전송은 재시도하지 않는 기존 영수증 규칙을 유지한다.


## 한비로 목록 유지·독립 개인 팝업 (2026-10-27 후속)

사용자 ff70ca17c0224a94a920f5d4f83d1080는 목록으로 돌아가는 버튼 추가에 그치지 않고 PC앱처럼 목록을 유지한 채 개인 대화창만 별도로 열고 닫는 동작을 명시했다. 추가 작업 승인은2f6b3afdf2794e698aa17d75a1558f6b. 실제 PID22032의 매핑 이미지는1EF455D5...이므로 이전 수정본이 실행 중인 상태에서도 Observe의 Attach(active) 및 알림의 Attach(matches[0])가 개인창을 메인 자리로 교체하는 구조를 확인했다.

NativeMessengerHost revision20261027-hanbiro-independent-popups-v3는 Hanbiro의 메인 목록 Windows와 별도 Floating 팝업 목록을 분리한다. 개인창은 원래 크기·제목 표시줄·IME·닫기 동작을 보존하고 Office 소유 관계만 기록한다. 메인 Fit/resize/숨김으로 개인창을 목록 자리에 끼워 넣지 않는다. 알림의 정확한 기존 제목 창도 목록 표시가 끝난 뒤 별도 팝업으로 표시한다. 앱 전환/통합창 닫기 시 별도 원본 복원 정보를 사용하며 native 대화나 원본 앱을 강제 종료하지 않는다. 다른 메신저 연결 경로는 유지한다.

컴파일 소스 일치 및 최종 설치 exe DAB0A779E10B2FEDF18F6FF7D14733E6B67114E847BA034B155368381796E96A를 확인했다. 현재 실행 중 앱을 에이전트가 종료하지 않았다. 사용자는 재실행을 직접 하겠다고 선택했다. 실제 독립 팝업/목록 유지·닫기와 초기 로딩 안정성은 새 실행본에서 확인 전이다. 구현 준비·다음 실행 설치와 실제 UI 확인을 구분한다. 근거 local_backups/hanbiro-independent-chat-20261027/.


### Hanbiro renderer-hidden native popup (2026-10-29)

Request `de2611de498a760d1d5312377d277c67` continued the user's current native
Hanbiro failure. In the opened popup, v4 reported native_ready=true and Win32
showed an enabled, visible, non-minimized owned main window. The actual Hanbiro
renderer still returned document.visibilityState=hidden and a stale 1210x1350
viewport; its input render host was hidden. A visible outer HWND was therefore
not sufficient readiness evidence. This was observed without desktop input.

Installed Hanbiro 2.1.257 main/preload source confirms its normal
`main-ipc-channels` / `setStateMainWindow` API calls BrowserWindow show/restore.
The v5 native popup uses `messenger_native_window.py` to observe the original
renderer and invoke that existing API only when hidden, within an explicit user
selection while the current Office/client is foreground. It validates native
ownership, both executable paths, the exact installed version, and the existing
loopback main-renderer target. It never starts/restarts an app, opens a room or
sends a chat message. No timer invokes this action. It fails visibly if the user
has moved to another foreground window instead of stealing that interaction.

Hanbiro native_ready now also requires an observed visible, loaded renderer;
the native receipt adds renderer_document_visible. rendering_verified remains
false: document visibility is not proof of painted pixels or successful input.
The native host keeps the existing independent popup, draft, resize and restore
paths. Other messenger paths are unchanged.

Revision `20261029-hanbiro-renderer-show-v5` compiled successfully. Python/Node
syntax checks passed, and the installed helper's observe-only action correctly
returned renderer_visible=false/repaired=false against the actual failing
window. The executable was hash-verified and installed for the next user
launch; current Office/Hanbiro were not restarted. Native receipts and the
previous source/executable are under local_backups/messenger-hanbiro-renderer-20261029/
and the native bin backup. User relaunch, actual show/restore execution,
first-open rendering and click/IME verification remain outstanding.
