# Brand work overview

Revision: 2026-11-02-brand-status-evidence-v2

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

Opening the existing overview or choosing its latest-status action runs the same
code reconciliation, with no model calls. Read failures preserve the last known
status and surface warnings. No new polling service or schedule is installed.

Private seeds, snapshots, operational receipts, company filenames, and account
state stay outside Git. Syntax checks and actual evidence/API readback are
different from visual confirmation in the user's running desktop app.
