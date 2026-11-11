"""iCIMS identity and frame boundary, with a synthetic hosted multi-step application.

The fixture imitates the hosted layout: a wrapper page embedding the same
requisition's content frame, a profile step, a conditional question step, an
upload and a receipt. It is not an iCIMS server and proves no live acceptance.
"""
import hashlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from hireme import icims
from hireme.ats_adapters import adapter_for
from hireme.browser_agent import AdaptiveBrowser
from hireme.field_context import ats
from hireme.util import Blocked, application_identity, digest

TITLE = 'Software Engineer Intern Summer 2027'
JOB = '/jobs/4821/software-engineer-intern-summer-2027/job'


def test_requisition_identity_ignores_slug_frame_query_and_step():
    base = 'https://careers-acme.icims.com'
    first = application_identity(base + '/jobs/4821/software-engineer-intern/job')
    assert first == ('icims', 'careers-acme.icims.com', '4821')
    assert application_identity(base + '/jobs/4821/renamed-role/job?in_iframe=1&mobile=false') == first
    assert application_identity(base + '/jobs/4821/login') == first
    assert application_identity(base + '/jobs/4822/software-engineer-intern/job') != first
    assert application_identity('https://careers-other.icims.com/jobs/4821/software-engineer-intern/job') != first
    # Unrecognized shapes keep their exact URL identity and gain no alias.
    assert application_identity(base + '/search?ss=1')[0] == 'url'
    assert application_identity('https://icims.com.attacker.invalid/jobs/4821/x/job')[0] == 'url'
    assert ats('careers-acme.icims.com') == 'iCIMS' and ats('icims.com.attacker.invalid') == 'Other'


def test_adapter_opens_only_the_same_requisitions_content_document():
    job = {'host': 'careers-acme.icims.com', 'url': 'https://careers-acme.icims.com/jobs/4821/software-engineer-intern/job?utm=x&session=abc'}
    assert adapter_for(job).navigation(job) == ('https://careers-acme.icims.com/jobs/4821/software-engineer-intern/job?in_iframe=1', None)
    with pytest.raises(Blocked, match='icims_posting_unrecognized'):
        adapter_for({**job, 'url': 'https://careers-acme.icims.com/search'}).navigation({**job, 'url': 'https://careers-acme.icims.com/search'})


@pytest.mark.parametrize('source', [
    'https://careers-other.icims.com/jobs/4821/software-engineer-intern/job?in_iframe=1',
    'https://careers-acme.icims.com/jobs/9999/another-role/job?in_iframe=1',
    'https://careers-acme.icims.com/jobs/4821/software-engineer-intern/job',
    'http://careers-acme.icims.com/jobs/4821/software-engineer-intern/job?in_iframe=1',
    'https://user:pw@careers-acme.icims.com/jobs/4821/software-engineer-intern/job?in_iframe=1',
    'https://attacker.invalid/collect?in_iframe=1', 'javascript:alert(1)', '',
])
def test_frames_from_another_origin_or_requisition_are_refused(source):
    with pytest.raises(Blocked, match='icims_frame_boundary'):
        icims.approved_frame('https://careers-acme.icims.com/jobs/4821/software-engineer-intern/job', source)


def test_matching_frame_is_approved_without_extra_query():
    page = 'https://careers-acme.icims.com/jobs/4821/software-engineer-intern/job'
    assert icims.approved_frame(page, page + '?in_iframe=1&hashed=-1') == page + '?in_iframe=1'


@pytest.fixture
def hosted(store):
    state = {'mode': 'normal', 'records': [], 'profile': None, 'questions': None, 'file': None, 'finals': 0, 'wrappers': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, status=200, ctype='text/html'):
            raw = body.encode() if isinstance(body, str) else body
            self.send_response(status);self.send_header('Content-Type', ctype);self.end_headers();self.wfile.write(raw)

        def wrapper(self, path):
            state['wrappers'] += 1
            source = {'foreign': 'http://localhost:1/jobs/4821/x/job?in_iframe=1',
                      'other_job': '/jobs/7777/another-role/job?in_iframe=1'}.get(state['mode'], path + '?in_iframe=1')
            return self.respond(f'<div class=header>Acme Careers</div><iframe id=icims_content_iframe src="{source}"></iframe>')

        def do_GET(self):
            parsed = urlsplit(self.path);path = parsed.path
            framed = parse_qs(parsed.query).get('in_iframe') == ['1']
            if path.startswith('/jobs/') and not framed:
                return self.wrapper(path)
            if path == JOB:
                return self.respond(f'''<h1>{TITLE}</h1><h2>Acme</h2><p>Build Python and TypeScript software in San Francisco, United States.</p>
                    <a href="/jobs/4821/profile">Apply for this job online</a>''')
            if path == '/jobs/4821/profile':
                return self.respond(f'''<h1>Candidate Profile</h1><h2>{TITLE}</h2>
                    <form><label>First Name<input id=first name=first required></label>
                    <label>Last Name<input id=last name=last required></label>
                    <label>Email<input id=email name=email type=email required></label>
                    <label>Resume<input id=resume name=resume type=file required></label>
                    <button id=next type=button>Next</button></form>
                    <script>
                    document.getElementById('resume').onchange=async event=>{{
                      await fetch('/upload',{{method:'POST',body:event.target.files[0]}});}};
                    document.getElementById('next').onclick=async()=>{{
                      const result=await fetch('{'/jobs/4821/profile' if state['mode']=='profile_write' else '/save/candidate'}',{{method:'POST',body:JSON.stringify({{first:first.value,last:last.value,email:email.value}})}});
                      if(result.ok)top.location.href='/jobs/4821/questions';}};
                    </script>''')
            if path == '/jobs/4821/questions':
                extra = '<label>What is your badge number at your current employer?<input name=badge required></label>' if state['mode'] == 'unknown' else ''
                return self.respond(f'''<h1>Application Questions</h1><h2>{TITLE}</h2>
                    <form><label>Are you legally authorized to work in the United States?
                      <select id=auth name=auth required><option value="">Select</option><option>Yes</option><option>No</option></select></label>
                    <div id=follow></div>{extra}
                    <button id=submit type=button>Submit Application</button></form>
                    <script>
                    auth.onchange=()=>{{follow.innerHTML=auth.value==='Yes'?
                      '<label>Will you now or in the future require sponsorship?<select id=sponsor name=sponsor required><option value="">Select</option><option>Yes</option><option>No</option></select></label>':'';}};
                    document.getElementById('submit').onclick=async()=>{{
                      const result=await fetch('/submit',{{method:'POST',body:JSON.stringify({{auth:auth.value,sponsor:document.getElementById('sponsor')?.value}})}});
                      if(result.ok)top.location.href='/jobs/4821/confirmation';}};
                    </script>''')
            if path == '/jobs/4821/confirmation':
                return self.respond(f'<h1>Your application has been received.</h1><h2>{TITLE}</h2>')
            self.respond('Not found', 404)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            db = sqlite3.connect(store.root / 'ledger.sqlite3')
            intents = db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0];db.close()
            state['records'].append({'path': self.path, 'intents': intents})
            if self.path == '/upload':
                state['file'] = raw
                return self.respond(json.dumps({'id': 'synthetic-resume'}), ctype='application/json')
            if self.path in ('/save/candidate', '/jobs/4821/profile'):
                state['profile'] = json.loads(raw)
            if self.path == '/submit':
                state['finals'] += 1;state['questions'] = json.loads(raw)
                return self.respond(json.dumps({'status': 'submitted', 'application_id': 'synthetic-application'}), ctype='application/json')
            self.respond(json.dumps({'saved': True}), ctype='application/json')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    base = 'http://127.0.0.1:' + str(server.server_address[1])
    job = {'id': digest(base + JOB), 'url': base + JOB, 'host': '127.0.0.1', 'company': 'Acme', 'title': TITLE,
           'location': 'San Francisco, United States', 'description': 'Build Python and TypeScript software.', 'source': 'fixture'}
    store.upsert_job(job);store.update_settings({'remote_drafts': True})
    yield base, job, state
    server.shutdown();server.server_close();thread.join(2)


def browser_for(store, hosted):
    browser = AdaptiveBrowser(store, test_url=hosted[0])
    browser.receipt_timeout = 1;browser.operation_timeout = 4
    return browser


def test_hosted_flow_enters_the_content_frame_and_completes_profile_questions_upload_and_receipt(store, hosted):
    base, job, state = hosted
    with browser_for(store, hosted) as browser:
        try:
            assert browser.apply(job) == 'confirmed'
        except Exception as error:
            error.add_note(json.dumps({'url': browser.page.url, 'memory': browser.memory, 'requests': state['records']}))
            raise
        with pytest.raises(Blocked):
            browser.apply(job)
    assert state['profile'] == {'first': 'Test', 'last': 'Person', 'email': 'test@candidate.invalid'}
    # The conditional sponsorship question appeared only after the authorization answer.
    assert state['questions'] == {'auth': 'Yes', 'sponsor': 'No'}
    document = store.db.execute("SELECT hash FROM documents WHERE kind='resume'").fetchone()
    assert hashlib.sha256(state['file']).hexdigest() == document['hash']
    assert state['finals'] == 1 and state['wrappers'] >= 2
    assert next(record for record in state['records'] if record['path'] == '/submit')['intents'] == 1
    assert not any(record['intents'] for record in state['records'] if record['path'] != '/submit')
    assert store.db.execute('SELECT state FROM applications').fetchone()[0] == 'confirmed'


@pytest.mark.parametrize('mode', ['foreign', 'other_job'])
def test_foreign_or_mismatched_frame_stops_before_any_value_is_sent(store, hosted, mode):
    base, job, state = hosted
    state['mode'] = mode
    with browser_for(store, hosted) as browser:
        with pytest.raises(Blocked, match='icims_frame_boundary'):
            browser.apply(job)
    assert not state['records'] and not store.db.execute("SELECT 1 FROM applications WHERE state!='prepared'").fetchone()


def test_unknown_question_in_a_hosted_step_is_held_before_the_final_action(store, hosted):
    base, job, state = hosted
    state['mode'] = 'unknown'
    with browser_for(store, hosted) as browser:
        with pytest.raises(Blocked):
            browser.apply(job)
    assert state['finals'] == 0
    assert store.db.execute("SELECT label FROM questions WHERE resolved=0").fetchone()['label'].startswith('What is your badge number')


def test_preparation_sends_nothing_to_the_hosted_application(store, hosted):
    base, job, state = hosted
    with browser_for(store, hosted) as browser:
        # A multi-step draft cannot advance without employer saves, so local preparation stops at the first step.
        with pytest.raises(Blocked, match='browser_preparation_incomplete'):
            browser.apply(job, live=False)
    assert not state['records'] and state['file'] is None and state['finals'] == 0


def test_explicit_ats_scope_treats_icims_separately_from_other_forms(store):
    job = {'id': 'icims-job', 'url': 'https://careers-acme.icims.com/jobs/4821/software-engineer-intern/job', 'host': 'careers-acme.icims.com',
           'company': 'Acme', 'title': TITLE, 'location': 'Remote (US)', 'description': 'Python', 'source': 'fixture'}
    store.upsert_job(job)
    store.update_settings({'allowed_application_ats': ['other', 'lever']})
    with pytest.raises(Blocked, match='ats_out_of_scope'):
        store._check_budget(job, store.settings())
    store.update_settings({'allowed_application_ats': ['icims']})
    store._check_budget(job, store.settings())
    # Staged rollout evidence exists only for Lever and Ashby; iCIMS cannot be commissioned through it.
    from hireme.rollout import commission
    with pytest.raises(ValueError, match='Lever and/or Ashby'):
        commission(store, 'controlled')


def test_step_that_saves_through_an_account_profile_endpoint_is_refused(store, hosted):
    """The executor completes applications; it never edits an employer account profile."""
    base, job, state = hosted
    state['mode'] = 'profile_write'
    with browser_for(store, hosted) as browser:
        with pytest.raises(Blocked, match='browser_operation_blocked'):
            browser.apply(job)
    assert state['profile'] is None and state['finals'] == 0
    assert [record['path'] for record in state['records']] == ['/upload']
