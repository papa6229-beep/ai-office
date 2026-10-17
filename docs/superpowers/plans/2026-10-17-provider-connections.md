# Complete manual provider connections

**Goal:** Select Codex, local Gemma, Gemini CLI or Claude Code subscriptions for Aemeath without changing the shared office tools/data or automatically spending through another provider.

**Architecture:** Keep Codex native runner. Local/CLI providers use the existing audited function-call executor. CLI processes return a JSON action envelope only; disable their native tools, extensions/MCP/hooks and execute returned office tool calls in the existing registry. Feed conversation and receipts each call; never reuse Codex sessions. Use the provider's own interactive login and subscription credentials, reject API-key/third-party billing routes. Keep explicit capability differences.

**Constraints:** Main agent only, no Astra. No added tests or model generation during implementation. Help/version/auth-status/model-list and syntax/read-only operational checks only. Preserve current selection/settings/data. CLI runtime/private data ignored. Do not expose credential values/account identifiers or place conversation content in command arguments. No automatic fallback.

- [x] Install/discover official Gemini CLI locally; inspect CLI options and sanitized auth readiness.
- [x] Implement bounded stdin/JSON CLI transport and normalize action responses into existing tool loop. Add provider routing for text and intent paths.
- [x] Enable provider profiles/manual selection, subscription login UI, capabilities and readiness. Keep native-agent-only capabilities accurately limited.
- [x] Update shared context, syntax/check flags, idle backup/activation and read-only status; journal and source-only commit/push. Real model/tool/image execution remains a user check after login.

**Review focus:** No accidental API billing; no native tool execution outside the office registry; no prompt in argv/logs; no claiming login validity or model success from local credential presence; no provider/session bleed; no automatic model load or account switching.

## Execution ledger

- Official Gemini CLI 0.64.0 installed under ignored .provider_runtime; existing Claude CLI reused. User completed Google login. CLI help and installed implementation confirm the core allowlist, MCP allowlist and hooksConfig setting. No model request was made.
- CLI function proposals use the existing executor and no provider session resume. Claude haiku/default Gemini CLI model; manual switching and subscription-only child environments. Text/office tools connected; native code execution and direct CLI images explicitly unsupported.
- Python syntax, JS/inline syntax and diff checks pass. Live server PID 9536 exposes all four routes selectable; local model listed, Claude subscription auth observed, Google OAuth cache present (not token validity proof). Active settings and tasks unchanged.
- No added/run tests, generation, tool/model quality check or actual provider selection. Real response/attachment/tool behavior remains a user check. CLI runtime and account/session files excluded from Git.
