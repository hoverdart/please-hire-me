import pytest

from hireme.answers import resolve, validate_package, field_key
from hireme.config import validate_fact
from hireme.util import Blocked, digest


def test_confirmed_finra_and_securities_status_are_distinct_from_security_credentials(store, job):
    store.put_facts({'finra_registered':'No','securities_licenses':'No'})
    for label,key in [('Are you currently registered with FINRA? *','finra_registered'),
                      ('Are you actively maintaining any securities licenses?','securities_licenses')]:
        f=field(label,options=['Yes','No'])
        answer=resolve(store,job['host'],f,context=job)
        assert answer['value']=='No' and answer['provenance']['fact_key']==key
    assert field_key('Do you hold a security clearance?') is None
    assert field_key("If 'Yes' to above, are you actively maintaining any security licenses?*") is None
    with pytest.raises(ValueError):validate_fact('finra_registered','Maybe')


def field(label, options=None):
    return {'label': label, 'type': 'select' if options else 'text',
            'required': True, 'options': options or [], 'maxlength': -1}


DEGREE = 'What is the highest degree level you are currently pursuing?'
AUTH = 'Are you legally authorized to work in the country where this role is based?'
AUTH_OPTIONS = ['Yes, I am authorized to work in this country for any employer',
                'No, I am not authorized to work in this country for any employer',
                'I am authorized to work in this country for my present employer only']
ELIGIBLE = 'Are you currently eligible to work in the United States?'
ELIGIBLE_OPTIONS = ['Yes, I am currently eligible to work in the location where this role is based.',
                    'No, I am not currently eligible to work in the location where this role is based.']
SPONSOR = 'Do you require visa sponsorship, now or in the future, to continue working in the United States?'
SPONSOR_OPTIONS = ['Yes, I will require visa sponsorship now or in the future to continue working in the country where this role is based.',
                   'No, I do not require visa sponsorship now or in the future to continue working in the country where this role is based.']


def test_pursuing_degree_is_distinct_from_completed_degree(store, job, package):
    store.put_facts({'degree': 'B.S.', 'highest_completed_degree': 'High School Diploma'})
    f = field(DEGREE, ['High School Diploma', "Bachelor's Degree", "Master's Degree"])
    assert field_key(DEGREE) == 'degree'
    a = resolve(store, job['host'], f, context=job)
    assert a['value'] == "Bachelor's Degree" and a['provenance']['fact_key'] == 'degree'
    completed = field('What is the highest degree you have completed?', f['options'])
    assert resolve(store, job['host'], completed, context=job)['value'] == 'High School Diploma'
    package.update(answers=[a], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, job, package)
    store.put_facts({'degree': 'M.S.'})
    with pytest.raises(Blocked):
        validate_package(store, job, {**package, 'facts_hash': digest(store.facts())})


def test_semantic_match_cannot_swap_completed_and_pursuing(store, job):
    store.put_facts({'degree': 'B.S.'})
    class Wrong:
        def match_field(self, *args): return {'fact_key': 'degree', 'template_id': None}
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('What is the highest degree you have earned?'), Wrong(), job)


@pytest.mark.parametrize('label', ['Alternate Email', 'Secondary e-mail address', 'Backup Email*'])
def test_alternate_email_requires_its_own_confirmed_value(store, job, label):
    f = field(label)
    with pytest.raises(Blocked, match='missing_fact'):
        resolve(store, job['host'], f, context=job)
    store.put_facts({'alternate_email': 'alternate@candidate.invalid'})
    a = resolve(store, job['host'], f, context=job)
    assert a['value'] == 'alternate@candidate.invalid'
    assert a['provenance']['fact_key'] == 'alternate_email'
    assert resolve(store, job['host'], field('Email'), context=job)['value'] == 'test@candidate.invalid'
    with pytest.raises(ValueError, match='email'):
        validate_fact('alternate_email', 'not an email')


def test_alternate_email_can_reuse_approved_context_but_not_primary(store, job):
    from hireme.materials import save_basic_context
    store.update_settings({'tailored_writing': True})
    save_basic_context(store, 'Alternate email: alternate@candidate.invalid.', 0)
    class Model:
        answer = 'alternate@candidate.invalid'
        def match_field(self, *args): return {'fact_key': 'email', 'template_id': None}
        def context_answer(self, f, choices, *args):
            source = next(c for c in choices if 'Alternate email:' in c['text'])
            return {'answer': self.answer, 'sentence_ids': [source['id']]}
    f = field('Alternate Email'); model = Model()
    a = resolve(store, job['host'], f, model, job)
    assert a['value'] == model.answer and a['provenance']['context_answer']
    assert resolve(store, job['host'], f, context=job) == a
    store.db.execute('DELETE FROM writing_answers')
    model.answer = 'test@candidate.invalid'
    with pytest.raises(Blocked, match='missing_fact'):
        resolve(store, job['host'], f, model, job)


def test_dv_any_employer_uses_citizenship_and_revalidates(store, job, package):
    job = {**job, 'location': 'New York'}
    f = field(AUTH, AUTH_OPTIONS)
    with pytest.raises(Blocked, match='option_mismatch'):
        resolve(store, job['host'], f, context=job)
    store.put_facts({'citizenship': 'United States'})
    a = resolve(store, job['host'], f, context=job)
    assert a['value'] == AUTH_OPTIONS[0]
    assert a['provenance']['citizenship_revision'] == store.facts()['citizenship']['revision']
    package.update(answers=[a], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, job, package)
    store.put_facts({'citizenship': 'Canada'})
    with pytest.raises(Blocked):
        validate_package(store, job, {**package, 'facts_hash': digest(store.facts())})


def test_unrestricted_declaration_also_establishes_any_employer(store, job):
    store.put_facts({'unrestricted_authorization': 'Yes'})
    a = resolve(store, job['host'], field(AUTH, AUTH_OPTIONS), context=job)
    assert a['value'] == AUTH_OPTIONS[0]
    assert 'unrestricted_authorization_revision' in a['provenance']


def test_nyc_and_residence_use_geography_without_model(store, job, package):
    store.put_facts({'citizenship': 'United States', 'location': 'Berkeley, CA'})
    auth = resolve(store, job['host'], field(AUTH, AUTH_OPTIONS), context={**job, 'location': 'NYC'})
    assert auth['value'] == AUTH_OPTIONS[0]
    residence = field('Please select the country where you currently reside. *', ['US', 'India', 'Canada'])
    answer = resolve(store, job['host'], residence, context=job)
    assert answer['value'] == 'US'
    package.update(answers=[answer], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, job, package)
    store.put_facts({'location': 'Toronto, Canada'})
    with pytest.raises(Blocked):
        validate_package(store, job, {**package, 'facts_hash': digest(store.facts())})


def test_stripe_summer_cohort_uses_confirmed_availability_not_second_cohort(store, job, package):
    store.update_settings({'contextual_preferences': True})
    store.put_facts({'summer_2027_available': 'Yes', 'earliest_start': '2027-05'})
    f = field('We host cohorts of 12 or 16 week internships over Winter or Summer. Please choose which cohort works best for you.*',
              ['Winter (January - April)', 'Summer (May - September)'])
    answer = resolve(store, job['host'], f, context=job)
    assert answer['value'] == f['options'][1]
    package.update(answers=[answer], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, job, package)
    second = field('Do you have flexibility to consider a second cohort option?',
                   ['I am not pursuing another cohort at this time', *f['options']])
    with pytest.raises(Blocked):
        resolve(store, job['host'], second, context=job)
    store.put_facts({'summer_2027_available': 'No'})
    with pytest.raises(Blocked):
        validate_package(store, job, {**package, 'facts_hash': digest(store.facts())})


@pytest.mark.parametrize('location', ['Remote', 'New York / London', 'United States; Canada', 'York', 'Toronto, Canada'])
def test_citizenship_does_not_authorize_foreign_or_ambiguous_role(store, job, location):
    store.put_facts({'citizenship': 'United States'})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field(AUTH, AUTH_OPTIONS), context={**job, 'location': location})


@pytest.mark.parametrize('label,options,expected', [(ELIGIBLE, ELIGIBLE_OPTIONS, 0), (SPONSOR, SPONSOR_OPTIONS, 1)])
def test_stripe_descriptive_choices_resolve_without_model(store, job, package, label, options, expected):
    job = {**job, 'location': 'San Francisco, Seattle, New York City'}
    a = resolve(store, job['host'], field(label, options), context=job)
    assert a['value'] == options[expected]
    package.update(answers=[a], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, job, package)


def test_us_citizenship_derivations_are_limited_and_revision_bound(store, job):
    store.db.execute("DELETE FROM facts WHERE key IN ('work_authorized_us','needs_sponsorship')")
    store.put_facts({'citizenship': 'United States'})
    for label, expected in [(ELIGIBLE, 'Yes'), (SPONSOR, 'No')]:
        a = resolve(store, job['host'], field(label, ['Yes', 'No']), context=job)
        assert a['value'] == expected and 'citizenship_revision' in a['provenance']
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('Do you require visa sponsorship to work in Canada?', ['Yes', 'No']), context=job)
    store.put_facts({'work_authorized_us': 'No'})
    with pytest.raises(Blocked, match='mapping_review'):
        resolve(store, job['host'], field(ELIGIBLE, ['Yes', 'No']), context=job)


def test_descriptive_choices_do_not_establish_extra_declarations(store, job):
    store.put_facts({'citizenship': 'United States'})
    f = field(AUTH, ['Yes, I am authorized to work in this country for any employer and hold a security clearance', 'No'])
    with pytest.raises(Blocked):
        resolve(store, job['host'], f, context=job)


def test_export_categories_use_citizenship_and_never_infer_it_from_us_person(store, job, package):
    label = 'The person hired will access items controlled by U.S. export control regulations including ITAR. Please identify which statement best applies to you:'
    options = ['A United States citizen or national', 'A person lawfully admitted for permanent residence of the United States (i.e. "Green Card" holder)', 'None of the Above']
    f = field(label, options)
    with pytest.raises(Blocked):
        resolve(store, job['host'], f, context=job)
    store.put_facts({'citizenship': 'United States'})
    a = resolve(store, job['host'], f, context=job)
    assert a['value'] == options[0]
    package.update(answers=[a], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, job, package)
    with pytest.raises(Blocked):
        resolve(store, job['host'], {**f, 'options': ['A United States citizen or national and clearance holder', options[1], options[2]]}, context=job)
    store.put_facts({'citizenship': 'Canada'})
    with pytest.raises(Blocked):
        validate_package(store, job, {**package, 'facts_hash': digest(store.facts())})


def test_question_details_are_visible_and_bind_agreement_approval(store, job):
    from hireme.browser import Browser
    with Browser(store, test_url='http://127.0.0.1:12345') as b:
        b.page.set_content('''<div data-field-path="consent"><fieldset class="ashby-application-form-input-checkbox-group">
            <label class="ashby-application-form-question-title required">Consent to Data Processing*</label>
            <div class="ashby-application-form-question-description">I consent for evaluating my application under
                <a href="https://employer.invalid/privacy-v1">Employer policy</a>.</div>
            <input type="checkbox" id="consent" name="I consent" required><label for="consent">I consent</label>
            </fieldset></div>''')
        f = b._snapshot()[0]
    assert 'evaluating my application' in f['help_text']
    assert f['help_links'] == [{'text': 'Employer policy', 'url': 'https://employer.invalid/privacy-v1'}]
    qid = store.ask(job['id'], job['host'], f['label'], f['options'], field=f, context=job)
    store.answer_question(qid, 'I consent')
    assert resolve(store, job['host'], f, context=job)['value'] == 'I consent'
    # Same heading and choice must not reuse consent to different policy terms.
    changed = {**f, 'help_links': [{'text': 'Employer policy', 'url': 'https://employer.invalid/privacy-v2'}]}
    with pytest.raises(Blocked):
        resolve(store, job['host'], changed, context=job)
    changed = {**f, 'help_text': 'I consent to marketing and selling my information.'}
    with pytest.raises(Blocked):
        resolve(store, job['host'], changed, context=job)


def test_discipline_search_reaches_other_beyond_initial_menu(store, job):
    from hireme.browser import Browser
    store.put_facts({'major': 'Electrical Engineering and Computer Sciences'})
    with Browser(store, test_url='http://127.0.0.1:12345') as b:
        b.page.set_content('''<label for="discipline">Discipline*</label>
            <input id="discipline" role="combobox" aria-controls="menu" required>
            <div id="menu" role="listbox"><div role="option">Computer Science</div></div>
            <script>
            const input=document.getElementById('discipline'),menu=document.getElementById('menu');
            input.addEventListener('input',()=>{
                menu.replaceChildren();
                const names=input.value==='Other'?['Other']:input.value?[]:['Computer Science'];
                for(const name of names){const option=document.createElement('div');option.setAttribute('role','option');
                    option.textContent=name;option.onclick=()=>input.value=name;menu.append(option);}
            });
            </script>''')
        f = b._snapshot()[0]
        assert f['options'] == ['Computer Science', 'Other']
        assert b._control(f).input_value() == ''
        answer = resolve(store, job['host'], f, context=job)
        assert answer['value'] == 'Other' and answer['provenance']['fact_key'] == 'major'
        b._fill(answer)
        assert b._control(f).input_value() == 'Other'


def test_greenhouse_details_include_policy_and_ignore_widget_state(store, job):
    from hireme.browser import Browser, SNAPSHOT, CONTROLS
    from hireme.field_context import field_context
    with Browser(store, test_url='http://127.0.0.1:12345') as b:
        b.page.set_content('''<div class="field-wrapper"><div class="select">
            <label for="terms">Terms and Conditions*</label>
            <input id="terms" role="combobox" aria-describedby="react-select-terms-placeholder terms-error">
            <div id="react-select-terms-placeholder">Select...</div><div id="terms-error"></div></div>
            <div class="body body__secondary"><p>I agree to the
                <a href="https://employer.invalid/privacy">privacy policy</a>.</p></div></div>''')
        before = b.page.evaluate(SNAPSHOT, CONTROLS)[0]
        assert before['help_text'] == 'I agree to the privacy policy.'
        assert before['help_links'] == [{'text': 'privacy policy', 'url': 'https://employer.invalid/privacy'}]
        b.page.evaluate("document.getElementById('react-select-terms-placeholder').remove();document.getElementById('terms-error').textContent='Please select a choice'")
        after = b.page.evaluate(SNAPSHOT, CONTROLS)[0]
        assert field_context(job['host'], before, job) == field_context(job['host'], after, job)


@pytest.mark.parametrize('native,expected',[(False,'05/19/2027'),(True,'2027-05-19')])
def test_calendar_requires_a_confirmed_day_and_formats_scoped_answer(store, job, package, native, expected):
    from hireme.browser import Browser
    store.put_facts({'earliest_start':'2027-05'})
    label='What is the earliest date you are available to start this position?'
    with Browser(store,test_url='http://127.0.0.1:12345') as b:
        b.page.set_content('<label for="start">'+label+'</label><input id="start" required '+
                           ('type="date"' if native else 'type="text" placeholder="Pick date..." class="ashby-application-form-input-date"')+'>')
        f=b._snapshot()[0]
        assert f['date_format']==('YYYY-MM-DD' if native else 'MM/DD/YYYY')
        with pytest.raises(Blocked,match='month alone'):
            resolve(store,job['host'],f,context=job)
        qid=store.ask(job['id'],job['host'],label,[],field=f,context=job)
        store.answer_question(qid,'2027-05-19')
        answer=resolve(store,job['host'],f,context=job)
        assert answer['value']==expected
        b._fill(answer)
        assert b._control(f).input_value()==expected
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.put_facts({'fulltime_start':'2028-05-19'})
    fulltime={**job,'title':'Software Engineer, New Grad'}
    # Another job/context cannot reuse the applicant's internship-day approval.
    assert resolve(store,job['host'],f,context=fulltime)['value']==expected.replace('2027','2028')
