# Recurring application failures — October 8, 2026

The physical Pi was running an older checkout and its approved personal context was present. The worker used that context for essays, but did not consistently use explicit declarations in it for structured questions. Several additional failures came from field parsing, missing posting text, and mixing internship availability with permanent employment availability.

## Corrections

| Observed failure | Repair |
| --- | --- |
| Structured questions repeatedly requested information already stated in approved context. | Add an explicit-context path with exact source quotes, independent model review, and source revision validation. Compound declarations must establish every part and time window. |
| Name pronunciation was already stated in the context. | Reuse the explicit pronunciation line with source provenance; conflicting sources still require review. |
| Language checkbox groups produced option mismatches. | Preserve multiple exact choices, selection limits and language punctuation. Confirmed C does not imply C++. |
| Sierra's candidate qualities question missed the essay path. | Use reviewed drafting from approved material, with bounded repair attempts. Unsupported writing is a provider outcome rather than a missing personal fact. |
| Lever read radio labels such as “Yes” or “Select One” as the question. | Read the employer question heading and required marker. Discovery cards can contain a separate optional detail field; ambiguous cards remain blocked. |
| Lever's application page replaced the posting with only its title and location. | Verify the requisition, title and destination against the official public posting API and retain its complete posting text for writing. Missing or changed posting evidence blocks preparation. The endpoint and fields follow [Lever's official Postings API](https://github.com/lever/postings-api). |
| Permanent start answers reused internship dates. | Keep a distinct confirmed permanent employment date and use it for full-time roles and their start-window screening. Summer internship preferences stay separate. |
| Model calls occasionally exceeded the Pi's 90-second deadline. | Review the exact timed-out request and increase the Pi deadline to 180 seconds. Durable daily and per-cycle request limits remain unchanged. |
| Sierra preparation stopped after filling its decimal GPA. | Its controlled input mirrored the entered GPA into its default-value attribute. Normalize only a blank numeric default updated to the exact verified answer when there is no explicit step. Changed bounds, explicit steps, unexpected values and unrelated form changes still block advancement. |
| A required demographic survey can block a form when the applicant has not supplied that trait. | Add an explicit opt-in preference to use one exact offered decline choice, while preserving supplied answers. The preference remains unset on this Pi pending the user's answer. Optional unanswered surveys stay blank; agreements and government declarations are excluded. |
| Claude's subscription limit appeared as repeated generic inference errors. | Recognize its JSON error on stdout even with exit code 1, including “hit your session limit.” Stop the cycle on the existing `provider_rate_limited` path, without asking for applicant facts or making more field calls. Normal model output containing the same words does not establish a vendor limit. |

The user-confirmed age, permanent start, nights/weekends, robotics and humanoid answers were saved separately. Robotics experience does not imply humanoid experience. The existing internship availability was preserved.

## Verification

- Final source `b6eaaf10bfc396d93b44bab10fcbe129fb7bbaa5`: **1,185 local tests passed in 326.97 seconds**, with five existing third-party PyMuPDF warnings.
- Physical-Pi source `7a72d6bf62640a7927e7ca0693cfa45f825cb034`: **188 focused tests passed in 25.42 seconds**. The subsequent subscription-limit repair awaits final deployment and Pi checks.
- Physical-Pi browser and focused checks at preceding source `0e30e030598fa9474d41473f3f81c67edcf8d236`: **273 tests passed in 436.60 seconds**. That source's browser implementation is unchanged by the opt-in survey preference.
- The final decimal-input correction also passed **13 targeted browser tests in 11.08 seconds**, including a local synthetic submission with a decimal GPA and a conditional field.
- Whitespace, JavaScript syntax and both shell entrypoint syntax checks passed.
- Live Heliux preparation completed with the corrected required answers and resume. Its bounded submission received an explicit Ashby possible-spam rejection and was persisted as `not_submitted`. It was not retried or classified as confirmed.
- Live Hermeus preparation retained the verified public posting body, resolved the discovery card heading and reused its reviewed mission answer. The timed-out education request completed after the deadline adjustment but abstained on completed junior-year standing, which is not explicitly established by the saved context. No Hermeus submission occurred. The obsolete discovery-placeholder questions are resolved; its education hold remains.
- Final live Sierra preparation resolved all required fields, including pronunciation, reviewed essays and decimal GPA. Its bounded submission received an explicit Ashby possible-spam rejection, persisted as `not_submitted` with its post-attempt screenshot. It was not retried or classified as confirmed. Its original missing-answer questions were retired automatically after current-field resolution.
- Immuta's full-stack internship preparation stopped at its employer-specific privacy policy checkbox. The checkbox links to a [job-applicant privacy policy](https://www.immuta.com/legal/privacy-policy-job-applicants/) that returned 404 during inspection. Generic recruitment-data consent was not silently expanded to that agreement.
- Klaviyo's engineering internship preparation stopped only at its required sexual-orientation question. The source does not state an answer. Its offered decline choice can be used if the user confirms the new preference; no trait was inferred and no submission occurred.
- Tower's developer internship preparation stopped on its government/PEP and family declarations, with additional provider errors recorded during later fields. A single metered diagnostic isolated these errors: Claude returned exit code 1, empty stderr and a JSON error stating “You've hit your session limit” with a **5:40 a.m. America/Los_Angeles reset**. No Tower submission occurred. The saved context's narrower government declarations do not establish these broader answers.

## Evidence boundaries

The approved Pi context does not contain an explicit never-served-in-the-military declaration, the full five-year government/PEP relationship and referral declaration, demographic-storage consent, or acceptance of Roblox arbitration. Related statements do not establish those answers. Employer-specific agreements remain explicit decisions. These holds are separate from context lookup defects.

No new employer acceptance was confirmed during these checks. Ashby's possible-spam response does not identify its cause, and a local check that ordinary browser input events are trusted does not prove the Pi passed employer-side anti-abuse checks. No CAPTCHA, anti-abuse protection or browser-security check was bypassed. Further source-grounded model preparation must wait for Claude's vendor reset; no API-credit fallback or quota override was used.

The repair does not complete the broader public-release gates. Unknown submission outcomes must never be retried automatically.

## Runtime handoff

Before deployment, the worker was paused, its cron entry removed temporarily, and a private SQLite backup, original crontab and baseline snapshot were retained on the Pi. Backup `PRAGMA quick_check` returned `ok`. This is a code/data rollback snapshot rather than a claimed portable archive restore test.

The preservation audit verified that all **37 original application rows**, **51 original fact rows**, **25 original approved answers**, **6 original templates**, and **65 original report rows** were unchanged. Five confirmed facts and one robotics-context template were added. The two new application rows are the explicit Heliux and Sierra rejections. All seven unknown outcomes and the existing verification-pending application were preserved. Model reservations increased by 88 during the application checks, then by one for the metered provider diagnostic; no quota history was reset. Reports were temporarily disabled during supervised checks, with 12 new outbox records retained.

The dashboard was restarted from the tested source on port **8766**. Its page and authenticated state API returned **200**, while unauthenticated state API access returned **403**.

The tested source was fast-forwarded and pushed to `main`. During final deployment, Brave stopped exposing a usable window (`cgWindowNotFound`), preventing further access to the Pi Connect shell. A request to unlock/open Brave is pending. The last verified Pi checkout is `7a72d6b`; the last dashboard restart used `0e30e03`.

**Pending runtime actions:** deploy the final subscription-limit repair, run its focused Pi checks, restart and health-check the dashboard, and restore the exact saved cron entry and original `gmail_reports=true`. Until this is verified, the Pi's automatic schedule remains removed and reports remain temporarily disabled. `live_enabled=true` and the original request ceilings (300/day, 100/cycle) are preserved; the intentional model deadline remains 180 seconds.

The [Pi completion script](finish-oct8-pi-repair.py) performs those actions under the worker lock, requires the tested source, checks original ledger rows, refuses to stop an unrelated listener, and verifies dashboard authentication and the exact restored schedule. It passed local Python compilation; it has not yet run on the Pi. If Pi Connect access cannot be restored, the operator can run:

```sh
cd ~/please-hire-me
git pull --ff-only origin main
.venv/bin/python docs/finish-oct8-pi-repair.py
```

The script prints only test results and runtime verification metadata; it does not print the dashboard token or application answers.
