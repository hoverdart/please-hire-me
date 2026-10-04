# v0.5.0 release candidate

This document tracks current release gates. It is not a public readiness certificate.

Baseline: `5c864cb`; candidate version: `0.5.0rc1`. Existing application outcomes remain authoritative. The release target is a self-hosted installation for one applicant, with a private loopback dashboard.

## Current implementation evidence

- Model controls explicitly select Sonnet/medium, with an opt-in bounded Opus/high mapping reconsideration. The same provenance and conversion rules apply to both.
- Durable model metadata records selected model, effort, duration, success and available token counts. Counters do not represent vendor credits.
- Temporary 500/day, 200/cycle limits have a persisted expiry and restore prior settings after restart. The Pi's permanent limits were changed to 300/day and 100/cycle; company and submission limits were preserved.
- HP IQ's graduation question was incorrectly included in screening text. Posting/form separation and month/year comparisons now have regression coverage.
- Roblox’s direct board URL redirects to its custom careers domain. A narrow adapter verifies the Greenhouse posting ID/title through the public API and navigates its Greenhouse embedded form instead; the original ledger URL remains unchanged. Read-only form inspection succeeded without employer writes.
- Required discovery data is bundled into the wheel. The first wheel passed installation, source discovery, dashboard resource and private fresh-onboarding checks outside the checkout, without credentials or model requests.
- Workday write protocol tests cover exact one-use grants, remote-draft opt-in, source revision checks and interrupted-save holds. This is protocol evidence, not proof of an authenticated employer application adapter.

The outcome-diagnostic implementation at `2e22b10` passed **745 local tests** (224.37 seconds, five existing PDF-library warnings) and all four CI jobs: macOS 14/Python 3.12, Ubuntu x86-64/Python 3.11 and 3.14, and Ubuntu ARM64/Python 3.11. Each CI job builds distributions and installs the wheel with declared dependencies in a fully isolated environment. Artifact private-file/path audits and fresh-onboarding checks passed. Desktop and 390/320-pixel controls preserved drafts without overflow or JavaScript errors.

The physical Pi passed the 725-test dropdown baseline (663.35 seconds), followed by 11 scheduler tests, eight numeric/browser tests, and 76 answer reliability tests covering subsequent changes. The outcome diagnostics passed three focused Pi browser tests and isolated wheel installation/private-file audits. The physical Pi full run exposed a cancellation-fixture race: fake preparation ended after five seconds before the slower browser loaded. The interrupted run recorded 221 passes and one failure. Commit `53bd93f` waits for actual cancellation (bounded at 60 seconds); both focused Pi cancellation/preparation checks passed in 14.72 seconds, followed by 745 passing local checks (226.48 seconds). The final physical-Pi wheel passed isolated dependency installation, bundled discovery/dashboard-resource checks and conservative private onboarding. The physical full-suite rerun passed **745 tests in 727.81 seconds**. The custom-receipt revision `0fe52f3` passed **751 local tests in 227.80 seconds**, its isolated wheel installation/private-file audit, and **11 focused Pi tests in 89.32 seconds**. All four CI jobs passed full suites and isolated artifact installation at `0fe52f3`; the final physical full-suite run passed **751 tests in 706.81 seconds**. Synthetic restricted Sonnet/medium subscription inference passed both the worker environment and a cron-style environment; durable reservations record requests and available token metadata. No API-key fallback or vendor billing changes were made.

A private backup restored to a separate directory with identical counts: 87,723 jobs, 10 applications, 20 approved answers and 82 questions; outcomes remained one confirmed, five not submitted and four unknown. Additive migrations preserved the original ledger. The latest restored copy also matched the current 87,723 jobs, 13 applications, 22 approved answers, 84 questions, 132 model reservations and nine metadata records; it remains paused. Later pre-deployment backups passed full ZIP CRC validation, including the 156,848,096-byte backup retaining the new ID.me receipt, Veeam uncertainty and local prepared package (credentials excluded). These are private operational artifacts, not public downloads. The final pre-deployment backup passed full CRC validation (158,235,333 bytes, 44 ZIP members). Its restored copy matched 87,723 jobs, 13 applications, 23 answers, 85 questions, 132 model reservations and nine metadata records, retaining three confirmed applications and all five unknown outcomes.

The verified runtime revision `0fe52f3` is deployed on the Pi. Post-deployment checks passed: loopback dashboard and authenticated APIs, subscription-only Claude connection, Gmail owner authentication, worker setup, five configured Workday discovery tenants, and the six-hour schedule. Permanent limits are 300 requests/day and 100/cycle with no temporary override. Submission/company limits and all uncertain outcomes were preserved. A private read-only observer is waiting for the 06:00 and 12:00 Pacific scheduled cycles; those cycles have not yet been observed.

### Supervised live evidence on October 4

Six supervised browser attempts produced two fresh confirmations at different Greenhouse employers:

| Employer | Attempt | Outcome | Evidence / next action |
| --- | --- | --- | --- |
| Veeam | 1 | Blocked before submission | Delayed dropdown options were not hydrated; fixed with real-form readback and regression fixtures. |
| Veeam | 2 | Blocked before submission | Controlled graduation year changed the HTML value attribute; effective numeric-constraint comparison now preserves genuine change detection. |
| Veeam | 3 | Unknown after final action | Employer displayed a generic processing error. No receipt found; exclude from automatic retry and inspect manually. |
| ID.me | 4 | **Confirmed** | Email verification completed; employer stated “We’ve received your application.” Private receipt screenshot and ledger confirmation retained. |
| Amperesand | 5 | Prepared locally | Seven source-grounded model requests; no employer submission. |
| Amperesand | 6 | **Confirmed after receipt inspection** | Email verification completed. The saved employer receipt promised Talent-team review and thanked the applicant for applying. Initially recorded unknown because custom wording was missed; reconciled from the screenshot without another employer write. |

Every completed attempt report was sent through Gmail with its attempted job link. A real-ledger worker check of the held prepared job and all five uncertain jobs produced zero browser attempts and zero model requests, preserving every application state. Known ID.me graduation/GPA facts resolved without model calls, and the five-day onsite statement was explicitly approved. The new Veeam uncertainty joins the existing protected uncertain applications; it has not been retried. Read-only candidate inspections and local form fixtures are not employer submissions. Amperesand’s five-day onsite requirement was explicitly approved for San Francisco and saved only in that employer context. The sixth attempt selected San Francisco and reused approved writing; it consumed no additional model requests. A correction email reported the inspected confirmation. The original run’s unknown event remains as historical evidence; reconciliation is recorded separately. Six of the twelve supervised attempts remain. Veeam remains uncertain and excluded.

- [ID.me posting](https://job-boards.greenhouse.io/idmeuniversityrecruiting/jobs/7980429003)
- [Amperesand posting](https://job-boards.greenhouse.io/amperesand/jobs/4381214009)

The owner approved NVIDIA remote draft saves for supervised validation, but cannot perform the required manual account login now. Authenticated Workday transport inspection and live confirmation remain unavailable; no Workday automation capability is claimed. Veeam’s three business/consent questions were explicitly approved and saved privately. Confirmed employer history was updated privately; the conflict disclosure remains scoped to Veeam.

## Owner-supplied employer credentials

The candidate now accepts employer-scoped passwords through **Needs you → Employer accounts → Supply credentials for an employer**. Submissions must be paused and the worker idle. The confirmed applicant email is reused; matching passwords must contain 20–128 characters. Saving credentials creates no employer account and never replaces existing account history. Recognized native registration forms reuse the supplied password; their separate registration intent and exact write grant remain required. Explicit and implicit account agreements stop before credentials are accessed. Passwords remain outside models, ledger events, reports and ordinary backups.

Supplied-password and agreement fixtures passed locally and on the Pi. The configured Workday shards now permit narrowly scoped first-party static assets; this permits rendering resources, not account or application writes. Workday JavaScript registration is still unverified. No real credentials were supplied or employer accounts created during this change. Four-platform CI at `747c882` passed **769 tests per platform**, including artifact installation and privacy checks. A filtered-ledger browser fixture was subsequently corrected to wait for the replacement table and resolve its element inside the same browser task, avoiding a detached-row race without weakening evidence assertions.

## Release gates still pending

1. Finish authenticated Workday adapter inspection and browser fixtures. NVIDIA account setup still needs a verified JavaScript registration/login flow or manual setup; remote draft saves require separate owner opt-in. Unknown agreements remain review items.
2. Obtain the remaining new Workday confirmation within the six remaining supervised attempts. Both distinct Greenhouse confirmations have been obtained. Never retry prior uncertain outcomes or count draft saves/preparation as submissions.
3. Observe the next two scheduled cycles (October 4 at 06:00 and 12:00 Pacific), confirm unchanged blockers/uncertainty exclusions and attempted-only emails. Permanent model caps are already restored.
4. Publish v0.5.0 only if every gate passes. Otherwise retain a clearly marked candidate and exact outstanding blockers.

Versioned source archive, wheel and source distribution at `0.5.0rc1` pass private-file/path audits. CI retains all three as downloadable candidate artifacts for 30 days. No v0.5.0 release has been published.

## Installation and upgrade contract

Install the source archive using `./setup.sh`, or install the wheel with Python 3.11+ and provision Playwright Chromium (`python -m playwright install chromium`). On Pi use system Chromium and the `hireme pi` setup commands. Optional Gmail dependencies are installed through the `gmail` extra.

Start `hireme dashboard` locally. Confirm applicant facts and documents; authorize model processing explicitly; submissions start paused. `hireme model-probe` sends one synthetic structured request and consumes model allowance. Claude subscription login is required for subscription mode; unsupported CLI isolation/model/effort flags stop rather than falling back.

Before upgrades, pause submissions, suspend scheduling, wait for worker locks, and create/validate a private history backup. Test migrations on a restored copy. Preserve browser sessions and OAuth credentials separately because normal history backups exclude them. Roll back using a verified previous code release and a private restored history copy; never erase uncertain outcomes to retry them.

## Support matrix

| Platform / ATS | Candidate capability | Release proof still required |
| --- | --- | --- |
| macOS / Linux x86-64 / Linux ARM64 | Source and wheel installation; private single-applicant workspace | Four-platform CI and physical Pi full suite passed at `0fe52f3` (751 tests) |
| Greenhouse | Existing guarded single-step submissions; segmented verification; approved contextual answers | Both required fresh employer confirmations obtained; custom-receipt regression passed |
| Workday configured tenants | Discovery, manual session handoff, contextual snapshot and guarded write-protocol foundation | Authenticated transport, uploads, step acknowledgements and posting-specific receipt |
| Ashby / Lever | Existing guarded functionality, preview support | Separate employer confirmation evidence |
| Unknown tenants / widgets | Manual completion with actionable review | A recognized adapter and matching evidence |

No authenticated Workday browser step/save/upload/receipt transport has been enabled in this candidate. The protocol controller is scaffolding, and fixtures cannot substitute for live employer proof. NVIDIA draft-save authorization is limited to supervised validation; it does not authorize account registration or new agreements.
