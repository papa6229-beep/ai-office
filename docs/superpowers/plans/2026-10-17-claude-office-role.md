# Claude office role and tool boundary

Evidence: the latest refusal has zero tool calls and no permission-denied receipt;
the previous request called only launcher_list before asking the user to click.
The transport puts every office system message inside a single CLI user prompt,
while Claude Code keeps its default coding-agent system prompt and has no native
tools. This is a role-delivery defect; its exact contribution to model behavior
requires a subsequent real user request to confirm.

1. Put the office protocol and existing system messages in Claude's explicit
   --system-prompt-file. Keep history and tool receipts as role-labelled data.
   Include the current user request separately and preserve the runtime catalog.
2. Explain the actual external executor contract: all registered office tools
   are available through JSON proposals; native CLI tool disabling is separate.
   Never infer denial without actual evidence or resume an unrelated old task.
3. Preserve auth, selected Haiku, native tool/MCP restrictions, input size limits,
   image handling, private input cleanup and real executor validation.
4. Check syntax and diff only; do not add tests or run a paid model. Activate at
   an idle point with private data backed up, document and push the source.
