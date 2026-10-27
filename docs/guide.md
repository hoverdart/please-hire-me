# User guide

Start with the [installation instructions](../README.md#get-started). All commands below run from the repository directory. For a background dashboard on Raspberry Pi OS, use the [Pi guide](pi.md).

## Find your way around

Home shows today's counts, items needing you and recent activity. Jobs holds the complete queue, posting imports and draft preparation. Applications holds outcomes, Recruiting Insights and batch history. Needs you collects unanswered questions and uncertain attempts. Profile contains facts and Materials; Settings contains Connections, search preferences and Setup.

Action feedback appears as a floating notification, including when you have scrolled down. Ordinary updates dismiss after a few seconds; hover or focus pauses that timer. Errors stay until dismissed. The Notifications button in the header keeps the latest 20 messages for the current browser session. Application records and batch history remain the durable evidence.

## Follow an application through the search

Open **Answers & evidence** on a recorded attempt in Jobs or Needs you. Its application record joins the captured posting identity and source, exact document hashes and known resume revision, submission status, reviewed recruiting stage and next action. **Application timeline** distinguishes preparation, final intent, the saved outcome and recruiting messages. The private JSON export records this view; original recorded PDF downloads and answers remain below it.

Reopen the evidence after reviewing email to refresh its recruiting context. Opening the record reads saved local data and does not sync mail. Recruiting stages never replace the original submission outcome: an uncertain attempt still needs verification even when a later interview message is reviewed. Older attempts without a posting snapshot explicitly identify their current-metadata fallback. See the [feedback-loop roadmap](feedback-loop.md) for the next stages.

## Set it up for your search

Start in **Settings → Search & automation** with the jobs you actually want: roles, seniority, locations, compensation requirements and companies to skip. The defaults focus on internships and new-grad technical roles, including Summer 2027 and school-location settings. Review them before your first batch.

In **Your facts**, confirm your own information. In **Profile → Materials**, add the projects, experience and writing samples you'd want an application to draw from, then review how each source may be used. You can revise or withdraw those sources later; the sections below explain what happens to existing drafts and records.

Choose your model, schedule and limits to fit how you want to use the app. Finish setup paused, prepare a few drafts, and review the answers and documents before enabling submissions. The [models and request limits](#models-and-request-limits) section explains the defaults and billing choices.

## Recruiting Insights

Open **Recruiting Insights** in Applications or find it through Find a tool. Choose the application period, a 7/14/30-day response window, and grouping by company, exact role title, application source or resume PDF. Click a metric or group count to reach its matching attempts, then **View application evidence** for the exact package, recorded PDF, receipt and reviewed messages. Refresh or reopen the panel after reviewing mail.

Response milestones and mail-review coverage use only confirmed submissions old enough for the full selected window. A 14-day comparison counts reviewed messages arriving in each attempt's first 14 days. Recent submissions, uncertain outcomes, failed submissions and unplaceable timestamps are separately inspectable. Acknowledgements count as reviewed mail but not as assessments, interviews, offers or recruiting rejections. Milestones may overlap; an offer does not establish that an interview was observed.

The comparison evidence identifies messages inside the selected window; the complete application history below it may contain later responses. All counts include retained attempts and are independent of dashboard page limits. Counts describe distinct attempts rather than distinct requisitions. Resume groups identify exact PDF bytes, including older original uploads; identical bytes share a group even if their artifact revisions differ. Known revisions appear in the per-attempt record. Historical snapshots are used when available; current-metadata fallback groups are explicitly labeled and kept separate.

Shares are descriptive, not forecasts or causal estimates. Every group shows its denominator. Fewer than 20 mature submissions triggers a display caution, not a significance test. Unsynced, unreviewed or absent mail remains missing evidence, so a low recorded response share cannot establish that the strategy failed. Opening Insights reads local saved data without syncing Gmail, calling a model or changing application policy.

## Import education and employment from a resume

In Your facts, open **Import history from your resume** and load the selected PDF. Common English headings, separated employer/role headers and explicit month/year dates (including `MM/YYYY`) produce proposals. Wrapped accomplishment lines are joined within their selected role. Choose a proposed excerpt or select line numbers from the full local text, then **Propose fields from these lines**. Ambiguous layouts require manual selection and correction; no model request or OCR is involved.

Check the original excerpt and every field. Missing dates remain blank; year-only ranges do not establish a month, and current status is proposed only for an explicit current/present range. Put each complete accomplishment on its own line, correcting PDF wrapping as needed. Review and confirm the checkbox before saving. Changing a field clears confirmation; changing the source range requires proposing and reviewing that range again.

Imports bind to the selected PDF hash and exact excerpt. A changed or damaged source prevents confirmation. The original PDF and existing history remain intact. Identical confirmed entries are reused; repeated confirmation cannot duplicate an import. A previously imported entry that was edited or withdrawn must be managed through the existing history editor, rather than being overwritten or revived by reimport. Similar entries are flagged for review, but the app does not automatically merge separate roles.

The private journal retains the original excerpt, PDF reference and the applicant-confirmed values, including corrections. Confirmed history remains applicant-authorized when a newer PDF is uploaded; edit or withdraw entries explicitly when needed. Changes and withdrawal invalidate source-bound future materials without rewriting terminal application history.

## Documents and confirmed facts

Upload a resume PDF with selectable text through **Your facts**. Review extracted values before confirming them; missing work authorization, sponsorship and other personal declarations require your answers. A transcript is optional. Use **Stop using transcript** to withdraw it from future uploads while retaining past application evidence.

New or changed values need confirmation. Reimporting an identical PDF preserves existing confirmations and drafts; replacing a document invalidates unattempted drafts containing it. Past attempts stay recorded. If a selected PDF is missing or unreadable, import it again before starting a batch. Re-uploading the matching original can repair its private copy.

Use **Basic context** for your current work, education, experience and preferences. **Add context in your own words** can propose fact updates and quoted notes from your explanation. Review those proposals before saving. Legal declarations and demographic answers still require explicit facts or scoped answers.

Clearing a saved optional fact removes it from future confirmed use. **Review saved answers** lets you find, revise or withdraw responses you previously approved. Source changes invalidate affected unattempted drafts; past attempted answers and evidence remain available.

## Writing and materials

For per-posting resumes, first confirm **Education and employment** in **Your facts**. Add complete responsibilities or accomplishments on separate lines under their original role, with only dates and details you can confirm. Enable **Tailored resumes** in Settings → Search & automation. The worker chooses up to four complete lines per role by relevance, keeps their wording and employer context, and renders a PDF with your confirmed education, skills and contact details. It makes no model calls for this step. Missing descriptions, unsupported PDF characters or content exceeding two pages need attention before that resume can be used.

Open a job's **Prepare application materials → Tailored resume PDF** to generate and download a version for review. When enabled, supported browser application runs use the generated resume. Your original upload remains selected in Your facts. Each revision stays in Materials; source changes invalidate its use in new submissions without deleting the historical download. Workday and platform-native controllers retain their separately validated document paths.

In **Profile → Materials**, upload DOCX, PDF, PPTX, TXT or Markdown sources. Review their excerpts and approve each for personal factual evidence, background reference or style only. Style examples never establish your qualifications. Search and page through the complete source library; save or discard excerpt edits before changing pages.

Approved personal sources can support factual answers and tailored writing when contextual choices are enabled. Generated answers retain source references and pass a separate grounding review. Model review can still miss errors, so review important drafts yourself.

Generated cover letters use a fixed one-page PDF layout. Templates guide wording and structure; arbitrary uploaded document layouts are not reproduced. Reusable materials and job-specific drafts retain their source revisions and content hashes. Official records and substantive work samples require applicant-supplied originals.

Changing or withdrawing an approved source invalidates affected drafts. Re-uploading its matching original can repair a missing private file while preserving its reviewed excerpt. Keep original files available for historical evidence and backups.

## Find and import opportunities

**Find opportunities** checks public job sources and saves listings. It works while submissions are paused and before onboarding is complete, with no model requests or application attempts. **Stop search & pause** stops at the next checkpoint and keeps listings already collected.

**Discovery source health** shows saved source checks, including failures. A failed source does not erase previously discovered listings. Repeated discovery refreshes current posting metadata while preserving record identity, holds and outcomes.

For spreadsheet imports, open **Add postings from a spreadsheet** in Overview:

1. Download the CSV template and fill Company, Role and Application URL. Location and Posting text are optional.
2. Save as UTF-8 CSV, with at most 500 rows and 1 MiB per file.
3. Choose **Check CSV**, correct any row errors, then choose **Add checked postings**.

The complete file must validate before anything is saved. Imports do not prepare drafts or start submissions. An exported opportunity ledger can also be imported; its status and outcome columns are ignored.

Save search, status and sort combinations under **Save and reopen your filters**. Up to 20 named views are stored in your private ledger and included in backups.

To export job metadata:

```bash
.venv/bin/python -m hireme export-ledger /absolute/path/application-ledger.csv
```

Choose a new filename. CSV exports are not applicant-history backups.

## Prepare and review work

Finish setup with submissions paused, then choose **Prepare drafts without submitting** in Overview. Preparation uses the normal discovery, attempt, time and model-request budgets. Questions and account walls remain held. Filter by **Prepared drafts** and open **Answers & evidence** to review the saved work.

A later live batch rechecks facts, documents, eligibility and limits before submission. **Check saved posting** provides a local assessment using saved facts and preferences without visiting a live page or calling a model. Unsaved edits are not included; this assessment does not certify that a form can be completed.

**Recorded documents** downloads the exact hash-verified PDFs saved with a draft or attempt. Missing or altered files show repair guidance instead of substituting today's upload. Keep these downloads private.

Use **View details → Your private notes** for reminders about a role. Notes remain separate from facts and model input. They travel in history backups. Saving an empty note clears it; if another tab changes a note, copy any draft you need before discarding edits and reloading.

## Held questions and outcomes

**Needs you** provides searchable, paginated questions, unresolved outcomes and employer accounts. Save or discard drafts before changing filters or pages. Ordinary eligibility mismatches stay out of the attention count; use **View all held opportunities** to inspect the complete list.

Answer a held question in its employer and posting context. **Answer for every employer** starts a reusable note for a recurring question, which still needs review. New wording or different options may need a fresh answer.

Verify uncertain results with the employer before recording the outcome and supporting evidence. Recording that no submission occurred keeps the application held for manual handling; it does not automatically retry it. Review **Saved answers & evidence** for the original response package and available receipt image.

On eligible job rows, **Applied manually** records your own application and **Don't apply** excludes that posting. Both survive rediscovery. **Undo** returns it to the queue. Manual applications count toward company limits and cooldowns. Jobs with confirmed or uncertain submission records use their existing reconciliation flow.

To exclude a company, use **Skip this company**. **Include this company again** removes the exclusion without erasing history or resuming submissions. Alternate company names share company limits; changing an alias cannot hide earlier applications or uncertainty holds.

## Models and request limits

Choose a model in **Settings → Connections**:

| Choice | Setup | Billing |
| --- | --- | --- |
| Claude Code CLI | Install Claude Code and run `claude auth login` on the worker machine | Supported subscription usage |
| Codex CLI | Install Codex CLI and run `codex login` with ChatGPT on the worker machine | Your plan's usage limits |
| Anthropic API | Supply a key and accessible model ID | Separate API billing |
| OpenAI API | Supply a key and accessible model ID | Separate API billing |

Subscription modes reject API-only authentication and omit inherited API keys. CLI versions must support the required isolation flags. Models receive no browser or shell tools; application code owns side effects. API keys stay in private local storage and are excluded from ordinary backups. A saved key does not establish model access; API connectivity is checked on the first request.

The model section in Connections shows durable worker request counts and available token metadata. Missing token usage is shown as unreported. Failed calls count toward limits. Drafting and grounding review are separate requests. These measurements do not show remaining subscription allowance, vendor credits or a dollar balance.

Defaults are six hours between batches, a target of seven confirmed applications per batch and 28 daily, with maximums of ten per batch and 40 daily. Additional defaults are ten attempts per batch, 40 model requests per batch, 100 model requests per local calendar day and a 180-minute batch time budget. Settings stores the values for your instance; existing installations may have different limits.

By default, there is no per-company application cap or cooldown. In Settings → Search & automation, **Applications per company** set to **0** means no company count limit; **Company cooldown, days** set to **0** means no waiting period or same-company daily restriction. Positive values enable those optional limits. Existing saved limits remain in place until you change them. Duplicate applications, uncertain outcomes, pending verification and company exclusions remain held even when company limits are disabled. Daily, batch and model-request limits are separate.

Older releases require positive company-limit values and cannot read unlimited preferences. Keep this release or newer when restoring an instance configured with zero; a downgrade must remain paused until its configuration compatibility is verified.

Targets are not promises of throughput. Rate limits stop work without automatic model retries. Set vendor spending limits separately for paid APIs. `hireme model-probe` makes one synthetic inference request and consumes request allowance.

## Pause, schedule and recover

```bash
.venv/bin/python -m hireme pause
.venv/bin/python -m hireme resume
.venv/bin/python -m hireme schedule status
```

Pause stops a cycle at cancellation checkpoints, including active CLI inference. An in-flight browser/API request may finish its timeout first. An employer request already sent cannot be undone. Resume permits a new cycle and cannot revive the cancelled one.

After saving a batch interval, choose **Settings → Search & automation → Apply saved interval**, then **Check schedule**. The CLI equivalent is `.venv/bin/python -m hireme schedule install`. Your computer must be awake for scheduled work. macOS uses a LaunchAgent; Linux uses cron unless Pi systemd units are installed. Cron intervals must divide 24 hours: 1, 2, 3, 4, 6, 8, 12 or 24.

To remove a schedule:

```bash
.venv/bin/python -m hireme schedule uninstall
```

`.venv/bin/python -m hireme daemon` is a foreground scheduler alternative. Use one scheduler per applicant. OS schedule names are shared per OS user, and installation verifies the applicant directory. Use separate OS accounts for independently scheduled applicants.

If a worker stopped unexpectedly, use **Recover and stay paused** after the worker lock is free, or:

```bash
.venv/bin/python -m hireme recover
```

Recovery keeps interrupted submission intents uncertain and unfinished accounts held for verification. It does not retry them.

## Back up and move your data

The authoritative database is `~/.local/share/please-hire-me/ledger.sqlite3`. Multiple checkouts share this directory by default. Documents, reviewed sources, evidence, browser sessions and credentials are private local files. For a different applicant, use another OS account or pass the same explicit `--data-dir` to every command for that instance. Do not split one applicant's history across directories.

Choose **Settings → Search & automation → Your data and backups** to download a backup after active work finishes. **Check a backup before moving it** validates an uploaded ZIP using a temporary restore, leaving the active workspace unchanged. Browser checks accept ZIP archives up to 1 GiB. The current CLI can restore up to 8 GiB compressed and 16 GiB expanded, subject to free disk space; update the restoration tool before using an archive larger than an older release supports.

For a terminal backup and restore:

```bash
.venv/bin/python -m hireme pause
.venv/bin/python -m hireme schedule uninstall
# Wait for active work to finish, then use a new filename:
.venv/bin/python -m hireme backup /absolute/private/history.zip
# On the destination, restore into a NEW private directory:
.venv/bin/python -m hireme --data-dir /absolute/private/new-applicant restore /absolute/private/history.zip
```

Backups contain history, documents and evidence and are **not encrypted**. Store them privately and keep an off-device copy. Restore verifies checksums and ledger references and starts paused. Old documents and screenshots referenced by past attempts must remain available. Ordinary backups exclude integration credentials, employer password keys and browser sessions; reconnect those on the destination.

For automatic encrypted Pi backups to a Mac, use the [operations guide](operations.md). The receiving Mac verifies a complete restore before marking an archive successful. Keep its age recovery key separately from the backup files.

Do not run the same applicant simultaneously on Mac and Pi. Pause the source and remove its schedule before moving history; locks do not coordinate across machines.

If both machines have independent application history, stop both before migration. Do not restore one over the other. An operator can retain original attempts in the destination's historical application records after verifying both private snapshots. These records remain visible in HireMeMaybe, outcome review, CSV exports and backups, and continue to enforce company limits and duplicate protection. Retained records cannot authorize employer writes. Reconcile each uncertain attempt separately; reconciling an older attempt does not replace the newer receipt for that job.

Employer passwords use a separate encrypted transfer. Pause both instances, restore matching history first, then use **Needs you → Move employer passwords to another computer**. Choose and repeat a passphrase of at least 12 characters. Keep the passphrase separately and remove the transfer file after verifying recovery.

```bash
# Source instance, using a NEW private filename:
.venv/bin/python -m hireme account-vault export /absolute/private/accounts.encrypted
# Destination instance with matching restored history:
.venv/bin/python -m hireme --data-dir /absolute/private/new-applicant account-vault import /absolute/private/accounts.encrypted
```

These commands prompt interactively. Transfer validates applicant/account identity, preserves verification states, and leaves submissions paused. It does not transfer Gmail/API credentials or browser sessions.

## Gmail and employer accounts

To find applications you made elsewhere, enable **Check Gmail for past applications** under **Connections → Email automation**. Open **Past applications from email**, enter a company such as Optiver or leave it blank, and choose **Scan application mail**. The first scan searches historical and archived application mail without a date cutoff; Spam and Trash are excluded. Receipt phrases are searched in the message text, so the employer's subject wording does not determine whether the message is found. Each bounded page saves its cursor; scan again when more mail remains. The CLI equivalent is `.venv/bin/python -m hireme gmail scan-applications --company Optiver`; omit `--company` for application mail across companies, or add `--restart` to repeat the full history search. Company searches use up to ten names, starting with the requested name and verified public names. Search an older name explicitly if your alias group is larger.

Role-specific email evidence holds matching jobs even if this app has no application record. Matching uses an exact posting link or a possible employer and complete role title in an application passage. Differences in year, location and seniority within the full role title are preserved. Ambiguous employer names create conservative holds instead of allowing another application. Inspect the original email, select the exact saved role and choose **Record past application** to keep the correct role held and release unrelated candidates. Dismiss unrelated messages. Company-only mail remains available for review; if no saved employer can be identified, use **Applied manually** on the verified job instead. An email hold does not mark a worker attempt confirmed or resolve an uncertain submission. Saved holds remain after Gmail is disconnected or scanning is disabled. Explicitly reviewed role links also survive changing your primary email within the same private workspace; unreviewed mail and original-message previews remain bound to the original account. To change an old email review, select its original confirmed account again. Email holds do not add to company quotas or cooldowns; use **Applied manually** when you need to record an external application in that history.

When enabled, live applications check the company's mailbox history before opening the application browser and require a completed check no older than 15 minutes before final submission. Incomplete scans or unavailable Gmail hold work rather than skip the check. Scans read at most 25 messages within a bounded work period; company checks have their own per-cycle ceiling tied to the attempt limit. Failed checks rotate behind fresh candidates; incomplete scans retain priority to make progress next cycle. A completed company scan restores normal priority. No application attempt is consumed by a mailbox check. Only bounded application passages, previews, posting-identity hashes and message references are retained privately. Authentication mail and self-sent reports are excluded; email links and attachments are never fetched, and inbox text is never sent to a model. Opaque Greenhouse board names are verified through the official public boards API. English-language matching can miss unusual receipt wording; an absent match is not proof that you never applied.

To track recruiting replies, enable **Find recruiting feedback in Gmail** under Connections → Email automation. Open **Recruiting feedback** and choose **Sync recruiting mail**, or run `.venv/bin/python -m hireme gmail sync-feedback`. Each sync handles at most ten messages within a bounded work period and retains its remaining page for the next sync. The initial search covers the last 30 days; later searches overlap to catch delayed arrivals. Matching uses the confirmed applicant recipient and prior application history, prioritizing exact posting URLs, then company/role text. Unmatched mail, authentication messages and messages sent by the applicant, including the app's own batch reports, are ignored. This English-language classifier can miss or misclassify messages.

Review the private preview and original Gmail message, correct its suggested category, choose the exact attempt and select **Confirm feedback**. Ambiguous company matches require your selection. Dismiss unrelated messages. Confirmed milestones appear in Applications and the job's application context. Feedback never marks an uncertain submission confirmed or releases it for retry; verify submission separately with the employer. Sender DKIM checks help inspection but do not prove the sender is authorized to recruit for that employer.

When feedback is enabled, live-mode batches also check a bounded mail page after finishing, including batches that stop paused. Preparation mode does not sync mail. This setting operates separately from sending batch reports. Message previews and reviews are included in private history backups; OAuth credentials are excluded. Inbox text is never sent to a model, and syncing does not send messages, follow links or fetch attachments.

Gmail is optional. Browser sign-in does not authorize background access. Create a Google Cloud project, enable Gmail API, configure OAuth consent and download a **Desktop app OAuth client JSON**. Upload it under Email automation, then run on the worker machine:

```bash
.venv/bin/python -m hireme gmail connect
.venv/bin/python -m hireme gmail status
.venv/bin/python -m hireme gmail check
```

Authorize the confirmed application email. Enable code handling and report delivery separately. Refer to [Google's OAuth quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) for provider setup; test-mode authorization may require reconnection.

`gmail status` checks saved connection files. `gmail check` refreshes credentials when needed and verifies the account through the Gmail API without reading messages or sending mail. Run it on the worker machine when verifying unattended operation; a saved token alone does not prove current access.

Code handling supports recognized Greenhouse challenges with sender, recipient, company, timing and one-use checks. Unsupported verification flows remain manual. Batch reports include attempted job links and are queued independently of applications. Missing authorization keeps mail pending; ambiguous delivery stays uncertain without automatic duplication. Mail failures never trigger application retries.

Employer account automation is off by default. Supported native forms use exact credentials, separate durable intents and one-use write grants. Account agreements, SSO, JavaScript-only registration and unverified account flows need manual handling. Workday application completion remains manual; see [release status](public-release.md).

To supply employer credentials, pause submissions and wait for active work to finish, then open **Needs you → Employer accounts → Supply credentials for an employer**. Enter the employer, exact HTTPS account origin and matching 20–128-character password. The confirmed applicant email is reused. Saving stores local credentials only and does not create an account, accept terms or replace existing account history.

For manual sign-in, use `.venv/bin/python -m hireme login APPROVED_PORTAL_URL` to open the dedicated browser. After verifying an account manually, record confirmation in **Needs you**. Account passwords stay outside model input, reports and ordinary backups.

## Troubleshooting

```bash
.venv/bin/python -m hireme doctor
.venv/bin/python -m hireme doctor --verify-login
```

The first checks saved setup and installed prerequisites. The second also checks subscription login. Neither makes model requests or applications. Use `--json` for structured output. The dashboard's **Check local setup** and **Download setup report** provide the same local guidance without exporting applicant answers or credentials.

For a foreground dashboard, **Ctrl+C** in its terminal stops it; start it again with `.venv/bin/python -m hireme dashboard --open`. Closing a browser tab does not stop the process. If port 8766 is occupied, reopen the existing dashboard with `.venv/bin/python -m hireme open-dashboard` or restart its owning process. For a Pi service, follow the [restart instructions](pi.md#restart-or-stop-the-dashboard).

Unsaved form drafts remain in the current page's memory. Save before closing or reloading it. A failed local request can be retried without clearing the form. If the dashboard is disconnected, restore its process before retrying.
