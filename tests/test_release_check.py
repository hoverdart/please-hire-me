import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from hireme import release_check
from hireme.cli import main

REVISION = 'a' * 40


@pytest.fixture
def local_checks(monkeypatch):
    monkeypatch.setattr(release_check, '_revision', lambda _: {'revision': REVISION, 'dirty': False, 'available': True})
    monkeypatch.setattr(release_check, '_scheduler', lambda _: {
        'installed_count': 0, 'matches_applicant': True, 'ready': True,
    })
    monkeypatch.setattr(release_check, '_integrations', lambda *_: {'ready': True})
    import hireme.workday
    monkeypatch.setattr(hireme.workday, 'status', lambda _: {'version': 1, 'tenants': []}, raising=False)


def inventory(root):
    return {str(path.relative_to(root)): (
        stat.S_IMODE(path.stat().st_mode), hashlib.sha256(path.read_bytes()).hexdigest(),
    ) for path in root.rglob('*') if path.is_file()}


def test_cli_never_initializes_missing_applicant(tmp_path, local_checks, monkeypatch, capsys):
    monkeypatch.setattr('hireme.cli.Store', lambda _: pytest.fail('Read-only command instantiated Store'))
    root = tmp_path / 'absent'
    assert main(['--data-dir', str(root), 'release-check', '--json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['failure_codes'] == ['local_snapshot_unavailable']
    assert not root.exists()


def test_snapshot_preserves_ledger_permissions_and_expired_override(store, local_checks, monkeypatch, capsys):
    store.update_settings({'live_enabled': False})
    store.db.execute('INSERT INTO model_limit_override VALUES(1,0,?)', (json.dumps({'max_model_requests_per_day': 200}),))
    root = store.root
    store.close()
    os.chmod(root / 'ledger.sqlite3', 0o400)
    before = inventory(root)
    monkeypatch.setattr('hireme.cli.Store', lambda _: pytest.fail('Read-only command instantiated Store'))
    assert main(['--data-dir', str(root), 'release-check', '--require-paused', '--expected-revision', REVISION]) == 0
    result = json.loads(capsys.readouterr().out)
    assert inventory(root) == before
    assert result['schema']['integrity_ok'] and result['schema']['matches_code']
    assert set(result['protected_history']) == set(release_check.PROTECTED_TABLES)
    assert result['protected_history']['facts']['rows'] > 0
    assert 'Test Person' not in json.dumps(result)
    assert 'candidate.invalid' not in json.dumps(result)


def test_schema_mismatch_fails_without_repair(store, local_checks):
    store.db.execute("UPDATE schema_metadata SET fingerprint='older-schema' WHERE id=1")
    report = release_check.check_release(store.root, Path('.'))
    assert not report['ready'] and 'schema_mismatch' in report['failure_codes']
    assert store.db.execute('SELECT fingerprint FROM schema_metadata').fetchone()[0] == 'older-schema'


def test_pi_release_gate_requires_an_available_kernel_memory_controller(store,local_checks,monkeypatch):
    store.update_settings({'deployment':'pi'})
    monkeypatch.setattr('hireme.pi.memory_controller_available',lambda:False)
    report=release_check.check_release(store.root,Path('.'))
    assert not report['ready'] and report['failure_codes']==['memory_controller_unavailable']
    assert report['memory_controller_available'] is False
    monkeypatch.setattr('hireme.pi.memory_controller_available',lambda:True)
    assert release_check.check_release(store.root,Path('.'))['ready']


def test_paused_revision_and_scheduler_are_explicit_gates(store, local_checks):
    report = release_check.check_release(
        store.root, Path('.'), expected_revision='b' * 40, require_paused=True, require_scheduler=True,
    )
    assert set(report['failure_codes']) == {'revision_mismatch', 'submissions_not_paused', 'scheduler_not_active'}
    assert store.settings()['live_enabled'] is True


def test_readonly_view_cannot_write_and_includes_committed_wal(store):
    view = release_check.ReadOnlyLedger(store.root)
    try:
        assert view.facts()['full_name']['value'] == 'Test Person'
        import sqlite3
        with pytest.raises(sqlite3.OperationalError):
            view.db.execute('DELETE FROM facts')
    finally:
        view.close()
    assert store.facts()['full_name']['value'] == 'Test Person'


def test_snapshot_racing_with_writer_fails_and_removes_temporary_copy(store, monkeypatch):
    original = release_check.shutil.copyfileobj
    calls = []
    def changed(source, target, length):
        original(source, target, length)
        if not calls:
            calls.append(True)
            store.put_facts({'first_name': 'Concurrent update'})
    monkeypatch.setattr(release_check.shutil, 'copyfileobj', changed)
    with pytest.raises(ValueError, match='ledger_changed_during_snapshot'):
        release_check.ReadOnlyLedger(store.root)
    assert not list(store.root.parent.glob('.hireme-release-check-*'))
    assert store.facts()['first_name']['value'] == 'Concurrent update'


def test_rollback_journal_requires_recovery_without_touching_source(store):
    (store.root / 'ledger.sqlite3-journal').write_bytes(b'private hot journal')
    before = inventory(store.root)
    with pytest.raises(ValueError, match='ledger_recovery_required'):
        release_check.ReadOnlyLedger(store.root)
    assert inventory(store.root) == before
    assert not list(store.root.parent.glob('.hireme-release-check-*'))


def test_wal_disappearing_before_copy_is_a_classified_snapshot_race(store, monkeypatch):
    wal = store.root / 'ledger.sqlite3-wal'
    assert wal.is_file()
    before = inventory(store.root)
    original = Path.open
    missing = []
    def disappeared(path, mode='r', *args, **kwargs):
        if path == wal and mode == 'rb' and not missing:
            missing.append(path)
            raise FileNotFoundError('WAL was checkpointed after generation inspection')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', disappeared)
    with pytest.raises(ValueError, match='^ledger_changed_during_snapshot$'):
        release_check.ReadOnlyLedger(store.root)
    assert missing == [wal]
    assert inventory(store.root) == before
    assert not list(store.root.parent.glob('.hireme-release-check-*'))


def test_live_worker_is_reported_without_recovery(store, local_checks):
    from hireme.store import worker_lock
    with worker_lock(store.root):
        report = release_check.check_release(store.root, Path('.'))
    assert report['worker']['running']
    assert 'worker_not_idle' in report['failure_codes']
    assert store.settings()['live_enabled'] is True


def test_changed_fact_changes_fingerprint_without_exposing_value(store, local_checks):
    first = release_check.check_release(store.root, Path('.'))
    store.put_facts({'first_name': 'Private Changed Name'})
    second = release_check.check_release(store.root, Path('.'))
    assert first['protected_history']['facts']['sha256'] != second['protected_history']['facts']['sha256']
    assert 'Private Changed Name' not in json.dumps(second)


def test_historical_document_is_checked_after_current_document_withdrawn(store, job, package, local_checks):
    store.db.execute('INSERT INTO applications VALUES(?,?,?,?,?,?,?,?,?,?,?)', (
        'application', job['id'], 'acme', 'unknown', json.dumps(package), 'package-hash',
        '2026-01-01', '2026-01-01', '2026-01-01', None, None,
    ))
    document = store.db.execute('SELECT filename FROM documents').fetchone()[0]
    store.db.execute('DELETE FROM documents')
    (store.root / 'documents' / document).write_bytes(b'changed')
    report = release_check.check_release(store.root, Path('.'))
    assert report['documents']['failed'] == 1
    assert 'documents_missing_or_changed' in report['failure_codes']
    assert report['application_states'] == {'unknown': 1}


@pytest.mark.parametrize('phase', ['before', 'after', 'verified'])
def test_release_check_accepts_recorded_screenshots_without_changing_history(store, job, package, local_checks, phase):
    aid = store.prepare(job, package)
    store.begin_submit(aid)
    name = aid + '-' + phase + '.jpg'
    directory = store.root / 'screenshots'
    directory.mkdir()
    (directory / name).write_bytes(b'synthetic screenshot')
    store.finish(aid, 'unknown', screenshot=name)
    before = inventory(store.root)
    report = release_check.check_release(store.root, Path('.'))
    assert report['ready'] and report['documents']['screenshots_checked'] == 1
    assert report['application_states'] == {'unknown': 1}
    assert inventory(store.root) == before


@pytest.mark.parametrize('damage', ['missing', 'symlink', 'directory', 'wrong_extension', 'traversal'])
def test_release_check_rejects_unsafe_or_missing_screenshots(store, job, package, local_checks, damage):
    aid = store.prepare(job, package)
    store.begin_submit(aid)
    name = aid + '-after.jpg'
    directory = store.root / 'screenshots'
    directory.mkdir()
    if damage == 'symlink':
        target = store.root / 'original.jpg'
        target.write_bytes(b'synthetic screenshot')
        (directory / name).symlink_to(target)
    elif damage == 'directory':
        directory.rmdir()
        target = store.root / 'other-screenshots'
        target.mkdir()
        (target / name).write_bytes(b'synthetic screenshot')
        directory.symlink_to(target, target_is_directory=True)
    elif damage == 'wrong_extension':
        name = aid + '-afterXjpg'
        (directory / name).write_bytes(b'synthetic screenshot')
    elif damage == 'traversal':
        name = '../' + name
        (directory / name).write_bytes(b'synthetic screenshot')
    store.finish(aid, 'unknown', screenshot=name)
    report = release_check.check_release(store.root, Path('.'))
    assert not report['ready']
    assert 'documents_missing_or_changed' in report['failure_codes']
    assert store.db.execute('SELECT state FROM applications').fetchone()[0] == 'unknown'


def test_duplicate_or_wrong_owner_schedulers_fail_closed(store, monkeypatch):
    from hireme import scheduler
    monkeypatch.setattr(release_check.shutil, 'which', lambda _: '/fixture/tool')
    monkeypatch.setattr(scheduler, 'status', lambda _, cron_only=False: {
        'installed': True, 'matches_applicant': not cron_only, 'kind': 'cron' if cron_only else 'systemd',
        'enabled': True, 'interval_hours': 6,
    })
    monkeypatch.setattr(release_check.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='active\n'))
    view = release_check.ReadOnlyLedger(store.root)
    try:
        result = release_check._scheduler(view)
    finally:
        view.close()
    assert result['installed_count'] == 2 and not result['matches_applicant'] and not result['ready']


def test_duplicate_cron_schedules_fail_release_gate_even_for_the_same_applicant(store, monkeypatch):
    import shlex
    from hireme import scheduler
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'linux')
    monkeypatch.setattr(release_check.shutil, 'which', lambda name: '/usr/bin/crontab' if name == 'crontab' else None)
    command = shlex.join(['python', '-m', 'hireme', '--data-dir', str(store.root), 'run'])
    line = '0 */6 * * * ' + command + ' # ' + scheduler.LABEL + '\n'
    monkeypatch.setattr(release_check.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=line * 2, stderr=''))
    view = release_check.ReadOnlyLedger(store.root)
    try:
        result = release_check._scheduler(view)
    finally:
        view.close()
    assert result['installed_count'] == 2 and result['matches_applicant'] and not result['ready']


@pytest.mark.parametrize('changed', [None, 'applicant', 'interval', 'directory', 'environment', 'extra_connection', 'unreadable'])
def test_launchd_release_check_verifies_loaded_job_not_only_saved_plist(store, tmp_path, monkeypatch, changed):
    import plistlib
    from hireme import scheduler
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'darwin')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    path=tmp_path/'Library/LaunchAgents'/f'{scheduler.LABEL}.plist'
    path.parent.mkdir(parents=True)
    arguments=['/fixture/python','-m','hireme','--data-dir',str(store.root),'run']
    saved={'Label':scheduler.LABEL,'ProgramArguments':arguments,'WorkingDirectory':'/fixture/repo',
           'EnvironmentVariables':{'PATH':'/fixture/bin','CLAUDE_CONFIG_DIR':'/fixture/claude'},
           'StartInterval':store.settings()['schedule_hours']*3600}
    path.write_bytes(plistlib.dumps(saved))
    loaded=list(arguments)
    if changed=='applicant':loaded[4]=str(tmp_path/'other-applicant')
    directory='/fixture/old-repo' if changed=='directory' else saved['WorkingDirectory']
    interval=1800 if changed=='interval' else saved['StartInterval']
    claude='/fixture/old-claude' if changed=='environment' else '/fixture/claude'
    extra='\t\tCODEX_HOME => /fixture/old-codex\n' if changed=='extra_connection' else ''
    output=('arguments = {\n'+''.join('\t\t'+arg+'\n' for arg in loaded)
            +'\t}\n\tworking directory = '+directory+'\n\trun interval = '+str(interval)+' seconds\n'
            +'\tenvironment = {\n\t\tPATH => /fixture/bin\n\t\tCLAUDE_CONFIG_DIR => '+claude+'\n'+extra
            +'\t\tXPC_SERVICE_NAME => '+scheduler.LABEL+'\n\t}\n')
    if changed=='unreadable':output='unexpected launchctl output'
    monkeypatch.setattr(release_check.subprocess,'run',lambda args,**kw:SimpleNamespace(
        returncode=0,stdout='disabled services = {\n}\n' if 'print-disabled' in args else output))
    view=release_check.ReadOnlyLedger(store.root)
    try:result=release_check._scheduler(view)
    finally:view.close()
    assert result['active'] and result['enabled'] and result['matches_applicant']
    assert result['loaded_matches_saved'] is (changed is None)
    assert result['ready'] is (changed is None)


@pytest.mark.parametrize('override,default,available,expected', [
    ('enabled',False,True,True),('disabled',False,True,False),
    (None,False,True,True),(None,True,True,False),('enabled',True,True,True),
    ('malformed',False,True,False),('duplicate',False,True,False),
    (None,False,False,False),
])
def test_launchd_release_check_requires_persistent_enabled_state(store,tmp_path,monkeypatch,override,default,available,expected):
    import plistlib
    from hireme import scheduler
    monkeypatch.setattr('hireme.scheduler.sys.platform','darwin')
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    path=tmp_path/'Library/LaunchAgents'/f'{scheduler.LABEL}.plist'
    path.parent.mkdir(parents=True)
    arguments=['/fixture/python','-m','hireme','--data-dir',str(store.root),'run']
    interval=store.settings()['schedule_hours']*3600
    path.write_bytes(plistlib.dumps({'Label':scheduler.LABEL,'ProgramArguments':arguments,
        'WorkingDirectory':'/fixture/repo','StartInterval':interval,'Disabled':default,
        'EnvironmentVariables':{'PATH':'/fixture/bin'}}))
    output=('arguments = {\n'+''.join('\t'+arg+'\n' for arg in arguments)
            +'}\nworking directory = /fixture/repo\nrun interval = '+str(interval)+' seconds\n'
            +'environment = {\n PATH => /fixture/bin\n}\n')
    if override=='malformed':disabled='unreadable launchctl output'
    else:
        entry=f'"{scheduler.LABEL}" => {override}\n' if override else ''
        if override=='duplicate':entry=f'"{scheduler.LABEL}" => enabled\n"{scheduler.LABEL}" => disabled\n'
        disabled='disabled services = {\n'+entry+'}\n'
    def run(args,**kwargs):
        if 'print-disabled' in args:return SimpleNamespace(returncode=0 if available else 1,stdout=disabled)
        return SimpleNamespace(returncode=0,stdout=output)
    monkeypatch.setattr(release_check.subprocess,'run',run)
    view=release_check.ReadOnlyLedger(store.root)
    try:result=release_check._scheduler(view)
    finally:view.close()
    assert result['active'] and result['loaded_matches_saved']
    assert result['enabled'] is expected and result['ready'] is expected


def test_scheduler_status_never_creates_a_missing_daemon_lock(store, monkeypatch):
    from hireme import scheduler
    monkeypatch.setattr(scheduler, 'status', lambda _: {'installed': False})
    monkeypatch.setattr(release_check.shutil, 'which', lambda _: None)
    before = inventory(store.root)
    view = release_check.ReadOnlyLedger(store.root)
    try:
        result = release_check._scheduler(view)
    finally:
        view.close()
    assert result['ready'] and result['daemon_running'] is False
    assert not (store.root / 'scheduler.lock').exists()
    assert inventory(store.root) == before


def test_active_timer_and_sleeping_daemon_fail_readiness_without_changing_files(store, monkeypatch):
    import threading
    from hireme import scheduler, worker
    store.update_settings({'live_enabled': False})
    settings = store.settings()
    daemon_store = SimpleNamespace(root=store.root, settings=lambda: settings)
    sleeping = threading.Event()
    stop = threading.Event()
    errors = []
    class StopDaemon(BaseException):
        pass
    def sleep(_):
        sleeping.set()
        if not stop.wait(10):
            raise RuntimeError('Test daemon was not stopped')
        raise StopDaemon()
    def run():
        try:
            worker.daemon(daemon_store, Path('.'))
        except StopDaemon:
            pass
        except BaseException as error:
            errors.append(error)
    monkeypatch.setattr(worker, 'cycle', lambda *_: None)
    monkeypatch.setattr(worker, 'time', SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(release_check, '_revision', lambda _: {'revision': REVISION, 'dirty': False, 'available': True})
    monkeypatch.setattr(release_check, '_integrations', lambda *_: {'ready': True})
    monkeypatch.setattr(release_check.shutil, 'which', lambda _: '/fixture/tool')
    monkeypatch.setattr(scheduler, 'status', lambda _, cron_only=False: {'installed': False} if cron_only else {
        'installed': True, 'matches_applicant': True, 'kind': 'systemd',
        'enabled': True, 'interval_hours': store.settings()['schedule_hours'],
    })
    monkeypatch.setattr(release_check.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='active\n'))
    daemon = threading.Thread(target=run)
    daemon.start()
    try:
        assert sleeping.wait(5)
        before = inventory(store.root)
        report = release_check.check_release(store.root, Path('.'), require_paused=True, require_scheduler=True)
        assert report['worker']['running'] is False
        assert report['scheduler']['installed_count'] == 1 and report['scheduler']['active'] is True
        assert report['scheduler']['daemon_running'] is True
        assert report['failure_codes'] == ['scheduler_unsafe_or_unavailable']
        assert not report['ready']
        assert inventory(store.root) == before
    finally:
        stop.set()
        daemon.join(5)
    assert not daemon.is_alive() and not errors
    view = release_check.ReadOnlyLedger(store.root)
    try:
        assert release_check._scheduler(view)['ready']
    finally:
        view.close()


def test_integration_inspection_never_prints_secrets_or_claims_access(store, monkeypatch):
    store.update_settings({'provider': 'anthropic-api', 'provider_model': 'configured', 'gmail_reports': True})
    directory = store.root / 'integrations'
    directory.mkdir()
    secret = 'private-key-do-not-print-123456789'
    (directory / 'provider-key.json').write_text(json.dumps({'provider': 'anthropic-api', 'key': secret}))
    (directory / 'gmail-account.json').write_text(json.dumps({'email': 'test@candidate.invalid'}))
    (directory / 'gmail-token.json').write_text('private-oauth-token')
    monkeypatch.setattr(release_check.subprocess, 'run', lambda *a, **kw: pytest.fail('Integration status spawned a process'))
    view = release_check.ReadOnlyLedger(store.root)
    try:
        result = release_check._integrations(view, view.settings())
    finally:
        view.close()
    assert result['ready'] and result['gmail']['owner_bound_configuration']
    assert result['gmail']['api_access'] == 'not_checked'
    assert result['model']['login_and_connectivity'] == 'not_checked'
    assert all(value not in json.dumps(result) for value in (secret, 'test@candidate.invalid', 'private-oauth-token'))


def test_cli_evidence_output_is_private_and_never_overwritten(store, tmp_path, local_checks, capsys):
    output = tmp_path / 'private-report.json'
    args = ['--data-dir', str(store.root), 'release-check', '--output', str(output)]
    assert main(args) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    original = output.read_bytes()
    assert json.loads(original)['ready']
    assert main(args) == 2
    assert output.read_bytes() == original
    assert 'could not be saved' in capsys.readouterr().err


def test_workday_status_uses_only_readonly_view(store, monkeypatch, capsys):
    monkeypatch.setattr('hireme.cli.Store', lambda _: pytest.fail('Status instantiated Store'))
    before = inventory(store.root)
    assert main(['--data-dir', str(store.root), 'workday', 'status']) == 0
    tenants = json.loads(capsys.readouterr().out)['tenants']
    assert len(tenants) == 5 and all(not item['enabled'] for item in tenants)
    assert not (store.root / 'browser').exists()
    assert inventory(store.root) == before


@pytest.mark.parametrize('action', ['commission', 'reconcile'])
def test_workday_cli_dispatches_one_exact_job_to_guarded_wrapper(store, monkeypatch, capsys, action):
    import hireme.workday
    calls = []
    def operation(selected, repo, job_id):
        calls.append((selected.root, job_id))
        return {'job_id': job_id, 'action': action}
    other = 'reconcile' if action == 'commission' else 'commission'
    monkeypatch.setattr(hireme.workday, other, lambda *args: pytest.fail('Dispatched wrong Workday operation'), raising=False)
    monkeypatch.setattr(hireme.workday, action, operation, raising=False)
    assert main(['--data-dir', str(store.root), 'workday', action, '--job-id', 'exact-job', '--json']) == 0
    assert calls == [(store.root, 'exact-job')]
    assert json.loads(capsys.readouterr().out) == {'job_id': 'exact-job', 'action': action}


def test_workday_commission_requires_job_before_opening_store(monkeypatch):
    monkeypatch.setattr('hireme.cli.Store', lambda _: pytest.fail('Missing job initialized Store'))
    with pytest.raises(SystemExit) as error:
        main(['workday', 'commission'])
    assert error.value.code == 2


def test_workday_status_filters_exact_tenant_with_json_flag(store, monkeypatch, capsys):
    monkeypatch.setattr('hireme.cli.Store', lambda _: pytest.fail('Status instantiated Store'))
    assert main(['--data-dir', str(store.root), 'workday', 'status', '--tenant', 'nvidia', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result['tenants']) == 1
    assert result['tenants'][0]['host'] == 'nvidia.wd5.myworkdayjobs.com'
