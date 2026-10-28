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


def setup_native(store, *, extra_questions=None, extra_fields=None, documents=None, region='us'):
    canonical = 'boards.eu.greenhouse.io' if region == 'eu' else 'boards.greenhouse.io'
    host = 'job-' + canonical
    url = 'https://' + host + '/acme/jobs/123'
    job = {'id': digest(url), 'url': url, 'host': host, 'company': 'Acme',
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
             'submitPath': 'https://' + canonical + '/acme/jobs/123',
             'confirmationPath': '/acme/jobs/123/confirmation',
             'jobPost': {'public_url': url, 'fingerprint': 'a' * 40, 'questions': questions}}
    state = {'url': url, 'timezone': 'America/Los_Angeles', 'loaders': {
        'root': {'internal': False, 'preview': False, 'urlToken': 'acme',
                 'ENV': {'JBEN_URL': 'https://' + canonical}}, ROUTE: route}}
    browser = SimpleNamespace(store=store, current_host=job['host'], page=SimpleNamespace(url=url, evaluate=lambda _: state),
                              greenhouse_uploads={}, greenhouse_upload_fields={}, greenhouse_native_options={},
                              uploaded_files=set(), upload_payloads={}, write_boundary=None, attempted=False)
    for document in documents:
        name = document['field']['ref']['id']
        upload_host = ('grnhse-prod-jben-eu-west-1.s3.eu-west-1.amazonaws.com' if region == 'eu'
                       else 'grnhse-prod-jben-us-east-1.s3.us-east-1.amazonaws.com')
        config = {'url': 'https://' + upload_host,
                  'key': 'applications/'+name+'.pdf', 'fields': {'policy': 'verified-policy'}}
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


@pytest.mark.parametrize('region', ['us', 'eu'])
@pytest.mark.parametrize('public_host', ['canonical', 'renderer'])
@pytest.mark.parametrize('query', ['', '?gh_jid=123'])
def test_public_posting_identity_accepts_only_same_region_host_alias_and_matching_tracking_id(store, region, public_host, query):
    browser, job, package, aid, state, _ = setup_native(store, region=region)
    canonical = 'boards.eu.greenhouse.io' if region == 'eu' else 'boards.greenhouse.io'
    host = canonical if public_host == 'canonical' else job['host']
    state['loaders'][ROUTE]['jobPost']['public_url'] = 'https://' + host + '/acme/jobs/123' + query
    grant = GreenhouseSubmission(browser, job, package, aid)
    assert grant.submit_url == 'https://' + canonical + '/acme/jobs/123'
    assert not grant.used
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('url', [
    'https://boards.eu.greenhouse.io/acme/jobs/123?gh_jid=123',
    'https://job-boards.eu.greenhouse.io/acme/jobs/123',
    'https://unreviewed.example.invalid/acme/jobs/123?gh_jid=123',
    'http://boards.greenhouse.io/acme/jobs/123?gh_jid=123',
    'https://user@boards.greenhouse.io/acme/jobs/123?gh_jid=123',
    'https://boards.greenhouse.io/acme/jobs/123?gh_jid=123#other',
    'https://boards.greenhouse.io/other/jobs/123?gh_jid=123',
    'https://boards.greenhouse.io/acme/jobs/456?gh_jid=123',
    'https://boards.greenhouse.io/acme/jobs/123?gh_jid=456',
    'https://boards.greenhouse.io/acme/jobs/123?gh_jid=',
    'https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&gh_jid=123',
    'https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&destination=other',
])
def test_public_posting_alias_rejects_changed_destination_identity_and_extra_parameters(store, url):
    browser, job, package, aid, state, _ = setup_native(store)
    state['loaders'][ROUTE]['jobPost']['public_url'] = url
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('state', ['prepared', 'unknown', 'awaiting_verification', 'confirmed'])
def test_adapter_update_releases_only_the_pre_submit_encoding_hold(store, job, package, monkeypatch, state):
    from hireme import job_holds
    aid = store.prepare(job, package)
    if state != 'prepared':
        store.begin_submit(aid)
        store.finish(aid, state)
    version = job_holds.ADAPTER_VERSION
    monkeypatch.setattr(job_holds, 'ADAPTER_VERSION', version - 1)
    job_holds.hold(store, job, 'unsupported_form')
    assert not job_holds.ready(store, job)
    intents = store.db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0]
    monkeypatch.setattr(job_holds, 'ADAPTER_VERSION', version)
    assert job_holds.ready(store, job) is (state == 'prepared')
    assert store.db.execute('SELECT state FROM applications WHERE id=?', (aid,)).fetchone()[0] == state
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0] == intents


@pytest.mark.parametrize('uncertain',['none','intent','unknown','application_unknown'])
def test_upload_selector_upgrade_reconsiders_only_unattempted_document_holds(store,job,package,tmp_path,monkeypatch,uncertain):
    from pypdf import PdfWriter
    from hireme.onboarding import import_resume
    from hireme import job_holds
    path=tmp_path/'transcript.pdf';pdf=PdfWriter();pdf.add_blank_page(width=612,height=792)
    with path.open('wb') as output:pdf.write(output)
    import_resume(store,path,'transcript')
    if uncertain=='application_unknown':
        aid=store.prepare(job,package)
    elif uncertain!='none':
        store.db.execute('INSERT INTO remote_draft_steps(id,job_id,tenant,step,request_hash,sources_hash,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                         ('held-upload',job['id'],'acme','upload',digest('request'),digest('sources'),uncertain,'2026-01-01','2026-01-01'))
    monkeypatch.setattr(job_holds,'ADAPTER_VERSION',32)
    job_holds.hold(store,job,'missing_answers','Attach')
    assert store.db.execute('SELECT 1 FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
    if uncertain=='application_unknown':
        store.begin_submit(aid);store.finish(aid,'unknown')
    assert not job_holds.ready(store,job)
    before=[dict(r) for r in store.db.execute('SELECT * FROM application_records')]
    monkeypatch.setattr(job_holds,'ADAPTER_VERSION',33)
    assert job_holds.ready(store,job)==(uncertain=='none')
    assert [dict(r) for r in store.db.execute('SELECT * FROM application_records')]==before


@pytest.mark.parametrize('ack',['valid','missing','unacknowledged'])
def test_custom_transcript_is_bound_to_its_native_attachment_slot(store,tmp_path,ack):
    from pypdf import PdfWriter
    from hireme.onboarding import import_resume
    path=tmp_path/'transcript.pdf';pdf=PdfWriter();pdf.add_blank_page(width=612,height=792)
    with path.open('wb') as output:pdf.write(output)
    imported=import_resume(store,path,'transcript')
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='transcript'").fetchone())
    document['field']=field(99,'question_456','Please upload a copy of your transcript',kind='file')
    resume=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    resume['field']=field(98,'resume','Resume/CV',kind='file')
    browser,job,package,aid,state,data=setup_native(store,documents=[resume,document])
    if ack=='missing':browser.greenhouse_uploads.pop('question_456')
    elif ack=='unacknowledged':browser.greenhouse_uploads['question_456']['acknowledged']=False
    if ack!='valid':
        with pytest.raises(Blocked):GreenhouseSubmission(browser,job,package,aid)
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
        return
    grant=GreenhouseSubmission(browser,job,package,aid)
    application=data['job_application']
    application['attachments']={'456_url':application.pop('question_456_url'),
                                '456_url_filename':application.pop('question_456_url_filename')}
    assert grant.expected['attachments']==application['attachments']
    assert browser.greenhouse_uploads['question_456']['hash']==imported['hash']
    store.begin_submit(aid);req,key=request(data)
    assert grant.grant(req,key)


def request(data, *, url='https://boards.greenhouse.io/acme/jobs/123', method='POST', raw=None):
    body = json.dumps(data).encode() if raw is None else raw
    req = SimpleNamespace(url=url, method=method, post_data_buffer=body,
                          headers={'content-type': 'application/json'})
    return req, (url, method, hashlib.sha256(body).hexdigest())


def embedded(browser, state):
    route = state['loaders'].pop(ROUTE)
    state['loaders']['routes/embed.job_app'] = route
    state['loaders']['root']['embedded'] = True
    route['submitPath'] = 'https://boards.greenhouse.io/embed/acme/jobs/123'
    route['confirmationPath'] = '/embed/job_app/confirmation?for=acme&token=123'
    route['jobPost']['public_url'] = 'https://careers.example.invalid/jobs/123?gh_jid=123'
    state['url'] = browser.page.url = 'https://job-boards.greenhouse.io/embed/job_app?for=acme&token=123'
    return route


def test_embedded_application_needs_one_matching_intent_ack_and_embedded_receipt(store):
    browser, job, package, aid, state, data = setup_native(store)
    route = embedded(browser, state)
    guard = GreenhouseSubmission(browser, job, package, aid)
    req, key = request(data, url=route['submitPath'])
    assert not guard.grant(req, key)
    store.begin_submit(aid)
    assert not guard.grant(*request(data))  # Standard and embedded writes differ.
    assert guard.grant(req, key)
    assert not guard.receipt_matches(state['url'])
    guard.observe(SimpleNamespace(status=200), key)
    receipt = 'https://job-boards.greenhouse.io' + route['confirmationPath']
    assert guard.receipt_matches(receipt)
    assert not guard.receipt_matches(receipt.replace('token=123', 'token=456'))
    assert not guard.grant(req, key)


@pytest.mark.parametrize('change', ['query_job', 'duplicate_job', 'unknown_query', 'tenant', 'route_job',
                                   'root', 'endpoint', 'confirmation', 'public_job', 'public_port'])
def test_embedded_identity_changes_stop_before_final_intent(store, change):
    browser, job, package, aid, state, _ = setup_native(store)
    route = embedded(browser, state)
    if change == 'query_job':
        state['url'] = state['url'].replace('token=123', 'token=456')
    elif change == 'duplicate_job':
        state['url'] += '&token=456'
    elif change == 'unknown_query':
        state['url'] += '&destination=other'
    elif change == 'tenant':
        route['urlToken'] = 'other'
    elif change == 'route_job':
        route['jobPostId'] = '456'
    elif change == 'root':
        state['loaders']['root']['embedded'] = False
    elif change == 'endpoint':
        route['submitPath'] = 'https://unreviewed.example.invalid/embed/acme/jobs/123'
    elif change == 'confirmation':
        route['confirmationPath'] = '/embed/job_app/confirmation?for=acme&token=456'
    elif change == 'public_job':
        route['jobPost']['public_url'] = route['jobPost']['public_url'].replace('123', '456')
    else:
        route['jobPost']['public_url'] = 'https://careers.example.invalid:bad/jobs/123?gh_jid=123'
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def city_setup(store):
    label = 'Test City, Region, United States'
    store.put_facts({'location': label})
    city = field(3, 'candidate-location', 'Location (City)', kind='combobox', options=[label])
    question = {'required': True, 'label': 'Candidate Location',
                'fields': [{'name': 'candidate_location', 'type': 'input_text'}]}
    values = setup_native(store, extra_questions=[question], extra_fields=[city])
    browser, _, _, _, state, _ = values
    embedded(browser, state)
    browser.page.evaluate = lambda expression, arg=None: state if arg is None else [str(value) for value in arg]
    feature = {'geometry': {'type': 'Point', 'coordinates': [-70.25, 40.5]},
               'properties': {'id': 'city-1', 'layer': 'locality', 'country': 'United States',
                              'country_code': 'US', 'label': 'Test City, RG, USA', 'region': 'Region'}}
    req = SimpleNamespace(method='GET', url='https://api-geocode-earth-proxy.greenhouse.io/v1/autocomplete?api_key=public&layers=locality%2Cborough&lang=en&text=Test')
    response = SimpleNamespace(request=req, status=200, json=lambda: {'features': [feature]})
    return values, response, feature


@pytest.mark.parametrize('layers', ['locality', 'locality%2Cborough'])
def test_city_coordinates_come_only_from_the_native_selected_city(store, layers):
    (browser, job, package, aid, state, data), response, _ = city_setup(store)
    response.request.url = response.request.url.replace('locality%2Cborough', layers)
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    observe_upload(browser, response, None)
    guard = GreenhouseSubmission(browser, job, package, aid)
    data['job_application'].update(location='Test City, Region, United States', latitude='40.5', longitude='-70.25', country_short_name='US')
    store.begin_submit(aid)
    endpoint = state['loaders']['routes/embed.job_app']['submitPath']
    data['job_application']['latitude'] = '41.5'
    assert not guard.grant(*request(data, url=endpoint))
    data['job_application']['latitude'] = '40.5'
    assert guard.grant(*request(data, url=endpoint))


@pytest.mark.parametrize('change', ['host', 'path', 'duplicate_query', 'address_layer', 'nan', 'range', 'country', 'ambiguous'])
def test_unreviewed_or_ambiguous_city_data_cannot_authorize_coordinates(store, change):
    (browser, job, package, aid, _, _), response, feature = city_setup(store)
    if change == 'host':
        response.request.url = response.request.url.replace('api-geocode-earth-proxy.greenhouse.io', 'other.example.invalid')
    elif change == 'path':
        response.request.url = response.request.url.replace('/v1/autocomplete', '/v1/reverse')
    elif change == 'duplicate_query':
        response.request.url += '&text=Other'
    elif change == 'address_layer':
        feature['properties']['layer'] = 'address'
    elif change == 'nan':
        feature['geometry']['coordinates'][1] = float('nan')
    elif change == 'range':
        feature['geometry']['coordinates'][1] = 91
    elif change == 'country':
        feature['properties']['country_code'] = 'unreviewed'
    observe_upload(browser, response, None)
    if change == 'ambiguous':
        feature['geometry']['coordinates'][1] = 41.5
        observe_upload(browser, response, None)
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def demographic_setup(store):
    store.put_facts({'gender': "I don't wish to answer"})
    choice = field(3, '77', 'Gender identity', kind='combobox', options=['Example', "I don't wish to answer"])
    values = setup_native(store, extra_fields=[choice])
    route = embedded(values[0], values[4])
    route['jobPost']['demographic_questions'] = {'questions': [{'id': 77, 'name': 'Gender identity',
        'required': True, 'answer_type': {'key': 'SINGLE_SELECT'}, 'answer_options': [
            {'id': 101, 'name': 'Example', 'free_form': False},
            {'id': 102, 'name': "I don't wish to answer", 'free_form': False, 'decline_to_answer': True}]}]}
    return values, route['jobPost']['demographic_questions']['questions'][0]


def test_explicit_demographic_opt_out_uses_the_exact_native_option_id(store):
    (browser, job, package, aid, state, data), _ = demographic_setup(store)
    guard = GreenhouseSubmission(browser, job, package, aid)
    data['job_application']['demographic_answers'] = [{'question_id': 77, 'answer_options': [{'answer_option_id': '102'}]}]
    store.begin_submit(aid)
    endpoint = state['loaders']['routes/embed.job_app']['submitPath']
    data['job_application']['demographic_answers'][0]['answer_options'][0]['answer_option_id'] = '101'
    assert not guard.grant(*request(data, url=endpoint))
    data['job_application']['demographic_answers'][0]['answer_options'][0]['answer_option_id'] = '102'
    assert guard.grant(*request(data, url=endpoint))


@pytest.mark.parametrize('change', ['question', 'label', 'choice', 'duplicate_choice', 'freeform', 'type'])
def test_changed_demographic_wording_or_choices_stop_before_intent(store, change):
    (browser, job, package, aid, _, _), question = demographic_setup(store)
    if change == 'question':
        question['id'] = 78
    elif change == 'label':
        question['name'] = 'Different disclosure'
    elif change == 'choice':
        question['answer_options'][1]['name'] = 'Publish my response'
    elif change == 'duplicate_choice':
        question['answer_options'][1]['id'] = 101
    elif change == 'freeform':
        question['answer_options'][1]['free_form'] = True
    else:
        question['answer_type']['key'] = 'UNREVIEWED'
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


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
def test_phone_uses_the_native_international_format_with_approved_digits_and_country(store, actual):
    phone = field(3, 'phone', 'Phone')
    country = field(4, 'country', 'Country*', kind='combobox', options=['United States +1'])
    question = {'required': True, 'label': 'Phone', 'fields': [{'name': 'phone', 'type': 'input_text'}]}
    browser, job, package, aid, _, data = setup_native(store, extra_fields=[phone, country], extra_questions=[question])
    browser._control = lambda _: SimpleNamespace(input_value=lambda: actual)
    if actual.startswith('+1'):
        with pytest.raises(Blocked, match='unsupported_form'):
            GreenhouseSubmission(browser, job, package, aid)
        assert store.db.execute('SELECT state FROM applications WHERE id=?', (aid,)).fetchone()[0] == 'prepared'
    else:
        guard = GreenhouseSubmission(browser, job, package, aid)
        store.begin_submit(aid)
        data['job_application']['phone'] = actual
        assert not guard.grant(*request(data))
        data['job_application']['phone'] = '+15551234567'
        assert guard.grant(*request(data))


def test_national_phone_without_an_approved_calling_code_stops_before_intent(store):
    phone = field(3, 'phone', 'Phone')
    question = {'required': True, 'label': 'Phone', 'fields': [{'name': 'phone', 'type': 'input_text'}]}
    browser, job, package, aid, _, _ = setup_native(store, extra_fields=[phone], extra_questions=[question])
    browser._control = lambda _: SimpleNamespace(input_value=lambda: '(555) 123-4567')
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_confirmed_international_phone_does_not_infer_a_country_or_keep_display_punctuation(store):
    store.put_facts({'phone': '+15551234567'})
    phone = field(3, 'phone', 'Phone')
    question = {'required': True, 'label': 'Phone', 'fields': [{'name': 'phone', 'type': 'input_text'}]}
    browser, job, package, aid, _, data = setup_native(store, extra_fields=[phone], extra_questions=[question])
    browser._control = lambda _: SimpleNamespace(input_value=lambda: '+1 555 123 4567')
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    data['job_application']['phone'] = '+15551234567'
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


@pytest.mark.parametrize('is_embedded', [False, True])
def test_education_uses_only_a_correlated_official_id_for_the_approved_school(store, is_embedded):
    store.put_facts({'school': 'University of California, Berkeley'})
    school = field(3, 'school--0', 'School', kind='combobox', options=['University of California, Berkeley'], section='education')
    browser, job, package, aid, state, data = setup_native(store, extra_fields=[school])
    if is_embedded:
        embedded(browser, state)
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    read = SimpleNamespace(url='https://boards.greenhouse.io/v1/boards/acme/education/schools?term=Berkeley&page=1', method='GET')
    response = SimpleNamespace(request=read, status=200, json=lambda: {'items': [{'id': 456, 'text': 'University of California, Berkeley'}]})
    observe_upload(browser, response, None)
    guard = GreenhouseSubmission(browser, job, package, aid)
    store.begin_submit(aid)
    data['job_application']['educations'][0]['school_name_id'] = '999'
    endpoint = state['loaders']['routes/embed.job_app' if is_embedded else ROUTE]['submitPath']
    assert not guard.grant(*request(data, url=endpoint))
    data['job_application']['educations'][0]['school_name_id'] = '456'
    assert guard.grant(*request(data, url=endpoint))


def ethnicity_helper_fixture(store, helper_value, *, race=None, required=False):
    browser, job, package, aid, state, data = setup_native(store)
    h = field(10, 'hispanic_ethnicity', 'Are you Hispanic/Latino?', kind='combobox',
              options=['Yes', 'No', 'Decline To Self Identify'])
    h['required'] = required
    fields = [(h, helper_value)]
    if race:
        fields.append((field(11, 'race', 'Race', kind='combobox',
                            options=['Asian', 'Hispanic or Latino', 'Decline To Self Identify']), race))
    for f, value in fields:
        qid = store.ask(job['id'], job['host'], f['label'], f['options'], field=f, context=job)
        store.answer_question(qid, value)
        package['answers'].append(resolve(store, job['host'], f, context=job))
        package['steps'][0]['fields'].append(f)
    aid = store.prepare(job, package)
    state['loaders'][ROUTE]['jobPost']['eeoc_sections'] = [{'questions': [{
        'label': 'Race', 'required': required, 'fields': [{'name': 'race',
        'type': 'multi_value_single_select', 'values': [
            {'label': 'Asian', 'value': '2'}, {'label': 'Hispanic or Latino', 'value': '4'},
            {'label': 'Decline To Self Identify', 'value': '8'}]}]}]}]
    return browser, job, package, aid, state, data


@pytest.mark.parametrize('helper,race,expected', [
    ('No', None, None), ('No', 'Asian', '2'),
    ('Yes', None, '4'), ('Decline To Self Identify', None, '8'),
])
def test_native_ethnicity_helper_uses_only_the_renderers_race_encoding(store, helper, race, expected):
    browser, job, package, aid, state, data = ethnicity_helper_fixture(store, helper, race=race)
    guard = GreenhouseSubmission(browser, job, package, aid)
    if expected is not None:
        data['job_application']['race'] = expected
    assert 'hispanic_ethnicity' not in guard.expected
    store.begin_submit(aid)
    forged = json.loads(json.dumps(data))
    forged['job_application']['hispanic_ethnicity'] = helper
    assert not guard.grant(*request(forged))
    assert guard.grant(*request(data))


@pytest.mark.parametrize('helper,race', [('Yes', 'Asian'), ('No', 'Hispanic or Latino'),
                                      ('No', 'Decline To Self Identify'), ('Decline To Self Identify', 'Asian')])
def test_conflicting_ethnicity_and_race_choices_stay_held(store, helper, race):
    browser, job, package, aid, _, _ = ethnicity_helper_fixture(store, helper, race=race)
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('helper', ['Yes', 'Decline To Self Identify'])
@pytest.mark.parametrize('marker', ['', ' ✱', '*'])
def test_required_race_is_satisfied_only_by_an_explicit_terminal_helper_choice(store, helper, marker):
    browser, job, package, aid, _, _ = ethnicity_helper_fixture(store, helper, required=True)
    # Resolve the marked field again so its approval is bound to that label.
    answer=next(a for a in package['answers'] if a['field']['ref']['id']=='hispanic_ethnicity')
    answer['field']['label']+=marker
    if marker:
        qid=store.ask(job['id'],job['host'],answer['field']['label'],answer['field']['options'],field=answer['field'],context=job)
        store.answer_question(qid,helper)
        answer.update(resolve(store,job['host'],answer['field'],context=job))
        aid=store.prepare(job,package)
    guard = GreenhouseSubmission(browser, job, package, aid)
    assert guard.expected['race'] == ('4' if helper == 'Yes' else '8')


def test_no_ethnicity_choice_does_not_supply_a_missing_required_race(store):
    browser, job, package, aid, _, _ = ethnicity_helper_fixture(store, 'No', required=True)
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)


def test_ethnicity_helper_cannot_borrow_a_missing_or_changed_native_race_definition(store):
    browser, job, package, aid, state, _ = ethnicity_helper_fixture(store, 'Yes')
    state['loaders'][ROUTE]['jobPost']['eeoc_sections'][0]['questions'][0]['fields'][0]['values'][1]['value'] = '44'
    with pytest.raises(Blocked, match='unsupported_form'):
        GreenhouseSubmission(browser, job, package, aid)


@pytest.mark.parametrize('change',['missing_race','duplicate_race','integer_value','extra_option','duplicate_helper'])
def test_ethnicity_helper_requires_its_exact_unique_native_definition(store,change):
    browser, job, package, aid, state, _ = ethnicity_helper_fixture(store,'Yes')
    meta=state['loaders'][ROUTE]['jobPost']
    if change=='missing_race':meta['eeoc_sections']=[]
    elif change=='duplicate_race':meta['eeoc_sections']*=2
    elif change=='integer_value':meta['eeoc_sections'][0]['questions'][0]['fields'][0]['values'][1]['value']=4
    else:
        if change=='duplicate_helper':package['answers'].append(dict(package['answers'][-1]))
        else:
            f=package['answers'][-1]['field']
            f['options'].append('Other')
            qid=store.ask(job['id'],job['host'],f['label'],f['options'],field=f,context=job)
            store.answer_question(qid,'Yes')
            package['answers'][-1]=resolve(store,job['host'],f,context=job)
        aid=store.prepare(job,package)
    with pytest.raises(Blocked):GreenhouseSubmission(browser,job,package,aid)


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
