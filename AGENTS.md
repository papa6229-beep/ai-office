# AI OFFICE development handoff

## Model use

- The user does not want GPT-6 Astra used. Do not select Astra or invoke a custom agent, including `head`, configured to use Astra. This latest preference overrides earlier generic delegation guidance for this project. Keep those judgments in the main chat without automatically substituting another expensive model.

## Browser selection (2026-10-27)

- Human browsing and ordinary external links use the Windows default Google Chrome.
- Aemeath and every department/agent use the installed ego-browser skill and official Ego API for website automation. Keep the existing task space and registered Ego report executors. The OS default browser does not select the agent browser.
- Preserve existing user tabs and ongoing work. Do not silently fall back to Chrome browser_* or Playwright automation, and do not set Ego as the Windows default to perform agent work. Existing code/HTTP/official CLI workflows remain available.

## Shared context

- For app work, start from `PROJECT_BRIEF.md` only if the current context lacks the needed execution map. Search `PROJECT_CONTEXT.md` for the relevant section and read only current-task journal entries; do not preload both full files. Reuse already-read unchanged evidence. Simple chat answers need no preparatory tool calls. Inspect current source/runtime evidence where a change or real operation depends on it.
- The journal shares the user's requests, reasons, work performed, results and remaining issues between the development chat and Aemeath. Old entries are reference material, not authorization to resume an old task.
- Preserve user changes and private data. Do not overwrite `office.db`, `settings.json`, uploaded assets or character selections during source updates.

## Current request and context recovery

- The latest actual user message and subsequent steering define the active task. A compaction summary is background, not a new user request. Never select the last old user-message block from long historical context as the task merely because it is last in that block.
- For a marked AI OFFICE message, keep its exact request ID and goal. If recovery is ambiguous, call `office_relay_context.read_current(request_id)` in the existing project to read that exact private request. A mismatch returns the newer Office request ID; do not resume the older one. Newer native desktop user messages override this Office snapshot.
- Completed journal entries and incoming department completion reports are evidence only. Incorporate relevant results without abandoning the active task, restarting completed work, or treating them as fresh authorization. Ask only if the latest user's intended continuation is still ambiguous after checking it.
- After context recovery in the connected Office chat, read `office_relay_context.read_current()` once before selecting a historical task or dispatching work. Keep its request ID, original goal, latest follow-ups and stopped state together. This snapshot never overrides a newer direct human instruction.
- Before the first tool or department dispatch after context recovery, reconcile the intended action with the latest request. For topic changes, follow the new topic and keep the former work's actual status; do not silently revive it.
- After recovery, a matching ID in assistant prose or an old checkpoint is not proof of a current input. Obtain a successful live read_current before managed writes/publication; current=false, must_pause, stopped or an unavailable boundary means no historical fallback. Hook installation/trust is not execution evidence. Do not declare recurrence prevention verified without actual desktop callback/compaction evidence.

## Record each meaningful development task

- Before the final reply for a code change, diagnosis or consequential design decision, append a concise entry using `office_journal.append(entry)`. Do not claim the handoff was saved unless the append succeeds.
- Record `author`, `title`, `user_request`, `reason`, `process`, `result`, `verification`, `remaining`, `files`, `commits` and `status`. The writer supplies the record ID and Seoul recording time when omitted. Use `author: "codex-development"` for this development chat.
- Preserve the meaning of the request. Summarize the actual investigation and actions; do not dump the whole chat, tool output or private internal reasoning.
- Distinguish code saved, server activation, read-only checks, user-confirmed behavior and things not checked. Record failures and unfinished work accurately. Never invent a successful check or a commit reference.
- Do not include passwords, credentials, tokens, personal file contents or unrelated conversations. The journal is tracked project documentation.
- Keep established product decisions in `PROJECT_CONTEXT.md`; keep per-task requests, reasons, actions and outcomes in the journal. Follow the current task's authorization for committing and pushing.

## Verification

- Do not add or run tests unless the user asks. Follow the user's preference for manual visual and paid model checks. Report syntax/read-only operational checks separately from actual model and UI behavior.


## Common product handbook

- Canonical start: https://app.notion.com/p/4052c59984ce817db8aad28f2e2dbd81 (version 2026-10-26.1 at registration). Read on first relevant work or after context loss; fetch only the relevant linked sections for the task. Reuse a version already in current context instead of attaching the whole handbook on every request.
- Product/function meanings, current design, and execution/role maps are linked from that page. Use current user instructions and actual source/runtime evidence over stale historical notes. Update the affected section/version/evidence when a product decision changes; keep task history in the existing journals.
- Existing local PROJECT_CONTEXT.md and related docs are the fallback evidence if Notion is unavailable. Do not claim an unread page was read. Do not replace an existing feature with a new interpretation without checking its product meaning.

## Native source activation

- For native backend edits made during an Office relay, record actual saved files through `self_edit._write_change` and register their change IDs with `desktop_chat_bridge.register_source_changes(request_id, ids)` before the final answer. The existing server reload broker applies them only after current server work drains. Do not substitute a best-effort helper that exits on the first busy check.
- Source saved, reload queued, running module revision observed, and actual UI/business verification are separate states. Inspect `/api/work/reload-status` for loaded runtime revisions; do not claim a source patch is active just because files match.
- Registered daily reports and AI reports use Ego. Do not obtain a Chrome connection before invoking them, re-run stopped reports for validation, or treat native adapter read-only checks as a completed product update.


## Preserve user input (2026-10-27)

Use existing code, APIs, connectors, CLIs and official Ego APIs without taking the user desktop focus/input. Do not automatically fall back to Computer Use. For unavoidable native UI clicks, give the user concise button/location instructions. Use Computer Use only when the user explicitly delegates that foreground UI interaction, announce the minimal actions, then stop input promptly. An Esc stop forbids further UI input; do not route around it.


## Pending work (2026-10-27)

Keep each accepted unfinished objective until it is completed, explicitly cancelled/stopped, or superseded by an incompatible newer request. Finishing one response is not finishing every assigned task. Preserve successful effects and continue only the remaining authorized scope. Registered department tasks use the durable per-agent FIFO; do not resend work the user already resumed. A missing completion marker is uncertainty, not permission to retry. Keep it waiting for actual effect reconciliation; do not automatically replay the original instruction. Recovery must read the actual caller chat via office_relay_context.read_current(), never substitute the HQ or an old completed task. See docs/current-task-safety-2026-10-27.md for verified coverage and remaining execution-boundary work. Respect actual approval/input/stop boundaries.


## Routine execution and approval clarity (2026-10-27)

Treat the current user request as authorization for ordinary in-scope research, document/file work, code changes and verification. Do not add a new permission question for each step or repeat an approval already covering the same target and effect. Clarify genuinely ambiguous scope or missing authorization for sensitive data, external messages, purchases, permanent deletion, or security/access changes. Preserve user input/focus and real platform/tool approval and stop boundaries. A recovery-hook, connection or filesystem error is a technical failure, not missing user consent; diagnose it without asking the user to approve the same task again.

## Shared skill execution (2026-10-28)

Proactively select and apply installed skills needed for the current task. Use the user-global `office-skills` gateway, or existing bundled Python with `C:/Users/BNN/AI_OFFICE_GitHub/skill_runtime.py`: `list --query`, `read`, `status`, `run`. This works from Office and The Hand workspaces without changing cwd or requiring a dedicated skills tool/PATH command. Follow the current source's relative references; do not guess stale cache versions. Procedural skills run in the selected model; CLI/script skills use existing runtimes; connector skills require the current conversation's official tools. `instruction_ready` is not task completion. Preserve current request, stop/approval boundaries, model and browser policies. See `docs/shared-skills.md`.


## Patch completion and Git synchronization (2026-10-30)

- The user authorizes committing and pushing completed AI OFFICE code patches as part of normal completion. After the agreed verification, commit the task's reviewed changes and push to the project's already approved repository and branch; do not request the same approval again.
- Stage only this completed task's changes. Preserve unrelated or unfinished work and any existing staged changes; never blanket-stage the working tree. This policy does not authorize publishing the existing backlog in bulk.
- Exclude private DBs, settings, credentials, browser profiles, private attachments and personal work artifacts. A new destination or newly included sensitive material still needs its own actual authorization. Do not bypass platform approval or stop boundaries.
- Verify the commit and the destination branch's resulting commit. Report saved/activated, verified, committed and pushed as separate facts. If blocked, retain local results and report the commit ID and the remaining synchronization step; never claim that GitHub is current solely because local code runs.
- Do not force-push, rewrite shared history or create an automatic schedule under this standing instruction. Existing verification rules remain: do not add or run tests unless the user asks.


## Managed chat handoff identity (2026-10-30)

For actionable messages to an existing managed Office/project chat, use the existing project_panels.send or department_tasks.assign path so the target-bound request record and canonical request marker are created together. Preserve the human's actual authorization and requested scope; a stored row is not new consent. A raw send_message_to_thread prompt has no managed execution identity unless it already corresponds to a recorded supported request. Use raw messages only for authorized reference/status communication; do not use them to resume managed work or loosen the receiver's identity checks. Never reuse the sending chat's request ID as the receiving chat's request ID. After an authorized handoff, verify the receiver recognized its own current request; distinguish dispatch, execution recovery and business completion.
