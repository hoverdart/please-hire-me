from email import policy
from email.parser import BytesParser
import hashlib
import json
from types import SimpleNamespace

import pytest

from hireme.answers import resolve
from hireme.greenhouse_submission import (GreenhouseSubmission, ROUTE, grant_upload,
                                         observe_upload)
from hireme.util import Blocked, digest


def field(index, name, label, *, kind='text', options=None, section=None):
    value = {'index': index, 'indices': [index], 'ref': {'id': name, 'name': '', 'tag': 'input'},
             'label': label, 'type': kind, 'required': True, 'options': options or [],
             'maxlength': -1, 'value': ''}
    if section:
        value['section'] = section
    return value


def setup_native(store, *, extra_questions=None, extra_fields=None, documents=None):
    url = 'https://job-boards.greenhouse.io/acme/jobs/123'
    job = {'id': digest(url), 'url': url, 'host': 'job-boards.greenhouse.io', 'company': 'Acme',
           'title': 'Software Engineer Intern Summer 2027', 'location': 'San Francisco, United States',
           'description': 'Build Python software.', 'source': 'fixture'}
    store.upsert_job(job)
    fields = [field(0, 'first_name', 'First Name'), field(1, 'last_name', 'Last Name'),
              field(2, 'email', 'Email')]
    questions = [{'required': True, 'label': f['label'], 'fields': [{'name': f['ref']['id'], 'type': 'input_text'}]}
                 for f in fields]
    fields.extend(extra_fields or [])
    questions.extend(extra_questions or [])
    if documents is None:
        document = dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
        document['field'] = field(99, 'resume', 'Resume/CV', kind='file')
        documents = [document]
    for document in documents:
        name = document['field']['ref']['id']
        questions.append({'required': True, 'label': document['field']['label'], 'fields': [{'name': name, 'type': 'input_file'}]})
    package = {'job_id': job['id'], 'url': url, 'answers': [resolve(store, job['host'], f, context=job) for f in fields],
               'documents': documents or [], 'facts_hash': digest(store.facts()), 'steps': [{'fields': fields}]}
    assert all(package['answers'])
    aid = store.prepare(job, package)
    route = {'urlToken': 'acme', 'jobPostId': '123',
             'submitPath': 'https://boards.greenhouse.io/acme/jobs/123',
             'confirmationPath': '/acme/jobs/123/confirmation',
             'jobPost': {'public_url': url, 'fingerprint': 'a' * 40, 'questions': questions}}
    state = {'url': url, 'timezone': 'America/Los_Angeles', 'loaders': {
        'root': {'internal': False, 'preview': False, 'urlToken': 'acme',
                 'ENV': {'JBEN_URL': 'https://boards.greenhouse.io'}}, ROUTE: route}}
    browser = SimpleNamespace(store=store, current_host=job['host'], page=SimpleNamespace(url=url, evaluate=lambda _: state),
                              greenhouse_uploads={}, greenhouse_upload_fields={}, greenhouse_native_options={},
                              uploaded_files=set(), upload_payloads={}, write_boundary=None, attempted=False)
    for document in documents:
        name = document['field']['ref']['id']
        config = {'url': 'https://grnhse-prod-jben-us-east-1.s3.us-east-1.amazonaws.com',
                  'key': 'applications/resume.pdf', 'fields': {'policy': 'verified-policy'}}
        browser.greenhouse_upload_fields[name] = config
        browser.greenhouse_uploads[name] = {'hash': document['hash'], 'filename': document['filename'],
            'url': config['url'] + '/' + config['key'], 'config_hash': digest(config),
            'request_keys': set(), 'response_seen': True, 'acknowledged': True}
    application = {'first_name': 'Test', 'last_name': 'Person', 'email': 'test@candidate.invalid',
        'answers_attributes': {}, 'demographic_answers': [], 'data_compliance': {}, 'attachments': {},
        'from_job_board_renderer': True,
        'educations': [{'school_name_id': None, 'degree_id': None, 'discipline_id': None,
                        'start_date': {'month': None, 'year': None}, 'end_date': {'month': None, 'year': None}}],
        'employments': [{'current': False, 'start_date': {'month': None, 'year': None},
                         'end_date': {'month': None, 'year': None}}],
        'mapped_url_token': None, 'appcast_click_id': None, 'time_zone': 'America/Los_Angeles'}
    for document in documents:
        name = document['field']['ref']['id']
        application[name + '_url'] = browser.greenhouse_uploads[name]['url']
        application[name + '_url_filename'] = document['filename']
    data = {'job_application': application, 'fingerprint': 'a' * 40, 'g-recaptcha-enterprise-token': 'native-token'}
    return browser, job, package, aid, state, data


def request(data, *, url='https://boards.greenhouse.io/acme/jobs/123', method='POST', raw=None):
    body = json.dumps(data).encode() if raw is None else raw
    req = SimpleNamespace(url=url, method=method, post_data_buffer=body,
                          headers={'content-type': 'application/json'})
    return req, (url, method, hashlib.sha256(body).hexdigest())


def test_exact_native_final_ack_requires_one_intent_and_confirmation_route(store):
    browser, job, package, aid, _, data = setup_native(store)
    guard = GreenhouseSubmission(browser, job, package, aid)
    req, key = request(data)
    assert not guard.grant(req, key)
    store.begin_submit(aid)
    assert guard.grant(req, key)
    assert not guard.grant(req, key)
    assert not guard.receipt_matches(job['url'] + '/confirmation')
    guard.observe(SimpleNamespace(request=req, status=200), ('wrong', 'POST', key[2]))
    assert not guard.response_seen
    guard.observe(SimpleNamespace(request=req, status=201), key)
    assert guard.acknowledged and guard.response_seen and not guard.failed
    assert guard.receipt_matches(job['url'] + '/confirmation')
    assert not guard.receipt_matches(job['url'] + '/confirmation?extra=1')
    assert not guard.receipt_matches('https://boards.greenhouse.io/acme/jobs/123/confirmation')
    events = [json.loads(r[0]) for r in store.db.execute("SELECT detail FROM events WHERE kind LIKE 'native_submission_%'")]
    assert events == [{'platform': 'greenhouse'}, {'platform': 'greenhouse', 'acknowledged': True}]


@pytest.mark.parametrize('change', ['wrong_job', 'other_email', 'extra_answer', 'extra_top', 'wrong_fingerprint', 'duplicate_json', 'wrong_method'])
def test_changed_native_fields_never_leave_the_browser(store, change):
    browser, job, package, aid, _, data = setup_native(store)
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    url, method, raw = 'https://boards.greenhouse.io/acme/jobs/123', 'POST', None
    if change == 'wrong_job':
        url = 'https://boards.greenhouse.io/acme/jobs/456'
    elif change == 'other_email':
        data['job_application']['email'] = 'other@example.invalid'
    elif change == 'extra_answer':
        data['job_application']['unapproved_consent'] = True
    elif change == 'extra_top':
        data['application'] = {'email': 'other@example.invalid'}
    elif change == 'wrong_fingerprint':
        data['fingerprint'] = 'b' * 40
    elif change == 'duplicate_json':
        raw = json.dumps(data)[:-1].encode() + b',"job_application":{}}'
    else:
        method = 'PUT'
    req, key = request(data, url=url, method=method, raw=raw)
    assert not guard.grant(req, key)
    assert not guard.used
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='native_submission_write'").fetchone()


@pytest.mark.parametrize('change', ['pause', 'source', 'deadline'])
def test_approval_and_last_deadline_are_checked_before_transport(store, monkeypatch, change):
    browser, job, package, aid, _, data = setup_native(store)
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    if change == 'pause':
        store.update_settings({'live_enabled': False})
    elif change == 'source':
        store.put_facts({'first_name': 'Changed'})
    else:
        original = guard.boundary.check
        def expire():
            original()
            guard.deadline = 0
        monkeypatch.setattr(guard.boundary, 'check', expire)
    req, key = request(data)
    if change == 'deadline':
        assert not guard.grant(req, key)
    else:
        with pytest.raises(Blocked):
            guard.grant(req, key)
    assert not guard.used


def test_already_sent_ack_can_be_observed_after_pause_and_source_change(store):
    browser, job, package, aid, _, data = setup_native(store)
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    req, key = request(data)
    assert guard.grant(req, key)
    store.update_settings({'live_enabled': False})
    store.put_facts({'first_name': 'Changed'})
    guard.observe(SimpleNamespace(request=req, status=200), key)
    assert guard.acknowledged and guard.receipt_matches(job['url'] + '/confirmation')


def test_official_custom_choice_encoding_is_bound_to_the_approved_label(store):
    f = field(3, 'question_99', 'Will you now or in the future require sponsorship?', kind='combobox', options=['Yes', 'No'])
    q = {'required': True, 'label': f['label'], 'fields': [{'name': 'question_99', 'type': 'multi_value_single_select',
                                     'values': [{'label': 'Yes', 'value': 1}, {'label': 'No', 'value': 0}]}]}
    browser, job, package, aid, _, data = setup_native(store, extra_fields=[f], extra_questions=[q])
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    data['job_application']['answers_attributes'] = {'99': {'question_id': '99', 'priority': 0, 'boolean_value': 1}}
    req, key = request(data)
    assert not guard.grant(req, key)
    data['job_application']['answers_attributes']['99']['boolean_value'] = 0
    assert guard.grant(*request(data))


@pytest.mark.parametrize('actual', ['(555) 123-4567', '+1 555 123 4567'])
def test_phone_freezes_only_the_native_display_with_exact_approved_digits(store, actual):
    phone = field(3, 'phone', 'Phone')
    question = {'required': True, 'label': 'Phone', 'fields': [{'name': 'phone', 'type': 'input_text'}]}
    browser, job, package, aid, _, data = setup_native(store, extra_fields=[phone], extra_questions=[question])
    browser._control = lambda _: SimpleNamespace(input_value=lambda: actual)
    if actual.startswith('+1'):
        with pytest.raises(Blocked, match='unsupported_form'):
            GreenhouseSubmission(browser, job, package, aid)
        assert store.db.execute('SELECT state FROM applications WHERE id=?', (aid,)).fetchone()[0] == 'prepared'
    else:
        guard = GreenhouseSubmission(browser, job, package, aid)
        store.begin_submit(aid)
        data['job_application']['phone'] = actual
        assert guard.grant(*request(data))


@pytest.mark.parametrize('change', ['posting', 'tenant', 'endpoint', 'fingerprint', 'unknown_field'])
def test_unsupported_loader_stops_before_intent(store, change):
    browser, job, package, aid, state, _ = setup_native(store)
    route = state['loaders'][ROUTE]
    if change == 'posting':
        route['jobPostId'] = '456'
    elif change == 'tenant':
        route['urlToken'] = 'other'
    elif change == 'endpoint':
        route['submitPath'] = 'https://attacker.invalid/acme/jobs/123'
    elif change == 'fingerprint':
        route['jobPost']['fingerprint'] = 'unreviewed'
    else:
        route['jobPost']['questions'].append({'fields': [{'name': 'unknown', 'type': 'input_text'}]})
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert store.db.execute('SELECT state FROM applications WHERE id=?', (aid,)).fetchone()[0] == 'prepared'
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def upload_setup(store):
    browser, *_ = setup_native(store)
    browser.greenhouse_uploads = {}
    browser.greenhouse_upload_fields = {}
    pdf = b'%PDF-1.4\nApproved file\x00\xff\n%%EOF'
    checksum = hashlib.sha256(pdf).hexdigest()
    browser.upload_payloads[checksum] = pdf
    url = 'https://grnhse-prod-jben-us-east-1.s3.us-east-1.amazonaws.com'
    read = SimpleNamespace(url='https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume', method='GET')
    config = {'url': url, 'resume': {'fields': {'policy': 'signed-policy', 'x-amz-signature': 'signature'},
                                    'key': 'applications/{timestamp}/{unique_id}/resume.pdf'}}
    observe_upload(browser, SimpleNamespace(request=read, status=200, json=lambda: config), None)
    values = {'utf8': '✓', 'policy': 'signed-policy', 'x-amz-signature': 'signature',
              'key': 'applications/1000000000000/abc/resume.pdf', 'authenticity_token': '1234',
              'Content-Type': 'application/octet-stream'}
    def multipart(blob=b'', filename=checksum + '.pdf', extra=None):
        fields = {**values, **(extra or {})}
        body = b''.join(b'--boundary\r\nContent-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n' + value.encode() + b'\r\n' for name, value in fields.items())
        return body + b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"\r\nContent-Type: application/pdf\r\n\r\n' + blob + b'\r\n--boundary--\r\n'
    raw = multipart()
    req = SimpleNamespace(url=url, method='POST', post_data_buffer=raw,
                          headers={'content-type': 'multipart/form-data; boundary=boundary'})
    key = (url, 'POST', hashlib.sha256(raw).hexdigest())
    return browser, req, key, pdf, checksum, multipart


def test_native_presigned_upload_rebuilds_omitted_bytes_and_binds_exact_reference(store):
    browser, req, key, pdf, checksum, _ = upload_setup(store)
    result = grant_upload(browser, req, key)
    assert result and not browser.uploaded_files
    message = BytesParser(policy=policy.default).parsebytes(b'Content-Type: ' + req.headers['content-type'].encode() + b'\r\n\r\n' + result['post_data'])
    assert list(message.iter_parts())[-1].get_payload(decode=True) == pdf
    assert not grant_upload(browser, req, key)
    rebuilt = (req.url, req.method, hashlib.sha256(result['post_data']).hexdigest())
    observe_upload(browser, SimpleNamespace(request=req, status=204), rebuilt)
    assert browser.uploaded_files == {checksum}
    record = browser.greenhouse_uploads['resume']
    assert record['acknowledged'] and record['filename'] == checksum + '.pdf'
    assert record['url'] == req.url + '/applications/1000000000000/abc/resume.pdf'


def test_native_upload_can_arrive_while_the_presign_response_body_is_being_read(store):
    browser, req, key, pdf, _, _ = upload_setup(store)
    config = browser.greenhouse_upload_fields.pop('resume')
    read = SimpleNamespace(url='https://boards.greenhouse.io/uncacheable_attributes/presigned_fields?fields%5B%5D=resume', method='GET')
    observed = []
    grants = []
    def body():
        if not observed:
            observed.append(True)
            # Playwright can dispatch the next routed request while an earlier
            # response.json() protocol read is yielding to the browser.
            grants.append(grant_upload(browser, req, key))
        return {'url': config['url'], 'resume': {'fields': config['fields'], 'key': config['key']}}
    observe_upload(browser, SimpleNamespace(request=read, status=200, json=body), None)
    assert len(grants) == 1 and pdf in grants[0]['post_data']
    assert not browser.greenhouse_upload_pending
    assert not browser.uploaded_files
    observe_upload(browser, SimpleNamespace(request=req, status=204), key)
    assert browser.greenhouse_uploads['resume']['acknowledged']


@pytest.mark.parametrize('change', ['bytes', 'filename', 'extra', 'key', 'signature', 'endpoint'])
def test_upload_grant_refuses_other_files_fields_or_presigned_scope(store, change):
    browser, req, key, pdf, _, multipart = upload_setup(store)
    if change == 'bytes':
        req.post_data_buffer = multipart(blob=pdf + b'unapproved')
    elif change == 'filename':
        req.post_data_buffer = multipart(filename='unapproved.pdf')
    elif change == 'extra':
        req.post_data_buffer = multipart(extra={'email': 'private@example.invalid'})
    elif change == 'key':
        req.post_data_buffer = multipart(extra={'key': 'other-application/resume.pdf'})
    elif change == 'signature':
        req.post_data_buffer = multipart(extra={'x-amz-signature': 'other'})
    else:
        req.url += '/other'
    assert not grant_upload(browser, req, key)
    assert not browser.greenhouse_uploads and not browser.uploaded_files


def test_upload_status_without_the_exact_granted_request_is_not_acknowledged(store):
    browser, req, key, *_ = upload_setup(store)
    observe_upload(browser, SimpleNamespace(request=req, status=204), key)
    assert not browser.uploaded_files
    assert grant_upload(browser, req, key)
    observe_upload(browser, SimpleNamespace(request=req, status=204), ('unrelated', 'POST', key[2]))
    assert not browser.uploaded_files


@pytest.mark.parametrize('change', ['missing_ack', 'changed_url', 'changed_question'])
def test_attachment_and_question_preflight_fail_before_an_intent(store, change):
    browser, job, package, aid, state, _ = setup_native(store)
    if change == 'missing_ack':
        browser.greenhouse_uploads['resume']['acknowledged'] = False
    elif change == 'changed_url':
        browser.greenhouse_upload_fields['resume']['url'] += '/other'
    else:
        state['loaders'][ROUTE]['jobPost']['questions'][0]['label'] = 'Unapproved declaration'
    with pytest.raises(Blocked):
        GreenhouseSubmission(browser, job, package, aid)
    assert store.db.execute('SELECT state FROM applications WHERE id=?', (aid,)).fetchone()[0] == 'prepared'
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_education_uses_only_a_correlated_official_id_for_the_approved_school(store):
    store.put_facts({'school': 'University of California, Berkeley'})
    school = field(3, 'school--0', 'School', kind='combobox', options=['University of California, Berkeley'], section='education')
    browser, job, package, aid, _, data = setup_native(store, extra_fields=[school])
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    read = SimpleNamespace(url='https://boards.greenhouse.io/v1/boards/acme/education/schools?term=Berkeley&page=1', method='GET')
    response = SimpleNamespace(request=read, status=200, json=lambda: {'items': [{'id': 456, 'text': 'University of California, Berkeley'}]})
    observe_upload(browser, response, None)
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    data['job_application']['educations'][0]['school_name_id'] = '999'
    assert not guard.grant(*request(data))
    data['job_application']['educations'][0]['school_name_id'] = '456'
    assert guard.grant(*request(data))


def test_one_same_application_native_email_verification_grant_after_a_delay(store):
    browser, job, package, aid, _, data = setup_native(store)
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    req, key = request(data)
    assert guard.grant(req, key)
    guard.observe(SimpleNamespace(request=req, status=422, json=lambda: {
        'code': 'captcha-failed', 'security_code_recipient': 'test@candidate.invalid'}), key)
    store.finish(aid, 'awaiting_verification')
    store.db.execute('INSERT INTO verification_challenges VALUES(?,?,?,?,?,?,?,?)',
                     (aid, 'greenhouse', job['url'], 'Acme', 0, 8, 'pending', 0))
    guard.deadline = guard.boundary.deadline = 0
    guard.prepare_verification('ABC12345')
    store.begin_verification(aid)
    verified = json.loads(json.dumps(data))
    verified.pop('g-recaptcha-enterprise-token')
    verified['security_code'] = 'ZZZ12345'
    assert not guard.grant(*request(verified))
    verified['security_code'] = 'ABC12345'
    verified_req, verified_key = request(verified)
    assert guard.grant(verified_req, verified_key)
    assert not guard.grant(verified_req, verified_key)
    store.update_settings({'live_enabled': False})
    guard.observe(SimpleNamespace(request=verified_req, status=200), verified_key)
    assert guard.receipt_matches(job['url'] + '/confirmation')
    with pytest.raises(Blocked):
        guard.prepare_verification('ABC12345')
    events = json.dumps([dict(row) for row in store.db.execute("SELECT * FROM events WHERE kind LIKE 'native_submission_%'")])
    assert 'ABC12345' not in events and 'ZZZ12345' not in events
