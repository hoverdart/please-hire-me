# Documents, voice and report delivery candidate 0.5.0rc17

This candidate changes what the applicant receives and sends: resume and letter PDFs, how drafts sound, and the batch email. It adds a bounded iCIMS entry path and a capability matrix. It does not add employer acceptance evidence; see the [capability matrix](capability-matrix.md).

## Resumes and letters

- **Templates and bounded style.** Three templates (ATS single column, modern, compact technical) plus a layout measured from a reference resume. Body size 9–12 pt, margins 36–72 pt, line spacing 1.15–1.6, 1–8 lines per role, one or two pages, section order, date placement, heading case and an accent colour that must meet 4.5:1 contrast on white. Settings outside these bounds are rejected.
- **Reference resumes are style sources.** Measuring a reference returns font size, margins, spacing, heading case, date placement and section order. None of its words are returned or stored, and the file is not kept. An embedded DejaVu sans or serif face stands in for the reference typeface, so the same input produces the same PDF on macOS, Linux and a Pi.
- **Nothing is trimmed to fit.** Content that exceeds the page limit is refused with the setting to change. Characters missing from the embedded font are refused by name. Every confirmed line must be present, selectable and in reading order in the finished PDF, or the PDF is not used. A role's heading, dates and first line share a page; a section heading never sits alone at the foot of a page.
- **Projects.** Structured history accepts confirmed projects (name, role, https link, dates, description). Projects with descriptions can build a tailored resume without employment history, and their links remain clickable in the PDF. Application forms' employment and education sections never read them.
- **Selection record.** Each revision stores which lines were included or left out and why. Materials shows it under **Why these lines**.
- **Optional grounded rewriting.** When the style's wording is set to rephrase and source-grounded writing is authorized, selected lines may be rephrased using the saved writing-voice guidance. A deterministic check refuses any new number, named tool or organization, link, inflated wording or substantial new wording; a separate model review then checks each remaining line. A line that fails either keeps the confirmed wording. An unavailable model falls back to exact selection. Two model requests per resume. Changes to voice guidance invalidate rewritten resumes while preserving earlier revisions.
- **Letters match.** Cover-letter PDFs use the saved style's typeface, accent and rule. A style change builds a new letter revision.
- **Relevance fix.** A skill at the end of a sentence ("…in Rust.") now matches the posting's "Rust". Previously the trailing full stop prevented the match.

Style, facts, history and posting changes still invalidate execution authority; earlier revisions stay downloadable.

## Writing voice

The dashboard shows a description of the applicant's voice measured from approved writing samples and past cover letters: sentence and paragraph length, first-person openings, contractions, punctuation and stock phrases already in use. Background reference documents are excluded. The description contains measurements only.

Saved preferences (phrases to avoid, exclamation marks, contractions, longest sentence, a short note) persist and invalidate cached writing. Drafts receive the description as style guidance. After each draft a deterministic pass removes a greeting or sign-off from a field that asked for a paragraph, and notes stock phrases, exclamation marks, verbatim posting text and repeated sentences. Those notes trigger at most one repair request and never reject an answer. Unsupported claims are still rejected by the existing factual review.

## Batch email

- **HTML and plain text.** Reports are sent as multipart messages. The HTML part separates employer-confirmed applications, work that needs the applicant, uncertain outcomes, known unsent results and local preparation, with posting links, exact questions, application record, confirmation time and resume hash. Known unsent results and local preparation do not count as uncertain submissions. Inline styles only: no images, scripts, tracking or remote assets. All posting text is escaped. Only public `https` posting links are included; the email contains no dashboard link or token and says so.
- **Delivery states.** Queued, sending, accepted by Gmail, uncertain, not sent, and reconciled are distinct. "Accepted by Gmail" does not claim inbox placement.
- **Failures.** A refusal that proves nothing was sent (authorization, missing scope, rate limit) leaves the report queued for at most five attempts. Other 4xx responses mark it not sent. Timeouts, connection errors and 5xx responses are uncertain and are never resent automatically.
- **Reconciliation.** Each send records its wire identifier and includes a matching opaque report reference in its own header, plain text and HTML. Gmail may replace Message-ID; a later flush also searches for the reference with the existing read-only scope and verifies the Sent label, exact owner addresses and correlation header. Search results alone and unsent drafts cannot establish acceptance. Checks rotate through unresolved sends for the connected account, including failures and negative lookups, so older uncertainty cannot starve newer reports. An absent result or failed lookup leaves a send uncertain, even after 15 minutes; mailbox search cannot prove that a send failed. Uncertain sends are never queued again automatically or through the retry control. Only proven refusals or pre-dispatch holds can be queued again. Response-decoding failures after dispatch also remain uncertain.
- **Preview.** Connections → Batch report delivery downloads a saved report exactly as it would be sent. `hireme reports preview --output FILE.html` does the same. Neither contacts Gmail.

## iCIMS

Hosted iCIMS postings gain a requisition identity (host and numeric ID), so a renamed title or frame query cannot create a duplicate. The adaptive executor uses the posting's approved content frame while retaining the real outer page: the live client can restore that wrapper when content is opened directly. Every nested wrapper must retain the exact origin and requisition. Controls outside the approved content document are excluded. Receipt reads may recover from a detached frame; saved steps and final requests are never replayed.

The exact AMD careers URL shape resolves to the same numeric requisition on AMD's fixed iCIMS host. The saved source URL and job ID remain unchanged. Hosted and careers links share duplicate comparisons, including historical manual applications and unacknowledged UI saves under another feed name. Other hosts, credentials, lookalikes and unknown paths gain no bridge.

`icims` is its own entry in the application scope, including the recognized AMD careers bridge; allowing `other` does not include it. Staged rollout still commissions Lever and Ashby only. Steps that save through an account-profile endpoint are refused. Public AMD careers and iCIMS entry pages were observed read-only on November 14, 2026, without applicant data, login or CAPTCHA interaction. Full application execution has synthetic coverage only; no live iCIMS application was submitted or accepted.

## Limits

No new employer acceptance evidence. Fixture results are local. The dashboard's inline PDF preview depends on the browser's PDF viewer; a download link is always offered. Reference measurement is approximate for scanned or unusual PDFs and can be adjusted before saving. On November 14, 2026, a real self-addressed Gmail test deliberately withheld an accepted-send acknowledgement: the report became uncertain, then its rewritten Message-ID was reconciled through the matching reference without another send. That message had both Sent and Inbox labels and HTML/plain-text alternatives. This proves that tested connection and message, not every mailbox or general inbox placement.

## Recovery and scope corrections

Ashby reconciliation now requires both the correlated native success acknowledgement and a separate, fresh receipt observation bound to the same requisition and final request. The observation stores hashes rather than page text. An acknowledgement alone cannot clear an unknown outcome; older attempts without this receipt evidence remain available for manual verification. Lever's native HTML acknowledgement already verifies a fresh completed-application receipt. Neither reconciliation path authorizes another application.

Hosted frame inspection uses the existing bounded DOM-read recovery when navigation replaces the page. Only the inspection is retried; the navigation, saved step and submission are never replayed.

All six ATS scope choices can be selected together; duplicates and lookalike ATS domains are rejected. Read-only release and health checks use the same complete settings, including the separately stored scope and document preferences, without initializing or changing the ledger. Writing-preference refreshes preserve the revision of an unsaved draft so another window's changes cannot be silently overwritten. Preview uses labelled sample content only when setup information is missing; changed document bytes surface an error. Backup/restore retains the saved styles, writing preferences, report previews and uncertain-mail dispatch evidence, and restoration still starts paused.

Recovery preserves existing remote-draft uncertainty holds even when the applicant has excluded the job. Repeated recovery no longer increments their retry counters or changes their timestamps. A missing hold is rebuilt when the job already displays uncertainty; the draft is never retried.

Schema initialization and upgrades commit their tables, indexes, initial configuration and readiness marker together. A failure rolls back the whole initialization, including an older marker upgrade. WAL and full synchronization remain enabled; batching avoids a separate disk synchronization for every schema statement on slower storage. Opening an already initialized ledger still needs no writer lock.

A configured paused schedule exits successfully with zero attempts even when its rollout pin is stale. Dry-run commissioning cannot turn that scheduled stop into preparation, and a concurrent Resume cannot bypass rollout validation. Enabled runs and explicit preparation still require current evidence; incomplete setup remains an error. Reporting and feedback retain their independent controls.
