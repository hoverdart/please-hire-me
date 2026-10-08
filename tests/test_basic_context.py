import pytest
from hireme.answers import resolve,validate_package
from hireme.materials import basic_context,save_basic_context,review_material,import_material
from hireme.provider import ClaudeProvider
from hireme.util import Blocked,digest


def field(label='Which product domain have you worked in?',options=None):
    return {'label':label,'type':'select' if options else 'text','required':True,'options':options or [],'maxlength':-1}


def test_basic_context_edit_revision_conflict_and_withdrawal(store):
    assert basic_context(store)['revision']==0
    save_basic_context(store,'I build manufacturing software at a startup.',0)
    original=basic_context(store)
    assert original['confirmed'] and original['role']=='personal'
    save_basic_context(store,'I build manufacturing software at a startup.',original['revision'])
    assert basic_context(store)==original
    save_basic_context(store,'I now lead a documented manufacturing software project.',original['revision'])
    latest=basic_context(store)
    assert latest['id']==original['id'] and latest['revision']==original['revision']+1
    with pytest.raises(ValueError,match='changed elsewhere'):
        save_basic_context(store,'A stale replacement must not overwrite current text.',original['revision'])
    assert basic_context(store)==latest
    review_material(store,latest['id'],latest['text'],'reference',True)
    assert not store.templates() and basic_context(store)['role']=='reference'


class ContextProvider:
    calls=0
    def match_field(self,*a):return {'fact_key':None,'template_id':None}
    def context_answer(self,field,choices,facts,context):
        self.calls+=1
        assert any('manufacturing' in c['text'] for c in choices)
        assert facts['email']['value']=='test@candidate.invalid'
        source=next(c for c in choices if 'manufacturing' in c['text'])
        return {'answer':'Manufacturing','sentence_ids':[source['id']]}


def test_structured_context_cached_and_invalidated_by_edit(store,job,package):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'I build manufacturing software at a startup.',0)
    f=field(options=['Manufacturing','Finance']);p=ContextProvider()
    a=resolve(store,job['host'],f,p,job)
    assert a['value']=='Manufacturing' and a['provenance']['context_answer']
    assert resolve(store,job['host'],f,context=job)==a and p.calls==1
    package.update(answers=[a],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    saved=basic_context(store)
    save_basic_context(store,'I now build education software at a different startup.',saved['revision'])
    with pytest.raises(Blocked):validate_package(store,job,package)
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)


def test_reference_sources_do_not_reach_factual_option_generation(store,job):
    store.update_settings({'tailored_writing':True})
    m=import_material(store,b'The company builds manufacturing software.','research.txt','context')
    review_material(store,m['id'],m['text'],'reference',True)
    class P(ContextProvider):
        def context_answer(self,f,choices,facts,context):
            assert not any('manufacturing' in c['text'] for c in choices)
            return {}
    with pytest.raises(Blocked,match='missing_fact'):resolve(store,job['host'],field(),P(),job)


@pytest.mark.parametrize('label',[
 'Are you Hispanic/Latino?', 'Do you consent to arbitration?',
 'Have you worked for the government in the past 24 months?',
 'Will you be available full-time in 2028?', 'Are you 18 years old?',
])
def test_sensitive_and_future_commitments_do_not_use_context_inference(store,job,label):
    store.update_settings({'tailored_writing':True})
    save_basic_context(store,'I build manufacturing software at a startup.',0)
    p=ContextProvider()
    with pytest.raises(Blocked):resolve(store,job['host'],field(label,['Yes','No']),p,job)
    assert p.calls==0


def test_explicit_proficiency_and_temporary_authorization_used_without_model(store,job):
    store.put_facts({'temporary_work_authorization':'No','programming_proficiency':'Expert'})
    assert resolve(store,job['host'],field('Do you currently have temporary work authorization (such as F-1, OPT, CPT, H-1B or similar)?',['Yes','No']),context=job)['value']=='No'
    assert resolve(store,job['host'],field('Please indicate your level of proficiency in programming analysis, design and creative problem-solving?', ['Advanced – frequent user','Expert – full mastery and able to train others']),context=job)['value'].startswith('Expert')
    store.put_facts({'race':'Asian'})
    with pytest.raises(Blocked):resolve(store,job['host'],field('Are you Hispanic/Latino?',['Yes','No']),context=job)


def test_context_provider_requires_independent_support_review():
    p=ClaudeProvider.__new__(ClaudeProvider);p.observer=None
    calls=[]
    def request(instruction,data,schema):
        calls.append((instruction,data,schema))
        return {'answer':'Manufacturing','sentence_ids':['s1']} if len(calls)==1 else {'supported':False,'reason':'Unsupported claim'}
    p.request=request
    assert p.context_answer(field(options=['Manufacturing','Finance']),[{'id':'s1','text':'I work in education.'}],{}, {})=={'rejected':'Unsupported claim'}
    assert len(calls)==2 and calls[0][2]['properties']['answer']['enum']==[None,'Manufacturing','Finance']


def test_fall_education_level_uses_chronological_year_and_revalidates(store,job,package):
    store.put_facts({'college_start':'2025-08','graduation':'2028-05','degree':'B.S.'})
    f=field('Please indicate your level of education during the Fall 2026 Semester:', ['Freshman','Sophomore','Junior','Senior'])
    a=resolve(store,job['host'],f,context=job)
    assert a['value']=='Sophomore' and 'chronological_academic_year' in a['provenance']
    package.update(answers=[a],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.put_facts({'college_start':'2024-08'})
    with pytest.raises(Blocked):validate_package(store,job,package)
    assert resolve(store,job['host'],f,context=job)['value']=='Junior'
    with pytest.raises(Blocked):resolve(store,job['host'],field('Official academic standing by completed credits',f['options']),context=job)
