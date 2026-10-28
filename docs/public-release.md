# Release status

The configured development version is **0.5.0rc8.dev1**. The latest published [public release candidate is 0.5.0rc7](https://github.com/hoverdart/please-hire-me/releases/tag/v0.5.0rc7), for a self-hosted, single-applicant installation. Stable-release validation remains incomplete. This document describes capability boundaries and release gates; dated implementation reports are retained in Git history.

## Capability boundaries

| Area | Implemented scope | Required external evidence |
| --- | --- | --- |
| macOS / Linux / Raspberry Pi | Source installation, packaged dashboard and discovery resources, private onboarding, local scheduling and portable history | Installation and operation on the target machine |
| Greenhouse / Ashby / Lever | Guarded supported forms, confirmed facts, scoped answers, hash-verified uploads, durable submission intents and outcome handling | Posting-specific employer acceptance; recognized hosts do not certify every form |
| Tailored resumes | Opt-in per-posting PDFs from confirmed education, employment and skills; exact experience selection, immutable revisions, source checks and supported browser uploads | Applicant review of structured history and generated layout; target-machine and employer upload/acceptance validation |
| History import and Recruiting Insights | Local PDF excerpt proposals with explicit confirmation; full-ledger comparisons with observation windows, review coverage and evidence drill-downs | Applicant review of proposed fields and real reviewed-mail evaluation; local fixtures do not establish parsing or outcome coverage for every applicant |
| NVIDIA Workday | Public discovery, owner-bound sessions, source-reviewed US manual-flow controller, confirmed structured history, exact operation grants and owned-draft reconciliation | Current native save/upload/receipt evidence and bounded commissioning; ordinary scheduling remains disabled until verified |
| Other configured Workday tenants | Public discovery and posting inspection, manual sign-in handoff | Separate tenant-specific transport and employer-acceptance evidence; completion remains manual |
| Handshake / Work at a Startup | Separate profiles, disabled connection/discovery/native-apply controls, immutable materials and fixture-validated machinery | Authorized access and independently validated platform endpoints, payloads, document acknowledgements and receipts |
| Gmail | Owner-bound OAuth, recognized Greenhouse codes, independent batch-report outbox and opt-in recruiting-feedback suggestions with reviewed attempt links | Actual authorization, code binding, mailbox matching and delivery; suggested email classifications require review and cannot establish submission or permit retries |
| Employer accounts | Optional supported native signup/sign-in, exact credential readback, separate intents and manual reconciliation | Verified account transport and agreements; JavaScript/SSO and unsupported verification remain manual |
| Unknown portals / controls | Discovery where recognized, with actionable holds for unsupported application behavior | A bounded adapter with matching form and outcome evidence |

## What the worker protects

- **Confirmed facts.** Unknown personal, legal and demographic facts require applicant input. Current confirmed facts take precedence over older source text, and fact changes invalidate cached writing. Models can still make mistakes; grounding and validation reduce that risk without guaranteeing every answer.
- **Scoped approval.** Saved answers bind to the employer question, choices, declarations and policy links. Changed wording requires review. General US work authorization or no sponsorship need cannot establish unrestricted authorization for any employer. An annual salary cannot establish an hourly amount.
- **Local preparation.** Preparation suppresses remote parsing, uploads, field saves, consent and submission. Read-only autocomplete searches can send confirmed school or location names to the employer's lookup service. Work samples, knowledge tests, explicit applicant proof requirements and applicant-facing no-AI restrictions require manual completion.
- **Bounded writes.** Live applications require reviewed transports, exact approved values, acknowledged uploads and durable submission intents. Approval is checked again immediately before transport. Pause revokes pending writes; an already transmitted request can still return its result.
- **Evidence for outcomes.** Confirmation requires a fresh completed-application receipt and the supported adapter's correlated acknowledgement. HTTP success, a visible filename, saved drafts, generic thanks and preexisting receipt text do not establish acceptance. Missing evidence remains uncertain and blocks automatic retries.
- **Private recovery.** Applicant history stays outside Git. History backups exclude credentials, stage on persistent private storage and stream ledger/document contents. Restores start paused and require integrations to be reconnected.

The [worker contract](worker-contract.md) describes execution and recovery in more detail.

## Supported form limits

**Ashby** binds field saves to the exact official posting, form identifiers, approved source values and matching acknowledgements. Choice labels must map unambiguously to official wire values. School-only education can use one confirmed university with an exact official identifier. Compound education supports confirmed structured records, canonical school identifiers, degree and major text, month/year dates and current-study status. It saves the complete approved record together and requires the matching acknowledgement. Expected graduation never becomes a completed education end date. Missing required details, ambiguous schools, changed controls and unsupported existing values remain held. Locations require an exact approved label and official provider ID. SMS consent has its own operation, confirmed preference, phone and employer policy context; WhatsApp and unrecognized contractual terms remain manual. Tenant renames require official evidence for the same requisition, preserving original history and duplicate holds.

Every live Ashby application uses one exact final request. An optional human-verification wait starts disabled, requires a visible browser and lasts at most 600 seconds. It lets the applicant complete an exposed challenge within that existing attempt. The worker does not solve challenges, authorize unrelated writes or restart an uncertain attempt. Passive transparent verification frames can remain pending while the worker waits for a receipt. Desktop availability and employer acceptance require separate verification.

**Greenhouse and Lever** require the approved PDF and a matching upload acknowledgement before final submission. Lever's resume-parser grant is limited to that PDF and the rendered employer account. Its location picker requires an exact approved suggestion and committed location object. Temporary official Greenhouse posting-read failures use bounded delayed retries; restricted or unrecognized responses require review before filling fields.

**NVIDIA Workday** requires current native definitions, approved values, correlated save responses and fresh owned-record readbacks. Installing the controller does not establish employer acceptance or enable ordinary scheduling. Unknown definitions, unsupported steps and unreadable saved values remain held. Employer application history supplements local duplicate/company-limit checks, and interrupted commissioning recovers paused. Other Workday tenants remain manual.

**Gmail and reports** have separate connection and delivery evidence. Saved OAuth configuration is distinct from `gmail check`, which verifies current API access without reading or sending messages. Batch reports preserve unknown and verification-pending outcomes after browser errors. Recovery uses each interrupted run's own durable attempts. Idle completed/paused cycles with no attempts or new outcomes stay local; failures and meaningful updates remain reportable. A queued report is not delivered mail.

**Recruiting feedback** reads a bounded Gmail page when enabled, after live-mode batches (including paused batches) or on explicit sync. It retains matching message previews privately and suggests acknowledgement, assessment, interview, rejection or offer categories. The applicant confirms a category and exact prior attempt. Latest reviewed milestones appear in Applications and each job's history context; original submission states stay unchanged. Sender authentication metadata is context for review, not independent employer-identity proof. Messages and attachments are never used as model prompts; attachments and links are not fetched. Live mailbox validation remains outstanding.

**Tailored resumes** require explicit opt-in and confirmed structured employment with descriptions. Selection retains up to four complete original lines per employer/role, ordered by posting relevance, with confirmed education, contact details and skills. No generative rewriting or extra model request is involved. The layout supports at most two pages; unsupported fonts or excess content remain held. Changed sources, settings or posting details invalidate execution authority, while historical PDF revisions remain downloadable. The generic supported browser flow uses these PDFs; independently gated platform and Workday controllers retain their own document paths.

## Release channels

**Past applications from email** is a separate opt-in duplicate check. Its initial owner-bound scan has no date cutoff, including archived mail. Exact posting links or possible-employer/complete-role passages hold matching jobs without creating submission records. Ambiguous employer candidates stay held until the applicant reviews and links the exact role; generic company messages require review. Saved holds survive disabling scanning or disconnecting Gmail, and explicitly reviewed role links remain in the same workspace across primary-email changes. Bounded company scans run before live browser work, and final intent requires a check completed within 15 minutes. Connection and scan-lock failures remain retryable, incomplete scans keep progressing, and actionable email-check failures remain reportable without counting them as application attempts. English-language receipt matching, mailbox access and employer-specific wording remain verification limits; no match does not establish that an application never happened.

The release candidate is suitable for evaluating the self-hosted workflow with the capability limits above. It is a prerelease, not a claim of unattended operation or universal ATS support. Stable publication requires every gate below. Workday and optional native platforms stay independently gated; their presence in the interface does not make them supported for automatic submission.

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

Rollback preserves the newest application ledger, including intents and outcomes created after deployment. Test previous-code compatibility on a copy before switching code. Never resume a pre-deployment history backup over newer application records. If compatibility is uncertain, keep the installation paused and preserve both copies. Restore starts paused and invalidates machine-bound execution authority; inspect history and reconnect integrations before resuming.

## Verification records

[Contributing](../CONTRIBUTING.md) lists reproducible local checks. CI builds the wheel and source distribution, creates a versioned source archive, audits private-file/path classes and installs the wheel outside the checkout.

Record checks against the exact revision, including pass/fail/skip results. Keep local, hosted CI, browser, device, scheduled-operation and employer-acceptance evidence distinct. Test totals and an old deployment report do not establish the state of a current installation. Personal application details, runtime logs, credentials and screenshots belong in private storage.

The [final release runbook](final-release-runbook.md) defines the bounded commissioning, evidence collection and rollback sequence. `hireme release-check --json` reads a private snapshot without initializing or repairing applicant history; its integration checks describe saved configuration, not live connectivity or employer acceptance.
