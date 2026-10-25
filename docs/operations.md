# Operating a Pi installation

Use one primary worker for an applicant. Keep other installations paused with scheduling disabled. The [Pi guide](pi.md) covers service setup; the [release runbook](final-release-runbook.md) covers upgrades and rollback.

## Check health

On the Pi:

```bash
cd ~/please-hire-me
.venv/bin/python -m hireme health --json
```

This checks storage, the latest cycle, report delivery, model subscription login, saved integration configuration, dashboard service, scheduler ownership and the latest off-device backup acknowledgement. It sends no applications, messages or model requests. It does not read Gmail messages or prove current Gmail API access. Use `hireme gmail check` separately for that access check.

A paused cycle is healthy when it is expected. Unknown outcomes, pending verification and intentionally held historical reports stay visible in the counts; they do not authorize retries. A stalled or overdue cycle, missing scheduler, expired model login, overdue report or low storage needs attention.

The worker refuses to start new application work below 512 MiB free. Backups and restoration reserve that headroom too. `release-check` additionally flags low storage below 10% of the volume, or 1 GiB, whichever is larger. These checks cannot reserve space against unrelated programs writing at the same time.

Health is a small query-only read of the current SQLite/WAL state. It is separate from the full snapshot, integrity and history checks in `release-check`.

## Encrypted backups and monitoring on a Mac

The receiving Mac polls Pi health every 15 minutes and takes a consistent history backup every six hours. It encrypts the stream with age, decrypts it in private temporary storage and performs a full restore, including SQLite integrity and document-reference checks. Only then does it publish the archive and acknowledge success to the Pi.

The agent retains verified archives for 14 days and always keeps the newest two. Failed or unverified downloads cannot acknowledge success or trigger retention cleanup. Credentials and browser sessions remain excluded from these ordinary history backups. Backups do not run applications.

Install `age` on the Mac and use a dedicated SSH key. Keep all configuration, keys, archives and logs outside the checkout in private directories. A suitable layout is:

```text
~/.local/share/please-hire-me-operations/
  keys/              private SSH identity and age recovery key
  backups/           verified encrypted archives and verification metadata
  monitor/           private health and backup status
  monitor.json       private configuration
```

Install age with `brew install age`. Generate an age identity with `age-keygen -o /absolute/private/backup.agekey`; derive its public recipient with `age-keygen -y /absolute/private/backup.agekey`. Use mode `0600` for keys and configuration, and `0700` for their directories. Keep a recoverable copy of the age identity separately; the SSH key cannot decrypt the archives.

The Pi's dedicated authorized-key entry must use `restrict` and a forced command pointing to the verified checkout, for example:

```text
restrict,command="cd /home/applicant/please-hire-me && .venv/bin/python -m hireme remote-ops" ssh-ed25519 PUBLIC_KEY
```

Replace the path and public key for that installation. The helper accepts exactly `health`, `backup` and `backup-ack`; the key cannot select another data directory or execute arbitrary shell commands. The Mac requires an already verified SSH host key and uses batch mode without password prompts or SSH agent forwarding.

Create the private Mac configuration with these fields, replacing the example paths and recipient:

```json
{
  "host": "applicant@pi.local",
  "identity": "/absolute/private/keys/pi-backup-ed25519",
  "age_identity": "/absolute/private/keys/backup.agekey",
  "age_binary": "/opt/homebrew/bin/age",
  "ssh_binary": "/usr/bin/ssh",
  "destination": "/absolute/private/backups",
  "state_directory": "/absolute/private/monitor",
  "recipient": "age1REPLACE_WITH_THE_PUBLIC_RECIPIENT"
}
```

Run and inspect the first backup before installing the schedule:

```bash
.venv/bin/python -m hireme backup-agent run /absolute/private/monitor.json --force
.venv/bin/python -m hireme backup-agent install /absolute/private/monitor.json
launchctl print gui/$(id -u)/com.pleasehireme.operations
```

`monitor/latest.json` records current health, the last verified archive, attempt fingerprints and whether the Pi acknowledged it. Notifications use the Mac desktop, independently of Gmail. Repeated identical alerts are limited to once per six hours; changed failures can notify sooner. Notification delivery depends on macOS permissions and notification settings.

The Mac must be awake, connected and logged in for this user agent to run. Missed checks can be detected on its next run; this arrangement does not provide an always-on external monitor while the Mac is unavailable. Keep enough free Mac storage for the encrypted download and a temporary complete restore. The current agent requires at least 4.5 GiB free before a download; larger histories need more restoration space.

To stop this monitor without changing Pi application scheduling:

```bash
launchctl bootout gui/$(id -u)/com.pleasehireme.operations
```

## Restore a verified encrypted backup

On the Mac, decrypt to private storage and restore into a new directory:

```bash
age -d -i /absolute/private/keys/backup.agekey -o /absolute/private/history.zip /absolute/private/backups/ARCHIVE.zip.age
.venv/bin/python -m hireme --data-dir /absolute/private/restored-applicant restore /absolute/private/history.zip
```

Inspect history and uncertain outcomes before choosing a primary worker. Restoration starts paused and requires integrations to be reconnected. Never restore an older snapshot over newer attempts and resume; preserve and reconcile the newest ledger first. Remove the temporary unencrypted ZIP when the verified restore is no longer needed.

CLI archives support up to 8 GiB compressed and 16 GiB expanded. The browser's upload/check limit remains 1 GiB. Older application versions may reject larger archives; upgrade the restoration tool first.

## Review failures

- **Storage:** stop new work and recover capacity. Verify an off-device copy before removing retired workspaces. Preserve attempt records and their referenced files.
- **Unknown outcome or pending verification:** inspect the existing attempt with the employer. Do not rerun it to test recovery.
- **Expired login:** reconnect on the worker machine, then verify access independently. A saved configuration file is not live access.
- **Failed or uncertain report:** inspect its outbox state. Do not resend held historical reports without reconciliation.
- **Source failures:** nonexistent boards, rate limits, upstream failures and access restrictions have different diagnostics and retry delays. Known healthy boards are checked before speculative candidates. Source failure does not erase cached postings.

Unchanged discovery exclusions retain their original hold instead of adding a new pair of events on every refresh. Changed postings or relevant facts/preferences still cause reevaluation. Previous event history is preserved.
