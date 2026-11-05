"""Regression evidence for productive batches, rather than attempt volume."""
import json
from pathlib import Path

import pytest

from hireme.answers import resolve
from hireme.job_holds import hold,ready,dependency,upgrade_information_holds
from hireme.policy import eligible,fit_score
from hireme.reports import queue_report
from hireme.util import Blocked,digest,now
from hireme.worker import cycle
from tests.test_answer_reuse import field


def test_internship_level_alone_never_establishes_technical_fit(store,job):
    unrelated={**job,'description':'The intern will learn new technologies and collaborate with the team.'}
    assert fit_score(unrelated,store.facts())[0]<store.settings()['min_fit_score']
    with pytest.raises(Blocked,match='low_fit'):eligible(unrelated,store.settings(),store.facts())
    assert eligible(job,store.settings(),store.facts())[1]==['python','typescript']


@pytest.mark.parametrize('title',[
    'Quantitative Research Intern (BS/MS) - Summer 2027',
    'Quantitative Trader/Researcher Intern - Summer 2027',
    'Software Defined Radio Hardware Intern (Summer 2027)',
    'Mechanical Engineering Research Intern Summer 2027',
    'Clinical Research Intern Summer 2027',
])
def test_observed_adjacent_roles_do_not_enter_a_software_search(store,job,title):
    with pytest.raises(Blocked,match='role_mismatch'):eligible({**job,'title':title},store.settings(),store.facts())


@pytest.mark.parametrize('title',['Software Engineer II','Software Developer 3','Research Scientist IV'])
def test_numbered_senior_levels_are_hard_exclusions(store,job,title):
    with pytest.raises(Blocked,match='seniority_mismatch'):eligible({**job,'title':title},store.settings(),store.facts())


def test_explicit_quantitative_search_remains_possible(store,job):
    store.update_settings({'roles':['quantitative research']})
    eligible({**job,'title':'Quantitative Research Intern Summer 2027'},store.settings(),store.facts())


@pytest.mark.parametrize('title',[
    'Software Engineering Intern (2027 Start) - Winter',
    'Software Intern Winter 2027',
    'Software Intern Spring 2027',
    'Software Intern Summer / Fall 2027',
])
def test_explicit_non_summer_title_wins_over_summer_program_body(store,job,title):
    store.update_settings({'seniority':['summer-internship','part-time']})
    candidate={**job,'title':title,'description':job['description']+' Learn more about our Summer 2027 program.'}
    with pytest.raises(Blocked,match='internship_out_of_scope'):eligible(candidate,store.settings(),store.facts())


@pytest.mark.parametrize('requirement,blocked',[
    ('You must have experience with Rust.',True),
    ('You must have experience with Python and Rust.',True),
    ('You must have experience with Python or Rust.',False),
    ('You must have experience with Python.',False),
    ('Rust experience is preferred.',False),
    ('Rust is not required.',False),
    ('You must have initiative to go beyond expectations.',False),
])
def test_explicit_technical_prerequisites_need_confirmed_support(store,job,requirement,blocked):
    candidate={**job,'description':job['description']+'\n'+requirement}
    if blocked:
        with pytest.raises(Blocked,match='technical_requirement_review'):eligible(candidate,store.settings(),store.facts())
        store.put_facts({'skills':'Python, TypeScript, Rust'})
    eligible(candidate,store.settings(),store.facts())


def test_required_fact_hold_ignores_unrelated_edits_and_identical_reconfirmation(store,job):
    f=field('When did you graduate from High School?')
    store.ask(job['id'],job['host'],f['label'],[],'missing_fact',field=f,context=job)
    hold(store,job,'missing_answers')
    store.put_facts({'phone':'5557654321','pronouns':'he/him'})
    assert not ready(store,job)
    store.put_facts({'high_school_graduation_year':'2025'})
    assert ready(store,job)
    hold(store,job,'missing_answers')
    store.put_facts({'high_school_graduation_year':'2025'})
    assert not ready(store,job)


def test_model_allowance_reset_cannot_release_missing_personal_fact(store,job):
    f=field('When did you graduate from High School?')
    store.ask(job['id'],job['host'],f['label'],[],'missing_fact',field=f,context=job)
    hold(store,job,'model_budget_exhausted')
    # An entirely unused new batch is already available; the personal fact is not.
    assert not ready(store,job)
    store.put_facts({'phone':'5557654321'})
    assert not ready(store,job)
    store.put_facts({'high_school_graduation_year':'2025'})
    assert ready(store,job)


def test_model_only_allowance_hold_can_resume_without_applicant_input(store,job):
    f=field('Tell us about a production service you built','textarea')
    store.ask(job['id'],job['host'],f['label'],[],'model_budget_exhausted',field=f,context=job)
    hold(store,job,'model_budget_exhausted')
    assert ready(store,job)


def test_unchanged_legacy_information_hold_upgrades_without_another_browser_attempt(store,job):
    f=field('When did you graduate from High School?')
    store.ask(job['id'],job['host'],f['label'],[],'missing_fact',field=f,context=job)
    hold(store,job,'missing_answers')
    store.db.execute('UPDATE job_holds SET dependency=?,evidence=? WHERE job_id=?',
        (dependency(store,job,'information','missing_answers',legacy_information=True),'{}',job['id']))
    upgrade_information_holds(store)
    assert not ready(store,job)
    store.put_facts({'phone':'5557654321'})
    assert not ready(store,job)
    store.put_facts({'high_school_graduation_year':'2025'})
    assert ready(store,job)


def test_unknown_declaration_does_not_spend_a_guaranteed_useless_fact_matching_request(store,job):
    class Model:
        def match_field(self,*args):pytest.fail('No executor-compatible facts or templates exist')
    with pytest.raises(Blocked,match='missing_fact'):
        resolve(store,job['host'],field('Which active security clearance do you currently hold?'),Model(),context=job)
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_rejected_writing_is_not_regenerated_until_approved_sources_change(store,job):
    store.update_settings({'tailored_writing':True})
    store.put_template('project','I built a Python service.')
    class Model:
        calls=0
        def draft_answer(self,*args):
            self.calls+=1
            return {'answer':'','sentence_ids':[],'rejected':'No supported outcome metric for the requested example'}
    model=Model();f=field('Describe a project and its measurable outcome','textarea')
    for _ in range(2):
        with pytest.raises(Blocked,match='writing_unsupported'):resolve(store,job['host'],f,model,context=job)
    assert model.calls==1
    store.put_template('project','The service reduced a measured task from ten minutes to two minutes.')
    with pytest.raises(Blocked,match='writing_unsupported'):resolve(store,job['host'],f,model,context=job)
    assert model.calls==2


def test_confirmed_experience_ranks_matching_work_above_incidental_keyword_volume(store,job):
    store.put_history_entry('employment',{'organization':'Synthetic Factory','title':'Software Engineer',
        'description':'Built backend APIs for production manufacturing systems.'},source='applicant',confirmed=True)
    other={**job,'id':digest('frontend'),'url':job['url']+'-frontend','title':'Frontend Software Intern Summer 2027'}
    store.upsert_job(other)
    relevant={**job,'description':job['description']+' Backend APIs for production manufacturing systems.'}
    store.upsert_job(relevant)
    visited=[]
    class Browser:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,item,live=True):visited.append(item['id']);raise Blocked('missing_answers')
    cycle(store,Path.cwd(),discover=False,browser_factory=Browser,max_attempts=1)
    assert visited==[job['id']]
    evidence=json.loads(store.db.execute("SELECT detail FROM events WHERE kind='application_started'").fetchone()[0])['quality']
    assert evidence['experience'][0]['domains']==['backend','production systems','manufacturing software']


def test_success_without_durable_confirmation_is_never_counted_or_retried(store,job):
    class Browser:
        calls=0
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,*args,**kwargs):Browser.calls+=1;return 'confirmed'
    for _ in range(2):
        result=cycle(store,Path.cwd(),discover=False,browser_factory=Browser)
        assert result['confirmed']==0
    assert Browser.calls==1
    assert store.db.execute('SELECT reason FROM job_holds').fetchone()[0]=='confirmation_missing'


def test_late_day_shortfall_does_not_increase_the_configured_completion_target(store,job,package,monkeypatch):
    from datetime import datetime
    class LateDay(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,1,1,23,tzinfo=tz)
    monkeypatch.setattr('hireme.worker.datetime',LateDay)
    for i in range(12):
        item={**job,'id':digest(i),'url':job['url']+str(i),'company':'Company '+str(i)}
        store.upsert_job(item)
    class Browser:
        def __init__(self,s):self.store=s
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,item,live=True):
            aid=self.store.prepare(item,{**package,'job_id':item['id'],'url':item['url']})
            self.store.begin_submit(aid);self.store.finish(aid,'confirmed','Synthetic employer receipt')
            return 'confirmed'
    result=cycle(store,Path.cwd(),discover=False,browser_factory=Browser)
    assert result['target']==store.settings()['target_per_cycle']==7
    assert result['confirmed']==result['attempts']==7


def test_report_leads_with_completed_work_and_preserves_receipt_version(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed','Synthetic employer receipt')
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',('quality-run','2020-01-01T00:00:00Z',now(),'finished',1,json.dumps({'attempts':7})))
    store.event('application_finished',job['id'],{'run_id':'quality-run','outcome':'confirmed'})
    queue_report(store,'quality-run')
    row=store.db.execute('SELECT subject,body FROM report_outbox').fetchone()
    assert row['body'].startswith('Confirmed submissions: 1\nHuman interventions: 0\nUnconfirmed results to verify: 0')
    assert aid in row['body'] and package['documents'][0]['hash'] in row['body']
    assert 'Confirmed at:' in row['body'] and 'do not apply again' in row['body']
    assert row['body'].index('Applied successfully')<row['body'].index('Browser sessions started: 7')
    assert 'confirmed' in row['subject'] and '7' not in row['subject']


def test_recruiting_report_lists_only_reviewed_owner_bound_latest_stages(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed')
    owner=digest('test@candidate.invalid')
    for i,(account,state,category,received) in enumerate([
        (owner,'confirmed','assessment','2026-01-01'),(owner,'confirmed','interview','2026-01-02'),
        (owner,'pending','assessment','2026-01-03'),(digest('other@candidate.invalid'),'confirmed','assessment','2026-01-04')]):
        store.db.execute('INSERT INTO recruiting_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (str(i),account,str(i),received,'sender','private subject','private excerpt',category,'[]','test',1,state,aid,1,received,received))
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',('stages','2026-01-01',now(),'finished',0,'{}'))
    queue_report(store,'stages')
    body=store.db.execute('SELECT body FROM report_outbox').fetchone()[0]
    assert 'Interviews — prepare or update status' in body and 'Assessments — check status' not in body
    assert 'Employer messages awaiting your review: 1' in body
    assert 'private subject' not in body and 'private excerpt' not in body
