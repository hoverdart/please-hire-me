"""Read-only release evidence. Never initialize, migrate, recover or resume a ledger.

Applicant files are never changed. A temporary snapshot uses private persistent
storage beside the applicant directory and is removed after inspection.
Integration state is configuration evidence, not connectivity or acceptance.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

from .config import validate_settings
from .util import digest, now

PROTECTED_TABLES = (
    'applications', 'archived_applications', 'facts', 'answers', 'writing_answers', 'templates',
    'materials', 'documents', 'generated_documents', 'application_artifacts',
    'questions', 'job_holds', 'job_decisions',
)
OPERATIONAL_TABLES = (
    'employer_accounts', 'remote_draft_steps', 'adapter_verifications',
    'confirmed_history', 'workday_drafts', 'recruiting_feedback', 'recruiting_sync',
)


class ReadOnlyLedger:
    """The small read interface shared by scheduler and adapter status checks."""

    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        path = self.root / 'ledger.sqlite3'
        if self.root.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('ledger_unavailable')
        self._temporary = tempfile.TemporaryDirectory(prefix='.hireme-release-check-', dir=self.root.parent)
        try:
            # Even mode=ro SQLite can create/update WAL/SHM sidecars. Inspect a
            # private copy instead. immutable=1 on the source would silently
            # omit committed WAL entries. Reject a copy racing with any writer.
            def generation():
                result = {}
                # A rollback-mode hot journal can contain the only committed
                # pre-crash page values. Never inspect a bare copy without it
                # or recover the original while producing a status report.
                journal = self.root / 'ledger.sqlite3-journal'
                if journal.exists() or journal.is_symlink():
                    raise ValueError('ledger_recovery_required')
                for name in ('ledger.sqlite3', 'ledger.sqlite3-wal'):
                    source = self.root / name
                    try:
                        metadata = source.lstat()
                    except FileNotFoundError:
                        if name == 'ledger.sqlite3':
                            raise ValueError('ledger_unavailable') from None
                        result[name] = None
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError('ledger_unavailable')
                    result[name] = (metadata.st_dev, metadata.st_ino, metadata.st_size,
                                    metadata.st_mtime_ns, metadata.st_ctime_ns)
                return result
            before = generation()
            from .storage import MIN_FREE_BYTES
            required = sum(info[2] for info in before.values() if info) + MIN_FREE_BYTES
            if shutil.disk_usage(self.root.parent).free < required:
                raise ValueError('snapshot_disk_space_unavailable')
            snapshot = Path(self._temporary.name)
            for name, metadata in before.items():
                if metadata is not None:
                    try:
                        source = (self.root / name).open('rb')
                    except FileNotFoundError:
                        raise ValueError('ledger_changed_during_snapshot') from None
                    with source, (snapshot / name).open('xb') as target:
                        os.fchmod(target.fileno(), 0o600)
                        shutil.copyfileobj(source, target, 1024 * 1024)
            if before != generation():
                raise ValueError('ledger_changed_during_snapshot')
            self.db = sqlite3.connect((snapshot / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=5)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA query_only=ON')
            self.db.execute('BEGIN')
        except BaseException:
            self._temporary.cleanup()
            raise

    def close(self):
        self.db.close()
        self._temporary.cleanup()

    def settings(self):
        row = self.db.execute('SELECT value FROM config WHERE id=1').fetchone()
        if not row:
            raise ValueError('settings_unavailable')
        # Do not call Store.settings(): it can expire a model-limit override.
        return validate_settings(json.loads(row[0]))

    def facts(self):
        return {row['key']: dict(row) for row in self.db.execute('SELECT * FROM facts WHERE confirmed=1')}


def _revision(repo):
    try:
        revision = subprocess.run(
            ['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=normal'],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', revision):
            raise ValueError('revision_unavailable')
        return {'revision': revision, 'dirty': bool(dirty), 'available': True}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'revision': None, 'dirty': None, 'available': False}


def _fingerprint(db, table):
    checksum = hashlib.sha256()
    count = 0
    # Names come only from the fixed table lists above, never from input.
    for row in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
        checksum.update(json.dumps(dict(row), sort_keys=True, default=str).encode())
        checksum.update(b'\n')
        count += 1
    return {'rows': count, 'sha256': checksum.hexdigest()}


def _documents(view):
    checked = 0
    failed = 0
    seen = set()
    references = []
    for table, directory in (
        ('documents', 'documents'), ('generated_documents', 'documents'),
        ('materials', 'materials'), ('application_artifacts', 'documents'),
    ):
        for row in view.db.execute('SELECT hash,filename FROM ' + table):
            references.append((directory, *row))
    screenshots = []
    for row in view.db.execute('SELECT package,screenshot FROM application_records'):
        try:
            package = json.loads(row['package'])
        except (ValueError, TypeError):
            package = {}
        documents = package.get('documents', []) if isinstance(package, dict) else []
        if isinstance(documents, list):
            references.extend(('documents', entry.get('hash'), entry.get('filename'))
                              for entry in documents if isinstance(entry, dict))
        if row['screenshot']:
            screenshots.append(row['screenshot'])
    for directory, checksum, filename in references:
        checked += 1
        if (not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum)
                or not isinstance(filename, str)
                or not re.fullmatch(re.escape(checksum) + r'\.(?:pdf|docx|pptx|txt|md)', filename)):
            failed += 1
            continue
        identity = (directory, checksum, filename)
        if identity in seen:
            checked -= 1
            continue
        seen.add(identity)
        path = view.root / directory / filename
        try:
            if ((view.root / directory).is_symlink() or path.is_symlink() or not path.is_file()
                    or path.resolve().parent != (view.root / directory).resolve()):
                raise ValueError('unsafe_document')
            with path.open('rb') as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
            if actual != checksum:
                raise ValueError('changed_document')
        except (OSError, ValueError):
            failed += 1
    for filename in set(screenshots):
        if (not isinstance(filename, str) or not re.fullmatch('[0-9a-f]{64}-(?:before|after|verified)\\.jpg', filename)
                or (view.root / 'screenshots').is_symlink()
                or (view.root / 'screenshots' / filename).is_symlink()
                or not (view.root / 'screenshots' / filename).is_file()):
            failed += 1
    return {'checked': checked, 'screenshots_checked': len(set(screenshots)), 'failed': failed, 'ready': failed == 0}


def _scheduler(view):
    from . import scheduler
    try:
        daemon_running = False
        try:
            daemon_fd = os.open(view.root / 'scheduler.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            pass
        else:
            try:
                if not stat.S_ISREG(os.fstat(daemon_fd).st_mode):
                    raise ValueError('unsafe_scheduler_lock')
                try:
                    fcntl.flock(daemon_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    daemon_running = True
            finally:
                os.close(daemon_fd)
        primary = scheduler.status(view)
        records = [primary]
        if primary.get('kind') == 'systemd' and shutil.which('crontab'):
            records.append(scheduler.status(view, cron_only=True))
        installed = [record for record in records if record.get('installed')]
        installed_count = sum(record.get('installed_count', 1) for record in installed)
        owned = all(record.get('matches_applicant') is True for record in installed)
        result = {
            'installed_count': installed_count, 'matches_applicant': owned,
            'kind': primary.get('kind'), 'interval_hours': primary.get('interval_hours'),
            'enabled': primary.get('enabled'), 'active': None,
            'daemon_running': daemon_running,
            'ready': (not daemon_running and installed_count <= 1 and owned
                      and not any(record.get('error') for record in records)),
        }
        if primary.get('kind') == 'systemd':
            active = subprocess.run(
                ['systemctl', '--user', 'is-active', 'please-hire-me-worker.timer'],
                capture_output=True, text=True, timeout=5,
            )
            result['active'] = active.returncode == 0 and active.stdout.strip() == 'active'
        elif primary.get('kind') == 'launchd':
            active = subprocess.run(
                ['launchctl', 'print', f'gui/{os.getuid()}/{scheduler.LABEL}'],
                capture_output=True, text=True, timeout=5,
            )
            result['active'] = result['enabled'] = active.returncode == 0
        elif installed and sys.platform == 'linux' and shutil.which('systemctl'):
            active = subprocess.run(['systemctl', 'is-active', 'cron'], capture_output=True, text=True, timeout=5)
            result['active'] = result['enabled'] = active.returncode == 0 and active.stdout.strip() == 'active'
        result['interval_matches_settings'] = (primary.get('interval_hours') == view.settings()['schedule_hours']) if installed else None
        if installed and not result['interval_matches_settings']:
            result['ready'] = False
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'installed_count': None, 'matches_applicant': None, 'ready': False, 'error': 'scheduler_unavailable'}


def _private_json(path):
    # Never create private directories or follow credential symlinks for status.
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        return None
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _integrations(view, settings):
    provider = settings['provider']
    integrations = view.root / 'integrations'
    if integrations.is_symlink():
        return {'ready': False, 'error': 'unsafe_integration_directory'}
    if provider.endswith('-cli'):
        model_configured = bool(shutil.which('claude' if provider == 'claude-cli' else 'codex'))
    else:
        key = _private_json(integrations / 'provider-key.json') or {}
        model_configured = (key.get('provider') == provider and isinstance(key.get('key'), str)
                            and 20 <= len(key['key']) <= 512 and bool(settings['provider_model']))
    account = _private_json(integrations / 'gmail-account.json') or {}
    owner = view.facts().get('email', {})
    owner_email = owner.get('value', '') if owner.get('confirmed') else ''
    token = integrations / 'gmail-token.json'
    gmail_bound = (bool(owner_email) and account.get('email') == owner_email.casefold()
                   and token.is_file() and not token.is_symlink())
    gmail_enabled = any(settings[key] for key in ('gmail_reports', 'gmail_verification', 'gmail_feedback'))
    return {
        'ready': model_configured and (not gmail_enabled or gmail_bound),
        'model': {'provider': provider, 'configured': model_configured, 'login_and_connectivity': 'not_checked'},
        'gmail': {'enabled': gmail_enabled, 'owner_bound_configuration': gmail_bound, 'api_access': 'not_checked'},
        'employer_sessions': 'not_checked',
    }


def check_release(root, repo, *, expected_revision=None, require_paused=False, require_scheduler=False):
    """Return aggregate private evidence without invoking Store or integrations."""
    if expected_revision is not None and not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', expected_revision):
        raise ValueError('Use a complete lowercase Git revision')
    report = {
        'format_version': 1, 'checked_at': now(), 'ready': False, 'failure_codes': [],
        'requested_gates': {'revision': expected_revision, 'paused': require_paused, 'scheduler': require_scheduler},
        'evidence_limits': [
            'Local snapshot only; no migrations, recovery, application writes, inference or mail.',
            'Integration configuration does not prove current access or employer acceptance.',
            'Quick integrity check does not replace a fully validated history restore.',
        ],
    }
    code = report['code'] = _revision(repo)
    failures = report['failure_codes']
    if not code['available']:
        failures.append('revision_unavailable')
    elif code['dirty']:
        failures.append('checkout_dirty')
    if expected_revision and code['revision'] != expected_revision:
        failures.append('revision_mismatch')
    view = None
    lock_fd = None
    try:
        from .storage import capacity
        report['storage']=capacity(root)
        if report['storage']['low']:
            failures.append('storage_low')
        view = ReadOnlyLedger(root)
        lock = view.root / 'worker.lock'
        worker_running = False
        if lock.exists() or lock.is_symlink():
            lock_fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                worker_running = True
        pending = {
            'runs': view.db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0],
            'submissions': view.db.execute("SELECT COUNT(*) FROM application_records WHERE state='submitting'").fetchone()[0],
            'accounts': view.db.execute("SELECT COUNT(*) FROM employer_accounts WHERE state IN ('creating','signing_in')").fetchone()[0],
        }
        report['worker'] = {'running': worker_running, 'unfinished': pending}
        if worker_running or any(pending.values()):
            failures.append('worker_not_idle')
        settings = view.settings()
        report['controls'] = {
            'paused': not settings['live_enabled'], 'onboarding_complete': settings['onboarding_complete'],
            'company_exclusions': len(settings['skip_companies']), 'remote_drafts': settings['remote_drafts'],
        }
        if settings['deployment']=='pi':
            from .pi import memory_controller_available
            report['memory_controller_available']=memory_controller_available()
            if not report['memory_controller_available']:failures.append('memory_controller_unavailable')
        if require_paused and settings['live_enabled']:
            failures.append('submissions_not_paused')
        if not settings['onboarding_complete']:
            failures.append('onboarding_incomplete')
        from .store import SCHEMA
        metadata = view.db.execute('SELECT fingerprint,schema_version FROM schema_metadata WHERE id=1').fetchone()
        schema_version = view.db.execute('PRAGMA schema_version').fetchone()[0]
        schema_matches = bool(metadata and metadata['fingerprint'] == digest(SCHEMA)
                              and metadata['schema_version'] == schema_version)
        integrity = view.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        report['schema'] = {'matches_code': schema_matches, 'sqlite_schema_version': schema_version,
                            'integrity_method': 'quick_check', 'integrity_ok': integrity}
        if not schema_matches:
            failures.append('schema_mismatch')
        if not integrity:
            failures.append('ledger_integrity_failed')
        tables = {row[0] for row in view.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        report['protected_history'] = {table: _fingerprint(view.db, table) for table in PROTECTED_TABLES}
        report['operational_history'] = {table: _fingerprint(view.db, table) for table in OPERATIONAL_TABLES if table in tables}
        states = {}
        for state, count in view.db.execute('SELECT state,COUNT(*) FROM application_records GROUP BY state'):
            name = state if state in {'prepared', 'submitting', 'confirmed', 'unknown', 'awaiting_verification', 'not_submitted'} else 'other'
            states[name] = states.get(name, 0) + count
        report['application_states'] = states
        report['documents'] = _documents(view)
        if not report['documents']['ready']:
            failures.append('documents_missing_or_changed')
        schedule = report['scheduler'] = _scheduler(view)
        if not schedule['ready']:
            failures.append('scheduler_unsafe_or_unavailable')
        if require_scheduler and (schedule.get('installed_count') != 1
                                  or schedule.get('enabled') is not True or schedule.get('active') is not True):
            failures.append('scheduler_not_active')
        report['integrations'] = _integrations(view, settings)
        if not report['integrations']['ready']:
            failures.append('integration_configuration_incomplete')
        from .workday import status as workday_status
        report['workday'] = workday_status(view)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        # SQL, filesystem and integration errors can contain personal values.
        failures.append('local_snapshot_unavailable')
    finally:
        if view is not None:
            view.close()
        if lock_fd is not None:
            os.close(lock_fd)
    report['ready'] = not failures
    return report


def write_report(path, report):
    """Create one explicitly requested private file; never overwrite evidence."""
    path = Path(path).expanduser()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
