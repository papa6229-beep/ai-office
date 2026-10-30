# Registered execution (2026-10-28)

Run registered work, do not reconstruct it before every request. All Office
models share ToolRegistry. All departments receive the same native runtime:

`python routine_runtime.py list`

`python routine_runtime.py run --request-id CURRENT_ACTUAL_REQUEST_ID`

The runtime resolves the actual current caller input, matches the entire user
request, and calls existing code tools directly. It never starts another model.
Unknown requests and follow-up-dependent instructions require interpretation;
they are not permission failures. A reserved/uncertain run is never replayed.

Verified corrections reuse the same routines and source activation ledger; see
`docs/correction-reuse.md`. The exact request's report inputs reach the common
report tool. Structured question answers preserve the original current goal but
never auto-replay the whole registered job. Active/hash-matching correction IDs
are linked in existing execution receipts, not another model or procedure store.

Explicit daily/AI report commands and the existing file/log/notification routines
remain in office_routines. `상세페이지 시연해줘` and `/상세페이지시연` now route
directly to detail_demo_run -> detail_builder_demo -> detail_builder_demo_ego.
The active demo path has no Chrome or Playwright fallback.

The fixed demo uses the existing11 approved assets/JSON. The deployed builder's
main-6vpGhzNy.js was observed using file.text -> importFile -> JSON parse ->
saveProject/IndexedDB, matching local ProjectManagerDialog.tsx and
useProjectWorkspace.ts. Imported content is compared with all41 approved fields.
The native executor downloads3 detail segments and4 thumbnails, verifies ZIP
contents and the original GIF hash, and keeps the result for the user.
This is approved fixed-data rehearsal, not autonomous creation of a new product.

This registration does not grant new external authority or bypass Codex review.
Do not change approval policies or automatically replay an action after denial.
Normal saved-account use and local-file processing do not imply that every
external transmission or destructive operation is already authorized.
