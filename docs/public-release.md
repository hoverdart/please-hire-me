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

Ordinary posting prose about proving that software works does not request an applicant proof. The standard employer notice about not inferring emotions or sensitive traits with AI is a privacy disclosure. Explicit proof instructions, proof questions and applicant-facing no-AI restrictions still require manual completion. After a guard update, Recheck can request a fresh read of an earlier work-sample hold; it cannot reconcile or retry a recorded application attempt.

Generated narrative reviews receive current confirmed facts alongside source excerpts, with current values taking precedence over older source wording. Confirmed fact changes invalidate cached writing.

Exact hourly-rate and LinkedIn example labels can use the corresponding confirmed facts without model inference. Hourly fields still require a confirmed hourly amount; an annual salary cannot establish it. Failed model requests retain their reason and recognized CLI result type beside the request record, excluding provider prose, prompts and answers.

The unanswered-question queue shows captured employer explanations and policy links before the answer control. A Lever checkbox's separate declaration is part of its approval context; an unchanged heading does not authorize changed wording or policy links.

Receipt detection requires fresh completed-application wording after the final click; generic acknowledgements, conditional instructions and incomplete/error messages remain uncertain. Gmail's saved connection status is distinct from `gmail check`, which verifies current API access without reading or sending messages.

Fully transparent verification frames, including transparent parent containers, can belong to a pending passive check. The worker keeps waiting for the outcome and stops if a challenge becomes exposed. Existing uncertain attempts remain excluded from retries after detector updates.

Ashby live runs require acknowledged, source-bound field saves before final submission. Preparation mode suppresses these text-field saves. Unsupported form encodings remain held. Backups stage on private persistent storage and stream archive entries so large Pi ledgers do not require an equally large RAM disk or memory allocation.

Ashby's reviewed structured application-form identifier must name the exact posting and external board. The original identifier string must match the native field-save request and acknowledgement; unknown formats, changed definitions and other postings remain held.

School-only Ashby education fields can save one confirmed university when its exact name has an unambiguous canonical ID from the reviewed official search or an exact official saved value. Forms requesting degrees, majors, dates or additional required entries remain held until their encodings are supported.

Descriptive US work-authorization choices naming any employer require confirmed US citizenship or an unrestricted-authorization declaration, alongside US posting context. General authorization or a lack of sponsorship need cannot establish that status.

An exact prefilled value in a verified official Ashby form response is already acknowledged; a visible browser value alone is insufficient. The posting's exact Application tab can provide a navigation-only entry when the page has ambiguous Apply buttons.

Ashby location saves bind the approved location label to the provider ID from the reviewed official autocomplete response (or an exact official saved value). A different ID, label or unacknowledged save cannot authorize final submission. Tenant renames require a same-host page and an official posting response for the same requisition UUID; the original job, company and application history remain intact. An existing attempt under another tenant with that UUID is held against duplicate submission.

Batch reports preserve unknown and verification-pending outcomes after browser errors, with instructions to verify the existing attempt before applying again. Temporary failures reading an official Greenhouse posting use bounded delayed retries; access restrictions and unrecognized responses require review before any fields are filled.

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
