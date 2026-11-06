# Sample administrator synchronization

User-defined workflow (2026-11-06): verified NateOn material receipt/organization
is waiting (`대기중`); verified team-member delivery is in progress (`진행중`),
with the actual assignee and first confirmed delivery date; verified completed
workbook delivery to the configured NateOn product group is complete (`완료`)
with the confirmed outgoing date. Receiving a completed file is insufficient.
Office's `작업중` maps to `진행중`. Held work maps to `대기중` and the supplied
reason is included in the memo. Existing product-team memo text is retained.

The destination is the existing sample screen reached from xadmin's order-system
link, not the shopping administrator or another new board. The current supported
tabs are `cn_in_progress` and `jp_in_progress`, selected per bound target; changing registration status does not press the separate
archive/work-complete button or move the sample between tabs.

`brand_work_events.py` keeps its verified file-receipt pipeline and cloud outbox.
After the cloud accepts an event, `brand_admin_sync.py` durably queues its ID.
The same existing background loop observes manual cloud-board changes and flushes
pending administrator projections, normally on its next ten-second cycle.
No AI/model call is needed for these updates. Unchanged snapshots do no browser work.
Each observed state transition is distinct, including a deliberate return to a
previous status; retries retain their existing queued identity.

`brand_admin_ego.mjs` uses only the installed official Ego API and the existing
logged-in default profile. It reads the site's own authenticated GET and partial
PUT endpoints inside that same page. Login material never leaves the browser and
is not saved in configuration, plans, results, logs or source. Session expiry is
reported as a connection/authentication issue; there is no silent browser switch.

Writes are restricted to `registration_status`, `worker`, `memo`, `start_date`,
and `end_date`. Every target is bound by an existing numeric sample ID plus its
supplier, logistics date and sample group. One Office batch may map to several
explicitly confirmed split rows. Bindings and business records remain private in
`local_backups/brand-status/sample-admin.json` and `sample-admin-outbox.sqlite3`.
Unknown batches enter `needs_binding`, not a guessed latest same-brand row.
Japanese targets also bind the exact original round memo; the shared web note
does not duplicate or remove that label. Three currently observed Japanese
sample rounds were added as separate batches without changing company records.
No sample creation, supplier, quantity, logistics date, priority or file operation
is part of the projection. Current setup covers the 15 confirmed batches/16 rows;
new unbound batches need identity reconciliation rather than another code patch.

Each PUT sends only differing allowed fields. The executor reads back persisted
values and checks other record fields were retained. The last acknowledged owned
values provide a guard against overwriting a concurrent manual edit. Conflicts
are retained for reconciliation. Successful or already-matching rows are recorded
independently of a later page-display problem; queue recovery reads the effects
instead of repeating file delivery or treating an absent final message as a retry.
The observed company endpoint has no revision/CAS contract; read-before-write and
readback do not constitute an atomic lock against an edit in the intervening gap.

Source event IDs deduplicate retries, and pending rows survive Office restarts.
Date setters use confirmed receipt times, not the time the retry runs. Existing
nonempty dates are preserved. The initial reconciliation uses only the date the
user explicitly supplied; unknown historical start/completion dates stay blank.
Scheduled/unassigned rows clear dates only in the explicit initial reconciliation;
unexpected dates during a later waiting transition are a conflict.

Status: `brand_work_events.status()['sample_admin']` or `brand_admin_sync.status()`
reports queued, synced, unbound and conflicting work without tokens or chat text.
Successful owned Ego Spaces are finished; uncertain runs retain the same Space
for readback. This integration cannot run while the PC and Office executors are off.
Native source activation, initial actual-site readback, and a future real file
workflow are separate verification levels. Do not resend files to validate it.
