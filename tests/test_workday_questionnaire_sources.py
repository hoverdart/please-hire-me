import copy

import pytest

from hireme.answers import resolve,validate_package,_us_eeo_ethnicity,_employer_authorization_support
from hireme.browser_agent import _step_key
from hireme.util import Blocked,digest


OPTIONS=['Asian (Not Hispanic or Latino) (United States of America)',
         'White (Not Hispanic or Latino) (United States of America)',
         'Hispanic or Latino (United States of America)',
         'Decline to State (United States of America)']


@pytest.mark.parametrize('race',['Asian','Southeast Asian','South Asian','East Asian'])
def test_us_compound_ethnicity_requires_both_confirmed_sources(store,job,package,race):
    store.put_facts({'race':race,'hispanic_latino':'No'})
    field={'label':'What is your ethnicity?*','type':'combobox','options':OPTIONS,'required':True}
    answer=resolve(store,job['host'],field,context=job)
    assert answer['value']==OPTIONS[0]
    assert set(answer['provenance']['us_eeo_ethnicity'])=={'race_revision','hispanic_latino_revision'}
    package['answers'].append(answer);package['steps'][0]['fields'].append(field)
    package['facts_hash']=digest(store.facts())
    validate_package(store,job,package)
    store.put_facts({'hispanic_latino':'Yes'})
    with pytest.raises(Blocked):validate_package(store,job,package)
    assert resolve(store,job['host'],field,context=job)['value']==OPTIONS[2]


@pytest.mark.parametrize('facts,location',[
    ({'race':'Asian'},'United States'),
    ({'race':'Unknown','hispanic_latino':'No'},'United States'),
    ({'race':'Asian','hispanic_latino':'No'},'Canada'),
    ({'citizenship':'China','hispanic_latino':'No'},'United States'),
])
def test_ethnicity_never_infers_missing_identity_or_foreign_categories(store,facts,location):
    store.put_facts(facts)
    assert _us_eeo_ethnicity(store,{'label':'What is your ethnicity?*','options':OPTIONS},{'location':location}) is None


@pytest.mark.parametrize('missing',['work_authorized_us','unrestricted_authorization','needs_sponsorship',None])
def test_employer_authorization_support_needs_all_explicit_premises(store,job,missing):
    store.put_facts({'work_authorized_us':'Yes','unrestricted_authorization':'Yes','needs_sponsorship':'No'})
    if missing:store.db.execute('DELETE FROM facts WHERE key=?',(missing,))
    field={'label':'Will you require employer support to obtain or maintain authorization to work in that country? e.g. (work permit)*','options':['Yes','No']}
    result=_employer_authorization_support(store,field,job)
    assert bool(result)==(missing is None)
    assert _employer_authorization_support(store,field,{**job,'location':'Canada'}) is None


def test_application_agreement_cannot_reuse_other_job_or_legacy_consent(store,job):
    field={'label':'By selecting the checkbox, you agree to our Terms and Conditions and Applicant Privacy Policy.*',
           'type':'checkbox','options':['Yes','No'],'application_agreement_job':job['id']}
    legacy=store.ask(job['id'],job['host'],field['label'],field['options'])
    store.answer_question(legacy,'Yes')
    assert store.saved_answer(job['host'],field['label'],field['options'],field=field,context=job) is None
    qid=store.ask(job['id'],job['host'],field['label'],field['options'],field=field,context=job)
    store.answer_question(qid,'Yes')
    assert store.saved_answer(job['host'],field['label'],field['options'],field=field,context=job)['id']==qid
    assert store.saved_answer(job['host'],field['label'],field['options'],field={**field,'application_agreement_job':'another-job'},context=job) is None


def test_revisited_step_signature_preserves_source_and_question_identity():
    step={'answers':[{'field':{'label':'Title','index':1,'ref':{'id':'workExperience-1--title'}},'value':'Engineer',
                      'provenance':{'history_entry_id':'approved-entry','revision':1}}],'documents':[]}
    reordered=copy.deepcopy(step)
    reordered['answers'][0]['field'].update(index=15,ref={'id':'workExperience-8--title'})
    assert _step_key(step)==_step_key(reordered)
    reordered['answers'][0]['provenance']['revision']=2
    assert _step_key(step)!=_step_key(reordered)
    for native_id in ('primaryQuestionnaire--first','primaryQuestionnaire--second'):
        step['answers'][0]['field']['ref']['id']=native_id
        if native_id.endswith('first'):first=_step_key(step)
        else:assert first!=_step_key(step)
