# Executable run contract

The authoritative settings, facts, answers, jobs, attempts and events are in the shared private SQLite ledger. JSON exports are review snapshots, not editable authority. Use the dashboard or CLI to change state.

1. Take an OS file lock shared by all checkouts using this ledger.
2. Recover unfinished submit intents to UNKNOWN; block retries.
3. Fetch full discovery snapshots into durable jobs. Individual source failures retain previous jobs and show degraded health; no global timestamp skips work.
4. Filter using confirmed facts and configured roles, locations, timing, compensation and company blocks. Rank using visible skill-match evidence.
5. Revisit the official posting and apply the policy to actual page text.
6. Fill known fields with confirmed facts, source-backed formatting/derivations, scoped saved answers or approved writing-sample sentences or explicitly enabled source-grounded drafts using approved wording and hash-bound excerpts from the uploaded resume. Semantic matches and assembled samples retain source references and are checked again before submit. Unknown required fields go to the question queue. No guessing, CAPTCHA solving or knowledge-test answers. Optional employer account automation requires explicit opt-in and a supported native form reached after posting eligibility; account POSTs have separate durable intents and exact one-use grants, never application-submit authorization.
7. Verify fields, upload only approved hash-checked PDFs, persist full Q&A and screenshot the prepared form.
8. Recheck facts, documents, eligibility, pause state and atomic company/day budgets. Company history includes originally recorded keys and current aliases of the original employer name; editing aliases cannot erase prior attempts from company holds or ceilings. Commit SUBMITTING before the final click.
9. Wait up to 45 seconds for the ATS outcome. Record confirmation and a best-effort screenshot; a screenshot timeout must not prevent email verification or erase a confirmed result. Missing confirmation, browser disconnect, crash or network ambiguity becomes UNKNOWN and blocks the company until reconciliation. An explicit Ashby spam rejection is NOT_SUBMITTED and requires manual action; it is never retried automatically.
10. Record cycle totals, shortfalls and blocked reasons. Targets never relax invariants.

No routine human approval. One-time setup confirms profile facts; new unknown questions are answered and saved in the dashboard. Account/CAPTCHA/unsupported verification/work-sample exceptions have clickable job links. Supported Greenhouse email challenges may continue through the applicant-authorized Gmail adapter.

Every model request reserves a durable per-batch and local-day slot before dispatch, including failed calls and separate grounding reviews. Attempt ceilings bound blocked applications too. Pause increments a cancellation generation; resume cannot revive a cancelled cycle. Unknown or verification-pending applications remain held.

Ashby `ApiSetFormValue` background autosaves are aborted without ending local form preparation. Unrecognized pre-submit mutations still stop the attempt and record request host/path/method without payload values. Final field verification and durable submission intent remain mandatory.

Greenhouse and Ashby uploads require a successful response for the approved PDF; Ashby also requires confirmation that its file handle was attached to the form. A visible filename alone is insufficient. Required Ashby question headings apply to radio, checkbox, autocomplete and Yes/No controls.

Confirmed locality and onsite preferences can answer Bay Area office questions and establish that a Berkeley resident does not require relocation to the Bay Area. Willingness to relocate does not establish relocation need. New experience claims and recent reading still require real source facts.

After an uncertain outcome is verified and reconciled as not submitted, an explicit `python -m hireme retry-not-submitted APPLICATION_ID --note 'Reason for retry'` preserves the previous attempt in the event ledger and requeues the job. Unknown, pending-verification and confirmed records cannot be retried through this command. Preparation cycles report prepared counts separately from confirmed submissions.
