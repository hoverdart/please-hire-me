import json
from types import SimpleNamespace
import pytest
from hireme.provider import ManagedProvider, ClaudeProvider
from hireme.graduation import window,matches,required
from hireme.answers import resolve
from hireme.util import Blocked

def test_temporary_caps_restore_after_reopen(store,monkeypatch):
    from hireme.store import Store
    store.update_settings({'max_model_requests_per_day':300,'max_model_requests_per_cycle':100})
    store.temporary_model_limits(seconds=10)
    assert store.settings()['max_model_requests_per_day']==500
    monkeypatch.setattr('hireme.store.time.time',lambda:10**12)
    other=Store(store.root)
    assert other.settings()['max_model_requests_per_day']==300
    assert other.settings()['max_model_requests_per_cycle']==100
    other.close()

def test_override_preserves_unrelated_changes_and_rejects_nested(store):
    store.temporary_model_limits()
    with pytest.raises(ValueError):store.temporary_model_limits()
    store.update_settings({'model_effort':'high'})
    assert store.restore_model_limits()['model_effort']=='high'
    assert store.settings()['max_model_requests_per_day']==100

@pytest.mark.parametrize('text,value,answer',[
 ('Must graduate between May 2027 and August 2028','2028-05',True),
 ('Must graduate before September 2027','2027-09',False),
 ('Must graduate before September 2027','2027-08',True),
 ('Must graduate after 2027','2028-01',True),
 ('Must graduate between 2027 and 2028','2028-12',True),
 ('Must graduate by September 2027','2027-09',True),
])
def test_graduation_precision(text,value,answer):
    assert matches(value,window(text)) is answer

def test_unknown_graduation_precision_and_wording():
    with pytest.raises(Blocked):matches('2027',window('Graduate before September 2027'))
    with pytest.raises(Blocked):required('Must graduate sometime soon','2028-05')

def test_hp_iq_cutoff_question_is_answered_from_confirmed_date(store,job):
    f={'label':'Will you be graduating before September 2027?*','type':'select','options':['Yes','No'],'required':True,'maxlength':-1}
    result=resolve(store,job['host'],f,context=job)
    assert result['value']=='No'
    store.put_facts({'graduation':'2027-08'})
    assert resolve(store,job['host'],f,context=job)['value']=='Yes'


@pytest.mark.parametrize('date,result',[('2028-01','Yes'),('2028-12','Yes'),('2027-12','No')])
def test_idme_graduation_year_cutoff_uses_confirmed_fact_without_model(store,job,date,result):
    store.put_facts({'graduation':date})
    f={'label':'Is your expected graduation date in 2028 or later?*','type':'select','options':['No','Yes'],'required':True,'maxlength':-1}
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']==result and answer['provenance']['graduation_window_revision']==store.facts()['graduation']['revision']


@pytest.mark.parametrize('score,result',[('3.8/4.0','3.5 - 3.99'),('3.49/4.0','3.49 - 3.0'),('4.0/4.0','4.0 or higher'),('2.99/4.0','2.99 or below')])
def test_idme_gpa_ranges_use_confirmed_fact_without_model(store,job,score,result):
    store.put_facts({'gpa':score})
    f={'label':'What is your cumulative GPA?*','type':'select','options':['2.99 or below','3.49 - 3.0','4.0 or higher','3.5 - 3.99'],'required':True,'maxlength':-1}
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']==result and answer['provenance']['fact_key']=='gpa'


@pytest.mark.parametrize('score,options',[('3.8/5.0',['3.5 - 3.99']),('3.8/4.0',['3.5 - 4.0','3.0 - 4.0']),('3.495/4.0',['3.0 - 3.49','3.5 - 3.99'])])
def test_gpa_range_ambiguity_and_unsupported_scale_do_not_cache(store,job,score,options):
    store.put_facts({'gpa':score})
    f={'label':'What is your cumulative GPA?*','type':'select','options':options,'required':True,'maxlength':-1}
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)
    assert not store.db.execute('SELECT 1 FROM field_bindings_v2').fetchone()

def test_model_selection_and_durable_metadata(store,monkeypatch):
    monkeypatch.setattr('hireme.provider.shutil.which',lambda _:'/fixture/claude')
    calls=[]
    def run(args,**kw):
        if args[1:]==['auth','status']:return SimpleNamespace(returncode=0,stdout=json.dumps({'loggedIn':True,'authMethod':'claude.ai','subscriptionType':'pro'}))
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'structured_output':{'fact_key':'school','template_id':None},'usage':{'input_tokens':20,'output_tokens':5,'private':'no'}}))
    monkeypatch.setattr('hireme.provider.subprocess.run',run)
    store.update_settings({'provider_model':'sonnet','model_effort':'medium','model_escalation':True})
    p=ManagedProvider(store,90)
    p.match_field({'label':'University'}, {'school':{'value':'A'}},[])
    p.reconsider_field({'label':'University'}, {'school':{'value':'A'}},[])
    assert [a[a.index('--model')+1] for a in calls]==['sonnet','opus']
    assert [a[a.index('--effort')+1] for a in calls]==['medium','high']
    assert store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==2
    rows=store.db.execute('SELECT * FROM model_request_metadata ORDER BY request_id').fetchall()
    assert len(rows)==2 and rows[0]['success']==1
    assert json.loads(rows[0]['usage'])=={'input_tokens':20,'output_tokens':5}


def test_packaged_discovery_resources_work_without_checkout(tmp_path):
    from pathlib import Path
    from hireme.resources import discovery_text
    from hireme.discovery import board_sources
    root=Path(__file__).resolve().parents[1]
    for name in ('boards.md','slug-candidates.txt'):
        assert discovery_text(tmp_path,name)==(root/'data'/name).read_text()
    assert board_sources(tmp_path,limit=2)==board_sources(root,limit=2)

def test_missing_fact_does_not_escalate(store,job):
    store.update_settings({'model_escalation':True})
    class Model:
        calls=0
        def match_field(self,*args):self.calls+=1;return {'fact_key':None,'template_id':None}
        def reconsider_field(self,*args):raise AssertionError('Must not escalate missing facts')
    m=Model()
    f={'label':'Do you consent to store your data?','type':'select','required':True,'options':['Yes','No'],'maxlength':-1}
    with pytest.raises(Blocked):resolve(store,job['host'],f,m,context=job)
    assert m.calls==1

def test_unsupported_cli_stops_without_fallback(monkeypatch):
    monkeypatch.setattr('hireme.provider.shutil.which',lambda _:'/fixture/claude')
    def run(args,**kw):
        if args[1:]==['auth','status']:return SimpleNamespace(returncode=0,stdout=json.dumps({'loggedIn':True,'authMethod':'claude.ai','subscriptionType':'pro'}))
        return SimpleNamespace(returncode=1,stdout='',stderr='unknown option --effort')
    monkeypatch.setattr('hireme.provider.subprocess.run',run)
    with pytest.raises(Blocked,match='provider_cli_incompatible'):ClaudeProvider().choose_answer('Synthetic',[])


def test_resume_date_requirement_is_not_a_graduation_cutoff():
    required('Anticipated graduation date (month and year) must be clearly indicated on a resume or CV to be considered.','2028-05')


def test_greenhouse_redirect_uses_verified_embed_only(monkeypatch):
    from hireme.ats_adapters import GreenhouseAdapter
    job={'url':'https://job-boards.greenhouse.io/roblox/jobs/8072713','title':'[Summer 2027] Software Engineer Intern','host':'job-boards.greenhouse.io'}
    response={'id':8072713,'title':job['title'],'content':'<p>Verified internship posting</p>'}
    monkeypatch.setattr('hireme.net.Network.json',lambda *args:response)
    url,text=GreenhouseAdapter().navigation(job)
    assert url=='https://job-boards.greenhouse.io/embed/job_app?for=roblox&token=8072713'
    assert 'Verified internship posting' in text
    response['id']=1
    with pytest.raises(Blocked,match='posting_fetch_failed'):GreenhouseAdapter().navigation(job)

def test_posting_questions_not_screening_requirements():
    from hireme.ats_adapters import SingleStepAdapter
    text='Currently pursuing a degree. Apply for this job Will you be graduating before September 2027?'
    posting=SingleStepAdapter().posting_text(text)
    assert 'before September' not in posting
    required(posting,'2028-05')


def test_employment_consent_is_not_marketing_consent(store,job):
    store.put_facts({'recruitment_data_consent':'Yes'})
    label='By checking this box, I agree to allow Veeam Software to store and process my data for the purpose of considering my eligibility regarding my current application for employment.'
    field={'label':label,'type':'checkbox','options':[],'required':True,'maxlength':-1}
    assert resolve(store,job['host'],field,context=job)['value']=='Yes'
    field['label']=label+' and marketing purposes.'
    with pytest.raises(Blocked):resolve(store,job['host'],field,context=job)


def test_confirmed_business_disclosure_resolves_without_inference(store,job):
    store.put_facts({'outside_business_activity':'Yes','business_activity_details':'I contract for the listed employer. No known overlap or conflict.'})
    f={'label':'Do you currently own, operate, or provide services to any other business or organization?','type':'select','options':['Yes','No'],'required':True,'maxlength':-1}
    assert resolve(store,job['host'],f,context=job)['value']=='Yes'


@pytest.mark.parametrize('options,value',[([], 'User-approved disclosure'),(['Yes','No'],'Yes')])
def test_mapping_update_preserves_contextually_identical_user_approval(store,job,options,value):
    from hireme.field_context import field_context
    from hireme.util import digest
    field={'label':'Employer-specific approved answer','type':'select' if options else 'text','required':True,'options':options,'maxlength':-1}
    context=field_context(job['host'],field,job);context['version']=3
    key=digest(['approved_answer',context])
    store.db.execute('INSERT INTO question_contexts VALUES(?,?)',(key,json.dumps(context)))
    store.db.execute('INSERT INTO answers VALUES(?,?,?,?,?,?,?,?)',(key,field['label'],job['host'],json.dumps(options),value,None,1,'2026-10-03T00:00:00+00:00'))
    assert resolve(store,job['host'],field,context=job)['value']==value
    with pytest.raises(Blocked):resolve(store,job['host'],{**field,'section':'employment'},context=job)


def test_second_education_entry_does_not_reuse_first_graduation(store,job):
    field={'label':'End date year','type':'number','options':[],'required':True,'maxlength':-1,'section':'education','section_entry':1}
    with pytest.raises(Blocked,match='repeated_entry_review'):resolve(store,job['host'],field,context=job)
    q=store.ask(job['id'],job['host'],field['label'],[],field=field,context=job)
    store.answer_question(q,'2026')
    assert resolve(store,job['host'],field,context=job)['value']=='2026'
    other={**field,'section_entry':2}
    with pytest.raises(Blocked,match='repeated_entry_review'):resolve(store,job['host'],other,context=job)


def test_required_combobox_without_options_never_uses_raw_fact_or_model(store,job):
    store.put_facts({'degree':'B.S.'})
    class Model:
        def match_field(self,*args):raise AssertionError('An unvalidated dropdown must stop before inference')
    f={'label':'Degree*','type':'combobox','options':[],'required':True,'maxlength':-1}
    with pytest.raises(Blocked,match='unsupported_widget'):resolve(store,job['host'],f,Model(),context=job)
    assert store.db.execute('SELECT count(*) FROM field_bindings_v2').fetchone()[0]==0
    assert resolve(store,job['host'],{**f,'required':False},Model(),context=job) is None
