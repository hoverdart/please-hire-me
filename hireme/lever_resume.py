"""Bind Lever's native resume parser to the approved PDF and rendered account."""
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit
import re
import hashlib

from .util import Blocked

HOSTS={'jobs.lever.co','jobs.eu.lever.co'}


class LeverResume:
    def __init__(self,browser,document):
        self.browser=browser;self.document=document;self.request_keys=set();self.storage_id=None
        self.used=False;self.parsed_company=None
        account=browser.page.locator('input[type=hidden][name=accountId]')
        storage=browser.page.locator('input[type=hidden][name=resumeStorageId]')
        if account.count()!=1 or storage.count()!=1:raise Blocked('upload_verification_failed','Lever resume controls are unavailable')
        self.account=account.input_value()
        if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',self.account):
            raise Blocked('upload_verification_failed','Lever account identifier is unavailable')

    def grant(self,request,key):
        url=urlsplit(request.url);browser=self.browser
        if (self.used or browser.current_host not in HOSTS or url.hostname!=browser.current_host or url.scheme!='https'
                or url.path!='/parseResume' or url.query or url.username or url.password or request.method!='POST'):return False
        blob=browser.upload_payloads.get(self.document['hash']);body=request.post_data_buffer or b''
        if not blob or not 0<len(body)<=len(blob)+8192:return None
        content_type=request.headers.get('content-type','')
        if not content_type.startswith('multipart/form-data;') or '\r' in content_type or '\n' in content_type:return False
        try:
            message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\n\r\n'+body)
            boundary=message.get_boundary()
            if not isinstance(boundary,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',boundary) or boundary.encode() in blob:return False
            parts=list(message.iter_parts())
            if message.defects or len(parts)!=2:return False
            fields={p.get_param('name',header='content-disposition'):p for p in parts}
            if set(fields)!={'resume','accountId'}:return False
            resume=fields['resume'];account=fields['accountId']
            if (any(p.defects or p.is_multipart() or p.get('Content-Transfer-Encoding') is not None
                    or len(p.keys())!=len(set(k.lower() for k in p.keys()))
                    or p.get_content_disposition()!='form-data' for p in parts)
                    or resume.get_filename()!=self.document['hash']+'.pdf' or resume.get_content_type()!='application/pdf'
                    or resume.get_payload(decode=True) not in (b'',blob) or account.get_filename() is not None
                    or account.get_payload(decode=True)!=self.account.encode('ascii')):return False
        except (ValueError,TypeError,UnicodeError):return False
        # Chromium omits file bytes from intercepted multipart metadata. Build
        # the actual transport from the hash-checked approved PDF, never trust
        # an omitted payload or an arbitrary file with the approved filename.
        payload=(b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="resume"; filename="'+
                 (self.document['hash']+'.pdf').encode()+b'"\r\nContent-Type: application/pdf\r\n\r\n'+blob+
                 b'\r\n--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="accountId"\r\n\r\n'+
                 self.account.encode()+b'\r\n--'+boundary.encode()+b'--\r\n')
        if browser.write_boundary:browser.write_boundary.check()
        self.used=True
        self.request_keys={key,(request.url,request.method,hashlib.sha256(payload).hexdigest())}
        return {'post_data':payload}

    def observe(self,response,key):
        if key not in self.request_keys or response.status!=200:return
        try:result=response.json()
        except Exception:return
        value=result.get('resumeStorageId') if isinstance(result,dict) else None
        if isinstance(value,str) and 0<len(value)<=200 and not re.search(r'[\s\x00-\x1f]',value):
            self.storage_id=value;self.browser.uploaded_files.add(self.document['hash'])
            company=result.get('position')
            self.parsed_company=(company if isinstance(company,str) and 0<len(company)<=1000
                                 and not re.search(r'[\x00-\x1f\x7f]',company) else None)

    def clear_unapproved_company(self,fields,answers,job_id):
        """Preserve an initially blank optional company after native parsing.

        Official parseResume.js fills org from position. Clear only that exact
        acknowledged parser value; existing entries and approved answers stay
        under the ordinary review and final-payload guards.
        """
        if self.parsed_company is None:return
        if any(ref.get('name')=='org' for answer in answers
               for ref in answer['field'].get('refs',[answer['field'].get('ref',{})])):return
        fields=[f for f in fields if f.get('ref',{}).get('name')=='org']
        if (len(fields)!=1 or fields[0].get('type')!='text' or fields[0].get('required')
                or fields[0].get('value','')!=''):return
        control=self.browser._control(fields[0])
        if control.input_value()!=self.parsed_company:return
        self.verify()
        if self.browser.write_boundary:self.browser.write_boundary.check()
        control.fill('')
        if control.input_value():raise Blocked('field_verification_failed',fields[0]['label'])
        self.browser.store.event('parser_prefill_cleared',job_id,{'field':'org','document_hash':self.document['hash']})

    def verify(self):
        storage=self.browser.page.locator('input[type=hidden][name=resumeStorageId]')
        account=self.browser.page.locator('input[type=hidden][name=accountId]')
        if (not self.storage_id or storage.count()!=1 or storage.input_value()!=self.storage_id
                or account.count()!=1 or account.input_value()!=self.account):
            raise Blocked('upload_verification_failed','Lever has not committed the approved resume acknowledgement')
