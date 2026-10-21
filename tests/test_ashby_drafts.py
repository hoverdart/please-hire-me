"""Public-protocol fixtures: field saves and final submissions are distinct."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.ashby_drafts import AshbyDraft,VARIABLES
from hireme.browser import Browser
from hireme.util import Blocked,digest
from tests.ashby_document_fixture import AshbyDocuments

QUERY=(Path(__file__).parent/'fixtures/ashby-set-form-value.graphql').read_text()
REQ='11111111-1111-4111-8111-111111111111'
RENDER='22222222-2222-4222-8222-222222222222'
DEFINITION='33333333-3333-4333-8333-333333333333'


def form(values=None):
    values=values or {}
    return {'id':RENDER,'sourceFormDefinitionId':DEFINITION,'errorMessages':[],'formErrors':[],
            'sections':[{'fieldEntries':[{'id':RENDER+'_'+path,'field':{'path':path,'title':title},
                'fieldValue':{'value':values[path]} if path in values else None}
                for path,title in [('_systemfield_name','Name'),('_systemfield_email','Email')]+([('_systemfield_phone','Phone')] if '_systemfield_phone' in values else [])]}]}


def request(value='test@candidate.invalid',**changes):
    variables={'organizationHostedJobsPageName':'acme','formRenderIdentifier':RENDER,
               'formDefinitionIdentifier':DEFINITION,'path':'_systemfield_email','value':value}
    variables.update(changes)
    data={'operationName':'ApiSetFormValue','query':QUERY,'variables':variables}
    return SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',post_data=json.dumps(data)),data


def draft(store,job,package,definition=DEFINITION):
    ashby={**job,'url':f'https://jobs.ashbyhq.com/acme/{REQ}','host':'jobs.ashbyhq.com'}
    ashby['id']=digest(ashby['url']);store.upsert_job(ashby)
    proof={**package,'job_id':ashby['id'],'url':ashby['url'],'steps':[]}
    proof['answers'][0]['field'].update(ashby_path='_systemfield_email',ashby_entry_id=RENDER+'__systemfield_email')
    d=AshbyDraft(store,ashby)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                            'variables':{'organizationHostedJobsPageName':'acme','jobPostingId':REQ}}))
    official=form();official['sourceFormDefinitionId']=definition
    response=SimpleNamespace(status=200,json=lambda:{'data':{'jobPosting':{'id':REQ,'applicationForm':official}}})
    d.observe_posting(read,response);d.enable(None,proof['answers'],proof)
    return d,ashby


def submission_fixture(store,job,package,surveys=False):
    from hireme.ashby_submission import AshbySubmission
    d,_=draft(store,job,package)
    action='44444444-4444-4444-8444-444444444444'
    official=form({'_systemfield_email':store.facts()['email']['value']})
    official['formControls']=[{'identifier':action,'title':'Submit','__typename':'FormControl'}]
    survey={'id':'55555555-5555-4555-8555-555555555555','sourceFormDefinitionId':DEFINITION,
            'formControls':[{'identifier':action,'title':'Submit','__typename':'FormControl'}],'sections':[]}
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                            'variables':{'organizationHostedJobsPageName':'acme','jobPostingId':REQ}}))
    posting={'id':REQ,'applicationForm':official,'surveyForms':[survey] if surveys else []}
    d.observe_posting(read,SimpleNamespace(status=200,json=lambda:{'data':{'jobPosting':posting}}))
    d.enable(None,d.package['answers'],d.package)
    aid=store.prepare(d.job,d.package);grant=AshbySubmission(d,aid)
    variables={'organizationHostedJobsPageName':'acme','jobPostingId':REQ,'recaptchaToken':'synthetic-human-token',
               'sourceAttributionCode':None,'viewedAutomatedProcessingLegalNoticeRuleId':None,'deviceFingerprint':None}
    if surveys:
        operation='ApiSubmitMultipleFormsAction';filename='ashby-submit-multiple-forms.graphql'
        variables.update(applicationFormRenderIdentifier=RENDER,applicationFormDefinitionIdentifier=DEFINITION,
            applicationFormActionIdentifier=action,surveyIdentifiers=[{'formRenderId':survey['id'],
                'sourceFormDefinitionId':DEFINITION,'actionIdentifier':action}])
    else:
        operation='ApiSubmitSingleApplicationFormAction';filename='ashby-submit-single-application-form.graphql'
        variables.update(formRenderIdentifier=RENDER,formDefinitionIdentifier=DEFINITION,actionIdentifier=action)
    data={'operationName':operation,'query':(Path(__file__).parent/'fixtures'/filename).read_text(),'variables':variables}
    request=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql')
    return d,grant,request,data


def submission_response(request,data,surveys=False,**changes):
    request.post_data=json.dumps(data)
    value={'applicationFormResult':{'__typename':'FormSubmitSuccess'},
           'messages':{'blockMessageForCandidateHtml':None}}
    if surveys:value['surveyFormResults']=[{'__typename':'FormSubmitSuccess'}]
    result={'data':{'submitMultipleFormsAction' if surveys else 'submitApplicationFormAction':value}}
    return SimpleNamespace(request=request,status=changes.get('status',200),json=lambda:changes.get('result',result))


@pytest.mark.parametrize('surveys',[False,True])
@pytest.mark.parametrize('change',['none','other_request','http_error','graphql_error','missing','wrong_type','blocked','bad_surveys','bad_messages'])
def test_native_submission_ack_requires_correlated_success_for_every_form(store,job,package,surveys,change):
    _,grant,r,data=submission_fixture(store,job,package,surveys);store.begin_submit(grant.aid)
    assert grant.allow(r,data)
    response=submission_response(r,data,surveys);result=response.json()
    value=result['data']['submitMultipleFormsAction' if surveys else 'submitApplicationFormAction']
    if change=='other_request':r.post_data=json.dumps({**data,'variables':{**data['variables'],'recaptchaToken':'different-token'}})
    elif change=='http_error':response.status=500
    elif change=='graphql_error':result['errors']=[{'message':'Synthetic error'}]
    elif change=='missing':result.clear()
    elif change=='wrong_type':value['applicationFormResult']['__typename']='FormSubmitError'
    elif change=='blocked':value['messages']['blockMessageForCandidateHtml']='Synthetic block'
    elif change=='bad_messages':value['messages']=[]
    elif change=='bad_surveys':
        if surveys:value['surveyFormResults']=[]
        else:value['applicationFormResult']=None
    grant.observe_response(response)
    assert grant.acknowledged==(change=='none')
    assert grant.response_seen==(change!='other_request')
    assert grant.failed==(change not in {'none','other_request'})
    assert not grant.allow(r,data)
    if change!='other_request':
        grant.observe_response(submission_response(r,data,surveys))
        assert grant.acknowledged==(change=='none')
    details=' '.join(x[0] for x in store.db.execute('SELECT detail FROM events'))
    assert 'synthetic-human-token' not in details and 'different-token' not in details


@pytest.mark.parametrize('change',['query','hidden_mutation','batch_mutation','endpoint','host','upload'])
def test_human_handoff_route_blocks_writes_disguised_as_reads_and_late_uploads(store,job,package,monkeypatch,change):
    from hireme.browser import ASHBY_UPLOAD_HOST
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,grant,r,data=submission_fixture(store,job,package);store.begin_submit(grant.aid)
    b=Browser(store);b.current_host='jobs.ashbyhq.com';b.ashby_submission=grant;b.attempted=True
    payload={'operationName':'Read','query':'query Read { jobPosting { id } }','variables':{}}
    if change=='hidden_mutation':payload.update(operationName='Write',query=payload['query']+' mutation Write { submitSomething }')
    elif change=='batch_mutation':payload=[payload,{'query':'mutation Write { submitSomething }','operationName':'Write'}]
    elif change=='endpoint':r.url='https://jobs.ashbyhq.com/other'
    elif change=='host':r.url='https://api.ashbyhq.com/api/non-user-graphql'
    elif change=='upload':
        r.url='https://'+ASHBY_UPLOAD_HOST+'/upload';r.post_data_buffer=b'approved';b.upload_payloads={'hash':b'approved'}
    r.post_data=json.dumps(payload)
    actions=[];route=SimpleNamespace(request=r,abort=lambda:actions.append('abort'),continue_=lambda:actions.append('continue'))
    b._route(route)
    assert actions==['continue' if change=='query' else 'abort'] and not grant.used


@pytest.mark.parametrize('surveys',[False,True])
def test_native_submission_grant_requires_one_committed_intent_and_keeps_tokens_private(store,job,package,surveys):
    d,grant,r,data=submission_fixture(store,job,package,surveys)
    assert not grant.allow(r,data)
    store.begin_submit(grant.aid)
    assert grant.allow(r,data) and not grant.allow(r,data)
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(grant.aid,)).fetchone()[0]=='submitting'
    details=' '.join(x[0] for x in store.db.execute('SELECT detail FROM events'))
    assert 'synthetic-human-token' not in details
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='ashby_submission_write'").fetchone()[0]==1


@pytest.mark.parametrize('expires_during_validation',[False,True])
def test_native_submission_deadline_blocks_a_late_human_completion(store,job,package,monkeypatch,expires_during_validation):
    _,grant,r,data=submission_fixture(store,job,package);store.begin_submit(grant.aid)
    clock=[grant.write_deadline if not expires_during_validation else grant.write_deadline-1]
    monkeypatch.setattr('hireme.ashby_submission.time.monotonic',lambda:clock[0])
    actual_validate=grant.valid_intent
    def validate():
        valid=actual_validate();clock[0]=grant.write_deadline
        return valid
    monkeypatch.setattr(grant,'valid_intent',validate)
    assert not grant.allow(r,data) and not grant.used
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_submission_write'").fetchone()


@pytest.mark.parametrize('surveys',[False,True])
@pytest.mark.parametrize('change',['tenant','posting','render','definition','action','empty_token','missing_token',
    'fingerprint','answer','attribution','notice','application_request','query','operation','endpoint','method','extra_survey'])
def test_native_submission_grant_rejects_changed_payloads_and_destinations(store,job,package,surveys,change):
    d,grant,r,data=submission_fixture(store,job,package,surveys);store.begin_submit(grant.aid)
    v=data['variables'];prefix='applicationForm' if surveys else 'form'
    if change=='tenant':v['organizationHostedJobsPageName']='another-employer'
    elif change=='posting':v['jobPostingId']=RENDER
    elif change=='render':v[prefix+'RenderIdentifier']=REQ
    elif change=='definition':v[prefix+'DefinitionIdentifier']=REQ
    elif change=='action':v['applicationFormActionIdentifier' if surveys else 'actionIdentifier']=REQ
    elif change=='empty_token':v['recaptchaToken']=''
    elif change=='missing_token':v.pop('recaptchaToken')
    elif change=='fingerprint':v['deviceFingerprint']={'answer':'not-a-fingerprint'}
    elif change=='answer':v['answer']='unapproved field'
    elif change=='attribution':v['sourceAttributionCode']='changed'
    elif change=='notice':v['viewedAutomatedProcessingLegalNoticeRuleId']=REQ
    elif change=='application_request':v['applicationRequestId']=REQ
    elif change=='query':data['query']+=' mutation Other { writeSomething }'
    elif change=='operation':data['operationName']='ApiSetFormValue'
    elif change=='endpoint':r.url='https://jobs.ashbyhq.com/other-endpoint'
    elif change=='method':r.method='GET'
    else:v['surveyIdentifiers']=[*(v.get('surveyIdentifiers') or []),{'formRenderId':REQ}]
    assert not grant.allow(r,data) and not grant.used
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_submission_write'").fetchone()


@pytest.mark.parametrize('change',['pause','pause_resume','boundary','facts','source','unknown'])
def test_native_submission_grant_stops_when_approval_or_attempt_changes(store,job,package,change):
    d,grant,r,data=submission_fixture(store,job,package);store.begin_submit(grant.aid)
    if change=='pause':store.update_settings({'live_enabled':False})
    elif change=='pause_resume':store.update_settings({'live_enabled':False});store.update_settings({'live_enabled':True})
    elif change=='boundary':store.update_settings({'max_per_company':1})
    elif change=='facts':store.put_facts({'skills':'Python, TypeScript, Rust'})
    elif change=='source':d.submission_contexts['acme']['application']['formControls'][0]['identifier']=REQ
    else:store.finish(grant.aid,'unknown','Synthetic interrupted attempt')
    try:assert not grant.allow(r,data)
    except Blocked:pass
    assert not grant.used
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_submission_write'").fetchone()


@pytest.mark.parametrize('change',['value','missing_value','definition','unacknowledged','action_title','duplicate_control'])
def test_native_submission_grant_requires_current_field_acknowledgements_and_submit_action(store,job,package,change):
    from hireme.ashby_submission import AshbySubmission
    d,grant,_,_=submission_fixture(store,job,package)
    if change=='value':d.forms[RENDER]['values']['_systemfield_email']='unapproved@candidate.invalid'
    elif change=='missing_value':d.forms[RENDER]['values'].pop('_systemfield_email')
    elif change=='definition':
        d.forms[RENDER]['definition']=REQ;d.submission_contexts['acme']['application']['sourceFormDefinitionId']=REQ
    elif change=='unacknowledged':d.saved.clear()
    elif change=='action_title':d.submission_contexts['acme']['application']['formControls'][0]['title']='Accept contract'
    else:d.submission_contexts['acme']['application']['formControls'].append(dict(d.submission_contexts['acme']['application']['formControls'][0]))
    with pytest.raises(Blocked,match='ashby_submission_unsupported'):AshbySubmission(d,grant.aid)


def human_wait_fixture(store,job,package,monkeypatch,surveys=False,on_wait=None):
    store.update_settings({'headless':False,'human_verification_wait_seconds':1})
    d,grant,r,data=submission_fixture(store,job,package,surveys);store.begin_submit(grant.aid)
    b=Browser(store);b.aid=grant.aid;b.attempted=True;b.ashby_draft=d;b.ashby_submission=grant
    b.visible=True;b.human_verification_seconds=1;clock=[0]
    monkeypatch.setattr('hireme.browser.time.monotonic',lambda:clock[0])
    class Page:
        def locator(self,selector):return SimpleNamespace(count=lambda:0)
        def wait_for_timeout(self,ms):
            clock[0]+=ms/1000
            if on_wait:on_wait(d,grant,r,data,clock[0])
    b.page=Page()
    def guard(*args,**kwargs):
        if not grant.used:raise Blocked('captcha_blocked')
        return 'Thank you for applying. Your application has been received.'
    monkeypatch.setattr(b,'_guard',guard)
    return b,d,grant,r,data


@pytest.mark.parametrize('surveys',[False,True])
def test_human_wait_keeps_same_intent_then_checks_receipt_without_interacting_with_widget(store,job,package,monkeypatch,surveys):
    def human_completed(d,grant,r,data,elapsed):
        if elapsed>=.5 and not grant.used:
            assert grant.allow(r,data)
            grant.observe_response(submission_response(r,data,surveys))
    b,d,grant,_,_=human_wait_fixture(store,job,package,monkeypatch,surveys,human_completed)
    text=b._wait_submission_outcome(d.job)
    assert 'application has been received' in text and grant.used
    events=[json.loads(x[0]) for x in store.db.execute("SELECT detail FROM events WHERE kind='human_verification' ORDER BY seq")]
    assert [x['phase'] for x in events]==['challenge','checking_receipt','finished']
    assert events[-1]['result']=='outcome_check_complete'
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0]==1
    assert not hasattr(b.page,'click')


@pytest.mark.parametrize('change',['timeout','pause','pause_resume','source','facts'])
def test_human_wait_cancels_without_granting_a_request(store,job,package,monkeypatch,change):
    def changed(d,grant,r,data,elapsed):
        if elapsed>.25:return
        if change=='pause':store.update_settings({'live_enabled':False})
        elif change=='pause_resume':store.update_settings({'live_enabled':False});store.update_settings({'live_enabled':True})
        elif change=='source':d.submission_contexts['acme']['application']['formControls'][0]['identifier']=REQ
        elif change=='facts':store.put_facts({'skills':'Python, TypeScript, Rust'})
    b,d,grant,_,_=human_wait_fixture(store,job,package,monkeypatch,on_wait=changed)
    with pytest.raises(Blocked):b._wait_submission_outcome(d.job)
    assert not grant.used
    events=[json.loads(x[0]) for x in store.db.execute("SELECT detail FROM events WHERE kind='human_verification' ORDER BY seq")]
    assert events[0]['phase']=='challenge' and events[-1]['phase']=='finished'
    assert events[-1]['result']==('timed_out' if change=='timeout' else 'interrupted')
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_submission_write'").fetchone()


def test_human_wait_does_not_wait_without_opt_in(store,job,package,monkeypatch):
    b,d,grant,r,data=human_wait_fixture(store,job,package,monkeypatch)
    b.human_verification_seconds=0
    def exposed(*args,**kwargs):raise Blocked('captcha_blocked')
    monkeypatch.setattr(b,'_guard',exposed)
    with pytest.raises(Blocked,match='captcha_blocked'):b._wait_submission_outcome(d.job)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='human_verification'").fetchone()


@pytest.mark.parametrize('acknowledged',[False,True])
def test_human_wait_observes_sent_request_with_lingering_frame_without_restarting(store,job,package,monkeypatch,acknowledged):
    b,d,grant,r,data=human_wait_fixture(store,job,package,monkeypatch)
    assert grant.allow(r,data)
    if acknowledged:grant.observe_response(submission_response(r,data))
    def exposed(*args,**kwargs):raise Blocked('captcha_blocked')
    monkeypatch.setattr(b,'_guard',exposed)
    b.page.locator=lambda selector:SimpleNamespace(count=lambda:0,inner_text=lambda **kwargs:'Thank you for applying. Your application has been received.')
    assert 'application has been received' in b._wait_submission_outcome(d.job)
    phases=[json.loads(x[0])['phase'] for x in store.db.execute("SELECT detail FROM events WHERE kind='human_verification' ORDER BY seq")]
    assert phases==['checking_receipt','finished']
    assert not grant.allow(r,data)
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='ashby_submission_write'").fetchone()[0]==1


@pytest.mark.parametrize('surveys',[False,True])
@pytest.mark.parametrize('mode',['complete','lingering_frame','bad_ack','changed_payload','changed_field','timeout','pause'])
def test_browser_human_handoff_preserves_one_intent_and_exact_native_submission(store,monkeypatch,surveys,mode):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    job={'id':digest(REQ),'url':f'https://jobs.ashbyhq.com/acme/{REQ}','host':'jobs.ashbyhq.com',
         'company':'Acme','title':'Software Engineer Intern Summer 2027','description':'Build Python software.',
         'location':'San Francisco, United States','source':'fixture'}
    store.upsert_job(job);remote={};writes=[];clicks=[]
    documents=AshbyDocuments(store,'acme',REQ,RENDER,DEFINITION)
    action='44444444-4444-4444-8444-444444444444'
    survey={'id':'55555555-5555-4555-8555-555555555555','sourceFormDefinitionId':DEFINITION,
            'formControls':[{'identifier':action,'title':'Submit','__typename':'FormControl'}],'sections':[]}
    def native_form():
        value=form(remote);value['formControls']=[{'identifier':action,'title':'Submit','__typename':'FormControl'}]
        return documents.form(value)
    variables={'organizationHostedJobsPageName':'acme','jobPostingId':REQ,'recaptchaToken':'fixture-human-token',
               'sourceAttributionCode':None,'viewedAutomatedProcessingLegalNoticeRuleId':None,'deviceFingerprint':None}
    operation='ApiSubmitMultipleFormsAction' if surveys else 'ApiSubmitSingleApplicationFormAction'
    filename='ashby-submit-multiple-forms.graphql' if surveys else 'ashby-submit-single-application-form.graphql'
    if surveys:
        variables.update(applicationFormRenderIdentifier=RENDER,applicationFormDefinitionIdentifier=DEFINITION,
            applicationFormActionIdentifier=action,surveyIdentifiers=[{'formRenderId':survey['id'],
                'sourceFormDefinitionId':DEFINITION,'actionIdentifier':action}])
    else:variables.update(formRenderIdentifier=RENDER,formDefinitionIdentifier=DEFINITION,actionIdentifier=action)
    final_payload={'operationName':operation,'query':(Path(__file__).parent/'fixtures'/filename).read_text(),'variables':variables}
    metadata=[('_systemfield_name','Name','text'),('_systemfield_email','Email','email')]
    inputs=''.join(f'<div data-field-path="{path}" data-field-entry-id="{RENDER}_{path}"><label>{title}<input id="{path}" name="{path}" type="{kind}" required></label></div>' for path,title,kind in metadata)
    html='<h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p><form>'+inputs+'''<label>Resume<input type="file" name="resume" required></label><button type="submit">Submit Application</button></form><script>
const endpoint='/api/non-user-graphql';const query='''+json.dumps(QUERY)+''';const finalPayload='''+json.dumps(final_payload)+''';
fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiJobPosting',query:'query ApiJobPosting { jobPosting { id } }',variables:{organizationHostedJobsPageName:'acme',jobPostingId:'''+json.dumps(REQ)+'''}})});
for(const el of document.querySelectorAll('input:not([type=file])'))el.addEventListener('input',()=>{
window.fixtureLastSave=fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiSetFormValue',query,variables:{organizationHostedJobsPageName:'acme',formRenderIdentifier:'''+json.dumps(RENDER)+''',formDefinitionIdentifier:'''+json.dumps(DEFINITION)+''',path:el.id,value:el.value}})}).catch(()=>{});
});
document.querySelector('form').onsubmit=e=>{e.preventDefault();window.fixtureClicks=(window.fixtureClicks||0)+1;
const frame=document.createElement('iframe');frame.id='fixture-challenge';frame.src='https://www.google.com/recaptcha/api2/bframe';frame.style='width:300px;height:100px';document.body.append(frame);};
window.fixtureHumanCompleted=async mode=>{
if(mode==='changed_field'){const input=document.getElementById('_systemfield_email');input.value='unapproved@candidate.invalid';input.dispatchEvent(new Event('input'));await window.fixtureLastSave;}
if(mode!=='lingering_frame')document.getElementById('fixture-challenge').remove();
if(mode==='changed_payload')finalPayload.variables.answer='unapproved';
try{const response=await fetch(endpoint,{method:'POST',body:JSON.stringify(finalPayload)});if(response.ok){
const frame=document.getElementById('fixture-challenge');document.body.innerHTML='Thank you for applying. Your application was received.';if(frame)document.body.append(frame);}}catch{}
};</script>'''
    html=documents.html(html)
    headed=os.environ.get('HIREME_TEST_HUMAN_VISIBLE')=='1'
    if headed:store.update_settings({'headless':False})
    with Browser(store) as b:
        # The fixture drives actual frame detection and request routing in a
        # headless test browser. Visible desktop operation is a separate gate.
        # Native browser/approval work must fit on the Pi as well as the Mac.
        # Only the explicit timeout case tests the one-second deadline; other
        # cases must reach their intended payload/acknowledgement checks.
        store.update_settings({'headless':False,'human_verification_wait_seconds':1 if mode=='timeout' else 10})
        if not headed:b.visible=True
        assert b.visible
        actual_event=store.event
        def event(kind,subject,detail):
            actual_event(kind,subject,detail)
            if kind=='human_verification' and detail['phase']=='challenge':
                clicks.append(b.page.evaluate('()=>window.fixtureClicks'))
                if mode=='pause':store.update_settings({'live_enabled':False})
                elif mode!='timeout':b.page.evaluate('(mode)=>window.fixtureHumanCompleted(mode)',mode)
        monkeypatch.setattr(store,'event',event)
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self,**changes):
                if documents.respond(b,self.route,changes):return
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiJobPosting':result={'data':{'jobPosting':{'id':REQ,'applicationForm':native_form(),'surveyForms':[survey] if surveys else []}}}
                elif data['operationName']=='ApiSetFormValue':
                    assert not b.attempted
                    v=data['variables'];remote[v['path']]=v['value'];result={'data':{'setFormValue':native_form()}}
                else:
                    assert data['operationName']==operation and remote=={'_systemfield_name':'Test Person','_systemfield_email':'test@candidate.invalid'}
                    writes.append(data);result=submission_response(SimpleNamespace(),data,surveys).json()
                    if mode=='bad_ack':result={'data':{'submitted':True}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
        def serve(route):
            if route.request.method=='GET':
                route.fulfill(status=200,content_type='text/html',body='<p>Synthetic challenge</p>' if 'www.google.com' in route.request.url else html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        if mode in {'complete','lingering_frame'}:assert b.apply(job)=='confirmed'
        elif mode=='bad_ack':assert b.apply(job)=='unknown'
        else:
            with pytest.raises(Blocked):b.apply(job)
        assert clicks==[1]
    assert writes==([final_payload] if mode in {'complete','lingering_frame','bad_ack'} else [])
    if mode=='bad_ack':
        assert b.ashby_submission.used and not b.ashby_submission.acknowledged
        responses=store.db.execute("SELECT detail FROM events WHERE kind='ashby_submission_response'").fetchall()
        assert len(responses)==1 and json.loads(responses[0][0])=={'acknowledged':False}
    assert store.db.execute('SELECT state FROM applications').fetchone()[0]==('confirmed' if mode in {'complete','lingering_frame'} else 'unknown')
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0]==1


def composite_definition(**changes):
    return json.dumps({'kind':'CompositeFormDefinitionId-JobPostingApplicationFormV2',
        'formDefinitionId':DEFINITION,'jobPostingId':REQ,'jobBoardSuperType':'External',**changes},separators=(',',':'))


def test_exact_composite_definition_from_official_posting_can_save_approved_field(store,job,package):
    definition=composite_definition();d,_=draft(store,job,package,definition)
    r,data=request(formDefinitionIdentifier=definition)
    assert d.allow(r,data,'request') and not d.complete
    acknowledgement=form({'_systemfield_email':'test@candidate.invalid'})
    acknowledgement['sourceFormDefinitionId']=definition
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':acknowledgement}}),'request')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('definition',[
    composite_definition(jobPostingId='44444444-4444-4444-8444-444444444444'),
    composite_definition(kind='UnreviewedCompositeDefinition'),
    composite_definition(jobBoardSuperType='Internal'),
    composite_definition(formDefinitionId='unverified'),
    composite_definition(extra='unreviewed'),
    json.dumps({'formDefinitionId':DEFINITION,'jobPostingId':REQ}),
    composite_definition()[:-1]+',"jobPostingId":"'+REQ+'"}',
    json.loads(composite_definition()),
    'not a definition',
    'a'*513,
])
def test_unverified_composite_definition_never_creates_field_save_grants(store,job,package,definition):
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):draft(store,job,package,definition)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind IN ('ashby_draft_write','submit_intent')").fetchone()


@pytest.mark.parametrize('change',['request','acknowledgement'])
def test_changed_composite_definition_cannot_borrow_a_grant_or_acknowledgement(store,job,package,change):
    definition=composite_definition();d,_=draft(store,job,package,definition)
    changed=composite_definition(formDefinitionId='44444444-4444-4444-8444-444444444444')
    r,data=request(formDefinitionIdentifier=changed if change=='request' else definition)
    if change=='request':
        assert not d.allow(r,data,'request') and not d.pending
    else:
        assert d.allow(r,data,'request')
        acknowledgement=form({'_systemfield_email':'test@candidate.invalid'});acknowledgement['sourceFormDefinitionId']=changed
        d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':acknowledgement}}),'request')
        assert d.failed and not d.complete


def test_exact_field_save_requires_verified_form_sources_and_acknowledgement(store,job,package):
    d,_=draft(store,job,package);r,data=request()
    assert d.allow(r,data,'request') and not d.complete
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':form({'_systemfield_email':'test@candidate.invalid'})}}),'request')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['exact','different','missing','error','wrong_entry'])
def test_only_exact_error_free_official_prefills_count_as_saved(store,job,package,mode):
    d,_=draft(store,job,package)
    saved=form({'_systemfield_email':'test@candidate.invalid'})
    entry=saved['sections'][0]['fieldEntries'][1]
    if mode=='different':entry['fieldValue']['value']='other@candidate.invalid'
    if mode=='missing':entry['fieldValue']=None
    if mode=='error':saved['formErrors']=['Synthetic form error']
    if mode=='wrong_entry':entry['id']='another-entry'
    d._remember_form(saved)
    if mode=='wrong_entry':
        with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,d.package['answers'],d.package)
        return
    d.enable(None,d.package['answers'],d.package)
    assert d.complete is (mode=='exact')
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_draft_write'").fetchone()


@pytest.mark.parametrize('changes',[
    {'organizationHostedJobsPageName':'other'}, {'formRenderIdentifier':'other'},
    {'formDefinitionIdentifier':'other'}, {'path':'unapproved'}, {'value':'unapproved@candidate.invalid'},
    {'extra':'unreviewed'}, {'value':True},
])
def test_changed_destination_path_or_value_has_no_write_grant(store,job,package,changes):
    d,_=draft(store,job,package);r,data=request(**changes)
    assert not d.allow(r,data,'request') and not d.pending


@pytest.mark.parametrize('change',['query','operation','batch'])
def test_no_additional_mutation_or_batch_can_borrow_a_field_grant(store,job,package,change):
    d,_=draft(store,job,package);r,data=request()
    if change=='query':data['query']+=' mutation Other { submitApplication { id } }'
    elif change=='operation':data['operationName']='ApiSubmitApplication'
    else:data=[data]
    assert not d.allow(r,data,'request')


def test_changed_sources_and_pause_revoke_an_existing_field_grant(store,job,package):
    d,_=draft(store,job,package);r,data=request()
    store.put_facts({'email':'changed@candidate.invalid'})
    with pytest.raises(Blocked):d.allow(r,data,'request')
    assert not d.pending
    store.update_settings({'live_enabled':False})
    with pytest.raises(Blocked,match='paused'):d.allow(r,data,'request')


@pytest.mark.parametrize('mode',['http_error','graphql_error','wrong_render','wrong_value','missing_value'])
def test_transport_success_alone_is_not_a_saved_field(store,job,package,mode):
    d,_=draft(store,job,package);r,data=request();assert d.allow(r,data,'request')
    saved=form({'_systemfield_email':'test@candidate.invalid'})
    if mode=='wrong_render':saved['id']='other'
    if mode=='wrong_value':saved['sections'][0]['fieldEntries'][1]['fieldValue']['value']='different'
    if mode=='missing_value':saved['sections'][0]['fieldEntries'][1]['fieldValue']=None
    result={'data':{'setFormValue':saved}}
    if mode=='graphql_error':result['errors']=[{'message':'Synthetic failure'}]
    d.observe_save(SimpleNamespace(status=500 if mode=='http_error' else 200,json=lambda:result),'request')
    assert d.failed and not d.complete


@pytest.mark.parametrize('restored_phone',[False,True])
@pytest.mark.parametrize('live,ack',[(True,True),(False,True),(True,False)])
def test_browser_saves_only_approved_fields_before_final_intent_and_keeps_preparation_local(store,monkeypatch,live,ack,restored_phone):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    job={'id':digest(REQ),'url':f'https://jobs.ashbyhq.com/acme/{REQ}', 'host':'jobs.ashbyhq.com',
         'company':'Acme','title':'Software Engineer Intern Summer 2027','description':'Build Python software.',
         'location':'San Francisco, United States','source':'fixture'}
    store.upsert_job(job);remote={};final=[]
    documents=AshbyDocuments(store,'acme',REQ,RENDER,DEFINITION)
    action='44444444-4444-4444-8444-444444444444'
    native={'operationName':'ApiSubmitSingleApplicationFormAction',
            'query':(Path(__file__).parent/'fixtures/ashby-submit-single-application-form.graphql').read_text(),
            'variables':{'organizationHostedJobsPageName':'acme','jobPostingId':REQ,'recaptchaToken':'synthetic-token',
                         'formRenderIdentifier':RENDER,'formDefinitionIdentifier':DEFINITION,'actionIdentifier':action}}
    def official_form():
        value=form(remote)
        value['formControls']=[{'identifier':action,'title':'Submit','__typename':'FormControl'}]
        return documents.form(value)
    metadata=[('_systemfield_name','Name','text'),('_systemfield_email','Email','email')]
    if restored_phone:
        remote['_systemfield_phone']=store.facts()['phone']['value']
        metadata.append(('_systemfield_phone','Phone','tel'))
    initial=dict(remote)
    inputs=''.join(f'<div data-field-path="{path}" data-field-entry-id="{RENDER}_{path}"><label>{title}<input id="{path}" name="{path}" type="{kind}" value="{remote.get(path, "")}" required></label></div>' for path,title,kind in metadata)
    html='''<html><body><h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p><form>'''+inputs+'''<label>Resume<input type="file" name="resume" required></label><button type="submit">Submit Application</button></form><script>
const query='''+json.dumps(QUERY)+''';
const endpoint='/api/non-user-graphql';
const initial='''+json.dumps(initial)+''';
fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiJobPosting',query:'query ApiJobPosting { jobPosting { id } }',variables:{organizationHostedJobsPageName:'acme',jobPostingId:'''+json.dumps(REQ)+'''}})});
for(const el of document.querySelectorAll('input:not([type=file])'))el.addEventListener('input',()=>{if(el.value===initial[el.id])return;fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiSetFormValue',query,variables:{organizationHostedJobsPageName:'acme',formRenderIdentifier:'''+json.dumps(RENDER)+''',formDefinitionIdentifier:'''+json.dumps(DEFINITION)+''',path:el.id,value:el.value}})}).catch(()=>{})});
document.querySelector('form').onsubmit=async e=>{e.preventDefault();const r=await fetch(endpoint,{method:'POST',body:JSON.stringify('''+json.dumps(native)+''')});if(r.ok)document.body.innerHTML='Thank you for applying. Your application was received.'};
</script></body></html>'''
    html=documents.html(html)
    with Browser(store) as b:
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self,**changes):
                if documents.respond(b,self.route,changes):return
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiJobPosting':result={'data':{'jobPosting':{'id':REQ,'applicationForm':official_form()}}}
                elif data['operationName']=='ApiSetFormValue':
                    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    v=data['variables'];remote[v['path']]=v['value'];result={'data':{'setFormValue':official_form()}}
                    if not ack:result['errors']=[{'message':'Synthetic save rejected'}]
                else:
                    assert data==native
                    assert store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    assert remote=={**initial,'_systemfield_name':'Test Person','_systemfield_email':'test@candidate.invalid'}
                    final.append(data);result={'data':{'submitApplicationFormAction':{'applicationFormResult':{'__typename':'FormSubmitSuccess'},'messages':{}}}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        if live and not ack:
            with pytest.raises(Blocked,match='ashby_draft_save_failed'):b.apply(job,live=True)
            assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
        else:assert b.apply(job,live=live)==('confirmed' if live else 'prepared')
        if live and ack:
            with pytest.raises(Blocked):b.apply(job)
    assert len(final)==int(live and ack)
    if not live:assert remote==initial


def test_saved_form_can_reveal_a_conditional_field_without_authorizing_its_value(store,job,package):
    d,_=draft(store,job,package);r,data=request();assert d.allow(r,data,'request')
    saved=form({'_systemfield_email':'test@candidate.invalid'})
    saved['sections'][0]['fieldEntries'].append({'id':RENDER+'_conditional','field':{'path':'conditional','title':'New employer question'},'fieldValue':None})
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':saved}}),'request')
    assert 'conditional' in d.forms[RENDER]['entries']
    r,data=request(path='conditional',value='Invented answer')
    assert not d.allow(r,data,'new-request')


@pytest.mark.parametrize('url,method',[
    ('https://attacker.invalid/api/non-user-graphql','POST'),
    ('https://jobs.ashbyhq.com/another-path','POST'),
    ('http://jobs.ashbyhq.com/api/non-user-graphql','POST'),
    ('https://jobs.ashbyhq.com/api/non-user-graphql','PUT'),
])
def test_exact_field_grant_has_no_other_transport_authority(store,job,package,url,method):
    d,_=draft(store,job,package);r,data=request();r.url=url;r.method=method
    assert not d.allow(r,data,'request')


def test_field_grant_does_not_send_extra_envelope_data_or_url_actions(store,job,package):
    d,_=draft(store,job,package);r,data=request();data['extensions']={'unreviewed':'extra-data'}
    assert not d.allow(r,data,'extra')
    r,data=request();r.url+='?op=ApiSubmitApplication'
    assert not d.allow(r,data,'other-operation')
    r.url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSetFormValue'
    assert d.allow(r,data,'approved')

LOCATION_QUERY=(Path(__file__).parent/'fixtures/ashby-autocomplete-location.graphql').read_text()
LOCATION='Berkeley, California, United States'

SCHOOL_QUERY=(Path(__file__).parent/'fixtures/ashby-search-school.graphql').read_text()
SCHOOL='University of California, Berkeley'
SCHOOL_PATH='_systemfield_education_history'


def choice_draft(store,job,package,kind):
    from hireme.answers import resolve
    store.put_facts({'degree':'BS'})
    store.update_settings({'contextual_preferences':True})
    d,_=draft(store,job,{**package,'facts_hash':digest(store.facts())})
    multiple=kind=='checkbox-group'
    field={'index':0,'indices':list(range(3 if multiple else 2)),
        'label':'Which programming languages are you experienced with?' if multiple else 'Degree Level Currently Pursuing',
        'type':kind,'required':True,'options':['Python','TypeScript','Rust'] if multiple else ["Bachelor's","Master's"],
        'maxlength':-1,'value':'','ashby_path':'fixture-choice','ashby_entry_id':RENDER+'_fixture-choice',
        'ref':{'id':'choice-0'},'refs':[{'id':'choice-'+str(i)} for i in range(3 if multiple else 2)]}
    values=['python-id','typescript-id','rust-id'] if multiple else ['bachelors-id','masters-id']
    official=form();official['sections'][0]['fieldEntries'].append({'id':field['ashby_entry_id'],
        'field':{'path':'fixture-choice','title':field['label'],'type':'MultiValueSelect' if multiple else 'ValueSelect',
                 'selectableValues':[{'label':label,'value':value,'isArchived':False} for label,value in zip(field['options'],values)]},
        'fieldValue':None})
    d._remember_form(official)
    answer=resolve(store,d.job['host'],field,context=d.job)
    proof={**d.package,'answers':[answer]}
    return d,proof,official,values[:2] if multiple else values[0]


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
def test_official_choices_encode_controls_without_native_value_attributes(store,job,package,kind):
    d,proof,official,value=choice_draft(store,job,package,kind)
    no_value=SimpleNamespace(_control=lambda *args:SimpleNamespace(get_attribute=lambda name:None))
    d.enable(no_value,proof['answers'],proof)
    r,data=request(value,path='fixture-choice')
    assert d.allow(r,data,'choice') and not d.complete
    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':value}
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'choice')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
def test_trimmed_visible_choice_labels_retain_original_wire_values(store,job,package,kind):
    d,proof,official,value=choice_draft(store,job,package,kind)
    for row in d.forms[RENDER]['entries']['fixture-choice']['selectableValues']:
        row['label']=' '+row['label']+' '
        row['value']=' '+row['value']+' '
    value=[' '+item+' ' for item in value] if isinstance(value,list) else ' '+value+' '
    d.enable(None,proof['answers'],proof)
    altered=[item.strip() for item in value] if isinstance(value,list) else value.strip()
    r,data=request(altered,path='fixture-choice');assert not d.allow(r,data,'altered')
    r,data=request(value,path='fixture-choice');assert d.allow(r,data,'choice')
    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':value}
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'choice')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
@pytest.mark.parametrize('change',['missing','malformed','label','duplicate_label','trimmed_duplicate_label','blank_label','duplicate_value','archived','archive_status','nonstring_value'])
def test_unverified_official_choice_metadata_never_grants_a_write(store,job,package,kind,change):
    d,proof,_,value=choice_draft(store,job,package,kind)
    metadata=d.forms[RENDER]['entries']['fixture-choice'];rows=metadata['selectableValues']
    if change=='missing':metadata.pop('selectableValues')
    elif change=='malformed':rows.append(None)
    elif change=='label':rows[0]['label']='Different choice'
    elif change=='duplicate_label':rows[1]['label']=rows[0]['label']
    elif change=='trimmed_duplicate_label':rows[1]['label']=' '+rows[0]['label']+' '
    elif change=='blank_label':rows[0]['label']=' \t '
    elif change=='duplicate_value':rows[1]['value']=rows[0]['value']
    elif change=='archived':rows[0]['isArchived']=True
    elif change=='archive_status':rows[0]['isArchived']='false'
    elif change=='nonstring_value':rows[0]['value']=False
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    r,data=request(value,path='fixture-choice');assert not d.allow(r,data,'choice')


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
@pytest.mark.parametrize('change',['labels','unapproved','wrong_shape'])
def test_official_choice_grant_rejects_other_wire_values_and_shapes(store,job,package,kind,change):
    d,proof,_,value=choice_draft(store,job,package,kind);d.enable(None,proof['answers'],proof)
    if change=='labels':value=proof['answers'][0]['value']
    elif change=='unapproved':value=['python-id','typescript-id','rust-id'] if kind=='checkbox-group' else 'masters-id'
    else:value=value[0] if kind=='checkbox-group' else [value]
    r,data=request(value,path='fixture-choice');assert not d.allow(r,data,'choice')


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
def test_official_choice_save_requires_the_exact_acknowledged_values(store,job,package,kind):
    d,proof,official,value=choice_draft(store,job,package,kind);d.enable(None,proof['answers'],proof)
    r,data=request(value,path='fixture-choice');assert d.allow(r,data,'choice')
    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':['python-id'] if kind=='checkbox-group' else 'masters-id'}
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'choice')
    assert d.failed and not d.complete


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
@pytest.mark.parametrize('padded',[False,True])
def test_native_controls_with_default_on_save_only_approved_official_choice_values(store,job,package,monkeypatch,kind,padded):
    import time
    from html import escape
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,proof,official,value=choice_draft(store,job,package,kind);field=proof['answers'][0]['field']
    if padded:
        for row in official['sections'][0]['fieldEntries'][-1]['field']['selectableValues']:
            row['label']=' '+row['label']+' ';row['value']=' '+row['value']+' '
        value=[' '+item+' ' for item in value] if isinstance(value,list) else ' '+value+' '
    wire=[row['value'] for row in official['sections'][0]['fieldEntries'][-1]['field']['selectableValues']]
    labels=[row['label'] for row in official['sections'][0]['fieldEntries'][-1]['field']['selectableValues']]
    inputs=''.join('<label><input type="'+('radio' if kind=='radio' else 'checkbox')+'" name="choices" id="choice-'+str(i)+'">'+escape(label)+'</label>' for i,label in enumerate(labels))
    inputs='<fieldset class="'+('ashby-application-form-input-checkbox-group' if kind=='checkbox-group' else '')+'"><legend>'+escape(field['label'])+'</legend>'+inputs+'</fieldset>'
    envelope=request(value,path='fixture-choice')[1];writes=[]
    html='<h1>Synthetic native choices</h1>'+inputs+'<script>const wire='+json.dumps(wire)+';const envelope='+json.dumps(envelope)+''';
const inputs=[...document.querySelectorAll('input')];
inputs.forEach((input,index)=>input.addEventListener('change',()=>{
const value=input.type==='radio'?wire[index]:inputs.flatMap((node,i)=>node.checked?[wire[i]]:[]);
fetch('/api/non-user-graphql',{method:'POST',body:JSON.stringify({...envelope,variables:{...envelope.variables,value}})}).catch(()=>null);
}));</script>'''
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=d
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data);writes.append(data)
                official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':data['variables']['value']}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps({'data':{'setFormValue':official}}))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve);b.page.goto(d.job['url'])
        assert b.page.locator('input').evaluate_all('(nodes)=>nodes.every(n=>n.getAttribute("value")===null&&n.value==="on")')
        assert b._snapshot()[0]['options']==field['options']
        d.enable(b,proof['answers'],proof);b._fill(proof['answers'][0])
        deadline=time.monotonic()+10
        while not (d.complete or d.failed) and time.monotonic()<deadline:b.page.wait_for_timeout(50)
        assert d.complete and not d.failed
        assert len(writes)==1 and writes[0]['variables']['value']==value
        assert b.page.locator('input:checked').count()==(2 if kind=='checkbox-group' else 1)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def school_draft(store,job,package,*,observe=True,restored=None):
    from hireme.answers import resolve
    store.put_facts({'school':SCHOOL})
    package={**package,'facts_hash':digest(store.facts())}
    d,_=draft(store,job,package)
    field={'index':0,'indices':[0],'label':'College/University','type':'combobox','required':True,
           'options':[SCHOOL],'maxlength':-1,'value':'','ashby_path':SCHOOL_PATH,
           'ashby_entry_id':RENDER+'_'+SCHOOL_PATH}
    proof={**d.package,'answers':[resolve(store,d.job['host'],field)]}
    official=form()
    official['sections'][0]['fieldEntries'].append({'id':field['ashby_entry_id'],
        'field':{'path':SCHOOL_PATH,'title':field['label'],'type':'EducationHistory',
                 'schoolName':'required','degree':'excluded','major':'excluded',
                 'startDate':'excluded','endDate':'excluded','isRepeatable':True,'minRepeat':1,'maxRepeat':3},
        'fieldValue':{'value':restored} if restored is not None else None})
    d._remember_form(official)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSearchSchoolByCanonicalName',
        post_data=json.dumps({'operationName':'ApiSearchSchoolByCanonicalName','query':SCHOOL_QUERY,
                             'variables':{'name':'Berkeley','organizationHostedJobsPageName':'acme'}}))
    result={'data':{'result':[{'id':'fixture-school','name':SCHOOL,'__typename':'CanonicalSchool'}]}}
    if observe:d.observe_schools(read,SimpleNamespace(status=200,json=lambda:result))
    return d,proof,read,result,official


@pytest.mark.parametrize('restored',[False,True])
def test_school_only_history_requires_exact_canonical_choice_and_acknowledgement(store,job,package,restored):
    value=[{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL}]
    d,proof,_,_,official=school_draft(store,job,package,observe=not restored,restored=value if restored else None)
    d.enable(None,proof['answers'],proof)
    assert d.complete is restored
    if not restored:
        r,data=request(value,path=SCHOOL_PATH)
        assert d.allow(r,data,'school') and not d.complete
        official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':value}
        d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'school')
        assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['missing','wrong_host','wrong_method','wrong_tenant','wrong_query','wrong_operation',
    'errors','ambiguous','malformed','partial_malformed','extra_variable','wrong_url_operation'])
def test_unverified_school_lookup_never_grants_a_history_write(store,job,package,mode):
    d,proof,read,result,_=school_draft(store,job,package,observe=False)
    data=json.loads(read.post_data)
    if mode=='wrong_host':read.url=read.url.replace('jobs.ashbyhq.com','attacker.invalid')
    if mode=='wrong_method':read.method='PUT'
    if mode=='wrong_tenant':data['variables']['organizationHostedJobsPageName']='other'
    if mode=='wrong_query':data['query']+=' mutation Other { submitApplication { id } }'
    if mode=='wrong_operation':data['operationName']='Other'
    if mode=='extra_variable':data['variables']['extra']='unreviewed'
    if mode=='wrong_url_operation':read.url=read.url.replace('ApiSearchSchoolByCanonicalName','Other')
    if mode=='errors':result['errors']=[{'message':'Synthetic error'}]
    if mode=='ambiguous':result['data']['result'].append({'id':'different-school','name':SCHOOL})
    if mode=='malformed':result['data']['result']={}
    if mode=='partial_malformed':result['data']['result'].append({'id':None,'name':SCHOOL})
    read.post_data=json.dumps(data)
    if mode!='missing':d.observe_schools(read,SimpleNamespace(status=200,json=lambda:result))
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    r,data=request([{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL}],path=SCHOOL_PATH)
    assert not d.allow(r,data,'school')


@pytest.mark.parametrize('value',[
    SCHOOL,[{'canonicalSchoolId':'unverified','schoolName':SCHOOL}],
    [{'canonicalSchoolId':'fixture-school','schoolName':'Other school'}],
    [{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL,'degree':'Unapproved'}],
    [{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL}]*2,
])
def test_school_grant_cannot_write_additional_or_changed_history(store,job,package,value):
    d,proof,_,_,_=school_draft(store,job,package);d.enable(None,proof['answers'],proof)
    r,data=request(value,path=SCHOOL_PATH)
    assert not d.allow(r,data,'school')


def test_school_lookup_retains_ambiguity_across_responses_and_repeated_paths_stay_held(store,job,package):
    d,proof,read,result,_=school_draft(store,job,package)
    result['data']['result'][0]['id']='different-school'
    d.observe_schools(read,SimpleNamespace(status=200,json=lambda:result))
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    d.schools={SCHOOL:{'fixture-school'}}
    proof={**proof,'answers':proof['answers']*2}
    with pytest.raises(Blocked,match='Duplicate Ashby field path'):d.enable(None,proof['answers'],proof)


@pytest.mark.parametrize('change',['degree','major','startDate','endDate','minRepeat','provenance','dom_prefill'])
def test_complex_history_or_unsourced_school_stays_held(store,job,package,change):
    d,proof,_,_,_=school_draft(store,job,package,observe=change!='dom_prefill')
    metadata=d.forms[RENDER]['entries'][SCHOOL_PATH]
    if change=='provenance':proof['answers'][0]['provenance']={'fact_key':'location','revision':1}
    elif change=='dom_prefill':proof['answers'][0]['field']['value']=SCHOOL
    elif change=='minRepeat':metadata[change]=2
    else:metadata[change]='required'
    expected='unsupported_or_stale_answer' if change=='provenance' else 'ashby_draft_unsupported'
    with pytest.raises(Blocked,match=expected):d.enable(None,proof['answers'],proof)


@pytest.mark.parametrize('mode',['saved','unverified_id','wrong_ack'])
def test_browser_routes_canonical_school_lookup_and_exact_history_save(store,job,package,monkeypatch,mode):
    import time
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,proof,read,result,official=school_draft(store,job,package,observe=False)
    d.package=None;d.grants={}
    value=[{'canonicalSchoolId':'unverified' if mode=='unverified_id' else 'fixture-school','schoolName':SCHOOL}]
    data=request(value,path=SCHOOL_PATH)[1];writes=[]
    html='<h1>Synthetic school widget</h1><script>window.lookup='+json.dumps(json.loads(read.post_data))+';window.save='+json.dumps(data)+''';
window.post = data => fetch('/api/non-user-graphql', {method:'POST',body:JSON.stringify(data)}).then(r=>r.json()).catch(()=>null);
</script>'''
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=d
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiSearchSchoolByCanonicalName':body=result
                else:
                    assert data['operationName']=='ApiSetFormValue'
                    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    writes.append(data)
                    ack=[{'canonicalSchoolId':'different','schoolName':SCHOOL}] if mode=='wrong_ack' else value
                    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':ack}
                    body={'data':{'setFormValue':official}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(body))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        b.page.goto(d.job['url'])
        b.page.evaluate('() => post(lookup)')
        deadline=time.monotonic()+10
        while not d.schools and time.monotonic()<deadline:b.page.wait_for_timeout(50)
        assert d.schools=={SCHOOL:{'fixture-school'}}
        d.enable(b,proof['answers'],proof)
        b.page.evaluate('() => post(save)')
        deadline=time.monotonic()+10
        while mode!='unverified_id' and not (d.complete or d.failed) and time.monotonic()<deadline:b.page.wait_for_timeout(50)
        assert len(writes)==(mode!='unverified_id')
        assert d.complete is (mode=='saved')
        assert d.failed is (mode=='wrong_ack')


def location_draft(store,job,package,*,observe=True,restored=None):
    from hireme.answers import resolve
    d,ashby=draft(store,job,package)
    field={'index':0,'indices':[0],'label':'Location','type':'combobox','required':True,
           'options':[LOCATION],'maxlength':-1,'value':'','ashby_path':'_systemfield_location',
           'ashby_entry_id':RENDER+'__systemfield_location'}
    answer=resolve(store,ashby['host'],field)
    proof={**d.package,'answers':[answer]}
    saved=form()
    saved['sections'][0]['fieldEntries'].append({'id':field['ashby_entry_id'],
        'field':{'path':'_systemfield_location','title':'Location','type':'Location','locationTypes':['City']},
        'fieldValue':{'value':restored} if restored else None})
    d._remember_form(saved)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiAutocompleteGeoLocation',
        post_data=json.dumps({'operationName':'ApiAutocompleteGeoLocation','query':LOCATION_QUERY,
                             'variables':{'text':'Berkeley','locationTypes':['City']}}))
    result={'data':{'result':{'suggestions':[{'name':LOCATION,'geoLocationPath':[
        {'providerLocationId':'fixture-country'},{'providerLocationId':'fixture-city'}]}]}}}
    if observe:d.observe_locations(read,SimpleNamespace(status=200,json=lambda:result))
    return d,proof,read,result


def test_location_object_requires_official_choice_and_exact_save_ack(store,job,package):
    d,proof,_,_=location_draft(store,job,package)
    d.enable(None,proof['answers'],proof)
    value={'text':LOCATION,'providerLocationId':'fixture-city'}
    r,data=request(value,path='_systemfield_location')
    assert d.allow(r,data,'location') and not d.complete
    saved=form();saved['sections'][0]['fieldEntries'].append({'id':RENDER+'__systemfield_location',
        'field':{'path':'_systemfield_location','title':'Location','type':'Location','locationTypes':['City']},
        'fieldValue':{'value':value}})
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':saved}}),'location')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['missing','wrong_host','wrong_method','wrong_query','wrong_operation','errors','wrong_types','ambiguous'])
def test_unverified_or_ambiguous_location_lookup_grants_no_write(store,job,package,mode):
    d,proof,read,result=location_draft(store,job,package,observe=False)
    if mode=='wrong_host':read.url=read.url.replace('jobs.ashbyhq.com','attacker.invalid')
    if mode=='wrong_method':read.method='PUT'
    data=json.loads(read.post_data)
    if mode=='wrong_query':data['query']+=' mutation Other { submitApplication { id } }'
    if mode=='wrong_operation':data['operationName']='Other'
    if mode=='wrong_types':data['variables']['locationTypes']=['Country']
    if mode=='errors':result['errors']=[{'message':'Synthetic failure'}]
    if mode=='ambiguous':result['data']['result']['suggestions'].append({'name':LOCATION,'geoLocationPath':[{'providerLocationId':'different-city'}]})
    read.post_data=json.dumps(data)
    if mode!='missing':d.observe_locations(read,SimpleNamespace(status=200,json=lambda:result))
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    r,data=request({'text':LOCATION,'providerLocationId':'fixture-city'},path='_systemfield_location')
    assert not d.allow(r,data,'location')


@pytest.mark.parametrize('value',[
    LOCATION,{'text':LOCATION,'providerLocationId':'unverified'},
    {'text':'Different city','providerLocationId':'fixture-city'},
    {'text':LOCATION,'providerLocationId':'fixture-city','extra':'unreviewed'},
])
def test_location_grant_cannot_save_arbitrary_client_object(store,job,package,value):
    d,proof,_,_=location_draft(store,job,package);d.enable(None,proof['answers'],proof)
    r,data=request(value,path='_systemfield_location')
    assert not d.allow(r,data,'location')


def test_exact_official_location_prefill_needs_no_new_autocomplete(store,job,package):
    d,proof,_,_=location_draft(store,job,package,observe=False,
        restored={'text':LOCATION,'providerLocationId':'fixture-city'})
    d.enable(None,proof['answers'],proof)
    assert d.complete


@pytest.mark.parametrize('target,verified',[
    (f'https://jobs.ashbyhq.com/renamed/{REQ}',True),
    (f'https://jobs.ashbyhq.com/renamed/{REQ}',False),
    (f'https://jobs.ashbyhq.com/renamed/{DEFINITION}',True),
    (f'https://attacker.invalid/renamed/{REQ}',True),
    (f'http://jobs.ashbyhq.com/renamed/{REQ}',True),
    (f'https://jobs.ashbyhq.com/renamed/{REQ}?submit=true',True),
])
def test_tenant_redirect_requires_same_posting_response_and_safe_page(store,job,package,target,verified):
    d,ashby=draft(store,job,package)
    proof=d.package
    d.package=None;d.grants={}
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                            'variables':{'organizationHostedJobsPageName':'renamed','jobPostingId':REQ}}))
    if verified:d.observe_posting(read,SimpleNamespace(status=200,json=lambda:{'data':{'jobPosting':{'id':REQ,'applicationForm':form()}}}))
    original=dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(ashby['id'],)).fetchone())
    if verified and target==f'https://jobs.ashbyhq.com/renamed/{REQ}':
        d.bind_page(target)
        assert d.identity==('renamed',REQ) and d.navigation_url==target
        d.enable(None,proof['answers'],proof)
        r,data=request();assert not d.allow(r,data,'old-tenant')
        r,data=request(organizationHostedJobsPageName='renamed');assert d.allow(r,data,'new-tenant')
    else:
        with pytest.raises(Blocked,match='unexpected_redirect'):d.bind_page(target)
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(ashby['id'],)).fetchone())==original


@pytest.mark.parametrize('mode',['saved','unverified_id','wrong_ack'])
def test_browser_routes_location_lookup_and_only_the_selected_object(store,job,package,monkeypatch,mode):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,proof,read,result=location_draft(store,job,package,observe=False)
    d.package=None;d.grants={}
    value={'text':LOCATION,'providerLocationId':'fixture-city' if mode!='unverified_id' else 'unverified'}
    data=request(value,path='_systemfield_location')[1];writes=[]
    html='<h1>Synthetic location widget</h1><script>window.lookup='+json.dumps(json.loads(read.post_data))+';window.save='+json.dumps(data)+''';
window.post = data => fetch('/api/non-user-graphql', {method:'POST',body:JSON.stringify(data)}).then(r=>r.json()).catch(()=>null);
</script>'''
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=d
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiAutocompleteGeoLocation':body=result
                else:
                    assert data['operationName']=='ApiSetFormValue'
                    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    writes.append(data)
                    ack={**value,'providerLocationId':'different'} if mode=='wrong_ack' else value
                    saved=form();saved['sections'][0]['fieldEntries'].append({'id':RENDER+'__systemfield_location',
                        'field':{'path':'_systemfield_location','title':'Location'},'fieldValue':{'value':ack}})
                    body={'data':{'setFormValue':saved}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(body))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        b.page.goto(d.job['url'])
        b.page.evaluate('() => post(lookup)')
        b.page.wait_for_timeout(100)
        assert d.locations
        d.enable(b,proof['answers'],proof)
        b.page.evaluate('() => post(save)')
        b.page.wait_for_timeout(100)
        assert len(writes)==(mode!='unverified_id')
        assert d.complete is (mode=='saved')
        assert d.failed is (mode=='wrong_ack')
