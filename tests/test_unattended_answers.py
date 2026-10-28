import pytest

from hireme.answers import resolve, validate_package, _discovery_answer
from hireme.job_holds import hold, ready
from hireme.util import Blocked


def question(label):
    return {'label': label, 'type': 'select', 'required': True,
            'options': ['Yes', 'No'], 'maxlength': -1}


@pytest.mark.parametrize('label', ['Still Student?', 'Are you currently a student?'])
def test_current_student_uses_confirmed_history_and_revalidates(store, job, package, monkeypatch, label):
    monkeypatch.setattr('hireme.util.now', lambda: '2026-10-28T00:00:00+00:00')
    entry = store.put_history_entry('education', {
        'institution': 'Fixture University', 'current': True,
        'start': '2025-08', 'expected_end': '2028-05',
    }, source='applicant-confirmed fixture', confirmed=True)
    answer = resolve(store, job['host'], question(label), context=job)
    assert answer['value'] == 'Yes'
    assert answer['provenance']['current_student_history']['id'] == entry['id']
    package.update(answers=[answer], steps=[])
    validate_package(store, job, package)
    store.withdraw_history_entry(entry['id'])
    with pytest.raises(Blocked):
        validate_package(store, job, package)


@pytest.mark.parametrize('confirmed,current,start,end', [
    (False, True, '2025-08', '2028-05'),
    (True, False, '2025-08', '2028-05'),
    (True, True, '2027-08', '2028-05'),
    (True, True, '2024-08', '2025-05'),
])
def test_student_status_is_not_guessed(store, job, monkeypatch, confirmed, current, start, end):
    monkeypatch.setattr('hireme.util.now', lambda: '2026-10-28T00:00:00+00:00')
    store.put_history_entry('education', {
        'institution': 'Fixture University', 'current': current,
        'start': start, 'expected_end': end,
    }, source='fixture', confirmed=confirmed)
    with pytest.raises(Blocked):
        resolve(store, job['host'], question('Still Student?'), context=job)


def test_branded_careers_source_uses_posting_provenance():
    context = {'company': 'grow-therapy', 'source': 'ash:grow-therapy'}
    options = ['LinkedIn', 'Grow website/careers page', 'Grow Engineering Blog', 'Other - Please describe']
    answer = _discovery_answer('How did you first learn about Grow Therapy?', options, context)
    assert answer == {'value': 'Grow website/careers page',
                      'provenance': {'job_source': 'ash:grow-therapy'}}


@pytest.mark.parametrize('source,company,options', [
    ('user', 'grow-therapy', ['Grow website/careers page']),
    ('ash:grow-therapy', 'grow-therapy', ['Growly website/careers page']),
    ('ash:grow-therapy', 'grow-therapy', ['Grow Engineering Blog', 'Employee referral']),
    ('gh:the-brattle-group', 'the-brattle-group', ['The website/careers page']),
    ('ash:grow-therapy', 'grow-therapy', ['Grow website/careers page', 'Grow website / careers page']),
])
def test_source_choice_does_not_guess_or_select_ambiguous_options(source, company, options):
    assert _discovery_answer('How did you hear about us?', options,
                             {'source': source, 'company': company}) is None


def test_answer_fix_reconsiders_unattempted_holds_without_releasing_uncertainty(store, job, package, monkeypatch):
    monkeypatch.setattr('hireme.answers.AUTOMATIC_ANSWER_VERSION', 1)
    hold(store, job, 'missing_answers')
    assert not ready(store, job)
    monkeypatch.setattr('hireme.answers.AUTOMATIC_ANSWER_VERSION', 2)
    assert ready(store, job)
    aid = store.prepare(job, package)
    store.begin_submit(aid)
    store.finish(aid, 'unknown')
    assert not ready(store, job)


@pytest.mark.parametrize('graduation,expected', [
    ('2027-12', 'Yes'), ('2028-05', 'Yes'), ('2028-06', 'Yes'),
    ('2028-01', 'No'), ('2028-04', 'No'), ('2028-07', 'No'),
])
def test_named_graduation_alternatives_keep_gaps(store, job, graduation, expected):
    store.put_facts({'graduation': graduation})
    answer=resolve(store, job['host'], question('Are you graduating in December 2027 or May/June 2028?'), context=job)
    assert answer['value']==expected


def test_year_only_graduation_cannot_answer_a_named_month_question():
    from hireme.graduation import qualifies
    with pytest.raises(Blocked,match='graduation_window_review'):
        qualifies('2028','Are you graduating in December 2027 or May/June 2028?')
