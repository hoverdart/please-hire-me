"""Real blocked question shapes, using synthetic applicant facts only."""
import pytest

from hireme.answers import resolve, validate_package
from hireme.job_holds import hold, ready
from hireme.util import Blocked, digest


def field(label, options=None):
    return {'label':label,'type':'select' if options else 'textarea',
            'options':options or [],'required':True,'maxlength':-1}


class NoModel:
    def __getattr__(self, name):
        raise AssertionError('Routine logistics must not require inference: '+name)


LOCAL_QUESTIONS=[
    'Are you able and willing to work from our San Francisco HQ 5 days a week (or relocate if needed)?',
    'Are you okay spending this internship in San Francisco?',
    'Relocation to San Francisco is mandatory for this role. Are you able to relocate and work in person in San Francisco?',
]


@pytest.mark.parametrize('host',['jobs.ashbyhq.com','jobs.lever.co'])
@pytest.mark.parametrize('label',LOCAL_QUESTIONS)
def test_local_office_question_uses_both_facts_and_revalidates(store,job,package,host,label):
    store.put_facts({'onsite':'Yes'})
    job={**job,'host':host}
    answer=resolve(store,host,field(label,['Yes','No']),NoModel(),context=job)
    assert answer['value']=='Yes'
    assert set(answer['provenance']['routine_logistics'])=={'onsite_revision','location_revision'}
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.put_facts({'location':'Toronto, Canada'})
    package['facts_hash']=digest(store.facts())
    with pytest.raises(Blocked):validate_package(store,job,package)


@pytest.mark.parametrize('change',[{'onsite':'No'},{'location':'Toronto, Canada'},{'location':'San Diego, CA'}])
def test_local_office_answer_does_not_follow_from_relocation_alone(store,job,change):
    store.put_facts({'onsite':'Yes','relocate':'Yes',**change})
    with pytest.raises(Blocked):resolve(store,job['host'],field(LOCAL_QUESTIONS[0],['Yes','No']),context=job)


def test_return_offer_year_uses_permanent_start_not_internship_or_graduation(store,job,package):
    f=field('If given a return offer (not guaranteed), what year would you be able to start?')
    store.put_facts({'earliest_start':'2027-05'})
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)
    store.put_facts({'fulltime_start':'2029-01-19'})
    answer=resolve(store,job['host'],f,NoModel(),context=job)
    assert answer['value']=='2029' and answer['provenance']['fact_key']=='fulltime_start'
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.put_facts({'fulltime_start':'2030-01-19'})
    package['facts_hash']=digest(store.facts())
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_internship_start_preserves_month_precision_without_inventing_end_date(store,job,package):
    store.put_facts({'earliest_start':'2027-05'})
    f=field('Earliest start date. If applying for an internship, include your available dates.')
    answer=resolve(store,job['host'],f,NoModel(),context=job)
    assert answer['value']=='May 2027'
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    with pytest.raises(Blocked):resolve(store,job['host'],f,context={**job,'title':'New Grad Software Engineer'})


def test_university_picker_label_maps_to_confirmed_school(store,job):
    store.put_facts({'school':'Fixture University'})
    f=field('Select a university or college',['Fixture University','Another University'])
    assert resolve(store,job['host'],f,NoModel(),context=job)['value']=='Fixture University'


@pytest.mark.parametrize('countries,allowed',[
    (['United States'],True),(['Canada'],False),(['United States','Canada'],False),
])
def test_template_country_authorization_uses_validated_employment_country(store,job,package,countries,allowed):
    f=field('Are you legally authorized to work in [Country]? (Yes/No)',['Yes','No'])
    context={**job,'employment_countries':countries}
    if not allowed:
        with pytest.raises(Blocked):resolve(store,job['host'],f,context=context)
        return
    answer=resolve(store,job['host'],f,NoModel(),context=context)
    assert answer['value']=='Yes' and answer['provenance']['fact_key']=='work_authorized_us'
    package.update(answers=[answer],steps=[])
    validate_package(store,context,package)
    with pytest.raises(Blocked):validate_package(store,{**context,'employment_countries':['Canada']},package)


@pytest.mark.parametrize('reason',['missing_answers','provider_unavailable','provider_cli_incompatible','provider_error'])
def test_changed_connection_releases_only_unattempted_holds(store,job,package,reason):
    hold(store,job,reason,at=1)
    assert not ready(store,job,at=2)
    store.update_settings({'provider':'codex-cli','provider_model':''})
    assert ready(store,job,at=2)
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.update_settings({'provider':'claude-cli','provider_model':'sonnet'})
    assert not ready(store,job,at=2)


def test_a_changed_provider_cannot_release_an_uncertain_ashby_draft(store,job):
    job={**job,'host':'jobs.ashbyhq.com'}
    store.db.execute("INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)",
        ('held',job['id'],'ashby','ashby:attempt','request','source','unknown','created','updated',''))
    hold(store,job,'missing_answers')
    store.update_settings({'provider':'codex-cli','provider_model':''})
    assert not ready(store,job)


def test_browser_change_rechecks_only_unattempted_native_widget_holds(store,job,package):
    hold(store,job,'unsupported_widget')
    assert not ready(store,job)
    store.update_settings({'browser_channel':'chrome'})
    assert ready(store,job)
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.update_settings({'browser_channel':'chromium'})
    assert not ready(store,job)


def test_answer_for_another_employer_does_not_reopen_a_missing_information_hold(store,job):
    f=field('Confirm the application instructions',['Yes','No'])
    own_scope=job['host']+'|'+store.company(job['company'])
    qid=store.ask(job['id'],own_scope,f['label'],f['options'],field=f,context=job)
    hold(store,job,'missing_answers')
    other={**job,'id':digest('other-employer'),'url':job['url']+'-other','company':'Other Employer'}
    store.upsert_job(other)
    other_qid=store.ask(other['id'],other['host']+'|'+store.company(other['company']),
        f['label'],f['options'],field=f,context=other)
    store.answer_question(other_qid,'Yes')
    assert not ready(store,job)
    store.answer_question(qid,'Yes')
    assert ready(store,job)


@pytest.mark.parametrize('state',['prepared','unknown','confirmed','awaiting_verification'])
def test_writing_source_upgrade_rechecks_exhausted_drafts_without_releasing_attempts(store,job,package,monkeypatch,state):
    monkeypatch.setattr('hireme.answers.WRITING_SOURCE_VERSION',1)
    aid=store.prepare(job,package)
    hold(store,job,'writing_unsupported',at=100)
    store.db.execute('update job_holds set retry_count=2,retry_at=NULL where job_id=?',(job['id'],))
    if state!='prepared':store.begin_submit(aid);store.finish(aid,state)
    before=dict(store.application_record(aid))
    assert not ready(store,job,at=10000)
    monkeypatch.setattr('hireme.answers.WRITING_SOURCE_VERSION',2)
    assert ready(store,job,at=10000)==(state=='prepared')
    assert dict(store.application_record(aid))==before


@pytest.mark.parametrize('state',['prepared','unknown'])
def test_writing_upgrade_rechecks_mixed_form_hold_without_reopening_plain_fact_holds(store,job,package,monkeypatch,state):
    monkeypatch.setattr('hireme.answers.WRITING_SOURCE_VERSION',1)
    writing=field("Please include a short description of the work you're proudest of.")
    store.ask(job['id'],job['host'],writing['label'],[],reason='writing_unsupported',field=writing,context=job)
    store.ask(job['id'],job['host'],'Exact internship start date',[],reason='missing_fact')
    aid=store.prepare(job,package)
    hold(store,job,'missing_answers')
    other={**job,'id':digest('plain-information-hold'),'url':job['url']+'-plain'}
    store.upsert_job(other)
    store.ask(other['id'],other['host'],'Exact internship start date',[],reason='missing_fact')
    hold(store,other,'missing_answers')
    if state=='unknown':store.begin_submit(aid);store.finish(aid,state)
    before=dict(store.application_record(aid))
    assert not ready(store,job) and not ready(store,other)
    monkeypatch.setattr('hireme.answers.WRITING_SOURCE_VERSION',2)
    assert ready(store,job)==(state=='prepared')
    assert not ready(store,other)
    assert dict(store.application_record(aid))==before


@pytest.mark.parametrize('change',['none','other_selected','different_card','not_adjacent','different_section','stale_degree'])
def test_required_degree_other_followup_requires_an_adjacent_confirmed_non_other_choice(store,job,package,change):
    card='11111111-1111-4111-8111-111111111111'
    job={**job,'host':'jobs.lever.co'}
    store.put_facts({'degree':'BS'})
    parent=field('Current degree',['BS','Other']);parent.update(section='Lever: Education',ref={'name':f'cards[{card}][field1]'})
    answer=resolve(store,job['host'],parent,NoModel(),context=job)
    follow=field('If you selected Other, please specify below:✱')
    follow.update(section='Lever: Education',ref={'name':f'cards[{card}][field2]'})
    if change=='other_selected':answer['value']='Other'
    elif change=='different_card':follow['ref']['name']=follow['ref']['name'].replace(card,'22222222-2222-4222-8222-222222222222')
    elif change=='not_adjacent':follow['ref']['name']=f'cards[{card}][field4]'
    elif change=='different_section':follow['section']='Lever: Other questions'
    elif change=='stale_degree':store.put_facts({'degree':'MS'})
    context={**job,'previous_answers':[answer]}
    if change!='none':
        with pytest.raises(Blocked):resolve(store,job['host'],follow,context=context)
        return
    result=resolve(store,job['host'],follow,NoModel(),context=context)
    assert result['value']=='Not applicable'
    package.update(answers=[answer,result],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)


def test_identical_lever_followup_wording_is_not_shared_between_native_card_fields(store,job):
    card='11111111-1111-4111-8111-111111111111';host='jobs.lever.co|acme'
    one=field('If you selected Other, please specify below:')
    one.update(section='Lever: Education',ref={'name':f'cards[{card}][field2]'})
    two={**one,'ref':{'name':f'cards[{card}][field6]'}}
    qid=store.ask(job['id'],host,one['label'],[],field=one,context=job)
    store.answer_question(qid,'Approved specific explanation')
    assert store.saved_answer(host,one['label'],[],field=one,context=job)
    assert store.saved_answer(host,two['label'],[],field=two,context=job) is None
