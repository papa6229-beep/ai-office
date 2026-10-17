# Aemeath model connections implementation plan

**Goal:** Make manual brain selection and its actual readiness/limitations visible while retaining the active Codex connection.

**Architecture:** Keep shared office data and tools independent of provider profiles. Adapt the existing Codex and local HTTP routes; reserve Gemini CLI and Claude CLI routes without presenting them as executable integrations. Readiness checks inspect installation/login/server inventory only and never generate a model response.

**Scope approved by user:** Switching foundation and capability display first; account login and actual inference checks follow separately. Main agent executes inline; no test suite or paid model checks.

## Tasks

- [x] Add `model_connections.py`: provider catalog, sanitized status/capabilities, independent saved profiles, validated atomic manual switch. Preserve all existing settings and default Codex Luna/low/Standard. No automatic fallback or API billing.
- [x] Connect guarded status/selection endpoints and existing advanced settings save in `app.py`. Hide every profile's secret in public settings. Refuse changes during active work. Pass selected connection facts to the tool runtime.
- [x] Add `model-connections.js` and `.css` to the connection settings screen: four provider cards, readiness refresh without AI, explicit unsupported adapter status, capabilities and local-versus-cloud explanation. Retain advanced settings.
- [x] Check syntax/diff and read-only running routes/UI. Back up private state before controlled activation, leave active settings unchanged. Record exact verification and remaining integrations in shared context/journal and push source files only.

## Verified differences and later adapter boundary

- Codex: existing native agent turn, native workspace/file/image tools and publish receipts. Installed binary alone does not prove login, quota or model entitlement.
- Local Gemma: existing OpenAI-compatible/Ollama conversation and function-tool loop. Native Codex workspace runner is absent; tool reliability and image understanding require actual model checks. `/models` is inventory, not proof of a loaded model or successful inference.
- Gemini: CLI absent on this PC. Google AI Pro can authenticate Gemini CLI with Google; headless and MCP tools exist. Login, CLI-to-office tool/event/session bridge and real execution are still needed.
- Claude: CLI installed and auth status reports a claude.ai login. No office adapter yet. CLI plan reporting is not current quota evidence. Subscription versus API credentials must remain explicit.
- Future CLI adapters must normalize tool events, attachment/vision input, session identity and execution receipts. Never reuse Codex thread IDs for other providers. Shared journal and work history remain shared reference data, not replayable instructions or permission.

## Official references checked 2026-10-17

- https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
- https://geminicli.com/docs/get-started/authentication/
- https://geminicli.com/docs/cli/headless/
- https://geminicli.com/docs/tools/mcp-server/
- https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- https://code.claude.com/docs/en/headless

## Completion evidence

Python AST and JavaScript syntax checks plus git diff whitespace check passed. Server activated as PID 32360 after DB/settings backup and zero running requests. Settings remained byte-identical. Read-only live readiness and actual settings UI showed Codex active, local server/E4B inventory, Claude account login and missing Gemini CLI. Actual switching/inference/tool quality not exercised. Conversation/runtime tables changed during live page activity; other backed-up tables compared equal. Unrelated notification edits present in the shared checkout were preserved and excluded from this commit.
