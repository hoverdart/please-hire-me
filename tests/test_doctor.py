import json

import pytest

from hireme.doctor import diagnose, format_report
from hireme.cli import main


def readiness(ready=True):
    return {'supported_platform': True, 'platform': 'linux', 'architecture': 'aarch64', 'python': '3.11',
            'browser_ready': ready, 'provider': {'ready': ready, 'message': 'Fixture subscription status'},
            'missing': [] if ready else ['resume', 'work_authorized_us']}


def test_doctor_reports_next_steps_without_changing_worker_or_request_budget(store, monkeypatch):
    monkeypatch.setattr('hireme.doctor.readiness', lambda s, verify=False: readiness(False))
    settings = store.settings(); generation = store.control_generation()
    result = diagnose(store)
    assert not result['ready']
    text = format_report(result)
    assert 'python -m playwright install chromium' in text
    assert 'Currently authorized to work in the US' in text
    assert 'No model requests or applications were sent' in text
    assert store.settings() == settings and store.control_generation() == generation
    assert not store.db.execute('SELECT * FROM model_requests').fetchone()


def test_doctor_cli_json_and_verification_flag(store, monkeypatch, capsys):
    seen = []
    def checked(s, verify=False): seen.append(verify); return readiness()
    monkeypatch.setattr('hireme.doctor.readiness', checked)
    assert main(['--data-dir', str(store.root), 'doctor', '--verify-login', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ready'] and result['verified_login'] and seen == [True]


def test_doctor_explains_interrupted_work_without_recovering_it(store, monkeypatch):
    monkeypatch.setattr('hireme.doctor.readiness', lambda s, verify=False: readiness())
    store.db.execute("INSERT INTO runs(id,started,status) VALUES('stale-run','2020-01-01T00:00:00+00:00','running')")
    result = diagnose(store)
    assert not result['ready'] and 'hireme recover' in format_report(result)
    assert store.db.execute("SELECT status FROM runs WHERE id='stale-run'").fetchone()[0] == 'running'


@pytest.mark.parametrize('port', ['0', '-1', '65536', 'abc'])
def test_dashboard_rejects_invalid_ports_before_opening_storage(port, tmp_path):
    root = tmp_path / 'untouched'
    with pytest.raises(SystemExit) as error:
        main(['--data-dir', str(root), 'dashboard', '--port', port])
    assert error.value.code == 2 and not root.exists()


def test_dashboard_port_collision_has_actionable_message(store, monkeypatch, capsys):
    def occupied(*args, **kwargs): raise OSError(48, 'Address already in use')
    monkeypatch.setattr('hireme.server.serve', occupied)
    assert main(['--data-dir', str(store.root), 'dashboard']) == 2
    assert 'Choose another port with --port' in capsys.readouterr().err


def test_dashboard_diagnostics_refreshes_browser_without_login_or_writes(store, monkeypatch):
    from hireme.doctor import dashboard_diagnostics
    seen = []
    monkeypatch.setattr('hireme.setup_status.browser_available', lambda channel, force=False: seen.append((channel, force)) or True)
    monkeypatch.setattr('hireme.doctor.readiness', lambda s, verify=False: readiness(False))
    store.put_facts({'full_name': 'Private Synthetic Name', 'email': 'private@candidate.invalid'})
    before = store.snapshot(); changes = store.db.total_changes
    result = dashboard_diagnostics(store)
    assert seen == [('chromium', True)]
    assert result['checked_at'] and not result['verified_login']
    assert 'Private Synthetic Name' not in result['report']
    assert 'private@candidate.invalid' not in result['report']
    assert str(store.root) not in result['report']
    assert 'Currently authorized to work in the US' in result['report']
    assert store.snapshot() == before and store.db.total_changes == changes
    assert not store.db.execute('SELECT * FROM model_requests').fetchone()


def test_demo_diagnostics_uses_sample_readiness_without_machine_checks(store, monkeypatch):
    from hireme.doctor import dashboard_diagnostics
    def forbidden(*args, **kwargs): raise AssertionError('Demo inspected real setup')
    monkeypatch.setattr('hireme.doctor.readiness', forbidden)
    monkeypatch.setattr('hireme.setup_status.browser_available', forbidden)
    result = dashboard_diagnostics(store, demo=True)
    assert result['demo'] and 'Sample workspace' in result['report']
    assert 'No model connected in this preview.' in result['report']
