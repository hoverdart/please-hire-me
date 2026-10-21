"""NVIDIA's reviewed native browser transport and schema bindings.

Requests use the signed-in browser and the official client's native endpoints.
Unknown definitions, prior history and response shapes hold the application.
Installing this module does not supply employer acceptance evidence.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from .util import Blocked, digest, safe_document
from .workday_native import (NativeOperation, SESSION_HEADER, SOURCE_SIGNATURE, checked_object, contains_saved,
    encoded, identifier, native_url, questionnaire_payload, QUESTION_TYPES)
from .workday_profiles import NVIDIA, NVIDIA_SITE


DRIVER_VERSION=1
NATIVE_TIMEOUT_MS=25000
US='bc33aa3152ec42d4995f4791a106ed09'
ORIGIN='https://'+NVIDIA
SOURCE_READS={
    'name':'namedefinition?country='+US,
    'address':'addressdefinition?country='+US,
}


class NativeBrowserTransport:
    """No APIRequestContext or cookie export; every request crosses Browser.route."""
    def __init__(self,controller):
        self.controller=controller
        self.browser,self.store=controller.browser,controller.store
        self.page=self.browser.page

    def request(self,operation):
        parsed=urlsplit(operation.url)
        if (parsed.scheme!='https' or parsed.hostname!=NVIDIA or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.fragment or '..' in parsed.path.split('/')
                or operation.method not in {'GET','POST','PUT'} or not isinstance(operation.body,bytes)):
            raise Blocked('workday_unapproved_operation')
        self.store.checkpoint()
        if operation.method=='GET':
            from .workday_profiles import PROFILE_READ
            driver=getattr(self.controller,'_native_driver',None)
            if operation.url!=PROFILE_READ and (not driver or driver.allow_read(operation.url) is not True):
                raise Blocked('workday_unapproved_operation','The native read is outside this owned application')
        if operation.method!='GET' and not getattr(self.browser,'workday_grant',None):
            scope=getattr(self.browser,'workday_read_scope',None)
            if not scope or not scope.allow(operation.url,operation.method,operation.body,operation.headers):
                raise Blocked('workday_unapproved_operation')
        try:
            with self.page.expect_response(lambda response:response.url==operation.url
                    and response.request.method==operation.method
                    and (response.request.post_data_buffer or b'')==operation.body,timeout=30000) as pending:
                self.page.evaluate("""async operation => {
                    if (window.workday?.tenant !== 'nvidia'
                        || window.workday?.siteId !== 'NVIDIAExternalCareerSite' || !window.workday.token)
                        throw new Error('Native bootstrap required');
                    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),operation.timeout);
                    try {
                        const response=await fetch(operation.url,{method:operation.method,credentials:'same-origin',
                            cache:'no-store',redirect:'error',signal:controller.signal,
                            headers:{...operation.headers,'Accept-Language':document.documentElement.lang,
                                'X-CALYPSO-CSRF-TOKEN':window.workday.token},
                            ...(operation.method==='GET'||!operation.body?{}:{body:operation.body})});
                        await response.text();
                    } finally {clearTimeout(timer)}
                }""",{'url':operation.url,'method':operation.method,'headers':operation.headers,
                       'body':operation.body.decode('utf-8'),'timeout':NATIVE_TIMEOUT_MS})
            return pending.value
        except Blocked:raise
        except Exception:
            raise Blocked('remote_draft_uncertain' if operation.method!='GET' else 'workday_native_read_failed',
                'The exact native employer response could not be verified') from None

    def get(self,url):
        return self.request(NativeOperation(url,'GET',b'',{'accept':'application/json'},'read'))

    def json(self,url):
        response=self.get(url)
        if response.status!=200:raise Blocked('workday_native_read_failed')
        try:data=response.json()
        except (ValueError,TypeError):raise Blocked('workday_native_read_failed') from None
        if not isinstance(data,(dict,list)) or isinstance(data,dict) and (data.get('errors') or data.get('errorCode')):
            raise Blocked('workday_native_read_failed')
        return data

    def upload(self,document,*,scan=False):
        path=safe_document(self.store.root/'documents'/document['filename'],self.store.root/'documents')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=document['hash']:raise Blocked('document_changed')
        url=native_url('common','scanfile' if scan else 'attachments')
        if not getattr(self.browser,'workday_grant',None):raise Blocked('workday_unapproved_operation')
        try:
            with self.page.expect_response(lambda response:response.url==url and response.request.method=='POST',timeout=30000) as pending:
                self.page.evaluate("""async file => {
                    if (window.workday?.tenant!=='nvidia'||window.workday?.siteId!=='NVIDIAExternalCareerSite'||!window.workday.token)
                        throw new Error('Native bootstrap required');
                    const bytes=Uint8Array.from(atob(file.data),character=>character.charCodeAt(0));
                    const body=new FormData();body.append('body',new Blob([bytes],{type:'application/pdf'}),file.name);
                    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),file.timeout);
                    try {
                        const response=await fetch(file.url,{method:'POST',body,credentials:'same-origin',redirect:'error',signal:controller.signal,
                            headers:{'X-CALYPSO-CSRF-TOKEN':window.workday.token}});
                        await response.text();
                    } finally {clearTimeout(timer)}
                }""",{'url':url,'data':base64.b64encode(data).decode(),'name':document['filename'],'timeout':NATIVE_TIMEOUT_MS})
            return pending.value
        except Exception:
            raise Blocked('upload_verification_failed','The native upload needs owner-bound reconciliation') from None


def definition_properties(data,name):
    """Resolve only the official JSON-schema local definition/reference form."""
    value=checked_object(data)
    definitions=value.get('definitions')
    if not isinstance(definitions,dict) or not isinstance(definitions.get(name),dict):
        raise Blocked('workday_schema_unreviewed','The native '+name+' definition changed')
    if value.get('allOf')!=[{'$ref':'#/definitions/'+name}]:
        raise Blocked('workday_schema_unreviewed','The native definition root changed')
    root=definitions[name]
    required=root.get('required',[])
    properties=root.get('properties')
    if (not isinstance(properties,dict) or not isinstance(required,list)
            or any(not isinstance(key,str) or key not in properties for key in required)):
        raise Blocked('workday_schema_unreviewed')
    return properties,set(required)


def native_date(value,precision):
    """The native manual widget sends only its metadata precision, without days."""
    if precision not in {'YYYY','YYYY-MM','YYYY-MM-DD'} or not isinstance(value,str):
        raise Blocked('workday_schema_unreviewed','The native date precision changed')
    lengths={'YYYY':4,'YYYY-MM':7,'YYYY-MM-DD':10}
    patterns={'YYYY':r'(?:19|20)\d{2}','YYYY-MM':r'(?:19|20)\d{2}-(?:0[1-9]|1[0-2])',
              'YYYY-MM-DD':r'(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])'}
    actual=next((kind for kind,pattern in patterns.items() if re.fullmatch(pattern,value)),None)
    if actual is None or lengths[actual]<lengths[precision]:raise Blocked('invalid_date_answer')
    if actual=='YYYY-MM-DD':
        try:datetime.strptime(value,'%Y-%m-%d')
        except ValueError:raise Blocked('invalid_date_answer') from None
    return value[:lengths[precision]]


def native_history_budget(store,job,history):
    """Fresh native history supplements local budgets; missing dates never mean old."""
    settings=store.settings()
    rows=[]
    for result in history:
        data=checked_object(result)
        values=data.get('data');total=data.get('total')
        if (type(total) is not int or total<0 or not isinstance(values,list) or total!=len(values)
                or any(not isinstance(row,dict) for row in values)):
            raise Blocked('workday_history_review_required','Read the complete employer history before another application')
        rows.extend(values)
    unique={}
    for row in rows:
        remote=row.get('applicationId')
        if not isinstance(remote,str) or not remote:raise Blocked('workday_history_review_required')
        previous=unique.get(remote)
        if previous is not None and previous!=row:raise Blocked('workday_history_review_required')
        unique[remote]=row
    if any(not row.get('dateApplied') and row.get('draft') is not True for row in unique.values()):
        raise Blocked('workday_history_review_required','A non-draft employer record has no verified application date')
    completed=[row for row in unique.values() if row.get('dateApplied')]
    if len(completed)>=settings['max_per_company']:raise Blocked('company_limit')
    ck=store.company(job['company'],settings)
    local=[row for row in store.application_history(settings,('submitting','unknown','confirmed','awaiting_verification'))
           if ck in row['company_keys']]
    manual=[row for row in store.db.execute("SELECT d.job_id,j.company FROM job_decisions d JOIN jobs j ON j.id=d.job_id "
        "WHERE d.decision='manually_applied'") if store.company(row['company'],settings)==ck]
    known={row['remote_id'] for row in store.db.execute('SELECT remote_id FROM workday_drafts WHERE job_id IN '
        '(SELECT job_id FROM application_records WHERE state IN (\'submitting\',\'unknown\',\'confirmed\',\'awaiting_verification\')) '
        'AND tenant=?',(NVIDIA,))}
    extra=[row for row in completed if row['applicationId'] not in known]
    if len(local)+len(manual)+len(extra)>=settings['max_per_company']:
        raise Blocked('workday_history_review_required',
            'The combined local and employer history reaches the company budget; reconcile overlap before continuing')
    cutoff=datetime.now(timezone.utc)-timedelta(days=settings['company_cooldown_days'])
    for row in completed:
        value=row['dateApplied']
        if not isinstance(value,str):raise Blocked('workday_history_review_required')
        try:
            when=datetime.strptime(value,'%B %d, %Y').replace(tzinfo=timezone.utc)
        except ValueError:
            try:when=datetime.fromisoformat(value.replace('Z','+00:00'))
            except ValueError:raise Blocked('workday_history_review_required') from None
            if when.tzinfo is None:raise Blocked('workday_history_review_required')
        # Native display dates have day precision. Their latest possible instant
        # is conservative at the cooldown edge rather than inventing a time.
        if when+timedelta(days=1)>cutoff:raise Blocked('company_cooldown')
    store._check_budget(job,settings)


@dataclass
class RecordPlan:
    resource: str
    fields: list
    constants: dict
    schema_hash: str
    entry_id: str = ''
    context: dict | None = None


def _root(data):
    value=checked_object(data)
    refs=value.get('allOf')
    if (not isinstance(refs,list) or len(refs)!=1 or not isinstance(refs[0],dict)
            or set(refs[0])!={'$ref'} or not isinstance(refs[0]['$ref'],str)
            or not re.fullmatch(r'#/definitions/[A-Za-z][A-Za-z0-9_]*',refs[0]['$ref'])):
        raise Blocked('workday_schema_unreviewed','The native form definition root changed')
    return definition_properties(value,refs[0]['$ref'].rsplit('/',1)[1])


def _plain(value):
    return html.unescape(re.sub('<[^>]*>',' ',value or '')).strip()


class NvidiaNativeDriver:
    """The supported US manual native flow, with exact approved request plans."""
    proof_scope='live'
    # A driver/schema contract change invalidates historical commissioning,
    # independently of the public client's read-protocol signature.
    protocol_signature=digest({'driver':'nvidia-us-manual','version':DRIVER_VERSION,
        'public_client':SOURCE_SIGNATURE,
        'records':['name','address','phonenumber','source','previousworker','workexperiences','educations',
            'resumeattachments','questionnaire:primary','questionnaire:secondary','uspersonalinfo','termsandconditions'],
        'native_readback_version':1,'owned_resume_version':1,'fresh_detail_config_receipt':1})

    def __init__(self,controller):
        self.controller,self.browser,self.store,self.job=controller,controller.browser,controller.store,controller.job
        self.transport=NativeBrowserTransport(controller)
        self.provider=getattr(self.browser,'provider',None)
        self.remote=self.session_id=None
        self.read_scope=None
        self.info=None
        self.questionnaires={}
        self.plans=[]
        self.bindings={}
        self.saved=[]
        self.attachment=None
        self.approved_package=None
        self.current_snapshot=None
        self.current_page=None
        self.resume_proof=None
        self.resuming=False
        self.read_only=False
        self.option_reads=set()

    def allow_read(self,url):
        posting=identifier(urlsplit(self.job['url']).path.rstrip('/').split('/')[-1])
        path=urlsplit(self.job['url']).path.split('/'+NVIDIA_SITE+'/job/',1)[1]
        allowed={ORIGIN+'/wday/cxs/nvidia/'+NVIDIA_SITE+'/job/'+path,
            native_url('jobapplication','job/'+posting+'/canapplycontinue'),
            native_url('jobapplication','jobpostings/'+posting+'/applyflowpages?applyDetail=applyManually'),
            native_url('candidatehome','applications?type=active&limit=4',site=True),
            native_url('candidatehome','applications?type=inactive&limit=4',site=True),
            native_url('candidatehome','applications?type=active&jobPostingAnchorId=Job_Posting_Anchor_ID='+posting+'&draftsOnly=true',site=True)}
        allowed.update(native_url('jobapplication',value) for value in SOURCE_READS.values())
        allowed.update(native_url('common','questionnaire/'+value) for value in self.questionnaires.values())
        if self.remote:
            allowed.update({native_url('candidatehome','application/'+self.remote+'/config',site=True),
                native_url('jobapplication','package/'+self.remote),
                native_url('jobapplication','jobapplication/'+self.remote+'/names'),
                native_url('jobapplication','jobapplication/'+self.remote+'/resumeattachments')})
            allowed.update(native_url('jobapplication','package/'+self.remote+'/'+resource)
                for resource in ('addresses','phonenumbers','workexperiences','educations','previousworker','webaddresses'))
            allowed.update(native_url('jobapplication','jobapplication/'+self.remote+'/questionnaireanswers?type='+kind)
                for kind in self.questionnaires)
        return url in allowed or url in self.option_reads

    def read_profile(self):
        from .workday_sessions import read_profile
        if urlsplit(self.browser.page.url).hostname!=NVIDIA:
            self.browser.current_host=NVIDIA
            self.browser.page.goto(self.job['url'],wait_until='domcontentloaded')
            self.browser.page.wait_for_function("window.workday?.tenant==='nvidia' && !!window.workday.token",timeout=30000)
        return read_profile(self.browser.page)

    def _json(self,url,headers=None):
        if headers is None:return self.transport.json(url)
        response=self.transport.request(NativeOperation(url,'GET',b'',headers,'read'))
        if response.status!=200:raise Blocked('workday_native_read_failed')
        try:return response.json()
        except (ValueError,TypeError):raise Blocked('workday_native_read_failed') from None

    def posting_identifier(self):
        posting=identifier(urlsplit(self.job['url']).path.rstrip('/').split('/')[-1])
        if self.info is None:
            path=urlsplit(self.job['url']).path
            marker='/'+NVIDIA_SITE+'/job/'
            if marker not in path:raise Blocked('posting_changed_review')
            detail_url=ORIGIN+'/wday/cxs/nvidia/'+NVIDIA_SITE+'/job/'+path.split(marker,1)[1]
            detail=self.transport.json(detail_url)
            info=detail.get('jobPostingInfo') if isinstance(detail,dict) else None
            if (not isinstance(info,dict) or info.get('jobPostingId')!=posting
                    or info.get('title')!=self.job['title'] or not isinstance(info.get('jobReqId'),str)
                    or not posting.endswith('_'+info['jobReqId']) and not re.fullmatch(
                        re.escape(posting.rsplit('_',1)[0]+'_'+info['jobReqId'])+r'-\d+',posting)):
                raise Blocked('posting_changed_review')
            previous=info.get('jobApplication')
            if isinstance(previous,dict) and previous.get('dateCompleted'):
                raise Blocked('duplicate_or_uncertain','The employer already records a completed application for this requisition')
            continuing=isinstance(previous,dict) and previous.get('draft') is True and isinstance(previous.get('id'),str)
            if info.get('canApply') is not True and not continuing and not self.read_only:
                raise Blocked('posting_closed','The employer does not authorize a new application')
            history=[self.transport.json(native_url('candidatehome','applications?type='+kind+'&limit=4',site=True))
                     for kind in ('active','inactive')]
            if not self.read_only:native_history_budget(self.store,self.job,history)
            country=self.store.facts().get('country',{}).get('value','').strip().casefold()
            if country not in {'us','usa','united states','united states of america'}:
                raise Blocked('mapping_review','This native driver supports an explicitly confirmed US residence')
            can=self.transport.json(native_url('jobapplication','job/'+posting+'/canapplycontinue'))
            if not self.read_only and (not isinstance(can,dict) or can.get('canContinue') is not True
                    or can.get('canApply') is not True and not continuing):
                raise Blocked('duplicate_or_uncertain','The employer cannot start or continue this application')
            self.info=info
            for kind,key in (('primary','questionnaireId'),('secondary','secondaryQuestionnaireId')):
                if info.get(key):self.questionnaires[kind]=identifier(info[key])
            if info.get('supplementaryQuestionnaireId'):
                raise Blocked('workday_schema_unreviewed','This application includes an additional questionnaire that needs review')
        return posting

    def read_flow_pages(self):
        posting=self.posting_identifier()
        response=self.transport.get(native_url('jobapplication','jobpostings/'+posting+'/applyflowpages?applyDetail=applyManually'))
        from .workday_native import pages
        flow=pages(response.json())
        supported={'My Information','My Experience','Application Questions','Application Questions 2','Voluntary Disclosures','Review'}
        if any(page['section'] not in supported for page in flow):
            raise Blocked('workday_schema_unreviewed','An assessment, self-identification or other unreviewed step needs manual completion')
        from .workday_history import entries
        for page in flow:
            if page['section']=='My Experience':
                for section in page['definition'].get('sections',[]):
                    if section.get('required') and re.search('education',section.get('label',''),re.I) and not entries(self.store,'education'):
                        raise Blocked('history_confirmation_required','Confirm an education entry in Your facts before creating an employer draft')
        return response

    def create_draft(self,operation):
        drafts=self.transport.json(native_url('candidatehome','applications?type=active&jobPostingAnchorId=Job_Posting_Anchor_ID='+self.posting_identifier()+'&draftsOnly=true',site=True))
        if not isinstance(drafts,dict) or drafts.get('total')!=0 or drafts.get('data')!=[]:
            raise Blocked('remote_draft_uncertain','Read and adopt the existing employer draft; another creation is not authorized')
        return self.transport.request(operation)

    def bind_draft(self,remote,session_id,session):
        from .workday_native import NativeReadScope
        self.remote,self.session_id=identifier(remote),session_id
        self.read_scope=NativeReadScope(self.store,session,remote,session_id,questionnaires=self.questionnaires,
            resume_proof=self.resume_proof)
        self.browser.workday_read_scope=self.read_scope

    def _definition(self,resource,context=None):
        if resource in SOURCE_READS:
            result=self.transport.json(native_url('jobapplication',SOURCE_READS[resource]))
            if resource=='address':
                properties,_=_root(result)
                if 'countryRegion' in properties:
                    properties['countryRegion']['promptUrl']=native_url('common','countries/'+US+'/regions')
            return result
        old=resource in {'uspersonalinfo','termsandconditions'}
        url=native_url('jobapplication','jobapplication/'+self.remote+'/'+resource,old=old)
        headers={'accept':'application/json','content-type':'application/json',SESSION_HEADER:self.session_id,'form-definition':'true'}
        response=self.transport.request(NativeOperation(url,'PUT' if old else 'POST',encoded(context or {}),headers,'definition_read'))
        if response.status!=200:raise Blocked('workday_schema_unreviewed','The employer did not provide a native form definition')
        result=checked_object(response.json())
        if resource=='educations':
            properties,_=_root(result)
            for key,path in [('school','schools'),('degree','values/educations/degrees?country='+US)]:
                if key in properties:properties[key]['promptUrl']=native_url('jobapplication',path)
        return result

    def _options(self,metadata,resource,key,history=None):
        values=metadata.get('enum')
        prompt=metadata.get('promptUrl')
        if not values and prompt:
            url=urljoin(ORIGIN,prompt)
            parsed=urlsplit(url)
            if (parsed.hostname!=NVIDIA or parsed.scheme!='https' or parsed.username or parsed.password
                    or parsed.fragment or not parsed.path.startswith(('/wday/calypso/cxs/common/nvidia/',
                        '/wday/calypso/cxs/jobapplication/nvidia/values/','/wday/calypso/cxs/jobapplication/nvidia/schools'))):
                raise Blocked('workday_schema_unreviewed','An unreviewed native option source needs review')
            if history and key in {'degree','fieldOfStudy','school'} and 'search=' not in parsed.query:
                wanted=history.get({'degree':'degree','fieldOfStudy':'field_of_study','school':'institution'}[key])
                if wanted and key!='degree':url+=('&' if parsed.query else '?')+urlencode({'search':wanted})
            known={('address','countryRegion'):native_url('common','countries/'+US+'/regions'),
                ('educations','degree'):native_url('jobapplication','values/educations/degrees?country='+US)}
            base={('educations','school'):native_url('jobapplication','schools'),
                ('educations','fieldOfStudy'):native_url('jobapplication','values/educations/fieldsOfStudy')}.get((resource,key))
            if url!=known.get((resource,key)) and not (base and urlsplit(url).path==urlsplit(base).path
                    and set(parse_qs(urlsplit(url).query))<={'search'}):
                raise Blocked('workday_schema_unreviewed','The native '+key+' option source needs review')
            self.option_reads.add(url)
            data=self.transport.json(url)
            values=data.get('data') if isinstance(data,dict) else data
            if isinstance(data,dict) and type(data.get('total')) is int and data['total']>len(values or []):
                raise Blocked('mapping_review','The native dropdown choices are incomplete')
        if not values:return []
        if not isinstance(values,list) or len(values)>500:raise Blocked('workday_schema_unreviewed')
        result=[]
        for value in values:
            if isinstance(value,str):result.append({'id':value,'descriptor':value})
            elif (isinstance(value,dict) and isinstance(value.get('id'),str)
                    and isinstance(value.get('descriptor'),str)):
                result.append({'id':identifier(value['id']),'descriptor':value['descriptor']})
            else:raise Blocked('workday_schema_unreviewed')
        if len({row['descriptor'] for row in result})!=len(result) or len({row['id'] for row in result})!=len(result):
            raise Blocked('mapping_review','The employer choices are ambiguous')
        return result

    def _record(self,resource,definition,allowed,*,entry=None,constants=None,context=None,prefix=()):
        properties,required=_root(definition)
        schema_hash=digest(definition)
        fields=[]
        for key,metadata in properties.items():
            if not isinstance(metadata,dict):raise Blocked('workday_schema_unreviewed')
            if key in {'id','href','descriptor','country','fullyComposedName'} or metadata.get('readOnly') is True:
                continue
            if metadata.get('hidden') is True:continue
            if key not in allowed:
                if key in required:raise Blocked('workday_schema_unreviewed','A new required native field needs review: '+key)
                continue
            kind=metadata.get('type')
            options=self._options(metadata,resource,key,entry['values'] if entry else None)
            multiple=kind=='array'
            field_type=('checkbox-group' if multiple else 'select') if options else ('radio' if kind=='boolean' else
                'number' if kind=='integer' else 'text' if kind=='string' else None)
            if field_type is None:
                if key in required:raise Blocked('unsupported_widget','The native '+key+' control needs review')
                continue
            label=_plain(metadata.get('label') or allowed[key])
            precision=metadata.get('precision')
            if precision:
                if precision not in {'YYYY','YYYY-MM','YYYY-MM-DD'}:raise Blocked('workday_schema_unreviewed')
                if precision=='YYYY':label+=' (year)'
                elif precision=='YYYY-MM':label+=' (month and year)'
            identity=digest([resource,entry['id'] if entry else '',key,schema_hash])
            field={'index':len(self.bindings),'label':label,'type':field_type,'required':key in required,
                'options':[row['descriptor'] for row in options] if options else ['Yes','No'] if kind=='boolean' else [],
                'maxlength':metadata.get('maxLength',-1),'value':'','multiple':multiple,
                'native_id':identity,'native_resource':resource,'native_key':key,'native_schema_hash':schema_hash}
            if kind=='integer':
                field.update({name:metadata.get(source) for name,source in [('min','minimum'),('max','maximum'),('step','multipleOf')]})
            if entry:
                history_key=allowed[key]
                field.update(section=entry['kind'],history_entry_id=entry['id'],history_key=history_key)
            self.bindings[identity]={'field':field,'path':prefix+(key,),'kind':kind,'options':options,'precision':precision}
            fields.append(field)
        plan=RecordPlan(resource,fields,constants or {},schema_hash,entry['id'] if entry else '',context)
        self.plans.append(plan)
        return fields

    def _question_fields(self,kind):
        questionnaire=self._json(native_url('common','questionnaire/'+self.questionnaires[kind]),{SESSION_HEADER:self.session_id})
        questions=checked_object(questionnaire).get('questions')
        if not isinstance(questions,list):raise Blocked('workday_schema_unreviewed')
        fields=[]
        for question in questions:
            if not isinstance(question,dict) or set(question)-{'id','body','order','possibleAnswers','required','type'}:
                raise Blocked('workday_schema_unreviewed','A branching or new native questionnaire field needs review')
            qid=identifier(question.get('id'))
            native_type=question.get('type',{})
            qtype=QUESTION_TYPES.get(native_type.get('id')) if isinstance(native_type,dict) else None
            if qtype not in {'text','single','multiple','date','number'}:raise Blocked('unsupported_widget')
            options=question.get('possibleAnswers',[])
            if (not isinstance(options,list) or any(not isinstance(row,dict) or not isinstance(row.get('id'),str)
                    or not isinstance(row.get('answerText'),str) for row in options)):
                raise Blocked('workday_schema_unreviewed')
            field={'index':len(self.bindings),'label':_plain(question.get('body')),'required':question.get('required') is True,
                'type':{'text':'textarea','single':'select','multiple':'checkbox-group','date':'text','number':'number'}[qtype],
                'options':[row['answerText'] for row in options],'maxlength':-1,'value':'',
                'native_id':kind+':'+qid,'native_resource':'questionnaire:'+kind,'native_key':qid,'native_schema_hash':digest(questionnaire)}
            self.bindings[field['native_id']]={'field':field,'kind':qtype,'options':options,'questionnaire':questionnaire}
            fields.append(field)
        self.plans.append(RecordPlan('questionnaire:'+kind,fields,{},digest(questionnaire)))
        return fields

    def enter(self,page):
        self.current_page=page
        self.plans=[]
        fields=[]
        section=page['section']
        if section=='My Information':
            fields+=self._record('name',self._definition('name'),{'firstName':'First Name','lastName':'Last Name'},
                constants={'legalName':{'country':{'id':US}}},prefix=('legalName',))
            fields+=self._record('address',self._definition('address'),{'addressLine1':'Address Line 1','city':'City','countryRegion':'State','postalCode':'Postal Code'},constants={'country':{'id':US}})
            for resource,allowed in [('phonenumber',{'phoneNumber':'Phone Number','countryPhoneCode':'Country Phone Code','phoneType':'Phone Device Type','extension':'Extension'}),
                ('source',{'source':'How did you hear about us?','referredBy':'Referred by'}),
                ('previousworker',{'candidateIsPreviousWorker':'Have you previously worked for NVIDIA?'})]:
                fields+=self._record(resource,self._definition(resource),allowed)
        elif section=='My Experience':
            from .workday_history import entries
            for kind,resource,allowed in [('employment','workexperiences',{'companyName':'organization','jobTitle':'title','location':'location',
                    'startDate':'start','endDate':'end','currentlyWorkHere':'current','roleDescription':'description'}),
                ('education','educations',{'schoolName':'institution','degree':'degree','fieldOfStudy':'field_of_study',
                    'gradeAverage':'gpa','firstYearAttended':'start','lastYearAttended':'end'})]:
                for entry in entries(self.store,kind):
                    mapping=dict(allowed)
                    if kind=='education' and 'expected_end' in entry['values']:mapping['lastYearAttended']='expected_end'
                    context={'currentlyWorkHere':entry['values'].get('current') is True} if kind=='employment' else {}
                    fields+=self._record(resource,self._definition(resource,context),mapping,entry=entry,context=context)
            fields.append(self.resume_field())
        elif section in {'Application Questions','Application Questions 2'}:
            kind='primary' if section=='Application Questions' else 'secondary'
            if kind not in self.questionnaires:raise Blocked('workday_schema_unreviewed')
            fields+=self._question_fields(kind)
        elif section=='Voluntary Disclosures':
            package=checked_object(self.transport.json(native_url('jobapplication','package/'+self.remote)))
            if package.get('termsAndConditionsUpdated') or not package.get('termsAndConditionsLockedMoment'):
                if not self.read_only:self._control('termslockmoment',page)
            fields+=self._record('uspersonalinfo',self._definition('uspersonalinfo'),{'gender':'Gender','ethnicity':'Race / ethnicity',
                'ethnicityMulti':'Race / ethnicity','hispanicOrLatino':'Hispanic or Latino','veteranStatus':'Veteran status'})
            fields+=self._record('termsandconditions',self._definition('termsandconditions'),{'acceptTermsAndAgreements':'Terms and Conditions'})
        elif section!='Review':raise Blocked('workday_schema_unreviewed')
        self.current_snapshot={'section':'Application Questions' if section.startswith('Application Questions') else section,
            'fields':fields,'signature':digest(fields),'version':2}

    def snapshot(self,page):return self.current_snapshot

    @staticmethod
    def resume_field():
        return {'index':999,'label':'Resume / CV','type':'file','required':True,'options':[],
                'native_id':'approved-resume','native_resource':'resumeattachments'}

    def selected_documents(self):
        row=self.store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
        if not row:raise Blocked('resume_not_in_package')
        return [{**dict(row),'field':self.resume_field()}]

    def documents(self,snapshot):
        return self.selected_documents() if snapshot['section']=='My Experience' else []

    def _value(self,binding,value):
        options=binding['options']
        if binding['kind']=='boolean':
            if value not in {'Yes','No'}:raise Blocked('mapping_review')
            return value=='Yes'
        if options:
            from .answers import selections
            chosen=selections(value,[row.get('descriptor',row.get('answerText')) for row in options])
            if not chosen:raise Blocked('mapping_review')
            values=[{'id':row['id']} for row in options if row.get('descriptor',row.get('answerText')) in chosen]
            return values if binding['kind'] in {'array','multiple'} else values[0]
        if binding.get('precision'):return native_date(value,binding['precision'])
        if binding['kind'] in {'integer','number'}:
            if not re.fullmatch(r'\d+',value):raise Blocked('invalid_numeric_answer')
            return int(value)
        return value

    def _payload(self,plan,answers):
        body=json.loads(json.dumps(plan.constants));used=0
        for answer in answers:
            field=answer['field']
            if field not in plan.fields:continue
            binding=self.bindings[field['native_id']]
            target=body
            for key in binding['path'][:-1]:target=target.setdefault(key,{})
            target[binding['path'][-1]]=self._value(binding,answer['value']);used+=1
        return body,used

    def writes(self,page,snapshot,answers,documents):
        from .workday_flow import StepWrite
        from .workday_native import record_operation
        result=[]
        for plan in self.plans:
            if plan.resource.startswith('questionnaire:'):
                kind=plan.resource.split(':')[1]
                data={}
                questionnaire=None
                for answer in answers:
                    if answer['field'] not in plan.fields:continue
                    binding=self.bindings[answer['field']['native_id']];questionnaire=binding['questionnaire']
                    value=self._value(binding,answer['value'])
                    data[answer['field']['native_key']]=([row['id'] for row in value] if isinstance(value,list) else
                        value['id'] if isinstance(value,dict) else value)
                if questionnaire is None:continue
                body=questionnaire_payload(questionnaire,data)
                operation=NativeOperation(native_url('jobapplication','jobapplication/'+self.remote+'/questionnaireresponse?type='+kind),
                    'POST',encoded(body),{'accept':'application/json','content-type':'application/json',SESSION_HEADER:self.session_id},'draft_write')
                write=StepWrite(operation,body,kind='questionnaire')
            else:
                body,used=self._payload(plan,answers)
                if not used:continue
                if plan.resource in {'uspersonalinfo','termsandconditions'}:
                    operation=NativeOperation(native_url('jobapplication','jobapplication/'+self.remote+'/'+plan.resource,old=True),
                        'PUT',encoded(body),{'accept':'application/json','content-type':'application/json',SESSION_HEADER:self.session_id},'draft_write')
                else:operation=record_operation(self.remote,self.session_id,plan.resource,body)
                write=StepWrite(operation,body,ack_validator=lambda data,plan=plan,body=body:self._record_ack(plan,body,data))
            self.saved.append((write,plan));result.append(write)
        if documents:
            if not self.attachment:raise Blocked('upload_verification_failed')
            body=dict(self.attachment)
            operation=record_operation(self.remote,self.session_id,'resumeattachments',body)
            plan=RecordPlan('resumeattachments',[],{},digest(body))
            write=StepWrite(operation,body,attachment_hashes=(documents[0]['hash'],),
                ack_validator=lambda data,plan=plan,body=body:self._record_ack(plan,body,data))
            self.saved.append((write,plan));result.append(write)
        return result

    def validate_bindings(self,write,snapshot,answers,documents):
        if snapshot is not self.current_snapshot:return False
        selected=next(((candidate,plan) for candidate,plan in self.saved if candidate is write),None)
        if not selected:return False
        _,plan=selected
        if plan.resource=='resumeattachments':
            return (bool(documents) and write.expected==self.attachment
                and write.attachment_hashes==(documents[0]['hash'],))
        if plan.resource.startswith('questionnaire:'):
            return all(answer['field'] in snapshot['fields'] for answer in answers)
        body,used=self._payload(plan,answers)
        return bool(used) and body==write.expected and all(field in snapshot['fields'] for field in plan.fields)

    @staticmethod
    def _record_ack(plan,expected,data):
        value=checked_object(data)
        # The official generic form submit and N0 attachment association accept
        # an empty aggregate object without claiming echoed values. It is an
        # explicitly recognized no-error ACK family, never sufficient by itself.
        if value=={}:return True
        allowed=set(expected)|{'id','descriptor'}
        if plan.resource=='name':allowed|={'legalName','preferredName'}
        if plan.resource=='uspersonalinfo':allowed|={'gender','ethnicity','ethnicityMulti','veteranStatus','hispanicOrLatino'}
        if set(value)-allowed:raise Blocked('remote_draft_uncertain','The native save acknowledgement changed')
        if 'id' in value:identifier(value['id'])
        if not ('id' in value or any(key in expected for key in value)):
            raise Blocked('remote_draft_uncertain','The native save acknowledgement is unrecognized')
        for key,item in value.items():
            if key in expected and not contains_saved(item,expected[key]):
                raise Blocked('remote_draft_uncertain','The native save acknowledgement does not match the approved values')
        return True

    @staticmethod
    def _correlate_saved_ack(plan,acknowledged,saved):
        # A syntactically valid but unrelated native ID cannot borrow matching
        # values from an older record. Empty aggregate ACKs have no ID claim;
        # their sole value proof remains the fresh owned readback.
        if 'id' in acknowledged and (not isinstance(saved,dict) or saved.get('id')!=acknowledged['id']):
            raise Blocked('remote_draft_uncertain','The native save acknowledgement identifies another record')
        if plan.resource=='name':
            for key in ('legalName','preferredName'):
                value=acknowledged.get(key)
                if isinstance(value,dict) and 'id' in value:
                    actual=saved.get(key) if isinstance(saved,dict) else None
                    if not isinstance(actual,dict) or actual.get('id')!=value['id']:
                        raise Blocked('remote_draft_uncertain','The native name acknowledgement identifies another record')
        return True

    def verify(self,answers,documents,snapshot):
        if snapshot is not self.current_snapshot:raise Blocked('form_changed')
        for field in snapshot['fields']:
            if field.get('required') and not any(row.get('field')==field for row in (documents if field['type']=='file' else answers)):
                raise Blocked('required_answer_missing',field['label'])
        return True

    def fill(self,snapshot,answers,documents):
        self.verify(answers,self.documents(snapshot),snapshot)

    def no_write_step(self,snapshot):
        return (snapshot is self.current_snapshot and
                not any(field.get('required') or field.get('value') for field in snapshot['fields']))

    def finish_no_write(self,page):
        self._control('validate',page)

    def upload(self,snapshot,document,queue):
        for scan in (True,False):
            response=self.transport.upload(document,scan=scan)
            queue.observe_response(response)

    def bind_attachment(self,document,attachment):self.attachment=dict(attachment)

    def reuse_documents(self,documents):
        """A resumed upload requires the original local byte-bound association."""
        if len(documents)!=1:raise Blocked('upload_verification_failed')
        document=documents[0]
        path=safe_document(self.store.root/'documents'/document['filename'],self.store.root/'documents')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=document['hash']:raise Blocked('document_changed')
        data=checked_object(self.transport.json(native_url('jobapplication','jobapplication/'+self.remote+'/resumeattachments')))
        records=data.get('attachments')
        if not isinstance(records,list):raise Blocked('remote_draft_uncertain')
        if not records:
            keys={digest(['scan',document['hash']]),digest(['upload',document['hash']])}
            if any(self.store.db.execute('SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step=?',
                    (self.job['id'],'My Experience:'+key)).fetchone() for key in keys):
                raise Blocked('remote_draft_uncertain','A previous upload exists without a matching saved attachment; review it before uploading again')
            return False
        candidates=[]
        for record in records:
            if not isinstance(record,dict):raise Blocked('remote_draft_uncertain')
            body={key:record[key] for key in ('file','fileName','fileLength','contentType') if key in record}
            if (body.get('fileName')!=document['filename'] or body.get('fileLength')!=len(raw)
                    or not isinstance(body.get('contentType'),dict)
                    or body['contentType'].get('id')!='Content_Type_ID=application/pdf'):continue
            identifier(body.get('file'))
            # Native descriptors are metadata; the approved wire payload had
            # only the source content-type ID.
            body['contentType']={'id':'Content_Type_ID=application/pdf'}
            request_hash=hashlib.sha256(encoded(body)).hexdigest()
            step=self.store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND tenant=? "
                "AND state='acknowledged' AND request_hash=? AND step LIKE 'My Experience:%'",
                (self.job['id'],NVIDIA,request_hash)).fetchone()
            uploaded=any(json.loads(row['detail']).get('document_hash')==document['hash'] for row in
                self.store.db.execute("SELECT detail FROM events WHERE kind='workday_pdf_uploaded' AND subject=?",(self.job['id'],)))
            if step and uploaded:candidates.append(body)
        if len(candidates)!=1:
            raise Blocked('remote_draft_uncertain',
                'The existing resume has no unique original approved-byte upload and attachment association. Review it manually; no replacement was uploaded.')
        self.attachment=candidates[0]
        return True

    def _resource_data(self,resource):
        if resource.startswith('questionnaire:'):
            kind=resource.split(':')[1]
            return self._json(native_url('jobapplication','jobapplication/'+self.remote+'/questionnaireanswers?type='+kind),
                {SESSION_HEADER:self.session_id})
        if resource=='name':url=native_url('jobapplication','jobapplication/'+self.remote+'/names')
        elif resource=='resumeattachments':url=native_url('jobapplication','jobapplication/'+self.remote+'/resumeattachments')
        elif resource in {'source','uspersonalinfo','termsandconditions'}:url=native_url('jobapplication','package/'+self.remote)
        else:url=native_url('jobapplication','package/'+self.remote+'/'+{'address':'addresses','phonenumber':'phonenumbers'}.get(resource,resource))
        return self.transport.json(url)

    @staticmethod
    def _empty_resource(resource,data):
        if resource.startswith('questionnaire:'):return data==[]
        value=checked_object(data)
        if resource=='name':return set(value)=={'legalName','preferredName'} and all(item is None for item in value.values())
        if resource=='resumeattachments':return value.get('attachments')==[]
        if resource in {'source','uspersonalinfo','termsandconditions'} and (
                type(value.get('termsAndConditionsUpdated')) is not bool or 'termsAndConditionsLockedMoment' not in value):
            raise Blocked('remote_draft_uncertain','The native application package is incomplete')
        if resource=='source':return value.get('source') is None
        if resource=='uspersonalinfo':return not any(key in value for key in ('gender','ethnicity','hispanicOrLatino','veteranStatuses'))
        if resource=='termsandconditions':return value.get('termsAndConditionsConsentMoment') is None
        return type(value.get('total')) is int and value['total']==0 and value.get('data')==[]

    def _readback(self,write,plan,data=None):
        from .workday_nvidia_readback import match
        resource=plan.resource
        data=self._resource_data(resource) if data is None else data
        if resource.startswith('questionnaire:'):
            from .workday_native import questionnaire_saved
            actual={'id':'readback','questionnaireAnswers':data} if isinstance(data,list) else data
            questionnaire_saved(actual,write.expected)
            return actual
        precision={field['native_key']:self.bindings[field['native_id']]['precision'] for field in plan.fields
                   if self.bindings[field['native_id']].get('precision')}
        try:return match(resource,data,write.expected,precision=precision)
        except Blocked:
            if resource=='address' and isinstance(data,dict) and isinstance(data.get('data'),list):
                missing=[key for key in ('addressLine1','postalCode') if key in write.expected
                    and not any(isinstance(row,dict) and key in row for row in data['data'])]
                if missing:
                    raise Blocked('workday_readback_unavailable',
                        'The employer does not expose '+', '.join(missing)+' in the owned address readback. '
                        'Review the existing draft manually; do not retry the save.') from None
            raise

    def perform_step(self,page,queue):
        self._control('validate',page)
        for step in queue.steps:
            write,plan=next((write,plan) for write,plan in self.saved if write.operation.url==step.grant.url
                and write.operation.body==step.grant.payload)
            response=self.transport.request(write.operation)
            if response.status!=200:raise Blocked('remote_draft_uncertain')
            acknowledged=checked_object(response.json())
            if write.ack_validator is not None:write.ack_validator(acknowledged)
            queue.observe_response(response)
            saved=self._readback(write,plan)
            if write.ack_validator is not None:self._correlate_saved_ack(plan,acknowledged,saved)
            from .workday_state import DraftObservation
            row=step.journal.current()
            if row['state']=='unknown':
                observation=DraftObservation(self.remote,digest(saved),self.controller.session.owner_hash,self.job['id'],
                    digest(write.expected),write.attachment_hashes)
                step.journal.acknowledge(observation,digest(saved),reconciled=True,record_id=step.grant.record_id)
        if page['section']=='My Information':self._control('initialize',page)

    def _control(self,purpose,page):
        from .approval_boundary import ApprovalBoundary
        from .workday import WriteGrant
        from .workday_state import CorrelatedStep,DraftJournal,DraftObservation
        if self.read_only or not self.remote or self.approved_package is None:raise Blocked('workday_unapproved_operation')
        if purpose=='validate':url=native_url('jobapplication','package/'+self.remote+'/validate');method='PUT';body=b''
        elif purpose=='initialize':url=native_url('jobapplication','jobapplication/'+self.remote+'/initialize',old=True);method='POST';body=b''
        elif purpose=='termslockmoment':url=native_url('jobapplication','jobapplication/'+self.remote+'/termslockmoment');method='POST';body=b'{}'
        else:raise Blocked('workday_unapproved_operation')
        section='Application Questions' if page['section'].startswith('Application Questions') else page['section']
        headers={SESSION_HEADER:self.session_id}
        operation=NativeOperation(url,method,body,headers,'draft_control')
        grant=WriteGrant(self.store,self.job,section,url,method,body,operation_key=digest([purpose,url]))
        grant.boundary=ApprovalBoundary(self.store,self.job,self.approved_package,require_live=False,
            profile_binding=lambda:self.controller.session.profile_binding if self.controller.session.current(self.store) else None)
        journal=DraftJournal(self.store,self.job,self.controller.session,self.controller.profile.signature)
        expected=digest(['native_control',purpose])
        def decode(data):
            checked_object(data)
            if purpose=='validate':
                if set(data)-{'validateNotSubmitted','validateActiveJobPosting'}:raise Blocked('remote_draft_uncertain')
                for key,value in data.items():
                    if not isinstance(value,dict) or set(value)-{'errors'}:raise Blocked('remote_draft_uncertain')
                    if value.get('errors'):raise Blocked('posting_changed_review')
            elif data not in ({},{'id':self.remote}):raise Blocked('remote_draft_uncertain')
            return DraftObservation(self.remote,digest(data),self.controller.session.owner_hash,self.job['id'],expected)
        control=CorrelatedStep(journal,grant,expected,decode,headers=headers)
        previous=self.browser.workday_grant;self.browser.workday_grant=control
        try:
            response=self.transport.request(operation);control.observe_response(response)
            if not control.observed or not grant.used or journal.current()['state']!='acknowledged':
                grant.uncertain();raise Blocked('remote_draft_uncertain','The native workflow control was not acknowledged')
        finally:self.browser.workday_grant=previous

    def wait(self,milliseconds):self.browser.page.wait_for_timeout(milliseconds)

    def review_matches(self,package,row):
        if row['remote_id']!=self.remote:return False
        return all(self._readback(write,plan) is not None for write,plan in self.saved)

    def submit(self,operation):return self.transport.request(operation)

    def completed_application(self,remote,package,data):
        if remote!=self.remote or data.get('id')!=remote:return False
        posting=self.posting_identifier()
        path=urlsplit(self.job['url']).path.split('/'+NVIDIA_SITE+'/job/',1)[1]
        detail=self.transport.json(ORIGIN+'/wday/cxs/nvidia/'+NVIDIA_SITE+'/job/'+path)
        info=detail.get('jobPostingInfo',{}) if isinstance(detail,dict) else {}
        application=info.get('jobApplication',{})
        if (not isinstance(application,dict) or application.get('id')!=remote
                or application.get('draft') is not False or not application.get('dateCompleted')):return False
        config=checked_object(self.transport.json(native_url('candidatehome','application/'+remote+'/config',site=True)))
        receipt=config.get('application')
        return (isinstance(receipt,dict) and receipt.get('applicationId')==remote
            and receipt.get('jobPostingAnchorId')==posting and receipt.get('jobRequisitionId')==self.info['jobReqId']
            and receipt.get('draft') is False and bool(receipt.get('dateApplied')))

    def read_draft(self,previous):
        return self._owned_draft(identifier(previous['remote_id']))

    def _owned_draft(self,remote):
        """Official resumed-draft path: owned query and config, never Apply/create."""
        from .workday_state import DraftObservation
        session=self.controller.session
        if not session.current(self.store):raise Blocked('workday_session_unverified')
        posting=self.posting_identifier()
        query=native_url('candidatehome','applications?type=active&jobPostingAnchorId=Job_Posting_Anchor_ID='+posting+'&draftsOnly=true',site=True)
        data=checked_object(self.transport.json(query))
        rows=data.get('data')
        if (not isinstance(rows,list) or type(data.get('total')) is not int or data['total']!=len(rows)
                or len(rows)!=1 or not isinstance(rows[0],dict)
                or rows[0].get('applicationId')!=remote or rows[0].get('draft') is not True
                or not isinstance(rows[0].get('posting'),dict) or rows[0]['posting'].get('isPosted') is not True):
            raise Blocked('remote_draft_uncertain','The employer did not identify one current owned draft for this requisition')
        self.remote=remote # The exact owned query permits only this config read.
        config=checked_object(self.transport.json(native_url('candidatehome','application/'+remote+'/config',site=True)))
        value=config.get('application')
        if (not isinstance(value,dict) or value.get('applicationId')!=remote or value.get('draft') is not True
                or value.get('jobPostingAnchorId')!=posting or value.get('jobRequisitionId')!=self.info['jobReqId']
                or value.get('dateApplied')):
            raise Blocked('remote_draft_uncertain','The owned draft configuration no longer matches this requisition')
        proof=digest({'query':data,'config':config})
        self.remote,self.session_id=remote,''
        self.resuming=True
        self.resume_proof={'remote_id':remote,'owner_hash':session.owner_hash,
            'profile_binding':session.profile_binding,'read_hash':proof}
        self.bind_draft(remote,'',session)
        return DraftObservation(remote,proof,session.owner_hash,self.job['id'],digest({})),proof

    def find_draft(self):
        """Discover one existing native draft before deciding whether creation is possible."""
        posting=self.posting_identifier()
        result=checked_object(self.transport.json(native_url('candidatehome',
            'applications?type=active&jobPostingAnchorId=Job_Posting_Anchor_ID='+posting+'&draftsOnly=true',site=True)))
        if result.get('total')==0 and result.get('data')==[]:return None
        values=result.get('data')
        if not isinstance(values,list) or len(values)!=1 or result.get('total')!=1 or not isinstance(values[0],dict):
            raise Blocked('remote_draft_uncertain','The employer draft list needs manual reconciliation')
        return self._owned_draft(identifier(values[0].get('applicationId')))

    def resumed_writes(self,writes):
        """Matching existing records need no replay; conflicting records require review."""
        if not self.resuming:return writes
        remaining=[]
        for write in writes:
            plan=next(plan for candidate,plan in self.saved if candidate is write)
            data=self._resource_data(plan.resource)
            if self._empty_resource(plan.resource,data):
                canonical='Application Questions' if self.current_page['section'].startswith('Application Questions') else self.current_page['section']
                key=digest([write.operation.url,write.operation.method,write.operation.body.hex()])
                if self.store.db.execute('SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step=?',
                        (self.job['id'],canonical+':'+key)).fetchone():
                    raise Blocked('remote_draft_uncertain','A previous native save has no matching record; repeating it is not authorized')
                remaining.append(write)
                continue
            try:self._readback(write,plan,data)
            except Blocked:
                raise Blocked('remote_draft_uncertain',
                    'The existing '+plan.resource+' does not provably match the approved record. Complete or reconcile this draft manually.') from None
        return remaining

    def read_pending(self,row,record_id,expected):
        """Rebuild the original approval and read its result; never repeat its request."""
        from .workday import source_hash
        from .workday_native import pages
        from .workday_state import DraftObservation
        if source_hash(self.store)!=expected.get('sources_hash'):
            raise Blocked('remote_draft_uncertain','The original approved sources changed; review this draft manually')
        step=self.store.db.execute('SELECT * FROM remote_draft_steps WHERE id=? AND job_id=?',
            (record_id,self.job['id'])).fetchone()
        if not step or step['state'] not in {'intent','unknown'} or step['request_hash']!=expected.get('request_hash'):
            raise Blocked('remote_draft_uncertain')
        self.read_only=True
        try:
            self._owned_draft(row['remote_id'])
            for page in pages(self.read_flow_pages().json()):
                if page['section']=='Review':break
                canonical='Application Questions' if page['section'].startswith('Application Questions') else page['section']
                if not step['step'].startswith(canonical+':'):continue
                self.enter(page)
                snapshot=self.snapshot(page)
                answers=self.controller.resolve_step(snapshot,self.provider)
                # Upload reconciliation needs the original byte-bound upload ID;
                # filenames and unrelated existing attachments cannot prove it.
                writes=self.writes(page,snapshot,answers,[])
                candidates=[(write,plan) for write,plan in self.saved if write in writes
                    and digest(write.expected)==expected.get('values_hash')
                    and hashlib.sha256(write.operation.body).hexdigest()==step['request_hash']
                    and canonical+':'+digest([write.operation.url,write.operation.method,write.operation.body.hex()])==step['step']]
                if len(candidates)!=1:continue
                write,plan=candidates[0]
                if expected.get('attachment_hashes'):break
                saved=self._readback(write,plan)
                return DraftObservation(self.remote,digest(saved),self.controller.session.owner_hash,
                    self.job['id'],digest(write.expected)),digest(saved)
            raise Blocked('remote_draft_uncertain',
                'This interrupted control, upload or final request cannot be proven by record readback. Review the same draft manually.')
        finally:self.read_only=False
