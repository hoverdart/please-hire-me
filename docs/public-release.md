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

The local candidate suite passed **723 tests** (163.35 seconds, five existing PDF-library warnings). The final Workday provenance guard also passed 31 focused checks. JavaScript syntax, dependency checks and final wheel installation/private-file audits passed. Desktop and 390/320-pixel controls preserved drafts without overflow or JavaScript errors. Pi and CI gates remain separate. The private 153,401,638-byte Pi backup restored to a separate directory with identical counts: 87,723 jobs, 10 applications, 20 approved answers and 82 questions; outcomes remained 1 confirmed, 5 not submitted and 4 unknown. The original ledger was preserved.

The owner approved NVIDIA remote draft saves for supervised validation, but cannot perform the required manual account login now. Authenticated Workday transport inspection and live confirmation remain unavailable; no Workday automation capability is claimed. Veeam’s three business/consent questions were explicitly approved and saved privately. Foundation Industries was added to employer history.

## Release gates still pending

1. Finish authenticated Workday adapter inspection and browser fixtures. NVIDIA account setup is manual; remote draft saves require separate owner opt-in. Unknown agreements remain review items.
2. Complete fresh-install/full-suite verification of the final candidate on macOS, Linux x86-64 and ARM64/Pi; inspect public artifact contents and UI controls.
3. Deploy the exact passing candidate commit and verify subscription inference/Gmail/scheduler. Private backup validation and migration-copy preservation have passed; deployment is still pending.
4. Within twelve supervised browser attempts, obtain three new confirmations: two different Greenhouse employers and one Workday tenant. Never retry prior uncertain outcomes. Do not count draft saves or preparation as submissions.
5. Observe two six-hour scheduled cycles, confirm unchanged blockers/uncertainty exclusions and attempted-only emails, and restore permanent model caps.
6. Publish v0.5.0 only if every gate passes. Otherwise retain a clearly marked candidate and exact outstanding blockers.

## Installation and upgrade contract

Install the source archive using `./setup.sh`, or install the wheel with Python 3.11+ and provision Playwright Chromium (`python -m playwright install chromium`). On Pi use system Chromium and the `hireme pi` setup commands. Optional Gmail dependencies are installed through the `gmail` extra.

Start `hireme dashboard` locally. Confirm applicant facts and documents; authorize model processing explicitly; submissions start paused. `hireme model-probe` sends one synthetic structured request and consumes model allowance. Claude subscription login is required for subscription mode; unsupported CLI isolation/model/effort flags stop rather than falling back.

Before upgrades, pause submissions, suspend scheduling, wait for worker locks, and create/validate a private history backup. Test migrations on a restored copy. Preserve browser sessions and OAuth credentials separately because normal history backups exclude them. Roll back using a verified previous code release and a private restored history copy; never erase uncertain outcomes to retry them.
