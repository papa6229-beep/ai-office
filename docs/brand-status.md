# Brand work register

Revision: 2026-11-06-japan-daily-report-v6

## Independent shared board

The separately deployed Sites Worker owns the durable D1 table. Its URL and
credential IDs are in private `local_backups/brand-status/cloud.json`; service
keys live in Windows Credential Manager, bound to that exact HTTPS origin.
Site source is a separate private Sites repository, not company operational data
in the Office repository. Initial records are imported through a protected API,
never SQL migrations or JavaScript bundles. At the user's explicit request on 2026-11-06, viewing is public and requires no
ChatGPT account or invitation. Writes remain restricted to the Office sync key
or the configured owner identity; anonymous visitors cannot change the table.
The board remains available when Office is closed. New PC-side file events wait
for Office/its existing executors to run; this does not make the PC always on.

Columns: sequence, brand, total, kind, logistics arrival, list work date, delivery,
priority, work state, assignee, discontinuation, edit controls. Vertical borders,
distinct state badges and combined brand/person/state filters support scanning.
Unassigned people and unknown discontinuation are blank. Delivery is O/X.
Product and web editor sections express field ownership, not separate login roles.
Updates use per-row optimistic revisions; concurrent changes retain unsaved input.

## Confirmed execution events

`brand_work_events.py` receives the existing `work_log.event` file receipts and
confirmed NateOn outgoing receipts. It performs no chat/report inference and no
model calls. Its private SQLite outbox survives restarts and deduplicates each
source key. The receiver deduplicates the same event ID atomically in D1.
Network retries update the board only; they never send messages or move files.

- Newly downloaded NateOn Excel, or successful organization into the registered
  materials tree: known brand/list filename date, delivery O, work scheduled.
- Confirmed Hanbiro delivery to one of the four registered team members:
  that assignee and in-progress state.
- Confirmed completed Excel sent to the registered NateOn product group: complete.
  Attempted/unknown sends, ordinary outgoing Excel and other rooms do not qualify.

Source receipt time is compared with the initial import cutoff. Old logs cannot
reopen completed historical work. Arrival date, counts and priority are never
invented from filenames. A filename's list date never becomes logistics arrival.
Matching uses existing batch IDs plus exact list date/alias or registered file.
New dated price lists get stable identities and preserve a private binding for
later steps. Ambiguous batches are held in `needs_batch`, never matched to the
latest same-brand row. Known progress/completion is not reset by receipt of
another copy. Post-confirmation assignee changes remain explicit assignment events.

## Native integration and recovery

`brand_status.start` starts the outbox and disables the older five-minute report
collector while cloud configuration exists. The Office menu reads a short-lived
cached copy of the same cloud rows and edits the cloud with its revision. The
existing `/brand-register` is retained for compatibility; the independent link is
shown in Office. The outbox refreshes the cloud snapshot every ten seconds, and
the UI refreshes saved rows while visible, pausing during an edit.

If cloud access fails, the last saved snapshot is retained. Pending receipt IDs
are retried with bounded backoff. Native source registration/reload, deployed
Worker revision, database readback and visible browser behavior are distinct
verification levels. The original local registry and evidence files are retained
as backups; they are not reinterpreted as new commands or uploaded to the Site.
No unit tests or paid model checks are run unless the user requests them.


## Four-state table and hold reasons (2026-11-06)

Visible states are 작업중, 완료, 작업예정 and 대기·보류. Legacy 진행 중/작업 예정
map to the new labels; standalone 대기/보류 share one state. Delivery X always
projects/saves an empty web work state, not 미수령. Every cell in a brand row receives the same soft tint for its work state;
the tint is not limited to the current-state column. The former management column is now remarks; click a brand name
to edit the product/web fields. Remarks are bounded multiline text and preserved
with the same row-revision conflict checks. Hold state requires an explicit reason.

`brand_work_hold` is a shared assistant tool. One interpretation of the current
human message chooses brand/date/reason; `brand_work_events.set_hold` verifies
the actual current request, a reason present in that message and exactly one
batch before one revision-checked cloud save/readback. Multiple same-brand
batches require a date; unknown reasons are not invented. Delivery X keeps the
state blank and can store the supplied note. The registered no-model command is
`유우 작업 보류: 상세사진 확인 대기`, optionally placing the arrival date after
the brand. Repeated identical instructions do not write again. New file receipt
copies do not clear a held state. File upload/download remains deferred.


## Account-free viewing (2026-11-06)

Sites access is public by the user's explicit request. The shared origin and
existing D1 rows stay the same. The Site returns `can_edit=false` to anonymous
readers and shows brand names as text. Filtering and refresh remain available.
All mutation endpoints enforce server-side authentication before reading input:
Office events/import require the existing Site-scoped sync key; table edits allow
that key or the trusted platform owner identity matched to the configured runtime
owner email. Secrets and owner identity configuration remain outside source.
Future publication must preserve public access and use save_site_version followed
by deploy_site_version, not the owner-private publishing operation.

## Existing company sample administrator (2026-11-06)

The user also authorized projecting the same workflow into the existing company
sample administrator. `brand_admin_sync.py` and `brand_admin_ego.mjs` reuse the
official Ego login and its observed same-origin sample endpoints. They update
only registration state, web worker, memo, start date and end date; supplier,
logistics, counts and other employees' memo content remain intact. Waiting,
assignment and completion follow confirmed file effects, with no model calls.
Current confirmed bindings cover 15 Office batches and 16 administrator rows.
Unknown identities and concurrent edits require reconciliation, never latest-brand
guessing. See [sample administrator synchronization](brand-sample-admin-sync.md)
for queue recovery, dates, scope and activation/verification boundaries.

## Japanese rounds and daily report (2026-11-06)

The same register now includes market (`일본` / `중국 외`) and an explicit
`batch_label`. Existing Chinese records default to `중국 외`. Japanese rows are
linked by brand plus paid/free round and, when supplied, round year. A missing or
ambiguous round stays unresolved; do not choose the latest same-brand record.
The exact round memo on company samples remains intact. Targets carry their own
`jp_in_progress` tab and exact supplier/logistics/group/round identity. Current
Japanese sample identities were read without changing company sample data.

The daily report's existing server reads the public register directly, without
the PC, an AI call or a browser session. A separate date-keyed snapshot stores
only report-facing brand fields. Today's Seoul date refreshes while viewed;
past snapshots retain the saved state. Reports predating the integration keep
their existing brand source. Team-lead and public views use the same projection;
other report inputs remain independent, and canonical rows cannot be saved as
new manual rows. Network failure retains the last good snapshot. Unknown counts,
progress and dates are not invented. The existing report sources remain stored.

Source publication, actual public/admin API readback, native broker activation,
and a later real messenger event are separate verification levels. No old file
send or completed job is replayed to check the new integration.
