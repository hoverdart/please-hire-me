from types import SimpleNamespace
from pathlib import Path
import os
import plistlib
from hireme.scheduler import install,LABEL
from hireme.scheduler import status, apply_saved_interval
from hireme.store import Store, worker_lock
from hireme.util import Blocked
import pytest


def test_mac_schedule_excludes_api_key_from_private_plist(store,tmp_path,monkeypatch):
    monkeypatch.setattr('hireme.scheduler.sys.platform','darwin')
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    monkeypatch.setenv('ANTHROPIC_API_KEY','synthetic-key')
    monkeypatch.setenv('UNRELATED_SECRET','must-not-forward')
    calls=[]
    def run(args,**kwargs):calls.append(args);return SimpleNamespace(returncode=0)
    monkeypatch.setattr('hireme.scheduler.subprocess.run',run)
    path=Path(install(store,tmp_path/'repo with spaces'))
    data=plistlib.loads(path.read_bytes())
    assert data['Label']==LABEL
    assert 'ANTHROPIC_API_KEY' not in data['EnvironmentVariables']
    assert 'CLAUDE_CODE_OAUTH_TOKEN' not in data['EnvironmentVariables']
    assert 'UNRELATED_SECRET' not in data['EnvironmentVariables']
    assert 'synthetic-key' not in ' '.join(data['ProgramArguments'])
    assert 'synthetic-key' not in ' '.join(' '.join(c) for c in calls)
    assert path.stat().st_mode & 0o777==0o600


def test_schedule_interval_can_be_applied_without_resuming_or_running(store, tmp_path, monkeypatch):
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'darwin')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('hireme.scheduler.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.chdir(tmp_path)
    store.root = Path('private')
    store.update_settings({'live_enabled': False, 'schedule_hours': 6})
    apply_saved_interval(store, tmp_path / 'repo')
    assert not store.settings()['live_enabled']
    assert not store.db.execute('SELECT * FROM runs').fetchone()
    assert status(store)['matches_applicant'] and status(store)['interval_hours'] == 6
    saved = plistlib.loads((tmp_path / 'Library/LaunchAgents' / (LABEL + '.plist')).read_bytes())
    assert Path(saved['ProgramArguments'][4]).is_absolute()
    store.update_settings({'schedule_hours': 3})
    assert status(store)['interval_hours'] == 6
    apply_saved_interval(store, tmp_path / 'repo')
    assert status(store)['interval_hours'] == 3
    other = Store(tmp_path / 'different-applicant')
    try:
        assert status(other)['installed'] and not status(other)['matches_applicant']
        with pytest.raises(ValueError, match='another applicant'): apply_saved_interval(other, tmp_path / 'repo')
        with pytest.raises(ValueError, match='another applicant'): install(other, tmp_path / 'repo')
        from hireme.scheduler import uninstall
        with pytest.raises(ValueError, match='another applicant'): uninstall(store=other)
        assert status(store)['matches_applicant'] and status(store)['interval_hours'] == 3
    finally: other.close()


def test_running_worker_blocks_schedule_changes(store, tmp_path):
    with worker_lock(store.root):
        with pytest.raises(Blocked, match='worker_busy'): apply_saved_interval(store, tmp_path)


def test_cron_status_identifies_applicant_with_spaces_and_percent(store, tmp_path, monkeypatch):
    import shlex
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'linux')
    monkeypatch.setattr('hireme.scheduler.shutil.which', lambda binary: '/usr/bin/crontab' if binary == 'crontab' else None)
    command = shlex.join(['python', '-m', 'hireme', '--data-dir', str(store.root), 'run']).replace('%', r'\%')
    line = '0 */4 * * * cd ' + shlex.quote(str(tmp_path / 'repo with % spaces')) + ' && ' + command + ' # ' + LABEL
    monkeypatch.setattr('hireme.scheduler.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=line))
    result = status(store)
    assert result['installed'] and result['matches_applicant'] and result['interval_hours'] == 4


def test_systemd_status_reads_interval_and_applicant_from_generated_units(store, tmp_path, monkeypatch):
    from hireme.pi import service_units
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'linux')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('hireme.scheduler.shutil.which', lambda binary: '/usr/bin/systemctl' if binary == 'systemctl' else None)
    monkeypatch.setattr('hireme.scheduler.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=0))
    directory = tmp_path / '.config/systemd/user'; directory.mkdir(parents=True)
    for name, content in service_units(store, tmp_path / 'repo with % spaces').items(): (directory / name).write_text(content)
    result = status(store)
    assert result['installed'] and result['matches_applicant']
    assert result['interval_hours'] == store.settings()['schedule_hours']


def test_pi_schedule_update_activates_worker_timer_without_starting_dashboard(store, tmp_path, monkeypatch):
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'linux')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('hireme.scheduler.shutil.which', lambda binary: '/usr/bin/systemctl' if binary == 'systemctl' else None)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if 'is-enabled' in args else 0)
    monkeypatch.setattr('hireme.scheduler.subprocess.run', run)
    store.update_settings({'deployment': 'pi', 'live_enabled': False})
    apply_saved_interval(store, tmp_path / 'repo')
    assert ['systemctl', '--user', 'enable', '--now', 'please-hire-me-worker.timer'] in calls
    assert not any('please-hire-me-dashboard.service' in call for call in calls)
    assert not store.settings()['live_enabled']


def test_disabled_systemd_schedule_still_protects_its_applicant(store, tmp_path, monkeypatch):
    from hireme.pi import service_units
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'linux')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr('hireme.scheduler.shutil.which', lambda binary: '/usr/bin/systemctl' if binary == 'systemctl' else None)
    monkeypatch.setattr('hireme.scheduler.subprocess.run', lambda *a, **kw: SimpleNamespace(returncode=1))
    directory = tmp_path / '.config/systemd/user'; directory.mkdir(parents=True)
    for name, content in service_units(store, tmp_path).items(): (directory / name).write_text(content)
    other = Store(tmp_path / 'other')
    try:
        assert status(store)['installed'] and not status(store)['enabled']
        with pytest.raises(ValueError, match='another applicant'): install(other, tmp_path)
    finally: other.close()


def test_unresponsive_scheduler_is_reported_without_changing_settings(store, monkeypatch):
    import subprocess
    monkeypatch.setattr('hireme.scheduler.sys.platform', 'linux')
    monkeypatch.setattr('hireme.scheduler.shutil.which', lambda binary: '/usr/bin/systemctl' if binary == 'systemctl' else None)
    def stalled(args, **kwargs):
        assert kwargs['timeout'] == 5
        raise subprocess.TimeoutExpired(args, 5)
    monkeypatch.setattr('hireme.scheduler.subprocess.run', stalled)
    previous = store.settings()
    with pytest.raises(ValueError, match='could not be checked'): apply_saved_interval(store, store.root)
    assert store.settings() == previous
