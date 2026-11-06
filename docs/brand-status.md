# Brand work register

Revision: 2026-11-06-cloud-register-events-v5

## Independent shared board

The separately deployed Sites Worker owns the durable D1 table. Its URL and
credential IDs are in private `local_backups/brand-status/cloud.json`; service
keys live in Windows Credential Manager, bound to that exact HTTPS origin.
Site source is a separate private Sites repository, not company operational data
in the Office repository. Initial records are imported through a protected API,
never SQL migrations or JavaScript bundles. Access starts owner-private; additional
viewers use the Site's supported invitation policy, not anonymous company data.
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
