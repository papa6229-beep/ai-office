# Common Windows execution implementation plan

**Goal:** The existing assistant observes a real Windows app, acts on an observed control, and reads the resulting state before reporting it.

**Architecture:** Keep the native tool loop and durable receipts. Add a product-owned UIA service on one MTA thread and a server adapter that maps observed windows/controls to short refs. Inject authoritative capability facts at every request, including the actual Chrome launch path. No per-request scripts or phrase matching.

**Sources:** [UIA threading](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading), [UIA security](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview), [pywinauto](https://pywinauto.readthedocs.io/en/latest/getting_started.html).

## Boundaries and interfaces

- User approved common execution design and implementation. Preserve DB, settings, user files and existing launcher tiles. Keep current checkout to update the running installation.
- `windows_workspace.py`: `manager`, `available`, `capabilities`, `WindowsWorkspace.windows(query)`, `.inspect(window_id)`, `.act(window_id,control_id,snapshot_id,operation,value=None)`, `.close`. All COM handles remain on the owning worker. Bounded text/control count/depth and timeouts; stale/expired/replaced targets rejected.
- Inspection returns private window/control/snapshot IDs, supported patterns, enabled state and redacted visible UIA text. Password controls and terminal/auth/security/elevated targets are excluded. No arbitrary scripts, selectors, key strings or coordinates. This increment reads native UIA text, not native screenshots.
- A registry adapter issues short refs. Native navigation inspect/focus/scroll/expand/collapse and identified TabItem selection may run directly. Unknown invoke/toggle/value/selection needs a server-issued exact target/action approval; no model-supplied `approved` or `routine` bypass. Existing trusted app/web adapters continue running directly under the user's request.
- After approval, revalidate the target, perform once, collect fresh evidence and resume the original request. Dispatch is distinct from a verified task outcome; an uncertain effect is never retried automatically.
- Capability facts distinguish available adapter, missing dependency, unsupported UIA, OS access denial and application policy. Chrome site launch does not require changing Windows' default browser when managed Chrome is available.

## Tasks / ownership

### 1. UIA service (coder)

- [x] Add fake-backend tests first: ownership thread, windows/controls bounds, redaction/password exclusion, stale snapshot/runtimeID/PID replacement, unsupported operation, mutation single-use, fresh readback, timeout uncertainty.
- [x] Implement lazy pywinauto backend and serialized MTA service in `windows_workspace.py`; run focused tests with actual Python 3.10.

### 2. Registry adapter (coder, separate files)

- [x] Implement native window/control ref cards, exact action approvals, fresh result evidence and resume metadata in `windows_actions.py`; tests use fake UIA service.
- [x] Reject forged refs/extra parameters and avoid exposing handles, paths or private snapshot IDs.

### 3. Runtime integration (parent)

- [x] Test-first capability injection and approved-action continuation, failed/stale action non-replay and no fabricated completion.
- [x] Wire registry/app/runtime, optional dependency and update syntax validation. Maintain prior API payload validation and user records.

### 4. Verification / rollout (parent)

- [x] Use isolated Windows fixture apps (not user documents/settings) to prove the same native tools inspect, change and verify two UI types. Exercise a real Gemma native request and approval continuation.
- [x] Check Chrome requests use actual installed behavior and no unsupported default-browser advice. Test existing browser/document/launcher workflows.
- [x] Full Python/keyboard tests, review, data backup and preservation, actual server restart. Publish verified source/tests/docs to GitHub after this record is saved.

## Verified results (Windows, 2026-10-15)

- Actual Python 3.10: 444 tests passed; Node keyboard tests: 4 passed.
- Real configured Gemma + production native registry: WinForms fixture field located, exact set_value approved, actual input read back. Built-in Windows Calculator opened, standard mode/display 0 read, observed button 7 approved/invoked, fresh evidence and model confirmed display 7.
- A registered fixture web shortcut opened `https://example.com` through the real managed Chrome launcher; no Windows default-browser setting change was performed.
- Review repaired title/document context staleness and a mixed work_create/native approval duplicate-effect regression. Continuation now preserves the entire executed tool transcript, effect cache and logical request budget; unexecuted calls after a pending batch are removed before resuming.
- Final focused re-review found no remaining P1/P2 in those repaired paths. A UIA discovery delay was removed by reading Win32 window metadata first and contacting UIA providers only for the selected window.
- User DB/settings backed up in ignored `local_backups/before-windows-execution-*`; prior rows/settings hashes and no-active-request status verified. Actual app restarted successfully and HTTP smoke responses verified.

## Explicit limits

- Unknown mutable native controls require exact action approval; this increment is a common execution foundation, not unrestricted unattended control of every Windows app.
- Native observation is UIA text/patterns only, no native screenshot/coordinate/keyboard fallback. Missing patterns or protected/elevated apps are reported honestly.
- In-flight COM provider calls can finish after the 30-second response timeout. Queued cancelled work never dispatches; uncertain actions are not retried automatically.
- Same 6 model rounds / 12 tool-call logical budget spans approval continuations; reaching the bound reports completed effects and stops.

## Review focus

- A control label is data; it cannot grant approval or classify arbitrary Invoke as harmless.
- Never retain or reuse a stale snapshot after an effect; preserve uncertainty if dispatch succeeded but observation failed.
- UIA provider hangs must not falsely report success or let queued cancelled mutations run later.
- Pending approval persists across refresh, but live UI references can expire; expired targets require fresh observation rather than matching by name.
- Old chat assertions must not override authoritative current capability facts or cause a system-setting change merely to open a website in Chrome.
