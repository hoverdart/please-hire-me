"""Bind Ashby's reviewed upload/attachment chain to one approved form field.

ApiCreateFileUploadHandle and ApiSetFormValueToFile were extracted from the
official public client. A handle, filename or unrelated successful form response
alone never acknowledges the approved document at its intended field.
"""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlsplit

from .ashby_drafts import same, verified_definition_identifier
from .util import Blocked, digest

HOST = 'ashbyhq-infra-prd-main-app-uploaded-files-us-east-1.s3.us-east-1.amazonaws.com'
CREATE = 'ApiCreateFileUploadHandle'
ATTACH = 'ApiSetFormValueToFile'
QUERIES = {CREATE: '0cee3a05f5bdcff55e167ac4bc78e1e7e258133e1b5173812591636908c225c1',
           ATTACH: '622e98b3379d83363dcf01f50f31a6d3356c9424f01f6e02943334ee7bba5ad6'}
CREATE_VARIABLES = {'organizationHostedJobsPageName', 'fileUploadContext', 'filename', 'contentType', 'contentLength'}
ATTACH_VARIABLES = {'organizationHostedJobsPageName', 'formRenderIdentifier', 'formDefinitionIdentifier', 'path', 'fileHandle'}


def _file(value):
    if (isinstance(value, dict) and value.get('__typename') == 'File'
            and isinstance(value.get('id'), str) and 0 < len(value['id']) <= 500
            and not re.search(r'[\s\x00-\x1f]', value['id'])
            and isinstance(value.get('filename'), str) and 0 < len(value['filename']) <= 255):
        return {'id': value['id'], 'filename': value['filename']}
    return None


def file_values(form):
    """Project exact native File values alongside the draft's JSONBox values."""
    values = {}
    if not isinstance(form, dict) or form.get('errorMessages') or form.get('formErrors'):
        return values
    for section in form.get('sections', []):
        if section.get('isHidden'):
            continue
        for entry in section.get('fieldEntries', []):
            path = entry.get('field', {}).get('path')
            value = _file(entry.get('fieldValue'))
            if (isinstance(path, str) and not entry.get('isHidden') and value
                    and entry.get('id') == str(form.get('id')) + '_' + path):
                if path in values:
                    return {}
                values[path] = value
    return values


def _json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique)


def _upload_url(value):
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == 'https' and parsed.hostname == HOST and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path in ('', '/'))
    except (ValueError, TypeError):
        return False


class _Attachments:
    def __init__(self, browser):
        self.browser, self.draft = browser, getattr(browser, 'ashby_draft', None)
        self.identity = self.draft.identity if self.draft else None
        boundary = getattr(browser, 'write_boundary', None)
        self.package = boundary.package if boundary else None
        if not self.identity or not self.package:
            raise Blocked('ashby_draft_unsupported', 'The approved attachment sources are unavailable')
        self.documents = {}
        self.pending, self.responses, self.handles, self.uploads = {}, {}, {}, {}
        self.create_counts = {}
        for document in self.package['documents']:
            field = document['field']; path, entry = field.get('ashby_path'), field.get('ashby_entry_id')
            matches = [(render, form) for render, form in self.draft.forms.items()
                       if isinstance(path, str) and entry == render + '_' + path and path in form['entries']]
            if len(matches) != 1:
                raise Blocked('ashby_draft_unsupported', 'The approved attachment has no exact native field')
            render, form = matches[0]; metadata = form['entries'][path]
            if (metadata.get('type') != 'File' or metadata.get('isMany') not in (None, False)
                    or not verified_definition_identifier(form['definition'], self.identity[1])):
                raise Blocked('ashby_draft_unsupported', 'The native attachment field requires review')
            key = render, path
            if key in self.documents:
                raise Blocked('ashby_draft_unsupported', 'Duplicate attachment target')
            self.documents[key] = {'hash': document['hash'], 'filename': document['filename'],
                'definition': form['definition'], 'metadata': digest(metadata), 'acknowledged': None, 'used': False}
        self.draft.attachment_check = self.check

    def current(self):
        if self.browser.ashby_draft is not self.draft or self.draft.identity != self.identity:
            raise Blocked('upload_verification_failed', 'The native attachment posting changed')
        for (render, path), document in self.documents.items():
            form = self.draft.forms.get(render, {})
            if (form.get('definition') != document['definition']
                    or digest(form.get('entries', {}).get(path)) != document['metadata']):
                raise Blocked('upload_verification_failed', 'The native attachment field changed')
        return True

    def authorize(self):
        self.current()
        self.browser.write_boundary.check()
        from .answers import validate_package
        validate_package(self.browser.store, self.draft.job, self.package)
        self.browser.store._check_budget(self.draft.job, self.browser.store.settings())
        self.browser.write_boundary.check()

    def decode_pending(self):
        # Response.json() can yield to the next native request; decode only the
        # already observed response, never issue another employer request.
        for key, response in list(self.responses.items()):
            self.observe(response, key)

    def check(self):
        self.decode_pending()
        self.current()
        for key, document in self.documents.items():
            actual = self.draft.forms[key[0]].get('file_values', {}).get(key[1])
            if not document['acknowledged'] or not same(actual, document['acknowledged']):
                raise Blocked('upload_verification_failed', 'The exact approved native attachment is not acknowledged')
        return True

    def grant(self, request, data, key):
        parsed = urlsplit(request.url)
        if (getattr(self.browser, 'attempted', False) or request.method != 'POST' or parsed.scheme != 'https'
                or parsed.hostname != 'jobs.ashbyhq.com' or parsed.port not in (None, 443)
                or parsed.path != '/api/non-user-graphql' or parsed.username or parsed.password or parsed.fragment
                or not isinstance(data, dict) or set(data) != {'operationName', 'query', 'variables'}):
            return False
        if not same(_json(request.post_data), data):
            return False
        operation, query, variables = data.get('operationName'), data.get('query'), data.get('variables')
        if (operation not in QUERIES or parse_qsl(parsed.query, keep_blank_values=True) not in ([], [('op', operation)])
                or not isinstance(query, str) or len(query) > 50000
                or hashlib.sha256(re.sub(r'\s+', '', query).encode()).hexdigest() != QUERIES[operation]
                or not isinstance(variables, dict)
                or set(variables) != (CREATE_VARIABLES if operation == CREATE else ATTACH_VARIABLES)
                or variables.get('organizationHostedJobsPageName') != self.identity[0]):
            return False
        self.decode_pending()
        if operation == CREATE:
            matches = [document for document in self.documents.values() if document['filename'] == variables.get('filename')]
            if not matches or len({document['hash'] for document in matches}) != 1:
                return False
            checksum = matches[0]['hash']; blob = self.browser.upload_payloads.get(checksum)
            if (not blob or hashlib.sha256(blob).hexdigest() != checksum
                    or variables.get('fileUploadContext') != 'NonUserFormEngine'
                    or variables.get('contentType') != 'application/pdf'
                    or type(variables.get('contentLength')) is not int or variables['contentLength'] != len(blob)
                    or self.create_counts.get(checksum, 0) >= len(matches)):
                return False
            pending = {'operation': operation, 'hash': checksum, 'filename': matches[0]['filename']}
        else:
            field = variables['formRenderIdentifier'], variables['path']
            document = self.documents.get(field); handle = self.handles.get(variables['fileHandle'])
            if (not document or document['used'] or not handle or not handle['uploaded']
                    or handle['hash'] != document['hash'] or variables['formDefinitionIdentifier'] != document['definition']):
                return False
            pending = {'operation': operation, 'field': field, 'handle': variables['fileHandle']}
        self.authorize()
        if operation == CREATE:
            self.create_counts[checksum] = self.create_counts.get(checksum, 0) + 1
        else:
            document['used'] = True
        self.pending[key] = pending
        return True

    def observe(self, response, key):
        pending = self.pending.get(key)
        if not pending:
            upload = self.uploads.pop(key, None)
            if upload:
                for alias in upload['keys']:
                    self.uploads.pop(alias, None)
                if 200 <= response.status < 300:
                    self.handles[upload['handle']]['uploaded'] = True
                    self.browser.uploaded_files.add(self.handles[upload['handle']]['hash'])
            return
        self.responses[key] = response
        try:
            result = response.json()
            if self.pending.get(key) is not pending:
                return
            if not 200 <= response.status < 300 or not isinstance(result, dict) or result.get('errors'):
                return
            if pending['operation'] == CREATE:
                value = result.get('data', {}).get('fileUploadHandle')
                if not isinstance(value, dict):
                    return
                handle, url, fields = value.get('handle'), value.get('url'), value.get('fields')
                fields = _json(fields) if isinstance(fields, str) else fields
                if (not isinstance(handle, str) or not 0 < len(handle) <= 10000 or re.search(r'[\s\x00-\x1f]', handle)
                        or handle in self.handles or not _upload_url(url) or not isinstance(fields, dict) or not fields
                        or set(fields) & {'file', 'Content-Type'}
                        or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', k)
                               or not isinstance(v, str) or len(v) > 20000 or re.search(r'[\r\n\x00]', v) for k, v in fields.items())):
                    return
                self.handles[handle] = {**pending, 'url': url, 'fields': fields, 'uploaded': False, 'upload_used': False}
                self.browser.ashby_file_handles[handle] = pending['hash']
            else:
                render, path = pending['field']; document = self.documents[pending['field']]
                form = result.get('data', {}).get('setFormValueToFile')
                if (not isinstance(form, dict) or form.get('id') != render
                        or form.get('sourceFormDefinitionId') != document['definition']
                        or form.get('errorMessages') or form.get('formErrors')):
                    return
                entries = [entry for section in form.get('sections', []) if not section.get('isHidden')
                           for entry in section.get('fieldEntries', []) if not entry.get('isHidden')
                           and entry.get('id') == render + '_' + path and entry.get('field', {}).get('path') == path]
                value = _file(entries[0].get('fieldValue')) if len(entries) == 1 else None
                if (not value or value['filename'] != document['filename']
                        or digest(entries[0]['field']) != document['metadata']):
                    return
                self.draft._remember_form(form,acknowledged=True)
                document['acknowledged'] = value
                self.browser.ashby_attached_files.add(document['hash'])
        except (ValueError, TypeError, AttributeError, KeyError):
            return
        finally:
            self.pending.pop(key, None)
            self.responses.pop(key, None)


def _state(browser):
    state = getattr(browser, 'ashby_attachment_state', None)
    if state is None:
        state = browser.ashby_attachment_state = _Attachments(browser)
    return state


def grant_attachment(browser, request, data, key):
    try:
        return _state(browser).grant(request, data, key)
    except (ValueError, TypeError, AttributeError, KeyError):
        return False


def observe_attachment(browser, response, key):
    state = getattr(browser, 'ashby_attachment_state', None)
    if state is not None:
        state.observe(response, key)


def attached(browser, document):
    state = getattr(browser, 'ashby_attachment_state', None)
    if state is None:
        return False
    try:
        state.decode_pending()
        state.current()
        field = document['field']; path = field.get('ashby_path'); entry = field.get('ashby_entry_id')
        matches = [(key, value) for key, value in state.documents.items()
                   if entry == key[0] + '_' + key[1] and path == key[1]
                   and value['hash'] == document['hash'] and value['filename'] == document['filename']]
        return (len(matches) == 1 and bool(matches[0][1]['acknowledged'])
                and same(state.draft.forms[matches[0][0][0]].get('file_values', {}).get(path), matches[0][1]['acknowledged']))
    except (Blocked, ValueError, TypeError, AttributeError, KeyError):
        return False


def grant_upload(browser, request, key):
    state = getattr(browser, 'ashby_attachment_state', None)
    if state is None or getattr(browser, 'attempted', False) or request.method != 'POST' or not _upload_url(request.url):
        return False
    state.decode_pending()
    content_type = (getattr(request, 'headers', None) or {}).get('content-type', '')
    body = getattr(request, 'post_data_buffer', None) or b''
    if not content_type.startswith('multipart/form-data;') or re.search(r'[\r\n]', content_type) or not 0 < len(body) <= 32 * 1024 * 1024:
        return False
    try:
        message = BytesParser(policy=policy.default).parsebytes(b'Content-Type: ' + content_type.encode('ascii') + b'\r\n\r\n' + body)
        boundary = message.get_boundary()
        if message.defects or not isinstance(boundary, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', boundary):
            return False
        parts = list(message.iter_parts()); names = [part.get_param('name', header='content-disposition') for part in parts]
        if len(names) != len(set(names)) or names.count('file') != 1:
            return False
        fields = {}
        for name, part in zip(names, parts):
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name)
                    or part.defects or part.is_multipart() or part.get_content_disposition() != 'form-data'
                    or part.get('Content-Transfer-Encoding') is not None
                    or len(part.keys()) != len({k.casefold() for k in part.keys()})
                    or set(k.casefold() for k in part.keys()) - {'content-disposition', 'content-type'}):
                return False
            if name != 'file':
                if part.get_filename() is not None:
                    return False
                fields[name] = part.get_payload(decode=True).decode('utf-8')
        file = parts[names.index('file')]
        matches = [(handle, value) for handle, value in state.handles.items()
                   if not value['upload_used'] and fields == {'Content-Type': 'application/pdf', **value['fields']}
                   and file.get_filename() == value['filename']]
        if len(matches) != 1 or file.get_content_type() != 'application/pdf':
            return False
        handle, value = matches[0]; blob = browser.upload_payloads.get(value['hash'])
        if (not blob or hashlib.sha256(blob).hexdigest() != value['hash']
                or file.get_payload(decode=True) not in (b'', blob) or boundary.encode() in blob):
            return False
        payload = b''
        for name in names:
            payload += b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="' + name.encode() + b'"'
            if name == 'file':
                payload += b'; filename="' + value['filename'].encode() + b'"\r\nContent-Type: application/pdf\r\n\r\n' + blob
            else:
                payload += b'\r\n\r\n' + fields[name].encode()
            payload += b'\r\n'
        payload += b'--' + boundary.encode() + b'--\r\n'
        state.authorize()
        value['upload_used'] = True
        upload = {'handle': handle, 'keys': {key, (request.url, request.method, hashlib.sha256(payload).hexdigest())}}
        for alias in upload['keys']:
            state.uploads[alias] = upload
        return {'post_data': payload}
    except (ValueError, TypeError, AttributeError, KeyError, UnicodeError):
        return False
