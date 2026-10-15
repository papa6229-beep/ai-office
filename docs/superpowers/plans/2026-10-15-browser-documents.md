# Browser and document understanding

**Goal:** Chat can open a real separate Chrome window, inspect rendered page text/links or a screenshot, and read/search supported local documents to answer with evidence.

**Architecture:** Product-owned Python Playwright service, headed installed Chrome, persistent private profile, one worker thread/queue owning all browser handles. No dependency on Codex's browser tool/session. Document module discovers approved local sources, issues opaque IDs, validates fresh paths and extracts bounded readable content. Existing native tool loop chooses operations and summarizes real evidence.

**Sources:** [Playwright browser profiles](https://playwright.dev/python/docs/api/class-browsertype), [Chrome channels](https://playwright.dev/python/docs/browsers), [threading](https://playwright.dev/python/docs/library#threading).

## Scope and constraints

- Browser open/tabs/read/query/scroll and HTTP(S) link navigation from observed links. Fixed browser-side collectors only; no model JavaScript/selectors/shell/click coordinates.
- Screenshot read via configured vision Gemma, input fields and whole iframe/frame elements masked; DOM observations exclude form values, scripts, hidden text and cookies/storage. Actual screenshot evidence must precede a visual claim.
- Dedicated ignored `browser_runtime/` profile, never user's Chrome profile/cookie copies or a public debugging port. Existing normal Chrome tabs are outside this managed window. Login occurs in the managed window.
- HTTP(S) validation, no credential URLs, file/data/javascript/chrome schemes, internal AI OFFICE/model control endpoints or metadata endpoints. Allow ordinary intranet sites; block automatic downloads and submission tools.
- Existing web tiles/bookmarks opened from AI OFFICE should use this managed browser when available; optional dependency failure preserves ordinary launch and reports reading unavailable honestly.
- Document sources: desktop, registered work files/folders, configured workspace and already approved PC inventory. Never search the entire PC. Bound directory depth, file counts, file size, extracted text/pages and response size.
- Read supported TXT/code/DOCX/PDF, query beyond the previous 6000-character preview within explicit bounds; report unread pages/truncation and unsupported/scanned formats. Secrets redacted before model output. File links/reparse paths/protected names are excluded.
- Preserve user files/settings/DB. Clipboard is not automatically replaced; excerpts appear in chat for copying. No file writes or external sends from document tools.

## Ownership

- Browser coder: `browser_workspace.py`, browser service tests.
- Document coder: `document_reader.py`, document reader tests.
- Parent: assistant registry/runtime/app/launcher integration, dependencies, docs, browser/API/native tests, rollout.

## Completion

- [x] Browser and document modules with focused regression tests.
- [x] Native tools and existing web launch integration, source refs and truthful receipts.
- [x] Real Chrome reads JS-generated text, follows observed link, scrolls and captures screenshot; actual Gemma can summarize text and inspect a visible visual fixture.
- [x] Real document content query yields the right excerpt (including beyond 6000 chars) and supports summary; ambiguous files carry source/folder for clarification.
- [x] Full tests, data/profile integrity, actual service restart and simple test commands for user.

Publishing follows the verified service restart: commit these source/test/documentation changes and push `main`.

## Verification evidence (Windows, 2026-10-15)

- Actual Python 3.10: all 412 Python tests passed; all 4 Node keyboard tests passed.
- Isolated app + configured real Gemma: native browser tools read a JS-fetched delivery date/cost, identified a green canvas from a real screenshot, followed an observed link and answered its department/deadline. The HTML source itself did not contain the fetched delivery values.
- Native document tools answered delivery date/department after character 6000 in a TXT and contract amount/VAT from a DOCX; unit tests cover PDF/HWPX and original offset mapping for Unicode case folding.
- Installed headed Chrome: nested iframe frames and main input fields verified masked by screenshot pixels; scroll, observed link navigation, window close/reopen passed. No public debugging port, default Chrome profile or cookie copying used.
- Review findings repaired: nested frame capture protection, original Unicode search indices, syntax validation of new modules during updates. Temporary Git remote fixtures confirmed invalid new modules trigger rollback while preserving DB/settings.
- Real DB/settings backed up under ignored `local_backups/before-browser-documents-*`; prior rows and settings hashes preserved. Fixture sources unchanged, test profiles and credentials excluded from Git.
- Actual service restarted successfully; `/`, `/api/work` and `/api/launcher` returned HTTP 200, with all 3 existing launcher tiles intact and no active request interrupted.

## Limits

- Only the dedicated Chrome window is observable. Existing ordinary browser tabs and general native app internal screens are outside this increment.
- Web text covers the main frame only; screenshots mask frames and editable fields. Arbitrary clicks, form submission, downloads, uploads and automatic login are unavailable.
- Document discovery is bounded to approved sources, with extraction limits and no OCR for scanned documents. Plainly displayed sensitive content outside input fields is not guaranteed masked by screenshot analysis.
