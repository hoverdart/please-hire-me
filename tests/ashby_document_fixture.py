"""Closed native document transport for legacy Ashby browser scenarios.

The real production grants inspect every request. This fake employer asserts
the rebuilt PDF bytes and returns the source-reviewed handle and full File ACK.
"""
from __future__ import annotations

import copy
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from hireme.ashby_attachments import ATTACH, CREATE, HOST

FIXTURES = Path(__file__).parent / 'fixtures'
CREATE_QUERY = (FIXTURES / 'ashby-create-file-upload-handle.graphql').read_text()
ATTACH_QUERY = (FIXTURES / 'ashby-set-form-value-to-file.graphql').read_text()
PATH = '_systemfield_resume'


class AshbyDocuments:
    def __init__(self, store, tenant, req, render, definition):
        self.tenant, self.req, self.render, self.definition = tenant, req, render, definition
        self.document = dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
        self.blob = (store.root / 'documents' / self.document['filename']).read_bytes()
        self.handle = 'fixture-handle-' + req
        self.upload_url = 'https://' + HOST + '/'
        self.signed = {'key': 'fixture/' + req + '/' + self.document['hash'],
                       'policy': 'synthetic-policy', 'x-amz-signature': 'synthetic-signature'}
        self.created = self.uploaded = False
        self.file = None
        self.latest = None
        self.operations = []

    def form(self, native_form):
        result = copy.deepcopy(native_form)
        assert result['id'] == self.render and result['sourceFormDefinitionId'] == self.definition
        entry = {'id': self.render + '_' + PATH,
                 'field': {'path': PATH, 'title': 'Resume', 'type': 'File', 'isMany': False},
                 'fieldValue': copy.deepcopy(self.file)}
        sections = result.setdefault('sections', [])
        for section in sections:
            section['fieldEntries'] = [value for value in section.get('fieldEntries', [])
                                      if value.get('field', {}).get('path') != PATH]
        target = next((section for section in sections if not section.get('isHidden')), None)
        if target is None:
            target = {'fieldEntries': []}
            sections.append(target)
        target.setdefault('fieldEntries', []).append(entry)
        self.latest = copy.deepcopy(result)
        return result

    def html(self, html):
        control = (f'<div data-field-path="{PATH}" data-field-entry-id="{self.render}_{PATH}">'
                   f'<label>Resume<input id="{PATH}" type="file" name="{PATH}" required></label></div>')
        html, count = re.subn(r'<label>\s*Resume\s*<input\b[^>]*type=["\']file["\'][^>]*>\s*</label>', control, html)
        assert count == 1, 'Fixture must retain exactly one required native resume input'
        base = {'organizationHostedJobsPageName': self.tenant, 'formRenderIdentifier': self.render,
                'formDefinitionIdentifier': self.definition, 'path': PATH}
        script = '''<script>(()=>{
const base=__BASE__,createQuery=__CREATE__,attachQuery=__ATTACH__;
const send=data=>fetch('/api/non-user-graphql',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
document.getElementById('_systemfield_resume').addEventListener('change',async event=>{
 const file=event.target.files[0];if(!file)return;
 const reply=await send({operationName:'ApiCreateFileUploadHandle',query:createQuery,variables:{
  organizationHostedJobsPageName:base.organizationHostedJobsPageName,fileUploadContext:'NonUserFormEngine',
  filename:file.name,contentType:file.type,contentLength:file.size}});
 const value=(await reply.json()).data.fileUploadHandle;
 const data=new FormData();data.append('Content-Type',file.type);
 for(const [name,content] of Object.entries(typeof value.fields==='string'?JSON.parse(value.fields):value.fields))data.append(name,content);
 data.append('file',file);
 const upload=await fetch(value.url,{method:'POST',body:data});if(!upload.ok)return;
 await send({operationName:'ApiSetFormValueToFile',query:attachQuery,variables:{...base,fileHandle:value.handle}});
});})();</script>'''
        script = script.replace('__BASE__', json.dumps(base)).replace('__CREATE__', json.dumps(CREATE_QUERY)).replace('__ATTACH__', json.dumps(ATTACH_QUERY))
        return '<meta charset="utf-8">' + html + script

    def respond(self, browser, route, changes=None):
        request = route.request
        parsed = urlsplit(request.url)
        headers = {'access-control-allow-origin': '*', 'access-control-allow-methods': 'POST, OPTIONS',
                   'access-control-allow-headers': 'Content-Type'}
        if parsed.hostname == HOST:
            assert request.method == 'POST' and parsed.path == '/' and not parsed.query
            body = (changes or {}).get('post_data', request.post_data_buffer or b'')
            message = BytesParser(policy=policy.default).parsebytes(
                b'Content-Type: ' + request.headers['content-type'].encode() + b'\r\n\r\n' + body)
            parts = list(message.iter_parts())
            names = [part.get_param('name', header='content-disposition') for part in parts]
            assert not message.defects and len(names) == len(set(names))
            fields = {name: part.get_payload(decode=True).decode() for name, part in zip(names, parts) if name != 'file'}
            assert self.created and not self.uploaded and fields == {'Content-Type': 'application/pdf', **self.signed}
            assert names.count('file') == 1
            file = parts[names.index('file')]
            assert file.get_filename() == self.document['filename'] and file.get_content_type() == 'application/pdf'
            assert file.get_payload(decode=True) == self.blob
            self.uploaded = True
            self.operations.append('signed-upload')
            route.fulfill(status=204, headers=headers, body='')
            return True
        if (request.method != 'POST' or parsed.hostname != 'jobs.ashbyhq.com'
                or parsed.path != '/api/non-user-graphql'):
            return False
        data = json.loads(request.post_data)
        operation = data.get('operationName') if isinstance(data, dict) else None
        if operation not in {CREATE, ATTACH}:
            return False
        assert not browser.attempted
        if operation == CREATE:
            assert not self.created and data['query'] == CREATE_QUERY
            assert data['variables'] == {'organizationHostedJobsPageName': self.tenant,
                'fileUploadContext': 'NonUserFormEngine', 'filename': self.document['filename'],
                'contentType': 'application/pdf', 'contentLength': len(self.blob)}
            self.created = True
            result = {'data': {'fileUploadHandle': {'handle': self.handle, 'url': self.upload_url,
                                                   'fields': json.dumps(self.signed)}}}
        else:
            assert self.created and self.uploaded and self.file is None and data['query'] == ATTACH_QUERY
            assert data['variables'] == {'organizationHostedJobsPageName': self.tenant,
                'formRenderIdentifier': self.render, 'formDefinitionIdentifier': self.definition,
                'path': PATH, 'fileHandle': self.handle}
            assert self.latest is not None
            self.file = {'__typename': 'File', 'id': 'native-file-' + self.req, 'filename': self.document['filename']}
            result = {'data': {'setFormValueToFile': self.form(self.latest)}}
        self.operations.append(operation)
        route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
        return True
