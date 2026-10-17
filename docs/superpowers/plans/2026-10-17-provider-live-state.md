# Provider-independent Aemeath live state

## Evidence and scope

Claude is authenticated and the current request has completed real office tools.
`app.py` captures live replies only for Codex, and the compatible loop publishes
neither prose nor activity. The frontend treats every busy request as working.
Repair the shared live state first, preserving the selected provider and the
running request. Gemini's discontinued personal-account transport is a separate
remaining issue; do not silently use an API key or a different provider.

## Implementation

1. Extend `chat_stream.py` with a request-scoped activity publisher. Preserve
   duplicate-request ownership, bounded ephemeral text and the committed DB as
   the authority. Capture chat actions for every provider in `app.py`.
2. Publish thinking, user-facing commentary, tool labels and final outcome from
   `assistant_runtime.py`. Tool errors and arguments remain in the ledger.
3. Use that state in `workdesk.js` for the header, provisional reply and character.
   Active work has its own character; unread notifications retain their indicator.
   Final POST success/error still owns completion and replay semantics.
4. Check Python/JavaScript syntax and the diff only. No tests or paid model calls.
   Activate only after existing work ends; keep DB/settings/assets. Actual Claude
   visual behavior requires a subsequent user request.
