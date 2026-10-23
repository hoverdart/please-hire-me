# Final release verification and rollout

Use one frozen commit for the final verification pass. Keep personal evidence in private storage, outside Git. Designate one primary worker; every secondary installation must remain paused with its scheduler disabled.

## Freeze and validate

Finish adapter changes and meaningful regression fixtures, inspect the complete diff, and commit once. Run the full local suite once on that content, then require all four platform CI test jobs, their wheel/source installation and privacy checks, and the dependency audit to pass at the exact commit. Rerun only checks invalidated by a change or unresolved failure. Stage changed browser cases on a disposable applicant root using the target machine's Chromium; never use employers as development fixtures.

`hireme release-check` reads a consistent private snapshot without initializing, migrating, repairing or changing applicant files. It copies the ledger and any WAL to a temporary directory beside the applicant root, checks that the source did not change during copying, and removes that directory afterward. Allow persistent disk space for another ledger plus WAL; it does not stage a large ledger on the Pi's RAM disk. Keep the workspace idle while taking the snapshot. An active writer or insufficient disk space fails the check without automatic retries.

The report contains the revision/dirty state, schema compatibility, SQLite quick-check result, protected-table counts and fingerprints, referenced-document checks, worker state, scheduler ownership and safe integration configuration. It excludes applicant values, credential contents and arbitrary error text. It does not log into services, refresh OAuth, infer answers, send mail or submit applications. An expired model-limit override is left unchanged. A successful quick check does not replace a full backup restoration test.

Before the update, pause the old installation, suspend its scheduler and wait for active work to finish. The first upgrade cannot invoke `release-check` from the old checkout: that command is new in this release. Capture the old revision, old schema and history fingerprints with the verified candidate's read-only helper instead. Set `CANDIDATE_WORK` to the existing isolated staging directory containing its verified `source/` and `venv/`; this command does not install or migrate anything. Keep the output filename new:

```bash
CANDIDATE_WORK="$HOME/.local/share/please-hire-me/release-candidate"
"$CANDIDATE_WORK/venv/bin/python" - "$CANDIDATE_WORK/source" "$HOME/please-hire-me" "$HOME/.local/share/please-hire-me" "$HOME/.local/share/please-hire-me/before-update.json" <<'PY'
import json
from pathlib import Path
import sys

candidate, checkout, applicant, output = map(Path, sys.argv[1:])
sys.path.insert(0, str(candidate.resolve(strict=True)))
from hireme.release_check import (
    OPERATIONAL_TABLES, PROTECTED_TABLES, ReadOnlyLedger,
    _fingerprint, _revision, write_report,
)

revision = _revision(checkout)
if not revision['available'] or revision['dirty']:
    raise SystemExit('Stop: the old checkout must be available and clean.')
view = ReadOnlyLedger(applicant)
try:
    settings = json.loads(view.db.execute('SELECT value FROM config WHERE id=1').fetchone()[0])
    if settings.get('live_enabled') is not False:
        raise SystemExit('Stop: pause the old installation first.')
    if view.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise SystemExit('Stop: the old ledger failed its integrity check.')
    tables = {row[0] for row in view.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    metadata = view.db.execute('SELECT fingerprint,schema_version FROM schema_metadata WHERE id=1').fetchone()
    report = {
        'format_version': 1, 'evidence_scope': 'pre_upgrade_baseline', 'code': revision,
        'schema': {'stored_metadata': dict(metadata) if metadata else None,
                   'sqlite_schema_version': view.db.execute('PRAGMA schema_version').fetchone()[0],
                   'integrity_method': 'quick_check', 'integrity_ok': True},
        'protected_history': {name: _fingerprint(view.db, name) for name in PROTECTED_TABLES},
        'operational_history': {name: _fingerprint(view.db, name) for name in OPERATIONAL_TABLES if name in tables},
    }
finally:
    view.close()
if _revision(checkout) != revision:
    raise SystemExit('Stop: the old checkout changed during inspection.')
write_report(output, report)
print('Pre-upgrade baseline saved; candidate readiness has not been asserted.')
PY
```

The baseline records the schema already installed; it does not require the old checkout or ledger to match the new release. It uses a private ledger snapshot and never instantiates `Store`. In the post-update `release-check` command below, exit status is `0` for the requested local gates, `1` for failed readiness, and `2` for invalid arguments or an unwritable/existing output file. Output files are mode `0600` and are never overwritten. The checkout must be clean. Provider installation/key presence and saved Gmail owner binding are reported separately from independently verified access.

## Deploy paused, then commission

Follow the [Pi upgrade sequence](pi.md#update-an-existing-checkout): pause, suspend the applicant's scheduler, wait for the active worker, validate a private backup, stop the dashboard, fast-forward to the exact verified commit, install locked dependencies and restart the dashboard. Preserve unrelated Git changes; a dirty or diverged checkout stops deployment.

For the final rollout, replace the guide's moving-branch pull with the exact revision below. Set `RELEASE_SHA` to the complete commit that passed verification. Run each line only after the preceding line succeeds; the equality check must pass before installation or dashboard startup:

```bash
cd "$HOME/please-hire-me"
systemctl --user stop please-hire-me-dashboard.service
git diff --quiet && git diff --cached --quiet && test -z "$(git ls-files --others --exclude-standard)"
git fetch origin "$RELEASE_SHA"
git merge --ff-only "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA"
.venv/bin/python -m pip install -r requirements/runtime.lock
.venv/bin/python -m pip install --no-deps -e .
systemctl --user start please-hire-me-dashboard.service
```

After updating, compare protected-history fingerprints and every pre-existing operational-history fingerprint with the before-update report. Newly added tables may be empty; existing structured history, draft acknowledgements and uncertainty must survive. Any intended schema/data change must have its own explicit preservation comparison. Reuse an already verified backup restore when neither schema nor preservation requirements changed; do not repeat a large restore merely because adapter code changed.

Verify the dashboard loopback/authentication boundaries, `doctor --verify-login`, one synthetic `model-probe`, and `gmail check` if Gmail is enabled. These commands establish different evidence; `release-check` does not replace them. Run the actual worker service while paused: it must exit successfully with zero attempts/submissions and a suppressed idle report. Restore exactly one applicant-owned timer at the saved interval, with no parallel cron or daemon, then check:

```bash
.venv/bin/python -m hireme release-check --expected-revision "$RELEASE_SHA" --require-paused --require-scheduler --output /absolute/private/after-update.json
.venv/bin/python -m hireme workday status --tenant nvidia --json
```

`release-check` reports local readiness even when an unsupported adapter is disabled. Its success cannot satisfy the NVIDIA automation gate. NVIDIA remains unavailable while its status reports `protocol_reviewed: false` or `operationally_verified: false`; the native driver, authenticated flow evidence and bounded commissioning must be completed before claiming that capability works.

Commission an adapter only through a legitimate, eligible and history-clear application with confirmed required answers and scoped consent. Use a bounded ordinary worker run, at most one attempt/submission, with guaranteed repause. A native save/upload acknowledgement is not an application receipt. Keep any uncertain outcome blocked; never retry it or select further employers just to chase a successful test.

Check employer-visible application history as well as the local ledger. A completed application discovered in the authenticated account still counts toward duplicate and company-cooldown decisions when it predates local tracking. Keep its historical evidence separate from a newly observed submission receipt. A commissioning request does not waive company limits or authorize another application to an already completed requisition.

Live browser automation is limited to the reviewed Greenhouse, Ashby and Lever transports and the separately commissioned NVIDIA controller. Other generic browser adapters stop before navigation that could transmit applicant values, uploads or final intents; discovery and local preparation remain available. Keep unsupported Workday tenants manual.

Workday capability is tenant/flow specific. A configured career site, remembered login, enabled setting, arbitrary signature or synthetic fixture cannot activate authenticated saves or submission. Reviewed native transports, current employer-bound sessions, acknowledged ordered steps and fresh correlated receipts are separate requirements. Unknown transport, new legal wording, MFA, unsupported steps or missing facts stop with a manual action. Account credentials and sessions remain private and require separate recovery after restoration.

The explicit commands below never install or enable a missing native transport. With a reviewed transport present, commissioning requires pre-enabled submissions and remote drafts, selects exactly one eligible job without discovery, retains normal budgets and reporting, and re-pauses after the bounded cycle. Reconciliation reads back the existing held draft and cannot retry a recorded final attempt:

```bash
.venv/bin/python -m hireme workday commission --job-id JOB_ID
.venv/bin/python -m hireme workday reconcile --job-id JOB_ID
```

## Observe and decide

Audit one ordinary timer-triggered cycle at its saved interval. Check persisted run/outcome counts, day/company/attempt limits, prior uncertainty exclusions, actionable holds and the report outbox. A valid idle cycle proves scheduling behavior, not employer acceptance. Verify report delivery independently; a queued message is not sent mail, and a provider send acknowledgement is not proof that the user read it.

Report one decision with evidence tied to the exact commit: engineering checks passed; deployed checks passed; scheduler operation observed; and employer acceptance proved for specifically named supported flows. Any unverified capability remains disabled or manual. External blockers do not justify weakening consent, source validation, budgets or duplicate protection.

## Roll back without erasing attempts

For migration between independently used workers, preserve and verify both snapshots before any cutover. Retain every original application identity, including separate attempts on the same requisition, with its source snapshot provenance. The operator-only `Store.retain_applications` import requires a paused, idle worker and rejects conflicting identities. Historical attempts participate in duplicate, company, uncertainty and daily limits; historical preparation never grants execution authority. Keep referenced private documents and screenshots, original manual decisions, and unsent-report evidence. Hold old pending reports for explicit delivery reconciliation instead of silently sending them during cutover. Verify destination access, integrations, recovery and history counts before removing personal data from the source.

Pause and suspend scheduling first. Preserve the **current** ledger, including all commissioning and post-deployment intents/outcomes. For compatible schema changes, test the previous code against a private copy of the newest ledger and roll back code while retaining that history.

Never overwrite production with a pre-application backup and resume: it can erase a submission intent and permit a duplicate. If the old code cannot read the newest ledger, remain paused, preserve both copies and reconcile newer application history before any restored instance can resume. Historical adapter evidence must not manufacture a current authenticated session or activate a new machine.
