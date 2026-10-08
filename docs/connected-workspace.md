# Connected application workspace

The local dashboard now groups work into Today, Jobs, Materials, Profile and Connections. Preferences and Setup remain utilities. The persistent worker strip exposes pause, discovery and batch controls across views. Today prioritizes progress and exceptions; Jobs uses one filtered, paginated ledger with adjacent desktop details and full-width mobile details. Existing answers, notes, reconciliation, reports, backups, model selection and request limits retain their existing contracts.

## Optional platforms

Handshake and Work at a Startup start disabled. Each has separate connection, automatic-discovery and native-application settings. Posting inspection, preparation, submission and verification also have independent, versioned capability evidence. A successful login or checked setting does **not** establish support for any application flow.

On the machine running the worker:

```bash
hireme connection handshake connect
hireme connection handshake status
hireme connection handshake discover
hireme connection handshake disconnect
```

Replace `handshake` with `workatastartup` for the second platform. Dashboard Connect opens a dedicated visible browser window; finish sign-in yourself, close the window, then choose Check connection. On a Pi, do this on its desktop through Raspberry Pi Connect. Each platform uses its own private profile under the worker's private data directory, separate from the employer browser and personal browsers. Disconnect clears that platform's local session and remote-document mappings while preserving application history. Portable restores disable connections and require fresh sign-in and machine-bound validation.

Native adapters currently provide fixture-validated execution machinery, **not verified production transports**. No production flow records are distributed. Actual platform selectors, read endpoints, upload encodings, request payloads and acknowledgements must be inspected and validated on the dedicated profile before the corresponding capability can become available. There is intentionally no dashboard or CLI switch that manufactures this evidence. Generic JSON/form request fixtures do not establish compatibility with a platform's actual upload API.

Handshake automatic discovery remains visibly unavailable until an authorized access route is established. Its [terms prohibit third-party bulk collection using automated scripts](https://joinhandshake.com/legal/tos/); lowering request rates does not establish authorization. For both platforms, automatic discovery is bounded to five pages or 100 distinct jobs per connection per cycle. Platform execution is sequential under existing worker/browser locks, cancellation, time and request budgets. An individual source failure preserves saved listings and permits other sources to continue.

Saved platform links can be imported separately. A verified external requisition destination enters the employer pipeline and retains the platform origin. Canonical employer requisition URLs deduplicate records; company/title similarity never merges jobs. Board URLs are held. Changed destinations or conflicting company/role identities retain a reviewable candidate and hold safe, unattempted jobs without rewriting their identity or past evidence. Opening an external link never records an application as sent.

## Materials and native execution

Reusable sources remain separate from generated application revisions. A job can generate a grounded cover letter, tailored introduction or supplemental narrative PDF using the selected model, existing billing mode and existing request allowance. Optional platform-profile suggestions are generated only on demand and can be reviewed and copied. The app does not edit platform profiles automatically.

Generated artifacts bind source revisions, facts, posting context and exact content hashes. Revisions preserve earlier bytes and application packages. Current-source validation applies before reuse; historical downloads verify recorded bytes without imposing today's source selection. Narrative PDFs enforce supported characters, extraction fidelity, one-page layout and the Handshake upload-size boundary. Project URLs must appear in approved factual sources. Official records and substantive work samples require applicant-supplied originals; unsupported upload requirements are held for manual completion.

Handshake uploads require exact approved bytes, private visibility and acknowledged remote document IDs. Changed materials create new uploads, never replacements. This matters because [replacing a Handshake document updates past applications](https://support.joinhandshake.com/hc/en-us/articles/219132587-How-to-Edit-or-Delete-a-Document). Its [document guidance specifies a 1 MB limit and private/public controls](https://support.joinhandshake.com/hc/en-us/articles/218692648-How-to-Upload-a-New-Document). A preselected stale resume cannot substitute for the approved document. Quick Apply is treated as a potentially final action and cannot be clicked before requirements are known.

Work at a Startup introductions use approved writing context and verified profile facts/resume. Stale or conflicting facts, relocation controls and unexpected profile-changing actions stop preparation or submission. Verified outcomes display **Introduction sent** and preserve the message, exact request reference and acknowledgement. No automatic follow-up is supported.

Native write grants bind a reviewed origin, path, method and complete payload to one operation. Unknown writes, changed forms/sources, unsupported requirements, pause and exhausted budgets stop execution. A durable submission intent precedes the final click; ambiguous outcomes stay held across restart and cannot automatically retry. Login windows are explicitly user controlled and do not grant worker application writes.

## API and storage compatibility

Authenticated connection routes live under `/api/connections`; artifact routes live under `/api/artifacts` and `/api/artifact-document`. Existing Host/Origin/capability checks and read-only demo behavior apply. Job responses add origins, destination, requirements, identity-review candidates and capability metadata. Application packages and private receipts load only on explicit evidence requests. Polling preserves active drafts and keyboard focus; drafts remain in page memory.

SQLite additions cover connection settings, versioned flow evidence, listing origins, identity conflicts, artifact revisions, remote-document acknowledgements, application receipts and saved-view connection filters. Existing job IDs, application records and historical evidence are retained. Browser sessions are excluded from portable history and release artifacts.

## Verification boundaries

| Capability or gate | Evidence in this change | Remaining evidence |
| --- | --- | --- |
| Additive storage, identity conflicts and history | Local migrations, exact requisition deduplication, conflict candidates, artifact revisions and restore fixtures | Worker migration on the physical Pi |
| Dashboard and materials | Desktop/mobile browser fixtures, draft/focus and authenticated-route checks; PDF extraction/character/layout/size checks | Physical Pi desktop and system Chromium behavior |
| Native forms | Local Handshake/Startup fixtures: exact immutable private uploads, stale selections, supplemental documents, acknowledgements, Quick Apply, profile-changing controls and external routing | Actual platform transport and account-specific requirements |
| Discovery | Disabled/access gates, session expiry, failure isolation, pagination limits and preserved jobs in fixtures | Authorized Handshake route; signed-in discovery for each platform |
| Submission and recovery | Local durable-intent, exact-payload, pause/budget, unknown-write and restart/no-retry fixtures | Separately authorized live upload/send and acknowledgement checks |
| Packaging | Wheel/sdist privacy audit and isolated wheel installation; JavaScript, Python and shell syntax checks | New runtime on the worker machine |
| Visual finish | Impeccable detector and independent desktop/mobile finish review of synthetic fixtures | No approved image comp or QUALITY BAR reference was supplied; no comp fidelity claim |

No real application was submitted, profile edited or follow-up sent during implementation. Dedicated-platform authenticated inspection and physical Pi validation were not performed: no dedicated signed-in session or configured Pi connection was available in this checkout. Both connections remain disabled and production native submission remains gated.

Final local verification on October 7, 2026:

- Complete pytest suite: **1,138 passed**, five third-party PyMuPDF deprecation warnings, 317.75 seconds.
- Final desktop/mobile workspace regressions: **3 passed**, including visible metric labels, draft/focus preservation, filters and authenticated API validation.
- Wheel and sdist builds, private-file audits and isolated wheel installation passed. Python compilation, JavaScript syntax, shell syntax, dependency consistency and whitespace checks passed.
- Impeccable detector found one small-text warning; it was corrected. Independent finish disposition: **ship**, covering the scored corrections. The documentation and synthetic screenshot provenance are recorded. No approved comp fidelity, live account, Pi or employer submission proof is implied.
- Dedicated-platform authenticated inspection, authorized discovery and physical Pi operation remain **not verified**. Actual platform submissions: **zero**.
