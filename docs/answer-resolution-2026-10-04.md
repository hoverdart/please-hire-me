# Answer-resolution repair — October 4, 2026

Runtime code `1103b284b2ef303113295e59ffe647cb540412a7` is deployed on the physical Pi. Public release remains blocked. This repair verifies answer resolution and supported controls; it does not claim a new employer acceptance or completion of the broader release gates.

## Isolated causes and changes

| Failure | Correction |
| --- | --- |
| Generic authorization wording such as “country for which you are applying” did not reuse the confirmed US authorization fact. Some cached locations contained only `SF` or `NYC`. | Recognize the reported wording and retain Ashby's structured country metadata. Refresh the affected cached postings from the employers' public APIs. Foreign, negated, ambiguous and mixed-country cases remain guarded. |
| Quora's authorization depends on its preceding employment-country selection. | Carry validated prior answers through resolution and package validation. With contextual preferences enabled, employment country can use the existing confirmed US residence; interview language uses confirmed skills. Both retain source revisions. |
| Changed facts made their saved fact-linked answers unusable. Cosmetic label/length changes invalidated explicit approvals. | Use the current confirmed linked fact; revocation still blocks. Reuse explicit approvals only in the same employer/role/location/widget/choices/constraints context, while enforcing the current length limit. |
| A known graduation date was treated as a missing answer to a seasonal confirmation. | Compare the date with the two discrete seasons, rather than their intervening months. A false required confirmation stops as `graduation_mismatch`, and a previous literal Yes cannot override the current date. |
| Ashby nested SMS consent beneath Phone; its radio answers contain longer consent sentences. Its texting selection also triggers a form-render draft mutation. | Give consent its own semantic label, map only confirmed SMS consent to the exact supported choices, and suppress that draft request without authorizing an application write. |
| Rich school choices concatenated the canonical school name, country and domain. | Read and select the exact canonical name. Reject multiple matching school choices and distinguish keyboard highlighting from a committed value. |
| Gecko's autocomplete was labeled “Start typing…” and focusing it did not open its choices. Its onsite choices used longer Yes/No statements. | Read the fieldset's real question, open Ashby's adjacent toggle, and map the supported onsite statements to the confirmed commitment. Quora's discipline/field-of-study wording also maps to the confirmed major. |
| Old question records remained actionable after the current field resolved. | Retire obsolete questions for that same job/field without broadening saved approvals. Precisely retire the misclassified Phone radio after confirmed SMS resolution; genuine phone questions remain separate. |

## Verification

- Final clean source export: **861 passed in 261.91 seconds**, with five existing third-party PyMuPDF deprecation warnings. The export contained committed baseline source plus the twelve explicit fix/test files; unrelated eligibility-hold, worker, policy and dashboard-test edits were excluded.
- Final physical-Pi regression run against the identical candidate source: **187 passed in 312.82 seconds**. This includes browser, discovery, answer, saved-approval, question-ledger and integrity coverage. The final Pi source files matched the tested candidate's SHA-256 manifest.
- Python compilation, JavaScript syntax and whitespace checks passed.
- Live Quora controls: confirmed phone, canonical school and SMS consent resolved, filled and passed DOM verification. Interview language and selected-country authorization resolved without inference. The texting draft save was suppressed without setting the unapproved-write failure.
- Live AfterQuery: its public posting supplied United States country metadata; the reported authorization field resolved from the confirmed authorization fact.
- Sage49: both reported authorization questions resolved after refreshing the official Greenhouse location, `New York, New York, United States`.
- Live Gecko: its onsite statement and actual discovery-source question resolved from the existing commitment and platform discovery source.
- Live/control validation added **zero application submissions and zero model-request reservations**. Synthetic ATS submissions stayed on the local fixture server.

Quora's coordination-hours acknowledgement has no matching approved answer in the active Pi ledger. It was not invented from unrelated facts. The read-only probe also did not invoke the writing model for its project essay; that probe's writing-upgrade result is not evidence that grounded writing generation failed. Quora is currently outside the configured full-time search. Scale AI's requested graduation seasons do not include the confirmed date, and those roles are also outside the configured full-time search. Sage49 remains outside the configured locations.

## Deployment and preserved state

The worker was paused cooperatively, its cron entry removed temporarily, and the active run allowed to stop. The primary SQLite integrity check returned `ok`. A private SQLite backup, source archive, original crontab and revision/state snapshots were retained on the Pi before deployment. This is a code/data rollback snapshot, not a claimed portable-backup restore test.

The Pi advanced from `2c9c309` to `1103b28` by a fast-forward update, including the already-committed main-branch credential, receipt and public-Workday inspection fixes. Unrelated local working-tree changes were preserved and were not deployed.

After refreshing the affected public posting metadata and revalidating known answers, both AfterQuery records and the canonical Gecko record are `discovered` with no missing-answer reason. The separate Gecko import and both Sage49 records retain location exclusions. Old Gecko placeholder questions were retired with an audit event recording the observed employer question. Scale AI's confirmations now resolve as comparisons, rather than requests for another graduation fact. Historical reports and receipts were not rewritten.

The loopback dashboard was restarted. The page and authenticated state API returned **200**; unauthenticated state access returned **403**. The dashboard reported no active worker during verification.

The exact original crontab was restored. Scheduler inspection confirmed `installed=true`, `matches_applicant=true`, `interval_hours=6`, `kind=cron`; `live_enabled=true` was restored. Limits remain **300 requests/day and 100/cycle**. Every application row, confirmed-fact revision and approved-answer revision matched the paused baseline. Outcomes remain **3 confirmed, 7 not submitted and 5 unknown**. No uncertain outcome was retried or reclassified.
