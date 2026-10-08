"""Recurring Pi batch failures: provenance, widget semantics and exact commitments."""
import pytest

from hireme.answers import resolve, validate_package, _compatible_field
from hireme.config import validate_fact
from hireme.materials import save_basic_context, basic_context
from hireme.provider import ClaudeProvider
from hireme.util import Blocked, digest


def field(label, kind='text', options=None):
    return {'label':label,'type':kind,'required':True,'options':options or [],'maxlength':-1}


def test_pronunciation_uses_approved_context_without_inference_and_revalidates(store,job,package):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'Name pronunciation: “TEST PER-sun”\nProjects: build manufacturing software.',0)
    f=field('Name pronounciation')
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']=='TEST PER-sun'
    assert answer['provenance']['context_fact']['key']=='name_pronunciation'
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    save_basic_context(store,'Name pronunciation: “DIFFERENT”\nProjects: build manufacturing software.',basic_context(store)['revision'])
    with pytest.raises(Blocked):validate_package(store,job,{**package,'facts_hash':digest(store.facts())})
    store.put_facts({'name_pronunciation':'CONFIRMED OVERRIDE'})
    assert resolve(store,job['host'],f,context=job)['value']=='CONFIRMED OVERRIDE'


def test_pronunciation_requires_explicit_consistent_personal_sources(store,job):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'My full name is Test Person. I build manufacturing software.',0)
    with pytest.raises(Blocked):resolve(store,job['host'],field('Name pronunciation'),context=job)
    store.put_template('experience','Name pronunciation: FIRST')
    store.put_template('experience','Name pronunciation: SECOND')
    with pytest.raises(Blocked,match='mapping_review'):resolve(store,job['host'],field('Name pronunciation'),context=job)


def test_language_checkboxes_resolve_without_model_and_enforce_limits(store,job,package):
    store.update_settings({'contextual_preferences':True})
    store.put_facts({'skills':'Python, C++, Java, C, TypeScript'})
    f=field('What development languages are you most experienced with?','checkbox-group',['Go','Python','C++','Ruby','Java','Bash'])
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']=='Python; C++; Java'
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.put_facts({'skills':'TypeScript, C'})
    with pytest.raises(Blocked):validate_package(store,job,{**package,'facts_hash':digest(store.facts())})


def test_checkbox_context_model_gets_array_without_magic_label_words():
    p=ClaudeProvider.__new__(ClaudeProvider);p.observer=None;calls=[]
    def request(instruction,data,schema):
        calls.append(schema)
        return {'answer':['Python','C++'],'sentence_ids':['source']} if len(calls)==1 else {'supported':True,'reason':''}
    p.request=request
    f=field('What cloud environments have you deployed code to in class?','checkbox-group',['Python','C++','Java'])
    result=p.context_answer(f,[{'id':'source','text':'Confirmed example.'}],{}, {})
    assert result['answer']=='Python; C++'
    assert calls[0]['properties']['answer']['type']==['array','null']


def test_sierra_qualities_question_uses_reviewed_writing_path(store,job):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'I build manufacturing software and evaluate coding agents.',0)
    class Model:
        def draft_answer(self,label,choices,context,maxlength):
            return {'answer':'I build manufacturing software and evaluate coding agents.','sentence_ids':[choices[0]['id']]}
    f=field('What qualities do you believe make someone a great Agent Engineer, and how do your skills and experiences make you the best candidate for this role?','textarea')
    a=resolve(store,job['host'],f,Model(),job)
    assert a['provenance']['tailored'] and a['provenance']['sample_parts']


def test_explicit_context_can_supply_a_known_fact_without_separate_fact_entry(store,job,package):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'I have never served in the military, National Guard, or reserves.',0)
    store.put_facts({'veteran':'I am not a military veteran'})
    class Model:
        def map_option(self,*a):return None
        def explicit_context_answer(self,f,choices,facts,context):
            assert all('template_id' in c for c in choices)
            return {'answer':'I have never served in the military','sentence_ids':[choices[0]['id']]}
    f=field('What is your military status?','combobox',['I have never served in the military','I am on active duty'])
    answer=resolve(store,job['host'],f,Model(),job)
    assert answer['provenance']['explicit_context']
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    save_basic_context(store,'My military answer has been withdrawn pending review.',basic_context(store)['revision'])
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_explicit_context_requires_real_quotes_and_independent_review():
    p=ClaudeProvider.__new__(ClaudeProvider);p.observer=None
    choices=[{'id':'source','text':'I have never served in the military.'}]
    f=field('What is your military status?','combobox',['I have never served in the military','Active duty'])
    calls=[]
    def request(instruction,data,schema):
        calls.append(data)
        return {'answer':'I have never served in the military','evidence':[{'source_id':'source','quote':'Invented quote'}]}
    p.request=request
    assert p.explicit_context_answer(f,choices,{}, {})=={} and len(calls)==1
    responses=iter([{'answer':'I have never served in the military','evidence':[{'source_id':'source','quote':choices[0]['text']}]},
                    {'supported':False,'reason':'Question not covered.'}])
    p.request=lambda *a:next(responses)
    assert p.explicit_context_answer(f,choices,{}, {})=={}


def test_source_rejection_is_a_model_failure_instead_of_missing_personal_information(store,job):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'I build software for manufacturing teams.',0)
    class Model:
        def match_field(self,*a):return {'fact_key':None,'template_id':None}
        def context_answer(self,*a):return {'rejected':'Draft asserted an unsupported metric.'}
    with pytest.raises(Blocked,match='writing_unsupported'):
        resolve(store,job['host'],field('What product domain have you worked in?','select',['Manufacturing','Finance']),Model(),job)
    assert not store.db.execute("SELECT 1 FROM model_abstentions WHERE method='context_answer'").fetchone()


def test_new_confirmed_answers_are_independent_and_replayable(store,job,package):
    store.put_facts({'over_18':'Yes','fulltime_start':'2028-05-19','nights_weekends':'Yes','robots_experience':'Yes','humanoids_experience':'No'})
    pairs=[(field('At the time of application, are you 18+ years of age?','combobox',['Yes','No']),'Yes'),
           (field('When will you be available to work as a full-time, permanent employee? Full-time means working 40 hours per week while being based in San Mateo, CA headquarters.','combobox',['Summer 2027','Spring 2028','Summer 2028']),'Spring 2028'),
           (field('Are you willing and able to work nights and weekends?','yesno',['Yes','No']),'Yes'),
           (field('Do you have experience working with robots?','yesno',['Yes','No']),'Yes'),
           (field('Have you worked with humanoids?','yesno',['Yes','No']),'No')]
    answers=[resolve(store,job['host'],f,context=job) for f,_ in pairs]
    assert [a['value'] for a in answers]==[value for _,value in pairs]
    package.update(answers=answers,steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    assert store.facts()['graduation']['value']=='2028-05'
    assert not _compatible_field('over_18',field('Are you at least 21 years old?'))
    assert not _compatible_field('robots_experience',field('Have you worked with humanoids?'))


def test_generic_start_date_respects_fulltime_posting_and_keeps_internship_availability(store,job,package):
    store.put_facts({'earliest_start':'2027-05','fulltime_start':'2028-05-19'})
    f=field('Earliest available start date')
    assert resolve(store,job['host'],f,context=job)['value']=='May 2027'
    fulltime={**job,'title':'Software Engineer, New Grad 2028'}
    answer=resolve(store,job['host'],f,context=fulltime)
    assert answer['value']=='May 19, 2028' and answer['provenance']['fact_key']=='fulltime_start'
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,fulltime,package)
    assert not _compatible_field('fulltime_start',f,job)


def test_fulltime_start_window_and_hold_dependencies_are_separate_from_internships(store,job):
    from hireme.policy import eligible
    from hireme.job_holds import dependency
    store.put_facts({'earliest_start':'2027-05','fulltime_start':'2028-05-19'})
    s=store.settings();s['min_fit_score']=0;s['seniority']=['internship','new-grad']
    eligible(job,s,store.facts())
    fulltime={**job,'title':'Software Engineer New Grad Summer 2027'}
    with pytest.raises(Blocked,match='start_window_mismatch'):eligible(fulltime,s,store.facts())
    before=dependency(store,fulltime,'eligibility','start_window_mismatch')
    store.put_facts({'fulltime_start':'2027-05-19'})
    assert before!=dependency(store,fulltime,'eligibility','start_window_mismatch')
    eligible(fulltime,s,store.facts())


def test_any_of_conflicts_uses_positive_activity_without_inventing_no(store,job):
    f=field('Do you have: a) any Personal/Familial Relationships (current Acme employees or employees of Acme vendors); b) any Outside Business Activities?','yesno',['Yes','No'])
    store.put_facts({'outside_business_activity':'No'})
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)
    store.put_facts({'outside_business_activity':'Yes'})
    assert resolve(store,job['host'],f,context=job)['value']=='Yes'
    with pytest.raises(Blocked):resolve(store,job['host'],{**f,'label':f['label'].replace('Outside Business Activities','Outside Business Activities that you wish to continue')},context=job)


@pytest.mark.parametrize('value',['2028-02-30','2028-13-19','2028-05','2028-5-19'])
def test_fulltime_date_is_a_real_day(value):
    with pytest.raises(ValueError):validate_fact('fulltime_start',value)


def test_export_control_any_of_is_satisfied_by_confirmed_citizenship(store,job,package):
    f=field('I am one of the following: (a) a citizen of the United States; (b) a lawful permanent resident of the United States; or (c) a person admitted into the United States as an asylee or refugee:','yesno',['Yes','No'])
    store.put_facts({'citizenship':'United States'})
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']=='Yes' and 'citizenship_revision' in answer['provenance']
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.put_facts({'citizenship':'Canada'})
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)


def test_lever_question_heading_and_checkbox_group_use_employer_structure(store):
    from hireme.browser import Browser
    with Browser(store,test_url='http://127.0.0.1:12345') as b:
        b.page.set_content('''<li class="application-question custom-question"><div>
            <div class="application-label"><div class="text">Are you legally authorized to work for any employer in the United States?<span class="required">✱</span></div></div>
            <div class="application-field"><label><input type="radio" name="auth" value="Yes" required>Yes</label><label><input type="radio" name="auth" value="No" required>No</label></div>
            </div></li><li class="application-question"><div class="application-label">Which languages do you use?✱</div>
            <div class="application-field"><label><input type="checkbox" name="languages" value="Python">Python</label><label><input type="checkbox" name="languages" value="C++">C++</label></div></li>''')
        fields=b._snapshot()
        assert len(fields)==2
        assert fields[0]['label'].startswith('Are you legally authorized')
        assert fields[0]['options']==['Yes','No'] and fields[0]['required']
        assert fields[1]['type']=='checkbox-group' and fields[1]['required']
        assert fields[1]['options']==['Python','C++']
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_lever_generic_instruction_uses_one_question_card_heading_only(store):
    from hireme.browser import Browser
    with Browser(store,test_url='http://127.0.0.1:12345') as b:
        b.page.set_content('''<div class="application-form" data-qa="additional-cards"><h4 data-qa="card-name">How did you hear about us?</h4>
            <li class="application-question"><div class="application-label">Select One✱</div>
            <label><input type="radio" name="source" value="Website">Website</label><label><input type="radio" name="source" value="Other">Other</label></li>
            <li class="application-question"><div class="application-label">Other source details</div><input name="details"></li></div>
            <div class="application-form" data-qa="additional-cards"><h4 data-qa="card-name">Ambiguous card</h4>
            <li class="application-question"><div class="application-label">Select One</div><input name="one"></li>
            <li class="application-question"><div class="application-label">Select One</div><input name="two"></li></div>''')
        fields=b._snapshot()
        assert fields[0]['label']=='How did you hear about us?*' and fields[0]['required']
        assert fields[0]['options']==['Website','Other']
        assert fields[1]['label']=='Other source details'
        assert fields[2]['label']==fields[3]['label']=='Select One'
