"""One source-bound modern Greenhouse JSON submission and native upload grant.

Reviewed standard and embedded renderers: entry.client-2CybloNd.js, SHA256
71f11e09f0995f3fed6367d6a3fe535dd7f22f24b292619e10e19f24ff8f964b;
embed.job_app-CgQ1D6ea.js, SHA256
30542f66f9ea490d54bdc45ddb843f37b937fe208e8b6f8887769db2807186ee.
Pelias city formatting: vendor-Ann2cuF6.js, SHA256
3ad72a1df8aa7ef0b61ffc534c09cc689174e47e38a491735eab005dcef2da18.
Its response.ok acknowledgement still requires the exact confirmation route and
a fresh completed-application receipt. Unknown/legacy encodings remain held.
"""
from __future__ import annotations

import calendar
from email import policy
from email.parser import BytesParser
import hashlib
import json
import math
import re
import time
from urllib.parse import parse_qsl, urljoin, urlsplit

from .approval_boundary import ApprovalBoundary
from .util import Blocked, digest

HOSTS = {'boards.greenhouse.io', 'job-boards.greenhouse.io',
         'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'}
UPLOAD_HOSTS = {'grnhse-prod-jben-us-west-2.s3.us-west-2.amazonaws.com',
                'grnhse-prod-jben-us-east-1.s3.us-east-1.amazonaws.com',
                'grnhse-prod-jben-eu-west-1.s3.eu-west-1.amazonaws.com'}
ROUTE = 'routes/$url_token_.jobs_.$job_post_id'
EMBED_ROUTE = 'routes/embed.job_app'
LOCATION_HOST = 'api-geocode-earth-proxy.greenhouse.io'
BASIC = {'first_name', 'last_name', 'preferred_name', 'email', 'phone',
         'resume_text', 'cover_letter_text'}
CONSENTS = {'gdpr_consent_given', 'gdpr_processing_consent_given',
            'gdpr_retention_consent_given', 'gdpr_demographic_data_consent_given'}


def _unsupported():
    raise Blocked('unsupported_form', 'The native Greenhouse application encoding needs review; no final submission was attempted')


def _https(url, hosts):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == 'https' and parsed.hostname in hosts and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.fragment):
            return parsed
    except (ValueError, TypeError):
        pass
    return None


def _page_identity(url):
    parsed = _https(url, HOSTS)
    if not parsed:
        return None
    match = re.fullmatch(r'/([A-Za-z0-9_-]+)/jobs/([0-9]+)', parsed.path)
    if match:
        return (*match.groups(), False)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    values = dict(pairs)
    if (parsed.path != '/embed/job_app' or len(pairs) != len(values)
            or set(values) - {'for', 'token', 't', 'gh_src', 'ccuid'}
            or not re.fullmatch(r'[A-Za-z0-9_-]+', values.get('for', ''))
            or not re.fullmatch(r'[0-9]+', values.get('token', ''))):
        return None
    return values['for'], values['token'], True


def _city(feature):
    """Only native city suggestions, using the reviewed Pelias display label."""
    if not isinstance(feature, dict):
        return None
    props, geometry = feature.get('properties'), feature.get('geometry')
    if not isinstance(props, dict) or not isinstance(geometry, dict):
        return None
    coords = geometry.get('coordinates')
    country, label, code = props.get('country'), props.get('label'), props.get('country_code')
    if (props.get('layer') not in {'locality', 'borough'} or geometry.get('type') != 'Point'
            or not isinstance(coords, list) or len(coords) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in coords)
            or not -180 <= coords[0] <= 180 or not -90 <= coords[1] <= 90
            or not all(isinstance(x, str) and 0 < len(x) <= 1000 for x in (country, label))
            or not isinstance(code, str) or not re.fullmatch(r'[A-Z]{2}', code)):
        return None
    parts = [part.strip() for part in label.split(',')]
    if len(parts) == 2:
        label = ', '.join((parts[0], country))
    elif len(parts) == 3:
        region = props.get('macroregion') if parts[1] in (props.get('macroregion'), props.get('macroregion_a')) else props.get('region')
        if not isinstance(region, str) or not region:
            return None
        label = ', '.join((parts[0], region, country))
    return label, (coords[1], coords[0], code)


def _phone(value, actual, answers):
    """The renderer sends intl-tel-input.getNumber(), not its display text."""
    approved = re.sub(r'[^0-9]', '', value)
    if (not isinstance(actual, str) or not approved or len(actual) > 100
            or not re.fullmatch(r'[+0-9 ().-]+', actual)
            or re.sub(r'[^0-9]', '', actual) != approved):
        _unsupported()
    if re.fullmatch(r'\+[0-9 ().-]+', value.strip()) and 8 <= len(approved) <= 15:
        return '+' + approved
    countries = [answer for answer in answers if _identity(answer['field']) == {'country'}]
    if (len(countries) == 1 and countries[0]['field'].get('type') == 'combobox'
            and re.search(r'\+1$', countries[0]['value'])):
        if len(approved) == 10:
            return '+1' + approved
        if len(approved) == 11 and approved.startswith('1'):
            return '+' + approved
    _unsupported()


def _identity(field):
    ref = field.get('ref') or {}
    return {value for value in (ref.get('id'), ref.get('name')) if isinstance(value, str) and value}


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_json_number')))


def _same(left, right):
    return digest(left) == digest(right)


def _native_key(template, actual):
    if (not isinstance(template, str) or not isinstance(actual, str) or not 1 <= len(actual) <= 2000
            or not re.fullmatch(r'[A-Za-z0-9_./{}-]+', template)
            or '..' in template.split('/') or template.startswith('/')
            or template.count('{timestamp}') > 1 or template.count('{unique_id}') > 1):
        return False
    pattern = re.escape(template).replace(re.escape('{timestamp}'), r'[0-9]{10,16}')
    pattern = pattern.replace(re.escape('{unique_id}'), r'[a-z0-9]{1,14}')
    return bool(re.fullmatch(pattern, actual))


def _same_upload_url(left, right):
    first, second = _https(left, UPLOAD_HOSTS), _https(right, UPLOAD_HOSTS)
    return bool(first and second and first.hostname == second.hostname
                and first.path in ('', '/') and second.path in ('', '/')
                and not first.query and not second.query)


def observe_upload(browser, response, key):
    """Remember only reviewed native reads and exact granted upload responses."""
    if browser.current_host not in HOSTS:
        return
    request = response.request
    location = _https(request.url, {LOCATION_HOST})
    if location and request.method == 'GET' and response.status == 200 and _page_identity(browser.page.url):
        pairs = parse_qsl(location.query, keep_blank_values=True)
        query = dict(pairs)
        if (location.path != '/v1/autocomplete' or len(pairs) != len(query)
                or set(query) - {'api_key', 'layers', 'lang', 'text', 'size'}
                or query.get('layers') not in {'locality', 'locality,borough'} or query.get('lang') != 'en'
                or not 0 < len(query.get('text', '')) <= 1000):
            return
        try:
            data = response.json()
            features = data.get('features') if isinstance(data, dict) else None
            if not isinstance(features, list) or len(features) > 1000:
                return
            suggestions = getattr(browser, 'greenhouse_native_locations', None)
            if suggestions is None:
                suggestions = browser.greenhouse_native_locations = {}
            for feature in features:
                city = _city(feature)
                if city:
                    label, value = city
                    suggestions.setdefault(label, set()).add(value)
        except (ValueError, TypeError, AttributeError):
            pass
        return
    parsed = _https(request.url, HOSTS)
    if parsed and request.method == 'GET' and response.status == 200:
        canonical = 'boards.eu.greenhouse.io' if '.eu.greenhouse.io' in browser.current_host else 'boards.greenhouse.io'
        if parsed.hostname != canonical:
            return
        try:
            if parsed.path == '/uncacheable_attributes/presigned_fields':
                # Reading a response body can yield to a subsequent native
                # upload route before this callback resumes. Retain that same
                # response so the paused upload can finish decoding it first.
                pending = getattr(browser, 'greenhouse_upload_pending', None)
                if pending is None:
                    pending = browser.greenhouse_upload_pending = {}
                pending[request.url] = response
            data = response.json()
            if parsed.path == '/uncacheable_attributes/presigned_fields':
                pairs = parse_qsl(parsed.query, keep_blank_values=True)
                names = [value for name, value in pairs if name == 'fields[]']
                if (not names or len(names) != len(pairs) or len(names) != len(set(names))
                        or not isinstance(data, dict) or not _https(data.get('url'), UPLOAD_HOSTS)):
                    return
                target = _https(data['url'], UPLOAD_HOSTS)
                if target.query or target.path not in ('', '/'):
                    return
                for name in names:
                    if not re.fullmatch(r'(?:resume|cover_letter|question_[0-9]+)', name):
                        return
                    value = data.get(name)
                    if not isinstance(value, dict) or not isinstance(value.get('fields'), dict):
                        return
                    fields = value['fields']
                    if (not fields or any(not isinstance(k, str) or not isinstance(v, str)
                            or re.search(r'[\r\n\x00]', k + v) or len(k) > 100 or len(v) > 20000
                            for k, v in fields.items())
                            or set(fields) & {'utf8', 'key', 'file', 'authenticity_token', 'Content-Type'}
                            or not _native_key(value.get('key'), value.get('key', '').replace('{timestamp}', '1000000000000').replace('{unique_id}', 'abc'))):
                        return
                for name in names:
                    browser.greenhouse_upload_fields[name] = {'url': data['url'], 'key': data[name]['key'],
                                                              'fields': dict(data[name]['fields'])}
                browser.greenhouse_upload_pending.pop(request.url, None)
                return
            match = re.fullmatch(r'/v1/boards/([A-Za-z0-9_-]+)/education/(schools|degrees|disciplines)', parsed.path)
            page = urlsplit(browser.page.url)
            identity = _page_identity(browser.page.url)
            if (match and page.hostname == browser.current_host and identity and identity[0] == match[1]
                    and all(name in {'term', 'page'} for name, _ in parse_qsl(parsed.query))
                    and isinstance(data, dict) and isinstance(data.get('items'), list) and len(data['items']) <= 5000):
                options = browser.greenhouse_native_options.setdefault(match[2], {})
                for item in data['items']:
                    if (not isinstance(item, dict) or type(item.get('id')) not in (int, str)
                            or not re.fullmatch(r'[0-9]+', str(item['id']))
                            or not isinstance(item.get('text'), str) or not 0 < len(item['text']) <= 1000):
                        return
                for item in data['items']:
                    options.setdefault(item['text'], set()).add(str(item['id']))
                return
        except (ValueError, TypeError, AttributeError, KeyError):
            return
    for value in browser.greenhouse_uploads.values():
        if key in value['request_keys'] and not value['response_seen']:
            value['response_seen'] = True
            value['acknowledged'] = 200 <= response.status < 300
            if value['acknowledged']:
                browser.uploaded_files.add(value['hash'])


def grant_upload(browser, request, key):
    """Rebuild Chromium's possibly byte-omitting multipart from the approved PDF."""
    if browser.current_host not in HOSTS or getattr(browser, 'attempted', False) or request.method != 'POST':
        return False
    parsed = _https(request.url, UPLOAD_HOSTS)
    body = request.post_data_buffer or b''
    content_type = request.headers.get('content-type', '')
    if (not parsed or parsed.query or parsed.path not in ('', '/') or not body or len(body) > 32 * 1024 * 1024
            or not content_type.startswith('multipart/form-data;') or re.search(r'[\r\n]', content_type)):
        return False
    for response in list(getattr(browser, 'greenhouse_upload_pending', {}).values()):
        observe_upload(browser, response, None)
    try:
        message = BytesParser(policy=policy.default).parsebytes(b'Content-Type: ' + content_type.encode('ascii') + b'\r\n\r\n' + body)
        boundary = message.get_boundary()
        if message.defects or not isinstance(boundary, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', boundary):
            return False
        parts = list(message.iter_parts())
        names = [part.get_param('name', header='content-disposition') for part in parts]
        if len(parts) != len(set(names)) or 'file' not in names or 'key' not in names:
            return False
        values = {}
        for name, part in zip(names, parts):
            if (not isinstance(name, str) or part.defects or part.is_multipart()
                    or part.get_content_disposition() != 'form-data'
                    or part.get('Content-Transfer-Encoding') is not None
                    or len(part.keys()) != len({k.casefold() for k in part.keys()})
                    or set(k.casefold() for k in part.keys()) - {'content-disposition', 'content-type'}):
                return False
            if name != 'file':
                if part.get_filename() is not None:
                    return False
                values[name] = part.get_payload(decode=True).decode('utf-8')
        candidates = [(name, config) for name, config in browser.greenhouse_upload_fields.items()
                      if _same_upload_url(config['url'], request.url) and _native_key(config['key'], values.get('key'))]
        if len(candidates) != 1:
            return False
        field, config = candidates[0]
        if field in browser.greenhouse_uploads:
            return False
        expected = {'utf8': '✓', **config['fields'], 'key': values['key'],
                    'authenticity_token': '1234', 'Content-Type': 'application/octet-stream'}
        if values != expected or set(names) != set(expected) | {'file'}:
            return False
        file = parts[names.index('file')]
        filename = file.get_filename()
        if (not isinstance(filename, str) or not re.fullmatch(r'[a-f0-9]{64}\.pdf', filename)
                or file.get_content_type() != 'application/pdf'):
            return False
        checksum = filename[:-4]
        blob = browser.upload_payloads.get(checksum)
        if (not blob or hashlib.sha256(blob).hexdigest() != checksum
                or file.get_payload(decode=True) not in (b'', blob) or boundary.encode() in blob):
            return False
        payload = b''
        for name in names:
            payload += b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="' + name.encode() + b'"'
            if name == 'file':
                payload += b'; filename="' + filename.encode() + b'"\r\nContent-Type: application/pdf\r\n\r\n' + blob
            else:
                payload += b'\r\n\r\n' + values[name].encode()
            payload += b'\r\n'
        payload += b'--' + boundary.encode() + b'--\r\n'
        if browser.write_boundary:
            browser.write_boundary.check()
        browser.greenhouse_uploads[field] = {'hash': checksum, 'filename': filename,
            'url': config['url'] + '/' + values['key'], 'config_hash': digest(config),
            'request_keys': {key, (request.url, request.method, hashlib.sha256(payload).hexdigest())},
            'response_seen': False, 'acknowledged': False}
        return {'post_data': payload}
    except (ValueError, TypeError, AttributeError, UnicodeError, KeyError):
        return False


class GreenhouseSubmission:
    def __init__(self, browser, job, package, application_id, submit=None):
        self.browser, self.store, self.job, self.aid = browser, browser.store, dict(job), application_id
        self.used = self.acknowledged = self.response_seen = self.failed = False
        self.request_key = None
        self.verification_code = None
        self.verification_used = False
        self.verification_available = False
        self.package = _json(json.dumps(package))
        self.package_hash = digest(package)
        row = self.store.db.execute('SELECT * FROM applications WHERE id=?', (self.aid,)).fetchone()
        if (not row or row['state'] != 'prepared' or row['job_id'] != job['id']
                or row['hash'] != self.package_hash or not _same(_json(row['package']), package)):
            _unsupported()
        self.deadline = time.monotonic() + 45
        self.boundary = ApprovalBoundary(self.store, job, self.package, deadline=self.deadline, application_id=application_id)
        from .answers import validate_package
        validate_package(self.store, job, self.package)
        state = browser.page.evaluate("""() => ({loaders:window.__remixContext?.state?.loaderData,
            timezone:Intl.DateTimeFormat().resolvedOptions().timeZone, url:location.href})""")
        if not isinstance(state, dict) or not isinstance(state.get('loaders'), dict):
            _unsupported()
        identity = _page_identity(state.get('url'))
        embedded = bool(identity and identity[2])
        root, route = state['loaders'].get('root'), state['loaders'].get(EMBED_ROUTE if embedded else ROUTE)
        if not isinstance(root, dict) or not isinstance(route, dict) or root.get('internal') or root.get('preview'):
            _unsupported()
        current = _https(state.get('url'), HOSTS)
        posting = _https(job['url'], HOSTS)
        tenant, identifier = route.get('urlToken'), route.get('jobPostId')
        if (not current or not posting or current.hostname != browser.current_host
                or not isinstance(tenant, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', tenant)
                or not isinstance(identifier, str) or not re.fullmatch(r'[0-9]+', identifier)
                or identity != (tenant, identifier, embedded)
                or _page_identity(job['url']) != (tenant, identifier, False)
                or posting.hostname != current.hostname or root.get('urlToken') != tenant
                or bool(root.get('embedded')) != embedded or route.get('internal')):
            _unsupported()
        self.tenant, self.identifier = tenant, identifier
        self.submit_url = route.get('submitPath')
        final = _https(self.submit_url, HOSTS)
        canonical = 'boards.eu.greenhouse.io' if '.eu.greenhouse.io' in current.hostname else 'boards.greenhouse.io'
        path = '/' + tenant + '/jobs/' + identifier
        confirmation = '/embed/job_app/confirmation?for=' + tenant + '&token=' + identifier if embedded else path + '/confirmation'
        if (not final or final.hostname != canonical or final.path != ('/embed' + path if embedded else path) or final.query
                or root.get('ENV', {}).get('JBEN_URL') != 'https://' + canonical
                or route.get('confirmationPath') != confirmation):
            _unsupported()
        self.confirmation_url = urljoin(state['url'], route['confirmationPath'])
        self.metadata = route.get('jobPost')
        # The renderer may identify the same posting on its canonical boards
        # host, with gh_jid repeating the requisition. Neither grants a new
        # destination: the submit endpoint and region remain independently bound.
        public = _https(self.metadata.get('public_url'), {canonical, 'job-' + canonical}) if isinstance(self.metadata, dict) else None
        public_valid = bool(public and public.path == path
                            and parse_qsl(public.query, keep_blank_values=True) in ([], [('gh_jid', identifier)]))
        if embedded and isinstance(self.metadata, dict):
            # The public URL may name the employer's custom careers page. It
            # is identity evidence only; it never gains navigation/write access.
            raw = self.metadata.get('public_url')
            try:
                candidate = urlsplit(raw) if isinstance(raw, str) else None
                public_valid = public_valid or bool(candidate and candidate.scheme == 'https' and candidate.hostname
                    and not candidate.username and not candidate.password and candidate.port in (None, 443)
                    and not candidate.fragment and candidate.path == '/jobs/' + identifier
                    and parse_qsl(candidate.query, keep_blank_values=True) == [('gh_jid', identifier)])
            except ValueError:
                public_valid = False
        if (not isinstance(self.metadata, dict) or not public_valid
                or not isinstance(self.metadata.get('questions'), list)
                or not isinstance(self.metadata.get('fingerprint'), str)
                or not re.fullmatch(r'[a-f0-9]{40,64}', self.metadata['fingerprint'])
                or not isinstance(state.get('timezone'), str) or not 1 <= len(state['timezone']) <= 100):
            _unsupported()
        self.fingerprint = self.metadata['fingerprint']
        self.expected = self._application(state)
        self.boundary.check()

    def _application(self, state):
        answers = self.package['answers']
        consumed = set()
        def answer(name, required=False, question=None):
            found = [(i, value) for i, value in enumerate(answers) if name in _identity(value['field'])]
            if len(found) > 1 or required and not found:
                _unsupported()
            if found:
                if question is not None:
                    label = question.get('label')
                    approved = found[0][1]['field'].get('label')
                    if (not isinstance(label, str) or not isinstance(approved, str)
                            or ' '.join(label.split()).rstrip(' *✱') != ' '.join(approved.split()).rstrip(' *✱')):
                        _unsupported()
                consumed.add(found[0][0])
                return found[0][1]['value']
            return None
        def selected(question, value):
            choices = question['fields'][0].get('values', [])
            labels = [choice.get('label') for choice in choices]
            if len(labels) != len(set(labels)) or not all(isinstance(label, str) for label in labels):
                _unsupported()
            from .answers import selections
            chosen = selections(value, labels)
            if not chosen:
                _unsupported()
            result = [str(choice['value']) for choice in choices if choice['label'] in chosen]
            if len(chosen) != len(result):
                _unsupported()
            return result
        result = {'first_name': '', 'last_name': '', 'email': '', 'answers_attributes': {},
                  'demographic_answers': [], 'data_compliance': {}, 'attachments': {},
                  'from_job_board_renderer': True}
        documents = {}
        for document in self.package['documents']:
            names = _identity(document['field'])
            matches = [name for name in names if re.fullmatch(r'(?:resume|cover_letter|question_[0-9]+)', name)]
            if len(matches) != 1 or matches[0] in documents:
                _unsupported()
            name = matches[0]
            uploaded = self.browser.greenhouse_uploads.get(name)
            config = self.browser.greenhouse_upload_fields.get(name)
            if (not uploaded or not uploaded['acknowledged'] or uploaded['hash'] != document['hash']
                    or uploaded['filename'] != document['filename'] or not config
                    or uploaded['config_hash'] != digest(config)):
                raise Blocked('upload_verification_failed', 'The exact native Greenhouse attachment is not acknowledged')
            documents[name] = uploaded
        priority = 0
        for question in self.metadata['questions']:
            fields = question.get('fields')
            if not isinstance(fields, list) or not fields or not isinstance(fields[0], dict):
                _unsupported()
            name, kind = fields[0].get('name'), fields[0].get('type')
            if not isinstance(name, str):
                _unsupported()
            if name in {'resume', 'cover_letter'} or re.fullmatch(r'question_[0-9]+', name) and kind == 'input_file':
                uploaded = documents.pop(name, None)
                if not uploaded:
                    if question.get('required'):
                        _unsupported()
                    continue
                target = result if name in {'resume', 'cover_letter'} else result['attachments']
                prefix = name if target is result else name.split('_')[1]
                target[prefix + '_url'] = uploaded['url']
                target[prefix + '_url_filename'] = uploaded['filename']
                continue
            if name == 'candidate_location':
                value = answer('candidate-location', bool(question.get('required')))
                if value is not None:
                    locations = getattr(self.browser, 'greenhouse_native_locations', {}).get(value, set())
                    if kind != 'input_text' or len(locations) != 1:
                        _unsupported()
                    latitude, longitude, country = next(iter(locations))
                    # Use the renderer's Number.toString representation, not a
                    # separately inferred or rounded location.
                    coordinates = self.browser.page.evaluate('(values) => values.map(String)', [latitude, longitude])
                    if (not isinstance(coordinates, list) or len(coordinates) != 2
                            or any(not isinstance(x, str) or not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?', x)
                                   or len(x) > 40 for x in coordinates)
                            or tuple(map(float, coordinates)) != (latitude, longitude)):
                        _unsupported()
                    result.update(location=value, latitude=coordinates[0], longitude=coordinates[1], country_short_name=country)
                continue
            value = answer(name, bool(question.get('required')), question)
            if name in BASIC:
                if value:
                    if name == 'phone':
                        fields = [item['field'] for item in answers if name in _identity(item['field'])]
                        actual = self.browser._control(fields[0]).input_value()
                        value = _phone(value, actual, answers)
                    result[name] = value
                continue
            match = re.fullmatch(r'question_([0-9]+)(?:\[\])?', name)
            if not match or kind not in {'input_text', 'textarea', 'multi_value_single_select', 'multi_value_multi_select'}:
                _unsupported()
            identifier = match[1]
            entry = {'question_id': identifier, 'priority': priority}
            priority += 1
            if kind in {'input_text', 'textarea'}:
                if value is not None:
                    entry['text_value'] = value
            elif value is None:
                entry['answer_selected_options_attributes'] = {}
            else:
                values = selected(question, value)
                if kind == 'multi_value_single_select' and len(values) != 1:
                    _unsupported()
                if len(values) == 1 and values[0] in {'0', '1'} and kind == 'multi_value_single_select':
                    entry['boolean_value'] = int(values[0])
                else:
                    entry['answer_selected_options_attributes'] = {str(i): {'question_option_id': choice} for i, choice in enumerate(values)}
            result['answers_attributes'][identifier] = entry
        if documents:
            _unsupported()
        for section in self.metadata.get('eeoc_sections') or []:
            for question in section.get('questions', []):
                name = question['fields'][0]['name']
                # The reviewed renderer's So component displays an ethnicity
                # helper but writes only race: Yes -> 4, decline -> 8, and No
                # reveals the race picker. No standalone helper is transmitted.
                helpers = [(i, a) for i, a in enumerate(answers)
                           if 'hispanic_ethnicity' in _identity(a['field'])] if name == 'race' else []
                if len(helpers) > 1:
                    _unsupported()
                helper = helpers[0] if helpers else None
                helper_value = None
                if helper:
                    f = helper[1]['field']
                    def normalize(v):
                        if not isinstance(v, str):
                            _unsupported()
                        return ' '.join(v.casefold().split())
                    if (helper[0] in consumed or _identity(f) != {'hispanic_ethnicity'}
                            or normalize(f.get('label', '')).rstrip(' *✱?') != 'are you hispanic/latino'
                            or len(f.get('options', [])) != 3
                            or {normalize(v) for v in f.get('options', [])} != {'yes', 'no', 'decline to self identify'}):
                        _unsupported()
                    helper_value = normalize(helper[1]['value'])
                    if helper_value not in {'yes', 'no', 'decline to self identify'}:
                        _unsupported()
                    choices = question['fields'][0].get('values', [])
                    if (question['fields'][0].get('type') != 'multi_value_single_select'
                            or not isinstance(choices, list) or not 2 <= len(choices) <= 500
                            or not all(isinstance(v, dict) and isinstance(v.get('label'), str)
                                       and isinstance(v.get('value'), str) for v in choices)):
                        _unsupported()
                    mapping = {str(v.get('value')): normalize(v.get('label', '')) for v in choices}
                    if (mapping.get('4') != 'hispanic or latino'
                            or mapping.get('8') != 'decline to self identify'
                            or len(mapping) != len(choices)):
                        _unsupported()
                    consumed.add(helper[0])
                value = answer(name, bool(question.get('required')) and helper_value not in {'yes', 'decline to self identify'}, question)
                if value is not None:
                    values = selected(question, value)
                    if len(values) != 1:
                        _unsupported()
                    result[name] = values[0]
                if helper_value in {'yes', 'decline to self identify'}:
                    expected = '4' if helper_value == 'yes' else '8'
                    if name in result and result[name] != expected:
                        _unsupported()
                    result[name] = expected
                elif helper_value == 'no' and result.get(name) in {'4', '8'}:
                    _unsupported()
        demographics = self.metadata.get('demographic_questions')
        for question in demographics.get('questions', []) if isinstance(demographics, dict) else []:
            identifier = question.get('id')
            if type(identifier) not in (int, str) or not re.fullmatch(r'[0-9]+', str(identifier)):
                _unsupported()
            value = answer(str(identifier), bool(question.get('required')),
                           {'label': question.get('name')})
            if value is None:
                continue
            choices = question.get('answer_options')
            if not isinstance(choices, list) or not choices or not all(isinstance(x, dict) for x in choices):
                _unsupported()
            labels = [choice.get('name') for choice in choices]
            ids = [str(choice.get('id')) for choice in choices]
            if (not all(isinstance(label, str) for label in labels) or len(set(labels)) != len(labels)
                    or len(set(ids)) != len(ids) or any(not re.fullmatch(r'[0-9]+', x) for x in ids)):
                _unsupported()
            from .answers import selections
            selected = selections(value, labels)
            schema = question.get('answer_type')
            answer_type = schema.get('key') if isinstance(schema, dict) else None
            if (not selected or answer_type not in {'SINGLE_SELECT', 'MULTI_SELECT'}
                    or answer_type == 'SINGLE_SELECT' and len(selected) != 1):
                _unsupported()
            options = []
            for choice in choices:
                if choice['name'] in selected:
                    if choice.get('free_form') is not False:
                        _unsupported()
                    options.append({'answer_option_id': str(choice['id'])})
            result['demographic_answers'].append({'question_id': identifier, 'answer_options': options})
        compliance = self.metadata.get('data_compliance') or {}
        keys = set()
        if compliance.get('single_purpose_consent'):
            for flag, name in (('requires_processing_consent', 'gdpr_processing_consent_given'),
                               ('requires_retention_consent', 'gdpr_retention_consent_given')):
                if compliance.get(flag):
                    keys.add(name)
            if compliance.get('demographic_data_consent_applies') and self.metadata.get('demographic_questions'):
                keys.add('gdpr_demographic_data_consent_given')
        elif compliance.get('retention_period'):
            keys.add('gdpr_consent_given')
        for name in keys:
            value = answer(name)
            if value not in (None, 'Yes', 'No'):
                _unsupported()
            result['data_compliance'][name] = value == 'Yes'
        result['educations'] = self._history(answers, consumed, 'education')
        result['employments'] = self._history(answers, consumed, 'employment')
        # The country widget contributes only to the approved phone formatter.
        # No separate country/location value may escape in the native payload.
        for i, value in enumerate(answers):
            if _identity(value['field']) == {'country'}:
                consumed.add(i)
        if len(consumed) != len(answers):
            _unsupported()
        pairs = parse_qsl(urlsplit(state['url']).query, keep_blank_values=True)
        first = lambda name: next((value for key, value in pairs if key == name), None)
        result.update(mapped_url_token=first('t') or first('gh_src'), appcast_click_id=first('ccuid'), time_zone=state['timezone'])
        return result

    def _history(self, answers, consumed, section):
        education = section == 'education'
        result = ({'school_name_id': None, 'degree_id': None, 'discipline_id': None} if education else {'current': False})
        result.update(start_date={'month': None, 'year': None}, end_date={'month': None, 'year': None})
        for i, answer in enumerate(answers):
            field = answer['field']
            if field.get('section') != section:
                continue
            if field.get('section_entry') not in (None, 0):
                _unsupported()
            names = _identity(field)
            pattern = (r'(school|degree|discipline|start-month|start-year|end-month|end-year)--0' if education else
                       r'(company-name|title|start-date-month|start-date-year|end-date-month|end-date-year|current-role)-0(?:_1)?')
            parts = [re.fullmatch(pattern, name) for name in names]
            matches = [match[1] for match in parts if match]
            if len(set(matches)) != 1:
                _unsupported()
            name, value = matches[0], answer['value']
            consumed.add(i)
            if education and name in {'school', 'degree', 'discipline'}:
                kind = {'school': 'schools', 'degree': 'degrees', 'discipline': 'disciplines'}[name]
                identifiers = self.browser.greenhouse_native_options.get(kind, {}).get(value, set())
                if len(identifiers) != 1:
                    _unsupported()
                result[{'school': 'school_name_id', 'degree': 'degree_id', 'discipline': 'discipline_id'}[name]] = next(iter(identifiers))
            elif name in {'company-name', 'title'}:
                result['company_name' if name == 'company-name' else 'title'] = value
            elif name == 'current-role':
                if value not in {'Yes', 'No'}:
                    _unsupported()
                result['current'] = value == 'Yes'
            else:
                date = 'start_date' if name.startswith('start-') else 'end_date'
                if name.endswith('month'):
                    months = [month for month in range(1, 13) if value == calendar.month_name[month]]
                    if not months:
                        _unsupported()
                    result[date]['month'] = months[0]
                elif re.fullmatch(r'[0-9]{4}', value):
                    result[date]['year'] = value
                else:
                    _unsupported()
        if not education and result['current']:
            del result['end_date']
        return [result]

    def prepare_verification(self, code):
        row = self.store.db.execute('SELECT state FROM applications WHERE id=?', (self.aid,)).fetchone()
        if (self.verification_used or not self.used or not self.response_seen or self.acknowledged
                or not self.verification_available
                or not row or row['state'] != 'awaiting_verification'
                or not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9]{8}', code)):
            raise Blocked('verification_form_changed')
        self.deadline = time.monotonic() + 45
        self.boundary.deadline = self.deadline
        self.boundary.check()
        self.verification_code = code
        self.used = self.response_seen = self.acknowledged = self.failed = False
        self.verification_used = True

    def grant(self, request, key):
        if self.used or time.monotonic() >= self.deadline or request.url != self.submit_url or request.method != 'POST':
            return False
        content_type = request.headers.get('content-type', '')
        raw = request.post_data_buffer or b''
        if content_type.split(';', 1)[0].strip().casefold() != 'application/json' or not 0 < len(raw) <= 2 * 1024 * 1024:
            return False
        try:
            data = _json(raw)
            allowed = {'job_application', 'fingerprint', 'g-recaptcha-enterprise-token', 'request_token', 'captcha_retried', 'security_code'}
            if (not isinstance(data, dict) or set(data) - allowed or data.get('fingerprint') != self.fingerprint
                    or not _same(data.get('job_application'), self.expected)
                    or 'captcha_retried' in data and data['captcha_retried'] is not True):
                return False
            if self.verification_code is not None:
                if data.get('security_code') != self.verification_code or 'g-recaptcha-enterprise-token' in data:
                    return False
            elif 'security_code' in data:
                return False
            for name in ('g-recaptcha-enterprise-token', 'request_token'):
                if name in data and (not isinstance(data[name], str) or not 0 < len(data[name]) <= 20000):
                    return False
            row = self.store.db.execute('SELECT state,job_id,hash,package FROM applications WHERE id=?', (self.aid,)).fetchone()
            if (not row or row['state'] != 'submitting' or row['job_id'] != self.job['id']
                    or row['hash'] != self.package_hash or not _same(_json(row['package']), self.package)):
                return False
            from .answers import validate_package
            validate_package(self.store, self.job, self.package)
            self.boundary.check()
            if time.monotonic() >= self.deadline:
                return False
            self.used = True
            self.request_key = key
            self.store.event('native_submission_write', self.aid, {'platform': 'greenhouse'})
            return True
        except (ValueError, TypeError, AttributeError, KeyError):
            return False

    def observe(self, response, key):
        if not self.used or self.response_seen or key != self.request_key:
            return
        self.response_seen = True
        self.acknowledged = 200 <= response.status < 300
        self.failed = not self.acknowledged
        if not self.acknowledged and not self.verification_used:
            try:
                result = response.json()
                recipient = result.get('security_code_recipient')
                owner = self.store.facts().get('email', {}).get('value')
                self.verification_available = (result.get('code') == 'captcha-failed'
                    and isinstance(recipient, str) and isinstance(owner, str)
                    and recipient.strip().casefold() == owner.strip().casefold())
            except (ValueError, TypeError, AttributeError):
                pass
        self.store.event('native_submission_response', self.aid, {'platform': 'greenhouse', 'acknowledged': self.acknowledged})

    def receipt_matches(self, page_url):
        return self.used and self.acknowledged and page_url == self.confirmation_url
