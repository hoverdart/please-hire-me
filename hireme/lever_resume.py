"""Bind Lever's native resume parser to the approved PDF and rendered account."""
from email import policy
from email.parser import BytesParser
from urllib.parse import urlsplit
import re

from .util import Blocked

HOSTS={'jobs.lever.co','jobs.eu.lever.co'}


class LeverResume:
    def __init__(self,browser,document):
        self.browser=browser;self.document=document;self.request_key=None;self.storage_id=None
        account=browser.page.locator('input[type=hidden][name=accountId]')
        storage=browser.page.locator('input[type=hidden][name=resumeStorageId]')
        if account.count()!=1 or storage.count()!=1:raise Blocked('upload_verification_failed','Lever resume controls are unavailable')
        self.account=account.input_value()
        if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',self.account):
            raise Blocked('upload_verification_failed','Lever account identifier is unavailable')

    def allow(self,request,key):
        url=urlsplit(request.url);browser=self.browser
        if (browser.current_host not in HOSTS or url.hostname!=browser.current_host or url.scheme!='https'
                or url.path!='/parseResume' or url.query or url.username or url.password or request.method!='POST'):return False
        blob=browser.upload_payloads.get(self.document['hash']);body=request.post_data_buffer or b''
        if not blob or not len(blob)<=len(body)<=len(blob)+8192:return False
        content_type=request.headers.get('content-type','')
        if not content_type.startswith('multipart/form-data;') or '\r' in content_type or '\n' in content_type:return False
        try:
            message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode('ascii')+b'\r\n\r\n'+body)
            parts=list(message.iter_parts())
            if message.defects or len(parts)!=2:return False
            fields={p.get_param('name',header='content-disposition'):p for p in parts}
            if set(fields)!={'resume','accountId'}:return False
            resume=fields['resume'];account=fields['accountId']
            if (any(p.defects or p.is_multipart() or p.get('Content-Transfer-Encoding') is not None
                    or len(p.keys())!=len(set(k.lower() for k in p.keys()))
                    or p.get_content_disposition()!='form-data' for p in parts)
                    or resume.get_filename()!=self.document['hash']+'.pdf' or resume.get_content_type()!='application/pdf'
                    or resume.get_payload(decode=True)!=blob or account.get_filename() is not None
                    or account.get_payload(decode=True)!=self.account.encode('ascii')):return False
        except (ValueError,TypeError,UnicodeError):return False
        self.request_key=key
        return True

    def observe(self,response,key):
        if key!=self.request_key or response.status!=200:return
        try:result=response.json()
        except Exception:return
        value=result.get('resumeStorageId') if isinstance(result,dict) else None
        if isinstance(value,str) and 0<len(value)<=200 and not re.search(r'[\s\x00-\x1f]',value):
            self.storage_id=value;self.browser.uploaded_files.add(self.document['hash'])

    def verify(self):
        storage=self.browser.page.locator('input[type=hidden][name=resumeStorageId]')
        account=self.browser.page.locator('input[type=hidden][name=accountId]')
        if (not self.storage_id or storage.count()!=1 or storage.input_value()!=self.storage_id
                or account.count()!=1 or account.input_value()!=self.account):
            raise Blocked('upload_verification_failed','Lever has not committed the approved resume acknowledgement')
