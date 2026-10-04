import json

import pytest

from hireme.answers import resolve
from hireme.util import Blocked


def test_clear_optional_fact_revokes_bound_answer_and_preserves_revision(store, job):
    store.put_facts({'preferred_name': 'Old nickname'})
    question = store.ask(job['id'], job['host'], 'What should we call you?', [])
    store.answer_question(question, 'Old nickname', 'preferred_name')
    field = {'label': 'What should we call you?', 'type': 'text', 'required': True, 'options': [], 'maxlength': -1}
    assert resolve(store, job['host'], field)['value'] == 'Old nickname'
    revision = store.facts()['preferred_name']['revision']
    store.put_facts({}, clear_keys=['preferred_name'])
    assert 'preferred_name' not in store.facts()
    assert store.facts(False)['preferred_name']['revision'] == revision + 1
    assert store.settings()['live_enabled']
    with pytest.raises(Blocked, match='stale_answer'): resolve(store, job['host'], field)
    assert 'preferred_name' not in json.loads((store.root / 'config/profile.json').read_text())
    store.put_facts({'preferred_name': 'New nickname'})
    assert store.facts()['preferred_name']['revision'] == revision + 2


def test_required_fact_revocation_cancels_worker_and_invalidates_prepared_packages(store, job, package):
    store.prepare(job, package)
    generation = store.control_generation(); store.run_generation = generation
    store.put_facts({}, clear_keys=['work_authorized_us'])
    assert not store.settings()['live_enabled'] and store.control_generation() == generation + 1
    assert 'work_authorized_us' in store.missing_setup()
    assert not store.db.execute("SELECT * FROM applications WHERE state='prepared'").fetchone()
    with pytest.raises(Blocked, match='paused'): store.checkpoint()


def test_email_identity_cannot_be_removed_or_replaced_through_revocation(store, job, package):
    store.prepare(job, package)
    original = store.facts()['email']['value']
    with pytest.raises(ValueError, match='identity cannot be cleared'):
        store.put_facts({'preferred_name': 'Do not save'}, clear_keys=['email'])
    assert store.facts()['email']['value'] == original and 'preferred_name' not in store.facts()
    assert store.db.execute('SELECT * FROM applications').fetchone()


@pytest.mark.parametrize('cleared', [['not_a_fact'], 'phone', [None], ['phone', {}]])
def test_invalid_revocations_are_atomic(store, cleared):
    before = store.facts()
    with pytest.raises(ValueError): store.put_facts({'preferred_name': 'Do not save'}, clear_keys=cleared)
    assert store.facts() == before


def test_clear_and_save_same_fact_is_rejected(store):
    before = store.facts()
    with pytest.raises(ValueError): store.put_facts({'phone': '5551230000'}, clear_keys=['phone'])
    assert store.facts() == before


def test_invalid_new_fact_does_not_revoke_an_existing_value(store):
    store.put_facts({'preferred_name': 'Retain this'})
    with pytest.raises(ValueError): store.put_facts({'work_authorized_us': 'maybe'}, clear_keys=['preferred_name'])
    assert store.facts()['preferred_name']['value'] == 'Retain this'


def test_required_fact_revocation_preserves_uncertain_application_history(store, job, package):
    application = store.prepare(job, package)
    store.begin_submit(application)
    store.finish(application, 'unknown', 'No definitive receipt', None)
    store.put_facts({}, clear_keys=['phone'])
    row = store.db.execute('SELECT * FROM applications WHERE id=?', (application,)).fetchone()
    assert row['state'] == 'unknown' and row['confirmation'] == 'No definitive receipt'
    assert not store.settings()['live_enabled']
