from types import SimpleNamespace

import pytest

from hireme.job_holds import category, hold, ready, recheck
from hireme.util import digest
from hireme.workday import WriteGrant
from hireme.workday_profiles import NVIDIA
from hireme.workday_state import CorrelatedStep, DraftObservation
from tests.test_workday_state import wd_job, journal


def test_confirmed_history_revision_releases_an_information_hold(store, wd_job):
    hold(store, wd_job, 'history_confirmation_required')
    assert category('history_confirmation_required') == 'information'
    assert not ready(store, wd_job)
    store.put_history_entry('education', {'institution': 'Confirmed University'}, source='Applicant', confirmed=True)
    assert ready(store, wd_job)
    assert recheck(store, wd_job['id'])['ready']
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('change', ['version', 'profile'])
def test_protocol_hold_tracks_adapter_evidence_not_unrelated_facts(store, wd_job, monkeypatch, change):
    hold(store, wd_job, 'workday_transport_unverified')
    store.put_facts({'phone': '5557654321'})
    assert not ready(store, wd_job)
    if change == 'version':
        import hireme.workday
        monkeypatch.setattr(hireme.workday, 'VERSION', hireme.workday.VERSION + 1)
    else:
        from hireme.workday_profiles import REVIEWED_TRANSPORTS
        monkeypatch.setitem(REVIEWED_TRANSPORTS, NVIDIA, SimpleNamespace(signature=digest('new-reviewed-profile')))
    assert ready(store, wd_job)


def test_session_recheck_only_requests_fresh_read_without_manufacturing_authority(store, wd_job):
    hold(store, wd_job, 'workday_session_unverified')
    store.put_facts({'phone': '5557654321'})
    assert not ready(store, wd_job)
    assert recheck(store, wd_job['id'])['ready']
    assert store.db.execute("SELECT 1 FROM events WHERE kind='workday_session_recheck_requested'").fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not (store.root / 'browser' / 'workday-binding.json').exists()


def uncertain_step(store, wd_job):
    saved = journal(store, wd_job)
    store.update_settings({'remote_drafts': True})
    grant = WriteGrant(store, wd_job, 'My Information', f'https://{NVIDIA}/fixture/save', 'POST', b'approved-fixture')
    observation = DraftObservation('remote-fixture', 'revision-2', saved.session.owner_hash, wd_job['id'], digest('approved'))
    operation = CorrelatedStep(saved, grant, observation.values_hash, lambda _: observation)
    assert operation.consume(grant.url, grant.method, grant.payload)
    return operation


@pytest.mark.parametrize('held_reason', ['remote_draft_uncertain', 'missing_answers'])
def test_uncertain_step_never_retries_after_facts_or_history_change(store, wd_job, held_reason):
    uncertain_step(store, wd_job)
    hold(store, wd_job, held_reason)
    store.put_facts({'phone': '5557654321'})
    store.put_history_entry('education', {'institution': 'Confirmed University'}, source='Applicant', confirmed=True)
    assert not ready(store, wd_job)
    assert recheck(store, wd_job['id']) == {'ready': False, 'reason': 'remote_draft_uncertain'}
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0] == 'intent'
    assert store.db.execute('SELECT state FROM workday_drafts').fetchone()[0] == 'unknown'


def test_correlated_draft_readback_releases_only_the_existing_draft_hold(store, wd_job):
    operation = uncertain_step(store, wd_job)
    hold(store, wd_job, 'remote_draft_uncertain')
    assert not ready(store, wd_job)
    grant = operation.grant
    response = SimpleNamespace(request=SimpleNamespace(url=grant.url, method=grant.method, post_data_buffer=grant.payload),
                               status=200, json=lambda: {'fixture': 'native-ack'})
    assert operation.observe_response(response)
    assert ready(store, wd_job)
    assert recheck(store, wd_job['id'])['ready']
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_draft_reconciliation_cannot_release_a_final_unknown_attempt(store, wd_job, package):
    operation = uncertain_step(store, wd_job)
    hold(store, wd_job, 'remote_draft_uncertain')
    grant = operation.grant
    response = SimpleNamespace(request=SimpleNamespace(url=grant.url, method=grant.method, post_data_buffer=grant.payload),
                               status=200, json=lambda: {'fixture': 'native-ack'})
    assert operation.observe_response(response)
    package = {**package, 'job_id': wd_job['id'], 'url': wd_job['url']}
    application_id = store.prepare(wd_job, package)
    store.begin_submit(application_id)
    store.finish(application_id, 'unknown')
    assert not ready(store, wd_job)
    with pytest.raises(ValueError, match='existing submission outcome'):
        recheck(store, wd_job['id'])
