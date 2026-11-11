import pytest
from hireme.answers import resolve, validate_package
from hireme.page_answers import plan, cached
from hireme.util import Blocked,digest
from hireme.provider import ClaudeProvider


@pytest.fixture(autouse=True)
def enable_grounded_writing(store):
    store.update_settings({'tailored_writing':True})


def field(label,options=None,**extra):
    return {'label':label,'type':'select' if options else 'text','options':options or [],'required':True,'maxlength':-1,**extra}


class Planner:
    def __init__(self,answer,source=None):self.answer=answer;self.source=source;self.calls=0
    def plan_form(self,targets,sources,context):
        self.calls+=1
        assert 'all_questions' in context and 'confirmed_facts' in context
        source=next(s for s in sources if (s.get('template_id')==self.source if self.source else 'template_id' in s))
        return {'answers':[{'field_id':t['field_id'],'classification':'inferred','answer':self.answer,
                           'sentence_ids':[source['id']],'reason':'Supported by the approved note'} for t in targets]}


def test_page_plan_reuses_context_and_invalidates_when_source_changes(store,job,package):
    tid=store.put_template('project','I shipped Puzzle Lab, an interactive solver at https://puzzle.example.test/.')
    f=field('What can we look at?');provider=Planner('https://puzzle.example.test/',tid)
    answers,pending=plan(store,job['host'],[f],[],[(f,'missing_fact')],provider,job)
    assert not pending and provider.calls==1
    assert resolve(store,job['host'],f,context=job)==answers[0]
    package.update(answers=answers,steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.edit_template(tid,'project','I retired Puzzle Lab and replaced it with another project.')
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_duplicate_wording_has_independent_section_caches(store,job,package):
    tid=store.put_template('experience','I can work onsite full-time during summer, but only part-time during school.')
    first=field('Can you work full-time?', ['Yes','No'],section='Summer 2027')
    second=field('Can you work full-time?', ['Yes','No'],section='School year')
    fields=[first,second]
    a,_=plan(store,job['host'],fields,[],[(first,'missing_fact')],Planner('Yes',tid),job)
    b,_=plan(store,job['host'],fields,a,[(second,'missing_fact')],Planner('No',tid),job)
    assert cached(store,job['host'],first,job)['value']=='Yes'
    assert cached(store,job['host'],second,job)['value']=='No'
    package.update(answers=a+b,steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)


@pytest.mark.parametrize('bad',['unlisted','unknown_source','unknown_field','duplicate'])
def test_invalid_page_plan_cannot_become_an_answer(store,job,bad):
    tid=store.put_template('experience','I can work full-time onsite during summer 2027.')
    f=field('Can you work full time onsite?', ['Yes','No'])
    class Bad(Planner):
        def plan_form(self,*args):
            result=super().plan_form(*args);item=result['answers'][0]
            if bad=='unlisted':item['answer']='Probably'
            if bad=='unknown_source':item['sentence_ids']=['invented']
            if bad=='unknown_field':item['field_id']='999'
            if bad=='duplicate':result['answers'].append(dict(item))
            return result
    try:answers,pending=plan(store,job['host'],[f],[],[(f,'missing_fact')],Bad('Yes',tid),job)
    except Blocked:answers=[]
    assert not answers and cached(store,job['host'],f,job) is None


def test_onsite_alone_does_not_answer_compound_hours_question(store,job):
    store.put_facts({'onsite':'Yes'})
    with pytest.raises(Blocked):resolve(store,job['host'],field('Are you willing to work on-site full-time?', ['Yes','No']),context=job)


@pytest.mark.parametrize('prefix',[
    'Do you currently require employer-sponsored work authorization (e.g., employer visa sponsorship) to work in',
    'If currently authorized to work legally in the country or location to which you are applying, will you in the future require employer-sponsored work authorization (e.g., employer visa sponsorship) to continue working in',
])
def test_country_or_location_sponsorship_reuses_fact_with_country_scope(store,job,prefix):
    f=field(prefix+' the country or location where this role is based?*',['Yes','No'])
    answer=resolve(store,job['host'],f,context={**job,'location':'Chicago, IL'})
    assert answer['value']=='No'
    with pytest.raises(Blocked):resolve(store,job['host'],f,context={**job,'location':'Toronto, Canada'})


def test_independent_page_review_rejects_unsupported_inference():
    p=object.__new__(ClaudeProvider);p.observer=None
    responses=iter([{'answers':[{'field_id':'0','classification':'inferred','answer':'Yes','sentence_ids':['note'],'reason':''}]},
                    {'reviews':[{'field_id':'0','supported':False,'reason':'Only summer availability supplied; question is about winter.'}]}]*2)
    p.request=lambda *args:next(responses)
    result=p.plan_form([{'field_id':'0','field':field('Can you work during winter?',['Yes','No'])}],[{'id':'note','text':'Summer only.'}],{})
    assert result['answers'][0]['classification']=='missing' and result['answers'][0]['answer'] is None


def test_page_review_repairs_an_overstatement_before_asking_again():
    p=object.__new__(ClaudeProvider);p.observer=None;calls=[]
    def answer(value):
        return {'answers':[{'field_id':'0','classification':'inferred','answer':value,'sentence_ids':['note'],'reason':'Supported scope'}]}
    responses=iter([answer('Expert'),{'reviews':[{'field_id':'0','supported':False,'reason':'Comfortable debugging does not establish expert mastery.'}]},
                    answer('Comfortable'),{'reviews':[{'field_id':'0','supported':True,'reason':'Matches the explicit assessment.'}]}])
    def request(instruction,data,schema):
        calls.append(data);return next(responses)
    p.request=request
    result=p.plan_form([{'field_id':'0','field':field('Your proficiency?',['Comfortable','Expert'])}],
                       [{'id':'note','text':'I am comfortable tracing and debugging this flow.'}],{})
    assert result['answers'][0]['answer']=='Comfortable'
    assert len(calls)==4 and calls[2]['repair_feedback'][0]['field_id']=='0'
    assert calls[3]['proposed']['answers'][0]['answer']=='Comfortable'


def test_page_repair_requires_complete_independent_review():
    p=object.__new__(ClaudeProvider);p.observer=None
    draft={'answers':[{'field_id':'0','classification':'inferred','answer':'Yes','sentence_ids':['note'],'reason':''}]}
    responses=iter([draft,{'reviews':[{'field_id':'0','supported':False,'reason':'Unsupported'}]},draft,{'reviews':[]}])
    p.request=lambda *args:next(responses)
    with pytest.raises(Blocked,match='Incomplete page review'):
        p.plan_form([{'field_id':'0','field':field('Can you work winters?',['Yes','No'])}],[{'id':'note','text':'Summer only.'}],{})


def test_current_notes_survive_a_large_library_of_older_writing(store,job):
    from hireme.materials import save_basic_context
    from hireme.answers import _approved_sentences
    for n in range(8):store.put_template('experience',('I built Python software infrastructure for internship work. '*150)+str(n))
    save_basic_context(store,'Employment restrictions: I am not subject to any non-compete or non-solicitation restrictions.',0)
    sources=_approved_sentences(store,{**job,'field_context':{'label':'What legal restrictions apply?'}})
    assert any('non-compete' in s['text'] for s in sources)


def test_resume_hyperlinks_are_grounded_sources(store,job):
    from io import BytesIO
    from reportlab.pdfgen.canvas import Canvas
    from hireme.answers import _approved_sentences,_validate_writing
    import hashlib
    data=BytesIO();canvas=Canvas(data);canvas.drawString(50,700,'Puzzle Lab project')
    canvas.linkURL('https://puzzle.example.test/',(50,690,180,715));canvas.save()
    raw=data.getvalue();sha=hashlib.sha256(raw).hexdigest();name=sha+'.pdf'
    (store.root/'documents'/name).write_bytes(raw)
    store.db.execute("UPDATE documents SET hash=?,filename=? WHERE kind='resume'",(sha,name))
    sources=_approved_sentences(store,job)
    assert any(s['text']=='Resume link: https://puzzle.example.test/' and s['resume_hash']==sha for s in sources)


def test_browser_plans_required_gaps_before_asking_or_filling(store,monkeypatch):
    from tests.test_browser import ats,local_job
    from hireme.browser import Browser
    # Use a browser-local page; no employer traffic or final submission.
    from playwright.sync_api import sync_playwright
    store.put_template('experience','I can work full-time onsite during summer 2027.')
    calls=[]
    class PageModel:
        def __init__(self,*args,**kwargs):pass
        def plan_form(self,targets,sources,context):
            calls.append(targets)
            assert not store.db.execute('SELECT 1 FROM questions WHERE resolved=0').fetchone()
            source=next(s for s in sources if 'template_id' in s)
            return {'answers':[{'field_id':t['field_id'],'classification':'inferred','answer':'Yes','sentence_ids':[source['id']]} for t in targets]}
    monkeypatch.setattr('hireme.provider.LazyProvider',PageModel)
    with sync_playwright() as p:
        chrome=p.chromium.launch(headless=True);page=chrome.new_page()
        html='''<h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p>
        <form><label>Email<input type=email name=email required></label><label>Full name<input name=name required></label>
        <label>Resume<input name=resume type=file required></label><label>Can you work full-time onsite during summer?
        <select name=availability required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
        <button type=submit>Submit application</button></form>'''
        page.route('http://fixture.invalid/**',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        b=Browser(store,test_url='http://fixture.invalid');b.page=page;b.preparing=True
        j={'id':digest('page-plan-fixture'),'url':'http://fixture.invalid/application','host':'fixture.invalid','company':'Fixture',
           'title':'Software Engineer Intern Summer 2027','location':'San Francisco, United States','description':'Build Python software','source':'fixture'}
        store.upsert_job(j)
        assert b.apply(j,live=False)=='prepared'
        assert page.locator('select').input_value()=='Yes' and len(calls)==1
        assert not store.db.execute('SELECT 1 FROM questions WHERE resolved=0').fetchone()
        chrome.close()
