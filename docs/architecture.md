# Worker architecture and verification boundaries

```text
Full public snapshots → durable jobs → deterministic eligibility and skill score
                                          ↓
Confirmed fact store + exact saved answers + approved prose templates
                                          ↓
Dedicated browser → field verification → immutable Q&A package + screenshot
                                          ↓
SQLite transaction: policy + budgets + durable SUBMITTING intent
                                          ↓
One final click → confirmation and screenshot → CONFIRMED, AWAITING_VERIFICATION or UNKNOWN
                                          ↓
Local dashboard: outcomes, questions, manual jobs, reconciliation, source health
```

## Authority

The SQLite ledger is authoritative. JSON files are private review exports. The provider has no tools and can only propose quoted facts, select confirmed fact keys/approved template IDs, or select sentences from approved samples. With applicant authorization it can also draft source-grounded answers from approved wording and hash-bound excerpts of the uploaded resume, reviewed in a second model request; this is a model-based factual check, not a proof. Skill-matched preferences are separately opt-in and preserve their source evidence. Question mappings and assembled samples are cached with source revisions; final package validation checks exact source text. Executor inputs never contain model-authored Python/JavaScript/selectors, file paths, arbitrary field values or shell commands. DOM evaluation is fixed read-only inspection code.

Private files use 0700 directories/0600 files. Uploaded PDFs are content-addressed and checked again before upload. Known application destinations are exact hostnames; network helpers reject private addresses and bound payload sizes. Browser requests restrict mutating origins and arbitrary third-party destinations; unsupported pre-submit writes are denied except read-only GraphQL and upload paths. This is application-layer defense, not a formal browser/OS sandbox. ATS scripts and intended destination servers necessarily receive submitted personal information.

The dashboard binds loopback, checks Host and Origin, requires a persisted private capability for APIs, has no external assets, disallows framing, uses DOM text nodes for remote content, and accepts bounded inputs. The URL token moves from the fragment into session storage; it is omitted from access logs and Referer headers.

## State and concurrency

Prepared packages contain fact/document revisions and exact answers. Any fact change invalidates prepared packages. Before click, the worker revalidates policy, values, docs, pause and limits inside a transaction. SUBMITTING survives process termination as UNKNOWN. There is no claimed exactly-once guarantee against arbitrary ATS servers; no automatic retry after intent is the safeguard. Human verification may classify a non-submitted attempt, but that job remains held rather than silently retrying.

All default checkouts share one store and OS locks. Explicit alternative data directories create independent applicant histories and must not be used to parallelize one person's submissions. The default queue rechecks blocked jobs in future cycles; facts/questions resolved once become available without manual per-job approval.

## Compatibility limits

Single-page forms with native controls and inspectable custom comboboxes are supported by the generic adapter. Cross-origin frames, invisible/unknown controls, custom consent widgets, multi-step profile saves and unverifiable confirmations are held. Portal adapters discover roles but do not certify application markup. Passive CAPTCHA can still reject automated input; interactive challenges are never attempted.

Gmail OAuth is optional, owner-bound and outside model input. Provider adapters support explicit subscription CLI or paid API choices. Every managed inference reserves a durable request before dispatch; failed calls also consume budget. No inherited API key is used. Arbitrary post-run hooks, clipboard brokers and personal-browser attachment are unsupported.

`accounts.py` handles opted-in native account forms reached after reading an eligible posting. Only a same-origin URL-encoded POST with the exact inspected payload is granted, once. Creation and sign-in intents are durable; interrupted or unconfirmed outcomes become uncertain and cannot retry automatically. The dashboard can record manual account confirmation while the worker is stopped. Registration requires email and two password controls; sign-in uses a stored account. Extra fields, agreement checkboxes, JS/SSO and verification stay held. Password files have mode 0600 and rely on local OS security; they are not encrypted and are excluded from portable backups, ledger, events, dashboard responses and model input.

`account_transfer.py` provides a separate, explicit encrypted credential archive for migration. Both instances must be paused and free of an active worker lock. A terminal-only passphrase derives a Fernet key with Argon2id (64 MiB, three passes, four lanes); the archive's 16-byte salt is random. Import authenticates/decrypts before using the bounded payload, binds it to the confirmed applicant and existing account history, validates every entry before writing, and rejects conflicting credentials. Per-file atomic writes make interrupted imports repeatable without changing account states. Passphrases and passwords are never logged or returned through dashboard APIs.

## Release validation

Run the full local suite and browser fixture tests, inspect the diff, and verify dashboard desktop/mobile. Actual employer acceptance, live portal sessions, scheduling while asleep and sustained daily throughput require independent operational evidence. Do not label them verified from local tests.

## Pause contract

A shared SQLite cancellation generation is incremented by pause. Live cycles capture their generation and check it during discovery, ranking, field resolution/filling, provider polling and the transaction reserving final submission. Resume cannot revive a cycle with an older generation. Claude runs in its own process group and is terminated on cancellation. Existing network/browser calls can take their bounded timeout to return. An employer request already sent is not undone; a durable submit intent remains pending until confirmation or reconciliation.

## Pi and local deployment

Pi 4 / 4 GB is the target, using 64-bit Raspberry Pi OS and system Chromium in headless mode. One worker and browser operate per applicant ledger. User-owned systemd files implement a separate loopback dashboard and batch timer; Raspberry Pi Connect provides desktop access. No public-hosting/password/Tailscale layer is needed. Generated services and backup restoration are fixture-tested; actual Pi operation is external evidence.

Backups use SQLite's online backup API under the worker lock. Hash-verified documents, reviewed materials and evidence accompany the ledger. Restores require a new directory and start paused. OAuth credentials, provider keys and browser sessions are excluded. Archives are private but unencrypted. Restores accept at most 1 GiB compressed and 2 GiB expanded; individual document members remain capped at 256 MiB, while the SQLite ledger can use the remaining expanded-size budget. Members are extracted and checksum-verified in 64 KiB chunks. Separate machines must not concurrently submit for the same applicant.

Gmail verification is bound to a durable Greenhouse application challenge, authenticated approved sender, confirmed recipient, employer and bounded time window. One-use consumption is recorded without persisting the code. Unsupported continuations remain held. Reports have their own outbox and independent delivery lock; ambiguous sends are not retried automatically. Restarted pending browser challenges and uncertain mail delivery need manual handling.

The first-run web flow checks browser/provider availability, imports candidate resume facts, collects reviewed context and style, saves targeting/budget preferences, and offers paused completion or explicit schedule activation. Provider keys never appear in snapshots. Separate applicant instances use separate data directories; CLI subscription identity belongs to the OS user.

## Observed worker run, October 2, 2026

Discovery was disabled and attempts were restricted to Figma SWE Intern (Summer 2027) and Amperesand Product Software Intern. Initial attempts failed before submission. The observed friction included a valid Claude Team subscription rejected by the provider, intake seasons mistaken for positions, unsearched university options, stale assembled writing, uninformative grounding rejection, changing dropdown options treated as a changed form, multi-select chips read as blank and a blocked Greenhouse city-search endpoint. Fixes now support the confirmed high-school fact, contextual role/season/track/location choices, source-grounded prose with bounded repair feedback, searchable school/city menus, chip verification and private field/model diagnostic events.

The final bounded run made two final-click attempts and confirmed zero submissions. Figma reached an emailed security-code challenge and is held as awaiting_verification. Amperesand displayed Cover Letter is required and remained on its form; captured screenshot evidence was used to reconcile it as not_submitted, which remains held for manual handling. The upload input's accessible label was Attach while its surrounding file-upload heading carried the required marker. That wrapper requirement is now inspected before canonicalizing the file label, so missing required documents block before click. The applicant's email, essays, answers and exact screenshots remain in the private ledger, not this repository. The worker was left paused with no active run.

These observations support the fixes above; they do not establish unattended submission success. Subsequent synthetic tests cover required generated cover letters and supported Greenhouse email continuation. These two real applications were not retried or confirmed by those fixtures.

## Contextual mappings and blocker holds

New semantic bindings use `field_bindings_v2`, fingerprinted by ATS, employer, role/location, section, widget, constraints, option label/value pairs, and rule version. `question_contexts` retains the same context for new review questions and their approved answers. Education and employment questions cannot resolve one another, and ambiguous historical literals require review. Confirmed source revisions must still match. Bindings are persisted after answer conversion and validation. Historical bindings remain evidence and do not authorize current field use.

`job_holds` separates missing information, mapping/document repair, eligibility, account tasks, unsupported flows, transient failures and unclassified review. Relevant dependency changes release repair holds. A stable discovery fingerprint distinguishes real posting updates from the extra navigation and form text captured by the browser. Document availability and modification time also release repaired-file holds even when the approved hash stays the same. Only failed initial read navigation receives two delayed retries (30 minutes then two hours); terminal and uncertain application states remain excluded. Recheck performs policy validation under the worker lock and cannot authorize another submission.

The authenticated coverage endpoint distinguishes configured discovery sources from supported single-step application forms. Workday discovery does not imply an application adapter. Cycle counts distinguish screening exclusions from browser-attempted failures. Live reports retain attempted-only links.
