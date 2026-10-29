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


@pytest.mark.parametrize('number', ['one', 'eleven', 'twelve'])
@pytest.mark.parametrize('statement', [
    'Candidates must have {number} year of experience.',
    'The role requires {number} years of relevant software experience.',
    '<li>Minimum of {number} years of industry experience</li>',
    '<li>{number} years of professional experience</li>',
    '- {number} years of Python experience',
    '{number} years of work experience is required.',
])
def test_additional_written_applicant_experience_requires_review(store, job, number, statement):
    candidate = {**job, 'description': job['description'] + '\n' + statement.format(number=number)}
    with pytest.raises(Blocked, match='ambiguous_experience'):
        eligible(candidate, store.settings(), store.facts())


@pytest.mark.parametrize('statement', [
    'Our company has one year of experience serving customers.',
    'Our company has at least one year of experience in this market.',
    'Our company was founded eleven years ago.',
    'The platform has been running for twelve years.',
    'We are two to three years ahead of everyone else in government approvals.',
])
def test_additional_written_numbers_do_not_block_company_history(store, job, statement):
    candidate = {**job, 'description': job['description'] + ' ' + statement}
    eligible(candidate, store.settings(), store.facts())


def test_reviewed_company_timeline_cannot_hide_an_explicit_applicant_requirement(store, job):
    candidate = {**job, 'description': job['description']
                 + ' We are two to three years ahead of everyone else in government approvals.'
                 + ' Candidates must have one year of software experience.'}
    store.upsert_job(candidate)
    hold(store, candidate, 'ambiguous_experience', stage='screening')
    assert not ready(store, candidate)
    with pytest.raises(Blocked, match='ambiguous_experience'):
        eligible(candidate, store.settings(), store.facts())


@pytest.mark.parametrize('number', ['one', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen',
                                     'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'])
@pytest.mark.parametrize('statement', [
    'Candidates need {number}+ years of C++, Python, or Java experience.',
    'Applicants should have {number} years+ of relevant industry experience.',
    'A candidate needs {number} years of JavaScript, React, and Python experience.',
])
def test_explicit_requirements_handle_plus_signs_skill_lists_and_candidate_phrasing(store, job, number, statement):
    candidate = {**job, 'description': job['description'] + ' ' + statement.format(number=number)}
    with pytest.raises(Blocked, match='ambiguous_experience'):
        eligible(candidate, store.settings(), store.facts())


@pytest.mark.parametrize('statement', [
    'We do not require one year of experience.',
    "We don't require eleven years of experience.",
    'Applicants are not required to have thirteen years of experience.',
    '<li>One year of Python experience is not required.</li>',
    '- Eleven years of experience are not necessary.',
    'Our company has at least thirteen years of experience serving customers.',
])
def test_negated_requirements_and_company_experience_are_not_holds(store, job, statement):
    eligible({**job, 'description': job['description'] + '\n' + statement}, store.settings(), store.facts())


def test_negated_clause_does_not_hide_a_separate_applicant_requirement(store, job):
    candidate = {**job, 'description': job['description']
                 + " We don't require one year of experience, but candidates need thirteen years of software experience."}
    with pytest.raises(Blocked, match='ambiguous_experience'):
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
    with pytest.raises(ValueError, match='existing submission outcome'):
        recheck(store, job['id'])
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
