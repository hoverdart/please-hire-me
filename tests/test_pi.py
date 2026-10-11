from pathlib import Path
from hireme.pi import service_units,install,configure


def test_pi_units_are_owner_local_quoted_and_include_no_credentials(store,tmp_path):
    units=service_units(store,Path('/repo with spaces/100% done'),'/venv with spaces/python')
    worker=units['please-hire-me-worker.service']
    assert '100%% done' in worker and '"/venv with spaces/python"' in worker
    assert 'WorkingDirectory=/repo with spaces/100%% done\n' in worker
    assert 'Type=oneshot' in worker and 'UMask=0077' in worker
    assert not any(line.startswith('Environment=') and ('API_KEY' in line or 'OAUTH_TOKEN' in line) for line in worker.splitlines()) and 'client_secret' not in worker and 'User=root' not in worker
    assert 'UnsetEnvironment=ANTHROPIC_API_KEY' in worker
    assert 'OnUnitInactiveSec=6h' in units['please-hire-me-worker.timer']
    result=install(store,Path('/repo'),tmp_path/'units',enable=False)
    assert not result['enabled'] and len(list((tmp_path/'units').iterdir()))==3


def test_pi_configuration_preserves_identity_and_limits_and_pauses(store):
    facts=store.facts();limit=store.settings()['max_per_day']
    configure(store)
    assert store.facts()==facts and store.settings()['max_per_day']==limit
    assert not store.settings()['live_enabled'] and store.settings()['headless']
    assert store.settings()['browser_channel']=='system-chromium'
