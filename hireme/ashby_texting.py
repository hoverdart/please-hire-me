"""A separate, acknowledged SMS preference on one verified Ashby draft."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlsplit

from .util import Blocked, digest

OPERATION = 'ApiSubmitCandidateTextingConsent'
QUERY = '002ba9d8f1c5ab50e94f5d39397274bed764dc75f2687bdb053d8d90ffed1517'
PATH = '_systemfield_texting_consent'
SECTION = '_systemsection.application_metadata'
CONTEXT_KEYS = ('shouldAskForTextingConsent', 'shouldAskForWhatsAppConsent',
                'candidateTextingPrivacyPolicyUrl', 'candidateTextingTermsAndConditionsUrl',
                'legalEntityNameForTextingConsent')


def saved_consent(form):
    """The vendor reads consent from this hidden metadata entry, not Phone."""
    entries = [entry for section in form.get('sections', []) if section.get('title') == SECTION
               for entry in section.get('fieldEntries', [])
               if entry.get('field', {}).get('path') == PATH]
    if len(entries) != 1:
        return None
    entry = entries[0]
    value = entry.get('fieldValue')
    if (entry.get('id') != form.get('id', '')+'_'+PATH or not isinstance(value, dict)
            or value.get('__typename') != 'JSONBox' or not isinstance(value.get('value'), str)
            or value['value'] not in {'given', 'notGiven'}):
        return None
    return value['value']


class AshbyTexting:
    def __init__(self, draft, answer, answers, grants):
        self.draft = draft
        self.generation = draft.store.control_generation()
        self.pending = set()
        self.remaining = 4
        self.failed = False
        self.saved = False
        field = answer['field']
        path = field.get('ashby_path')
        entry = field.get('ashby_entry_id')
        context = draft.texting_contexts.get(draft.identity[0], {})
        self.context = dict(context)
        self.render = context.get('render')
        form = draft.forms.get(self.render, {})
        self.definition = form.get('definition')
        metadata = form.get('entries', {}).get(path, {})
        phone_answers = [a for a in answers if a['field'].get('ashby_path') == path
                         and a['field'].get('ashby_entry_id') == entry
                         and a['field'].get('type') == 'tel'
                         and a.get('provenance', {}).get('fact_key') == 'phone']
        normalize = lambda value: ' '.join(value.split()).casefold() if isinstance(value, str) else ''
        links = {link.get('url') for link in field.get('help_links', []) if isinstance(link, dict)}
        privacy = context.get('candidateTextingPrivacyPolicyUrl')
        terms = context.get('candidateTextingTermsAndConditionsUrl')
        entity = context.get('legalEntityNameForTextingConsent')
        fact = draft.store.facts().get('sms', {}).get('value')
        self.value = {'Yes': 'given', 'No': 'notGiven'}.get(fact)
        options = field.get('options', [])
        wire_values = field.get('option_values', [])
        # This is the reviewed single-channel, privacy-notice-only renderer.
        # A general SMS preference cannot approve additional contractual terms.
        declaration = (f'Check Yes or No to indicate your agreement to receive text message updates from {entity} '
                       'regarding your job application. Frequency may vary. Message and data rates may apply. '
                       'Reply STOP to opt out of future messaging. View our privacy policy here: Privacy Policy')
        if (context.get('shouldAskForTextingConsent') is not True
                or context.get('shouldAskForWhatsAppConsent') is not False
                or not isinstance(privacy, str) or not privacy or privacy not in links
                or terms not in (None, '')
                or not isinstance(entity, str) or not entity.strip()
                or normalize(declaration) != normalize(field.get('help_text', ''))
                or field.get('ashby_texting_consent') != 'sms'
                or field.get('type') != 'radio' or field.get('multiple')
                or field.get('label') != 'Consent to receiving text messages'
                or field.get('ref', {}).get('name') not in {'communicationConsent', 'smsConsent'}
                or len(options) != 2 or len(set(options)) != 2 or wire_values != ['given', 'notGiven']
                or options != ['Yes - I consent to receiving text messages', 'No - I do not consent to receiving text messages']
                or answer.get('provenance', {}).get('fact_key') != 'sms' or self.value is None
                or answer['value'] != options[0 if fact == 'Yes' else 1]
                or not self.render or entry != self.render+'_'+str(path)
                or metadata.get('type') != 'Phone' or len(phone_answers) != 1
                or normalize(metadata.get('title', '')) != normalize(phone_answers[0]['field']['label'])
                or (self.render, path) not in grants):
            raise Blocked('ashby_draft_unsupported', 'The separate Ashby texting preference is unverified')
        self.phone_path = path
        self.phone_metadata = dict(metadata)
        self.saved = form.get('texting_value') == self.value

    def current(self):
        form = self.draft.forms.get(self.render, {})
        return (bool(self.draft.identity) and self.draft.texting_contexts.get(self.draft.identity[0]) == self.context
                and form.get('definition') == self.definition
                and form.get('entries', {}).get(self.phone_path) == self.phone_metadata)

    @property
    def complete(self):
        return (self.saved and not self.failed and not self.pending and self.current()
                and self.draft.forms.get(self.render, {}).get('texting_value') == self.value)

    def allow(self, request, data, key):
        parsed = urlsplit(request.url)
        variables = data.get('variables')
        if (self.failed or self.remaining <= 0 or not self.current()
                or request.method != 'POST' or parsed.scheme != 'https'
                or parsed.hostname != 'jobs.ashbyhq.com' or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment
                or parsed.path != '/api/non-user-graphql'
                or parse_qsl(parsed.query, keep_blank_values=True) not in ([], [('op', OPERATION)])
                or set(data) != {'operationName', 'query', 'variables'} or data.get('operationName') != OPERATION
                or not isinstance(data.get('query'), str)
                or hashlib.sha256(re.sub(r'\s+', '', data['query']).encode()).hexdigest() != QUERY
                or not isinstance(variables, dict) or set(variables) != {
                    'organizationHostedJobsPageName', 'formRenderIdentifier', 'formDefinitionIdentifier', 'consent'}
                or not isinstance(variables['organizationHostedJobsPageName'], str)
                or variables['organizationHostedJobsPageName'].casefold() != self.draft.identity[0]
                or variables['formRenderIdentifier'] != self.render
                or variables['formDefinitionIdentifier'] != self.definition or variables['consent'] != self.value):
            return False
        self.draft.authorize_save()
        # Validation can outlive a user edit or pause; check again at the write.
        if (self.draft.store.control_generation() != self.generation
                or not self.draft.store.settings()['live_enabled']):
            raise Blocked('paused')
        if self.draft.package.get('facts_hash') != digest(self.draft.store.facts()):
            raise Blocked('facts_changed')
        if not self.current():
            return False
        self.remaining -= 1
        self.pending.add(key)
        self.draft.store.event('ashby_texting_write', self.draft.job['id'], {'value_hash': digest(self.value)})
        return True

    def observe_save(self, response, key):
        if key not in self.pending:
            return False
        self.pending.remove(key)
        self.failed = True
        try:
            result = response.json()
            form = result.get('data', {}).get('submitCandidateTextingConsent', {})
            if (200 <= response.status < 300 and not result.get('errors')
                    and form.get('id') == self.render and form.get('sourceFormDefinitionId') == self.definition
                    and not form.get('errorMessages') and not form.get('formErrors')
                    and saved_consent(form) == self.value and self.current()):
                self.draft._remember_form(form)
                self.saved = True
                self.failed = not self.current()
        except (ValueError, TypeError, AttributeError):
            pass
        self.draft.store.event('ashby_texting_saved', self.draft.job['id'], {'acknowledged': not self.failed})
        return True
