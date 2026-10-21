"""One approved native Lever hosted-form POST, with its own HTML receipt.

Reviewed 2026-10-21: jobs.lever.co/shopback-2/1804a30e-2d2e-4631-9e85-614c91806ddf/apply
is a multipart POST to that same posting. Its hCaptcha callback clicks a hidden
submit button; /js/application.js does not replace that native form submission.
This is deliberately separate from Lever's authenticated postings API.
"""
from __future__ import annotations

from collections import Counter
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from .approval_boundary import ApprovalBoundary
from .util import Blocked, digest

HOSTS={'jobs.lever.co','jobs.eu.lever.co'}
UUID=r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
METADATA={'accountId','origin','referer','timezone','socialReferralKey','socialSource','source','resumeStorageId'}
CAPTCHA='h-captcha-response'
FORM="""() => {
 const forms=document.querySelectorAll('form#application-form');
 if(forms.length!==1)return null;
 const form=forms[0];
 return {method:form.method,action:form.action,enctype:form.enctype,
   controls:Array.from(form.elements).filter(e=>e.name&&!e.disabled).map(e=>({
     name:e.name,id:e.id,type:e.type,hidden:e.type==='hidden',value:e.value,checked:e.checked})),
   entries:Array.from(new FormData(form)).map(([name,value])=>typeof value==='string'
     ? {name,value} : {name,filename:value.name,size:value.size,type:value.type})};
}"""


class ReceiptHTML(HTMLParser):
    def __init__(self):
        super().__init__();self.skip=0;self.text=[];self.form=False;self.email=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','style','head'}:self.skip+=1
        if tag=='form':self.form=True
        if tag=='input' and (attrs.get('type')=='email' or attrs.get('name')=='email'):self.email=True
        if tag in {'p','div','h1','h2','h3','br','li'}:self.text.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style','head'}:self.skip=max(0,self.skip-1)
    def handle_data(self,value):
        if not self.skip:self.text.append(value)


class LeverSubmission:
    @staticmethod
    def _approved_pairs(package,form):
        expected=[];names=set();location=None
        for answer in package['answers']:
            field=answer['field'];kind=field['type'];value=answer['value']
            refs=field.get('refs',[field.get('ref',{})])
            native_names={ref.get('name') for ref in refs}
            if len(native_names)!=1 or None in native_names or '' in native_names:
                raise Blocked('unsupported_form','The approved Lever field has no unique native name')
            name=next(iter(native_names))
            if name in names:raise Blocked('unsupported_form','Duplicate approved Lever field name')
            names.add(name)
            controls=[control for control in form['controls'] if control['name']==name and not control['hidden']]
            if (len(controls)!=len(refs) or any(ref.get('id') and control.get('id')!=ref['id']
                    for ref,control in zip(refs,controls))):
                raise Blocked('field_verification_failed',field['label'])
            if kind in {'radio','checkbox-group','checkbox'}:
                values=field.get('option_values')
                if (not isinstance(values,list) or len(values)!=len(controls)
                        or values!=[control['value'] for control in controls]
                        or any(control['type']!=('radio' if kind=='radio' else 'checkbox') for control in controls)):
                    raise Blocked('unsupported_form','The native Lever choice encoding changed')
                if kind=='checkbox':
                    if value not in {'Yes','No'}:raise Blocked('field_verification_failed',field['label'])
                    chosen=[0] if value=='Yes' else []
                else:
                    from .answers import selections
                    labels=[value] if kind=='radio' else selections(value,field['options'])
                    if not labels or any(label not in field['options'] for label in labels):raise Blocked('field_verification_failed',field['label'])
                    chosen=[field['options'].index(label) for label in labels]
                if [i for i,control in enumerate(controls) if control.get('checked')]!=sorted(chosen):
                    raise Blocked('field_verification_failed',field['label'])
                expected.extend((name,values[i]) for i in chosen)
            elif len(controls)!=1:
                raise Blocked('field_verification_failed',field['label'])
            else:
                actual=controls[0]['value'].replace('\r\n','\n')
                wanted=value.replace('\r\n','\n')
                if kind=='select' and field.get('option_values'):
                    wanted=field['option_values'][field['options'].index(value)]
                elif kind not in {'text','textarea','email','tel','url','date','number','select'} and not field.get('lever_location'):
                    raise Blocked('unsupported_form','The Lever applicant value encoding needs review')
                if answer.get('provenance',{}).get('fact_key')=='phone':
                    if re.sub(r'[^0-9]','',actual)!=re.sub(r'[^0-9]','',wanted):raise Blocked('field_verification_failed',field['label'])
                elif actual!=wanted:raise Blocked('field_verification_failed',field['label'])
                expected.append((name,actual))
                if field.get('lever_location'):location=actual
            # Native marketing consent always transmits its hidden default0;
            # only an approved checked declaration adds the reviewed value1.
            defaults=[control for control in form['controls'] if control['name']==name and control['hidden']]
            if defaults:
                if (kind!='checkbox' or not re.fullmatch(r'consent\[[a-z]+\]',name)
                        or len(defaults)!=1 or defaults[0]['value']!='0'):
                    raise Blocked('unsupported_form','An extra Lever applicant control would be transmitted')
                expected.append((name,'0'))
        outgoing=[(entry['name'],entry['value'].replace('\r\n','\n')) for entry in form['entries']
                  if 'value' in entry and entry['name'] in names]
        if Counter(outgoing)!=Counter(expected):raise Blocked('field_verification_failed','The native Lever payload differs from approved answers')
        selected=[control for control in form['controls'] if control['name']=='selectedLocation' and control['value']]
        if selected:
            def unique(pairs):
                result={}
                for key,item in pairs:
                    if key in result:raise ValueError('duplicate_json_key')
                    result[key]=item
                return result
            try:chosen=json.loads(selected[0]['value'],object_pairs_hook=unique)
            except (ValueError,TypeError):chosen=None
            if (len(selected)!=1 or location is None or not isinstance(chosen,dict)
                    or set(chosen)!={'id','name'} or chosen.get('name')!=location
                    or not isinstance(chosen.get('id'),str) or not 0<len(chosen['id'])<=1000
                    or re.search(r'[\x00-\x1f\x7f]',chosen['id'])):
                raise Blocked('field_verification_failed','The native Lever location differs from the approved choice')
        elif location is not None:raise Blocked('field_verification_failed','The native Lever location is not committed')
        return names,location

    def __init__(self,browser,job,package,application_id,submit=None):
        self.browser=browser;self.store=browser.store;self.job=job;self.package=package;self.aid=application_id
        self.used=False;self.acknowledged=False;self.failed=False;self.response_seen=False;self.request_keys=set()
        self.deadline=time.monotonic()+45
        parsed=urlsplit(browser.page.url)
        posting=urlsplit(job['url'])
        match=re.fullmatch(r'/([^/]+)/('+UUID+r')/apply',parsed.path,re.I)
        if (parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.hostname!=job['host']
                or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment
                or not match or posting.hostname!=parsed.hostname
                or posting.path.rstrip('/') not in {parsed.path,parsed.path[:-6]}):
            raise Blocked('unsupported_form','The native Lever posting identity is unverified')
        self.url=browser.page.url
        row=self.store.application_record(application_id)
        if (not row or row['state']!='prepared' or row['job_id']!=job['id']
                or row['hash']!=digest(package) or json.loads(row['package'])!=package):
            raise Blocked('application_changed')
        self.boundary=ApprovalBoundary(self.store,job,package,deadline=self.deadline,application_id=application_id)
        from .answers import validate_package
        validate_package(self.store,job,package)
        form=browser.page.evaluate(FORM)
        if (not isinstance(form,dict) or form.get('method','').casefold()!='post'
                or form.get('action')!=self.url or form.get('enctype')!='multipart/form-data'):
            raise Blocked('unsupported_form','The native Lever form destination changed')
        approved,location=self._approved_pairs(package,form)
        documents={doc['field'].get('ref',{}).get('name'):doc for doc in package['documents']}
        if None in documents or '' in documents:raise Blocked('unsupported_form','The Lever document field is unverified')
        self.documents=documents;self.files=[];self.pairs=[]
        successful={entry['name'] for entry in form['entries']}
        for control in form['controls']:
            name=control['name'];value=control['value']
            if not re.fullmatch(r'[A-Za-z0-9_\[\]-]{1,250}',name):raise Blocked('unsupported_form','Unknown Lever field identity')
            declined=bool(re.fullmatch(r'consent\[[a-z]+\]',name) and value=='0')
            if (name not in successful or not value or control['type'] in {'file','submit','button'}
                    or name==CAPTCHA or name in approved and (not control['hidden'] or declined)):continue
            template=bool(re.fullmatch(r'cards\['+UUID+r'\]\[baseTemplate\]',name,re.I))
            selected=name=='selectedLocation' and location is not None
            if control['hidden'] and (name in METADATA or template or selected or declined):continue
            raise Blocked('unsupported_form','An unapproved Lever field would be transmitted')
        for entry in form['entries']:
            if 'filename' in entry:
                doc=documents.get(entry['name'])
                if entry['size']==0 and entry['filename']=='':
                    self.files.append({**entry,'bytes':b''});continue
                if (not doc or entry['filename']!=doc['filename'] or entry['type']!='application/pdf'
                        or entry['size']!=len(browser.upload_payloads.get(doc['hash'],b''))):
                    raise Blocked('upload_verification_failed','The final Lever document is not approved')
                self.files.append({**entry,'bytes':browser.upload_payloads[doc['hash']]})
            elif entry['name']!=CAPTCHA:self.pairs.append((entry['name'],entry['value'].replace('\r\n','\n')))
        resume=browser.lever_resume
        pairs=dict(self.pairs)
        metadata=Counter(name for name,_ in self.pairs if name in METADATA or name=='selectedLocation')
        if any(count!=1 for count in metadata.values()):raise Blocked('unsupported_form','Duplicate native Lever metadata')
        if (not resume or pairs.get('accountId')!=resume.account or pairs.get('resumeStorageId')!=resume.storage_id
                or not resume.storage_id or not any(control['name']==CAPTCHA for control in form['controls'])):
            raise Blocked('upload_verification_failed','Lever has not committed the approved account and resume')
        resume.verify()
        self.baseline=browser.receipt_baseline
        self.boundary.check()

    def grant(self,request,key):
        if self.used or request.url!=self.url or request.method!='POST':return False
        self.boundary.check()
        row=self.store.application_record(self.aid)
        if (not row or row['state']!='submitting' or row['hash']!=digest(self.package)
                or json.loads(row['package'])!=self.package):raise Blocked('application_changed')
        from .answers import validate_package
        validate_package(self.store,self.job,self.package)
        content_type=request.headers.get('content-type','')
        body=request.post_data_buffer or b''
        if not content_type.startswith('multipart/form-data;') or len(body)>sum(len(f['bytes']) for f in self.files)+1048576:return False
        try:
            message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\n\r\n'+body)
            boundary=message.get_boundary()
            if (not isinstance(boundary,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',boundary)
                    or message.defects or not message.is_multipart()):return False
            pairs=[];files=[];parts=[];captchas=[]
            for part in message.iter_parts():
                if (part.defects or part.is_multipart() or part.get('Content-Transfer-Encoding') is not None
                        or len(part.keys())!=len(set(k.lower() for k in part.keys()))
                        or part.get_content_disposition()!='form-data'):return False
                name=part.get_param('name',header='content-disposition')
                if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_\[\]-]{1,250}',name):return False
                blob=part.get_payload(decode=True);filename=part.get_filename()
                if filename is None:
                    value=blob.decode('utf-8')
                    (captchas if name==CAPTCHA else pairs).append(value if name==CAPTCHA else (name,value.replace('\r\n','\n')))
                    parts.append((name,None,None,blob))
                else:
                    matches=[f for f in self.files if f['name']==name and f['filename']==filename and f['type']==part.get_content_type()]
                    if len(matches)!=1 or blob not in (b'',matches[0]['bytes']):return False
                    selected=matches[0]
                    files.append((name,filename,part.get_content_type()))
                    parts.append((name,filename,part.get_content_type(),selected['bytes']))
            if (Counter(pairs)!=Counter(self.pairs) or len(captchas)!=1 or not 0<len(captchas[0])<=20000
                    or re.search(r'[\x00-\x20\x7f]',captchas[0])
                    or Counter(files)!=Counter((f['name'],f['filename'],f['type']) for f in self.files)):return False
            delimiter=boundary.encode();transport=b''
            for name,filename,kind,blob in parts:
                if delimiter in blob:return False
                transport+=b'--'+delimiter+b'\r\nContent-Disposition: form-data; name="'+name.encode()+b'"'
                if filename is not None:transport+=b'; filename="'+filename.encode()+b'"\r\nContent-Type: '+kind.encode()
                transport+=b'\r\n\r\n'+blob+b'\r\n'
            transport+=b'--'+delimiter+b'--\r\n'
        except (ValueError,TypeError,UnicodeError,AttributeError):return False
        self.boundary.check()
        self.used=True;self.request_keys={key,(request.url,request.method,hashlib.sha256(transport).hexdigest())}
        self.store.event('native_submission_write',self.aid,{'platform':'lever'})
        return {'post_data':transport}

    def observe(self,response,key):
        if not self.used or self.response_seen or key not in self.request_keys:return
        # Body reads can reenter the outcome poller; pending is not failure.
        self.response_seen=True
        try:
            if not 200<=response.status<300:return
            content_type=response.headers.get('content-type','').split(';')[0].strip().casefold()
            if content_type!='text/html':return
            content=response.text()
            if len(content)>2000000:return
            parsed=ReceiptHTML();parsed.feed(content)
            from .browser import submission_receipt
            self.acknowledged=not parsed.form and not parsed.email and submission_receipt(''.join(parsed.text),self.baseline)
            self.failed=not self.acknowledged
        except (ValueError,TypeError,AttributeError):pass
        finally:
            self.failed=not self.acknowledged
            self.store.event('native_submission_response',self.aid,{'platform':'lever','acknowledged':self.acknowledged})

    def receipt_matches(self,page_url):
        return self.acknowledged and page_url==self.url
