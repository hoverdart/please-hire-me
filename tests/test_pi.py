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
    assert '127.0.0.1:8766/' in message and 'open-dashboard --port 8766' in message
    assert dashboard_startup_message(url)==f'Dashboard: {url}'


def test_dashboard_reopen_command_preserves_runtime_applicant_and_custom_port(tmp_path,monkeypatch):
    import shlex
    executable=tmp_path/'runtime with spaces/bin/python'
    root=tmp_path/'private applicant'
    monkeypatch.setattr('hireme.server.sys.executable',str(executable))
    message=dashboard_startup_message('http://127.0.0.1:55110/#token=fixture-secret-token',True,root)
    command=shlex.split(message.split('. Run: ',1)[1])
    assert command==[str(executable),'-I','-m','hireme','--data-dir',str(root.resolve()),'open-dashboard','--port','55110']
    assert 'fixture-secret-token' not in message and 'Pi desktop' not in message


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


def test_unknown_firmware_flags_are_not_reported_as_healthy(monkeypatch):
    from hireme import pi
    from types import SimpleNamespace
    monkeypatch.setattr(pi.shutil,'which',lambda _:'/synthetic/vcgencmd')
    monkeypatch.setattr(pi.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='throttled=0x100000'))
    assert pi.power_status()=={'available':False}


def test_dashboard_reopen_runtime_works_outside_the_checkout(tmp_path):
    import shlex,subprocess
    root=tmp_path/'private-applicant'
    message=dashboard_startup_message('http://127.0.0.1:55110/#token=fixture-secret-token',True,root)
    command=shlex.split(message.split('. Run: ',1)[1])
    command=command[:command.index('open-dashboard')]+['--help']
    unrelated=tmp_path/'unrelated';unrelated.mkdir()
    result=subprocess.run(command,cwd=unrelated,capture_output=True,text=True,timeout=15,check=True)
    assert '--data-dir' in result.stdout and not root.exists()
