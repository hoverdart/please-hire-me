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
    class ForbiddenModel:
        def match_field(self,*args):raise AssertionError('Student status must not fall through to model inference')
        def context_answer(self,*args):raise AssertionError('Student status must not fall through to writing')
    with pytest.raises(Blocked,match='missing_fact'):
        resolve(store, job['host'], question('Still Student?'), ForbiddenModel(), context=job)


def test_branded_careers_source_uses_posting_provenance():
    context = {'company': 'grow-therapy', 'source': 'ash:grow-therapy'}
    options = ['LinkedIn', 'Grow website/careers page', 'Grow Engineering Blog', 'Other - Please describe']
    answer = _discovery_answer('How did you hear about this role?', options, context)
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








@pytest.mark.parametrize('location,allowed',[('San Francisco, CA',True),('Toronto, Canada',False)])
def test_descriptive_work_authorization_needs_us_posting_evidence(store,job,location,allowed):
    f=question('Are you legally authorized to work in the country where the job is located?*')
    f['options']=['Yes, I am currently legally authorized to work in the country where the jobs is located.',
                  'No, I am not currently legally authorized to work in the country where the job is located.']
    store.put_facts({'work_authorized_us':'Yes'})
    context={**job,'location':location}
    if allowed:
        assert resolve(store,job['host'],f,context=context)['value']==f['options'][0]
    else:
        with pytest.raises(Blocked):resolve(store,job['host'],f,context=context)


@pytest.mark.parametrize('text',[
    'I certify that I personally completed this application and that it was not filled out, in whole or in part, by another person or by an artificial intelligence (AI) tool on my behalf.',
    'I confirm this application was not written by an AI tool.',
    'I confirm this application has not been prepared by artificial intelligence.',
])
def test_applicant_only_attestations_require_manual_work(text):
    from hireme.answers import page_requires_manual_work
    assert page_requires_manual_work(text)


def test_employer_ai_work_description_is_not_an_applicant_attestation():
    from hireme.answers import page_requires_manual_work
    assert not page_requires_manual_work('Build AI tools and develop well-tested systems. AI-assisted development experience is welcome.')


def test_company_first_awareness_is_not_inferred_from_discovery():
    with pytest.raises(Blocked,match='missing_fact'):
        _discovery_answer('How did you first learn about Grow Therapy?',
                          ['Grow website/careers page'],{'company':'grow-therapy','source':'ash:grow-therapy'})


def test_year_only_graduation_overlap_requires_month_precision():
    from hireme.graduation import matches,endpoint
    with pytest.raises(Blocked,match='graduation_window_review'):
        matches('2028',(endpoint('May 2028')[0],endpoint('June 2028')[1]))


@pytest.mark.parametrize('binding',['template','fact'])
def test_valid_student_history_overrides_legacy_bindings(store,job,monkeypatch,binding):
    monkeypatch.setattr('hireme.util.now',lambda:'2026-10-28T00:00:00+00:00')
    f=question('Still Student?')
    store.put_facts({'school':'Fixture University'})
    store.put_history_entry('education',{'institution':'Fixture University','current':True,
        'start':'2025-08','expected_end':'2028-05'},source='fixture',confirmed=True)
    if binding=='template':
        tid=store.put_template('experience','I am currently a student at Fixture University.')
        store.bind_field(job['host'],f['label'],f['options'],template_id=tid)
    else:store.bind_field(job['host'],f['label'],f['options'],fact_key='school')
    a=resolve(store,job['host'],f,context=job)
    assert a['value']=='Yes' and 'current_student_history' in a['provenance']


@pytest.mark.parametrize('cache',['writing','saved','binding'])
def test_legacy_student_answers_cannot_bypass_expired_history(store,job,monkeypatch,cache):
    monkeypatch.setattr('hireme.util.now',lambda:'2026-10-28T00:00:00+00:00')
    f=question('Still Student?')
    store.put_history_entry('education',{'institution':'Fixture University','current':True,
        'start':'2024-08','expected_end':'2025-05'},source='fixture',confirmed=True)
    if cache=='writing':store.save_writing_answer(job['host'],f['label'],f['options'],{'value':'Yes','provenance':{'sample_parts':[]}})
    elif cache=='saved':
        qid=store.ask(job['id'],job['host'],f['label'],f['options'],'missing_fact',field=f,context=job)
        store.answer_question(qid,'Yes')
    else:
        tid=store.put_template('experience','I am currently a student at Fixture University.')
        store.bind_field(job['host'],f['label'],f['options'],template_id=tid)
    with pytest.raises(Blocked,match='missing_fact'):resolve(store,job['host'],f,context=job)
