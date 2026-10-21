import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.approval_boundary import ApprovalBoundary
from hireme.ashby_attachments import (ATTACH, CREATE, HOST, attached, file_values,
                                      grant_attachment, grant_upload, observe_attachment)
from hireme.ashby_drafts import AshbyDraft
from hireme.util import Blocked, digest

FIXTURES = Path(__file__).parent / 'fixtures'
QUERIES = {CREATE: (FIXTURES / 'ashby-create-file-upload-handle.graphql').read_text(),
           ATTACH: (FIXTURES / 'ashby-set-form-value-to-file.graphql').read_text()}
POSTING = '11111111-1111-4111-8111-111111111111'
RENDER = '22222222-2222-4222-8222-222222222222'
DEFINITION = '33333333-3333-4333-8333-333333333333'
PATH = '_systemfield_resume'


def setup(store, job, package):
    url = 'https://jobs.ashbyhq.com/acme/' + POSTING
    job = {**job, 'url': url, 'host': 'jobs.ashbyhq.com', 'id': digest(url)}
    store.upsert_job(job)
    package = copy.deepcopy({**package, 'url': url, 'job_id': job['id']})
    document = package['documents'][0]
    document['field'].update(ashby_path=PATH, ashby_entry_id=RENDER + '_' + PATH)
    metadata = {'path': PATH, 'title': 'Resume', 'type': 'File', 'isMany': False}
    form = {'id': RENDER, 'sourceFormDefinitionId': DEFINITION, 'errorMessages': [], 'formErrors': [],
            'sections': [{'fieldEntries': [{'id': RENDER + '_' + PATH, 'field': metadata, 'fieldValue': None}]}]}
    draft = AshbyDraft(store, job)
    draft._remember_form(form)
    browser = SimpleNamespace(store=store, ashby_draft=draft, attempted=False,
        write_boundary=ApprovalBoundary(store, job, package), ashby_attachment_state=None,
        ashby_file_handles={}, ashby_attached_files=set(), uploaded_files=set(),
        upload_payloads={document['hash']: (store.root / 'documents' / document['filename']).read_bytes()})
    return browser, package, document, form


def operation(name, **changes):
    variables = ({'organizationHostedJobsPageName': 'acme', 'fileUploadContext': 'NonUserFormEngine'} if name == CREATE else
                 {'organizationHostedJobsPageName': 'acme', 'formRenderIdentifier': RENDER,
                  'formDefinitionIdentifier': DEFINITION, 'path': PATH, 'fileHandle': 'native-handle'})
    variables.update(changes)
    data = {'operationName': name, 'query': QUERIES[name], 'variables': variables}
    request = SimpleNamespace(url='https://jobs.ashbyhq.com/api/non-user-graphql', method='POST',
                              post_data=json.dumps(data))
    key = request.url, request.method, hashlib.sha256(request.post_data.encode()).hexdigest()
    return request, data, key


def wire(request, data):
    request.post_data = json.dumps(data)
    return request.url, request.method, hashlib.sha256(request.post_data.encode()).hexdigest()


def create(browser, document):
    req, data, key = operation(CREATE, filename=document['filename'], contentType='application/pdf',
                               contentLength=len(browser.upload_payloads[document['hash']]))
    assert grant_attachment(browser, req, data, key)
    result = {'data': {'fileUploadHandle': {'handle': 'native-handle', 'url': 'https://' + HOST + '/',
               'fields': json.dumps({'key': 'native-storage-key', 'policy': 'native-policy', 'x-amz-signature': 'signature'})}}}
    response = SimpleNamespace(request=req, status=200, json=lambda: result)
    observe_attachment(browser, response, key)
    return req, data, key, response


def multipart(document, blob=b'', *, key='native-storage-key', extra=None):
    fields = {'Content-Type': 'application/pdf', 'key': key, 'policy': 'native-policy',
              'x-amz-signature': 'signature', **(extra or {})}
    result = b''.join(b'--boundary\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n' + value.encode() + b'\r\n' for name, value in fields.items())
    return result + b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="' + document['filename'].encode() + b'"\r\nContent-Type: application/pdf\r\n\r\n' + blob + b'\r\n--boundary--\r\n'


def uploaded(browser, document):
    create(browser, document)
    raw = multipart(document)
    req = SimpleNamespace(url='https://' + HOST + '/', method='POST', post_data_buffer=raw,
                          headers={'content-type': 'multipart/form-data; boundary=boundary'})
    key = req.url, req.method, hashlib.sha256(raw).hexdigest()
    grant = grant_upload(browser, req, key)
    assert grant and browser.upload_payloads[document['hash']] in grant['post_data']
    assert not grant_upload(browser, req, key)
    observe_attachment(browser, SimpleNamespace(request=req, status=204), key)
    assert document['hash'] in browser.uploaded_files
    return req, key


def attachment_response(req, form, document):
    value = copy.deepcopy(form)
    value['sections'][0]['fieldEntries'][0]['fieldValue'] = {
        '__typename': 'File', 'id': 'native-file-id', 'filename': document['filename']}
    return SimpleNamespace(request=req, status=200, json=lambda: {'data': {'setFormValueToFile': value}}), value


def test_one_exact_native_upload_and_attachment_acknowledge_the_approved_field(store, job, package):
    browser, package, document, form = setup(store, job, package)
    uploaded(browser, document)
    req, data, key = operation(ATTACH)
    assert grant_attachment(browser, req, data, key)
    assert not grant_attachment(browser, req, data, key)
    assert not attached(browser, document)
    response, _ = attachment_response(req, form, document)
    observe_attachment(browser, response, ('other', 'POST', key[2]))
    assert not attached(browser, document)
    observe_attachment(browser, response, key)
    assert attached(browser, document)
    assert browser.ashby_draft.attachment_check()
    assert document['hash'] in browser.ashby_attached_files
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('change', ['tenant', 'render', 'definition', 'path', 'handle', 'extra_variable', 'extra_mutation'])
def test_other_attachment_targets_or_operations_are_never_granted(store, job, package, change):
    browser, _, document, _ = setup(store, job, package)
    uploaded(browser, document)
    req, data, key = operation(ATTACH)
    mapping = {'tenant': 'organizationHostedJobsPageName', 'render': 'formRenderIdentifier',
               'definition': 'formDefinitionIdentifier', 'path': 'path', 'handle': 'fileHandle'}
    if change in mapping:
        data['variables'][mapping[change]] = 'other'
    elif change == 'extra_variable':
        data['variables']['value'] = 'unapproved'
    else:
        data['query'] += '\nmutation Other { deleteApplication }'
    key = wire(req, data)
    assert not grant_attachment(browser, req, data, key)
    assert not browser.ashby_attached_files


@pytest.mark.parametrize('change', ['render', 'definition', 'entry', 'path', 'filename', 'file_type', 'errors', 'truthy_only'])
def test_unrelated_or_partial_native_ack_does_not_promote_a_global_hash(store, job, package, change):
    browser, _, document, form = setup(store, job, package)
    uploaded(browser, document)
    req, data, key = operation(ATTACH)
    assert grant_attachment(browser, req, data, key)
    response, value = attachment_response(req, form, document)
    entry = value['sections'][0]['fieldEntries'][0]
    if change == 'render': value['id'] = 'other'
    elif change == 'definition': value['sourceFormDefinitionId'] = 'other'
    elif change == 'entry': entry['id'] = 'other'
    elif change == 'path': entry['field']['path'] = 'other'
    elif change == 'filename': entry['fieldValue']['filename'] = 'other.pdf'
    elif change == 'file_type': entry['fieldValue']['__typename'] = 'JSONBox'
    elif change == 'errors': value['formErrors'] = [{'message': 'rejected'}]
    else: response.json = lambda: {'data': {'setFormValueToFile': {'id': RENDER}}}
    observe_attachment(browser, response, key)
    assert not attached(browser, document) and not browser.ashby_attached_files


def test_a_later_official_form_change_revokes_the_attachment_ack(store, job, package):
    browser, _, document, form = setup(store, job, package)
    uploaded(browser, document)
    req, data, key = operation(ATTACH)
    assert grant_attachment(browser, req, data, key)
    response, value = attachment_response(req, form, document)
    observe_attachment(browser, response, key)
    assert attached(browser, document)
    value['sections'][0]['fieldEntries'][0]['fieldValue']['id'] = 'other-file'
    browser.ashby_draft._remember_form(value)
    assert not attached(browser, document)
    with pytest.raises(Blocked, match='upload_verification_failed'):
        browser.ashby_draft.attachment_check()


@pytest.mark.parametrize('change', ['extra_mutation', 'other_context', 'other_hash', 'other_size'])
def test_handle_creation_requires_exact_reviewed_query_and_approved_document(store, job, package, change):
    browser, _, document, _ = setup(store, job, package)
    req, data, key = operation(CREATE, filename=document['filename'], contentType='application/pdf',
                               contentLength=len(browser.upload_payloads[document['hash']]))
    if change == 'extra_mutation': data['query'] += '\nmutation Other { deleteApplication }'
    elif change == 'other_context': data['variables']['fileUploadContext'] = 'CandidateFiles'
    elif change == 'other_hash': data['variables']['filename'] = 'unapproved.pdf'
    else: data['variables']['contentLength'] += 1
    key = wire(req, data)
    assert not grant_attachment(browser, req, data, key)
    assert not browser.ashby_file_handles


def test_signed_upload_cannot_borrow_another_key_or_send_other_bytes(store, job, package):
    browser, _, document, _ = setup(store, job, package)
    create(browser, document)
    for raw in (multipart(document, blob=b'unapproved'), multipart(document, key='another-key'),
                multipart(document, extra={'email': 'private@example.invalid'})):
        req = SimpleNamespace(url='https://' + HOST + '/', method='POST', post_data_buffer=raw,
                              headers={'content-type': 'multipart/form-data; boundary=boundary'})
        assert not grant_upload(browser, req, (req.url, req.method, hashlib.sha256(raw).hexdigest()))
    assert not browser.uploaded_files


def test_native_attachment_ack_is_retained_after_pause_without_authorizing_another_write(store, job, package):
    browser, _, document, form = setup(store, job, package)
    uploaded(browser, document)
    req, data, key = operation(ATTACH)
    assert grant_attachment(browser, req, data, key)
    store.update_settings({'live_enabled': False})
    response, _ = attachment_response(req, form, document)
    observe_attachment(browser, response, key)
    assert attached(browser, document)
    assert not grant_attachment(browser, req, data, key)


def test_native_file_projection_never_accepts_duplicate_or_hidden_targets():
    value = {'id': RENDER, 'sections': [{'fieldEntries': [
        {'id': RENDER + '_' + PATH, 'field': {'path': PATH},
         'fieldValue': {'__typename': 'File', 'id': 'native-file', 'filename': 'approved.pdf'}}]}]}
    assert file_values(value)[PATH] == {'id': 'native-file', 'filename': 'approved.pdf'}
    value['sections'][0]['fieldEntries'] *= 2
    assert file_values(value) == {}
