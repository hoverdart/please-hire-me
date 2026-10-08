"""Report blockers from the 2026-10-06 batch emails, reproduced with synthetic forms."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hireme.answers import field_key, resolve, selection_limit, selections, validate_package, _compatible_binding
from hireme.materials import save_basic_context
from hireme.provider import ClaudeProvider
from hireme.reports import queue_report
from hireme.util import Blocked, digest


def field(label, kind='checkbox-group', options=None, **extra):
    return {'label': label, 'type': kind, 'required': True, 'options': options or [], 'maxlength': -1, **extra}


SUMMER = {'title': 'Software Engineering Intern (Summer 2027)'}


@pytest.mark.parametrize('label', ['Name pronounciation', 'Name pronunciation', 'Pronunciation of your name',
                                   'How should we pronounce your name?'])
def test_name_pronunciation_is_its_own_fact_not_pronouns(store, job, label):
    assert field_key(label) == 'name_pronunciation'
    assert not _compatible_binding('pronouns', label)
    with pytest.raises(Blocked, match='missing_fact'):
        resolve(store, job['host'], field(label, 'text'), context=job)
    store.put_facts({'name_pronunciation': 'TEST PER-sun'})
    assert resolve(store, job['host'], field(label, 'text'), context=job)['value'] == 'TEST PER-sun'


def test_link_spellings_use_confirmed_profile_urls(store, job):
    store.put_facts({'github': 'https://github.com/test-person', 'linkedin': 'https://linkedin.com/in/test-person'})
    assert resolve(store, job['host'], field('Github Link', 'text'), context=job)['value'] == 'https://github.com/test-person'
    assert resolve(store, job['host'], field('LinkedIn Link', 'text'), context=job)['value'] == 'https://linkedin.com/in/test-person'


def test_selection_limits_and_exact_choices():
    assert selection_limit('What type of work are you most interested in? Select 2.') == (2, 2)
    assert selection_limit('Choose up to three teams') == (1, 3)
    assert selection_limit('Please indicate all cohort dates you would be able to work') == (1, None)
    assert selection_limit('Select your school') is None
    assert selections('A; C', ['A', 'B', 'C']) == ['A', 'C']
    assert selections('C; A', ['A', 'B', 'C']) == ['A', 'C']
    assert selections('A; D', ['A', 'B', 'C']) is None


COHORTS = ['May 18 - Aug 7', 'Jun 15 - Sep 4', 'Jul 6 - Sep 25']


def test_cohorts_follow_confirmed_start_window_and_revalidate(store, job, package):
    store.put_facts({'earliest_start': '2027-05', 'latest_start': '2027-06'})
    f = field('Please indicate all cohort dates you would be able to work for this position.', options=COHORTS)
    context = {**job, **SUMMER}
    answer = resolve(store, job['host'], f, context=context)
    assert answer['value'] == 'May 18 - Aug 7; Jun 15 - Sep 4' and 'start_window' in answer['provenance']
    package.update(answers=[answer], steps=[], facts_hash=digest(store.facts()))
    validate_package(store, {**job, **SUMMER}, package)
    store.put_facts({'latest_start': '2027-05'})
    with pytest.raises(Blocked):
        validate_package(store, {**job, **SUMMER}, {**package, 'facts_hash': digest(store.facts())})


@pytest.mark.parametrize('options,title', [
    (['May 18 - Aug 7', 'Flexible'], SUMMER['title']),  # one choice is not a date range
    (COHORTS, 'Software Engineering Intern'),            # no year anywhere
])
def test_cohorts_abstain_when_any_choice_is_unreadable(store, job, options, title):
    store.put_facts({'earliest_start': '2027-05', 'latest_start': '2027-08'})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('Please indicate all cohort dates you would be able to work.', options=options),
                context={**job, 'title': title})


LOCATIONS = ['San Francisco', 'New York', 'London']
RELOCATE = 'Please indicate all locations that you would be interested in relocating to for this position.'


def test_relocation_destinations_come_from_chosen_locations(store, job):
    store.update_settings({'contextual_preferences': True})
    store.put_facts({'relocate': 'Yes'})
    context = {**job, 'title': 'Software Engineering Intern'}
    assert resolve(store, job['host'], field(RELOCATE, options=LOCATIONS), context=context)['value'] == 'San Francisco; New York'
    store.put_facts({'relocate': 'No'})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field(RELOCATE, options=LOCATIONS), context=context)


def test_relocation_needs_the_preference_opt_in_and_a_multi_select(store, job):
    store.put_facts({'relocate': 'Yes'})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field(RELOCATE, options=LOCATIONS), context=job)
    store.update_settings({'contextual_preferences': True})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('Which office would you relocate to?', 'radio', LOCATIONS), context=job)


def test_context_answer_returns_several_exact_choices():
    p = ClaudeProvider.__new__(ClaudeProvider); p.observer = None
    calls = []
    def request(instruction, data, schema):
        calls.append(schema)
        return {'answer': ['Infrastructure', 'Product'], 'sentence_ids': ['s1']} if len(calls) == 1 else {'supported': True, 'reason': ''}
    p.request = request
    f = field('What type of work are you most interested in? Select 2.', options=['Product', 'Research', 'Infrastructure'])
    assert p.context_answer(f, [{'id': 's1', 'text': 'Team interests: product and infrastructure.'}], {}, {})['answer'] == 'Product; Infrastructure'
    assert calls[0]['properties']['answer']['type'] == ['array', 'null']


def test_multi_choice_context_answer_must_match_the_requested_count(store, job):
    store.update_settings({'tailored_writing': True})
    save_basic_context(store, 'Team interests: product engineering and infrastructure.', 0)
    class Model:
        def match_field(self, *a): return {'fact_key': None, 'template_id': None}
        def context_answer(self, f, choices, facts, context):
            source = next(c for c in choices if 'Team interests' in c['text'])
            return {'answer': self.answer, 'sentence_ids': [source['id']]}
    f = field('What type of work are you most interested in? Select 2.', options=['Product', 'Research', 'Infrastructure'])
    model = Model(); model.answer = 'Product'
    with pytest.raises(Blocked): resolve(store, job['host'], f, model, job)
    store.db.execute('DELETE FROM model_abstentions')
    model.answer = 'Product; Infrastructure'
    assert resolve(store, job['host'], f, model, job)['value'] == 'Product; Infrastructure'


def test_model_abstention_is_not_repeated_until_sources_change(store, job):
    store.update_settings({'tailored_writing': True})
    save_basic_context(store, 'I build manufacturing software at a startup.', 0)
    class Model:
        calls = 0
        def match_field(self, *a): Model.calls += 1; return {'fact_key': None, 'template_id': None}
        def context_answer(self, *a): Model.calls += 1; return {}
    f = field('How many prior internships have you had?', 'text')
    for _ in range(2):
        with pytest.raises(Blocked, match='missing_fact'): resolve(store, job['host'], f, Model(), job)
    assert Model.calls == 2
    store.put_facts({'skills': 'Python, TypeScript, SQL'})
    with pytest.raises(Blocked, match='missing_fact'): resolve(store, job['host'], f, Model(), job)
    assert Model.calls == 4


def test_provider_failure_is_not_remembered_as_abstention(store, job):
    store.update_settings({'tailored_writing': True})
    class Model:
        def match_field(self, *a): raise Blocked('provider_timeout')
    with pytest.raises(Blocked, match='provider_timeout'):
        resolve(store, job['host'], field('How many prior internships have you had?', 'text'), Model(), job)
    assert not store.db.execute('SELECT 1 FROM model_abstentions').fetchone()


def test_latest_attempt_retires_superseded_questions(store, job):
    first = store.ask(job['id'], job['host'], 'Country', [], 'unsupported_widget')
    kept = store.ask(job['id'], job['host'], 'Country', ['United States'], 'option_mismatch')
    legacy = store.ask(job['id'], job['host'], 'Legacy history', [], 'legacy_history_review')
    store.retire_questions(job['id'], {kept})
    open_ids = {r[0] for r in store.db.execute('SELECT id FROM questions WHERE resolved=0')}
    assert open_ids == {kept, legacy} and first not in open_ids


def _report(store, job, reason):
    store.db.execute("INSERT INTO runs VALUES('run-one','2026-01-01T00:00:00Z','2026-01-01T00:00:05Z','finished',0,'{}')")
    store.event('application_finished', job['id'], {'run_id': 'run-one', 'outcome': 'blocked', 'reason': reason, 'detail': ''})
    queue_report(store, 'run-one')
    return store.db.execute('SELECT body FROM report_outbox').fetchone()[0]


def test_report_lists_each_question_once(store, job):
    store.ask(job['id'], job['host'], 'Name pronounciation', [], 'missing_fact')
    store.ask(job['id'], job['host'], 'Name  pronounciation', ['x'], 'missing_fact')
    body = _report(store, job, 'missing_answers')
    assert body.count('Name pronounciation') == 1


def test_report_puts_model_allowance_waits_apart_from_blockers(store, job):
    store.ask(job['id'], job['host'], 'Where was your last internship?', [], 'model_budget_exhausted')
    body = _report(store, job, 'model_budget_exhausted')
    assert 'Waiting for the model allowance — retried automatically' in body
    assert 'Blocked — review' not in body and 'Model budget exhausted' not in body


# Browser behaviour, on local synthetic pages only.

POSTING = b'''<!doctype html><html><body><h1>Software Engineer Intern Summer 2027</h1>
<a href="/apply">Apply for this job</a><p>Build Python software in San Francisco, United States.</p>
<a href="/apply">Apply for this job</a></body></html>'''
SPLIT = POSTING.replace(b'<a href="/apply">Apply for this job</a></body>', b'<a href="/other">Apply for this job</a></body>')


@pytest.fixture
def site():
    from pathlib import Path
    pages = {'/posting': POSTING, '/split': SPLIT,
             '/apply': (Path(__file__).parent / 'fixtures/application.html').read_bytes()}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
            self.wfile.write(pages.get(self.path.split('?')[0], b'<html><body>Not found</body></html>'))
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length'])); self.send_response(200); self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield 'http://127.0.0.1:' + str(server.server_address[1])
    server.shutdown(); server.server_close()


def _job(store, url):
    j = {'id': digest(url), 'url': url, 'host': '127.0.0.1', 'company': 'Synthetic ATS', 'title': 'Software Engineer Intern Summer 2027',
         'location': 'San Francisco, United States', 'description': 'Build Python software', 'source': 'fixture'}
    store.upsert_job(j); return j


def test_repeated_apply_links_to_one_destination_are_one_entry(store, site):
    from hireme.browser import Browser
    with Browser(store, test_url=site) as b:
        assert b.apply(_job(store, site + '/posting'), live=False) == 'prepared'
        with pytest.raises(Blocked, match='unsupported_form'):
            b.apply(_job(store, site + '/split'), live=False)


def test_page_without_any_loaded_dropdown_is_a_retryable_load_failure(store, site, monkeypatch):
    from hireme.browser import Browser
    with Browser(store, test_url=site) as b:
        original = b._snapshot
        def injected():
            fields = original()
            if not fields: return fields
            return fields + [{'index': 90 + i, 'indices': [90 + i], 'label': label, 'type': 'combobox', 'required': True,
                              'options': [], 'maxlength': -1, 'value': ''} for i, label in enumerate(('Country', 'Degree'))]
        b._snapshot = injected
        with pytest.raises(Blocked, match='posting_fetch_failed'):
            b.apply(_job(store, site + '/apply'), live=False)
    assert not store.db.execute('SELECT 1 FROM questions').fetchone()


def test_fields_after_budget_exhaustion_wait_for_allowance(store, site, monkeypatch):
    from hireme.browser import Browser
    store.put_facts({'github': 'https://github.com/test-person'})
    class Model:
        def __init__(self, *a): pass
        def match_field(self, *a, **k): raise Blocked('model_budget_exhausted')
    monkeypatch.setattr('hireme.provider.ManagedProvider', Model)
    with Browser(store, test_url=site) as b:
        original = b._snapshot
        def injected():
            extra = [('Where was your last internship?', 'text'), ('Why are you interested in this role?', 'textarea'),
                     ('Name pronounciation', 'text')]
            return original() + [{'index': 90 + i, 'indices': [90 + i], 'label': label, 'type': kind, 'required': True,
                                  'options': [], 'maxlength': -1, 'value': ''} for i, (label, kind) in enumerate(extra)]
        b._snapshot = injected
        with pytest.raises(Blocked, match='model_budget_exhausted'):
            b.apply(_job(store, site + '/apply'), live=False)
    reasons = dict(store.db.execute('SELECT label,reason FROM questions WHERE resolved=0').fetchall())
    assert reasons == {'Where was your last internship?': 'model_budget_exhausted',
                       'Why are you interested in this role?': 'model_budget_exhausted',
                       'Name pronounciation': 'missing_fact'}
