# Current-request recovery: reports are reference material

Request: ebf1bb9262374183b932bc6300535cea, development chat 01a175be-b210-7e02-aa27-8337b9698d33. The user requested existing HQ investigation results, an independent inspection using professional review skills, and a fix for repeated revival of completed tasks. Incoming HQ/research reports are evidence for that request, not replacement instructions.

## Confirmed defect and correction

`office_current_task.read_current` selected the last user-or-delivery item in the live turn. In this actual development turn, the original user request was followed by an HQ evidence share and a research evidence share. The resolver selected the research report, rejected its non-HQ sender, and returned current=false instead of retaining the active request. This was observed before editing, through the real read-only app connection; it was not a synthetic replay.

`office_current_task.py` revision `2026-10-27-reference-boundary-v3` now distinguishes these reference deliveries before choosing the current input:

- Non-HQ department returns are reference data, not authority to replace a human request.
- The explicit HQ existing-evidence-share protocol is reference data.
- A marked HQ automatic department report is reference only when its exact saved record belongs to that thread, has origin=department_report, and has no original user goal.
- Selection stays within the latest actual turn. A newer native human input remains authoritative. Unknown/malformed HQ input fails closed; no completed historical turn is searched as a substitute. A report-only turn cannot revive an earlier task.
- One bounded turn is read with outputs included, rather than two broad reads. Exact marked Office goals still come from their canonical saved records. This reduces lookup size; no measured performance claim is made.

After the change, the same real turn, with both reports still present, resolved the original request and original input item. The previously completed chat-order request was rejected. Source registration to the relay reload path was attempted and refused because this is a project-panel request rather than a desktop_chat_requests relay. The existing approved backend-only restart workflow applied the saved source: PID58056 -> PID58968, ticket095915333cf4488ebe4ff68d8c15e6d8, runtime current_task_revision=reference-boundary-v3. Codex and native app windows were not closed.

Evidence: `local_backups/current-task-audit-20261027/observations-before-activation.json`, `observations.json`, `source-changes.json`, and `restart-ticket.json`. Source receipt: 831cb0fdf25340c89502550f0e02409a. No commit or push was made.

## Review and installed skills

Official getsentry/skills commit d12f250460ffd961f549dbebf7eeb0cf4afd4c2e was inspected and installed using Codex's skill-installer:

- https://github.com/getsentry/skills/blob/d12f250460ffd961f549dbebf7eeb0cf4afd4c2e/skills/find-bugs/SKILL.md
- https://github.com/getsentry/skills/blob/d12f250460ffd961f549dbebf7eeb0cf4afd4c2e/skills/code-review/SKILL.md

Both installed SKILL.md files were read. Review used their input/state/authorization/side-effect checklists, scoped to this request rather than the large unrelated working tree. Installation makes them discoverable on the next turn; the files were read directly for this review. A skill is review guidance, not proof of runtime prevention.

Fully reviewed: `office_current_task.py` and its entire task-specific source diff. `office_context_hook.py` was inspected for lifecycle/scope/checkpoint behavior. Selected integration sections of `desktop_chat_bridge.py`, `department_tasks.py`, `office_relay_context.py`, `self_edit.py`, and `office_reload.py` were inspected; no whole-file or whole-repository audit is claimed for those modules.

Checklist result for the changed module:

| Area | Result |
| --- | --- |
| Inputs, session/state, business logic | Confirmed reference/goal collision fixed above. Native and canonical Office boundaries preserved. |
| SQL/injection | Existing table names remain code constants; record values stay parameterized. No new command/template queries. |
| Authorization | Department reports cannot create human authority; thread-bound canonical lookup remains required. This does not prove semantic correctness of arbitrary commands. |
| Races | No new mutable request store, cached permission grant, or write transaction. The pre-existing gap between a live read and an arbitrary later native action remains outside this patch. |
| Runtime errors and compatibility | AST parsed successfully; real current and old-ID reads succeeded/denied as intended. Private helper call sites checked. No public response field removed. |
| Performance/DoS | One bounded live turn; no history scan/retry loop or unbounded new lookup. |
| Information disclosure | No conversation content added to tracked review records; observations contain identifiers/status/metadata. |
| XSS, CSRF, cookie sessions, cryptography | No changed browser rendering, HTTP state endpoint, cookie, credential, or cryptographic operation. Existing SHA-256 revision fingerprint unchanged. |

No new tests, synthetic hook invocations, paid model checks, task replays, extra agents, or business-job restarts were used. Python AST and actual read-only operational observations are separate from user-visible and model behavior verification.

## Still unverified — not a whole-system completion claim

Actual lifecycle hook callback records remain zero, including after the Office server restart. Global hook definitions and trusted hashes are present; that is not desktop callback evidence. The effective desktop hook enablement/dispatch cause remains unconfirmed. A question about the previously requested full Codex restart is pending; Office backend restart permission does not authorize closing Codex.

Official hook documentation (https://learn.chatgpt.com/docs/hooks) describes supported local tool interception, compact SessionStart context, and failures that can leave a call unblocked. Thus neither installing hooks nor a correct snapshot proves prevention at every arbitrary native action. Actual compact callback -> correct current-goal recovery -> appropriate denial/continuation, plus stop/follow-up behavior, still requires observation in the real desktop session. Saved transport sent status alone is not a completed-work ledger. Do not report the original cross-department problem as fully resolved from this narrower repair.
