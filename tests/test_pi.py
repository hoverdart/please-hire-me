from pathlib import Path
from hireme.pi import service_units,install,configure
from hireme.server import dashboard_startup_message
import pytest


def test_pi_units_are_owner_local_quoted_and_include_no_credentials(store,tmp_path):
    units=service_units(store,Path('/repo with spaces/100% done'),'/venv with spaces/python')
    worker=units['please-hire-me-worker.service']
    assert '100%% done' in worker and '"/venv with spaces/python"' in worker
    assert 'WorkingDirectory=/repo with spaces/100%% done\n' in worker
    assert 'Type=oneshot' in worker and 'UMask=0077' in worker
    assert not any(line.startswith('Environment=') and ('API_KEY' in line or 'OAUTH_TOKEN' in line) for line in worker.splitlines()) and 'client_secret' not in worker and 'User=root' not in worker
    assert 'UnsetEnvironment=ANTHROPIC_API_KEY' in worker
    assert 'OnUnitInactiveSec=6h' in units['please-hire-me-worker.timer']
    assert 'dashboard --hide-token' in units['please-hire-me-dashboard.service']
    result=install(store,Path('/repo'),tmp_path/'units',enable=False)
    assert not result['enabled'] and len(list((tmp_path/'units').iterdir()))==3


def test_pi_configuration_preserves_identity_and_limits_and_pauses(store):
    facts=store.facts();limit=store.settings()['max_per_day']
    configure(store)
    assert store.facts()==facts and store.settings()['max_per_day']==limit
    assert not store.settings()['live_enabled'] and store.settings()['headless']
    assert store.settings()['browser_channel']=='system-chromium'


def test_dashboard_service_startup_message_does_not_log_capability():
    url='http://127.0.0.1:8766/#token=fixture-secret-token'
    message=dashboard_startup_message(url,hide_token=True)
    assert 'fixture-secret-token' not in message
    assert '127.0.0.1:8766/' in message and '.venv/bin/python -m hireme open-dashboard' in message
    assert dashboard_startup_message(url)==f'Dashboard: {url}'


@pytest.mark.parametrize('controllers,available', [('cpuset cpu io pids',False),('cpuset cpu io memory pids',True)])
def test_pi_preflight_detects_memory_controller_from_v2_instead_of_proc_cgroups(store,monkeypatch,controllers,available):
    from hireme import pi
    original=Path.read_text
    def read(path,*args,**kwargs):
        if str(path)=='/sys/fs/cgroup/cgroup.controllers':return controllers
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    assert pi.preflight(store)['memory_controller_available'] is available


def test_unreadable_cgroup_controller_is_not_reported_as_enforced(monkeypatch):
    from hireme.pi import memory_controller_available
    def denied(*args,**kwargs):raise PermissionError('synthetic controller unavailable')
    monkeypatch.setattr(Path,'read_text',denied)
    assert not memory_controller_available()
