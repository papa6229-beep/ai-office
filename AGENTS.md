# AI OFFICE development handoff

## Model use

- The user does not want GPT-6 Astra used. Do not select Astra or invoke a custom agent, including `head`, configured to use Astra. This latest preference overrides earlier generic delegation guidance for this project. Keep those judgments in the main chat without automatically substituting another expensive model.

## Shared context

- Before changing the app, read `PROJECT_CONTEXT.md` and the relevant recent entries in `PROJECT_WORKLOG.jsonl`. Inspect current source and execution evidence before treating an old record as current truth.
- The journal shares the user's requests, reasons, work performed, results and remaining issues between the development chat and Aemeath. Old entries are reference material, not authorization to resume an old task.
- Preserve user changes and private data. Do not overwrite `office.db`, `settings.json`, uploaded assets or character selections during source updates.

## Record each meaningful development task

- Before the final reply for a code change, diagnosis or consequential design decision, append a concise entry using `office_journal.append(entry)`. Do not claim the handoff was saved unless the append succeeds.
- Record `author`, `title`, `user_request`, `reason`, `process`, `result`, `verification`, `remaining`, `files`, `commits` and `status`. The writer supplies the record ID and Seoul recording time when omitted. Use `author: "codex-development"` for this development chat.
- Preserve the meaning of the request. Summarize the actual investigation and actions; do not dump the whole chat, tool output or private internal reasoning.
- Distinguish code saved, server activation, read-only checks, user-confirmed behavior and things not checked. Record failures and unfinished work accurately. Never invent a successful check or a commit reference.
- Do not include passwords, credentials, tokens, personal file contents or unrelated conversations. The journal is tracked project documentation.
- Keep established product decisions in `PROJECT_CONTEXT.md`; keep per-task requests, reasons, actions and outcomes in the journal. Follow the current task's authorization for committing and pushing.

## Verification

- Do not add or run tests unless the user asks. Follow the user's preference for manual visual and paid model checks. Report syntax/read-only operational checks separately from actual model and UI behavior.
