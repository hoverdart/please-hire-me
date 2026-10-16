# Release status

The configured version is **0.5.0rc2**, a public release candidate for a self-hosted, single-applicant installation. Stable-release validation remains incomplete. This document describes capability boundaries and release gates; dated implementation reports are retained in Git history.

## Capability boundaries

| Area | Implemented scope | Required external evidence |
| --- | --- | --- |
| macOS / Linux / Raspberry Pi | Source installation, packaged dashboard and discovery resources, private onboarding, local scheduling and portable history | Installation and operation on the target machine |
| Greenhouse / Ashby / Lever | Guarded supported forms, confirmed facts, scoped answers, hash-verified uploads, durable submission intents and outcome handling | Posting-specific employer acceptance; recognized hosts do not certify every form |
| Workday configured tenants | Public discovery and posting inspection, manual sign-in handoff, default-off draft-write protocol | Authenticated step/save/upload transport and a posting-specific receipt; application completion remains manual |
| Handshake / Work at a Startup | Separate profiles, disabled connection/discovery/native-apply controls, immutable materials and fixture-validated machinery | Authorized access and independently validated platform endpoints, payloads, document acknowledgements and receipts |
| Gmail | Owner-bound OAuth, recognized Greenhouse codes and independent batch-report outbox | Actual authorization, code binding and delivery; broader recruiter/interview/rejection synchronization is not implemented |
| Employer accounts | Optional supported native signup/sign-in, exact credential readback, separate intents and manual reconciliation | Verified account transport and agreements; JavaScript/SSO and unsupported verification remain manual |
| Unknown portals / controls | Discovery where recognized, with actionable holds for unsupported application behavior | A bounded adapter with matching form and outcome evidence |

Models can still make factual mistakes. Grounding reviews, confirmed sources and deterministic validators reduce risk but do not establish universal answer correctness. Unknown personal/legal facts require applicant input. An HTTP 200 response, prepared draft or saved remote draft is not an accepted application.

Generated narrative reviews receive current confirmed facts alongside source excerpts, with current values taking precedence over older source wording. Confirmed fact changes invalidate cached writing.

Receipt detection requires fresh completed-application wording after the final click; generic acknowledgements, conditional instructions and incomplete/error messages remain uncertain. Gmail's saved connection status is distinct from `gmail check`, which verifies current API access without reading or sending messages.

Ashby live runs require acknowledged, source-bound field saves before final submission. Preparation mode suppresses these text-field saves. Unsupported form encodings remain held. Backups stage on private persistent storage and stream archive entries so large Pi ledgers do not require an equally large RAM disk or memory allocation.

Preparation can upload approved documents. Lever's native resume parser is limited to the exact approved PDF and the rendered employer account; the returned storage acknowledgement must match the form before submission. An upload acknowledgement is not an application receipt.

## Release gates

Before publishing a stable release:

1. Pass the complete local suite, synthetic browser fixtures and supported CI matrix at the release revision. Verify isolated wheel installation and source/wheel archive contents.
2. Validate upgrades and restoration using a private restored history copy. Preserve original outcomes, facts, approved answers, documents and uncertain holds.
3. Verify the dashboard, authentication boundaries, selected model connection, Gmail if enabled, and a single working scheduler on the target machine.
4. Complete authenticated multi-step adapter validation before enabling the corresponding capability. Workday transport and additional portal flows remain outstanding; fixtures alone do not close these gates.
5. Obtain fresh, separately authorized employer confirmations across the claimed supported flows. Preserve pending verification, rejection and ambiguous outcomes without automatic retries.
6. Audit scheduled cycles for correct limits, stable uncertainty exclusions, actionable blockers and attempted-only report emails. Record operational evidence separately from test results.

Native platform capabilities require reviewed, machine-bound flow evidence. A login or setting cannot manufacture that evidence. A hosted multi-tenant offering requires independent identity, tenant, deployment and recovery validation.

## Installation and upgrades

Use the [README](../README.md) for source installation and the [Pi guide](pi.md) for Raspberry Pi services. Wheels require Python 3.11+, Chromium provisioning and optional Gmail dependencies through the `gmail` extra.

Before upgrades, pause submissions, suspend scheduling, wait for active work to finish, and create and validate a private history backup. Test database migrations on a restored copy. Browser sessions and OAuth/provider credentials are excluded from history backups and need separate recovery.

Rollback uses a verified previous code release and a private restored history copy. Never erase uncertain outcomes to retry them. Restore starts paused; inspect history and reconnect integrations before resuming.

## Verification records

[Contributing](../CONTRIBUTING.md) lists reproducible local checks. CI builds the wheel and source distribution, creates a versioned source archive, audits private-file/path classes and installs the wheel outside the checkout.

Record checks against the exact revision, including pass/fail/skip results. Keep local, hosted CI, browser, device, scheduled-operation and employer-acceptance evidence distinct. Test totals and an old deployment report do not establish the state of a current installation. Personal application details, runtime logs, credentials and screenshots belong in private storage.
