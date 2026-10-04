import json
import pytest
from hireme.answers import resolve
from hireme.field_context import binding, save_binding
from hireme.job_holds import ready, hold, recheck
from hireme.coverage import coverage, cycle_funnel
from hireme.util import Blocked

def field(label='Choose your university',options=None,**extra):
    return {'label':label,'type':'select' if options else 'text','options':options or [],'required':True,'maxlength':-1,**extra}

class Model:
    calls=0
    def match_field(self,*args):self.calls+=1;return {'fact_key':'school','template_id':None}

def test_invalid_option_never_poisoned_binding(store,job):
    store.put_facts({'school':'Confirmed University'})
    f=field(options=['Different university']);m=Model()
    with pytest.raises(Blocked,match='option_mismatch'):resolve(store,job['host'],f,m,context=job)
    assert store.db.execute('SELECT COUNT(*) FROM field_bindings_v2').fetchone()[0]==0

def test_binding_scope_reordered_options_revisions_and_sections(store,job):
    store.put_facts({'school':'Confirmed University'})
    f=field(options=['Confirmed University','Other'],option_values=['a','b']);m=Model()
    resolve(store,job['host'],f,m,context=job)
    reordered={**f,'options':list(reversed(f['options'])),'option_values':['b','a']}
    assert resolve(store,job['host'],reordered,context=job)['value']=='Confirmed University'
    assert binding(store,job['host'],{**f,'section':'employment'},job) is None
    assert binding(store,job['host'],f,{**job,'company':'Other'}) is None
    assert binding(store,job['host'],{**f,'option_values':['x','b']},job) is None
    store.put_facts({'school':'Other'})
    assert binding(store,job['host'],f,job) is None

def test_phone_code_does_not_infer_country(store,job):
    store.put_facts({'phone':'+12025550123','location':'Toronto, Ontario'})
    assert resolve(store,job['host'],field('Phone country code'))['value']=='+1'
    with pytest.raises(Blocked):resolve(store,job['host'],field('Country',options=['United States','Canada']))

def test_ambiguous_alias_options_block(store,job):
    store.put_facts({'state':'CA'})
    with pytest.raises(Blocked):resolve(store,job['host'],field('State',options=['California','CA']))

def test_hold_releases_on_facts_answers_documents_or_rules(store,job):
    hold(store,job,'missing_fact','Unknown answer')
    assert not ready(store,job)
    assert recheck(store,job['id'])['ready'] is False
    store.put_facts({'school':'Confirmed University'})
    assert ready(store,job)
    assert recheck(store,job['id'])['ready'] is True

@pytest.mark.parametrize('reason',['navigation_failed','provider_timeout'])
def test_transient_retry_only_two_delayed_retries(store,job,reason):
    hold(store,job,reason,at=1000)
    assert not ready(store,job,at=2799)
    assert ready(store,job,at=2800)
    hold(store,job,reason,at=2800)
    assert not ready(store,job,at=9999)
    assert ready(store,job,at=10000)
    hold(store,job,reason,at=10000)
    assert not ready(store,job,at=999999)

@pytest.mark.parametrize('state',['unknown','submitting','awaiting_verification','confirmed','not_submitted'])
def test_recheck_never_releases_existing_outcome(store,job,package,state):
    store.prepare(job,package)
    store.db.execute('UPDATE applications SET state=?',(state,))
    assert not ready(store,job)
    with pytest.raises(ValueError):recheck(store,job['id'])

def test_unknown_reason_requires_review_and_adapter_hold_only_version(store,job):
    hold(store,job,'new_unknown_reason')
    store.put_facts({'school':'Confirmed University'})
    assert not ready(store,job)
    hold(store,job,'multi_step_requires_adapter')
    store.put_facts({'school':'Another'})
    assert not ready(store,job)

def test_coverage_counts_attempts_before_submit_and_workday_limits(store,job):
    from hireme.discovery import posting
    store.upsert_job(posting('https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Test_R1','Nvidia','Software engineer intern','United States','portal:Nvidia'))
    store.event('application_started',job['id'],{'run_id':'r'})
    result=coverage(store)
    group=next(g for g in result['groups'] if g['source']=='test')
    assert group['attempted']==1 and group['confirmed']==0
    workday=[g for g in result['groups'] if g['ats']=='Workday']
    assert len(workday)==5 and all(g['account_required'] and g['application_capability']=='manual completion' for g in workday)
    nvidia=next(g for g in workday if g['source']=='portal:Nvidia')
    assert nvidia['configured'] and nvidia['discovered']==1
    assert not group['configured']

def test_funnel_deduplicates_run_attempts_and_keeps_screening_separate(store,job):
    from hireme.util import now
    store.db.execute('INSERT INTO runs(id,started,status,detail) VALUES(?,?,?,?)',('r',now(),'finished',json.dumps({'eligible':1,'discovered':3})))
    for _ in range(2):store.event('application_started',job['id'],{'run_id':'r'})
    store.event('application_finished',job['id'],{'run_id':'r','outcome':'blocked'})
    store.event('job_screening_blocked','different',{'run_id':'r'})
    f=cycle_funnel(store)
    assert f['attempted']==1 and f['blocked']==1 and f['screened_out']==1 and f['eligible']==1


def test_missing_answers_releases_after_question_answer_without_repeating_browser(store,job):
    q=store.ask(job['id'],job['host'],'A required question',[], 'missing_fact')
    hold(store,job,'missing_answers','A required question')
    assert not ready(store,job)
    store.answer_question(q,'Confirmed answer')
    assert ready(store,job)

def test_document_repair_releases_same_hash_hold(store,job):
    path=store.root/'documents'/store.db.execute('SELECT filename FROM documents LIMIT 1').fetchone()[0]
    data=path.read_bytes();path.unlink()
    hold(store,job,'document_tampered')
    assert not ready(store,job)
    path.write_bytes(data)
    assert ready(store,job)

def test_model_cannot_bind_employment_date_to_education(store,job):
    store.put_facts({'college_start':'2025-08'})
    class Wrong:
        def match_field(self,*args):return {'fact_key':'college_start','template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field('Start date year',section='employment'),Wrong(),context=job)
    assert store.db.execute('SELECT COUNT(*) FROM field_bindings_v2').fetchone()[0]==0


def test_browser_updated_posting_does_not_trigger_spurious_retry(store,job):
    fresh={**job,'description':'Employer page checked with full description','answer_scope':job['host']+'|acme'}
    store.upsert_job(fresh)
    hold(store,job,'missing_answers','Required field')
    assert not ready(store,fresh)

def test_worker_skips_unchanged_hold_without_consuming_attempt(store,job):
    from pathlib import Path
    from hireme.worker import cycle
    class Browser:
        calls=0
        def __init__(self,store):self.store=store
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,job,live=True):
            Browser.calls+=1
            raise Blocked('missing_answers','Known missing answer')
    first=cycle(store,Path.cwd(),discover=False,browser_factory=Browser,limit=1,max_attempts=1)
    second=cycle(store,Path.cwd(),discover=False,browser_factory=Browser,limit=1,max_attempts=1)
    assert first['attempts']==1 and second['attempts']==0 and Browser.calls==1
    store.put_facts({'school':'Confirmed University'})
    third=cycle(store,Path.cwd(),discover=False,browser_factory=Browser,limit=1,max_attempts=1)
    assert third['attempts']==1 and Browser.calls==2


def test_named_employer_rule_handles_spaces_but_not_general_employment(store,job):
    store.put_facts({'worked_outside_resume':'No'})
    context={**job,'company':'Akuna Capital'}
    assert resolve(store,job['host']+'|akunacapital',field('Have you ever worked at Akuna Capital?',options=['Yes','No']),context=context)['value']=='No'
    with pytest.raises(Blocked):resolve(store,job['host']+'|akunacapital',field('Have you previously been employed?',options=['Yes','No']),context=context)


def test_unchanged_discovery_refresh_preserves_hold_but_posting_change_releases(store,job):
    store.upsert_job({**job,'description':'Live form text and navigation','answer_scope':job['host']+'|acme'})
    hold(store,job,'missing_answers','Required field')
    store.upsert_job(job)
    assert not ready(store,job)
    changed={**job,'description':'Changed employer requirements in the discovery feed'}
    store.upsert_job(changed)
    assert ready(store,changed)


def test_report_uses_latest_outcome_once_and_excludes_screening(store,job):
    from hireme.reports import queue_report
    from hireme.util import now
    store.db.execute('INSERT INTO runs(id,started,finished,status,detail) VALUES(?,?,?,?,?)',('report-r',now(),now(),'finished',json.dumps({'attempts':1})))
    store.event('application_finished',job['id'],{'run_id':'report-r','outcome':'blocked','reason':'missing_answers'})
    store.event('application_finished',job['id'],{'run_id':'report-r','outcome':'confirmed'})
    other={**job,'id':'other-job','url':job['url']+'other','company':'Other'};store.upsert_job(other)
    store.event('job_screening_blocked',other['id'],{'run_id':'report-r','outcome':'blocked'})
    queue_report(store,'report-r')
    body=store.db.execute('SELECT body FROM report_outbox WHERE id=?',('report-r',)).fetchone()[0]
    assert body.count(job['url'])==1 and other['url'] not in body and 'missing_answers' not in body


def test_review_questions_and_manual_approvals_are_section_scoped(store,job):
    employment=field('End date year',section='employment')
    education=field('End date year',section='education')
    first=store.ask(job['id'],job['host'],employment['label'],[],field=employment,context=job)
    second=store.ask(job['id'],job['host'],education['label'],[],field=education,context=job)
    assert first!=second
    store.answer_question(first,'2023')
    assert resolve(store,job['host'],employment,context=job)['value']=='2023'
    assert resolve(store,job['host'],education,context=job)['value']=='2028'
    store.db.execute('UPDATE questions SET resolved=0 WHERE id=?',(first,))
    resolve(store,job['host'],education,context=job)
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(first,)).fetchone()[0]==0

def test_ambiguous_legacy_literal_answer_requires_section_review(store,job):
    f=field('End date year',section='education')
    q=store.ask(job['id'],job['host'],f['label'],[]);store.answer_question(q,'2023')
    with pytest.raises(Blocked,match='stale_answer'):resolve(store,job['host'],f,context=job)

def test_contextual_approved_answer_does_not_transfer_widget_or_employer(store,job):
    f=field('A personal response')
    q=store.ask(job['id'],job['host'],f['label'],[],field=f,context=job)
    store.answer_question(q,'Reviewed response')
    assert resolve(store,job['host'],f,context=job)['value']=='Reviewed response'
    with pytest.raises(Blocked):resolve(store,job['host'],{**f,'type':'textarea'},context=job)
    with pytest.raises(Blocked):resolve(store,job['host'],f,context={**job,'company':'Other employer'})


def test_role_location_invalidate_cached_bindings_and_approvals(store,job):
    store.put_facts({'school':'Confirmed University'})
    f=field();resolve(store,job['host'],f,Model(),context=job)
    assert binding(store,job['host'],f,{**job,'title':'Intern Summer 2028'}) is None
    assert binding(store,job['host'],f,{**job,'location':'London'}) is None

def test_foreign_authorization_cannot_reuse_us_fact(store,job):
    class Wrong:
        def match_field(self,*args):return {'fact_key':'work_authorized_us','template_id':None}
    f=field('Are you legally authorized to work in Canada?',options=['Yes','No'])
    with pytest.raises(Blocked):resolve(store,job['host'],f,Wrong(),context={**job,'location':'Toronto, Canada'})
    assert store.db.execute('SELECT COUNT(*) FROM field_bindings_v2').fetchone()[0]==0


def test_legacy_literal_needs_context_or_matching_confirmed_fact(store,job):
    question=field('A personal response')
    q=store.ask(job['id'],job['host'],question['label'],[]);store.answer_question(q,'Older reviewed response')
    with pytest.raises(Blocked,match='stale_answer'):resolve(store,job['host'],question,context=job)
    email=field('Email');q=store.ask(job['id'],job['host'],email['label'],[]);store.answer_question(q,store.facts()['email']['value'])
    assert resolve(store,job['host'],email,context=job)['value']==store.facts()['email']['value']

def test_ten_digit_phone_constraint_uses_confirmed_national_component(store,job):
    store.put_facts({'phone':'+12025550123'})
    assert resolve(store,job['host'],field('Phone',maxlength=10))['value']=='2025550123'

@pytest.mark.parametrize('key,value',[('email','owner@candidate.test'),('phone','+12025550123'),('school','Confirmed University'),('college_start','2025-08'),('onsite','Yes'),('summer_2027_available','Yes')])
def test_existing_but_unrelated_fact_never_authorizes_model_answer(store,job,key,value):
    store.put_facts({key:value})
    class Wrong:
        def match_field(self,*args):return {'fact_key':key,'template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field('Have you published five research papers?',options=['Yes','No']),Wrong(),context=job)
    assert store.db.execute('SELECT COUNT(*) FROM field_bindings_v2').fetchone()[0]==0

def test_writing_template_cannot_answer_factual_question(store,job):
    tid=store.put_template('project','An approved project description.')
    class Wrong:
        def match_field(self,*args):return {'fact_key':None,'template_id':tid}
    with pytest.raises(Blocked):resolve(store,job['host'],field('How many years have you worked at NASA?'),Wrong(),context=job)
    assert store.db.execute('SELECT COUNT(*) FROM field_bindings_v2').fetchone()[0]==0

def test_changed_constraints_invalidate_contextual_binding(store,job):
    store.put_facts({'school':'Confirmed University'})
    f=field();resolve(store,job['host'],f,Model(),context=job)
    for constraint,value in [('required',False),('pattern','[A-Z]+'),('min','1'),('max','10'),('step','1'),('multiple',True)]:
        assert binding(store,job['host'],{**f,constraint:value},job) is None

@pytest.mark.parametrize('label,key',[('Are you a United States citizen?','us_person'),('Are you authorized to work in Spain?','work_authorized_us')])
def test_related_legal_status_never_substitutes_for_requested_status(store,job,label,key):
    store.put_facts({key:'Yes'})
    class Wrong:
        def match_field(self,*args):return {'fact_key':key,'template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field(label,options=['Yes','No']),Wrong(),context=job)
    assert store.db.execute('SELECT COUNT(*) FROM field_bindings_v2').fetchone()[0]==0

@pytest.mark.parametrize('label,key,value',[
 ('This role is based out of our San Jose, CA office. Are you willing and able to accommodate this work environment?*','onsite','Yes'),
 ('Are you eligible to work in the country where this vacancy is posted?*','work_authorized_us','Yes'),
 ('Please enter the ZIP code of your primary residence in the United States.','postal_code','94704'),
 ('To be eligible for this position, your primary residence must be located in the United States. Please indicate the state in which you currently reside.*','state','CA'),
 ('Have you previously worked for Veeam (including as a contractor or intern)? Current Veeam employees are required to apply through the Internal Job Board.*','worked_outside_resume','No')])
def test_real_veeam_wording_uses_confirmed_fact_without_model(store,job,label,key,value):
    store.put_facts({key:value})
    context={**job,'company':'Veeam Software','location':'San Jose, CA'}
    answer=resolve(store,job['host']+'|veeamsoftware',field(label),context=context)
    assert answer['value']==value and answer['provenance']['fact_key']==key

@pytest.mark.parametrize('location',['San Jose, Costa Rica','Toronto, Canada','San Jose','Berlin, Germany'])
def test_scoped_work_eligibility_needs_unambiguous_us_posting(store,job,location):
    store.put_facts({'work_authorized_us':'Yes'})
    with pytest.raises(Blocked):resolve(store,job['host'],field('Are you eligible to work in the country where this vacancy is posted?'),context={**job,'location':location})

@pytest.mark.parametrize('prior',['Veeam','Veeam Software'])
def test_short_employer_name_does_not_override_prior_work_history(store,job,prior):
    store.put_facts({'worked_outside_resume':'No'});store.update_settings({'prior_employers':[prior]})
    with pytest.raises(Blocked):resolve(store,job['host']+'|veeamsoftware',field('Have you previously worked for Veeam?'),context={**job,'company':'Veeam Software'})

def test_model_allowance_hold_waits_for_reset_or_approved_answer(store,job):
    import time
    store.update_settings({'max_model_requests_per_day':1,'max_model_requests_per_cycle':1})
    store.reserve_model_request()
    f=field('An unanswered required fact')
    q=store.ask(job['id'],job['host'],f['label'],[],field=f,context=job)
    hold(store,job,'model_budget_exhausted',f['label'])
    assert not ready(store,job)
    assert ready(store,job,at=time.time()+86400)
    store.answer_question(q,'Owner approved value')
    assert ready(store,job)
    assert store.db.execute('SELECT count(*) FROM model_requests').fetchone()[0]==1

def test_quota_block_records_hold_and_preserves_funnel_without_repeated_attempt(store,job):
    from hireme.worker import cycle
    from pathlib import Path
    store.update_settings({'max_model_requests_per_day':1,'max_model_requests_per_cycle':1});store.reserve_model_request()
    calls=[]
    class Browser:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,*args,**kwargs):calls.append(job['id']);raise Blocked('model_budget_exhausted','An unanswered required fact')
    with pytest.raises(Blocked,match='model_budget_exhausted'):cycle(store,Path('.'),discover=False,limit=1,browser_factory=Browser)
    run=store.db.execute('SELECT detail FROM runs ORDER BY started DESC LIMIT 1').fetchone()
    detail=json.loads(run[0]);assert detail['eligible']==1 and detail['discovered']==0
    assert not ready(store,job)
    result=cycle(store,Path('.'),discover=False,limit=1,browser_factory=Browser)
    assert result['attempts']==0 and calls==[job['id']]

@pytest.mark.parametrize('key,label',[('worked_outside_resume','Have you previously worked for Veeam?'),('contacts_outside_resume','Do you know anyone at Veeam?')])
def test_positive_general_history_does_not_prove_specific_employer_history(store,job,key,label):
    store.put_facts({key:'Yes'})
    with pytest.raises(Blocked):resolve(store,job['host']+'|veeamsoftware',field(label),context={**job,'company':'Veeam Software'})

def test_fact_linked_negative_history_revalidates_prior_employer_changes(store,job):
    host=job['host']+'|veeamsoftware';context={**job,'company':'Veeam Software'};f=field('Have you previously worked for Veeam?')
    store.put_facts({'worked_outside_resume':'No'})
    q=store.ask(job['id'],host,f['label'],[],field=f,context=context);store.answer_question(q,'No','worked_outside_resume')
    assert resolve(store,host,f,context=context)['value']=='No'
    store.update_settings({'prior_employers':['Veeam']})
    with pytest.raises(Blocked,match='stale_answer'):resolve(store,host,f,context=context)

@pytest.mark.parametrize('constraints',[{'min':'3'},{'max':'2'},{'step':'1'},{'min':'NaN'}])
def test_numeric_constraints_reject_answer_before_binding_persistence(store,job,constraints):
    store.put_facts({'professional_years':'2.5'})
    class Model:
        def match_field(self,*args):return {'fact_key':'professional_years','template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field('Provide your years of professional experience',type='number',**constraints),Model(),context=job)
    assert store.db.execute('SELECT count(*) FROM field_bindings_v2').fetchone()[0]==0

def test_native_step_base_and_decimal_precision_are_respected(store,job):
    store.put_facts({'professional_years':'2.5'})
    class Model:
        def match_field(self,*args):return {'fact_key':'professional_years','template_id':None}
    answer=resolve(store,job['host'],field('Provide your years of professional experience',type='number',step='1',step_base='0.5'),Model(),context=job)
    assert answer['value']=='2.5'

@pytest.mark.parametrize('widget,options,expected',[('number',[],'8'),('select',['Jan','Aug'],'Aug'),('select',['01','08'],'08')])
def test_education_month_components_match_numeric_and_abbreviated_widgets(store,job,widget,options,expected):
    store.put_facts({'college_start':'2025-08'})
    f=field('Start date month',type=widget,options=options,section='education')
    assert resolve(store,job['host'],f,context=job)['value']==expected

def test_text_prefill_value_does_not_change_binding_meaning(store,job):
    store.put_facts({'school':'Confirmed University'})
    f=field();resolve(store,job['host'],f,Model(),context=job)
    assert resolve(store,job['host'],{**f,'step_base':'Unrelated prefill'},context=job)['value']=='Confirmed University'


@pytest.mark.parametrize('submitted',[True,False])
def test_latest_funnel_reflects_review_without_rewriting_the_attempt(store,job,package,submitted):
    from hireme.util import now
    store.db.execute('INSERT INTO runs(id,started,status,detail) VALUES(?,?,?,?)',('review-run',now(),'finished','{}'))
    store.event('application_started',job['id'],{'run_id':'review-run'})
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.event('application_finished',job['id'],{'run_id':'review-run','outcome':'unknown'})
    assert cycle_funnel(store)['uncertain']==1
    store.reconcile(aid,submitted,'Inspected the employer outcome manually')
    result=cycle_funnel(store)
    assert result['attempted']==1 and result['reconciled']==1 and result['uncertain']==0
    assert result['confirmed']==int(submitted) and result['not_submitted']==int(not submitted)
    assert result['recorded_outcomes']['unknown']==1 and result['recorded_outcomes']['confirmed']==0
    original=json.loads(store.db.execute("SELECT detail FROM events WHERE kind='application_finished'").fetchone()[0])
    assert original['outcome']=='unknown'


def test_funnel_does_not_credit_a_different_jobs_reconciliation(store,job,package):
    from hireme.util import now
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.db.execute('INSERT INTO runs(id,started,status,detail) VALUES(?,?,?,?)',('other-run',now(),'finished','{}'))
    store.event('application_started','different-job',{'run_id':'other-run'})
    store.reconcile(aid,True,'Inspected receipt from the older unrelated application')
    result=cycle_funnel(store)
    assert result['attempted']==1 and result['confirmed']==0 and result['reconciled']==0


@pytest.mark.parametrize('finished',[True,False])
def test_funnel_does_not_reuse_review_from_before_the_current_attempt(store,job,package,finished):
    from hireme.util import now
    store.db.execute('INSERT INTO runs(id,started,status,detail) VALUES(?,?,?,?)',('new-run',now(),'finished','{}'))
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.reconcile(aid,True,'A prior attempt had its receipt inspected')
    store.event('application_started',job['id'],{'run_id':'new-run'})
    if finished:store.event('application_finished',job['id'],{'run_id':'new-run','outcome':'blocked'})
    result=cycle_funnel(store)
    assert result['blocked']==int(finished) and result['confirmed']==0 and result['reconciled']==0
