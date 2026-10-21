"""One approved PDF in Workday's reviewed multipart `body` field.

The common attachment acknowledgement is separate from the later native
resumeattachments save that associates its ID with this application draft.
"""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import hashlib
import json
import re

from .approval_boundary import ApprovalBoundary
from .util import Blocked, digest, safe_document
from .workday import WriteGrant
from .workday_native import native_url, upload_saved


class UploadGrant:
    def __init__(self,journal,document,*,scan=False,package=None):
        self.journal,self.store,self.document=journal,journal.store,dict(document)
        self.scan=scan
        self.boundary=ApprovalBoundary(self.store,journal.job,package,require_live=True,
            profile_binding=lambda:journal.session.profile_binding if journal.session.current(self.store) else None)
        current=self.store.db.execute('SELECT hash,filename FROM documents WHERE kind=?',(document.get('kind'),)).fetchone()
        path=safe_document(self.store.root/'documents'/document.get('filename',''),self.store.root/'documents')
        self.data=path.read_bytes()
        if (not current or current['hash']!=document.get('hash') or current['filename']!=document.get('filename')
                or hashlib.sha256(self.data).hexdigest()!=document.get('hash') or not self.data.startswith(b'%PDF-')
                or not 0<len(self.data)<=5_000_000):
            raise Blocked('document_changed','The approved Workday PDF is missing, changed or exceeds the supported native size')
        self.url=native_url('common','scanfile' if scan else 'attachments')
        self.grant=None;self.signatures=set();self.observed=False;self.acknowledged=False;self.attachment=None

    def consume(self,url,method,payload):
        return False # Header-aware multipart parsing is mandatory.

    def consume_request(self,request):
        if self.grant or request.url!=self.url or request.method!='POST':return False
        headers={key.lower():value for key,value in request.headers.items()}
        content_type=headers.get('content-type','')
        payload=request.post_data_buffer
        if ('form-definition' in headers or not isinstance(payload,bytes)
                or len(payload)>len(self.data)+16384 or '\r' in content_type or '\n' in content_type
                or not content_type.lower().startswith('multipart/form-data;')):return False
        try:
            message=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+payload)
            boundary=message.get_boundary()
            if (message.defects or not isinstance(boundary,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',boundary)
                    or boundary.encode() in self.data):return False
            parts=list(message.iter_parts()) if message.is_multipart() else []
            if len(parts)!=1:return False
            part=parts[0]
            parameters=part.get_params(header='content-disposition') or []
            if (part.defects or part.is_multipart() or len(part.get_all('Content-Disposition',[]))!=1
                    or len(part.get_all('Content-Type',[]))!=1 or part.get_content_disposition()!='form-data'
                    or set(key.lower() for key in part.keys())!={'content-disposition','content-type'}
                    or len(parameters)!=3 or len({key for key,_ in parameters})!=3
                    or part.get_param('name',header='content-disposition')!='body'
                    or part.get_filename()!=self.document['filename'] or part.get_content_type()!='application/pdf'
                    or part.get_payload(decode=True) not in (b'',self.data)):return False
        except (ValueError,TypeError,UnicodeError):return False
        # Chromium's intercepted multipart metadata omits the file bytes.
        # Always reconstruct the actual transmitted file from the approved PDF,
        # including when intercepted metadata contains an empty file part.
        wire=(b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="body"; filename="'+
              self.document['filename'].encode('ascii')+b'"\r\nContent-Type: application/pdf\r\n\r\n'+self.data+
              b'\r\n--'+boundary.encode()+b'--\r\n')
        self.boundary.check()
        grant=WriteGrant(self.store,self.journal.job,'My Experience',self.url,'POST',wire,
            operation_key=digest(['scan' if self.scan else 'upload',self.document['hash']]))
        grant.boundary=self.boundary
        if not grant.consume(request.url,request.method,wire):return False
        self.grant=grant
        self.signatures={(request.url,request.method,hashlib.sha256(body).hexdigest(),content_type) for body in (payload,wire)}
        return {'post_data':wire}

    def observe_response(self,response):
        if not self.grant or self.observed:return False
        request=response.request
        headers={key.lower():value for key,value in request.headers.items()}
        payload=request.post_data_buffer or b''
        signature=(request.url,request.method,hashlib.sha256(payload).hexdigest(),headers.get('content-type',''))
        if signature not in self.signatures:return False
        try:
            data=response.json()
            # Playwright can resume the native fetch while another response
            # callback is waiting for this body. A reentrant observer may have
            # already finished its durable projection during that wait.
            if self.observed:return False
            if response.status!=200 or not isinstance(data,dict) or data.get('errors'):raise ValueError()
            if self.scan:
                if (data.get('diagnosis')!='clean' or data.get('containsMacros') is not False
                        or type(data.get('fileSize')) is not int or data['fileSize']!=len(self.data)
                        or data.get('fileType')!='application/pdf'):raise ValueError()
            else:
                self.attachment=upload_saved(data,filename=self.document['filename'],length=len(self.data),content_type='application/pdf')
            with self.store.transaction():
                self.grant.acknowledge(digest(data))
                self.store.event('workday_pdf_scanned' if self.scan else 'workday_pdf_uploaded',self.journal.job['id'],
                    {'document_hash':self.document['hash'],'acknowledgement_hash':digest(data)})
            self.acknowledged=True
            return True
        except (Blocked,ValueError,TypeError,AttributeError,KeyError):
            if not self.observed:self.grant.uncertain()
            return False
        finally:
            # Pending body decoding is not a terminal rejection. Consumers may
            # advance only after the acknowledgement (or rejection) is durable.
            self.observed=True

    @property
    def complete(self):
        return self.observed and self.acknowledged

    def uncertain(self):
        if self.grant:self.grant.uncertain()


class UploadQueue:
    def __init__(self,grants):
        self.grants=list(grants)
        if len({grant.url for grant in self.grants})!=len(self.grants):raise Blocked('workday_unapproved_operation')

    def consume(self,url,method,payload):return False

    def consume_request(self,request):
        matches=[grant for grant in self.grants if grant.url==request.url and not grant.grant]
        if matches and not matches[0].scan and any(grant.scan and not grant.complete for grant in self.grants):return False
        return matches[0].consume_request(request) if len(matches)==1 else False

    def observe_response(self,response):
        return any(grant.observe_response(response) for grant in self.grants)

    @property
    def complete(self):return bool(self.grants) and all(grant.complete for grant in self.grants)
