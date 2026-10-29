"""Worker/browser/ATS/outbox integration with a completely closed fake network.

Fixtures use synthetic identities on native ATS origins so production request
routing, upload acknowledgements and submission guards run unchanged. Nothing
falls through to an employer, Google, or a real mailbox.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html import escape
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import pytest

from hireme.browser import ASHBY_UPLOAD_HOST, Browser
from hireme.gmail import GmailClient
from hireme.store import Store
from hireme.util import Blocked, digest
from hireme.worker import cycle


FIXTURES = Path(__file__).parent / 'fixtures'
SAVE_QUERY = (FIXTURES / 'ashby-set-form-value.graphql').read_text()
SUBMIT_QUERY = (FIXTURES / 'ashby-submit-single-application-form.graphql').read_text()
CREATE_FILE_QUERY = (FIXTURES / 'ashby-create-file-upload-handle.graphql').read_text()
ATTACH_FILE_QUERY = (FIXTURES / 'ashby-set-form-value-to-file.graphql').read_text()
GH_UPLOAD_HOST = 'grnhse-prod-jben-us-west-2.s3.us-west-2.amazonaws.com'
GH_EU_UPLOAD_HOST = 'grnhse-prod-jben-eu-west-1.s3.eu-west-1.amazonaws.com'
TITLE = 'Software Engineer Intern Summer 2027'
DESCRIPTION = 'Build Python and TypeScript software in San Francisco, United States.'
RECEIPT = 'Thank you for applying. Your application has been received.'


def identifier(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic-pipeline/' + value))


@dataclass
class Posting:
    platform: str
    name: str
    outcome: str = 'confirmed'
    questions: tuple[tuple[str, str], ...] = ()
    saved: dict = field(default_factory=dict)
    requests: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    attached_file: dict | None = None
    region: str = 'us'
    parser_company: str | None = None
    initial_company: str = ''
    parser_links: list | None = None
    initial_other: str = ''
    final_body: bytes | None = None

    def __post_init__(self):
        self.req = identifier(self.name)
        self.render = identifier(self.name + '/render')
        self.definition = identifier(self.name + '/definition')
        self.action = identifier(self.name + '/submit')
        self.account = identifier(self.name + '/account')
        self.tenant = 'pipeline-' + digest(self.name)[:10]
        self.host = {'ashby': 'jobs.ashbyhq.com', 'greenhouse': 'job-boards.eu.greenhouse.io' if self.region=='eu' else 'job-boards.greenhouse.io',
                     'lever': 'jobs.lever.co'}[self.platform]
        path = (f'/{self.tenant}/jobs/{int(digest(self.name)[:7], 16)}' if self.platform == 'greenhouse'
                else f'/{self.tenant}/{self.req}')
        self.url = 'https://' + self.host + path
        self.job = {'id': digest(self.url), 'url': self.url, 'host': self.host,
                    'company': self.name, 'title': TITLE, 'description': DESCRIPTION,
                    'location': 'San Francisco, United States', 'source': 'synthetic-pipeline'}

    @property
    def fields(self):
        basic=([('name','Full name','text'),('email','Email','email')] if self.platform=='lever' else
               [('first_name','First name','text'),('last_name','Last name','text'),('email','Email','email')] if self.platform=='greenhouse' else
               [('_systemfield_name','Full name','text'),('_systemfield_email','Email','email')])
        return [*basic,
                *[(f'question_{1000+i}' if self.platform=='greenhouse' else f'question-{i}', label, kind)
                  for i, (label, kind) in enumerate(self.questions)]]

    def greenhouse_loader(self):
        identifier=self.url.rsplit('/',1)[1]
        canonical='https://boards.eu.greenhouse.io' if self.region=='eu' else 'https://boards.greenhouse.io'
        return {'root':{'internal':False,'preview':False,'urlToken':self.tenant,
                       'ENV':{'JBEN_URL':canonical}},
                'routes/$url_token_.jobs_.$job_post_id':{'urlToken':self.tenant,'jobPostId':identifier,
                    'submitPath':canonical+urlsplit(self.url).path,
                    'confirmationPath':urlsplit(self.url).path+'/confirmation',
                    'jobPost':{'public_url':self.url,'post_type':'job_post','fingerprint':'a'*40,
                        'questions':[{'label':label,'required':True,'fields':[{'name':name,'type':'input_text'}]}
                            for name,label,_ in self.fields]+[{'label':'Resume','required':True,
                                'fields':[{'name':'resume','type':'input_file'}]}]}}}

    def form(self):
        entries=[{'id': self.render + '_' + path, 'field': {'path': path, 'title': label, 'type': 'String'},
                  'fieldValue': {'value': self.saved[path]} if path in self.saved else None}
                 for path, label, _ in self.fields]
        entries.append({'id':self.render+'__systemfield_resume',
                        'field':{'path':'_systemfield_resume','title':'Resume','type':'File','isMany':False},
                        'fieldValue':self.attached_file})
        return {'id': self.render, 'sourceFormDefinitionId': self.definition,
                'formControls': [{'identifier': self.action, 'title': 'Submit', '__typename': 'FormControl'}],
                'errorMessages': [], 'formErrors': [], 'sections': [{'fieldEntries':entries}]}

    def submit_payload(self):
        return {'operationName': 'ApiSubmitSingleApplicationFormAction', 'query': SUBMIT_QUERY,
                'variables': {'organizationHostedJobsPageName': self.tenant, 'jobPostingId': self.req,
                              'formRenderIdentifier': self.render, 'formDefinitionIdentifier': self.definition,
                              'actionIdentifier': self.action, 'recaptchaToken': 'synthetic-passive-token',
                              'sourceAttributionCode': None, 'viewedAutomatedProcessingLegalNoticeRuleId': None,
                              'deviceFingerprint': None}}

    def html(self):
        controls = ''.join(
            f'<div data-field-path="{path}" data-field-entry-id="{self.render}_{path}">'
            f'<label for="{path}">{escape(label)}</label><input id="{path}" name="{path}" '
            f'type="{kind}" required></div>' for path, label, kind in self.fields)
        lever = (f'<input type="hidden" name="accountId" value="{self.account}">'
                 '<input type="hidden" name="resumeStorageId" value="">'
                 '<input type="hidden" name="h-captcha-response" value="synthetic-native-token">' if self.platform == 'lever' else '')
        if self.platform=='lever' and self.parser_company is not None:
            lever+='<label for="org">Current company</label><input id="org" name="org" value="'+escape(self.initial_company,quote=True)+'">'
        if self.platform=='lever' and self.parser_links is not None:
            lever+='<label for="parser-other">'+escape(self.name)+'</label><input id="parser-other" name="urls[Other]" value="'+escape(self.initial_other,quote=True)+'">'
        resume_id = 'resume-upload-input' if self.platform == 'lever' else 'resume'
        base = {'organizationHostedJobsPageName': self.tenant, 'formRenderIdentifier': self.render,
                'formDefinitionIdentifier': self.definition}
        bootstrap = {'operationName': 'ApiJobPosting', 'query': 'query ApiJobPosting { jobPosting { id } }',
                     'variables': {'organizationHostedJobsPageName': self.tenant, 'jobPostingId': self.req}}
        return ('<!doctype html><html><head><meta charset="utf-8"></head><body><h1>' + TITLE + '</h1><p>' + DESCRIPTION + '</p>'
                '<form id="application-form" method="post" enctype="multipart/form-data">' + controls + lever
                +f'<div data-field-path="_systemfield_resume" data-field-entry-id="{self.render}__systemfield_resume"><label for="{resume_id}">Resume</label>'
                f'<input id="{resume_id}" type="file" name="resume" required></div>'
                '<button type="submit">Submit application</button></form><script>\n'
                f'const platform={json.dumps(self.platform)}, outcome={json.dumps(self.outcome)};\n'
                f'const base={json.dumps(base)}, saveQuery={json.dumps(SAVE_QUERY)};\n'
                f'const finalPayload={json.dumps(self.submit_payload())};\n'
                f'const createFileQuery={json.dumps(CREATE_FILE_QUERY)},attachFileQuery={json.dumps(ATTACH_FILE_QUERY)};\n'
                f'const ghLoader={json.dumps(self.greenhouse_loader())};\n'
                "if(platform==='greenhouse')window.__remixContext={state:{loaderData:ghLoader}};\n"
                "const endpoint='/api/non-user-graphql';\n"
                "const send=data=>fetch(endpoint,{method:'POST',body:JSON.stringify(data)});\n"
                "if(outcome==='blur_validation')document.querySelector('input[type=email]').addEventListener('blur',e=>fetch('https://email-address-validator.us.greenhouse.io/address/validate?address='+encodeURIComponent(e.target.value)).catch(()=>{}));\n"
                + (f'send({json.dumps(bootstrap)});\n' if self.platform == 'ashby' else '')
                + "for(const el of document.querySelectorAll('input:not([type=file]):not([type=hidden])')){\n"
                " if(platform==='ashby')el.addEventListener('input',()=>send({operationName:'ApiSetFormValue',"
                "query:saveQuery,variables:{...base,path:el.id,value:el.value}}).catch(()=>{}));}\n"
                f"document.getElementById({json.dumps(resume_id)}).addEventListener('change',async e=>{{\n"
                " const file=e.target.files[0];if(!file)return;let data=new FormData();\n"
                " if(platform==='greenhouse'){const config=await(await fetch(ghLoader.root.ENV.JBEN_URL+'/uncacheable_attributes/presigned_fields?fields%5B%5D=resume')).json();\n"
                "  const key=config.resume.key.replace('{timestamp}',String(Date.now())).replace('{unique_id}','synthetic');\n"
                "  data.append('utf8','✓');for(const [name,value] of Object.entries(config.resume.fields))data.append(name,value);\n"
                "  data.append('key',key);data.append('authenticity_token','1234');data.append('Content-Type','application/octet-stream');data.append('file',file);\n"
                "  await fetch(config.url,{method:'POST',body:data});window.fixtureGHUpload={name:file.name,url:config.url+'/'+key};return;}\n"
                " if(platform==='lever'){data.append('resume',file);data.append('accountId',document.querySelector('[name=accountId]').value);\n"
                "  const response=await fetch('/parseResume',{method:'POST',body:data});const result=await response.json();\n"
                "  document.querySelector('[name=resumeStorageId]').value=result.resumeStorageId;\n"
                "  if(result.position!==undefined)document.querySelector('[name=org]').value=outcome==='company_changed'?'Changed applicant entry':result.position;\n"
                "  const urls=new Map();for(const link of result.links||[]){const kind=['LinkedIn','Twitter','Quora','GitHub'].find(name=>link.domain&&link.domain.toLowerCase().includes(name.toLowerCase()))||'Other';urls.set(kind,link.url)}\n"
                "  for(const [kind,value] of urls){const el=document.querySelector('input[name=\"urls['+kind+']\"]');if(el)el.value=outcome==='links_changed'?'https://changed.invalid/':value}return;}\n"
                " if(platform==='ashby'){const response=await send({operationName:'ApiCreateFileUploadHandle',"
                "query:createFileQuery,variables:{organizationHostedJobsPageName:base.organizationHostedJobsPageName,fileUploadContext:'NonUserFormEngine',filename:file.name,contentType:'application/pdf',contentLength:file.size}});"
                " const result=await response.json();window.fixtureUpload=result.data.fileUploadHandle;window.fixtureHandle=window.fixtureUpload.handle;\n"
                "  data.append('Content-Type','application/pdf');for(const [name,value] of Object.entries(JSON.parse(window.fixtureUpload.fields)))data.append(name,value);}\n"
                " data.append('file',file);const upload=window.fixtureUpload.url;\n"
                " await fetch(upload,{method:'POST',body:data});\n"
                " if(platform==='ashby')await send({operationName:'ApiSetFormValueToFile',"
                "query:attachFileQuery,"
                "variables:{...base,path:'_systemfield_resume',fileHandle:window.fixtureHandle}});\n"
                "});\n"
                "window.fixtureComplete=async form=>{\n"
                " let response;if(platform==='greenhouse'){const application={first_name:'',last_name:'',email:'',answers_attributes:{},demographic_answers:[],data_compliance:{},attachments:{},from_job_board_renderer:true,\n"
                "  educations:[{school_name_id:null,degree_id:null,discipline_id:null,start_date:{month:null,year:null},end_date:{month:null,year:null}}],\n"
                "  employments:[{current:false,start_date:{month:null,year:null},end_date:{month:null,year:null}}],mapped_url_token:null,appcast_click_id:null,time_zone:Intl.DateTimeFormat().resolvedOptions().timeZone};\n"
                "  let priority=0;for(const input of form.querySelectorAll('input:not([type=file]):not([type=hidden])')){if(input.name.startsWith('question_')){const id=input.name.slice(9);application.answers_attributes[id]={question_id:id,priority:priority++,text_value:input.value};}else application[input.name]=input.value;}\n"
                "  application.resume_url=window.fixtureGHUpload.url;application.resume_url_filename=window.fixtureGHUpload.name;\n"
                "  window.fixtureGHApplication=application;const route=ghLoader['routes/$url_token_.jobs_.$job_post_id'];const payload=JSON.stringify({job_application:application,fingerprint:'a'.repeat(40),'g-recaptcha-enterprise-token':'synthetic-passive-token'});\n"
                "  if(outcome==='wrong_region')await fetch('https://boards.greenhouse.io'+new URL(route.submitPath).pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:payload}).catch(()=>{});\n"
                "  response=await fetch(route.submitPath,{method:'POST',headers:{'Content-Type':'application/json'},body:payload});\n"
                " }else response=platform==='ashby'?await send(finalPayload):await fetch(location.href,{method:'POST',body:new FormData(form)});\n"
                + (f" if(outcome==='receipt_read'||outcome==='challenge_receipt_read')await send({json.dumps(bootstrap)});\n" if self.platform == 'ashby' else '')
                + " if(outcome==='captcha'){document.body.insertAdjacentHTML('beforeend','<iframe src=\"about:blank#bframe\" style=\"width:300px;height:100px\"></iframe>');return;}\n"
                " if(outcome==='verification'){document.body.innerHTML='<p>A verification code was sent to test@candidate.invalid. To submit your application, enter the 8-character code.</p><label for=\"code\">Security code</label><input id=\"code\">';return;}\n"
                " if(outcome.startsWith('verification_')){document.body.innerHTML='<p>A verification code was sent to test@candidate.invalid. To submit your application, enter the 8-character code.</p><form><label for=\"code\">Security code</label><input id=\"code\" maxlength=\"8\"><button type=\"submit\">Submit application</button></form>';\n"
                "  document.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();const route=ghLoader['routes/$url_token_.jobs_.$job_post_id'];await fetch(route.submitPath,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_application:window.fixtureGHApplication,fingerprint:'a'.repeat(40),security_code:document.getElementById('code').value})});location.assign(route.confirmationPath);});return;}\n"
                " if(outcome==='rejected'){document.body.innerHTML=\"We couldn't submit your application. Your application submission was flagged as possible spam.\";return;}\n"
                " if(outcome==='extra_write')await fetch('/unapproved-cleanup',{method:'POST',body:'synthetic cleanup'}).catch(()=>{});\n"
                + (f" if(outcome==='extra_write')await send({json.dumps(bootstrap)}).catch(()=>{{}});\n" if self.platform=='ashby' else '')
                +
                " if(platform==='greenhouse'){location.assign(ghLoader['routes/$url_token_.jobs_.$job_post_id'].confirmationPath);return;}\n"
                f" document.body.innerHTML={json.dumps(RECEIPT)};\n"
                "};document.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();\n"
                " if(outcome==='challenge_receipt_read'){document.body.insertAdjacentHTML('beforeend','<iframe id=\"fixture-challenge\" src=\"about:blank#bframe\" style=\"width:300px;height:100px\"></iframe>');return;}\n"
                " await window.fixtureComplete(e.target);});</script></body></html>")


class FakeMailbox:
    """The real GmailClient checks its owner and serializes the actual MIME."""
    def __init__(self, fail_send=False):
        self.fail_send = fail_send
        self.messages_sent = []

    def users(self): return self
    def messages(self): return self
    def getProfile(self, **kwargs):
        assert kwargs == {'userId': 'me'}
        return SimpleNamespace(execute=lambda: {'emailAddress': 'test@candidate.invalid'})

    def send(self, **kwargs):
        assert kwargs['userId'] == 'me'
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(kwargs['body']['raw']))
        self.messages_sent.append(message)
        def execute():
            if self.fail_send: raise TimeoutError('synthetic delivery interruption')
            return {'id': 'synthetic-mail-' + str(len(self.messages_sent))}
        return SimpleNamespace(execute=execute)


class ClosedATS:
    def __init__(self, postings, store, monkeypatch, *, fail_mail=False, crash_after_acceptance=False):
        self.postings = postings
        self.store = store
        self.mailbox = FakeMailbox(fail_mail)
        self.unexpected = []
        self.browser_starts = 0
        self.crash_after_acceptance = crash_after_acceptance
        self.document = dict(store.db.execute('SELECT * FROM documents WHERE kind="resume"').fetchone())
        self.document_bytes = (store.root / 'documents' / self.document['filename']).read_bytes()
        monkeypatch.setattr('hireme.browser.public_host', lambda host: True)
        import hireme.reports
        flush = hireme.reports.flush_reports
        monkeypatch.setattr(hireme.reports, 'flush_reports',
                            lambda s: flush(s, lambda selected: GmailClient(selected, self.mailbox)))
        fixture = self
        class FixtureBrowser(Browser):
            def __enter__(browser):
                fixture.browser_starts += 1
                super().__enter__()
                fixture.last_browser=browser
                browser.context.route('**/*', lambda route: fixture.route(browser, route))
                return browser
            def _outcome_screenshot(browser, path, **kwargs):
                if fixture.crash_after_acceptance:
                    fixture.crash_after_acceptance = False
                    assert browser.ashby_submission.acknowledged
                    assert browser.store.application_record(browser.aid)['state'] == 'submitting'
                    raise SystemExit('synthetic process loss after accepted transport')
                return super()._outcome_screenshot(path, **kwargs)
        self.browser_factory = FixtureBrowser

    def install_posting_reads(self, monkeypatch):
        fixture = self
        def read(network, url, data=None):
            for posting in fixture.postings:
                expected = f'https://api.lever.co/v0/postings/{posting.tenant}/{posting.req}?mode=json'
                if posting.platform == 'lever' and url == expected and data is None:
                    if network.checkpoint: network.checkpoint()
                    return {'id': posting.req, 'text': TITLE, 'descriptionPlain': DESCRIPTION, 'lists': [],
                            'hostedUrl': posting.url, 'applyUrl': posting.url + '/apply'}
            raise AssertionError('Unintercepted non-browser network request: ' + str(url))
        monkeypatch.setattr('hireme.net.Network.json', read)

    def route(self, browser, route):
        request = route.request
        posting = next((p for p in self.postings if request.url in {p.url, p.url + '/apply'}), None)
        posting = posting or next((p for p in self.postings if p.host == browser.current_host and
                        (browser.ashby_draft and browser.ashby_draft.job['id'] == p.job['id']
                         or p.platform != 'ashby' and p.url in browser.page.url)), None)
        if posting is None:
            self.unexpected.append((request.method, request.url)); route.abort(); return
        fixture = self
        class Transport:
            def __init__(self): self.request = request
            def abort(self): route.abort()
            def continue_(self, **changes):
                fixture.respond(browser, posting, route, changes)
        # Every candidate request uses the real production destination/write guard.
        browser._route(Transport())

    def respond(self, browser, posting, route, changes):
        request = route.request
        parsed = urlsplit(request.url)
        posting.requests.append((request.method, parsed.hostname, parsed.path))
        headers = {'access-control-allow-origin': '*','access-control-allow-methods':'GET, POST, OPTIONS',
                   'access-control-allow-headers':'Content-Type'}
        def answer(value, status=200):
            route.fulfill(status=status, content_type='application/json', headers=headers, body=json.dumps(value))
        if request.method=='OPTIONS':route.fulfill(status=204,headers=headers,body='');return
        if posting.platform=='greenhouse' and parsed.path==urlsplit(posting.url).path+'/confirmation':
            route.fulfill(status=200,content_type='text/html',body='<html><body>'+RECEIPT+'</body></html>');return
        if posting.platform=='greenhouse' and parsed.path=='/uncacheable_attributes/presigned_fields':
            answer({'url':'https://'+(GH_EU_UPLOAD_HOST if posting.region=='eu' else GH_UPLOAD_HOST)+'/', 'resume':{'key':'fixture/{timestamp}/{unique_id}/resume',
                'fields':{'policy':'synthetic-upload-policy','x-amz-signature':'synthetic-signature'}}});return
        if request.method == 'GET' and request.url in {posting.url, posting.url + '/apply'}:
            if posting.platform=='lever' and request.url==posting.url:
                route.fulfill(status=200,content_type='text/html',body='<h1>'+TITLE+'</h1><p>'+DESCRIPTION+'</p><a href="'+posting.url+'/apply">Apply for this job</a>');return
            route.fulfill(status=200, content_type='text/html', body=posting.html()); return
        if parsed.hostname in {ASHBY_UPLOAD_HOST, GH_UPLOAD_HOST, GH_EU_UPLOAD_HOST}:
            body = changes.get('post_data',request.post_data_buffer or b'')
            assert self.document['hash'].encode() in body
            assert self.document_bytes in body
            answer({'uploaded': True}); return
        if posting.platform == 'ashby' and parsed.path == '/api/non-user-graphql':
            data = json.loads(request.post_data)
            operation = data['operationName']
            posting.operations.append(operation)
            if operation == 'ApiJobPosting':
                answer({'data': {'jobPosting': {'id': posting.req, 'applicationForm': posting.form(),
                                                'surveyForms': [], 'automatedProcessingLegalNotice': None}}}); return
            if operation == 'ApiCreateFileUploadHandle':
                assert data['query']==CREATE_FILE_QUERY
                assert data['variables']['organizationHostedJobsPageName']==posting.tenant
                assert data['variables']['fileUploadContext']=='NonUserFormEngine'
                assert data['variables']['filename'] == self.document['filename']
                assert data['variables']['contentLength'] == len(self.document_bytes)
                answer({'data': {'fileUploadHandle': {'handle': 'fixture-' + posting.req,
                    'url':'https://'+ASHBY_UPLOAD_HOST+'/',
                    'fields':json.dumps({'key':'fixture/'+posting.req,'policy':'synthetic-policy','x-amz-signature':'synthetic-signature'})}}}); return
            if operation == 'ApiSetFormValueToFile':
                assert data['query']==ATTACH_FILE_QUERY
                assert data['variables']['fileHandle'] == 'fixture-' + posting.req
                assert data['variables']=={'organizationHostedJobsPageName':posting.tenant,'formRenderIdentifier':posting.render,
                    'formDefinitionIdentifier':posting.definition,'path':'_systemfield_resume','fileHandle':'fixture-'+posting.req}
                posting.attached_file={'__typename':'File','id':'native-file-'+posting.req,'filename':self.document['filename']}
                answer({'data': {'setFormValueToFile':posting.form()}}); return
            if operation == 'ApiSetFormValue':
                assert not browser.attempted
                posting.saved[data['variables']['path']] = data['variables']['value']
                value = {'data': {'setFormValue': posting.form()}}
                if posting.outcome == 'bad_save': value['errors'] = [{'message': 'Synthetic save rejection'}]
                answer(value); return
            assert operation == 'ApiSubmitSingleApplicationFormAction'
            assert data == posting.submit_payload() and browser.attempted
            assert posting.saved['_systemfield_email'] == 'test@candidate.invalid'
            result = 'FormSubmitError' if posting.outcome in {'bad_ack', 'rejected'} else 'FormSubmitSuccess'
            answer({'data': {'submitApplicationFormAction': {'applicationFormResult': {'__typename': result},
                                                           'messages': {'blockMessageForCandidateHtml': None}}}})
            return
        if posting.platform == 'lever' and parsed.path == '/parseResume':
            assert not browser.attempted
            payload = changes.get('post_data', request.post_data_buffer or b'')
            assert self.document_bytes in payload and posting.account.encode() in payload
            answer({'resumeStorageId': 'fixture-storage-' + posting.req,
                    **({'links':posting.parser_links} if posting.parser_links is not None else {}),
                    **({'position':posting.parser_company} if posting.parser_company is not None else {})}); return
        if posting.platform in {'greenhouse', 'lever'} and (posting.platform=='greenhouse' and parsed.hostname==('boards.eu.greenhouse.io' if posting.region=='eu' else 'boards.greenhouse.io') and parsed.path==urlsplit(posting.url).path or posting.platform=='lever' and request.url==posting.url+'/apply'):
            assert browser.attempted
            payload = changes.get('post_data',request.post_data_buffer or b'')
            posting.final_body=payload
            assert b'test@candidate.invalid' in payload
            assert (b'Test Person' in payload if posting.platform=='lever' else b'Test' in payload and b'Person' in payload)
            if posting.platform == 'lever': assert ('fixture-storage-' + posting.req).encode() in payload
            if posting.platform=='lever':
                assert self.document_bytes in payload
                route.fulfill(status=200,content_type='text/html',body='<html><body>'+RECEIPT+'</body></html>')
            elif posting.outcome.startswith('verification'):
                data=json.loads(payload)
                if 'security_code' not in data:
                    answer({'code':'captcha-failed','security_code_recipient':'test@candidate.invalid'},403)
                else:
                    assert data['security_code']=='AB12CD34'
                    answer({},503 if posting.outcome=='verification_bad_ack' else 200)
            else:answer({})
            return
        self.unexpected.append((request.method, request.url)); route.abort()

    def run(self, store=None, **kwargs):
        selected = store or self.store
        return cycle(selected, Path('.'), discover=False, browser_factory=self.browser_factory,
                     job_ids=[p.job['id'] for p in self.postings], limit=10, **kwargs)


def harness(store, monkeypatch, postings, **kwargs):
    store.update_settings({'gmail_reports': True, 'target_per_cycle': 10})
    for posting in postings: store.upsert_job(posting.job)
    network = ClosedATS(postings, store, monkeypatch, **kwargs)
    network.install_posting_reads(monkeypatch)
    return network


@pytest.mark.parametrize('platform,region', [('ashby','us'),('greenhouse','us'),('greenhouse','eu'),('lever','us')])
def test_real_pipeline_confirms_and_reports_then_excludes_the_same_posting(store, monkeypatch, platform,region):
    posting = Posting(platform, 'Synthetic ' + platform+' '+region,region=region)
    network = harness(store, monkeypatch, [posting])
    result = network.run()
    assert not result['reasons'],{'outcomes':[json.loads(row[0]) for row in store.db.execute("SELECT detail FROM events WHERE kind='application_finished'")],
        'upload_fields':network.last_browser.greenhouse_upload_fields,'uploads':network.last_browser.greenhouse_uploads,'requests':posting.requests,'unexpected':network.unexpected}
    assert result['confirmed'] == result['attempts'] == 1,result
    app = store.db.execute('SELECT * FROM applications').fetchone()
    assert app['state'] == 'confirmed' and app['attempted']
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0] == 1
    if platform == 'ashby':
        assert store.db.execute("SELECT count(*) FROM events WHERE kind='ashby_submission_write'").fetchone()[0] == 1
        response = store.db.execute("SELECT detail FROM events WHERE kind='ashby_submission_response'").fetchone()
        assert json.loads(response[0])['acknowledged'] is True
    assert store.db.execute('SELECT state,attempts FROM report_outbox').fetchone()[:] == ('sent', 1)
    message, = network.mailbox.messages_sent
    body = message.get_body(preferencelist=('plain',)).get_content()
    assert message['To'] == message['From'] == 'test@candidate.invalid'
    assert 'Confirmed submissions: 1' in body and 'Applied successfully' in body and body.count(posting.url) == 1
    before = copy.deepcopy(posting.requests)
    reopened = Store(store.root)
    try:
        second = network.run(reopened)
    finally:
        reopened.close()
    assert second['attempts'] == second['confirmed'] == 0
    assert posting.requests == before and network.browser_starts == 1 and not network.unexpected
    assert len(network.mailbox.messages_sent) == 1
    assert {row[0] for row in store.db.execute('SELECT state FROM report_outbox')} == {'sent', 'suppressed'}


@pytest.mark.parametrize('initial_company,outcome',[('','confirmed'),('Existing applicant entry','confirmed'),('','company_changed')])
def test_lever_parser_company_cannot_add_an_unapproved_claim_or_erase_existing_input(store,monkeypatch,initial_company,outcome):
    posting=Posting('lever','Synthetic parser company',parser_company='Parsed Employer',initial_company=initial_company,outcome=outcome)
    network=harness(store,monkeypatch,[posting])
    result=network.run()
    if initial_company or outcome=='company_changed':
        assert result['confirmed']==0
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='parser_prefill_cleared'").fetchone()
        assert any(request[2]=='/parseResume' for request in posting.requests)==(not initial_company)
    else:
        assert result['confirmed']==1,result
        app=store.db.execute('SELECT package FROM applications').fetchone()
        assert all(a['field'].get('ref',{}).get('name')!='org' for a in json.loads(app[0])['answers'])
        assert store.db.execute("SELECT 1 FROM events WHERE kind='parser_prefill_cleared'").fetchone()
    assert not network.unexpected


@pytest.mark.parametrize('initial_other,outcome',[('','confirmed'),('Existing applicant entry','confirmed'),('','links_changed')])
def test_lever_parser_links_cannot_add_unapproved_urls_or_erase_existing_input(store,monkeypatch,initial_other,outcome):
    posting=Posting('lever','Synthetic parser links',parser_links=[{'domain':'example.invalid','url':'https://parser-only.invalid/'}],
                    initial_other=initial_other,outcome=outcome)
    network=harness(store,monkeypatch,[posting]);result=network.run()
    if initial_other or outcome=='links_changed':
        assert result['confirmed']==0
        assert result['reasons']=={'missing_answers' if initial_other else 'unsupported_form':1}
        if initial_other:assert not any(request[2]=='/parseResume' for request in posting.requests)
        assert not store.db.execute("SELECT 1 FROM events WHERE kind IN ('submit_intent','parser_prefill_cleared')").fetchone()
    else:
        assert result['confirmed']==1,result
        events=[json.loads(r[0]) for r in store.db.execute("SELECT detail FROM events WHERE kind='parser_prefill_cleared'")]
        assert [e['field'] for e in events]==['urls[Other]']
        final=[r for r in posting.requests if r[0]=='POST' and r[2].endswith('/apply')]
        assert len(final)==1
        assert posting.final_body and b'https://parser-only.invalid/' not in posting.final_body
    assert not network.unexpected


def test_eu_greenhouse_rejects_wrong_region_post_before_the_one_canonical_native_grant(store,monkeypatch):
    posting=Posting('greenhouse','Synthetic EU native region binding','wrong_region',region='eu')
    network=harness(store,monkeypatch,[posting])
    result=network.run()
    assert result['confirmed']==result['attempts']==1,result
    final_path=urlsplit(posting.url).path
    assert ('POST','boards.eu.greenhouse.io',final_path) in posting.requests
    assert ('POST','boards.greenhouse.io',final_path) not in posting.requests
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='native_submission_write'").fetchone()[0]==1
    assert not network.unexpected


@pytest.mark.parametrize('platform,outcome,state', [
    ('ashby', 'bad_ack', 'unknown'), ('greenhouse', 'verification', 'awaiting_verification'),
    ('lever', 'captcha', 'unknown'),
])
def test_real_pipeline_preserves_uncertainty_across_mail_failure_restart_and_second_cycle(
        store, monkeypatch, platform, outcome, state):
    posting = Posting(platform, 'Synthetic uncertain ' + platform, outcome)
    network = harness(store, monkeypatch, [posting], fail_mail=True)
    result = network.run()
    assert result['confirmed'] == 0 and result['attempts'] == 1
    assert result['outcomes'] == {state: 1}
    app = dict(store.db.execute('SELECT * FROM applications').fetchone())
    assert app['state'] == state
    first_report = dict(store.db.execute('SELECT * FROM report_outbox').fetchone())
    assert first_report['state'] == 'uncertain' and first_report['attempts'] == 1
    body = network.mailbox.messages_sent[0].get_body(preferencelist=('plain',)).get_content()
    assert 'Other outcomes — review before retrying' in body
    assert ('Email verification is pending.' if state == 'awaiting_verification'
            else 'Submission outcome is uncertain.') in body
    before = copy.deepcopy(posting.requests)
    reopened = Store(store.root)
    try:
        second = network.run(reopened)
    finally:
        reopened.close()
    assert second['attempts'] == second['confirmed'] == 0 and posting.requests == before
    assert dict(store.db.execute('SELECT * FROM applications').fetchone()) == app
    assert dict(store.db.execute('SELECT * FROM report_outbox WHERE id=?', (first_report['id'],)).fetchone()) == first_report
    assert sum(first_report['message_id'] == message['Message-ID'] for message in network.mailbox.messages_sent) == 1
    assert len(network.mailbox.messages_sent) == 1
    assert not network.unexpected


def test_real_pipeline_draft_rejection_has_no_submit_intent_and_reports_its_hold(store, monkeypatch):
    posting = Posting('ashby', 'Synthetic draft rejection', 'bad_save')
    network = harness(store, monkeypatch, [posting])
    result = network.run()
    assert result['confirmed'] == 0 and result['outcomes'] == {}
    assert result['reasons'] == {'ashby_draft_save_failed': 1}
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    body = network.mailbox.messages_sent[0].get_body(preferencelist=('plain',)).get_content()
    assert 'Ashby draft save needs review' in body and posting.url in body
    before = copy.deepcopy(posting.requests)
    assert network.run()['attempts'] == 0
    assert posting.requests == before and not network.unexpected
    assert len(network.mailbox.messages_sent) == 1


@pytest.mark.parametrize('platform', ['ashby', 'greenhouse', 'lever'])
def test_preparation_keeps_documents_and_personal_values_local(store, monkeypatch, platform):
    posting = Posting(platform, 'Synthetic local preparation ' + platform)
    network = harness(store, monkeypatch, [posting])
    result = network.run(live=False)
    assert result['prepared'] == 1 and result['confirmed'] == 0
    app = store.db.execute('SELECT state,package FROM applications').fetchone()
    assert app['state'] == 'prepared'
    package = json.loads(app['package'])
    assert package['documents'][0]['hash'] == network.document['hash']
    assert package['facts_hash'] == digest(store.facts())
    writes = [request for request in posting.requests if request[0] not in {'GET', 'HEAD', 'OPTIONS'}]
    assert writes == ([('POST', posting.host, '/api/non-user-graphql')] if platform == 'ashby' else [])
    assert posting.saved == {} and not network.mailbox.messages_sent and not network.unexpected
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_setup_failure_still_delivers_an_actionable_report_without_browser_activity(store, monkeypatch):
    network = harness(store, monkeypatch, [])
    store.update_settings({'live_enabled': False, 'onboarding_complete': False})
    with pytest.raises(Blocked, match='setup_incomplete'):
        network.run()
    assert network.browser_starts == 0 and not network.unexpected
    assert store.db.execute('SELECT state FROM report_outbox').fetchone()[0] == 'sent'
    message, = network.mailbox.messages_sent
    body = message.get_body(preferencelist=('plain',)).get_content()
    assert 'Status: blocked' in body and 'Attempts: 0' in body and 'setup_incomplete' in body


def test_preparation_blocks_native_greenhouse_email_validation_on_blur_without_blocking_posting_reads(store,monkeypatch):
    posting=Posting('greenhouse','Synthetic local preparation email privacy','blur_validation')
    network=harness(store,monkeypatch,[posting])
    parent=network.browser_factory
    class BlurBrowser(parent):
        def _verify(browser,answers,documents,fields):
            super()._verify(answers,documents,fields)
            browser.page.locator('input[type=email]').focus()
            browser.page.locator('input[type=email]').press('Tab')
            browser.page.wait_for_timeout(50)
    with BlurBrowser(store) as browser:assert browser.apply(posting.job,live=False)=='prepared'
    assert ('GET',posting.host,urlsplit(posting.url).path) in posting.requests
    assert not any('email-address-validator' in host for _,host,_ in posting.requests)
    suppressed=[json.loads(row[0]) for row in store.db.execute("SELECT detail FROM events WHERE kind='preparation_write_suppressed'")]
    assert {'host':'email-address-validator.us.greenhouse.io','path':'/address/validate','method':'GET'} in suppressed
    assert 'test@candidate.invalid' not in json.dumps(suppressed)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    assert not network.unexpected and not network.mailbox.messages_sent


@pytest.mark.parametrize('change', ['pause', 'facts'])
def test_native_accepted_outcome_is_observed_after_authority_changes_without_new_writes(store, monkeypatch, change):
    posting = Posting('ashby', 'Synthetic accepted then changed ' + change, 'receipt_read')
    network = harness(store, monkeypatch, [posting])
    event = store.event
    changed = []
    def record_and_change(kind, subject, detail):
        event(kind, subject, detail)
        if kind == 'ashby_submission_response' and detail.get('acknowledged') and not changed:
            changed.append(change)
            if change == 'pause': store.update_settings({'live_enabled': False})
            else: store.put_facts({'skills': 'Python, TypeScript, Rust'})
    monkeypatch.setattr(store, 'event', record_and_change)
    if change == 'pause':
        with pytest.raises(Blocked, match='paused'): network.run()
    else:
        assert network.run()['confirmed'] == 1
    assert changed == [change]
    assert store.db.execute('SELECT state FROM applications').fetchone()[0] == 'confirmed'
    run = store.db.execute('SELECT status,submitted FROM runs').fetchone()
    assert run[:] == ('paused' if change == 'pause' else 'finished', 1)
    write = store.db.execute("SELECT seq FROM events WHERE kind='ashby_submission_write'").fetchone()[0]
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_draft_write' AND seq>?", (write,)).fetchone()
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='ashby_submission_write'").fetchone()[0] == 1
    assert posting.operations.count('ApiJobPosting') == 2
    assert posting.operations.count('ApiSubmitSingleApplicationFormAction') == 1
    message, = network.mailbox.messages_sent
    assert 'Confirmed submissions: 1' in message.get_body(preferencelist=('plain',)).get_content()
    before = copy.deepcopy(posting.requests)
    if change == 'pause': store.update_settings({'live_enabled': True})
    assert network.run()['attempts'] == 0
    assert posting.requests == before and len(network.mailbox.messages_sent) == 1 and not network.unexpected


@pytest.mark.parametrize('platform',['ashby','greenhouse','lever'])
def test_challenge_completion_keeps_native_accepted_outcome_after_pause_without_another_write(store,monkeypatch,platform):
    posting=Posting(platform,'Synthetic challenge accepted then paused '+platform,'challenge_receipt_read')
    network=harness(store,monkeypatch,[posting])
    parent=network.browser_factory
    active=[]
    class ChallengeBrowser(parent):
        def __enter__(browser):
            super().__enter__()
            # Exercise the actual frame detection, transport and human-wait
            # phases in headless Chromium; desktop visibility is a separate gate.
            browser.visible=True
            store.update_settings({'headless':False,'human_verification_wait_seconds':2})
            active.append(browser)
            return browser
    network.browser_factory=ChallengeBrowser
    actual_event=store.event
    phases=[];paused=[]
    def event(kind,subject,detail):
        actual_event(kind,subject,detail)
        if kind=='human_verification':
            phases.append(detail['phase'])
            if detail['phase']=='challenge':
                browser=active[0]
                assert browser.page.locator('#fixture-challenge').is_visible()
                assert not (browser.ashby_submission or browser.native_submission).used
                browser.page.evaluate('() => window.fixtureComplete(document.querySelector("form"))')
        if kind in {'ashby_submission_response','native_submission_response'} and detail.get('acknowledged') and not paused:
            paused.append(True)
            store.update_settings({'live_enabled':False})
    monkeypatch.setattr(store,'event',event)
    with pytest.raises(Blocked,match='paused'):network.run()
    assert paused==[True] and phases==['challenge','checking_receipt','finished']
    assert store.db.execute('SELECT state FROM applications').fetchone()[0]=='confirmed'
    assert store.db.execute('SELECT status,submitted FROM runs').fetchone()[:]==('paused',1)
    if platform=='ashby':
        assert posting.operations.count('ApiSubmitSingleApplicationFormAction')==1
        assert posting.operations.count('ApiJobPosting')==2
    assert store.db.execute("SELECT count(*) FROM events WHERE kind IN ('ashby_submission_write','native_submission_write')").fetchone()[0]==1
    final=store.db.execute("SELECT seq FROM events WHERE kind IN ('ashby_submission_write','native_submission_write')").fetchone()[0]
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_draft_write' AND seq>?",(final,)).fetchone()
    message,=network.mailbox.messages_sent
    assert 'Confirmed submissions: 1' in message.get_body(preferencelist=('plain',)).get_content()
    before=copy.deepcopy(posting.requests)
    store.update_settings({'live_enabled':True})
    assert network.run()['attempts']==0
    assert posting.requests==before and len(network.mailbox.messages_sent)==1 and not network.unexpected


@pytest.mark.parametrize('platform',['ashby','greenhouse','lever'])
def test_blocked_extra_write_after_native_acceptance_does_not_discard_the_receipt(store,monkeypatch,platform):
    posting=Posting(platform,'Synthetic accepted before blocked cleanup '+platform,'extra_write')
    network=harness(store,monkeypatch,[posting])
    result=network.run()
    assert result['confirmed']==result['attempts']==1,result
    assert store.db.execute('SELECT state FROM applications').fetchone()[0]=='confirmed'
    assert network.last_browser.denied_write
    assert network.last_browser.denied_request['path']=='/unapproved-cleanup'
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='post_submit_write_blocked'").fetchone()[0]==1
    assert not any(path=='/unapproved-cleanup' for _,_,path in posting.requests)
    if platform=='ashby':assert posting.operations.count('ApiJobPosting')==2
    assert store.db.execute("SELECT count(*) FROM events WHERE kind IN ('ashby_submission_write','native_submission_write')").fetchone()[0]==1
    message,=network.mailbox.messages_sent
    assert 'Confirmed submissions: 1' in message.get_body(preferencelist=('plain',)).get_content()
    before=copy.deepcopy(posting.requests)
    assert network.run()['attempts']==0
    assert posting.requests==before and len(network.mailbox.messages_sent)==1 and not network.unexpected


@pytest.mark.parametrize('outcome,state',[('verification_confirmed','confirmed'),('verification_bad_ack','unknown')])
def test_greenhouse_email_verification_still_requires_its_own_matching_native_ack(store,monkeypatch,outcome,state):
    posting=Posting('greenhouse','Synthetic native OTP '+outcome,outcome)
    network=harness(store,monkeypatch,[posting])
    store.update_settings({'gmail_verification':True})
    monkeypatch.setattr('hireme.gmail.GmailClient',lambda selected:GmailClient(selected,network.mailbox))
    monkeypatch.setattr(GmailClient,'find_code',lambda self,*args:'AB12CD34')
    result=network.run()
    assert result['confirmed']==int(state=='confirmed'),result
    assert store.db.execute('SELECT state FROM applications').fetchone()[0]==state
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='native_submission_write'").fetchone()[0]==2
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0]==1
    message,=network.mailbox.messages_sent
    body=message.get_body(preferencelist=('plain',)).get_content()
    assert 'Confirmed submissions: '+str(int(state=='confirmed')) in body
    assert 'AB12CD34' not in body
    before=copy.deepcopy(posting.requests)
    assert network.run()['attempts']==0
    assert posting.requests==before and len(network.mailbox.messages_sent)==1 and not network.unexpected


@pytest.mark.parametrize('fail_mail', [False, True])
def test_crash_after_native_acceptance_recovers_unknown_and_reports_once_without_resubmission(store, monkeypatch, fail_mail):
    posting = Posting('ashby', 'Synthetic process-loss recovery')
    network = harness(store, monkeypatch, [posting], crash_after_acceptance=True, fail_mail=fail_mail)
    with pytest.raises(SystemExit, match='synthetic process loss'):
        network.run()
    original = dict(store.db.execute('SELECT * FROM runs').fetchone())
    app = store.db.execute('SELECT id,state FROM applications').fetchone()
    assert original['status'] == 'running' and app['state'] == 'submitting'
    assert not network.mailbox.messages_sent
    before = copy.deepcopy(posting.requests)
    reopened = Store(store.root)
    try:
        assert network.run(reopened)['attempts'] == 0
        assert reopened.application_record(app['id'])['state'] == 'unknown'
        assert reopened.db.execute('SELECT status FROM runs WHERE id=?', (original['id'],)).fetchone()[0] == 'interrupted'
        recovered = dict(reopened.db.execute('SELECT * FROM report_outbox WHERE id=?', (original['id'],)).fetchone())
        assert recovered['state'] == ('uncertain' if fail_mail else 'sent') and recovered['attempts'] == 1
        assert network.run(reopened)['attempts'] == 0
        assert dict(reopened.db.execute('SELECT * FROM report_outbox WHERE id=?', (original['id'],)).fetchone()) == recovered
    finally:
        reopened.close()
    message, = network.mailbox.messages_sent
    body = message.get_body(preferencelist=('plain',)).get_content()
    assert 'Confirmed submissions: 0' in body and 'Attempts: 1' in body
    assert 'Submission outcome is uncertain.' in body and posting.url in body
    assert posting.requests == before and not network.unexpected
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0] == 1


def test_report_recovery_cannot_borrow_acceptance_from_a_later_explicit_retry(store, job, package):
    from hireme.reports import recover_reports
    from hireme.util import now
    store.update_settings({'gmail_reports': True})
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',
                     ('interrupted-attempt', now(), now(), 'interrupted', 0, '{"mode":"live"}'))
    store.event('application_started', job['id'], {'run_id': 'interrupted-attempt', 'attempt': 1})
    old_id = store.prepare(job, package)
    store.begin_submit(old_id)
    store.finish(old_id, 'not_submitted', 'Synthetic explicit employer rejection')
    store.retry_not_submitted(old_id, 'Fixture owner verified the rejection and explicitly approved a retry.')
    store.event('application_started', job['id'], {'run_id': 'later-attempt', 'attempt': 1})
    new_id = store.prepare(job, package)
    assert new_id == old_id  # Same package reuses its deterministic identity.
    store.begin_submit(new_id)
    store.finish(new_id, 'confirmed', 'Synthetic later employer acceptance')
    assert recover_reports(store) == 1
    report = dict(store.db.execute('SELECT * FROM report_outbox').fetchone())
    assert report['id'] == 'interrupted-attempt'
    assert 'Confirmed submissions: 0' in report['body'] and 'not_submitted' in report['body']
    assert store.application_record(new_id)['state'] == 'confirmed'
    assert recover_reports(store) == 0


def approve_text(store, posting, label, value):
    """Fixture onboarding supplies the same exact, scoped user approval as UI."""
    scope = posting.host + '|' + store.company(posting.name)
    field = {'label': label, 'type': 'text', 'required': True, 'options': [], 'maxlength': -1,
             'min': None, 'max': None, 'step': None, 'pattern': None, 'multiple': False}
    qid = store.ask(posting.job['id'], scope, label, [], field=field, context=posting.job)
    store.answer_question(qid, value)


def test_ten_job_email_regression_reuses_confirmed_facts_and_keeps_real_manual_boundaries(store, monkeypatch):
    """Sanitized shapes from the reported batch, never real employer postings.

    Explicit approvals fix personal answers. Changed agreements and unsupported
    narratives stay held; failed saves/unknown/rejected outcomes stay distinct.
    """
    pay = 'What is your expected hourly pay range for this internship?'
    waterloo = 'Are you a current University of Waterloo student?'
    military = 'Have you served in the military?'
    interest = 'Are you interested in full-time employment opportunities with us upon graduation?'
    start = 'If offered a full-time position upon graduation, what is your earliest available start date?'
    postings = [
        Posting('greenhouse', 'newsbreak fixture', questions=((pay, 'text'),)),
        Posting('greenhouse', 'point72 fixture', questions=((waterloo, 'text'), (military, 'text'), ('Privacy', 'text'))),
        Posting('ashby', 'fab2 fixture', questions=(('Have you included a portfolio with your application?', 'text'),)),
        Posting('ashby', 'chartahealth fixture', 'bad_save'),
        Posting('ashby', 'bobyard fixture', 'bad_ack'),
        Posting('ashby', 'beaconsoftware fixture'),
        Posting('ashby', 'anthelioncap fixture', questions=((interest, 'text'), (start, 'text'))),
        Posting('greenhouse', 'akunacapital platform fixture', questions=(('I certify all information and accept the new employer agreement.', 'text'),)),
        Posting('ashby', 'architect fixture', 'rejected'),
        Posting('greenhouse', 'akunacapital python fixture', questions=(('Street address', 'text'),)),
    ]
    store.put_facts({'salary': '25/hr', 'school': 'University of California, Berkeley',
                     'fulltime_start': '2028-05-19'})
    network = harness(store, monkeypatch, postings)
    approve_text(store, postings[1], waterloo, 'No')
    approve_text(store, postings[1], military, 'No')
    approve_text(store, postings[6], interest, 'Yes')
    facts = store.facts()
    approvals = [tuple(row) for row in store.db.execute('SELECT * FROM answers ORDER BY id')]
    result = network.run()
    assert result['attempts'] == 10 and result['confirmed'] == 3
    assert result['outcomes'] == {'confirmed': 3, 'unknown': 1, 'not_submitted': 1}
    by_company = {row['company']: row['status'] for row in store.db.execute('SELECT company,status FROM jobs')}
    assert by_company == {p.name: ('confirmed' if p in (postings[0], postings[5], postings[6]) else
                                  'unknown' if p is postings[4] else
                                  'not_submitted' if p is postings[8] else 'blocked') for p in postings}
    assert store.facts() == facts
    assert [tuple(row) for row in store.db.execute('SELECT * FROM answers ORDER BY id')] == approvals
    package = json.loads(store.db.execute('SELECT package FROM applications WHERE job_id=?',
                                         (postings[0].job['id'],)).fetchone()[0])
    assert next(a['value'] for a in package['answers'] if a['field']['label'] == pay) == '25'
    assert postings[6].saved['question-0'] == 'Yes'
    assert postings[6].saved['question-1'] == 'May 19, 2028'
    point_questions = list(store.db.execute('SELECT label FROM questions WHERE job_id=? AND resolved=0',
                                          (postings[1].job['id'],)))
    assert [row[0] for row in point_questions] == ['Privacy'], dict(store.db.execute(
        'SELECT reason FROM jobs WHERE id=?', (postings[1].job['id'],)).fetchone())
    message, = network.mailbox.messages_sent
    body = message.get_body(preferencelist=('plain',)).get_content()
    assert 'Confirmed submissions: 3' in body and 'Attempts: 10' in body
    assert all(body.count(p.url) == 1 for p in postings)
    assert '  • Privacy — A personal answer is missing' in body
    assert 'University of Waterloo student?' not in body and 'Have you served in the military?' not in body
    assert 'Ashby draft save needs review' in body
    assert 'Submission outcome is uncertain.' in body and 'not_submitted' in body
    before = copy.deepcopy([p.requests for p in postings])
    assert network.run()['attempts'] == 0
    assert [p.requests for p in postings] == before
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0] == 5
    assert not network.unexpected
    assert len(network.mailbox.messages_sent) == 1
