# What AutoApply helped improve

I built please-hire-me because repeating the same applications was wearing me down. Looking at another project is useful when it helps remove more of that repetition without losing track of what actually happened.

This comparison reviews [AutoApply at revision `053071b`](https://github.com/AbhishekMandapmalvi/AutoApply/tree/053071ba1bba5733b522d78c3d645002d817e55a). It is a source review, not a live benchmark or a claim about its current deployment. AutoApply is MIT licensed; the changes here are original implementations of useful ideas, with no third-party source copied.

## Useful ideas to bring over

| AutoApply approach | What helps here |
| --- | --- |
| [Ranks experience with TF-IDF and keyword boosts](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/core/resume_scorer.py) | Choose relevant experience for each posting instead of uploading the same resume everywhere. Its ONNX branch falls back to TF-IDF; the reviewed code does not demonstrate neural scoring. |
| [Groups knowledge-base entries into roles and renders a tailored PDF](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/core/resume_assembler.py) | Keep dates, employer and role together; make the resulting document downloadable and tied to its sources. |
| [Keeps resume versions through its API](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/routes/resumes.py) | Preserve what was generated for an application, rather than overwriting the only resume. |
| [Connects discovery, tailoring and applying in a bot pipeline](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/bot/bot.py) | Put materials and outcomes into the normal workflow; a separate resume generator alone would not remove much repetition. |

## Strengths to keep here

**A final click needs evidence.** AutoApply's reviewed [Greenhouse handler](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/bot/apply/greenhouse.py) treats absence of visible errors after clicking as success. Its [Ashby handler](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/bot/apply/ashby.py) includes generic thanks and an assume-success fallback. Here, a fresh supported acknowledgement is required; missing evidence remains uncertain. More green counters would not make that more reliable.

**An uncertain submission stays held.** AutoApply's [base applier](https://github.com/AbhishekMandapmalvi/AutoApply/blob/053071ba1bba5733b522d78c3d645002d817e55a/bot/apply/base.py) retries failures and exceptions. Here, durable submission intent prevents automatic retries after a potentially consequential request. Recruiting email must not silently reopen that attempt.

**Applicant-confirmed history stays authoritative.** AutoApply's assembler asks a model to use knowledge-base facts, but also supports ingesting its generated resume back into that knowledge base. Here, generated wording does not become verified applicant experience. The new resume assembler selects exact confirmed description lines and keeps their original employer and role.

**Self-hosting stays understandable.** Deployment, credentials and application history remain under the user's control. Self-hosting is not a promise of local inference: configured model providers and intended employers still receive the data needed for their enabled features. The new deterministic resume and feedback classifiers make no model calls.

## Implemented from this review

### Posting-specific resumes

Enable **Tailored resumes** in Preferences after uploading an original resume and confirming employment descriptions in Your facts. Generate and download a PDF from a job's materials, or let the supported browser application path build it before uploading.

The assembler ranks exact description lines by posting-word overlap, selects up to four per employment entry, and preserves employer, role, supplied dates, education and confirmed contact details. It reorders confirmed skills without adding them. It does not infer missing dates or rewrite accomplishments. The PDF must retain its text and fit within two pages; unsupported characters or oversized content hold the application with an explanation.

Each revision records its posting, confirmed sources and original-resume hash. Changes invalidate it for future submission; historical downloads remain available. The original resume remains intact. Workday's separate native workflow is not covered by this integration. This is source-bound selection, not a validated ATS score or a promise of better hiring outcomes.

### Recruiting feedback attached to attempts

Enable **Recruiting feedback** in Connections after connecting the applicant's Gmail account. A bounded sync suggests acknowledgement, assessment, interview, rejection or offer categories from English message text. Candidate matches use posting URLs, company and role; ambiguous matches require choosing an exact prior attempt. Review the message and category before confirming.

Only mail addressed to the confirmed owner is imported. No links are followed, attachments fetched or model requests made. Private previews and reviewed links are stored locally and included in private backups. Failed syncs retain their cursor; deleted messages do not block later pages. This is a review queue, not autonomous mailbox authority. It cannot change submission receipts, resolve uncertainty or trigger another application.

### Visibility across the whole search

Today now separates all-history submission outcomes from recruiting progress. Prepared applications, confirmed receipts, uncertainty and failures have separate counts. Reviewed recruiting stages appear in Today and the relevant job. Repeated messages for one attempt count once at its latest reviewed stage, with acknowledgements kept from erasing a later recruiting stage.

## What would make this better next

| Priority | Improvement | Evidence needed |
| --- | --- | --- |
| First | Commission the actual managed worker and its submission path | Owner-authorized employer receipt, persisted terminal state, ordinary scheduler cycle, clean restart and one active scheduler. Local fixtures cannot establish these. |
| Implemented locally | Import resume experience into a reviewable history editor | Exact excerpts, explicit applicant confirmation, duplicate protection and withdrawal/edit invalidation. See the [user guide](guide.md#import-education-and-employment-from-a-resume). |
| Next | Explain why each experience line was selected; compare PDF revisions | Visible posting terms and source links, plus applicant review. Evaluate ranking quality before introducing match scores. |
| Next | Commission Gmail with real recruiting messages | Owner connection, assessment/interview/rejection examples, HTML/forwarded mail and ambiguous company/role matches. Current automated proof is synthetic. |
| Next | Reusable search presets and editable season defaults | A clean new-user setup for different roles, locations and experience levels; current defaults still reflect the original early-career search. |
| Later | Simplify installation and upgrades | Fresh-machine onboarding, clean upgrade/restore and one scheduler. A desktop shell alone does not prove a working application pipeline. |
| Later | Broaden ATS support | Reviewed native requests and fresh receipts for each supported form; unsupported forms should remain clearly held. |

The [release gates](public-release.md) remain the authority for public-release claims. These improvements are implemented locally; they do not supply the missing device, live Gmail or employer evidence.
