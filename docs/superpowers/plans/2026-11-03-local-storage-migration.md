# Local storage migration implementation plan

**Goal:** Store AI OFFICE, Codex state and its dedicated runtime cache on D while preserving existing launch paths and private data.

**Architecture:** Pre-copy live data without deleting originals. At an explicitly agreed quiet boundary, stop writers, synchronize again, verify bytes/links and switch the three original directories to exact destination junctions. Preserve originals as rollback data until the running apps and destination writes are verified.

**Constraints:** No active team interruption, account reconfiguration, permission bypass, arbitrary link following, private-data publication or new schedule. No added/running tests; use syntax and read-only migration checks. Main agent works alone.

- [ ] Copy the three approved roots to the private `D:/Aemeath` directory with ACLs and reparse points preserved. Record failed/locked paths separately; a live copy is not a consistent database backup.
- [x] Keep source editing/journaling rooted at the actual module directory. Recognize only the three approved exact migration junctions for existing file links and operation guards; retain descendant reparse and sensitive-file checks.
- [ ] Prepare a cold synchronization and rollback entry point outside the moving directories. Fail closed on running writers, unknown paths, copy failures or a different migration owner.
- [ ] Verify cold copies before switching. Retain rollback roots; do not delete unverified or unreadable originals.
- [ ] Launch existing app identities, verify storage aliases, app status and actual D writes. Report source saved, physical cutover and runtime checks separately.
- [ ] Commit/push only reviewed source/docs to the approved private repository. Never include the migration manifest, DBs, settings, profiles, sessions or attachments.

Until the cold switch succeeds, C remains authoritative and D is a pre-copy. Apps must not be launched directly from the partial D copy.
