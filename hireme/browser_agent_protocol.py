"""Durable, source-bound UI operations for the application browser.

The website's own client implements its transport. Authority comes from an
observed control, approved answers/documents and a bounded operation, rather
than an inferred or model-authored backend request. No native capability
verification records are manufactured by this execution strategy.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urljoin, urlsplit

from .approval_boundary import ApprovalBoundary
from .draft_journal import DraftJournal
from .util import Blocked, digest, now


def origin(url):
    parsed=urlsplit(url)
    return parsed.scheme+'://'+parsed.netloc


def request_identity(request):
    # Route and response callbacks can carry different channel wrappers across
    # a document navigation. The exact observed request bytes remain stable.
    return (request.url,request.method,hashlib.sha256(request.post_data_buffer or b'').hexdigest())


def guard_pending(store,job):
    """A lost UI save blocks the job and company before another browser opens."""
    active=getattr(store,'browser_agent_session',None)
    from .util import application_identity
    identity=application_identity(job['url'])
    rows=store.db.execute("""SELECT DISTINCT d.job_id,d.session,d.state,j.company,j.url FROM draft_operations d
        JOIN jobs j ON j.id=d.job_id WHERE d.operation LIKE 'ui:%' AND d.state IN ('intent','unknown')""")
    for row in rows:
        if row['job_id']==job['id'] and row['session']==active and row['state']=='intent':continue
        if (row['job_id']==job['id'] or application_identity(row['url'])==identity
                or store.company(row['company'])==store.company(job['company'])):
            raise Blocked('browser_draft_uncertain','An interrupted employer UI operation needs reconciliation before another application')


def response_ok(data):
    if not isinstance(data,(dict,list)):return True
    if isinstance(data,list):return all(response_ok(item) for item in data)
    for key,value in data.items():
        if key.casefold() in {'error','errors','validationerrors','validation_errors'} and value:return False
        if key.casefold() in {'success','accepted','ok'} and value is False:return False
        if key.casefold() in {'status','state'} and isinstance(value,str) and value.casefold() in {'error','failed','rejected','invalid'}:return False
        if isinstance(value,(dict,list)) and not response_ok(value):return False
    return True


def explicit_acceptance(data):
    if isinstance(data,list):return any(explicit_acceptance(item) for item in data)
    if not isinstance(data,dict) or not response_ok(data):return False
    for key,value in data.items():
        normalized=re.sub(r'[^a-z]','',key.casefold())
        if normalized in {'submitted','applicationsubmitted','accepted','success','introductioncreated','messagesent'} and value is True:return True
        if normalized in {'status','state','applicationstatus'} and isinstance(value,str) and value.casefold() in {
                'submitted','application submitted','success','accepted','complete','completed','sent'}:return True
        if isinstance(value,(dict,list)) and explicit_acceptance(value):return True
    return False


def workday_definition_context(resource,data):
    """Native form-definition reads can include the current prompt context."""
    if data=={}:return True
    if not isinstance(data,dict):return False
    short=lambda value:isinstance(value,str) and len(value)<=2000
    def reference(value,*,prompt=False):
        if not isinstance(value,dict) or not {'id','descriptor'}<=set(value):return False
        strings={'id','descriptor','ariaLabel','ariaLabelSelected','ariaLabelUnselected'} if prompt else {'id','descriptor'}
        allowed=strings|({'index','isRTL','isSelected'} if prompt else set())
        if not set(value)<=allowed:return False
        for key,item in value.items():
            if key in strings:
                if not short(item):return False
            elif key=='index':
                if type(item) is not int or not 0<=item<=100000:return False
            elif type(item) is not bool:return False
        return True
    if resource=='previousworker':
        if len(data)==1 and next(iter(data)) in {'currentlyWorkHere','candidateIsPreviousWorker'}:
            return type(next(iter(data.values()))) is bool
        if set(data)=={'id','candidateIsPreviousWorker'}:
            return short(data['id']) and type(data['candidateIsPreviousWorker']) is bool
        return (set(data)=={'id','candidateIsPreviousWorker','manager','email','employeeID','location'}
            and short(data['id']) and type(data['candidateIsPreviousWorker']) is bool
            and all(data[k] is None or short(data[k]) for k in ('manager','email','employeeID','location')))
    if resource=='workexperiences':return set(data)=={'currentlyWorkHere'} and type(data['currentlyWorkHere']) is bool
    if resource=='source':
        return (set(data) in ({'source'},{'source','sourcePopulatedFromUrl'}) and reference(data['source'],prompt=True)
            and ('sourcePopulatedFromUrl' not in data or type(data['sourcePopulatedFromUrl']) is bool))
    if resource=='emailaddress':return set(data)=={'emailAddress'} and short(data['emailAddress'])
    if resource=='phonenumber':
        return ({'countryPhoneCode','phoneNumber'}<=set(data)<={'id','descriptor','countryPhoneCode','extension','phoneNumber','phoneType'}
            and reference(data['countryPhoneCode']) and all(short(v) for k,v in data.items() if k not in {'countryPhoneCode','phoneType'})
            and ('phoneType' not in data or reference(data['phoneType'])))
    if resource=='socials':return set(data)=={'facebookAccount','linkedInAccount','twitterAccount'} and all(short(v) for v in data.values())
    if resource=='skills':return data=={'skills':None}
    if resource=='termsandconditions':
        return set(data)=={'acceptTermsAndAgreements'} and type(data['acceptTermsAndAgreements']) is bool
    if resource=='uspersonalinfo':
        fields={'veteranStatus','gender','ethnicity'}
        return (fields<=set(data)<=fields|{'id','ethnicityMulti'} and all(reference(data[key]) for key in fields)
            and ('id' not in data or short(data['id']))
            and ('ethnicityMulti' not in data or isinstance(data['ethnicityMulti'],list)
                 and len(data['ethnicityMulti'])<=20 and all(reference(item) for item in data['ethnicityMulti'])))
    if resource=='selfidentify':
        return (set(data)=={'id','disabilityForm','name','employeeId','dateSignedOn','disabilityStatus'}
            and all(short(data[key]) for key in ('id','name','employeeId','dateSignedOn'))
            and all(isinstance(data[key],dict) and set(data[key])=={'id'} and short(data[key]['id'])
                    for key in ('disabilityForm','disabilityStatus')))
    if resource=='resumeattachments':
        if (not {'attachments'}<=set(data)<={'attachments','id'} or 'id' in data and not short(data['id'])
                or not isinstance(data['attachments'],list) or len(data['attachments'])>20):return False
        return all(isinstance(item,dict) and set(item)=={'id','descriptor','fileName','fileLength','contentType','file'}
            and all(short(item[key]) for key in ('id','descriptor','fileName','file'))
            and type(item['fileLength']) is int and 0<=item['fileLength']<=32*1024*1024
            and reference(item['contentType']) for item in data['attachments'])
    return False


def read_request(request):
    if request.method in {'GET','HEAD','OPTIONS'}:return True
    if request.method not in {'POST','PUT'}:return False
    try:
        raw=request.post_data_buffer or b''
        if len(raw)>256000:return False
        data=json.loads(raw)
        parsed=urlsplit(request.url)
        if re.fullmatch(r'[a-z0-9-]+\.wd\d{1,3}\.myworkdayjobs\.com',parsed.hostname or ''):
            headers={key.casefold():value for key,value in request.headers.items()}
            definition=re.fullmatch(r'/wday/(?:calypso/)?cxs/(?:jobapplication/)?[a-z0-9-]+/jobapplication/[A-Za-z0-9._~-]+/'
                r'(?:name|address|emailaddress|phonenumber|source|previousworker|workexperiences|educations|webaddresses|resumeattachments|selfidentify|termsandconditions|uspersonalinfo|socials|skills)',parsed.path)
            if (definition and headers.get('form-definition')=='true' and not parsed.query
                    and workday_definition_context(parsed.path.rsplit('/',1)[-1],data)):return True
            if request.method=='POST' and re.fullmatch(r'/wday/calypso/cxs/common/[a-z0-9-]+/questionnaire/[A-Za-z0-9._~-]+/definition',parsed.path) and data=={}:
                return True
            if request.method=='POST' and re.fullmatch(r'/wday/cxs/[a-z0-9-]+/[A-Za-z0-9_-]+/jobs',parsed.path) and isinstance(data,dict):
                return (set(data)=={'appliedFacets','limit','offset','searchText'} and data['appliedFacets']=={}
                    and type(data['limit']) is int and 1<=data['limit']<=100 and type(data['offset']) is int and 0<=data['offset']<=10000
                    and isinstance(data['searchText'],str) and len(data['searchText'])<=500)
        if request.method!='POST':return False
        items=data if isinstance(data,list) else [data]
        return bool(items) and len(items)<=20 and all(isinstance(item,dict)
            and isinstance(item.get('query'),str) and re.match(r'^\s*query\b',item['query'])
            and not re.search(r'\b(?:mutation|subscription)\b',item['query']) for item in items)
    except (ValueError,UnicodeError,TypeError):return False


def looks_final(request):
    parsed=urlsplit(request.url)
    if re.search(r'/(?:submit|finalize|send-application|send_introduction|quick-apply)(?:/|$)',parsed.path,re.I):return True
    try:
        data=json.loads(request.post_data_buffer or b'')
        items=data if isinstance(data,list) else [data]
        return any(isinstance(item,dict) and re.search(r'submit|finalize|sendIntroduction|sendApplication|quickApply',
            str(item.get('operationName','')),re.I) for item in items)
    except (ValueError,TypeError,UnicodeError):return False


def unrelated_write(request):
    # The UI executor owns job applications, not account creation, global
    # platform-profile edits, withdrawal or deletion operations.
    if re.search(r'/(?:login|logout|register|signup|sign_in|sign_out|profile|userprofile|delete|withdraw)(?:/|$)',urlsplit(request.url).path,re.I):return True
    try:
        data=json.loads(request.post_data_buffer or b'')
        items=data if isinstance(data,list) else [data]
        return any(isinstance(item,dict) and re.search(r'updateProfile|editProfile|createAccount|deleteAccount|withdrawApplication|deleteDocument',str(item.get('operationName','')),re.I) for item in items)
    except (ValueError,TypeError,UnicodeError):return False


def validate_browser_package(store,job,package):
    metadata=package.get('browser_agent')
    if not metadata:return
    from .icims import posting_url
    if (not isinstance(metadata,dict) or metadata.get('version')!=1
            or not re.fullmatch(r'[a-f0-9]{64}',str(metadata.get('profile_binding','')))
            or not isinstance(metadata.get('last_url'),str)
            or origin(metadata['last_url'])!=origin(posting_url(job['url']))
            or type(metadata.get('complete')) is not bool):
        raise Blocked('browser_package_invalid')
    completed=metadata.get('completed_steps')
    if not isinstance(completed,list) or len(completed)>30:raise Blocked('browser_package_invalid')
    recorded=[]
    for index,step in enumerate(completed):
        if (not isinstance(step,dict) or not isinstance(step.get('answers'),list)
                or not isinstance(step.get('fields'),list)
                or index>=len(package.get('steps',[]))
                or package['steps'][index].get('fields')!=step['fields']):
            raise Blocked('browser_package_invalid')
        recorded.extend(step['answers'])
    if recorded!=package['answers'][:len(recorded)]:raise Blocked('browser_package_invalid')
    uploads=metadata.get('uploaded_documents')
    if not isinstance(uploads,list) or len(uploads)>30:raise Blocked('browser_package_invalid')
    for upload in uploads:
        if (not isinstance(upload,dict) or upload.get('profile_binding')!=metadata['profile_binding']
                or not any(d['hash']==upload.get('hash') and d['kind']==upload.get('kind') for d in package.get('documents',[]))):
            raise Blocked('upload_verification_failed')
        operation=store.db.execute('SELECT job_id,operation,target,state FROM draft_operations WHERE id=?',
                                  (upload.get('operation_id'),)).fetchone()
        try:target=json.loads(operation['target']) if operation else {}
        except (ValueError,TypeError):target={}
        source=(operation and (operation['operation']=='ui:upload' and target.get('document_hash')==upload.get('hash')
                or operation['operation']=='ui:next' and upload.get('hash') in target.get('document_hashes',[])))
        if (not source or operation['job_id']!=job['id']
                or operation['state']!='acknowledged'
                or target.get('profile_binding')!=metadata['profile_binding']):
            raise Blocked('upload_verification_failed')


class UIOperation:
    def __init__(self,agent,action,package,*,final=False,document=None):
        from .answers import validate_package
        validate_package(agent.store,agent.job,package)
        self.agent,self.store,self.job=agent,agent.store,agent.job
        self.action,self.package,self.final,self.document=action,package,final,document
        self.key=('ui',uuid.uuid4().hex)
        self.record=None;self.requests={};self.responses={};self.denied=False;self.upload_sent=False
        self.handles=set();self.failure=False;self.accepted=False
        self.transmitted_documents=set()
        self.last_activity=time.monotonic()
        self.boundary=ApprovalBoundary(self.store,self.job,package,
            application_id=agent.aid if final else None,
            settings=('adaptive_browser',),require_live=not agent.preparing,
            reserve_retry=False)

    def allow(self,request):
        self.boundary.check(application_id=self.agent.aid if self.final else None)
        if unrelated_write(request):
            self.denied=True
            return False
        if self.action['kind']=='search':
            self.denied=True
            return False
        if request.method not in {'POST','PUT','PATCH'}:return False
        if self.agent.preparing:return False
        if not getattr(self.agent,'owner_verified',True):
            raise Blocked('browser_identity_unverified','Sign in as the confirmed applicant in the dedicated employer browser before saving this application')
        if not self.final and not self.store.settings()['remote_drafts']:
            self.denied=True
            return False
        if not self.final and looks_final(request):
            self.denied=True
            return False
        raw=request.post_data_buffer or b''
        if len(raw)>32*1024*1024:return False
        carried={checksum for checksum,document in self.agent.staged_documents.items()
                 if raw.count(self.agent.document_bytes(document))==1}
        if self.final and self.agent.resume['hash'] not in self.agent.uploads and self.agent.resume['hash'] not in carried:
            self.denied=True
            return False
        self.transmitted_documents.update(carried)
        if self.document:
            data=self.agent.document_bytes(self.document)
            encoded=base64.b64encode(data)
            carries_file=(raw==data or raw.count(data)==1 or encoded in raw)
            linked=bool(self.upload_sent and self.handles and any(handle.encode() in raw for handle in self.handles))
            if not carries_file and not linked:
                self.denied=True
                return False
            self.upload_sent=self.upload_sent or carries_file
        request_key=request_identity(request)
        if request_key in self.requests:
            self.denied=True
            return False
        if self.record is None:
            self.record=self.agent.journal.intent([self.key],'ui:'+self.action['kind'],
                {'control':self.action['id'],'host':self.job['host'],'profile_binding':self.agent.binding,
                 'document_hash':self.document['hash'] if self.document else None,
                 'document_hashes':sorted(carried)},self.package)
        self.last_activity=time.monotonic()
        self.requests[request_key]={'request':request,'commitment':self.agent.journal.commitment({
            'url':request.url,'method':request.method,'body':hashlib.sha256(raw).hexdigest()})}
        with self.store.transaction():
            self.store.db.execute('UPDATE draft_operations SET request_commitment=?,updated=? WHERE id=?',
                (self.agent.journal.commitment(sorted(item['commitment'] for item in self.requests.values())),now(),self.record))
        return True

    def response(self,response):
        key=request_identity(response.request)
        if key not in self.requests:return
        data=None
        with contextlib.suppress(Exception):data=response.json()
        self.capture(response.request,response.status,data)

    def capture(self,request,status,data,*,headers=None,body=b''):
        key=request_identity(request)
        if key not in self.requests:return
        redirect=None
        if status in {301,302,303,307,308}:
            location=(headers or {}).get('location')
            if isinstance(location,str):redirect=urljoin(request.url,location)
        ok=200<=status<300 or bool(redirect and origin(redirect)==self.agent.allowed_origin)
        ok=ok and response_ok(data)
        self.responses[key]={'ok':ok,'digest':digest(data) if data is not None else hashlib.sha256(body).hexdigest() if body else digest(status)}
        self.failure=self.failure or not ok
        if not ok:
            # Keep bounded, redacted employer validation feedback so a failed
            # save can be diagnosed without retaining payloads or credentials.
            messages=[]
            def collect(value,depth=0):
                if depth>6 or len(messages)>=6:return
                if isinstance(value,dict):
                    for name,item in value.items():
                        if name.casefold() in {'message','errormessage','error','errors','validationerrors','validation_errors'}:
                            if isinstance(item,str):messages.append(item)
                            else:collect(item,depth+1)
                        elif isinstance(item,(dict,list)):collect(item,depth+1)
                elif isinstance(value,list):
                    for item in value[:10]:
                        if isinstance(item,str):messages.append(item)
                        else:collect(item,depth+1)
            collect(data)
            cleaned=[]
            for message in messages[:6]:
                message=re.sub(r'https?://\S+|[^\s@]+@[^\s@]+','[private]',message)
                message=re.sub(r'\b[A-Za-z0-9_=-]{32,}\b|\d{4,}','[value]',message)
                cleaned.append(message[:300])
            self.failure_detail='; '.join(cleaned)
            self.store.event('browser_ui_save_failed',self.job['id'],{'operation_id':self.record,
                'status':status,'resource':urlsplit(request.url).path.rsplit('/',1)[-1],
                'messages':cleaned})
        self.last_activity=time.monotonic()
        if self.final and ok:self.accepted=self.accepted or explicit_acceptance(data)
        if self.final and ok and redirect and re.search(r'(?:thanks|thank.you|confirmation|submitted|complete)',urlsplit(redirect).path,re.I):
            self.accepted=True
        if self.final and ok and body and 'text/html' in (headers or {}).get('content-type',''):
            import html
            text=html.unescape(re.sub(r'<[^>]+>',' ',body.decode('utf-8',errors='replace')))
            if self.agent._receipt(text,self.agent.receipt_baseline):self.accepted=True
        if self.final and ok and looks_final(request) and isinstance(data,dict):
            remote=data.get('id')
            if isinstance(remote,str) and re.fullmatch(r'[A-Za-z0-9._~-]{1,300}',remote):
                path=urlsplit(request.url).path
                # Workday's own finalizer returns only its application ID.
                if path.rstrip('/').endswith('/'+remote+'/finalize'):self.accepted=True
        if self.document and ok and isinstance(data,dict):
            for name in ('id','fileId','file_id','documentId','attachmentId'):
                handle=data.get(name)
                if isinstance(handle,str) and re.fullmatch(r'[A-Za-z0-9._~-]{1,300}',handle):self.handles.add(handle)

    @property
    def complete(self):
        return not self.failure and len(self.requests)==len(self.responses)

    def acknowledge(self,observation):
        if self.denied or not self.complete:
            raise Blocked('browser_draft_uncertain','The employer did not acknowledge every UI save')
        if self.record:
            with self.store.transaction():
                changed=self.store.db.execute("UPDATE draft_operations SET state='acknowledged',updated=?,acknowledgement=? WHERE id=? AND state='intent'",
                    (now(),self.agent.journal.commitment(observation),self.record)).rowcount
                if changed!=1:raise Blocked('browser_draft_uncertain')

    def uncertain(self):
        if self.record:
            self.store.db.execute("UPDATE draft_operations SET state='unknown',updated=? WHERE id=? AND state='intent'",(now(),self.record))
