# Provider capability continuity implementation plan

**Goal:** Preserve Aemeath's work, notifications, context, browser and artifact workflows when the user selects another supported model.

**Architecture:** Keep the existing office registry and audited receipts as the executor. Add provider-neutral work-copy tools and multimodal CLI input; retain the existing source publication, goal and screen verification boundaries. A provider change does not create a new product identity or copy private data into provider settings.

**Constraints:** Work in the main chat; no Astra, tests or paid model calls. Preserve DB/settings/assets and current provider. Readiness and syntax checks do not prove actual inference quality.

- [x] Connect Claude streaming image blocks and Gemini explicit private image input; prevent user text from becoming CLI file directives.
- [x] Add common workspace listing, reading, hash-checked writing, image review and bounded Python execution. Local execution uses current Windows user permissions, not an OS filesystem sandbox; code must stay within the current authorized task. Keep source publication checks intact.
- [x] Give non-native requests durable work-copy identity across pending continuations. Route screenshot review through the selected model and mark only successful image reviews as viewed.
- [x] Update instructions and capability descriptions, retaining the same notification/work/report tools and shared journal.
- [x] Check syntax, source diff and safe operational readiness without model generation; back up and activate only when idle. Record the actual verification limits.

## Execution ledger

Existing inspection confirms that non-native requests lack agent_session_id and CLI vision raises unsupported. Shared notification/browser/report registry tools already exist. Implement parity through this common registry rather than enabling uncontrolled CLI-native tools. User explicitly wants each provider to perform every supported task, including notifications, without a different Aemeath experience.


Implementation completed in the current workspace and activated as server PID 5604. Python AST checks for 10 files, actual runtime library presence and shared tool registration succeeded. Read-only server API returned all four providers with five capability rows each. Settings bytes and tasks rows match the pre-activation SQLite/settings backup. No tests, model generations, notification actions, source publication through a model, or real provider task were performed. Thus image delivery and task quality remain unverified in actual inference.

Ruling: remove the separately required intent classifier in app.work_service, because its structured answer previously gated the entire non-native request before the shared reasoning/tool loop. Keep executor schema/reference/effect validation. Remove source-observation tool-list narrowing so recovery tools remain available. Add model-input-only receipt compaction and history retrieval; retain actual stored history and effect deduplication.
