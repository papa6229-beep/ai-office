# Brand work overview

Revision: 2026-11-06-manual-shared-register-v4

## Current basis table

The existing Office menu and the separate local `/brand-register` page use one
private registry. The columns are sequence, brand, original total/types, logistics
arrival, list work date, web-team delivery, current state, priority,
discontinuation check, and assignee. Counts retain the supplied table's units;
report work counts remain supporting evidence rather than replacements.

The product-team editor owns brand/counts, arrival/list dates, delivery, priority
and discontinuation check. The web-team editor owns current state and assignee.
These sections express workflow ownership, not authenticated team accounts. This
iteration stays behind the existing localhost/Origin/action-token boundary; it
does not publish company records or enable remote team access. Shared hosting,
individual access control and report/messenger automation are the next stage.

Saves validate only the selected section, compare the row's expected revision,
retain the previous registry in private history and atomically replace the
registry. Conflicts preserve the unsaved form instead of retrying or overwriting.
GET overlays the authoritative registry, so an older evidence cache or concurrent
read-only collection cannot replace manual state, counts or assignment. Cached
report evidence can still be inspected. Manual values supersede the earlier
automatic status-advancement policy below. Changes do not send messages or move
materials. The UI polls saved data while visible and pauses during editing.

The existing overview reads reports, confirmed personal-room file receipts,
download receipts, and completed file-operation steps. Refreshing it never sends,
moves, restarts, or assigns anything. The original private seed stays unchanged.

`brand_status.py` keeps report retrieval and cache publication. The helper
`brand_status_evidence.py` follows successful move/rename/transfer history,
including folder moves with conflict suffixes. The existing isolated messenger
worker returns file metadata for registered team members; it does not return
credentials or conversation text. The server does not import the reader's binary
dependencies into its own interpreter.

Delivery matching accepts workbooks and archives. A confirmed durable receipt
requires its current file size and SHA256 to match. Native cached records without
a durable receipt require a unique local name/size match. Multiple recipients or
a conflicting existing owner produce a warning instead of reassignment.

New delivered batches are discovered from confirmed receipts since the original
seed's date, not the most recent refresh time. Pending material folders remain
discoverable. Campaigns/events remain in the existing separate events section.
Existing report assignments with different owners/start dates are preserved.

Completion is distinct from delivery. A received workbook must have a successful
download receipt, verified original sender, current location in that sender's
completed-files folder, matching content digest, and exactly one matching brand,
batch date, and owner. Conflicting same-day/newer reports remain visible for
review. Counts are never invented from a file receipt. The UI labels the basis as
`담당자 완료파일 수신·정리 확인` and keeps historical notes separate from current
delivery evidence.

If the messenger changes a cached attachment ref, a unique original incoming
candidate is linked only after its actual HTTPS file size/hash matches the saved
download. The original company origin, normal TLS validation, and redirect denial
are retained. Successful alias evidence is reused; it is not a resend/download.

With a private arrival registry configured, the existing server starts one
read-only collector every five minutes. The panel reads the saved projection every
15 seconds while visible; opening it does not start another full investigation.
The manual refresh action remains available. No Codex schedule, model call,
external transmission, material move or automatic assignment is created.

`brand_status_registry.py` uses the private `registry.json` to preserve an immutable
arrival-batch ID, arrival date, aliases, separately linked list dates/files and an
explicit person/start-date report binding. User-confirmed status has its own
timestamp. Older reports can supply labeled counts but cannot override it; only a
later-day submitted report with the exact binding may advance status. A same-day
report without a submission timestamp cannot establish ordering after the user's
confirmation. An explicit completed report also requires zero remaining and a
valid completion date. Stale completion dates in progress rows are ignored.

Files are scanned only in the configured material tree, excluding links and
junctions. Exact registered file links or unique alias/list-date matches attach
them to an arrival; a filename date is never copied into the arrival date. A file
in a completion folder alone does not change work status. Existing verified sender,
download and digest evidence remains distinct. Member-name suffixes are optional.
Unknown image/archive names are not assumed to belong to a workbook. Unlinked
files and other existing report assignments remain visible for reconciliation.

The private evidence ledger deduplicates records by source identity and retains
facts across refreshes/restarts. It reads registered Hanbiro personal/group cached
text, registered official local NateOn exports and collected notifications. The
export's registered room header is checked. Notification sender labels do not
prove group membership; coverage and export date ranges are displayed explicitly.
The collector does not automatically export new native NateOn history. Group
forwarding is labeled only when that verified export contains an outgoing exact
completion filename, separately from receipt and completion. This filename match
does not assert that the exported attachment bytes were rechecked.

The current arrival list is displayed separately from other batches and legacy
history. New or ambiguous batches remain candidates; no same-brand historical
completion is silently reused. Source failure leaves the last good snapshot and
reports an error rather than publishing empty state as a successful refresh.

Private seeds, snapshots, operational receipts, company filenames, and account
state stay outside Git. Syntax checks and actual evidence/API readback are
different from visual confirmation in the user's running desktop app.

The discontinuation-check field starts blank and accepts manual single-line text (up to 200 characters). It is not an automatically assigned status or an enum. The initial generated labels were cleared once at the current user request; later reads and collectors do not recreate them. Material delivery is displayed/selected as O or X, with backward-compatible reads of prior receipt labels.
