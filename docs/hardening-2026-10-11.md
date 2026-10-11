# Public-release hardening — 2026-10-11

The release remains **not ready for unrestricted public use**. This sprint repaired reproducible correctness, privacy, recovery, deployment, and dashboard performance problems and verified the source on the applicant's Raspberry Pi. Completed preparation and passing tests do not establish employer acceptance, authenticated multi-step support, or hosted multi-user isolation.

This document contains engineering evidence only. The detailed application-attempt table, question/source records, resume fingerprints, screenshots, and preservation audit are private under `~/.local/share/please-hire-me/hardening-2026-10-11/` on the Pi. They must not be added to distribution artifacts or Git.

## Implemented changes

| Issue | Root cause and resulting behavior | Evidence |
| --- | --- | --- |
| Exception text could disclose submitted values or signed URLs | Raw browser/discovery exceptions entered events, run history, source errors and batch mail. Unknown failures now retain their type; dashboard failures use a safe action message. Cross-host redirects retain the destination host only. | Six privacy regressions fail against the original source; worker continuation and dashboard recovery fixtures pass. |
| Alternate requisition URLs bypassed recorded attempts | Job identity used the full URL; company cooldowns eventually expired. Exact Ashby, Lever and Greenhouse requisition aliases now check existing non-prepared attempts and manual applications without rewriting historical IDs. | Alias fixtures cover all five protected terminal states, manual history, distinct boards/regions and preparation. Live inventory contained 550 Ashby requisitions with multiple URL forms. |
| DNS outage became a permanent navigation hold | Public-address validation conflated lookup failure with an actual private address and cached that verdict. Lookup unavailability is now distinct, grants no access, and is re-resolved at the next application. Only an explicitly observed lookup failure makes generic `ERR_FAILED` retryable. | Four DNS regression cases fail against original source; private, mixed-address and certificate-error cases remain blocked. Earlier scheduled failures could not be reproduced, so their historical cause is unconfirmed. |
| Dashboard polling could remain stuck indefinitely | Fetch and response-body reads had no deadline. GET reads now abort after 30 seconds, release the refresh lock and offer reconnect while retaining unsaved edits. Writes receive no read deadline. | Browser fixtures at 320 and 1440 pixels cover timeout, reconnect, retained drafts and an unfinished synthetic write. |
| Combined residence question received only a state | Broad state matching captured a city-and-state question. Combined current-residence fields now use the confirmed location. State-only fields and desired work locations remain separate. | Six cases fail against original source. Existing incorrectly prepared packages fail revalidation. |
| High-school graduation year was inferred from college enrollment | Model source review accepted a plausible chronology without explicit evidence of the historical year. A distinct confirmed year fact now supplies that field; inferred/cached writing cannot satisfy it. | Deterministic tests cover absence, a gap between school and college, cached answers, package rejection and year format. No year was fabricated or added to the applicant's facts. |
| Pi service definitions did not start | `WorkingDirectory` used argument quotes, which systemd treated as literal path characters. The generator now emits a literal absolute directory and retains argument quoting for `ExecStart`. | The Pi rejected the old generated unit. The corrected unit passes `systemd-analyze --user verify` and starts successfully. |
| Large-ledger dashboard reads were slow | Ranking `j.*` read large posting payloads across nearly 100,000 rows; summary scans also lacked a compact covering index. The dashboard ranks metadata first, fetches only its selected payloads and adds a covering metadata index. | Attention-order and store-migration fixtures pass. Same-script Pi profiling: snapshot 2.72 to 0.61 seconds; combined snapshot/summary work 9.38 to 3.40 seconds. Warm caches influence absolute timings. |
| Portal telemetry masqueraded as a candidate draft write | The known `/vslog` background POST on configured career sites set the draft-write blocker. It is now discarded without declaring a candidate write. No telemetry request is sent and no new submission permission is granted. | Three vendor fixtures fail against original source; actual candidate writes and identical paths on another ATS remain blocked. |

## Verification

- Final source: `ae4d2d3`, ten scoped implementation commits after `54aafd0`.
- Clean final local suite: **1,272 passed**, five existing PyMuPDF/SWIG deprecation warnings, 341.05 seconds. This adds 55 test cases to the starting 1,217-case suite.
- Deployed Pi focused suite: **76 passed**, 19.14 seconds. Covers aliases, privacy, DNS, residence, explicit graduation year, telemetry, services, ledger ordering and store initialization.
- JavaScript syntax, shell syntax and full implementation diff checks passed.
- Fresh wheel/sdist private-file and path audit passed. An isolated installed wheel passed dependency checks, packaged discovery/dashboard assets, private onboarding and conservative defaults.
- Credential-pattern scan inspected **1,280 reachable Git blobs**, with no matched key/token/private-key patterns. This is a limited pattern scan, not proof that all secrets or personal data are absent.
- The Pi dashboard is loopback-bound. Public page, unauthenticated API and authenticated API checks returned **200 / 403 / 200**. Live 320/1440-pixel loads passed with no JavaScript errors or horizontal overflow; screenshots stay private. Timeout fixtures were visually reviewed. The UI detector reported no findings for the changed JavaScript.
- Dashboard user service is enabled, with user lingering already enabled. Cron remains the sole application scheduler; no second worker timer was installed.

## Coverage and unresolved release gates

Ashby and Greenhouse were exercised through preparation, verified resume upload and final submission attempts. Lever exposed the ungrounded historical-year failure and received a deterministic repair. Workday's public posting adapter was exercised but authenticated application remained blocked. SmartRecruiters lacked a supported entry; AMD's iCIMS-facing shell did not render application controls; an Eightfold career portal exposed the telemetry classification issue. A Taleo posting was read and rejected by discovery's destination allowlist. These are distinct stages, not eight supported end-to-end adapters.

1. **Employer acceptance and verification:** spam rejection and pending email verification must be reconciled without retrying an uncertain application or bypassing employer protections. Transport HTTP 200 alone is not confirmation.
2. **Account and multi-step adapters:** complete explicitly verified native flows, resume acknowledgements, remote-draft grants, account/verification behavior and final outcomes for Workday, SmartRecruiters, iCIMS, Eightfold and Taleo. Registered hosts do not establish application capability.
3. **Answer correctness:** extend deterministic checks and adversarial evaluation for historical dates and other compound fields. Model source review does not provide a universal factual guarantee. A missing high-school year remains applicant input.
4. **Email product scope:** the existing Gmail integration binds owner identity, Greenhouse codes and batch reports. Recruiter/interview/rejection classification and broader application-status synchronization still need a dedicated implementation and evaluation; this sprint did not send follow-up messages.
5. **Public distribution scope:** this remains a single-applicant local product. A hosted offering requires independent identity/tenant boundaries, operational controls, recovery and deployment proof.
6. **Operations and UX:** the Pi has limited free disk after its private safety backup. Large-ledger scans still have measurable cost. The design tool reported stale `.impeccable/design.json` context and an unset build path; the metadata was not refreshed as an incidental UI change.

See the private report for every attempted posting, exact stages and outcomes, model usage, resume/source evidence, and final restoration checks. No new provider billing path or external-limit bypass was introduced.
