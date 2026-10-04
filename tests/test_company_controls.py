import pytest

from hireme.company_controls import allow_company, skip_company
from hireme.ledger import search_jobs
from hireme.util import Blocked, digest


def test_skip_company_respects_aliases_and_clears_only_unattempted_work(store, job, package):
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'}})
    second = {**job, 'id': digest('second-company-role'), 'url': job['url'] + '-second', 'company': 'ACME Technologies'}
    store.upsert_job(second)
    qid = store.ask(second['id'], second['host'], 'Future application question', [])
    aid = store.prepare(job, package)
    generation = store.control_generation()
    skip_company(store, job['id'])
    assert store.settings()['skip_companies'] == ['Acme']
    assert store.control_generation() == generation and store.settings()['live_enabled']
    assert not store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?', (qid,)).fetchone()[0] == 1
    jobs = search_jobs(store)['jobs']
    assert all(row['company_skipped'] and row['reason'] == 'company_blocked' for row in jobs)
    with pytest.raises(Blocked, match='company_blocked'): store.prepare(job, package)
    skip_company(store, second['id'])
    assert store.settings()['skip_companies'] == ['Acme']


@pytest.mark.parametrize('state', ['submitting', 'confirmed', 'unknown', 'awaiting_verification'])
def test_skip_company_never_rewrites_attempt_or_its_pending_questions(store, job, package, state):
    aid = store.prepare(job, package); store.begin_submit(aid)
    if state != 'submitting': store.finish(aid, state)
    qid = store.ask(job['id'], job['host'], 'Past attempt verification', [])
    application = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    original = dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
    skip_company(store, job['id'])
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == application
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()) == original
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?', (qid,)).fetchone()[0] == 0


def test_unknown_opportunity_cannot_change_company_boundaries(store):
    original = store.settings()
    with pytest.raises(ValueError): skip_company(store, 'unknown')
    assert store.settings() == original


def test_allow_company_removes_matching_alias_exclusions_only(store, job):
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'},
                           'skip_companies': ['Acme', 'Acme Technologies', 'Other Company']})
    generation = store.control_generation()
    result = allow_company(store, job['id'])
    assert result['removed'] == 2
    assert store.settings()['skip_companies'] == ['Other Company']
    assert store.settings()['live_enabled'] and store.control_generation() == generation
    assert not search_jobs(store)['jobs'][0]['company_skipped']
    assert allow_company(store, job['id'])['removed'] == 0


@pytest.mark.parametrize('state', ['prepared', 'submitting', 'unknown', 'confirmed', 'awaiting_verification'])
def test_allow_company_does_not_change_applications_questions_or_job_states(store, job, package, state):
    aid = store.prepare(job, package)
    if state != 'prepared':
        store.begin_submit(aid)
        if state != 'submitting': store.finish(aid, state)
    qid = store.ask(job['id'], job['host'], 'Saved question', [])
    store.update_settings({'skip_companies': ['Acme'], 'live_enabled': False})
    before_app = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    before_job = dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
    before_question = dict(store.db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone())
    generation = store.control_generation()
    allow_company(store, job['id'])
    assert not store.settings()['live_enabled'] and store.control_generation() == generation
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == before_app
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()) == before_job
    assert dict(store.db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()) == before_question
    if state == 'unknown':
        with pytest.raises(Blocked, match='company_uncertain'): store._check_budget(job, store.settings())


@pytest.mark.parametrize('job_id', ['missing', '', None, 123, 'x' * 101])
def test_allow_company_requires_existing_opportunity(store, job_id):
    original = store.settings()
    with pytest.raises(ValueError): allow_company(store, job_id)
    assert store.settings() == original
