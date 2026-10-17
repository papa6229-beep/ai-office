# Background web work implementation plan

**Goal:** Aemeath performs web work without activating the user's browser or taking the mouse, and can wait for incoming notifications within the same request.

**Architecture:** Keep the existing persistent automation profile, run Chrome headless, and expose its background identity in every tab receipt. Direct browser-window desktop/UIA actions return a clear instruction to use browser tools before any focus/input. Add a bounded read-only notification wait, with a fresh timestamp boundary and optional source filter. Keep login/OTP interpretation in the current agent request.

**Constraints:** No Astra or subagents. No new tests or paid model calls. Preserve DB/settings/uploads/profile. Do not log credentials or SMS content. This task does not automate a real login, request a new code, or promise site compatibility. Use syntax and read-only operational checks; the user performs the full login flow.

- [x] Make browser workspace headless by default, remove forced activation, and identify its separate session accurately.
- [x] Guard desktop/browser UIA fallback before any effect; preserve ordinary Windows-app controls.
- [x] Add notification waiting (up to 25 seconds per call, bounded current-time cursor, repeatable in one active request); update tool descriptions and runtime guidance.
- [x] Update shared context and source guidance, check syntax/diff, back up and activate when idle, inspect read-only receipts, append journal, and commit/push source only.

**Review focus:** No automatic foreground retry; no false use of the user's Chrome/session; no replay of old OTP notifications; no false completion after requesting a code; no credentials in records. Existing connection/partial-list states are not proof that no new notification exists. A wait timeout is not success and not an automatic new SMS request.

## Execution ledger

- Implemented in the main agent without delegation or added tests. Ten Python sources parse; diff whitespace check passes.
- Activated server PID 8964 after idle check and SQLite/settings backup. Live launcher opened the report page with background=true, session=office_automation, shares_user_browser=false. No login or SMS action. The foreground window stayed unchanged during that request. Pointer position changed between samples, so no empirical claim of unchanged pointer position is made; the background browser path contains no desktop input calls.
- Live notification wait returned a bounded no-match timeout with cursor. Full SMS/login continuation needs the user’s actual next workflow.
- Existing settings bytes and task rows match the backup. The prior diagnosis journal entry was already pending and will travel with this task.
