# Reusable saved logins

The user explicitly authorizes registering and reusing their site account so
routine delegated work does not ask for the same credentials on every login.

- Use Windows Credential Manager generic credentials under an AI OFFICE-only
  prefix, persisted for this Windows user on this computer. No credential JSON
  or password is written into tracked sources, guidelines or reports.
- Add save/list metadata tools and a browser fill tool that checks the exact
  HTTPS origin of the top page and both observed field frames before loading
  the secret. The model gets refs and a result, never a vault password read tool.
- Mask registration arguments in execution logs/history and hash effect keys.
  Preserve idempotency and browser target/signature/expiry checks.
- Register the already supplied account once and add a non-secret work guideline.
  Back up DB/settings before that guideline change and activate only when idle.
- Syntax and native registration/read-only metadata checks only. No tests or
  paid model calls. Browser login/OTP success remains a separate user check.

Native API references:
https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw
https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew
