# Your information

Each applicant runs a private local instance. The default workspace is `~/.local/share/please-hire-me/`, outside the source checkout. It contains confirmed facts, documents, writing sources, application answers, receipts, screenshots, recruiting previews and operational history.

## Where data goes

| Feature | Data and destination |
| --- | --- |
| Job discovery | Public posting requests to the configured job sources. |
| Model-assisted preparation | The selected provider receives the question, posting context and selected confirmed facts or approved sources needed for the request. This is remote inference. |
| Supported applications | The intended employer and its reviewed application service receive the approved answers and documents when live work is authorized. |
| Gmail verification and feedback | The connected owner's mailbox is queried for the enabled feature. Message previews and reviewed links stay in the private workspace. Inbox text is not sent to a model; feedback does not fetch links or attachments. |
| Batch reports | Application summaries are sent through the connected Gmail account to that same owner. |
| Past applications from email | Opt-in scans read historical application mail, including archived messages. Bounded application passages, previews, message references, possible employer keys and posting-identity hashes stay privately in the ledger and backups. Ambiguous exact-role matches remain held for review without inventing a confirmed worker submission. Scanning sends no messages, fetches no email links or attachments, and supplies no email text to a model. Public Greenhouse board metadata can verify employer names. Reviewed role decisions persist within the same private workspace if the primary email changes; original mail previews remain account-bound. |
| Encrypted off-device backups | The configured Mac receives the history snapshot over SSH and retains an age-encrypted archive after a verified restore. |

## Credentials and local access

Provider credentials, Gmail tokens and browser sessions rely on the operating system's local account and filesystem protections. Optional employer password files have private permissions and are not encrypted in place. The separate account-transfer archive is encrypted. These credentials and sessions are excluded from ordinary history backups and must be reconnected after restoration.

Keep the dashboard's access token private. The token authorizes access to applicant information and controls. Local users with access to the same OS account can access its private files; filesystem permissions do not protect against a compromised account.

Ordinary history ZIPs are not encrypted. The Mac backup agent encrypts its retained archives, with its recovery key stored separately from the archive directory. Protect that key and keep a recoverable copy elsewhere. A Mac that is asleep, disconnected or signed out cannot perform its scheduled checks until it runs again.

## Keeping or removing data

History preserves the exact documents and known outcomes of past attempts. Facts and source changes invalidate future execution authority without rewriting those historical records. Reviewed recruiting feedback does not change submission confirmation.

Pause work, remove scheduling, wait for active work to finish and stop the dashboard before moving or removing a workspace. Keep a verified backup when you need the history. Starting an empty workspace loses the local evidence used to prevent duplicate applications; preserve and restore relevant attempts before resuming the same job search.

Removing local files does not remove information already sent to employers, model providers or Gmail. Use the relevant service's controls for its copy. This app does not erase employer records or recall requests already transmitted.

Private reports and exports can contain applicant information. Share a redacted reproduction using invented data when requesting support.
