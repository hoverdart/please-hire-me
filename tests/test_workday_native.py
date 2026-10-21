"""Synthetic native shapes from the reviewed client; never employer acceptance."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from hireme.browser import Browser
from hireme.util import Blocked, digest
from hireme.workday import WorkdayController, WriteGrant
from hireme.workday_flow import NativeFlow, NvidiaNativeProfile, StepWrite
from hireme.workday_native import (PAGES, QUESTION_TYPES, SESSION_HEADER, NativeOperation, NativeReadScope,
    encoded, finalized, native_url, pages, questionnaire_payload, questionnaire_saved, record_operation, saved_record)
from hireme.workday_profiles import NVIDIA, bind_session
from hireme.workday_state import CorrelatedStep, DraftObservation, GrantQueue
from hireme.workday_uploads import UploadGrant, UploadQueue
from tests.test_workday_state import wd_job, journal, session_response
from tests.test_workday_sessions import Context as NativeCookieContext


def request(operation):
    return SimpleNamespace(url=operation.url,method=operation.method,post_data_buffer=operation.body,headers=operation.headers)


def response(operation,data,status=200):
    return SimpleNamespace(request=request(operation),status=status,json=lambda:data)


@pytest.mark.parametrize('body',[None,[],1,True,'text'])
def test_definition_reads_reject_non_object_json_without_router_errors(store,wd_job,body):
    j=journal(store,wd_job)
    scope=NativeReadScope(store,j.session,'remote-fixture','native-session')
    operation=record_operation('remote-fixture','native-session','workexperiences',{},definition=True)
    assert not scope.allow(operation.url,operation.method,encoded(body),operation.headers)
    assert scope.allow(operation.url,operation.method,b'{}',operation.headers)
    assert not scope.allow(operation.url,operation.method,b'{}',{SESSION_HEADER:'other','form-definition':'true'})


@pytest.mark.parametrize('data',[{'data':[{'id':[]}]},{'data':[{'id':'unknown'}]},{'data':[]},
    {'data':[{'id':key} for key,value in PAGES.items() if value=='My Information']}])
def test_unknown_flow_or_missing_review_is_an_actionable_hold(data):
    with pytest.raises(Blocked):pages(data)


def test_native_questionnaire_encodings_preserve_official_question_and_choice_ids():
    types={value:key for key,value in QUESTION_TYPES.items()}
    questionnaire={'id':'questionnaire','questions':[
        {'id':'text','type':{'id':types['text']}},
        {'id':'single','type':{'id':types['single']},'possibleAnswers':[{'id':'choice','answerText':'Yes'}]},
        {'id':'number','type':{'id':types['number']}},
        {'id':'date','type':{'id':types['date']}}]}
    payload=questionnaire_payload(questionnaire,{'text':'Approved narrative','single':'choice','number':25,'date':'2028-05-19'})
    assert payload['questionnaireAnswers']==[
        {'questionItem':{'id':'text'},'answerText':'Approved narrative'},
        {'questionItem':{'id':'single'},'questionMultipleChoiceAnswers':[{'id':'choice'}]},
        {'questionItem':{'id':'number'},'answerNumeric':25},
        {'questionItem':{'id':'date'},'answerDate':'2028-05-19'}]
    assert questionnaire_saved({'id':'response',**payload},payload)=='response'
    changed=copy.deepcopy(payload);changed['questionnaireAnswers'][1]['questionMultipleChoiceAnswers'][0]['id']='other'
    with pytest.raises(Blocked):questionnaire_saved({'id':'response',**changed},payload)
    with pytest.raises(Blocked):questionnaire_payload(questionnaire,{'single':'other'})
    with pytest.raises(Blocked):questionnaire_payload(questionnaire,{'number':0})
    with pytest.raises(Blocked):questionnaire_saved({'id':'response','questionnaireAnswers':[{'questionItem':['wrong']}]},payload)


def test_multi_operation_step_requires_every_out_of_order_ack_and_binds_response_headers(store,wd_job):
    j=journal(store,wd_job);store.update_settings({'remote_drafts':True})
    steps=[];operations=[]
    for resource,value in [('name','Name'),('address','City')]:
        operation=record_operation('remote-fixture','native-session',resource,{'approved':value})
        grant=WriteGrant(store,wd_job,'My Information',operation.url,operation.method,operation.body,operation_key=digest(resource))
        observation=DraftObservation('remote-fixture',digest(value),j.session.owner_hash,wd_job['id'],digest(value))
        steps.append(CorrelatedStep(j,grant,digest(value),lambda data,observation=observation:observation,headers=operation.headers))
        operations.append(operation)
    queue=GrantQueue(steps)
    for operation in operations:assert queue.consume_request(request(operation))
    wrong=response(operations[1],{'fixture':True});wrong.request.headers={**operations[1].headers,'form-definition':'true'}
    assert not queue.observe_response(wrong)
    assert queue.observe_response(response(operations[1],{'fixture':'address'})) and not queue.complete
    assert queue.observe_response(response(operations[0],{'fixture':'name'})) and queue.complete
    assert store.db.execute("SELECT COUNT(*) FROM remote_draft_steps WHERE state='acknowledged'").fetchone()[0]==2


def multipart(document,data,*,extra=False):
    boundary='fixtureBoundary'
    body=(b'--fixtureBoundary\r\nContent-Disposition: form-data; name="body"; filename="'+document['filename'].encode()+
        b'"\r\nContent-Type: application/pdf\r\n\r\n'+data+b'\r\n')
    if extra:body+=b'--fixtureBoundary\r\nContent-Disposition: form-data; name="other"\r\n\r\nnot approved\r\n'
    return body+b'--fixtureBoundary--\r\n','multipart/form-data; boundary='+boundary


@pytest.mark.parametrize('change',['wrong_bytes','extra_field','wrong_filename','form_definition'])
def test_native_upload_rejects_other_files_fields_or_operations(store,wd_job,change):
    j=journal(store,wd_job);store.update_settings({'remote_drafts':True})
    doc=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    upload=UploadGrant(j,doc)
    body,content_type=multipart(doc,b'wrong' if change=='wrong_bytes' else upload.data,extra=change=='extra_field')
    if change=='wrong_filename':body=body.replace(doc['filename'].encode(),b'other.pdf')
    headers={'content-type':content_type}
    if change=='form_definition':headers['form-definition']='true'
    req=SimpleNamespace(url=upload.url,method='POST',post_data_buffer=body,headers=headers)
    assert not upload.consume_request(req)
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


@pytest.mark.parametrize('change',['name','length','type','request'])
def test_upload_acknowledgement_cannot_accept_a_different_attachment(store,wd_job,change):
    j=journal(store,wd_job);store.update_settings({'remote_drafts':True})
    doc=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    grant=UploadGrant(j,doc);body,content_type=multipart(doc,b'')
    req=SimpleNamespace(url=grant.url,method='POST',post_data_buffer=body,headers={'content-type':content_type})
    assert isinstance(grant.consume_request(req),dict)
    ack={'id':'attachment','fileName':doc['filename'],'fileLength':len(grant.data),
         'contentType':{'id':'Content_Type_ID=application/pdf'}}
    if change=='name':ack['fileName']='other.pdf'
    if change=='length':ack['fileLength']+=1
    if change=='type':ack['contentType']['id']='Content_Type_ID=text/plain'
    if change=='request':req=SimpleNamespace(**{**req.__dict__,'url':req.url+'/other'})
    assert not grant.observe_response(SimpleNamespace(request=req,status=200,json=lambda:ack))
    assert not grant.complete and not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_real_browser_formdata_metadata_is_rebuilt_from_only_the_approved_pdf(store,wd_job):
    j=journal(store,wd_job);store.update_settings({'remote_drafts':True})
    doc=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    grant=UploadGrant(j,doc);captured=[]
    with Browser(store,test_url='http://fixture.invalid') as browser:
        def intercept(route):
            override=grant.consume_request(route.request)
            assert isinstance(override,dict)
            captured.append((route.request.post_data_buffer,override['post_data']))
            route.fulfill(status=200,content_type='application/json',body='{}')
        browser.page.route(grant.url,intercept)
        browser.page.set_content('<input type="file" id="resume">')
        browser.page.locator('#resume').set_input_files(str(store.root/'documents'/doc['filename']))
        browser.page.evaluate("""async url=>{const data=new FormData();data.append('body',document.querySelector('input').files[0]);
            await fetch(url,{method:'POST',body:data}).catch(()=>{});} """,grant.url)
    assert len(captured)==1 and grant.data in captured[0][1]
    # Chromium omits the file from intercepted metadata; the actual transport
    # override contains the exact approved bytes regardless of that omission.
    assert hashlib.sha256(grant.data).hexdigest()==doc['hash']
    assert grant.grant.payload==captured[0][1]


class FixtureDriver:
    """Normalized synthetic native driver, explicitly not a production DOM driver."""
    proof_scope='fixture';provider=None
    def __init__(self,controller):
        self.controller=controller;self.browser=controller.browser;self.store=controller.store
        self.page=None;self.remote='native-draft';self.session_id='native-session';self.records={};self.created=0
        self.fields=[];self.read_scope=None;self.pending=[];self.attachment=None

    def read_profile(self):return session_response(self.store)
    def posting_identifier(self):return self.controller.job['url'].rsplit('/',1)[1]
    def read_flow_pages(self):
        wanted={'My Information','My Experience','Application Questions','Voluntary Disclosures','Self Identification','Review'}
        data={'data':[{'id':key,'descriptor':value} for key,value in PAGES.items() if value in wanted]}
        url=native_url('jobapplication',f'jobpostings/{self.posting_identifier()}/applyflowpages?applyDetail=applyManually')
        return response(NativeOperation(url,'GET',None,{},'read'),data)
    def create_draft(self,operation):
        assert self.browser.workday_grant.consume_request(request(operation))
        self.created+=1
        return response(operation,{'id':self.remote,'sessionId':self.session_id})
    def bind_draft(self,remote,session_id,session):
        self.read_scope=NativeReadScope(self.store,session,remote,session_id)
    def enter(self,page):
        self.page=page
        label={'My Information':'Email','Application Questions':'First name',
               'Voluntary Disclosures':'Race / ethnicity','Self Identification':'Disability status'}.get(page['section'])
        if page['section']=='My Experience':
            self.fields=[{'index':0,'label':'Resume','type':'file','required':True,'options':[]}]
        elif label:
            options=['I do not wish to answer'] if page['section'] in {'Voluntary Disclosures','Self Identification'} else []
            self.fields=[{'index':0,'indices':[0],'label':label,'type':'select' if options else 'text',
                          'required':True,'options':options,'maxlength':-1,'value':''}]
        else:self.fields=[]
        self.browser.fields=self.fields
    def selected_documents(self):
        return [{**dict(self.store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()),
                 'field':{'index':-1,'label':'Resume','type':'file','required':False}}]
    def documents(self,snapshot):
        return [{**self.selected_documents()[0],'field':snapshot['fields'][0]}] if self.page['section']=='My Experience' else []
    def fill(self,snapshot,answers,documents):assert not documents
    def upload(self,snapshot,document,queue):
        for upload in queue.grants:
            body,content_type=multipart(document,b'')
            req=SimpleNamespace(url=upload.url,method='POST',post_data_buffer=body,headers={'content-type':content_type})
            override=queue.consume_request(req);assert isinstance(override,dict)
            assert upload.data in override['post_data']
            data=({'diagnosis':'clean','containsMacros':False,'fileSize':len(upload.data),'fileType':'application/pdf'} if upload.scan else
                {'id':'attachment','fileName':document['filename'],'fileLength':len(upload.data),'contentType':{'id':'Content_Type_ID=application/pdf'}})
            assert queue.observe_response(SimpleNamespace(request=req,status=200,json=lambda data=data:data))
    def bind_attachment(self,document,attachment):self.attachment=attachment
    def writes(self,page,snapshot,answers,documents):
        section=page['section']
        if section=='My Experience':
            expected={'attachments':[self.attachment]};resource='resumeattachments'
        elif section=='Application Questions':
            expected={'questionnaireAnswers':[{'questionItem':{'id':'profile-question'},'answerText':answers[0]['value']}]}
            operation=NativeOperation(native_url('jobapplication',f'jobapplication/{self.remote}/questionnaireresponse?type=primary'),
                'POST',encoded(expected),{'content-type':'application/json',SESSION_HEADER:self.session_id},'draft_write')
            return [StepWrite(operation,expected,'questionnaire')]
        elif section in {'Voluntary Disclosures','Self Identification'}:
            # Synthetic choice IDs model the official response's selected IDs;
            # no real demographic choice or legal approval is invented.
            expected={'disabilityStatus' if section=='Self Identification' else 'ethnicity':{'id':'explicit-decline'}}
            resource='selfidentify'
        else:expected={'emailAddress':answers[0]['value']};resource='emailaddress'
        return [StepWrite(record_operation(self.remote,self.session_id,resource,expected),expected)]
    def validate_bindings(self,write,snapshot,answers,documents):
        return bool(documents and self.attachment or answers)
    def perform_step(self,page,queue):
        for step in queue.steps:
            req=SimpleNamespace(url=step.grant.url,method=step.grant.method,post_data_buffer=step.grant.payload,headers=step.headers)
            assert queue.consume_request(req)
            body=json.loads(step.grant.payload)
            value={'id':'record-'+page['id'],**body}
            assert queue.observe_response(SimpleNamespace(request=req,status=200,json=lambda value=value:value))
    def wait(self,milliseconds):raise AssertionError('Every fixture native response is synchronous')
    def review_matches(self,package,row):return row['remote_id']==self.remote and len(package['steps'])==5 and self.attachment is not None
    def submit(self,operation):
        assert self.browser.workday_grant.consume_request(request(operation))
        return response(operation,{'id':self.remote})
    def completed_application(self,remote,package,data):return remote==self.remote and data['id']==remote


def native_controller(store,wd_job,monkeypatch):
    store.put_facts({'race':'I do not wish to answer','disability':'I do not wish to answer'})
    store.update_settings({'remote_drafts':True})
    browser=SimpleNamespace(store=store,context=NativeCookieContext(),fields=[],workday_grant=None,_snapshot=lambda:browser.fields,
                            _verify=lambda answers,documents,fields:None)
    profile=NvidiaNativeProfile(FixtureDriver)
    monkeypatch.setitem(__import__('hireme.workday_profiles',fromlist=['REVIEWED_TRANSPORTS']).REVIEWED_TRANSPORTS,NVIDIA,profile)
    controller=WorkdayController(browser,wd_job)
    controller._ready()
    return controller,controller._native_driver


def test_synthetic_native_flow_accumulates_steps_uploads_receipts_and_preserves_fixture_scope(store,wd_job,monkeypatch):
    controller,driver=native_controller(store,wd_job,monkeypatch)
    assert NativeFlow(controller,driver).run()=='confirmed'
    assert driver.created==1
    app=store.db.execute('SELECT * FROM applications').fetchone()
    assert app['state']=='confirmed' and len(json.loads(app['package'])['steps'])==5
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0]==1
    assert store.db.execute("SELECT state FROM workday_drafts").fetchone()[0]=='submitted'
    from hireme.workday import verify_capability
    with pytest.raises(ValueError):verify_capability(store,NVIDIA,controller.profile.signature,app['id'])
    assert not store.db.execute('SELECT 1 FROM adapter_verifications').fetchone()


@pytest.mark.parametrize('change',['package','draft','step_ack','step_missing','step_unknown','bare_event','chain_hash'])
def test_capability_rechecks_submitted_package_draft_and_acknowledged_chain(store,wd_job,monkeypatch,change):
    from hireme.workday import VERSION,verify_capability
    from hireme.workday_profiles import operational_evidence
    controller,driver=native_controller(store,wd_job,monkeypatch)
    # This test-only registration cannot install a driver or evidence in a real
    # process. It exercises promotion after the closed synthetic lifecycle.
    driver.proof_scope='live'
    assert NativeFlow(controller,driver).run()=='confirmed'
    app=store.db.execute('SELECT * FROM applications').fetchone()
    verify_capability(store,NVIDIA,controller.profile.signature,app['id'])
    assert operational_evidence(store,NVIDIA,VERSION)
    if change=='package':
        store.db.execute("UPDATE applications SET package='{}' WHERE id=?",(app['id'],))
    elif change=='draft':
        store.db.execute("UPDATE workday_drafts SET remote_id='different-native-draft'")
    elif change in {'step_ack','step_missing','step_unknown'}:
        row=store.db.execute("SELECT id FROM remote_draft_steps WHERE step!='Review' LIMIT 1").fetchone()
        if change=='step_missing':store.db.execute('DELETE FROM remote_draft_steps WHERE id=?',(row['id'],))
        elif change=='step_unknown':store.db.execute("UPDATE remote_draft_steps SET state='unknown' WHERE id=?",(row['id'],))
        else:store.db.execute("UPDATE remote_draft_steps SET acknowledgement=? WHERE id=?",(digest('different-ACK'),row['id']))
    else:
        for kind in ('workday_receipt','workday_operational_verified'):
            row=store.db.execute('SELECT seq,detail FROM events WHERE kind=? ORDER BY seq DESC LIMIT 1',(kind,)).fetchone()
            evidence=json.loads(row['detail'])
            if change=='bare_event':
                for key in ('chain_version','package_hash','draft_hash','step_chain_hash','step_record_ids'):evidence.pop(key)
            else:evidence['step_chain_hash']=digest('different-chain')
            store.db.execute('UPDATE events SET detail=? WHERE seq=?',(json.dumps(evidence),row['seq']))
    with pytest.raises(ValueError):verify_capability(store,NVIDIA,controller.profile.signature,app['id'])
    assert not operational_evidence(store,NVIDIA,VERSION)


def test_native_preparation_stops_before_any_draft_upload_or_save(store,wd_job,monkeypatch):
    controller,driver=native_controller(store,wd_job,monkeypatch)
    with pytest.raises(Blocked,match='workday_preparation_requires_draft'):NativeFlow(controller,driver).run(live=False)
    assert driver.created==0 and not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_wrong_posting_stops_before_draft_creation(store,wd_job,monkeypatch):
    controller,driver=native_controller(store,wd_job,monkeypatch)
    driver.posting_identifier=lambda:'Other_JR99999'
    with pytest.raises(Blocked,match='posting_changed_review'):NativeFlow(controller,driver).run()
    assert driver.created==0 and not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_same_browser_profile_candidate_change_after_create_validation_revokes_authority(store,wd_job,monkeypatch):
    controller,driver=native_controller(store,wd_job,monkeypatch)
    original=driver.create_draft
    def switched(operation):
        controller.browser.context.values[1]['value']='synthetic-other-candidate-session'
        return original(operation)
    monkeypatch.setattr(driver,'create_draft',switched)
    with pytest.raises(Blocked,match='account_blocked'):
        NativeFlow(controller,driver).run()
    assert driver.created==0 and not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_crash_between_creation_ack_and_projection_keeps_uncertainty(store,wd_job,monkeypatch):
    controller,driver=native_controller(store,wd_job,monkeypatch)
    flow=NativeFlow(controller,driver)
    monkeypatch.setattr(flow.journal,'remember',lambda *a:(_ for _ in ()).throw(RuntimeError('interrupted projection')))
    with pytest.raises(RuntimeError):flow.run()
    assert driver.created==1
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='unknown'
    assert not store.db.execute('SELECT 1 FROM workday_drafts').fetchone()
    with pytest.raises(Blocked,match='remote_draft_uncertain'):NativeFlow(controller,driver).run()
    assert driver.created==1



@pytest.mark.parametrize('state',['intent','unknown','acknowledged'])
def test_captured_creation_without_projection_must_be_adopted_before_any_second_create(store,wd_job,monkeypatch,state):
    controller,driver=native_controller(store,wd_job,monkeypatch)
    posting=driver.posting_identifier()
    url=native_url('jobapplication',f'jobpostings/{posting}/jobapplications',old=True)
    key='My Information:'+digest(['create',url])
    store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('captured-native-create',wd_job['id'],NVIDIA,key,digest('{}'),'captured-source',state,'created','updated','private-proof-pointer'))
    with pytest.raises(Blocked,match='remote_draft_uncertain'):NativeFlow(controller,driver)._draft()
    assert driver.created==0
    assert store.db.execute('SELECT COUNT(*) FROM remote_draft_steps').fetchone()[0]==1
