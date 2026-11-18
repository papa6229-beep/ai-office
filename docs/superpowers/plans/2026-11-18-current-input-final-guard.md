# Current-input binding and contradictory-stop repair

The user requested an actual repair after the research department read the
correct active request twice but applied an old stop from retained history.
The native transcript is preserved. Models, permissions, profiles and business
artifacts are not changed by this repair.

## Implementation

1. Record the exact successful live input, including turn, input, request,
   revision, original goal and current follow-ups, in private runtime storage.
2. Recheck the binding before supported tools and supply a bounded reminder.
   A new turn needs its own read; a prior turn's lookup cannot stand in for it.
3. Use the official synchronous Stop hook for one contradictory-final review.
   Preserve real interruption, newer human input, technical failure and denial.
   A continuation is linked to its exact still-active original input. It is
   never captured as a fresh human request, and has no extra authorization.
4. Reject the same unsupported stop as business completion in HQ publication,
   HQ relay observation, assigned department and direct department observation.
   Preserve uncertain effects and do not automatically resend original work.
5. Install the one scoped Stop definition and review it through the official
   CLI hooks UI. Do not edit trust records or unrelated hook definitions.

## Verification and limits

Use syntax checks, live read-only scope observation and replay of the actual
incident response. No new model session is used for a paid reproduction, no
old business task is replayed, and no test suite is added or run. Source,
official hook trust, observed production callbacks, server activation and
business/model outcomes are recorded separately.

This is an identity/lifecycle gate plus a deterministic contradiction detector,
not a semantic proof of every arbitrary shell command. Known stop-promises and
wrong request markers are checked; unrestricted natural-language goal fidelity
must not be reported as mathematically guaranteed by a regular expression.

Official event contract reviewed on 2026-11-18:
https://learn.chatgpt.com/docs/hooks
