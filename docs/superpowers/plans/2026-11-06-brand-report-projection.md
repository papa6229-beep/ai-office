# Brand register to daily report implementation plan

> Implement in this main chat using the existing source and official publication paths. No subagents, unit tests, model checks, or new schedules.

**Goal:** Include Japanese sample batches in the existing shared register and use the same canonical brand state in today's team-lead/public daily report.

**Architecture:** The existing public read/protected-write Sites register remains canonical. The report server reads its bounded public snapshot, persists a separate date-keyed last-known snapshot, and renders it in both report views. Only the current Seoul date refreshes; historical snapshots never refresh. Existing report notes, requests, attachments, member work and original brand data remain stored.

**Approved date behavior:** User selected current-day live projection and historical saved-state preservation on 2026-11-06.

## Constraints
- Reuse the Site URL and existing official Ego/credential sessions; no foreground input.
- Preserve 12-column layout and existing row revisions/access controls.
- Distinguish country and stable batch identity; Japanese TIS paid/free rounds are separate.
- Never infer logistics dates or completed state from filenames alone.
- Canonical status updates follow confirmed receipt/assignment/completed-group-send events; preserve remarks round labels.
- Do not fabricate remaining counts, progress or completion dates absent from the canonical source.
- Network failure preserves last-known data with freshness information, never empties a healthy snapshot.
- Report refresh updates only brands and related KPIs, never another unsaved field.
- Existing dated reports before this integration retain their legacy data.

## Work
- [x] Read existing Japanese sample metadata and initialize exact current batches without changing company sample rows.
- [x] Extend Site fields with market/batch label; show a market filter and round label within the existing brand cell. Preserve old Chinese rows through defaults.
- [x] Teach existing event matcher the explicit Japanese brand/paid-free/round identities; retain exact event deduplication and cutoff.
- [x] Add report-side date-keyed snapshot storage and current-day bounded read/cache; preserve original report data and history.
- [x] Use the same projection in both report views, with safe visible-page refresh and source/freshness indication.
- [x] Check syntax/build as appropriate without unit tests; deploy existing Site and existing report project, verify live readbacks and visual views.
- [ ] Register Office source activation if native modules changed, journal actual verification/remaining scope, and synchronize only task source changes.

## Verification ledger

- Existing Site published as v11, public viewing/protected writes retained.
- Three Japanese sample batches added through the protected additive endpoint;
  the original 15 cloud row revisions were identical before/after.
- TypeScript no-emit, JS/Python syntax, and production build completed; no unit
  tests or paid model checks were run.
- Report production deployment `dpl_GvPykd1V244m6GaByyghHXVHmBVX` is ready.
- Public API reads 18 canonical brands including 3 Japanese rounds. Previous-day
  complete response was byte-equivalent as JSON; today's non-brand fields matched.
- Official Ego observed Site's Japan filter, public report and authenticated leader
  view. Canonical brand inputs are read-only; other report textareas remain editable.
- Native code receipts registered, currently awaiting request completion/idle reload.
  No new live messenger transfer was performed to exercise a future event.

## Review focus
- Same-brand paid/free rounds must not collapse by brand name.
- A slow request must not overwrite newer source or an edited report field.
- Prior-day reports must not acquire today's state.
- Missing source data must not be reported as zero remaining/100 percent complete.
- Existing member reports and unrelated brand/manual data must remain recoverable.
