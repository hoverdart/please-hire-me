import json
import math
import pytest
from pathlib import Path
from hireme.answers import resolve,validate_package
from hireme.config import validate_settings
from hireme.policy import eligible
from hireme.util import Blocked,canonical_url
from hireme.migration import import_legacy


def test_nonfinite_settings_rejected():
    with pytest.raises(ValueError):validate_settings({'min_annual_usd':float('nan')})
    with pytest.raises(ValueError):validate_settings({'max_years_required':float('inf')})


def test_resume_required_in_package(store,job,package):
    package['documents']=[]
    with pytest.raises(Blocked):store.prepare(job,package)


def test_user_confirmed_prior_employment_rule_is_company_scoped(store,job):
    store.put_facts({'worked_outside_resume':'No','contacts_outside_resume':'No'})
    store.update_settings({'prior_employers':['Former Employer']})
    f={'label':'Have you previously worked for this company?','type':'select','options':['Yes','No'],'required':True}
    assert resolve(store,'jobs.lever.co|acme',f)['value']=='No'
    with pytest.raises(Blocked):resolve(store,'jobs.lever.co|formeremployer',f)


def test_employer_specific_answer_not_reused(store,job):
    f={'label':'Why do you want to work here?','type':'textarea','required':True,'options':[]}
    q=store.ask(job['id'],'jobs.lever.co|acme',f['label'],[])
    store.answer_question(q,'I want to work at Acme on their product.')
    assert resolve(store,'jobs.lever.co|acme',f)['value'].startswith('I want')
    with pytest.raises(Blocked):resolve(store,'jobs.lever.co|anothercompany',f)


def test_templates_cannot_be_rewritten(store,job,package):
    tid=store.put_template('project','I built a Python service using TypeScript interfaces.')
    f={'label':'Describe a project you built','type':'textarea','required':True,'options':[],'maxlength':1000}
    a=resolve(store,job['host'],f)
    assert a['provenance']['template_id']==tid
    package['answers']=[a];package['steps']=[]
    store.prepare(job,package)
    a['value']+=' It served one million users.'
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_legacy_history_cannot_be_ignored(store,tmp_path):
    legacy=tmp_path/'legacy';(legacy/'logs').mkdir(parents=True)
    (legacy/'logs/applications-log.md').write_text('Applied to Example on August 1')
    import_legacy(store,legacy)
    assert 'legacy history review' in store.missing_setup()
    with pytest.raises(ValueError):store.update_settings({'onboarding_complete':True,'live_enabled':True})


def test_summer_relocation_and_school_locations(store,job):
    store.put_facts({'summer_2027_relocate':'Yes'})
    assert eligible({**job,'location':'Boston, MA'},store.settings(),store.facts())[0]>0
    with pytest.raises(Blocked,match='location'):eligible({**job,'title':'Software Engineer Intern Fall 2027','location':'Boston, MA'},store.settings(),store.facts())


def test_sf_abbreviation_is_eligible_for_us_summer_relocation(store,job):
    store.put_facts({'summer_2027_relocate':'Yes'})
    assert eligible({**job,'location':'SF'},store.settings(),store.facts())[0]>0
    with pytest.raises(Blocked,match='location'):
        eligible({**job,'location':'London, UK'},store.settings(),store.facts())


def test_month_window_overlaps_summer(store,job):
    store.put_facts({'earliest_start':'2027-06','latest_start':'2027-08'})
    assert eligible(job,store.settings(),store.facts())[0]>0
    store.put_facts({'earliest_start':'2027-09','latest_start':'2027-12'})
    with pytest.raises(Blocked,match='start_window'):eligible(job,store.settings(),store.facts())


def test_greenhouse_alias_and_lever_apply_have_one_identity():
    assert canonical_url('https://boards.greenhouse.io/acme/jobs/123')==canonical_url('https://job-boards.greenhouse.io/acme/jobs/123')
    assert canonical_url('https://jobs.lever.co/acme/ABC/apply')==canonical_url('https://jobs.lever.co/acme/ABC')


def test_linked_universal_fact_reused_but_company_text_not(store,job):
    f={'label':'Enter your contact email here','type':'text','required':True,'options':[]}
    q=store.ask(job['id'],'jobs.lever.co|acme',f['label'],[])
    store.answer_question(q,store.facts()['email']['value'],'email')
    assert resolve(store,'jobs.lever.co|anothercompany',f)['value']==store.facts()['email']['value']


def test_expected_graduation_field_not_a_posting_gate(store,job):
    assert eligible({**job,'description':'Build Python software. Expected graduation date *'},store.settings(),store.facts())[0]>0
