"""SMS preference transport must never share Phone's answer or final grant."""
import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.answers import resolve
from hireme.ashby_texting import OPERATION, PATH, QUERY, SECTION
from hireme.browser import Browser
from hireme.util import Blocked, digest
from tests.test_ashby_drafts import DEFINITION, RENDER, REQ, QUERY as SAVE_QUERY, draft, form
from tests.ashby_document_fixture import AshbyDocuments

NATIVE_QUERY = (Path(__file__).parent/'fixtures/ashby-submit-candidate-texting-consent.graphql').read_text()
PHONE_PATH = '66666666-6666-4666-8666-666666666666'
POLICY = 'https://example.invalid/acme/privacy'
DECLARATION = ('Check Yes or No to indicate your agreement to receive text message updates from Acme Inc. '
               'regarding your job application. Frequency may vary. Message and data rates may apply. '
               'Reply STOP to opt out of future messaging. View our privacy policy here: Privacy Policy')


def consent_form(value=None):
    result = form()
    result['sections'][0]['fieldEntries'].append({
        'id': RENDER+'_'+PHONE_PATH, 'field': {'path': PHONE_PATH, 'title': 'Phone', 'type': 'Phone'},
        'fieldValue': {'value': '5551234567'}})
    if value is not None:
        result['sections'].append({'title': SECTION, 'isHidden': True, 'fieldEntries': [{
            'id': RENDER+'_'+PATH, 'isHidden': True, 'field': {'path': PATH},
            'fieldValue': {'__typename': 'JSONBox', 'value': value}}]})
    return result


def observe(d, official):
    request = SimpleNamespace(method='POST', url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName': 'ApiJobPosting', 'query': 'query ApiJobPosting { jobPosting { id } }',
                             'variables': {'organizationHostedJobsPageName': 'acme', 'jobPostingId': REQ}}))
    posting = {'id': REQ, 'applicationForm': official, 'shouldAskForTextingConsent': True,
               'shouldAskForWhatsAppConsent': False, 'candidateTextingPrivacyPolicyUrl': POLICY,
               'candidateTextingTermsAndConditionsUrl': None, 'legalEntityNameForTextingConsent': 'Acme Inc.'}
    d.observe_posting(request, SimpleNamespace(status=200, json=lambda: {'data': {'jobPosting': posting}}))


def setup_consent(store, job, package, preference='No', restored=None):
    store.put_facts({'sms': preference})
    package = copy.deepcopy(package)
    package['facts_hash'] = digest(store.facts())
    d, _ = draft(store, job, package)
    phone = {'index': 1, 'indices': [1], 'label': 'Phone', 'type': 'tel', 'required': True,
             'options': [], 'maxlength': -1, 'value': '', 'ashby_path': PHONE_PATH,
             'ashby_entry_id': RENDER+'_'+PHONE_PATH}
    sms = {'index': 2, 'indices': [2, 3], 'ref': {'name': 'communicationConsent'},
           'label': 'Consent to receiving text messages', 'type': 'radio', 'required': True,
           'options': ['Yes - I consent to receiving text messages', 'No - I do not consent to receiving text messages'],
           'option_values': ['given', 'notGiven'], 'maxlength': -1, 'value': '',
           'ashby_path': PHONE_PATH, 'ashby_entry_id': RENDER+'_'+PHONE_PATH,
           'ashby_texting_consent': 'sms', 'help_text': DECLARATION,
           'help_links': [{'text': 'Privacy Policy', 'url': POLICY}]}
    proof = d.package
    proof['answers'].extend(resolve(store, d.job['host'], field) for field in [phone, sms])
    official = consent_form(restored)
    official['sections'][0]['fieldEntries'][1]['fieldValue'] = {'value': store.facts()['email']['value']}
    observe(d, official)
    d.enable(None, proof['answers'], proof)
    return d, proof, official


def native_request(value='notGiven'):
    data = {'operationName': OPERATION, 'query': NATIVE_QUERY, 'variables': {
        'organizationHostedJobsPageName': 'acme', 'formRenderIdentifier': RENDER,
        'formDefinitionIdentifier': DEFINITION, 'consent': value}}
    return SimpleNamespace(method='POST', url='https://jobs.ashbyhq.com/api/non-user-graphql'), data


def response(official, **changes):
    return SimpleNamespace(status=changes.get('status', 200), json=lambda: changes.get('result', {
        'data': {'submitCandidateTextingConsent': official}}))


def test_native_texting_fixture_matches_reviewed_vendor_query():
    assert hashlib.sha256(re.sub(r'\s+', '', NATIVE_QUERY).encode()).hexdigest() == QUERY


@pytest.mark.parametrize('preference,value', [('Yes', 'given'), ('No', 'notGiven')])
def test_consent_needs_its_own_correlated_hidden_value_ack(store, job, package, preference, value):
    d, _, _ = setup_consent(store, job, package, preference)
    assert d.grants[(RENDER, PHONE_PATH)]['value'] == store.facts()['phone']['value']
    assert PATH not in [path for _, path in d.grants]
    assert not d.complete
    request, data = native_request(value)
    assert d.allow(request, data, 'native-consent')
    official = consent_form(value)
    official['sections'][0]['fieldEntries'][1]['fieldValue'] = {'value': store.facts()['email']['value']}
    d.observe_save(response(official), 'other-request')
    assert not d.complete
    d.observe_save(response(official), 'native-consent')
    assert d.complete
    assert not store.db.execute("select 1 from events where kind='submit_intent'").fetchone()


@pytest.mark.parametrize('change', ['tenant', 'render', 'definition', 'consent', 'extra', 'query',
                                   'operation', 'method', 'host', 'fragment', 'url_operation'])
def test_consent_grant_rejects_another_destination_payload_or_operation(store, job, package, change):
    d, _, _ = setup_consent(store, job, package)
    request, data = native_request()
    variables = data['variables']
    if change == 'tenant': variables['organizationHostedJobsPageName'] = 'other'
    elif change == 'render': variables['formRenderIdentifier'] = REQ
    elif change == 'definition': variables['formDefinitionIdentifier'] = REQ
    elif change == 'consent': variables['consent'] = 'given'
    elif change == 'extra': variables['path'] = PHONE_PATH
    elif change == 'query': data['query'] += '\nmutation SubmitApplication { id }'
    elif change == 'operation': data['operationName'] = 'ApiSetFormValue'
    elif change == 'method': request.method = 'PUT'
    elif change == 'host': request.url = 'https://other.invalid/api/non-user-graphql'
    elif change == 'fragment': request.url += '#unreviewed'
    elif change == 'url_operation': request.url += '?op=ApiSubmitSingleApplicationFormAction'
    assert not d.allow(request, data, change)
    assert not d.texting.pending


@pytest.mark.parametrize('change', ['missing', 'value', 'render', 'definition', 'section', 'duplicate',
                                   'typename', 'errors', 'form_errors', 'http', 'malformed_value', 'phone'])
def test_consent_requires_exact_ack_and_does_not_accept_a_success_status_alone(store, job, package, change):
    d, _, _ = setup_consent(store, job, package)
    request, data = native_request()
    assert d.allow(request, data, 'consent')
    official = consent_form('notGiven')
    changes = {}
    if change == 'missing': official['sections'].pop()
    elif change == 'value': official['sections'][-1]['fieldEntries'][0]['fieldValue']['value'] = 'given'
    elif change == 'render': official['id'] = REQ
    elif change == 'definition': official['sourceFormDefinitionId'] = REQ
    elif change == 'section': official['sections'][-1]['title'] = 'Other metadata'
    elif change == 'duplicate': official['sections'].append(copy.deepcopy(official['sections'][-1]))
    elif change == 'typename': official['sections'][-1]['fieldEntries'][0]['fieldValue']['__typename'] = 'String'
    elif change == 'errors': changes['result'] = {'errors': [{'message': 'Rejected'}], 'data': {'submitCandidateTextingConsent': official}}
    elif change == 'form_errors': official['formErrors'] = [{'message': 'Invalid phone'}]
    elif change == 'http': changes['status'] = 500
    elif change == 'malformed_value': official['sections'][-1]['fieldEntries'][0]['fieldValue']['value'] = {}
    elif change == 'phone': official['sections'][0]['fieldEntries'][-1]['field']['type'] = 'String'
    d.observe_save(response(official, **changes), 'consent')
    assert d.failed and not d.complete


def test_texting_native_operation_url_and_write_ceiling(store, job, package):
    d, _, _ = setup_consent(store, job, package)
    request, data = native_request()
    request.url += '?op='+OPERATION
    for attempt in range(4):
        assert d.allow(request, data, str(attempt))
    assert not d.allow(request, data, 'extra')
    assert not d.complete


def test_preparation_cannot_use_a_previously_created_consent_grant(store, job, package):
    d, _, _ = setup_consent(store, job, package)
    d.package = None
    request, data = native_request()
    assert not d.allow(request, data, 'prepare')
    assert not d.texting.pending


@pytest.mark.parametrize('change', ['pause', 'facts', 'policy'])
def test_edits_during_validation_cannot_authorize_a_late_consent_write(store, job, package, monkeypatch, change):
    d, _, _ = setup_consent(store, job, package)
    original = d.authorize_save
    def validate_then_edit():
        original()
        if change == 'pause': store.update_settings({'live_enabled': False})
        elif change == 'facts': store.put_facts({'sms': 'Yes'})
        else: d.texting_contexts['acme']['candidateTextingPrivacyPolicyUrl'] += '/changed'
    monkeypatch.setattr(d, 'authorize_save', validate_then_edit)
    request, data = native_request()
    if change == 'policy': assert not d.allow(request, data, 'late')
    else:
        with pytest.raises(Blocked): d.allow(request, data, 'late')
    assert not d.texting.pending
    assert not store.db.execute("select 1 from events where kind='ashby_texting_write'").fetchone()


@pytest.mark.parametrize('change', ['privacy', 'body', 'whatsapp', 'wire_values', 'phone', 'no_parent', 'duplicate', 'terms', 'added_agreement'])
def test_renderer_or_policy_changes_cannot_borrow_phone_identity(store, job, package, change):
    d, proof, _ = setup_consent(store, job, package)
    sms = proof['answers'][-1]['field']
    if change == 'privacy': d.texting_contexts['acme']['candidateTextingPrivacyPolicyUrl'] += '/changed'
    elif change == 'body': d.texting_contexts['acme']['legalEntityNameForTextingConsent'] = 'Another employer'
    elif change == 'whatsapp': d.texting_contexts['acme']['shouldAskForWhatsAppConsent'] = True
    elif change == 'wire_values': sms['option_values'].reverse()
    elif change == 'phone': d.forms[RENDER]['entries'][PHONE_PATH]['type'] = 'String'
    elif change == 'no_parent': proof['answers'].pop(-2)
    elif change == 'duplicate': proof['answers'].append(copy.deepcopy(proof['answers'][-1]))
    elif change == 'terms': d.texting_contexts['acme']['candidateTextingTermsAndConditionsUrl'] = POLICY+'/terms'
    elif change == 'added_agreement': sms['help_text'] += ' You also accept binding arbitration.'
    with pytest.raises(Blocked): d.enable(None, proof['answers'], proof)


@pytest.mark.parametrize('change', ['pause', 'fact', 'company'])
def test_consent_write_rechecks_current_approval_and_limits(store, job, package, change):
    d, _, _ = setup_consent(store, job, package)
    if change == 'pause': store.update_settings({'live_enabled': False})
    elif change == 'fact': store.put_facts({'sms': 'Yes'})
    elif change == 'company': store.update_settings({'skip_companies': ['Acme']})
    request, data = native_request()
    with pytest.raises(Blocked): d.allow(request, data, 'stale')
    assert not d.texting.pending


def test_restored_consent_and_later_server_changes_are_observed(store, job, package):
    d, _, official = setup_consent(store, job, package, restored='notGiven')
    assert d.complete
    official['sections'][-1]['fieldEntries'][0]['fieldValue']['value'] = 'given'
    d._remember_form(official)
    assert not d.complete


def test_sms_declaration_and_channel_are_part_of_snapshot_approval(store):
    with Browser(store, test_url='http://127.0.0.1') as b:
        b.page.set_content('''<div data-field-path="phone" data-field-entry-id="render_phone">
          <label>Phone<input type="tel"></label>
          <div class="ashby-application-form-texting-consent-description">
            <div class="_consentBody_fixture">Updates from Acme Inc. <a href="https://example.invalid/privacy">Privacy Policy</a></div>
            <label><input type="radio" name="communicationConsent" value="given">Yes - I consent to receiving text messages</label>
            <label><input type="radio" name="communicationConsent" value="notGiven">No - I do not consent to receiving text messages</label>
            <label><input type="radio" name="whatsAppConsent" value="given">Yes - WhatsApp</label>
            <label><input type="radio" name="whatsAppConsent" value="notGiven">No - WhatsApp</label>
          </div></div>''')
        phone, sms, whatsapp = b._snapshot()
        assert sms['ashby_path'] == phone['ashby_path'] == 'phone'
        assert sms['ashby_texting_consent'] == 'sms'
        assert sms['option_values'] == ['given', 'notGiven']
        assert sms['help_text'] == 'Updates from Acme Inc. Privacy Policy'
        assert sms['help_links'] == [{'text': 'Privacy Policy', 'url': 'https://example.invalid/privacy'}]
        assert whatsapp['label'] == 'Consent to receiving WhatsApp messages'
        assert 'ashby_texting_consent' not in whatsapp


@pytest.mark.parametrize('mode,preference', [('live', 'No'), ('live', 'Yes'), ('prepare', 'No'), ('bad_ack', 'No')])
def test_browser_keeps_phone_sms_and_final_submission_separate(store, monkeypatch, mode, preference):
    monkeypatch.setattr('hireme.browser.public_host', lambda host: True)
    store.put_facts({'sms': preference})
    job = {'id': digest(REQ), 'url': f'https://jobs.ashbyhq.com/acme/{REQ}', 'host': 'jobs.ashbyhq.com',
           'company': 'Acme', 'title': 'Software Engineer Intern Summer 2027',
           'description': 'Build Python software.', 'location': 'San Francisco, United States', 'source': 'fixture'}
    store.upsert_job(job)
    documents = AshbyDocuments(store, 'acme', REQ, RENDER, DEFINITION)
    remote = {}
    consent = []
    final = []
    action = '44444444-4444-4444-8444-444444444444'

    def native_form():
        value = form(remote)
        value['sections'][0]['fieldEntries'].append({'id': RENDER+'_'+PHONE_PATH,
            'field': {'path': PHONE_PATH, 'title': 'Phone', 'type': 'Phone'},
            'fieldValue': {'value': remote[PHONE_PATH]} if PHONE_PATH in remote else None})
        value['formControls'] = [{'identifier': action, 'title': 'Submit', '__typename': 'FormControl'}]
        if consent and mode != 'bad_ack':
            value['sections'].extend(consent_form(consent[-1])['sections'][1:])
        return documents.form(value)

    final_data = {'operationName': 'ApiSubmitSingleApplicationFormAction',
        'query': (Path(__file__).parent/'fixtures/ashby-submit-single-application-form.graphql').read_text(),
        'variables': {'organizationHostedJobsPageName': 'acme', 'jobPostingId': REQ,
            'formRenderIdentifier': RENDER, 'formDefinitionIdentifier': DEFINITION, 'actionIdentifier': action,
            'recaptchaToken': 'fixture-token', 'sourceAttributionCode': None,
            'viewedAutomatedProcessingLegalNoticeRuleId': None, 'deviceFingerprint': None}}
    inputs = ''.join(f'<div data-field-path="{path}" data-field-entry-id="{RENDER}_{path}"><label>{title}'
                     f'<input id="{path}" name="{path}" type="{kind}" required></label></div>'
                     for path, title, kind in [('_systemfield_name', 'Name', 'text'), ('_systemfield_email', 'Email', 'email')])
    html = '<h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p><form>'+inputs+f'''
      <div data-field-path="{PHONE_PATH}" data-field-entry-id="{RENDER}_{PHONE_PATH}">
        <label>Phone<input id="{PHONE_PATH}" type="tel" required></label>
        <div class="ashby-application-form-texting-consent-description">
          <div class="_consentBody_fixture">{DECLARATION.removesuffix('Privacy Policy')}<a href="{POLICY}">Privacy Policy</a></div>
          <label><input type="radio" name="communicationConsent" value="given">Yes - I consent to receiving text messages</label>
          <label><input type="radio" name="communicationConsent" value="notGiven">No - I do not consent to receiving text messages</label>
        </div>
      </div>
      <label>Resume<input type="file" name="resume" required></label><button type="submit">Submit Application</button></form>
      <script>
      const endpoint='/api/non-user-graphql';const send=data=>fetch(endpoint,{{method:'POST',body:JSON.stringify(data)}});
      const base={{organizationHostedJobsPageName:'acme',formRenderIdentifier:{json.dumps(RENDER)},formDefinitionIdentifier:{json.dumps(DEFINITION)}}};
      send({{operationName:'ApiJobPosting',query:'query ApiJobPosting {{ jobPosting {{ id }} }}',variables:{{organizationHostedJobsPageName:'acme',jobPostingId:{json.dumps(REQ)}}}}});
      for(const el of document.querySelectorAll('input[type=text],input[type=email],input[type=tel]'))el.addEventListener('input',()=>{{
        send({{operationName:'ApiSetFormValue',query:{json.dumps(SAVE_QUERY)},variables:{{...base,path:el.id,value:el.value}}}}).catch(()=>{{}});
      }});
      for(const el of document.querySelectorAll('input[type=radio]'))el.addEventListener('change',()=>{{
        send({{operationName:{json.dumps(OPERATION)},query:{json.dumps(NATIVE_QUERY)},variables:{{...base,consent:el.value}}}}).catch(()=>{{}});
      }});
      document.querySelector('form').onsubmit=async e=>{{e.preventDefault();const r=await send({json.dumps(final_data)});
        if(r.ok)document.body.innerHTML='Thank you for applying. Your application was received.';
      }};
      </script>'''
    html = documents.html(html)
    with Browser(store) as b:
        class Route:
            def __init__(self, route): self.route = route; self.request = route.request
            def abort(self): self.route.abort()
            def continue_(self, **changes):
                if documents.respond(b, self.route, changes): return
                data = json.loads(self.request.post_data)
                operation = data['operationName']
                if operation == 'ApiJobPosting':
                    posting = {'id': REQ, 'applicationForm': native_form(), 'surveyForms': [],
                        'shouldAskForTextingConsent': True, 'shouldAskForWhatsAppConsent': False,
                        'candidateTextingPrivacyPolicyUrl': POLICY, 'candidateTextingTermsAndConditionsUrl': None,
                        'legalEntityNameForTextingConsent': 'Acme Inc.'}
                    result = {'data': {'jobPosting': posting}}
                elif operation == 'ApiSetFormValue':
                    assert not b.attempted
                    variables = data['variables']; remote[variables['path']] = variables['value']
                    result = {'data': {'setFormValue': native_form()}}
                elif operation == OPERATION:
                    assert not b.attempted
                    consent.append(data['variables']['consent'])
                    result = {'data': {'submitCandidateTextingConsent': native_form()}}
                else:
                    assert operation == final_data['operationName'] and b.attempted
                    assert consent == [{'Yes': 'given', 'No': 'notGiven'}[preference]]
                    assert remote[PHONE_PATH] == store.facts()['phone']['value']
                    final.append(data)
                    result = {'data': {'submitApplicationFormAction': {
                        'applicationFormResult': {'__typename': 'FormSubmitSuccess'},
                        'messages': {'blockMessageForCandidateHtml': None}}}}
                self.route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
        def serve(route):
            if route.request.method == 'GET': route.fulfill(status=200, content_type='text/html', body=html)
            else: b._route(Route(route))
        b.page.route('**/*', serve)
        if mode == 'bad_ack':
            with pytest.raises(Blocked, match='ashby_draft_save_failed'): b.apply(job)
        else:
            assert b.apply(job, live=mode != 'prepare') == ('prepared' if mode == 'prepare' else 'confirmed')
    assert len(final) == int(mode == 'live')
    assert len(consent) == int(mode != 'prepare')
    assert documents.operations == ([] if mode == 'prepare' else ['ApiCreateFileUploadHandle', 'signed-upload', 'ApiSetFormValueToFile'])
    assert store.db.execute("select count(*) from events where kind='submit_intent'").fetchone()[0] == int(mode == 'live')
