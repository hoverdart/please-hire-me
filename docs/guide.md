# User guide

Start with the [installation instructions](../README.md#get-started). All commands below run from the repository directory. For a background dashboard on Raspberry Pi OS, use the [Pi guide](pi.md).

## Documents and confirmed facts

Upload a resume PDF with selectable text through **Your facts**. Review extracted values before confirming them; missing work authorization, sponsorship and other personal declarations require your answers. A transcript is optional. Use **Stop using transcript** to withdraw it from future uploads while retaining past application evidence.

New or changed values need confirmation. Reimporting an identical PDF preserves existing confirmations and drafts; replacing a document invalidates unattempted drafts containing it. Past attempts stay recorded. If a selected PDF is missing or unreadable, import it again before starting a batch. Re-uploading the matching original can repair its private copy.

Use **Basic context** for your current work, education, experience and preferences. **Add context in your own words** can propose fact updates and quoted notes from your explanation. Review those proposals before saving. Legal declarations and demographic answers still require explicit facts or scoped answers.

Clearing a saved optional fact removes it from future confirmed use. **Review saved answers** lets you find, revise or withdraw responses you previously approved. Source changes invalidate affected unattempted drafts; past attempted answers and evidence remain available.

## Writing and materials

In **Writing & context**, upload DOCX, PDF, PPTX, TXT or Markdown sources. Review their excerpts and approve each for personal factual evidence, background reference or style only. Style examples never establish your qualifications. Search and page through the complete source library; save or discard excerpt edits before changing pages.

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

Choose a connection in **Model connection**:

| Choice | Setup | Billing |
| --- | --- | --- |
| Claude Code CLI | Install Claude Code and run `claude auth login` on the worker machine | Supported subscription usage |
| Codex CLI | Install Codex CLI and run `codex login` with ChatGPT on the worker machine | Your plan's usage limits |
| Anthropic API | Supply a key and accessible model ID | Separate API billing |
| OpenAI API | Supply a key and accessible model ID | Separate API billing |

Subscription modes reject API-only authentication and omit inherited API keys. CLI versions must support the required isolation flags. Models receive no browser or shell tools; application code owns side effects. API keys stay in private local storage and are excluded from ordinary backups. A saved key does not establish model access; API connectivity is checked on the first request.

Model connection shows durable worker request counts and available token metadata. Missing token usage is shown as unreported. Failed calls count toward limits. Drafting and grounding review are separate requests. These measurements do not show remaining subscription allowance, vendor credits or a dollar balance.

Defaults are six hours between batches, a target of seven confirmed applications per batch and 28 daily, with maximums of ten per batch and 40 daily. Additional defaults are ten attempts per batch, 40 model requests per batch, 100 model requests per local calendar day and a 180-minute batch time budget. Preferences stores the values for your instance; existing installations may have different limits.

Targets are not promises of throughput. Rate limits stop work without automatic model retries. Set vendor spending limits separately for paid APIs. `hireme model-probe` makes one synthetic inference request and consumes request allowance.

## Pause, schedule and recover

```bash
.venv/bin/python -m hireme pause
.venv/bin/python -m hireme resume
.venv/bin/python -m hireme schedule status
```

Pause stops a cycle at cancellation checkpoints, including active CLI inference. An in-flight browser/API request may finish its timeout first. An employer request already sent cannot be undone. Resume permits a new cycle and cannot revive the cancelled one.

After saving a batch interval, choose **Preferences → Apply saved interval**, then **Check schedule**. The CLI equivalent is `.venv/bin/python -m hireme schedule install`. Your computer must be awake for scheduled work. macOS uses a LaunchAgent; Linux uses cron unless Pi systemd units are installed. Cron intervals must divide 24 hours: 1, 2, 3, 4, 6, 8, 12 or 24.

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

Choose **Preferences → Your data and backups** to download a backup after active work finishes. **Check a backup before moving it** validates an uploaded ZIP using a temporary restore, leaving the active workspace unchanged. Archives up to 1 GiB are supported.

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

Do not run the same applicant simultaneously on Mac and Pi. Pause the source and remove its schedule before moving history; locks do not coordinate across machines.

Employer passwords use a separate encrypted transfer. Pause both instances, restore matching history first, then use **Needs you → Move employer passwords to another computer**. Choose and repeat a passphrase of at least 12 characters. Keep the passphrase separately and remove the transfer file after verifying recovery.

```bash
# Source instance, using a NEW private filename:
.venv/bin/python -m hireme account-vault export /absolute/private/accounts.encrypted
# Destination instance with matching restored history:
.venv/bin/python -m hireme --data-dir /absolute/private/new-applicant account-vault import /absolute/private/accounts.encrypted
```

These commands prompt interactively. Transfer validates applicant/account identity, preserves verification states, and leaves submissions paused. It does not transfer Gmail/API credentials or browser sessions.

## Gmail and employer accounts

Gmail is optional. Browser sign-in does not authorize background access. Create a Google Cloud project, enable Gmail API, configure OAuth consent and download a **Desktop app OAuth client JSON**. Upload it under Email automation, then run on the worker machine:

```bash
.venv/bin/python -m hireme gmail connect
.venv/bin/python -m hireme gmail status
```

Authorize the confirmed application email. Enable code handling and report delivery separately. Refer to [Google's OAuth quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) for provider setup; test-mode authorization may require reconnection.

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
