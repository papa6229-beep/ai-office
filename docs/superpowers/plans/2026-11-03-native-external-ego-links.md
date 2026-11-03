# Native Office external links: Ego on the current work screen

The user reported Office disappearing after opening research links and asked to fix the behavior after discussing Ego as the common browser.

Read-only reproduction found the same Office process/window remaining alive while the existing Chrome window belonged to a different Windows virtual desktop. The three-minute process observation did not record the main Office process exiting. This explains the observed disappearance but does not establish the cause of every earlier occurrence.

## Change

- `Program.External` validates HTTP/HTTPS and rejects embedded credentials as before.
- Resolve the existing installed Citro Labs Ego executable from normal installation locations. Launch a new window with a correctly quoted URL. Do not use a separate automation profile, move existing windows, clear sessions, change security flags or activate an unrelated desktop.
- Keep local Office previews and agent browser automation unchanged.
- Record native start/closing/closed and external-launch events privately. External events contain hostname only; no URL path/query, chat body or credentials.
- Build with the existing WebView2 DLLs/compiler. Preserve the current binary, install for the next launch, and leave the active user window running.
- Windows-wide default associations remain a separate native Settings confirmation. Never fabricate UserChoice hashes or claim the default changed without a registry readback.

## Verification boundary

Native compilation and installed/source hashes are operational evidence. They are not proof of the currently running binary or actual Ego window placement. After the user closes and reopens Office, check the new lifecycle revision and the user's real link click. No automated tests or paid model checks are added/run.
