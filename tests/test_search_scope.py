import pytest
from pathlib import Path
from hireme.policy import eligible,employment_kind
from hireme.util import Blocked,digest
from hireme.worker import cycle

@pytest.mark.parametrize('title,description',[
 ('Software Engineer New Grad','Previous internship experience required.'),
 ('Summer 2027 Software Engineer - New Grad','Our internship program develops engineers.'),
 ('Member of Technical Staff (Early Career - Industry)','Internship experience is valued.'),
 ('Software Engineer','Mentor an intern and lead our internship program.'),
])
def test_internship_search_excludes_incidental_description_mentions(store,job,title,description):
    store.update_settings({'seniority':['internship','part-time','summer-internship']})
    with pytest.raises(Blocked,match='fulltime_out_of_scope'):
        eligible({**job,'title':title,'description':description},store.settings(),store.facts())

@pytest.mark.parametrize('changes,kind',[
 ({'title':'Software Engineer Intern','employment_type':'FullTime'},'internship'),
 ({'title':'Part-Time Software Developer'},'part-time'),
 ({'title':'Research Software Engineer','employment_type':'PartTime'},'part-time'),
 ({'title':'Software Engineer','description':'This position is a part-time role.'},'part-time'),
 ({'title':'Software Engineer','description':'This role is an internship for students.'},'internship'),
 ({'title':'Software Engineer','source':'simplify:internships'},'internship'),
 ({'title':'Software Engineer New Grad','source':'simplify:internships'},'new-grad'),
])
def test_role_type_uses_advertised_title_and_structured_context(job,changes,kind):
    assert employment_kind({**job,**changes})==kind


def test_part_time_scope_and_hourly_minimum(store,job):
    j={**job,'title':'Software Engineer Part-Time','compensation':{'currency':'USD','period':'hour','min':30}}
    with pytest.raises(Blocked,match='parttime_out_of_scope'):eligible(j,store.settings(),store.facts())
    store.update_settings({'seniority':['part-time'],'min_hourly_usd':25,'min_annual_usd':100000})
    eligible(j,store.settings(),store.facts())
    store.update_settings({'min_hourly_usd':35})
    with pytest.raises(Blocked,match='compensation_mismatch'):eligible(j,store.settings(),store.facts())


def test_summer_only_scope_does_not_enable_full_time_or_winter(store,job):
    store.update_settings({'seniority':['summer-internship']})
    eligible(job,store.settings(),store.facts())
    with pytest.raises(Blocked,match='internship_out_of_scope'):
        eligible({**job,'title':'Software Engineer Intern Winter 2027'},store.settings(),store.facts())
    with pytest.raises(Blocked,match='fulltime_out_of_scope'):
        eligible({**job,'title':'Software Engineer New Grad Summer 2027'},store.settings(),store.facts())

@pytest.mark.parametrize('state,confirmation,reason',[
 ('unknown','','company_uncertain'),
 ('awaiting_verification','','company_verification_pending'),
 ('not_submitted','Your application submission was flagged as possible spam.','company_submission_rejected'),
])
def test_company_hold_prevents_browser_and_model_even_through_alias(store,job,package,state,confirmation,reason):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,state,confirmation)
    store.update_settings({'company_aliases':{'Acme':'Acme Technologies'}})
    other={**job,'id':digest('another-job'),'url':job['url']+'-other','company':'Acme Technologies'}
    store.upsert_job(other)
    class ForbiddenBrowser:
        def __init__(self,*args):pytest.fail('Company held before opening browser')
    result=cycle(store,Path.cwd(),discover=False,browser_factory=ForbiddenBrowser)
    assert result['attempts']==0 and result['reasons'][reason]==1
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    assert store.application_record(aid)['state']==state


def test_new_grad_false_positive_never_reaches_browser(store,job):
    store.update_settings({'seniority':['internship','part-time','summer-internship']})
    store.upsert_job({**job,'title':'Software Engineer New Grad','description':'Python TypeScript. Prior internship desirable.'})
    class ForbiddenBrowser:
        def __init__(self,*args):pytest.fail('Full-time role reached browser')
    result=cycle(store,Path.cwd(),discover=False,browser_factory=ForbiddenBrowser)
    assert result['attempts']==0 and result['reasons']=={'fulltime_out_of_scope':1}


def test_discovery_question_uses_actual_board_source_and_revalidates(store,job,package):
    from hireme.answers import resolve,validate_package
    from tests.test_answer_reuse import field
    j={**job,'company':'Gallup','source':'gh:gallup'}
    store.upsert_job(j)
    f=field('What led you to apply for this opportunity at Gallup? Select all that apply.','checkbox',['LinkedIn','Gallup Careers website','Other'])
    answer=resolve(store,j['host'],f,context=j)
    assert answer['value']=='Gallup Careers website' and answer['provenance']=={'job_source':'gh:gallup'}
    p={**package,'answers':[answer],'steps':[{'fields':[f]}]}
    validate_package(store,j,p)
    with pytest.raises(Blocked,match='unsupported_or_stale_answer'):
        validate_package(store,{**j,'source':'simplify:internships'},p)


@pytest.mark.parametrize('ats,data',[
 ('ash',{'jobs':[{'jobUrl':'https://jobs.ashbyhq.com/acme/part','title':'Software Engineer','location':'San Francisco','employmentType':'PartTime'}]}),
 ('lv',[{'hostedUrl':'https://jobs.lever.co/acme/part','text':'Software Engineer','categories':{'location':'San Francisco','commitment':'Part-time'}}]),
])
def test_discovery_retains_employment_type(ats,data):
    from hireme.discovery import probe
    class Network:
        def json(self,url):return data
    jobs=probe(Network(),ats,'acme')
    assert len(jobs)==1 and employment_kind(jobs[0])=='part-time'


def test_excluded_role_questions_leave_action_queue_without_erasing_evidence(store,job):
    from hireme.ledger import summary
    from hireme.question_ledger import search_questions
    store.ask(job['id'],job['host'],'Current education status',['Student','Graduate'],'option_mismatch')
    assert summary(store)['question_count']==1
    original=[dict(row) for row in store.db.execute('SELECT * FROM questions')]
    store.block(job['id'],'fulltime_out_of_scope')
    assert summary(store)['question_count']==0 and summary(store)['attention_count']==0
    assert search_questions(store)['total']==0 and store.snapshot()['questions']==[]
    assert [dict(row) for row in store.db.execute('SELECT * FROM questions')]==original
    store.db.execute("UPDATE jobs SET status='discovered',reason='' WHERE id=?",(job['id'],))
    assert summary(store)['question_count']==1 and search_questions(store)['total']==1
    assert len(store.snapshot()['questions'])==1


@pytest.mark.parametrize('title',[
 'Backend Engineering Intern (2027 Summer Internship)',
 'Full Stack Engineering Intern (2027 Summer Internship)',
 'Fullstack Engineer Intern - Product Team',
 'Frontend Engineer Intern - Product & UX/UI',
 'AI/SWE Intern',
])
def test_software_scope_accepts_observed_standard_title_variants(store,job,title):
    eligible({**job,'title':title},store.settings(),store.facts())


def test_reverse_season_year_respects_availability(store,job):
    store.put_facts({'earliest_start':'2027-05'})
    store.update_settings({'seniority':['summer-internship']})
    eligible({**job,'title':'Software Intern (2027 Summer)'},store.settings(),store.facts())
    store.update_settings({'seniority':['internship']})
    with pytest.raises(Blocked,match='start_window_mismatch'):
        eligible({**job,'title':'Software Intern (2026 Summer)'},store.settings(),store.facts())


def test_live_hourly_rate_and_veteran_variants_reuse_confirmed_context(store,job,package):
    from hireme.answers import resolve,validate_package,category
    from tests.test_answer_reuse import field
    store.put_facts({'salary':'30/hr','veteran':'I am not a veteran'})
    f=field('Please indicate your hourly rate requirement:','number');f.update(min='0',step='any')
    a=resolve(store,job['host'],f,context=job)
    assert a['value']=='30' and a['provenance']['fact_key']=='salary'
    v=resolve(store,job['host'],field('Veteran Status*','select',['I am not a protected veteran','I identify as one or more of the classifications of a protected veteran',"I don't wish to answer"]),context=job)
    assert v['value']=='I am not a protected veteran'
    validate_package(store,job,{**package,'facts_hash':digest(store.facts()),'answers':[*package['answers'],a,v]})
    store.put_facts({'salary':'100000/year'})
    with pytest.raises(Blocked,match='missing_fact'):resolve(store,job['host'],f,context=job)
    assert category('What are your career plans?')=='motivation'
    assert category('What are your career aspirations?')=='motivation'


def test_named_campus_radius_uses_confirmed_enrollment_and_revalidates(store,job,package,monkeypatch):
    from hireme.answers import resolve,validate_package
    from tests.test_answer_reuse import field
    monkeypatch.setattr('hireme.util.now',lambda:'2026-10-04T12:00:00+00:00')
    store.put_facts({'school':'University of California, Berkeley','college_start':'2025-08','graduation':'2028-05'})
    f=field('Do you currently attend a college or university within an 80-mile radius of San Francisco, CA? *','select',['Yes','No'])
    a=resolve(store,job['host'],f,context=job)
    assert a['value']=='Yes' and a['provenance']['campus_radius']['school_revision']==store.facts()['school']['revision']
    validate_package(store,job,{**package,'facts_hash':digest(store.facts()),'answers':[*package['answers'],a]})
    store.put_facts({'school':'Different University'})
    with pytest.raises(Blocked):validate_package(store,job,{**package,'facts_hash':digest(store.facts()),'answers':[*package['answers'],a]})
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_same_school_and_degree_synonyms_choose_known_equivalent_without_model(store,job):
    from hireme.answers import resolve
    from tests.test_answer_reuse import field
    store.put_facts({'school':'University of California, Berkeley','degree':'B.S.'})
    options=['University of California - Berkeley','University of California Berkeley','Berkeley College']
    a=resolve(store,job['host'],field('School*','select',options),context=job)
    assert a['value'] in options[:2] and a['provenance']['fact_key']=='school'
    d=resolve(store,job['host'],field('Degree*','select',['Bachelors',"Bachelor's Degree",'Masters']),context=job)
    assert d['value']=='Bachelors' and d['provenance']['fact_key']=='degree'
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_coding_interview_preference_uses_opt_in_confirmed_skills(store,job):
    from hireme.answers import resolve
    from tests.test_answer_reuse import field
    store.update_settings({'contextual_preferences':True})
    f=field('If you were to join us for a technical interview, what is your preferred coding language when answering general coding questions?','select',['Java','Python','C++'])
    a=resolve(store,job['host'],f,context=job)
    assert a['value']=='Python' and 'contextual_preference' in a['provenance']
    store.update_settings({'contextual_preferences':False})
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)


@pytest.mark.parametrize('option',['2028 - Spring','Spring 2028','May 2028','2028-05'])
def test_graduation_choice_formats_preserve_confirmed_month(store,job,option):
    from hireme.answers import resolve
    f={'label':'Expected graduation.*','type':'combobox','required':True,'options':[option,'2028 - Fall','Other'],'maxlength':-1}
    assert resolve(store,job['host'],f,context=job)['value']==option


def test_free_text_discovery_uses_recorded_board_without_model(store,job):
    from hireme.answers import resolve
    f={'label':'How did you hear about us? If Other, please specify','type':'text','required':True,'options':[],'maxlength':255}
    a=resolve(store,job['host'],f,context={**job,'source':'gh:acme'})
    assert a['value']=='Your Greenhouse careers page.' and a['provenance']=={'job_source':'gh:acme'}
    with pytest.raises(Blocked,match='missing_fact'):resolve(store,job['host'],f,context={**job,'source':'user'})
