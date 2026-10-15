# G-Clicker shortcut import

**Goal:** A chat request imports existing G-Clicker web/app shortcuts into AI OFFICE, with real results and no source changes.

**Architecture:** A bounded reader examines only `%APPDATA%/HotSpot/StreamDock/profiles` live `.sdProfile/manifest.json` files. It returns allowlisted web/app targets and locally validated icons. Native assistant tools expose discovery and a batch import of server-held candidates; existing QuickLauncher stores tiles and handles validation. No arbitrary path or command input.

**Completion:** Real Gemma understands “G-Clicker에 등록한 바로가기들을 자비스에 가져와” and imports the discovered supported targets into an isolated database; repeats preserve existing tiles; actual user source profiles are unchanged. Deploy the code to the running service and push after verification. Actual user launcher population happens on their chat request.

## Constraints

- Preserve original profiles, DB/settings and existing tiles. Back up user DB/settings before service restart.
- Import only website and single openApps actions. Ignore passwords, hotkeys, scripts, composite actions, navigation and unsupported plugins without exposing their Settings.
- openApps.appId selects one entry from applications; the array is an app catalog, not a collection to import. Verify zero-based selection with actual data and app sources if available.
- Exact registered AppID mapping or validated local EXE/LNK only; no guessed launch commands or fuzzy AppID mapping.
- Validate HTTP/HTTPS, safe local routes, profile/file/node/depth/size limits, icon size/type, JSON failures and source changes. No archive extraction, network fetch or app execution during discovery/import.
- Model receives candidate names, short references and summary counts; private targets/icons remain server-held. Existing tool error/receipt/idempotency behavior applies.

## Work

- [x] Reader and parser regression tests: selected app only; nested profiles; exclusions; duplicates; corrupt/oversized/reparse/unsafe input; icons.
- [x] Native discovery/import tools with server-issued refs, stable child request IDs, existing-tile preservation and partial failure reporting.
- [x] API regression tests, real Windows source discovery and isolated Gemma/browser import/repeat verification.
- [x] Full tests, read-only source integrity comparison, user DB/settings preservation and service restart.

## Verification

- Actual Windows Python: 369 tests run, 368 passed, one optional PDF dependency test skipped. Keyboard tests: four passed.
- Actual G-Clicker profiles: 28 unique shortcuts (17 web, 11 apps), six validated icons. One duplicate and seven unsupported actions excluded.
- Real Gemma plus real profiles in an isolated DB: 28 added, zero failed; repeat: zero added, 28 preserved, zero failed. A read-only request left registration unchanged.
- Source revision unchanged; all prior user DB rows and settings hash preserved. No native app launch or source file writes occurred during import.
- Review regressions cover malformed UUID/title values and SQLite item failures. Server-authored inventory avoids extra model-generated counts when displaying the actual list.

Generic window inspection and document-specific chat tools remain separate follow-up work; this change delivers the agreed first G-Clicker import use case.
