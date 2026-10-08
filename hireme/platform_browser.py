"""Platform UI adapters. Production writes require independently installed flow evidence.

Transport bindings are exact reviewed JSON/form shapes, not a blanket host grant.
An unfamiliar operation or profile mutation stops the attempt. No endpoints are
guessed from the representative UI inspection.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from .browser import Browser
from .platform_connections import (VERSION, MAX_JOBS, MAX_PAGES, configuration,
    flows, platform, profile_path, session_result)
from .util import Blocked, digest, private_dir, safe_document


def flow_signature(page, document_selectors=()):
    return digest(page.locator('input:not([type=hidden]),textarea,select,button').evaluate_all(
        "(els, selectors) => els.filter(e=>e.getClientRects().length).map(e=>({tag:e.tagName,type:e.type||'',name:e.name||'',required:e.required||false,maxlength:e.maxLength??-1,label:e.getAttribute('aria-label')||(e.tagName==='BUTTON'?e.textContent.trim().slice(0,200):[...(e.labels||[])].map(l=>l.textContent.trim()).join(' ').slice(0,200)),options:e.tagName==='SELECT'&&!selectors.some(s=>e.matches(s))?[...e.options].map(o=>[o.value,o.textContent]):[]}))", list(document_selectors)))


def validate_connection_package(store, job, package):
    connection = package['connection']
    if not isinstance(connection, dict) or connection.get('id') != job.get('connection_id') or connection.get('version') != VERSION:
        raise Blocked('connection_flow_changed')
    matching = [row for row in flows(store, connection['id'], 'submission') if row['signature'] == connection.get('signature')]
    if len(matching) != 1 or digest(matching[0]['evidence']) != connection.get('evidence_hash'):
        raise Blocked('connection_flow_changed')
    config = configuration(store, connection['id'])
    if not config['enabled'] or not config['native_apply_enabled']:
        raise Blocked('connection_disabled')
    if connection.get('profile_facts_hash') != digest(store.facts()):
        raise Blocked('connection_profile_changed')


class OperationGrant:
    """One operation, one exact request body. Unknown extra fields never pass."""
    def __init__(self, origin, spec, values):
        self.origin = origin
        self.spec = spec
        self.used = False
        self.request = None
        def bind(value):
            if isinstance(value, str) and value.startswith('$'):
                if value[1:] not in values:
                    raise Blocked('connection_protocol_review', 'A reviewed request binding is unavailable.')
                return values[value[1:]]
            if isinstance(value, list):
                return [bind(v) for v in value]
            if isinstance(value, dict):
                return {k: bind(v) for k, v in value.items()}
            return value
        self.expected = bind(spec.get('body', {}))

    def consume(self, request):
        parsed = urlsplit(request.url)
        if self.used or parsed.scheme + '://' + parsed.netloc != self.origin or parsed.path != self.spec.get('path') or parsed.query or request.method != self.spec.get('method', 'POST'):
            return False
        try:
            raw = request.post_data_buffer or b''
            if self.spec.get('encoding') == 'json':
                body = json.loads(raw)
            elif self.spec.get('encoding') == 'form':
                pairs = parse_qsl(raw.decode(), keep_blank_values=True)
                if len(dict(pairs)) != len(pairs):
                    return False
                body = dict(pairs)
            else:
                return False
            if digest(body) != digest(self.expected):
                return False
        except (ValueError, UnicodeError):
            return False
        self.used = True
        self.request = request
        return True


class PlatformBrowser(Browser):
    def __init__(self, store, connection_id, test_url=None, interactive=False):
        super().__init__(store, test_url=test_url)
        self.connection_id = connection_id
        self.spec = platform(connection_id)
        self.profile_directory = profile_path(store, connection_id)
        self.origin = (urlsplit(test_url).scheme + '://' + urlsplit(test_url).netloc) if test_url else 'https://' + self.spec['host']
        self.current_host = urlsplit(self.origin).hostname
        self.operation = None
        self.operation_response = None
        self.read_protocol = []
        self.active_flow = None
        self.interactive = interactive
        self.stopped_reason = None

    def __enter__(self):
        super().__enter__()
        self.page.remove_listener('response', self._upload_response)
        if self.interactive:
            self.context.remove_listener('page', self._popup_handler)
        self.page.on('response', self._platform_response)
        return self

    def _route(self, route):
        # An explicit sign-in window is under the applicant's control. It never
        # participates in the application worker or receives a submit grant.
        if self.interactive:
            return route.continue_()
        try:self.store.checkpoint()
        except Blocked as error:
            self.stopped_reason = error
            return route.abort()
        request = route.request
        parsed = urlsplit(request.url)
        origin = parsed.scheme + '://' + parsed.netloc
        same = origin == self.origin or (not self.test_url and self.connection_id == 'workatastartup' and origin == 'https://workatastartup.com')
        if same and request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return route.continue_()
        if same and any(OperationGrant(self.origin, spec, {}).consume(request) for spec in self.read_protocol):
            return route.continue_()
        if self.operation and self.operation.consume(request):
            return route.continue_()
        # Styles/images on reviewed static origins receive no writes or application grant.
        static = (self.active_flow or {}).get('static_origins', [])
        if origin in static and request.method == 'GET' and request.resource_type in {'stylesheet', 'image', 'font'}:
            return route.continue_()
        if same and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            self.denied_write = True
            self.store.event('connection_request_blocked', self.connection_id,
                             {'path': parsed.path, 'method': request.method})
        return route.abort()

    def _platform_response(self, response):
        if not self.operation or not self.operation.used:
            return
        request = response.request
        parsed = urlsplit(request.url)
        if request == self.operation.request:
            self.operation_response = response

    def _guard_connection(self):
        if self.stopped_reason:
            raise self.stopped_reason
        self.store.checkpoint()
        if self.denied_write:
            raise Blocked('unapproved_draft_write', 'An unrecognized platform write was blocked. Validate this flow before retrying.')
        text = self.page.locator('body').inner_text()[:100000]
        if re.search(r'captcha|verify you are human|security challenge', text, re.I):
            raise Blocked('captcha_blocked')
        return text

    def check_session(self):
        self.page.goto(self.test_url or self.spec['search_url'], wait_until='domcontentloaded')
        self._guard_connection()
        selector = ('a[href*="/application"]' if self.connection_id == 'workatastartup' else '[aria-label="Open profile options"]')
        ready = self.page.locator(selector).count() > 0 and not self.page.get_by_role('textbox', name=re.compile('password', re.I)).count()
        return ready

    def _read_flow(self, capability):
        verified = flows(self.store, self.connection_id, capability)
        if len(verified) != 1:
            raise Blocked('connection_flow_unverified', 'This platform capability needs independent flow validation.')
        evidence = verified[0]['evidence']
        self.active_flow = evidence
        self.read_protocol = evidence.get('reads', [])
        return evidence

    def discover(self):
        evidence = self._read_flow('discovery')
        if not self.check_session():
            session_result(self.store, self.connection_id, False, 'Sign in again on the worker machine.')
            raise Blocked('connection_sign_in_required')
        from .platform_connections import platform_listing
        settings = self.store.settings()
        parameters = {'q': ' '.join(settings['roles'])}
        if self.connection_id == 'workatastartup':
            parameters = {'role': 'eng', 'jobType': 'intern' if settings['seniority'] == ['internship'] else 'any'}
        base = self.test_url or self.spec['search_url']
        seen = set()
        for page_number in range(1, MAX_PAGES + 1):
            self.store.checkpoint()
            self._read_flow('discovery')
            self.page.goto(base + ('&' if '?' in base else '?') + urlencode({**parameters, 'page': page_number}), wait_until='domcontentloaded')
            self._guard_connection()
            cards = self.page.locator(evidence['card_selector'])
            if not cards.count():
                break
            added = 0
            listings = []
            for card in cards.all():
                self.store.checkpoint()
                link = card.locator(evidence['link_selector']).first
                url = urljoin(self.origin, link.get_attribute('href') or '')
                try:
                    _, _, url = platform_listing(url)
                except ValueError:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                added += 1
                title = card.locator(evidence['title_selector']).inner_text().strip()
                company = card.locator(evidence['company_selector']).inner_text().strip()
                location = card.locator(evidence['location_selector']).inner_text().strip()
                listing = {'url': url, 'company': company, 'title': title, 'location': location,
                           'description': card.inner_text()[:100000]}
                listings.append(listing)
                if len(seen) >= MAX_JOBS:
                    break
            for listing in listings:
                if flows(self.store, self.connection_id, 'inspection'):
                    try:
                        inspected, _ = self.inspect({**listing, 'id': digest(listing['url']), 'host': self.spec['host'], 'source': self.connection_id, 'connection_id':self.connection_id}, persist=False)
                        listing['description'] = inspected['description']
                        listing['requirements'] = inspected.get('requirements', [])
                        if inspected.get('external_url'):
                            listing['external_url'] = inspected['external_url']
                    except Blocked as error:
                        if error.reason in {'paused','cycle_timeout','model_budget_exhausted'}:raise
                        self.store.event('connection_inspection_held', self.connection_id, {'url':listing['url'],'reason':error.reason})
                yield listing
            if len(seen) >= MAX_JOBS:
                return
            if not added:
                break

    def inspect(self, job, persist=True):
        evidence = self._read_flow('inspection')
        target = (self.test_url if persist else urljoin(self.origin, urlsplit(job['url']).path)) if self.test_url else job['url']
        self.page.goto(target, wait_until='domcontentloaded')
        self._guard_connection()
        if not self.test_url and urlsplit(self.page.url).hostname not in {self.spec['host'], 'workatastartup.com'}:
            raise Blocked('unexpected_redirect')
        company = self.page.locator(evidence['company_selector']).inner_text().strip()
        title = self.page.locator(evidence['title_selector']).inner_text().strip()
        if self.store.company(company) != self.store.company(job['company']) or title != job['title']:
            raise Blocked('posting_changed_review', 'The platform posting no longer matches the saved company and role.')
        description = self.page.locator(evidence['posting_selector']).inner_text()[:100000]
        updated = {**job, 'description': description, 'requirements':evidence.get('requirements', [])}
        # A reviewed external link identifies the actual employer requisition.
        # Navigation or a link click never establishes application completion.
        selector = evidence.get('external_selector')
        if selector and self.page.locator(selector).count():
            links = self.page.locator(selector)
            if links.count() != 1:
                raise Blocked('connection_identity_review', 'Multiple application destinations need review.')
            destination = urljoin(self.page.url, links.get_attribute('href') or '')
            from .discovery import posting
            employer = posting(destination, job['company'], job['title'], job.get('location',''), self.connection_id, description)
            if not persist:
                return {**updated, 'external_url':employer['url']}, evidence
            from .platform_connections import import_listing
            import_listing(self.store, job['url'], job['company'], job['title'], job.get('location',''), description, external_url=destination)
            # Saved native identities cannot be silently rekeyed; import_listing
            # holds those conflicts. Fresh inspected listings can reuse the ATS.
            updated = employer
            return updated, evidence
        if not persist:
            return updated, evidence
        from .policy import eligible
        eligible(updated, self.store.settings(), self.store.facts())
        self.store.upsert_job(updated)
        return updated, evidence

    def _profile(self, evidence):
        profile = evidence.get('profile', {})
        if profile.get('facts_hash') != digest(self.store.facts()):
            raise Blocked('connection_profile_changed', 'Review your platform profile against the confirmed facts.')
        resume = self.store.db.execute("SELECT hash FROM documents WHERE kind='resume'").fetchone()
        if not resume or profile.get('resume_hash') != resume['hash']:
            raise Blocked('connection_profile_changed', 'The platform profile uses a different resume.')
        target = profile.get('url')
        expected_origin = self.origin if self.test_url else 'https://' + self.spec['host']
        if not isinstance(target, str) or urlsplit(target).scheme + '://' + urlsplit(target).netloc != expected_origin:
            raise Blocked('connection_profile_unverified')
        self.context.remove_listener('page', self._popup_handler)
        try:
            page = self.context.new_page()
        finally:
            self.context.on('page', self._popup_handler)
        try:
            page.goto(target, wait_until='domcontentloaded')
            self.store.checkpoint()
            if urlsplit(page.url).scheme + '://' + urlsplit(page.url).netloc != expected_origin:
                raise Blocked('connection_profile_unverified')
            snapshot = page.locator(profile['selector']).inner_text()
            if digest(snapshot) != profile.get('fingerprint'):
                raise Blocked('connection_profile_changed', 'Your platform profile changed. Review it before applying.')
        finally:
            page.close()
        return profile['fingerprint']

    def _selected_document(self, kind, field):
        row = self.store.db.execute('SELECT * FROM documents WHERE kind=?', (kind,)).fetchone()
        if not row:
            raise Blocked('supplied_document_required', f'Import your {kind} PDF first.')
        document = {**dict(row), 'field': field}
        data = safe_document(self.store.root / 'documents' / row['filename'], self.store.root / 'documents').read_bytes()
        if hashlib.sha256(data).hexdigest() != row['hash']:
            raise Blocked('document_tampered')
        if self.connection_id == 'handshake' and len(data) > 1024 * 1024:
            raise Blocked('document_too_large', 'Handshake documents must be at most 1 MB.')
        return document

    def prepare(self, job):
        job, inspection = self.inspect(job)
        if not job.get('connection_id'):
            return job, None, None
        config = configuration(self.store, self.connection_id)
        if not config['enabled'] or not config['native_apply_enabled']:
            raise Blocked('connection_disabled')
        preparation = self._read_flow('preparation')
        if preparation.get('requirements_selector') and digest(self.page.locator(preparation['requirements_selector']).inner_text()) != preparation.get('requirements_hash'):
            raise Blocked('connection_requirements_changed', 'The employer requirements changed. Review the posting before using this flow.')
        # Shared profiles are reviewed snapshots, never automatically overwritten.
        profile_hash = self._profile(preparation)
        self._read_flow('preparation')
        entry = preparation.get('entry_selector')
        if entry:
            if preparation.get('quick_apply'):
                # Quick Apply itself is the final action. Inspection must not click it.
                pass
            else:
                self.page.locator(entry).click()
        self._guard_connection()
        matches = [row for row in flows(self.store, self.connection_id, 'submission') if row['signature'] == flow_signature(self.page, [d['selector'] for d in row['evidence'].get('documents', [])])]
        if len(matches) != 1:
            raise Blocked('connection_flow_unverified', 'This native form has not been validated for submission.')
        evidence = matches[0]['evidence']
        signature = matches[0]['signature']
        self.active_flow = evidence
        # Consent/profile mutation controls must be absent or explicitly unchecked.
        for selector in evidence.get('profile_change_selectors', []):
            controls = self.page.locator(selector)
            if any(control.is_checked() for control in controls.all()):
                raise Blocked('connection_profile_change_review', 'Applying would change platform profile preferences. Review this on the platform.')
        fields = []; answers = []; documents = []
        resume_field = {'label': 'Resume', 'type': 'file', 'required': True, 'options': []}
        documents.append(self._selected_document('resume', resume_field)); fields.append(resume_field)
        if self.connection_id == 'workatastartup':
            from .application_artifacts import generate
            prompt = evidence.get('message_prompt', f'Share something about you, what you are looking for, and why {job["company"]} interests you. Write a concise, personal introduction for this role.')
            artifact = generate(self.store, job, 'introduction', prompt)
            provenance = json.loads(self.store.db.execute('SELECT provenance FROM application_artifacts WHERE id=?', (artifact['id'],)).fetchone()[0])
            answer = provenance['answer']
            if len(answer['value']) < 50:
                raise Blocked('answer_too_short', 'The platform requires at least 50 characters.')
            textarea = self.page.locator(evidence['message_selector'])
            textarea.fill(answer['value'])
            if textarea.input_value() != answer['value']:
                raise Blocked('field_verification_failed')
            answers.append(answer); fields.append(answer['field'])
        else:
            for requirement in evidence.get('requirements', []):
                field = {'label': requirement['label'], 'type': 'file', 'required': True, 'options': []}
                kind = requirement['kind']
                if kind == 'resume':
                    continue
                if kind == 'cover_letter':
                    from .letters import generate_cover_letter
                    from .provider import ManagedProvider
                    document = {**generate_cover_letter(self.store, job, ManagedProvider(self.store)), 'field': field}
                elif kind == 'supplemental_response':
                    from .application_artifacts import generate
                    artifact = generate(self.store, job, kind, requirement['prompt'], requirement_key=requirement['label'])
                    document = {'kind': kind, 'artifact_id': artifact['id'], 'hash': artifact['hash'], 'filename': artifact['filename'], 'field': field}
                elif kind == 'transcript':
                    document = self._selected_document(kind, field)
                else:
                    raise Blocked('supplied_document_required', requirement['label'])
                documents.append(document); fields.append(field)
            self._attach_documents(documents, evidence)
        package = {'job_id': job['id'], 'url': job['url'], 'answers': answers, 'documents': documents,
            'facts_hash': digest(self.store.facts()), 'steps': [{'fields': fields}],
            'connection': {'id': self.connection_id, 'version': VERSION, 'signature': signature,
                'evidence_hash': digest(evidence), 'profile_fingerprint': profile_hash,
                'profile_facts_hash': digest(self.store.facts()), 'kind': job.get('application_kind')}}
        self._guard_connection()
        self.aid = self.store.prepare(job, package)
        return job, package, evidence

    def _attach_documents(self, documents, evidence):
        # Upload support is exact JSON/base64 protocol when a reviewed platform
        # flow uses it; otherwise require a known acknowledged immutable handle.
        import base64
        for document in documents:
            field_spec = next((r for r in evidence.get('documents', []) if r['kind'] == document['kind']), None)
            if not field_spec:
                raise Blocked('connection_document_flow_unverified')
            saved = self.store.db.execute('SELECT remote_id FROM platform_documents WHERE connection_id=? AND hash=? AND kind=?',
                (self.connection_id, document['hash'], document['kind'])).fetchone()
            if not saved:
                upload = field_spec.get('upload')
                if not upload or upload.get('visibility') != 'private' or upload.get('immutable') is not True:
                    raise Blocked('connection_document_flow_unverified', 'This immutable private upload flow needs validation.')
                data = safe_document(self.store.root / 'documents' / document['filename'], self.store.root / 'documents').read_bytes()
                if hashlib.sha256(data).hexdigest() != document['hash'] or len(data) > 1024 * 1024:
                    raise Blocked('document_tampered')
                values = {'document': base64.b64encode(data).decode(), 'filename': document['filename'], 'kind': document['kind'], 'visibility': 'private'}
                grant = OperationGrant(self.origin, upload, values)
                self.operation = grant; self.operation_response = None
                if upload.get('kind_selector'):
                    self.page.locator(upload['kind_selector']).select_option(document['kind'])
                    if self.page.locator(upload['kind_selector']).input_value() != document['kind']:
                        raise Blocked('connection_document_flow_unverified')
                public_selector = upload.get('public_selector')
                if public_selector:
                    self.page.locator(public_selector).uncheck()
                    if self.page.locator(public_selector).is_checked():
                        raise Blocked('connection_document_visibility_review')
                # This uses the site's documented UI control. No guessed direct API request.
                self.page.locator(upload['file_selector']).set_input_files({'name': document['filename'], 'mimeType': 'application/pdf', 'buffer': data})
                self.page.locator(upload['save_selector']).click()
                self._wait_operation()
                response = self.operation_response
                if not response or not 200 <= response.status < 300:
                    raise Blocked('upload_verification_failed')
                try:
                    remote_id = str(response.json()[upload['response_id_key']])
                except (ValueError, KeyError, TypeError):
                    raise Blocked('upload_verification_failed') from None
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', remote_id):
                    raise Blocked('upload_verification_failed')
                from .util import now
                self.store.db.execute('INSERT INTO platform_documents VALUES(?,?,?,?,?)',
                    (self.connection_id, document['hash'], document['kind'], remote_id, now()))
            else:
                remote_id = saved['remote_id']
            selector = self.page.locator(field_spec['selector'])
            selector.select_option(remote_id)
            if selector.input_value() != remote_id:
                raise Blocked('upload_verification_failed')
            document['remote_id'] = remote_id
        self.operation = None

    def _wait_operation(self):
        import time
        deadline = time.monotonic() + 15
        while self.operation_response is None and time.monotonic() < deadline:
            self._guard_connection()
            self.page.wait_for_timeout(100)

    def submit(self, job, package, evidence):
        # Re-read the profile immediately before intent; return and refill the
        # exact reviewed form rather than trusting its previous DOM values.
        self._guard_connection()
        if self._profile(evidence) != package['connection']['profile_fingerprint']:
            raise Blocked('connection_profile_changed')
        if flow_signature(self.page, [d['selector'] for d in evidence.get('documents', [])]) != package['connection']['signature']:
            raise Blocked('connection_flow_changed')
        for selector in evidence.get('profile_change_selectors', []):
            if any(control.is_checked() for control in self.page.locator(selector).all()):
                raise Blocked('connection_profile_change_review')
        for answer in package['answers']:
            if self.page.locator(evidence['message_selector']).input_value() != answer['value']:
                raise Blocked('field_verification_failed')
        for document in package['documents']:
            if self.connection_id == 'handshake':
                spec = next(r for r in evidence['documents'] if r['kind'] == document['kind'])
                if self.page.locator(spec['selector']).input_value() != document.get('remote_id'):
                    raise Blocked('upload_verification_failed')
        values = {'message': package['answers'][0]['value'] if package['answers'] else '',
            'job_id': job['platform_job_id'], 'document_ids': [d['remote_id'] for d in package['documents'] if d.get('remote_id')]}
        csrf = evidence.get('csrf_selector')
        if csrf:
            values['csrf'] = self.page.locator(csrf).input_value()
        self.operation = OperationGrant(self.origin, evidence['submit'], values)
        self.operation_response = None
        self.store.begin_submit(self.aid)
        self.attempted = True
        try:
            self.page.locator(evidence['submit_selector']).click()
            self._wait_operation()
            return self.verify(evidence)
        except Exception:
            if self.store.db.execute('SELECT state FROM applications WHERE id=?', (self.aid,)).fetchone()[0] == 'submitting':
                self.store.finish(self.aid, 'unknown', 'Native submission outcome needs verification. Do not retry automatically.')
            raise
        finally:
            self.operation = None

    def verify(self, evidence):
        verified = [row for row in flows(self.store, self.connection_id, 'verification') if row['signature'] == evidence.get('verification_signature')]
        if not verified or not self.operation_response or not 200 <= self.operation_response.status < 300:
            self.store.finish(self.aid, 'unknown', 'No validated platform acknowledgement was received.')
            return 'unknown'
        try:
            receipt = self.operation_response.json()
            spec = evidence['receipt']
            confirmed = isinstance(receipt, dict) and receipt.get(spec['key']) == spec['value']
            if spec.get('job_id_key'):
                confirmed = confirmed and str(receipt.get(spec['job_id_key'])) == str(self.operation.expected.get(spec.get('request_job_id_key','job_id')))
            from .util import now
            self.store.db.execute('INSERT OR IGNORE INTO connection_receipts VALUES(?,?,?,?,?)',
                (self.aid, self.connection_id, json.dumps(receipt), digest(self.operation.expected), now()))
        except (ValueError, KeyError, TypeError):
            confirmed = False
        text = 'Introduction sent' if self.connection_id == 'workatastartup' else 'Application submitted'
        screenshot = ''
        if confirmed:
            try:
                screenshot = self.aid + '-after.jpg'
                self.page.screenshot(path=str(private_dir(self.store.root / 'screenshots') / screenshot), type='jpeg')
            except Exception:
                screenshot = ''
        self.store.finish(self.aid, 'confirmed' if confirmed else 'unknown', text if confirmed else 'Platform acknowledgement could not be verified.', screenshot)
        return 'confirmed' if confirmed else 'unknown'

    def apply(self, job, live=True):
        self.store.checkpoint()
        config = configuration(self.store, self.connection_id)
        if not config['enabled'] or not config['native_apply_enabled']:
            raise Blocked('connection_disabled')
        previous = self.store.db.execute('SELECT state FROM applications WHERE job_id=?',(job['id'],)).fetchone()
        if previous and previous[0] != 'prepared':
            raise Blocked('duplicate_or_uncertain')
        self.store._check_budget(job, self.store.settings())
        missing = self.store.missing_setup()
        if missing:
            raise Blocked('missing_fact', 'Confirm the required applicant facts and documents before preparing a native application.')
        if not all(flows(self.store, self.connection_id, capability) for capability in ('inspection', 'preparation', 'submission', 'verification')):
            raise Blocked('connection_flow_unverified', 'Native applications remain disabled until the complete flow is validated.')
        if not self.check_session():
            session_result(self.store, self.connection_id, False, 'Sign in again on the worker machine.')
            raise Blocked('connection_sign_in_required')
        job, package, evidence = self.prepare(job)
        if package is None:
            # Close the platform profile before the employer browser is opened.
            raise Blocked('connection_external_routed', 'This listing uses an employer site. Review its saved employer requisition in Jobs.')
        if not live:
            return 'prepared'
        return self.submit(job, package, evidence)


class ConnectedBrowser:
    """One active browser profile at a time, sharing the worker's existing lock."""
    def __init__(self, store):
        self.store = store
        self.legacy = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.legacy:
            self.legacy.__exit__(*args)

    def apply(self, job, live=True):
        connection_id = job.get('connection_id')
        if connection_id:
            if self.legacy:
                self.legacy.__exit__(); self.legacy = None
            with PlatformBrowser(self.store, connection_id) as browser:
                return browser.apply(job, live=live)
        if self.legacy is None:
            self.legacy = Browser(self.store)
            self.legacy.__enter__()
        return self.legacy.apply(job, live=live)
