# In-flight chat input and stop

Existing chat disables its composer during one long POST. Add two independent
authenticated controls: text steering attached to that active request, and stop.
Persist idempotent inputs as private DB chat records. No separate concurrent task
or automatic replay is started. Apply steering at model/tool boundaries and after
a final-response race; preserve the original goal unless the user changes it.

Use one shared request control for Codex, Claude and the generic loop. Native
model waits can be interrupted; already executing office effects settle and keep
their receipts. Stop prevents new tool execution, is not undo, and gets its own
terminal state. Local HTTP waits may need their current timeout to return.

Keep the composer editable during chat work, show an additional-message send
label and a separate stop button. Preserve new drafts when the original request
finishes. Keep action-token/origin checks and idempotent retries. No tests or paid
model calls; check syntax/served source and activate only after existing work ends.
