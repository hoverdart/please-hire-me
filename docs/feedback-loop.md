# A job search that learns from what happens

The product standard is simple: start please-hire-me, let the supported work run, and return to an accurate account of what it did, what happened afterward, and what needs attention next.

Discovery, applications, email and networking should contribute to that account. A growing collection of disconnected agents would add work instead of removing it.

## The foundation now in place

The existing SQLite ledger is the shared record. A read-only application view joins each exact attempt to its posting identity, document hashes and known resume revision, submission timestamps, receipt text, screenshot reference, reviewed recruiting messages and next action. It is available under **Answers & evidence** and as a private JSON download. Recorded PDFs remain independently downloadable.

New preparation and submission-intent journal entries capture company, role, URL, source and known platform origins. These snapshots survive later posting refreshes. Older attempts without a snapshot explicitly label current job metadata as a fallback; the app does not reconstruct historical identity from today's listing.

Submission outcome and recruiting stage remain separate. An interview message can appear beside an uncertain submission, but cannot resolve that uncertainty or authorize another application. Suggested mail categories require review before becoming recruiting progress. Missing feedback remains unknown, not rejected.

The view reads saved local data; opening it does not sync Gmail, call a model, change worker settings or send an application. Its timeline separates message arrival from the time its category was reviewed. The JSON download includes private recruiting subjects and references, so it belongs with the user's private application history.

## Priorities

| Priority | Work | Current position and next proof |
| --- | --- | --- |
| P0 | Reliable autonomous applications | Supported transports, durable intents and uncertainty holds exist. Fresh employer receipts, target-machine operation and ordinary scheduled cycles remain release gates. |
| P0 | Truthful, useful resume tailoring | Confirmed-history selection, immutable PDFs and reviewed resume-to-history import exist. Next: selection explanations and applicant evaluation of the generated result. Current ranking is a baseline, not a validated ATS score. |
| P0 | Complete application evidence | Per-attempt records now join posting snapshots, documents, receipts and reviewed feedback. Preserve historical downloads and explicit gaps in older records. |
| P1 | Gmail intelligence | Owner-bound classification suggestions and reviewed links exist. Commission against real acknowledgements, assessments, interviews, rejections, offers and ambiguous matches. |
| P1 | Recruiting analysis | Local Insights now aggregates by captured company, exact role title, PDF bytes and source. It shows response windows, ages, denominators, review coverage and inspectable attempts. Next: evaluate against real reviewed outcomes before recommending strategy experiments. |
| P1 | Relationship tracking and outreach drafts | Planned: connect a supplied contact, public work, draft, conversation and follow-up to a job. Start with applicant-reviewed contacts and drafts; sending messages is a separate explicit action. |
| P2 | Adaptive search strategy | Planned: recommend bounded experiments from observed patterns. Show uncertainty and alternative explanations; do not silently change the search or claim a resume caused an interview. |
| P2 | LinkedIn content assistance | Planned: draft technical or humorous posts from approved projects and writing. Private conversations are not an automatic source, and drafts are not published automatically. |

## Recovery should preserve trust

A useful recovery agent can recognize supported fields, inspect validation errors and repair a source-bound answer before final intent using reviewed transports. An unfamiliar legal agreement, eligibility question, contradictory fact or changed operation still needs applicant input or adapter review. A model's plausible next click is not transport authority.

After final intent, recovery means collecting the existing attempt's acknowledgement and receipt. If the result is ambiguous, preserve it and request verification. Do not restart the application because the UI changed or the browser timed out.

## Measure the feedback loop carefully

An application with no reviewed response is not an observed rejection. A newly submitted application has had less time to receive a response than an older one. A resume version may be used for different roles, companies or sources, so different response rates do not establish that the document caused the difference.

Before recommending a new strategy, show the number of confirmed submissions, uncertain attempts, reviewed responses and applications still awaiting feedback. Compare cohorts with comparable response windows, retain small sample warnings, and describe patterns as observations. Distinct attempts and distinct requisitions need separate counts. A mail match requiring review must not enter a confirmed interview numerator.

The first practical improvement is a useful recommendation the applicant can inspect, such as testing a different role mix. Autonomous strategy changes come after the evidence and controls to evaluate them.

See [release status](public-release.md) for operational gates and the [AutoApply comparison](autoapply-comparison.md) for the source review behind the initial tailoring and feedback work.
