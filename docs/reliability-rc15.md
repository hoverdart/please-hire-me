# Application browser candidate 0.5.0rc15

This public candidate adds an application browser that observes the current page, fills confirmed answers, verifies uploads and advances the employer's visible workflow. It uses the site's own client for application transport. Operational native adapters remain preferred, and existing native drafts keep their original transport.

## What changed

- Workday supports custom prompts, virtualized academic choices, structured history, saved-application continuation, split phone fields, questionnaire legends and scoped agreements. Saved history binds by source identity rather than row order. Unknown or duplicate saved rows require review; the executor does not delete records.
- A dedicated employer sign-in action opens the app's own browser profile. NVIDIA sign-in verifies the confirmed owner before retaining its session. Personal browser profiles and tabs are never attached.
- Handshake and Work at a Startup saved listings can follow verified external employer links. Enabled native-only listings can use the adaptive browser when account ownership, document contents and controls can be verified. Bulk discovery and fixed native transport commissioning keep their independent gates.
- Every transmitted UI mutation has a durable intent. Approved sources, document hashes and controls are rechecked before writes. Saved steps survive restart, and uncertain saves or submissions block automatic retries.
- Final confirmation requires a new receipt and correlated employer evidence. Opaque Workday acknowledgements can use a fresh owner-verified Application Received record for the exact application and requisition, without resubmitting.
- Runtime and development locks update `pypdf` to 6.21.4 and `pyasn1` to 0.6.5 to address the advisories found by the dependency audit. Core PDF installations and the Gmail extra require the fixed minimum versions, including upgrades of existing environments.

## Enable multi-step applications

After onboarding, open Search & automation and enable live applications and **Allow live application steps to save…**. Remote draft saves remain off in a new installation until the applicant opts in. Keep the adaptive browser enabled. Use **Sign in to employer** when a saved job needs authentication, and answer missing factual or agreement questions in the dashboard. Approval of an application-specific agreement does not approve another job or changed terms.

Models can choose supplied action IDs; they cannot author browser code, selectors, requests, applicant values or credentials. The Python executor owns policy, sources, budgets, browser isolation and outcome verification.

## Evidence and scope

An authenticated NVIDIA application completed the visible manual workflow. The UI displayed Application Submitted; a new authenticated read for the same application ID and requisition reported `draft=false` and Application Received. Work history, education and the approved resume association were independently checked. The original uncertain final acknowledgement was reconciled from the employer record without another submission. The new automatic readback has synthetic coverage and accepts that independently captured record.

Local tests, hosted CI, clean artifact installation, private-history restoration and target-machine checks remain separate evidence. Publish only artifacts from the exact revision that passed the release checks. Keep personal logs, screenshots and receipts outside Git.

NVIDIA evidence does not certify every Workday tenant. Native-only Handshake and Work at a Startup acceptance, cross-origin frames, unfamiliar widgets and other employer-specific flows may still require help. CAPTCHA, unknown declarations and applicant work samples remain human handoffs. This candidate is intended for evaluating the guarded local workflow; broader unattended and stable-release claims still require the operational evidence described in [release status](public-release.md).
