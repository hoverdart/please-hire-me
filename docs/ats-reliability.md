# Ashby and Lever reliability audit — October 31–November 1, 2026

The primary application bottlenecks were a signed-out model connection, avoidable factual-answer lookups, and native form mismatches. Increasing the submission target alone did not resolve them.

## Repairs

- Codex structured output now omits the unsupported wire-level `uniqueItems` constraint while retaining the complete original schema for local validation. Unknown source IDs, duplicate citations and excess items remain invalid.
- A form shares its model connection across fields and cover letters. An unavailable or incompatible connection is checked once per configuration within that form rather than spending another request slot for every question.
- Source selection preserves the current approved resume before trimming supplementary context to the existing 24,000-character limit. Keyword-heavy context notes previously displaced resume dates and measured results. Independent grounding review and source hashes remain mandatory.
- Short descriptions of work or projects use the narrative writing-and-review loop. A rejected unsupported detail can be repaired and reviewed again before caching, rather than ending in an avoidable short-fact hold.
- Writing repairs reconsider unattempted mixed-form holds containing rejected writing. They leave ordinary missing-fact holds and uncertain applications closed.
- Observed university, return-offer year, internship start and local office questions use revision-bound confirmed facts. Country placeholders require validated employment-country context. A graduation date does not establish permanent employment availability or an exact start day.
- Direct graduate-study requirements such as “Ph.D. candidate working in” and “Current MS or PhD student or recent graduate” are screened before application browsing. Descriptions of graduate students mentoring interns remain distinct from applicant requirements.
- Model and browser changes can reconsider unattempted information and widget holds. Existing application history and uncertain guest drafts remain protected.
- Saving an employer-specific answer no longer releases unrelated employers' information holds. Relevant scoped answers and legacy host-wide answers still trigger reconsideration.
- An exact saved approval takes precedence over an older generated answer cache. Current-date enrollment and graduation confirmations remain derived from confirmed dates.
- Ashby's empty auxiliary resume-autofill picker is excluded from application questions. Named, required, populated and form-owned upload controls still require inspection.
- Ashby's native verification fingerprint accepts a bounded string up to 20,000 characters. The prior 2,000-character limit rejected a 3,810-character value from the official client, aborting the final request and producing a network-error message. Fingerprint and CAPTCHA contents stay out of diagnostics.
- Native Ashby dates convert an approved exact display date to the observed ISO calendar-date wire format before autosave authorization. Unknown formats, invalid dates and month-only values remain held. The display answer and its source remain unchanged, and the final grant still requires the exact native acknowledgement.
- A native single checkbox has one checked wire value and an unchecked absence. The planner and answer context now represent that encoding consistently.
- Lever permits exact descriptors for an entirely unanswered optional survey only after checking employer identity, template identity, optional fields, rendered choices and absence of responses. It does not generate demographic answers. Other survey shapes remain held.
- Lever location selection can make a second read-only lookup using the complete approved label. Selection still requires one exact suggestion and its matching native location object.
- Lever discovery and fresh posting inspection share the hosted page's HTML description fields. An inspected Summer 2027 posting retained Summer 2026 wording and a 2025 deadline in its stale API plain-text copy. Plain text remains a fallback when HTML is absent; an explicitly empty HTML field cannot resurrect removed content.
- Lever university widgets retain the validated visible Select2 proxy's named native select, requiredness and official options. Follow-up approvals bind to their native card field; a wrongly mandatory “Other” degree follow-up can say “Not applicable” only after an adjacent, confirmed non-Other degree choice in that same card.
- Duplicate unselected labels in a native select no longer block a uniquely approved label with a unique native value. Ambiguous selections, missing native mappings and duplicated selected IDs remain blocked.
- Explicit undergraduate and graduate transcript requests use the approved transcript document, including a request ending in a sentence period. Combined or unrelated document requests still cannot disclose it.
- Greenhouse's localized English race prompt retains its canonical field identity and approved answer. A standalone custom checkbox maps its approved checked state to the sole native option only when its current ID, name, type, value and option label match; it never creates consent or a demographic answer.

## Evidence and limits

Two real Ashby applications returned correlated native success acknowledgements and fresh completed-application receipts after the fingerprint fix. Another inspected Ashby attempt reached an applicant CAPTCHA and remains uncertain after its challenge wait expired. These observations validate the accepted forms, not every Ashby employer or conditional workflow.

Two real Greenhouse applications exercising the localized prompt and standalone checkbox paths also returned correlated native acknowledgements and fresh completed-application receipts after those repairs.

Lever's real Shield AI application passed complete field filling, document checks and native submission preflight after the university fixes, then reached an applicant CAPTCHA. Its bounded challenge wait expired without an acceptance receipt. Native transport also has synthetic coverage, but live acceptance remains unverified: another inspected form returned an employer verification error. These attempts remain uncertain; no receipt was invented and no uncertain application was retried.

A daily target is an operating setting, not an achieved submission count. Reaching 8–10 confirmed submissions per day still needs observation of normal scheduled runs with sufficient eligible openings and successful employer verification. CAPTCHA and applicant-only attestations can require human involvement. Unknown outcomes remain protected regardless of targets.

An ordinary six-hour Mac scheduled cycle completed after installation and recorded two confirmed applications. This is runtime evidence for that cycle; it does not establish a sustained daily rate or Lever acceptance.

The Mac installation and its private applicant workspace are separate from the source checkout and Raspberry Pi installation. Install the tested artifact before restoring the schedule. Use one active scheduler for the applicant and keep private receipts, logs, documents and backups outside Git.

## Repeatable checks

Run the complete suite with `python scripts/run_ci_tests.py`. Focused coverage includes `test_ats_logistics_reliability.py`, `test_provider.py`, `test_application_spec.py`, `test_ashby_preflight.py`, `test_ashby_drafts.py`, `test_lever_submission.py`, the Lever location cases in `test_browser.py`, and `test_pipeline_integration.py`.

Build a wheel and run `scripts/check_install_artifact.py` against it. Verify the installed source files, dashboard health, subscription login, Gmail access and preservation of original application records independently. Live acceptance requires the exact posting's native acknowledgement and fresh receipt; HTTP success, CAPTCHA completion and a visible uploaded filename are insufficient.
