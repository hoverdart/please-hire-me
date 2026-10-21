"""Real NVIDIA driver/worker integration on a completely closed employer network.

Schemas below are explicitly synthetic instances of the reviewed native schema
families. They contain no captured applicant values. Native protocol guards,
fresh owner reads, exact upload bytes, durable history and Gmail MIME stay real.
Fixture receipts cannot establish operational capability on a real machine.
"""
from __future__ import annotations

import copy
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

import pytest

from hireme.browser import Browser
from hireme.gmail import GmailClient
from hireme.store import Store
from hireme.util import Blocked,digest
from hireme.workday_flow import NvidiaNativeProfile
from hireme.workday_native import PAGES,QUESTION_TYPES,SESSION_HEADER,native_url
from hireme.workday_profiles import NVIDIA,NVIDIA_SITE,PROFILE_READ,REVIEWED_TRANSPORTS,operational_evidence
from tests.test_pipeline_integration import FakeMailbox


ORIGIN='https://'+NVIDIA
REQ='JR9000001'
SLUG='Synthetic-Software-Engineering-Intern_'+REQ
URL=ORIGIN+'/en-US/'+NVIDIA_SITE+'/job/US-CA/'+SLUG
TITLE='Software Engineer Intern Summer 2027'
DESCRIPTION='Build Python and TypeScript software in San Francisco, United States.'
REMOTE='synthetic-native-application'
SESSION='synthetic-native-session'
COUNTRY='bc33aa3152ec42d4995f4791a106ed09'
FLOW=[{'id':key,'descriptor':label,'sections':[]} for key,label in PAGES.items()
      if label in {'My Information','My Experience','Application Questions','Application Questions 2','Voluntary Disclosures','Review'}]
DATE='2026-10-21T12:00:00Z'


def schema(name,properties,required=()):
    return {'allOf':[{'$ref':'#/definitions/'+name}],
            'definitions':{name:{'properties':properties,'required':list(required)}}}


def question(identifier,label,kind,choices):
    type_id=next(key for key,value in QUESTION_TYPES.items() if value==kind)
    return {'id':identifier,'body':'<p>'+label+'</p>','required':True,'type':{'id':type_id},
            'possibleAnswers':[{'id':identifier+'-'+str(index),'answerText':value,'order':str(index)}
                               for index,value in enumerate(choices)]}


class ClosedNvidiaEmployer:
    def __init__(self,store,monkeypatch,*,outcome='confirmed'):
        self.store,self.outcome=store,outcome
        self.requests=[];self.native_writes=[];self.unexpected=[];self.records={};self.attachments=[];self.final_sent=False
        self.questionnaire_answers={};self.terms_locked=False;self.draft_exists=False;self.active_session=SESSION
        self.receipt_reads=[];self.browser_starts=0;self.mailbox=FakeMailbox()
        self.document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
        self.document_bytes=(store.root/'documents'/self.document['filename']).read_bytes()
        self.job={'id':digest(URL),'url':URL,'host':NVIDIA,'company':'Synthetic NVIDIA fixture',
                  'title':TITLE,'description':DESCRIPTION,'location':'San Francisco, United States','source':'closed-native-pipeline'}
        store.upsert_job(self.job)
        store.put_facts({'country':'United States of America','school':'University of California, Berkeley',
                         'degree':'B.S.','major':'Computer Science'})
        store.put_history_entry('education',{'institution':'University of California, Berkeley','degree':'B.S.',
            'field_of_study':'Computer Science','start':'2024-08','expected_end':'2028-05','current':True},
            source='Explicit synthetic applicant confirmation for this disposable test',confirmed=True)
        store.put_history_entry('employment',{'organization':'Synthetic internship employer','title':'Engineering Intern',
            'start':'2025-06','end':'2025-08','current':False,'description':'Implemented Python services and reviewed their production behavior.'},
            source='Explicit synthetic employment record for this disposable test',confirmed=True)
        store.update_settings({'gmail_reports':True,'remote_drafts':True,'signed_in_portals':[NVIDIA]})
        if os.environ.get('HIREME_TEST_HUMAN_VISIBLE')=='1':store.update_settings({'headless':False})
        self.info={'id':'synthetic-posting-anchor','title':TITLE,'jobDescription':DESCRIPTION,'jobReqId':REQ,
            'jobPostingId':SLUG,'jobPostingSiteId':NVIDIA_SITE,'externalUrl':URL,'canApply':True,'posted':True,
            'country':{'id':COUNTRY,'descriptor':'United States of America'},
            'jobRequisitionLocation':{'descriptor':'US, CA, San Francisco','country':{'id':COUNTRY,'descriptor':'United States of America','alpha2Code':'US'}},
            'questionnaireId':'synthetic-primary','secondaryQuestionnaireId':'synthetic-secondary'}
        self.questionnaires={
            'synthetic-primary':{'id':'synthetic-primary','questions':[
                question('synthetic-authorized','Are you legally authorized to work in the United States?','single',['Yes','No']),
                question('synthetic-sponsorship','Will you require visa sponsorship now or in the future?','single',['Yes','No'])]},
            'synthetic-secondary':{'id':'synthetic-secondary','questions':[
                question('synthetic-first-name','First name','text',[])]}}
        actual=json.loads((Path(__file__).parent/'fixtures/workday-nvidia-us-definitions.json').read_text())
        self.definitions={key:value['data'] for key,value in actual['definitions'].items()}
        self.definitions.update({
            'phonenumber':schema('phonenumber',{'phoneNumber':{'type':'string'}},['phoneNumber']),
            'source':schema('source',{}),'previousworker':schema('previousworker',{}),
            'educations':schema('educations',{'schoolName':{'type':'string'},
                'firstYearAttended':{'type':'string','precision':'YYYY'},'lastYearAttended':{'type':'string','precision':'YYYY'}},
                ['schoolName','firstYearAttended','lastYearAttended']),
            'workexperiences':schema('workexperiences',{'companyName':{'type':'string'},'jobTitle':{'type':'string'},
                'startDate':{'type':'string','precision':'YYYY-MM'},'endDate':{'type':'string','precision':'YYYY-MM'},
                'currentlyWorkHere':{'type':'boolean'},'roleDescription':{'type':'string'}}),
            'termsandconditions':schema('termsandconditions',{}),'uspersonalinfo':schema('uspersonalinfo',{})})
        monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
        expected=ORIGIN+'/wday/cxs/nvidia/'+NVIDIA_SITE+'/job/US-CA/'+SLUG
        def posting_read(network,url,data=None):
            assert url==expected and data is None,'Unintercepted public posting read: '+url
            if network.checkpoint:network.checkpoint()
            return {'jobPostingInfo':copy.deepcopy(self.info)}
        monkeypatch.setattr('hireme.net.Network.json',posting_read)
        import hireme.reports
        flush=hireme.reports.flush_reports
        monkeypatch.setattr(hireme.reports,'flush_reports',lambda selected:flush(selected,lambda s:GmailClient(s,self.mailbox)))
        employer=self
        class NativeBrowser(Browser):
            def __enter__(browser):
                super().__enter__();employer.browser_starts+=1;employer.last_browser=browser
                employer.active_session='' if employer.draft_exists else SESSION
                browser.workday_commission_job=employer.job['id']
                browser.context.add_cookies([{'name':name,'value':'synthetic-'+name,'domain':NVIDIA,'path':'/',
                    'httpOnly':True,'secure':True,'sameSite':'Lax','expires':-1}
                    for name in ('CALYPSO_CSRF_TOKEN','CALYPSO_SESSION','PLAY_SESSION')])
                browser.context.route('**/*',lambda route:employer.route(browser,route))
                return browser
            def __exit__(browser,*args):
                employer.last_text=browser.page.locator('body').inner_text(timeout=1000)
                return super().__exit__(*args)
        self.browser_factory=NativeBrowser

    def install_driver(self,monkeypatch):
        from hireme import workday_nvidia
        driver=getattr(workday_nvidia,'NvidiaNativeDriver',None)
        assert driver is not None,'The real reviewed NVIDIA driver must be installed before its positive release gate'
        monkeypatch.setattr(driver,'proof_scope','fixture')
        monkeypatch.setitem(REVIEWED_TRANSPORTS,NVIDIA,NvidiaNativeProfile(driver))
        return driver

    def route(self,browser,route):
        employer=self
        class Transport:
            request=route.request
            def abort(self):route.abort()
            def continue_(self,**changes):employer.respond(browser,route,changes)
        browser._route(Transport())

    def respond(self,browser,route,changes):
        request=route.request;parsed=urlsplit(request.url);path=parsed.path;query=parse_qs(parsed.query)
        body=changes.get('post_data',request.post_data_buffer or b'')
        self.requests.append((request.method,request.url))
        if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('form-definition')!='true':
            self.native_writes.append((request.method,request.url))
        def reply(data,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(data))
        if parsed.hostname!=NVIDIA:
            self.unexpected.append((request.method,request.url));route.abort();return
        if request.method=='GET' and request.url==URL:
            misleading=("const originalFetch=window.fetch.bind(window);window.fetch=async(...args)=>{const response=await originalFetch(...args);if(String(args[0]).endsWith('/finalize'))document.body.innerHTML='Your application has been successfully submitted.';return response;};"
                        if self.outcome=='misleading_receipt' else '')
            return route.fulfill(content_type='text/html',body=(f'<html lang="en-US"><h1>{TITLE}</h1><p>{DESCRIPTION}</p>'
                '<button type="button">Apply</button><script>window.workday={tenant:"nvidia",siteId:"NVIDIAExternalCareerSite",token:"synthetic-native-csrf"};'+misleading+'</script></html>'))
        if request.method=='GET' and request.url==PROFILE_READ:return reply({'emailAddress':'test@candidate.invalid'})
        if request.method=='GET' and path=='/wday/cxs/nvidia/'+NVIDIA_SITE+'/job/US-CA/'+SLUG:
            info=copy.deepcopy(self.info)
            if self.final_sent:
                completed=self.outcome!='misleading_receipt'
                info['jobApplication']={'id':REMOTE,'draft':not completed,'dateCompleted':DATE if completed else None}
                self.receipt_reads.append(('detail',True))
            return reply({'jobPostingInfo':info})
        if request.method=='GET' and path.endswith('/job/'+SLUG+'/info'):return reply(self.info)
        if request.method=='GET' and path.endswith('/applyflowpages'):return reply({'total':len(FLOW),'data':FLOW})
        if request.method=='GET' and path.endswith('/canapplycontinue'):return reply({'canApply':True,'canContinue':True})
        if request.method=='GET' and path.endswith('/applications') and query.get('draftsOnly')==['true']:
            assert query.get('type')==['active'] and query.get('jobPostingAnchorId')==['Job_Posting_Anchor_ID='+SLUG]
            rows=[{'applicationId':REMOTE,'draft':True,'posting':{'isPosted':True}}] if self.draft_exists and not self.final_sent else []
            return reply({'total':len(rows),'data':rows})
        if request.method=='GET' and path.endswith('/applications') and query.get('type') in (['active'],['inactive']):return reply({'total':0,'data':[]})
        if request.method=='GET' and request.url==native_url('common','countries/'+COUNTRY+'/regions'):
            return reply({'total':1,'data':[{'id':'synthetic-us-ca','descriptor':'California'}]})
        if request.method=='GET' and path.endswith('/'+NVIDIA_SITE+'/info'):return reply({'id':NVIDIA_SITE,'applyConfirmationMessage':'Your application has been successfully submitted.'})
        if request.method=='GET' and '/questionnaire/' in path:
            value=self.questionnaires.get(path.rsplit('/',1)[1])
            if value is not None:return reply(value)
        if request.method=='GET' and path.endswith('/namedefinition'):return reply(self.definitions['name'])
        if request.method=='GET' and path.endswith('/addressdefinition'):return reply(self.definitions['address'])
        if request.headers.get('form-definition')=='true':
            assert request.headers.get(SESSION_HEADER.lower())==self.active_session
            resource=path.rsplit('/',1)[1]
            if resource in self.definitions:return reply(self.definitions[resource])
            if resource=='definition' and '/questionnaire/' in path:
                name=path.rsplit('/',2)[1];questions=self.questionnaires[name]['questions']
                return reply(schema('questionnaire',{item['id']:{'type':'string'} for item in questions},[item['id'] for item in questions]))
        if request.method=='POST' and path.endswith('/jobpostings/'+SLUG+'/jobapplications'):
            assert body==b'{}'
            assert not self.draft_exists
            self.draft_exists=True
            return reply({'id':REMOTE,'sessionId':SESSION})
        if request.method=='POST' and path.endswith(('/scanfile','/attachments')):
            message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\n\r\n'+body)
            parts=list(message.iter_parts());assert len(parts)==1
            file=parts[0];assert file.get_filename()==self.document['filename'] and file.get_payload(decode=True)==self.document_bytes
            if path.endswith('/scanfile'):return reply({'diagnosis':'clean','containsMacros':False,'fileSize':len(self.document_bytes),'fileType':'application/pdf'})
            return reply({'id':'synthetic-attachment','fileName':self.document['filename'],'fileLength':len(self.document_bytes),'contentType':{'id':'Content_Type_ID=application/pdf','descriptor':'application/pdf'}})
        if request.method=='PUT' and path.endswith('/package/'+REMOTE+'/validate'):
            assert body==b'';return reply({})
        if request.method=='POST' and path.endswith('/jobapplication/'+REMOTE+'/initialize'):
            assert body==b'';return reply({})
        if request.method=='POST' and path.endswith('/jobapplication/'+REMOTE+'/termslockmoment'):
            assert body==b'{}';self.terms_locked=True;return reply({})
        if request.method=='POST' and path.endswith('/jobapplication/'+REMOTE+'/finalize'):
            assert body==b'{}' and request.headers.get(SESSION_HEADER.lower())==self.active_session
            self.final_sent=True
            if self.outcome=='timeout':route.abort();return
            return reply({'id':'unrelated-native-id' if self.outcome=='bad_ack' else REMOTE})
        if request.method=='GET' and path.endswith('/application/'+REMOTE):
            self.receipt_reads.append(('detail',self.final_sent))
            completed=self.final_sent and self.outcome!='misleading_receipt'
            return reply({'jobApplication':{'id':REMOTE,'draft':not completed,'dateCompleted':DATE if completed else None}})
        if request.method=='GET' and path.endswith('/application/'+REMOTE+'/config'):
            self.receipt_reads.append(('config',self.final_sent))
            completed=self.final_sent and self.outcome!='misleading_receipt'
            return reply({'application':{'applicationId':REMOTE,'jobPostingAnchorId':SLUG,'jobRequisitionId':REQ,
                'draft':not completed,'dateApplied':DATE if completed else None},'xipLabels':{},'previousWorker':{}})
        if request.method=='GET' and path.endswith('/jobapplication/'+REMOTE+'/names'):
            name=self.records.get('name')
            return reply({'legalName':name,'preferredName':None})
        if request.method=='GET' and path.endswith('/jobapplication/'+REMOTE+'/resumeattachments'):
            return reply({'attachments':self.attachments})
        if request.method=='GET' and path.endswith('/jobapplication/'+REMOTE+'/questionnaireanswers'):
            return reply(self.questionnaire_answers.get(query['type'][0],[]))
        if request.method=='GET' and '/package/'+REMOTE+'/' in path:
            resource=path.rsplit('/',1)[1];rows=list(self.records.get(resource,{}).values())
            return reply({'total':len(rows),'data':rows})
        if request.method=='GET' and path.endswith('/package/'+REMOTE):
            return reply({'id':REMOTE,'draft':True,'termsAndConditionsUpdated':False,
                          'termsAndConditionsLockedMoment':DATE if self.terms_locked else None})
        if request.method in {'POST','PUT'} and '/jobapplication/'+REMOTE+'/' in path:
            resource=path.rsplit('/',1)[1]
            data=json.loads(body or b'{}')
            if resource=='name':self.records['name']={'id':'native-name',**copy.deepcopy(data['legalName'])}
            elif resource=='resumeattachments':self.attachments=[{'id':'native-resume-link',**copy.deepcopy(data)}]
            elif resource=='questionnaireresponse':
                self.questionnaire_answers[query['type'][0]]=copy.deepcopy(data['questionnaireAnswers'])
                return reply({'id':'native-questionnaire-'+query['type'][0],**data})
            else:
                collection={'address':'addresses','phonenumber':'phonenumbers'}.get(resource,resource)
                record={'id':'record-'+resource,**copy.deepcopy(data)}
                if resource=='workexperiences':
                    for wire,native in [('companyName','jobHistoryCompanyName'),('roleDescription','responsibilitiesAndAchievements')]:
                        if wire in record:record[native]=record.pop(wire)
                    for key in ('start','end'):
                        date=record.pop(key+'Date',None)
                        if date:
                            year,month=date.split('-');record[key+'Year']=year;record[key+'Month']=int(month)
                self.records.setdefault(collection,{})['record-'+resource]=record
            if self.outcome=='unknown_save_ack' and resource=='name':return reply({'unexpected':True})
            # The source client accepts a successful aggregate object; a fresh
            # native collection GET, not an invented echoed save, proves values.
            return reply({})
        self.unexpected.append((request.method,request.url));route.abort()

    def run(self,store=None):
        from hireme.worker import cycle
        return cycle(store or self.store,Path('.'),discover=False,browser_factory=self.browser_factory,
                     job_ids=[self.job['id']],limit=1,max_attempts=1)


def test_nvidia_native_worker_success_report_and_duplicate_protection(store,monkeypatch):
    employer=ClosedNvidiaEmployer(store,monkeypatch)
    employer.install_driver(monkeypatch)
    result=employer.run()
    assert result['confirmed']==result['attempts']==1,{'reasons':result.get('reasons'),'job':store.db.execute('SELECT reason FROM jobs WHERE id=?',(employer.job['id'],)).fetchone()[0],'unexpected':employer.unexpected}
    application=store.db.execute('SELECT * FROM applications').fetchone()
    assert application['state']=='confirmed'
    package=json.loads(application['package'])
    assert {step['native_page'] for step in package['steps']}=={page['id'] for page in FLOW if page['descriptor']!='Review'}
    assert employer.final_sent and {kind for kind,after in employer.receipt_reads if after}=={'detail','config'}
    receipt=store.db.execute("SELECT detail FROM events WHERE kind='workday_receipt'").fetchone()
    assert receipt and json.loads(receipt[0])['proof_scope']=='fixture'
    assert not operational_evidence(store,NVIDIA,1)
    assert not store.db.execute('SELECT 1 FROM adapter_verifications').fetchone()
    message,=employer.mailbox.messages_sent
    assert 'Confirmed submissions: 1' in message.get_body(preferencelist=('plain',)).get_content()
    assert store.db.execute('SELECT state,attempts FROM report_outbox').fetchone()[:]==('sent',1)
    before=copy.deepcopy(employer.requests)
    assert employer.run()['attempts']==0
    assert employer.requests==before and len(employer.mailbox.messages_sent)==1 and not employer.unexpected


@pytest.mark.parametrize('outcome',['bad_ack','misleading_receipt','timeout'])
def test_nvidia_native_worker_preserves_unknown_without_retry_after_unverified_final_outcome(store,monkeypatch,outcome):
    employer=ClosedNvidiaEmployer(store,monkeypatch,outcome=outcome)
    employer.install_driver(monkeypatch)
    result=employer.run()
    assert result['confirmed']==0 and result['attempts']==1 and result['outcomes']=={'unknown':1},result
    application=dict(store.db.execute('SELECT * FROM applications').fetchone())
    assert application['state']=='unknown' and employer.final_sent
    if outcome=='misleading_receipt':assert 'successfully submitted' in employer.last_text
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0]==1
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='workday_receipt'").fetchone()
    message,=employer.mailbox.messages_sent
    body=message.get_body(preferencelist=('plain',)).get_content()
    assert 'Confirmed submissions: 0' in body and 'Submission outcome is uncertain.' in body
    before=copy.deepcopy(employer.requests)
    reopened=Store(store.root)
    try:assert employer.run(reopened)['attempts']==0
    finally:reopened.close()
    assert employer.requests==before and len(employer.mailbox.messages_sent)==1 and not employer.unexpected


@pytest.mark.parametrize('change',['pause','facts'])
def test_nvidia_native_validation_cannot_authorize_later_writes_after_applicant_change(store,monkeypatch,change):
    employer=ClosedNvidiaEmployer(store,monkeypatch)
    employer.install_driver(monkeypatch)
    from hireme.workday_nvidia import NativeBrowserTransport
    request=NativeBrowserTransport.request
    changed=[]
    def validate_then_change(transport,operation):
        response=request(transport,operation)
        driver=transport.controller._native_driver
        if (not changed and operation.url.endswith('/package/'+REMOTE+'/validate')
                and driver.current_page['section']=='My Experience'):
            changed.append(len(employer.requests))
            if change=='pause':store.update_settings({'live_enabled':False})
            else:store.put_facts({'phone':'5557654321'})
        return response
    monkeypatch.setattr(NativeBrowserTransport,'request',validate_then_change)
    if change=='pause':
        with pytest.raises(Blocked,match='paused'):employer.run()
    else:
        result=employer.run()
        assert result['confirmed']==0
    assert len(changed)==1 and not employer.final_sent
    assert not [item for item in employer.requests[changed[0]:] if item[0] not in {'GET','HEAD','OPTIONS'}]
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='workday_receipt'").fetchone()
    assert not employer.unexpected


def test_nvidia_native_process_loss_after_acceptance_recovers_unknown_and_reports_once(store,monkeypatch):
    employer=ClosedNvidiaEmployer(store,monkeypatch)
    driver=employer.install_driver(monkeypatch)
    completed=driver.completed_application
    def accepted_then_lost(native,*args):
        assert completed(native,*args) is True
        assert employer.final_sent
        raise SystemExit('synthetic NVIDIA process loss after native acceptance')
    monkeypatch.setattr(driver,'completed_application',accepted_then_lost)
    with pytest.raises(SystemExit,match='synthetic NVIDIA process loss'):employer.run()
    application=store.db.execute('SELECT id,state FROM applications').fetchone()
    original=store.db.execute('SELECT id,status FROM runs').fetchone()
    assert application['state']=='submitting' and original['status']=='running'
    assert not employer.mailbox.messages_sent
    before=copy.deepcopy(employer.requests)
    reopened=Store(store.root)
    try:
        assert employer.run(reopened)['attempts']==0
        assert reopened.application_record(application['id'])['state']=='unknown'
        assert reopened.db.execute('SELECT status FROM runs WHERE id=?',(original['id'],)).fetchone()[0]=='interrupted'
        report=dict(reopened.db.execute('SELECT * FROM report_outbox WHERE id=?',(original['id'],)).fetchone())
        assert report['state']=='sent' and report['attempts']==1
        assert employer.run(reopened)['attempts']==0
        assert dict(reopened.db.execute('SELECT * FROM report_outbox WHERE id=?',(original['id'],)).fetchone())==report
    finally:reopened.close()
    message,=employer.mailbox.messages_sent
    body=message.get_body(preferencelist=('plain',)).get_content()
    assert 'Confirmed submissions: 0' in body and 'Submission outcome is uncertain.' in body
    assert employer.requests==before and len(employer.mailbox.messages_sent)==1 and not employer.unexpected


def test_nvidia_unknown_save_ack_cannot_be_promoted_by_already_correct_backend_values(store,monkeypatch):
    employer=ClosedNvidiaEmployer(store,monkeypatch,outcome='unknown_save_ack')
    employer.install_driver(monkeypatch)
    result=employer.run()
    assert result['confirmed']==0 and result['attempts']==1,result
    assert employer.records['name']['firstName']=='Test' and employer.records['name']['lastName']=='Person'
    assert store.db.execute('SELECT state FROM workday_drafts').fetchone()[0]=='unknown'
    assert not employer.final_sent
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    writes=[item for item in employer.requests if item[0] not in {'GET','HEAD','OPTIONS'}]
    employer.run()
    assert [item for item in employer.requests if item[0] not in {'GET','HEAD','OPTIONS'}]==writes
    assert not employer.unexpected


def test_nvidia_partial_native_draft_resumes_without_recreating_or_repeating_saved_information(store,monkeypatch):
    employer=ClosedNvidiaEmployer(store,monkeypatch)
    driver=employer.install_driver(monkeypatch)
    enter=driver.enter
    interrupted=[]
    def stop_before_experience(native,page):
        if page['section']=='My Experience' and not interrupted:
            interrupted.append(True)
            store.update_settings({'live_enabled':False})
            store.checkpoint()
        return enter(native,page)
    monkeypatch.setattr(driver,'enter',stop_before_experience)
    with pytest.raises(Blocked,match='paused'):employer.run()
    first=json.loads(store.db.execute('SELECT detail FROM runs').fetchone()[0])
    assert first['attempts']==1 and first['confirmed']==0 and interrupted
    assert store.db.execute('SELECT state FROM workday_drafts').fetchone()[0]=='acknowledged'
    assert employer.records['name']['firstName']=='Test'
    assert not employer.attachments and not employer.final_sent
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    before=len(employer.native_writes)
    store.update_settings({'live_enabled':True})
    monkeypatch.setattr(driver,'enter',enter)
    resumed=employer.run()
    assert resumed['attempts']==resumed['confirmed']==1,{
        'run':resumed,'job':store.db.execute('SELECT reason FROM jobs WHERE id=?',(employer.job['id'],)).fetchone()[0],
        'unexpected':employer.unexpected}
    assert employer.active_session=='' and employer.final_sent
    writes=[(method,urlsplit(url).path) for method,url in employer.native_writes]
    assert sum(path.endswith('/jobpostings/'+SLUG+'/jobapplications') for _,path in writes)==1
    assert sum(path.endswith('/jobapplication/'+REMOTE+'/name') for _,path in writes)==1
    assert not [url for method,url in employer.native_writes[before:]
                if urlsplit(url).path.endswith(('/name','/address','/phonenumber'))]
    assert sum(path.endswith('/scanfile') for _,path in writes)==1
    assert sum(path.endswith('/attachments') for _,path in writes)==1
    assert sum(path.endswith('/jobapplication/'+REMOTE+'/resumeattachments') for _,path in writes)==1
    assert sum(path.endswith('/jobapplication/'+REMOTE+'/workexperiences') for _,path in writes)==1
    assert employer.records['workexperiences'] and employer.records['educations']
    message=employer.mailbox.messages_sent[-1]
    assert 'Confirmed submissions: 1' in message.get_body(preferencelist=('plain',)).get_content()
    after=copy.deepcopy(employer.requests)
    assert employer.run()['attempts']==0 and employer.requests==after and not employer.unexpected
