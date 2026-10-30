# Registered source activation (2026-10-28)

All newly registered native edits share `source_activation.py`: HQ Office relays,
project-panel requests, department assignments and direct native Codex inputs.
`desktop_chat_bridge.register_source_changes` validates saved receipts and live
current input, and records the observed thread/turn/input and goal/follow-up digest.
No old pending task is imported as new authorization.

The server worker waits for that exact live turn to be completed. It checks the
boundary again immediately before preparing the existing idle reload broker.
Stopped/failed turns, changed/newer inputs and changed source hashes block publication.
Unknown boundaries stay technical waits with an explicit reason; they never cause
business re-execution, model invocation or a request for repeated consent.

The same owner's next queued department entry checks this existing ledger before
dispatch. Pending publication stays ahead of the next FIFO input so that the next
task cannot accidentally supersede that publication boundary. Queue order and
other owners' work remain unchanged; terminal activation outcomes release the
barrier without replaying any business action. Correction IDs/evidence links
share these activation rows; see docs/correction-reuse.md.

States distinguish awaiting_completion, boundary_unavailable, awaiting_reload_slot, queued, active,
refresh_required, stopped, superseded, input_changed, source_conflict and
activation_failed. The latest status pointer alone is not activation evidence.
UI-only sources require a separate screen refresh;
server-side code does not require closing the Office/native messenger windows.

Use `/api/work/reload-status` -> source_activation for bounded current metadata.
Source registered/saved, actual server load, and business/UI verification are
separate claims. The worker does not replay old business work or send messages.

For direct native development, obtain the actual current request ID from
office_relay_context.read_current(), save through self_edit._write_change and
register those receipt IDs before the final response. Direct native inputs are
registered in the source ledger without manufacturing an Office chat request.


At the actual idle drain, the broker revalidates source hashes and the exact
completed input again. It also reads real statuses of registered department/HQ
threads and recently observed local native work; stale sent rows never imply
current work. Inaccessible native status prevents a restart and exposes a
technical wait. These reads do not invoke models. The final check is uncached.

## Durable load evidence and adjacent registrations (2026-10-29)

`local_backups/self-edit/reload-history/<ticket>.json` preserves each ticket and
its stage history. `boots/<launch-nonce>.json` is written by the actual serving
process bound to the existing HTTP server. It records the launch nonce, actual
PID/port, loaded revisions, exact source receipt coverage and comparison of
executing Python definitions/literal constants with the pinned source. A source
hash, CLI import check, launch request or latest global ticket alone is not a
successful server load. Frontend source availability remains separate from UI
refresh/visual verification.

Ticket metadata and restart ownership use cross-process file leases. Only queued
work from the same drain is merged; a registration arriving during restart is
deferred without overwriting that ticket. A stale helper writes its own history,
not another ticket's latest status. Unknown/failed tickets are observed rather
than automatically restarted. SQLite errors retain the publication phase and
actual error type instead of turning a queued attempt into another publication.

Workers reconcile exact coverage from durable serving-process receipts even
after a newer ticket replaces the display pointer. For an older overwritten
ticket with no receipt, history remains unknown. Explicitly reviewed correction
retention may link original IDs/receipts to current registered replacement
coverage; only a fresh verified server load qualifies the retained code.
`original_reload_id` and original source receipts remain preserved. This does
not fabricate a past successful load or replay a past business task.

Registered department source results use a confirmation/report gate. The existing
worker waits for load evidence and required correction references, then appends
a distinct code-observed result and releases the existing single report. Earlier
agent text remains provisional. Failure/deadline observations are not successes.
The older installed runtime's one bootstrap `office_reload.py --confirm REQUEST_ID`
phase leases only that assignment's existing poll/report and reuses its read-only
watcher. It archives observed legacy ticket stages, observes load receipts and
releases the same report; it never prepares/restarts a ticket, sends a message,
invokes a model or reruns business work. It does not exit at the first busy
observation; its PID/deadline/result are retained in the existing ledger.
