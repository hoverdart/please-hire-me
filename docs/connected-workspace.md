# Connected application workspace

The local dashboard groups work into Today, Jobs, Materials, Profile and Connections. Preferences and Setup remain utilities. The persistent worker strip exposes pause, discovery and batch controls across views. Today prioritizes progress and exceptions; Jobs uses one filtered, paginated ledger with adjacent desktop details and full-width mobile details. Existing answers, notes, reconciliation, reports, backups, model selection and request limits retain their existing contracts.

## Optional platforms

Handshake and Work at a Startup start disabled. Each has separate connection, automatic-discovery and native-application settings. Posting inspection, preparation, submission and verification also have independent, versioned capability evidence. A successful login or checked setting does **not** establish support for any application flow.

From the repository directory on the machine running the worker:

```bash
.venv/bin/python -m hireme connection handshake connect
.venv/bin/python -m hireme connection handshake status
.venv/bin/python -m hireme connection handshake check
.venv/bin/python -m hireme connection handshake discover
.venv/bin/python -m hireme connection handshake disconnect
```

Replace `handshake` with `workatastartup` for the second platform. These commands use the project's virtual environment directly; activation is not required. With the terminal connect command, finish sign-in in the browser it opens, then press Enter in that terminal to close the window and verify the session. Dashboard Sign in opens a dedicated visible browser window; finish sign-in yourself, close the window, then choose Check connection. On a Pi, do this on its desktop through Raspberry Pi Connect. Each platform uses its own private profile under the worker's private data directory, separate from the employer browser and personal browsers. Disconnect clears that platform's local session and remote-document mappings while preserving application history. Portable restores disable connections and require fresh sign-in and machine-bound validation.

The session check uses a visible browser on the worker desktop and waits up to thirty seconds for an authenticated account response or signed-in profile control. Handshake's reviewed script and style assets and two exact, variable-free account bootstrap queries can load without granting application writes. Query hashes pin the observed read payloads; an operation name cannot authorize changed queries or mutations. A visible sign-in form or confirmed null account response means sign-in is needed; an incomplete page, failed request or changed account control is shown as **Could not check sign-in**, preserving the saved session. **Browser verification needed** requires you to complete the challenge in the Sign in window. Status reports the saved result; `check` performs a fresh check. A `session_ready` result is separate from the capability fields: enabled settings do not make an unavailable capability ready.

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

Local fixtures cover identity conflicts, revisions, immutable uploads, exact payload grants, pause/budget handling, interrupted operations and conservative restores. They do not establish live platform access, actual transport compatibility or employer acceptance.

Both connections start disabled. Production capabilities remain gated until the dedicated session has independently validated access and matching machine-bound flow evidence. The [release status](public-release.md) records the outstanding gates; [Contributing](../CONTRIBUTING.md) lists reproducible local checks.
