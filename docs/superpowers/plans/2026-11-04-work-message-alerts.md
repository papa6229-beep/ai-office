# Work message alerts implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. The current user approved the filtering design and asked to implement it. Do not dispatch another model.

**Goal:** Tell the user about new Hanbiro/NateOn work messages and real incoming attachments in the existing main Office chat, without model calls, desktop input, or automatic replies/file operations.

**Architecture:** Reuse the notification/message SQLite tables, existing passive Hanbiro attachment reader, and desktop_chat_mirror.announce. A durable queue records new events after a first-start baseline, groups related arrivals, deduplicates native/phone echoes, and publishes only when the main assistant is idle.

**Tech Stack:** Existing Python, SQLite and the existing Office chat renderer. No new dependency, model, automation schedule or UI design.

**Spec:** Current Office requests 9f16b5696f13bedaea9eaa92730a6aec, caf28906dc267c9a74d69ae1dfc99b94, and implementation authorization 12c2e321f9a47d6f2c3199b28c13442e. Prior messages define requirements, not authority to execute their old business jobs.

## Global constraints

- Only Hanbiro/NateOn. Exclude official advertising senders, explicit ad notices, simple acknowledgements and duplicate observations. Do not exclude work about advertising/banner design solely because it contains the word advertising.
- Attachment evidence from the actual Hanbiro cache proves an incoming attachment; text claiming a file was sent does not prove attachment receipt or download. Never imply a local download without a download receipt.
- Never mark messages read, send messages/files, download or move files, replay old work, invoke an AI model, or take focus while detecting arrivals.
- First start ignores existing history; subsequent restarts preserve pending notices and published identities. Busy/unknown main assistant state defers publication. Consecutive events settle for 25 seconds and notices are rate limited.
- User preference: no test files or test-suite execution. Use syntax checks, read-only stored-message classification and real API/runtime readback. Future incoming-event/UI acceptance remains explicitly separate.

## Review focus

- PC/phone observations of the same message or an attachment with its caption must not create duplicate prompts.
- Old history backfilled after activation must not become a new alert.
- A busy main assistant or unresolved main request must not be interrupted.
- A source failure preserves existing queues and never claims full messenger history was observed.
- Crash between chat publication and queue acknowledgement reuses a deterministic notice ID and does not republish it.

## Tasks

- [x] Add work_message_alerts.py: durable baseline/cursors/events, filtering, passive attachment scan, grouping, idempotent main-chat publication, status and lifecycle.
- [x] Start it from existing app.py boot and expose its operational state through the existing office_notifications.snapshot response.
- [x] Check syntax and classify bounded real stored records read-only; inspect diffs. Register actual source changes with the existing idle reload broker; distinguish saved, queued and loaded state.
- [ ] Update product context and journal. Commit only this task's reviewed files and synchronize to the already approved private cloud branch, preserving unrelated work.

## Latest steering and evidence

The user clarified in b8e202b448483c72380a53f71d7dfdb8 that no stop was requested and current work takes priority over department reports. The successful live read confirmed current=true, stopped=false. The origin of the earlier stop signal remains unconfirmed. New department completion report dispatch now rechecks main Office busy state; previously sent reports are reconciled without resending. Pending reports stay queued and department execution itself is unchanged.

Five Python source files passed AST parsing. A read-only classification of 200 real stored incoming messages yielded 133 ignored, 6 completion notices, 20 file notices and 41 work messages; this is operational inspection, not a test suite or full accuracy measurement. No production message was injected, sent, downloaded or marked read. File captions settle for 50 seconds to allow the existing bounded attachment-cache scan to join them; ordinary messages settle for 25 seconds. Real post-activation arrivals and visible chat acceptance remain unverified until observed.
