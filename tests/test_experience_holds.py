import json

import pytest

from hireme.job_holds import hold, ready, recheck
from hireme.policy import eligible
from hireme.util import Blocked


@pytest.mark.parametrize('claim', [
    'We are two to three years ahead of everyone else in government approvals.',
    'Our company is three to four years ahead of competitors in product capabilities.',
    'We are two to five years ahead of the competition.',
])
def test_competitive_company_timeline_is_not_an_applicant_requirement(store, job, claim):
    candidate = {**job, 'description': job['description'] + ' ' + claim}
    eligible(candidate, store.settings(), store.facts())


@pytest.mark.parametrize('claim', [
    'Candidates must have three years of experience.',
    'We are two to three years ahead of everyone else and require three years of experience.',
    'Applicants must be three years ahead of other applicants.',
    'We have three years of experience building our platform.',
    'Our company is two to three years ahead of industry benchmarks.',
    'We are two to three years ahead of everyone else and require 3 years of work experience.',
])
def test_experience_requirements_and_unreviewed_timelines_remain_blocked(store, job, claim):
    candidate = {**job, 'description': job['description'] + ' ' + claim}
    with pytest.raises(Blocked, match='ambiguous_experience|experience_mismatch'):
        eligible(candidate, store.settings(), store.facts())


def saved_candidate(store, job, suffix=''):
    candidate = {**job, 'description': job['description']
                 + ' We are two to three years ahead of everyone else in government approvals.' + suffix}
    store.upsert_job(candidate)
    return candidate


def test_legacy_screening_hold_rechecks_only_when_current_posting_passes(store, job):
    candidate = saved_candidate(store, job)
    store.block(job['id'], 'ambiguous_experience')
    hold(store, candidate, 'ambiguous_experience', stage='screening')
    assert ready(store, candidate)
    assert recheck(store, job['id'])['ready']
    assert not store.db.execute('SELECT 1 FROM job_holds WHERE job_id=?', (job['id'],)).fetchone()


@pytest.mark.parametrize('kind', ['other_review', 'application_stage', 'real_requirement', 'no_reviewed_timeline'])
def test_unrelated_and_unproven_review_holds_are_not_released(store, job, kind):
    candidate = saved_candidate(store, job, ' Requires three years of experience.' if kind == 'real_requirement' else '')
    if kind == 'no_reviewed_timeline':
        candidate = {**job, 'description': job['description']}
        store.upsert_job(candidate)
    hold(store, candidate, 'other_review' if kind == 'other_review' else 'ambiguous_experience',
         stage='application' if kind == 'application_stage' else 'screening')
    assert not ready(store, candidate)


@pytest.mark.parametrize('archived', [False, True])
def test_review_recheck_never_releases_uncertain_attempt(store, job, package, archived):
    candidate = saved_candidate(store, job)
    hold(store, candidate, 'ambiguous_experience', stage='screening')
    aid = store.prepare(candidate, package)
    store.begin_submit(aid)
    store.finish(aid, 'unknown')
    if archived:
        store.db.execute("INSERT INTO archived_applications SELECT *, 'test' FROM applications WHERE id=?", (aid,))
        store.db.execute('DELETE FROM applications WHERE id=?', (aid,))
    assert not ready(store, candidate)
    if not archived:
        with pytest.raises(ValueError, match='existing submission outcome'):
            recheck(store, job['id'])
    else:
        assert not recheck(store, job['id'])['ready']
    assert store.db.execute('SELECT state FROM application_records WHERE id=?', (aid,)).fetchone()[0] == 'unknown'


@pytest.mark.parametrize('evidence', ['{', 'null', '[]', '{}', '{"stage":"application"}'])
def test_malformed_or_non_screening_evidence_keeps_hold(store, job, evidence):
    candidate = saved_candidate(store, job)
    hold(store, candidate, 'ambiguous_experience', stage='screening')
    store.db.execute('UPDATE job_holds SET evidence=? WHERE job_id=?', (evidence, job['id']))
    assert not ready(store, candidate)
    assert not recheck(store, job['id'])['ready']
    assert store.db.execute('SELECT reason FROM job_holds WHERE job_id=?', (job['id'],)).fetchone()[0] == 'ambiguous_experience'


@pytest.mark.parametrize('payload', ['{', 'null', '[]', '{"description":null}', '{"description":42}'])
def test_malformed_posting_keeps_hold_in_worker_and_explicit_recheck(store, job, payload):
    candidate = saved_candidate(store, job)
    hold(store, candidate, 'ambiguous_experience', stage='screening')
    store.db.execute('UPDATE jobs SET payload=? WHERE id=?', (payload, job['id']))
    assert not ready(store, candidate)
    assert recheck(store, job['id']) == {'ready':False,'reason':'invalid_posting'}
    assert store.db.execute('SELECT reason FROM job_holds WHERE job_id=?', (job['id'],)).fetchone()[0] == 'ambiguous_experience'


def test_posting_divergence_or_invalid_policy_input_does_not_release_hold(store, job):
    candidate = saved_candidate(store, job)
    hold(store, candidate, 'ambiguous_experience', stage='screening')
    assert not ready(store, {**candidate, 'description': candidate['description'] + ' Requires three years of experience.'})
    assert not ready(store, {**candidate, 'company': 'Another employer'})
    invalid = {**candidate, 'min_years': 'not a number'}
    store.db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(invalid), job['id']))
    assert not ready(store, invalid)
    assert recheck(store, job['id']) == {'ready':False,'reason':'invalid_posting'}
