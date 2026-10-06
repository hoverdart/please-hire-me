"""Reproduce report blockers using confirmed sources, without model inference."""
import pytest

from hireme.answers import resolve, validate_package
from hireme.util import Blocked, digest


def field(label, kind='select', options=None, **extra):
    return {'label': label, 'type': kind, 'required': True,
            'options': ['Yes', 'No'] if options is None else options,
            'maxlength': -1, **extra}


@pytest.mark.parametrize('label', [
    'Are you legally authorized to work in the country where you are applying?',
    'Are you legally authorized to work in the country for which you are applying?*',
    'Are you legally authorized to work in that country?',
])
def test_report_authorization_uses_confirmed_us_fact(store, job, label):
    assert resolve(store, job['host'], field(label), context=job)['value'] == 'Yes'


@pytest.mark.parametrize('location', ['Remote', 'Toronto, Canada', 'United States; Canada', 'London, UK'])
def test_generic_authorization_requires_unambiguous_country(store, job, location):
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('Are you legally authorized to work in the country for which you are applying?*'),
                context={**job, 'location': location})


@pytest.mark.parametrize('label', [
    'Are you legally authorized to work in the country for which you are applying (Canada)?',
    'Are you not legally authorized to work in the country for which you are applying?',
])
def test_us_posting_cannot_override_foreign_or_negated_authorization_label(store, job, label):
    class IncorrectModel:
        def match_field(self, *args): return {'fact_key': 'work_authorized_us', 'template_id': None}
    with pytest.raises(Blocked): resolve(store, job['host'], field(label), IncorrectModel(), context=job)


def test_quora_selected_employment_country_scopes_authorization(store, job):
    store.update_settings({'contextual_preferences': True})
    store.put_facts({'country': 'United States'})
    country = field('In which of the following employment eligible countries (https://www.careers.quora.com/eligible-countries) are you seeking to work, if hired?',
                    options=['Canada', 'United States', 'United Kingdom'])
    selected = resolve(store, job['host'], country, context={**job, 'location': 'Remote'})
    assert selected['value'] == 'United States'
    context = {**job, 'location': 'Remote', 'previous_answers': [selected]}
    assert resolve(store, job['host'], field('Are you legally authorized to work in that country?'), context=context)['value'] == 'Yes'
    selected['value'] = 'Canada'
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('Are you legally authorized to work in that country?'), context=context)


@pytest.mark.parametrize('key,label,old,new', [
    ('phone', 'Phone', '+12025550123', '+12025550124'),
    ('school', 'School Name', 'Old Confirmed University', 'New Confirmed University'),
])
def test_current_confirmed_fact_supersedes_stale_linked_answer(store, job, package, key, label, old, new):
    f = field(label, 'text', [])
    store.put_facts({key: old})
    qid = store.ask(job['id'], job['host'], label, [], field=f, context=job)
    store.answer_question(qid, old, key)
    store.put_facts({key: new})
    answer = resolve(store, job['host'], f, context=job)
    assert answer['value'] == new
    assert answer['provenance'] == {'fact_key': key, 'revision': store.facts()[key]['revision']}
    package['answers'].append(answer)
    package['facts_hash'] = digest(store.facts())
    validate_package(store, job, package)


@pytest.mark.parametrize('date,expected', [('2028-05', 'No'), ('2026-12', 'Yes'), ('2027-05', 'Yes'), ('2027-07', 'No')])
def test_scale_graduation_confirmation_is_a_boolean_comparison(store, job, date, expected):
    store.put_facts({'graduation': date})
    f = field('I confirm that my graduation date will be either Fall 2026 or Spring 2027*')
    answer = resolve(store, job['host'], f, context=job)
    assert answer['value'] == expected
    assert answer['provenance']['graduation_window_revision'] == store.facts()['graduation']['revision']


def test_interview_language_preference_uses_confirmed_skills_when_opted_in(store, job):
    store.update_settings({'contextual_preferences': True})
    question = field('What is your preferred programming language for your interviews (you can change this later)?',
                     options=['Java', 'Python', 'C++'])
    assert resolve(store, job['host'], question, context=job)['value'] == 'Python'
    store.update_settings({'contextual_preferences': False})
    with pytest.raises(Blocked): resolve(store, job['host'], question, context=job)


def test_explicit_approval_survives_label_formatting_and_length_changes(store, job):
    label = 'I understand that all employees for this position will be expected to be available for meetings during coordination hours'
    f = field(label)
    qid = store.ask(job['id'], job['host'], label, f['options'], field=f, context=job)
    store.answer_question(qid, 'Yes')
    changed = {**f, 'label': label + '*', 'maxlength': 50}
    assert resolve(store, job['host'], changed, context=job)['value'] == 'Yes'
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?', (qid,)).fetchone()[0] == 1
    with pytest.raises(Blocked):
        resolve(store, job['host'], changed, context={**job, 'company': 'Another Employer'})


def test_coordination_hours_are_not_invented_from_onsite_or_skills(store, job):
    store.put_facts({'onsite': 'Yes'})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('I understand that all employees for this position will be expected to be available during coordination hours'), context=job)


@pytest.mark.parametrize('language', ['C++', 'C#'])
def test_interview_language_preserves_language_symbols(store, job, language):
    store.put_facts({'skills': language + ', Python'})
    store.update_settings({'contextual_preferences': True})
    answer = resolve(store, job['host'], field('Preferred programming language for interviews?', options=['C', 'C++', 'C#']), context=job)
    assert answer['value'] == language


def test_selected_country_dependency_is_revalidated_in_package(store, job, package):
    store.update_settings({'contextual_preferences': True})
    store.put_facts({'country': 'United States'})
    country = field('In which of the following employment eligible countries are you seeking to work, if hired?', options=['United States', 'Canada'])
    selection = resolve(store, job['host'], country, context=job)
    authorization = resolve(store, job['host'], field('Are you legally authorized to work in that country?'), context={**job, 'previous_answers': [selection]})
    package['answers'] += [selection, authorization]
    package['facts_hash'] = digest(store.facts())
    validate_package(store, job, package)
    selection['value'] = 'Canada'
    with pytest.raises(Blocked): validate_package(store, job, package)


def test_changed_limit_and_mapping_version_clear_old_question_without_losing_approval(store, job):
    from hireme.field_context import field_context
    import json
    f = field('Previously approved scheduling answer')
    metadata = field_context(job['host'], f, job)
    metadata['version'] = 6
    qid = digest(['approved_answer', metadata])
    store.db.execute('INSERT INTO question_contexts VALUES(?,?)', (qid, json.dumps(metadata)))
    store.db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,0)', (qid, job['id'], job['host'], f['label'], json.dumps(f['options']), 'missing_fact'))
    store.answer_question(qid, 'Yes')
    store.db.execute('UPDATE questions SET resolved=0 WHERE id=?', (qid,))
    assert resolve(store, job['host'], {**f, 'label': f['label'] + '*', 'maxlength': 20}, context=job)['value'] == 'Yes'
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?', (qid,)).fetchone()[0] == 1
    with pytest.raises(Blocked, match='answer_too_long'):
        resolve(store, job['host'], {**f, 'maxlength': 2}, context=job)


def test_old_graduation_confirmation_cannot_override_current_date(store, job):
    f = field('I confirm that my graduation date will be either Fall 2026 or Spring 2027*')
    qid = store.ask(job['id'], job['host'], f['label'], f['options'], field=f, context=job)
    store.answer_question(qid, 'Yes')
    assert resolve(store, job['host'], f, context=job)['value'] == 'No'


def test_named_graduation_seasons_do_not_include_gap_and_screen_before_browser(store, job):
    from hireme.graduation import required
    text='Required graduation date will be either Fall 2026 or Spring 2027'
    required(text, '2026-12')
    with pytest.raises(Blocked, match='graduation_mismatch'): required(text, '2027-02')
    with pytest.raises(Blocked, match='graduation_mismatch'): required(text, '2028-05')


def test_employment_country_preference_uses_confirmed_residence_without_country_fact(store,job,package):
    store.update_settings({'contextual_preferences':True})
    f=field('In which of the following employment eligible countries are you seeking to work, if hired?',options=['Canada','United States'])
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']=='United States'
    assert answer['provenance']['contextual_preference']['residence_location_revision']==store.facts()['location']['revision']
    package['answers'].append(answer);package['facts_hash']=digest(store.facts())
    validate_package(store,job,package)
    store.put_facts({'location':'Toronto, Canada'})
    with pytest.raises(Blocked):validate_package(store,job,package)


@pytest.mark.parametrize('consent', ['Yes','No'])
def test_sms_descriptive_options_use_only_confirmed_consent(store,job,consent):
    options=['Yes - I consent to receiving text messages','No - I do not consent to receiving text messages']
    f=field('Consent to receiving text messages','radio',options)
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)
    store.put_facts({'sms':consent})
    assert resolve(store,job['host'],f,context=job)['value']==options[consent=='No']
    with pytest.raises(Blocked):resolve(store,job['host'],{**f,'options':['Yes - I consent to marketing messages','No - I decline marketing messages']},context=job)


def test_structured_employer_countries_prevent_mixed_country_authorization(store,job):
    f=field('Are you legally authorized to work in the country where you are applying?')
    assert resolve(store,job['host'],f,context={**job,'location':'SF','employment_countries':['United States']})['value']=='Yes'
    with pytest.raises(Blocked):resolve(store,job['host'],f,context={**job,'employment_countries':['United States','Poland']})


@pytest.mark.parametrize('commitment',['Yes','No'])
def test_gecko_onsite_descriptive_options_reuse_confirmed_commitment(store,job,commitment):
    options=['Yes, I am able and willing to work in the office location listed in the job description.',
             'No, I am unable and/or unwilling to work in the office location listed in the job description.']
    store.put_facts({'onsite':commitment})
    f=field('I acknowledge that Gecko has an in-office culture, and that I am willing and able to work in the office location listed on the job description.','radio',options)
    assert resolve(store,job['host'],f,context=job)['value']==options[commitment=='No']


def test_quora_discipline_field_maps_to_confirmed_major(store,job):
    store.put_facts({'major':'Computer Science'})
    assert resolve(store,job['host'],field('Discipline/Field of Study','text',[]),context=job)['value']=='Computer Science'


def test_resolved_current_field_retires_obsolete_question_for_same_job_only(store,job):
    store.put_facts({'school':'Confirmed University'})
    old=field('School Name','combobox',['Confirmed UniversityUnited Statesexample.edu'])
    qid=store.ask(job['id'],job['host'],old['label'],old['options'],field=old,context={**job,'location':'Old listing location'})
    other={**job,'id':'different-job','location':'Old listing location'}
    other_id=store.ask(other['id'],job['host'],old['label'],old['options'],field=old,context={**other,'title':'Different role'})
    current={**old,'options':['Confirmed University']}
    assert resolve(store,job['host'],current,context=job)['value']=='Confirmed University'
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(qid,)).fetchone()[0]==1
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(other_id,)).fetchone()[0]==0


def test_changed_choices_cannot_reuse_literal_approval_or_retire_unanswered_question(store,job):
    f=field('Previously reviewed agreement')
    qid=store.ask(job['id'],job['host'],f['label'],f['options'],field=f,context=job)
    store.answer_question(qid,'Yes')
    store.db.execute('UPDATE questions SET resolved=0 WHERE id=?',(qid,))
    with pytest.raises(Blocked):resolve(store,job['host'],{**f,'options':['I agree','I decline']},context=job)
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(qid,)).fetchone()[0]==0


def test_generic_country_approval_is_scoped_to_structured_employment_countries(store,job):
    f=field('Are you legally authorized to work in the country where you are applying?')
    context={**job,'employment_countries':['United States']}
    qid=store.ask(job['id'],job['host'],f['label'],f['options'],field=f,context=context)
    store.answer_question(qid,'Yes')
    assert resolve(store,job['host'],f,context=context)['value']=='Yes'
    with pytest.raises(Blocked):resolve(store,job['host'],f,context={**context,'employment_countries':['Poland']})


def test_corrected_nested_sms_label_retires_only_the_mislabeled_radio_question(store,job):
    options=['Yes - I consent to receiving text messages','No - I do not consent to receiving text messages']
    old=field('Phone','radio',options)
    qid=store.ask(job['id'],job['host'],old['label'],options,field=old,context=job)
    phone=field('Phone','tel',[])
    phone_qid=store.ask(job['id'],job['host'],phone['label'],[],field=phone,context=job)
    store.put_facts({'sms':'Yes'})
    assert resolve(store,job['host'],{**old,'label':'Consent to receiving text messages'},context=job)['value']==options[0]
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(qid,)).fetchone()[0]==1
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(phone_qid,)).fetchone()[0]==0


def test_reported_ashby_questions_resolve_from_confirmed_facts(store, job):
    store.put_facts({'school': 'Confirmed University', 'college_start': '2025-08', 'citizenship': 'United States',
                     'contacts_outside_resume': 'No', 'phone': '(202) 555-0123'})
    ashby = 'jobs.ashbyhq.com|saronic'
    context = {**job, 'company': 'Saronic'}
    assert resolve(store, ashby, field('Phone', 'number', []), context=context)['value'] == '2025550123'
    assert resolve(store, ashby, field('College/University', 'text', []), context=context)['value'] == 'Confirmed University'
    assert resolve(store, ashby, field('Expected Graduation Month', 'radio', ['April/May/June', 'August/September', 'December']), context=context)['value'] == 'April/May/June'
    assert resolve(store, ashby, field('Are you related to any current Saronic employees?', 'yesno'), context=context)['value'] == 'No'
    assert resolve(store, ashby, field('Are you a US Citizen, Green Card Holder, or Permanent Resident', 'yesno'), context=context)['value'] == 'Yes'
    assert resolve(store, ashby, field('Are you currently enrolled in a degree program at a registered college or university?', 'yesno'), context=context)['value'] == 'Yes'


def test_citizen_or_resident_and_enrollment_never_infer_no(store, job):
    store.put_facts({'citizenship': 'Canada', 'school': 'Confirmed University', 'college_start': '2020-08', 'graduation': '2024-05'})
    for label in ('Are you a US Citizen, Green Card Holder, or Permanent Resident',
                  'Are you currently enrolled in a degree program at a registered college or university?'):
        with pytest.raises(Blocked):resolve(store, job['host'], field(label, 'yesno'), context=job)


def test_grouped_month_choice_must_be_unique(store, job):
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('Expected Graduation Month', 'radio', ['April/May', 'May/June']), context=job)


@pytest.mark.parametrize('label,expected', [
    ('When is your anticipated graduation date?', 'May 2028'),
    ('Graduation Semester', 'Spring 2028'),
    ('Expected graduation (MM/YYYY)', '05/2028'),
    ('Graduation month/year', '05/2028'),
    ('Graduation year', '2028'),
])
def test_text_dates_are_written_as_people_write_them(store, job, label, expected):
    assert resolve(store, job['host'], field(label, 'text', []), context=job)['value'] == expected


@pytest.mark.parametrize('label,key', [
    ('Are you or have you been entrusted with a prominent public function?*', 'government_official'),
    ('Are you an immediate family member of someone holding such a position?*', 'government_official'),
    ('Were you referred to this role by a current Acme employee?*', 'contacts_outside_resume'),
])
def test_disclosures_follow_their_confirmed_fact(store, job, label, key):
    with pytest.raises(Blocked):resolve(store, job['host'], field(label), context=job)
    store.put_facts({key: 'No'})
    assert resolve(store, job['host'], field(label), context=job)['value'] == 'No'
