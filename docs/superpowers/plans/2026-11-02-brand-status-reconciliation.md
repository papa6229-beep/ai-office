# Brand status reconciliation implementation plan

**Goal:** Keep the existing brand overview consistent with confirmed file moves,
messenger deliveries, and incoming completed workbooks, without sending files.

**Architecture:** Reuse the journaled file-operation moves and the existing
isolated messenger reader. Keep report assignments distinct by owner and batch;
project only observed evidence into the existing private status cache.

**Spec:** Current user request 51dc15ff137731c423faafa9875e727d and the
existing brand-status section of PROJECT_CONTEXT.md.

## Constraints

- Work in this chat; no additional model calls or agents.
- No added or executed tests. Use syntax, actual read-only evidence reconciliation,
  and existing API readback; do not send or move business files again.
- Preserve DB, settings, source seed, existing rows, report counts, and concurrent work.
- No completion inferred merely from delivery, a filename, or a folder location.
- Keep the messenger reader in its existing interpreter; return no credentials or text.
- Register native source changes, preserve reload guards, and push only reviewed code
  to the already approved private repository.

## Work

- [x] Add brand_status_evidence.py: completed move resolution, bounded worker
  metadata, exact delivery matching, and completion-workbook reconciliation.
- [x] Connect the existing refresh and worker action; discover new delivered batches
  without importing all historical folders. Keep events separate from brands.
- [x] Show the completion basis in the current table without changing its layout.
- [x] Reconcile current real receipts once; verify counts, identity, warnings,
  current file locations, and repeated-refresh stability without business reruns.
- [ ] Record and register saved source, update relevant product documentation,
  commit/push reviewed changes, and distinguish activation from UI verification.

## Review focus

Moved folders with conflict suffixes; archives without workbooks; duplicated filenames;
multiple recipients or assignments; newer report/completion conflicts; reader failure;
discovery after a cache refresh; and preservation of historical unknown states.
