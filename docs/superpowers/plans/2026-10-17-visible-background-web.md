# Visible background browser correction

**Goal:** The user can inspect the real work page without Aemeath taking physical mouse/keyboard control or repeatedly activating the window.

**Decision:** Background work and headless visibility are separate. Use headed persistent Chrome, minimize only the initial blank window, and preserve user restores. Keep page-element actions and the desktop-browser fallback guard. No preview system or broader browser transport rewrite is needed for this correction.

- [x] Change the default to headed and separate background/headless/visible-window receipts.
- [x] Minimize the initial managed window through its exact CDP window ID; do not minimize later tabs or bring them forward.
- [x] Correct tool/runtime/shared descriptions.
- [x] Confirm idle, back up, activate, inspect real report-launch/window state without login or writes; append journal and save source.

**Verification boundary:** Syntax and read-only operational observations only; no added tests or model calls. First headed startup creates a real window before minimizing it, so a brief initial appearance is possible; do not promise that it can never flash. Normal page actions do not use the desktop pointer. Existing user Chrome/session is separate. Do not interrupt a running report request.

## Execution ledger

- The initial headed change still restored/activated the window when opening tabs. Creating a background CDP target alone was insufficient. Removed headed viewport emulation and screenshot viewport resizing as well. Headless explicit mode keeps its viewport.
- Final live server PID 32036: first and second report-launch receipts background=true/visible_window=true; foreground window unchanged both times; actual automation Chrome window exists and is minimized. No login, SMS, report edits or model calls. No new tests. Settings bytes and tasks unchanged.
- User also reported authentication failures in the separate browser. Historical receipts show input/click dispatch followed by authentication UI; the root cause is not confirmed. Do not attribute this to the separate browser or claim authentication is fixed. Corrected a stale unsupported-key error that suggested desktop fallback.
