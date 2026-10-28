"""Bind Lever's native resume parser to the approved PDF and rendered account."""
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit
import re
import hashlib

from .util import Blocked

HOSTS={'jobs.lever.co','jobs.eu.lever.co'}


def parsed_urls(links):
    """Reviewed parseResume.js mapping; later links replace earlier categories.

    Public source SHA256 (2026-10-27 PDT):
    b114955d021349e3d56975ae7f625e66341928d69c15d7f4a5aff654383c0ef8.
    These are parser observations, never approved applicant facts.
    """
    if not isinstance(links,list) or len(links)>100:return {}
    values={}
    for link in links:
        if not isinstance(link,dict):return {}
        domain=link.get('domain');url=link.get('url')
        if (domain is not None and not isinstance(domain,str)
                or not isinstance(url,str) or not 0<len(url)<=2000 or re.search(r'[\x00-\x1f\x7f]',url)):
            return {}
        name=next((name for name in ('LinkedIn','Twitter','Quora','GitHub')
                   if domain and name.lower() in domain.lower()),'Other')
        values['urls['+name+']']=url
    return values


class LeverResume:
    def __init__(self,browser,document):
        self.browser=browser;self.document=document;self.request_keys=set();self.storage_id=None
        self.used=False;self.parsed_company=None;self.parsed_urls={}
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
            self.parsed_urls=parsed_urls(result.get('links',[]))

    def clear_unapproved_prefills(self,fields,answers,job_id):
        """Preserve initially blank optional fields after native parsing.

        Clear only exact values from the acknowledged parser response in its
        reviewed org/URL fields. Existing entries, required fields and approved
        answers stay under the ordinary review and final-payload guards.
        """
        prefills={**self.parsed_urls,**({'org':self.parsed_company} if self.parsed_company is not None else {})}
        approved={ref.get('name') for answer in answers
                  for ref in answer['field'].get('refs',[answer['field'].get('ref',{})])}
        for name,value in prefills.items():
            if name in approved:continue
            matches=[f for f in fields if f.get('ref',{}).get('name')==name]
            if (len(matches)!=1 or matches[0].get('type')!='text'
                    or matches[0].get('required') or matches[0].get('value','')!=''):continue
            control=self.browser._control(matches[0])
            if control.input_value()!=value:continue
            self.verify()
            if self.browser.write_boundary:self.browser.write_boundary.check()
            control.fill('')
            if control.input_value():raise Blocked('field_verification_failed',matches[0]['label'])
            self.browser.store.event('parser_prefill_cleared',job_id,{'field':name,'document_hash':self.document['hash']})

    def verify(self):
        storage=self.browser.page.locator('input[type=hidden][name=resumeStorageId]')
        account=self.browser.page.locator('input[type=hidden][name=accountId]')
        if (not self.storage_id or storage.count()!=1 or storage.input_value()!=self.storage_id
                or account.count()!=1 or account.input_value()!=self.account):
            raise Blocked('upload_verification_failed','Lever has not committed the approved resume acknowledgement')
