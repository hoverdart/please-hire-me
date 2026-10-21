"""Bounded dispatch seam: capability promotion cannot undo accepted history.

The native controller's accepted result is supplied here; native transport is
covered separately. Public posting inspection, eligibility, durable transitions,
worker accounting, outbox and Gmail MIME handling use their real implementations.
Every browser/non-browser network boundary is closed to local fixture responses.
"""
from pathlib import Path
from types import SimpleNamespace

from hireme.answers import resolve
from hireme.browser import Browser
from hireme.gmail import GmailClient
from hireme.util import digest
from hireme.workday_profiles import NVIDIA, NVIDIA_SITE, operational_evidence
from hireme.workday import VERSION, WorkdayController
from hireme.worker import cycle
from tests.test_pipeline_integration import FakeMailbox


def test_accepted_commission_remains_confirmed_when_capability_promotion_is_rejected(store, monkeypatch):
    url = f'https://{NVIDIA}/en-US/{NVIDIA_SITE}/job/US-CA/Synthetic_JR12345'
    title = 'Software Engineer Intern Summer 2027'
    description = 'Build Python and TypeScript software in San Francisco, United States.'
    job = {'id': digest(url), 'url': url, 'host': NVIDIA, 'company': 'Synthetic Workday employer',
           'title': title, 'description': description, 'location': 'San Francisco, United States',
           'source': 'synthetic-commission-dispatch'}
    store.upsert_job(job)
    store.update_settings({'gmail_reports': True, 'remote_drafts': True, 'signed_in_portals': [NVIDIA]})
    seen, accepted, promotions = [], [], []
    endpoint = f'https://{NVIDIA}/wday/cxs/nvidia/{NVIDIA_SITE}/job/US-CA/Synthetic_JR12345'
    def posting_read(network, requested, data=None):
        assert requested == endpoint and data is None
        if network.checkpoint: network.checkpoint()
        return {'jobPostingInfo': {'title': title, 'jobDescription': description,
            'jobReqId': 'JR12345', 'jobPostingId': 'Synthetic_JR12345', 'jobPostingSiteId': NVIDIA_SITE,
            'externalUrl': url, 'canApply': True}}
    monkeypatch.setattr('hireme.net.Network.json', posting_read)
    monkeypatch.setattr('hireme.browser.public_host', lambda host: True)

    class CommissionBrowser(Browser):
        def __enter__(self):
            super().__enter__()
            self.workday_commission_job = job['id']
            browser = self
            def closed(route):
                request = route.request
                assert (request.method, request.url) == ('GET', url)
                class Transport:
                    def __init__(self): self.request = request
                    def abort(self): route.abort()
                    def continue_(self):
                        seen.append((request.method, request.url))
                        route.fulfill(content_type='text/html', body=(f'<h1>{title}</h1><p>{description}</p>'
                                                                       '<button type="button">Apply</button>'))
                browser._route(Transport())
            self.context.route('**/*', closed)
            return self

    def accepted_controller_result(controller):
        # This seam begins after native acceptance. It installs no production
        # transport/profile, performs no request and claims no live-flow proof.
        accepted.append(controller.job['id'])
        controller.profile = SimpleNamespace(signature=digest('fixture-dispatch-profile'))
        field = {'index': 0, 'indices': [0], 'label': 'Email', 'type': 'email',
                 'required': True, 'options': [], 'maxlength': -1, 'value': ''}
        document = dict(store.db.execute('SELECT * FROM documents WHERE kind="resume"').fetchone())
        document['field'] = {'index': 1, 'label': 'Resume', 'type': 'file', 'required': True}
        package = {'job_id': controller.job['id'], 'url': controller.job['url'],
                   'answers': [resolve(store, controller.job['host'], field)], 'documents': [document],
                   'steps': [{'fields': [field, document['field']]}], 'facts_hash': digest(store.facts())}
        aid = store.prepare(controller.job, package)
        store.begin_submit(aid)
        store.finish(aid, 'confirmed', 'Synthetic controller accepted-result seam')
        return 'confirmed'
    monkeypatch.setattr(WorkdayController, 'commission', accepted_controller_result)
    def rejected_promotion(*args):
        promotions.append(args[3])
        raise ValueError('private-promotion-diagnostic-must-not-leak')
    monkeypatch.setattr('hireme.workday.verify_capability', rejected_promotion)
    mailbox = FakeMailbox()
    import hireme.reports
    flush = hireme.reports.flush_reports
    monkeypatch.setattr(hireme.reports, 'flush_reports', lambda selected:
                        flush(selected, lambda s: GmailClient(s, mailbox)))

    def run():
        return cycle(store, Path('.'), discover=False, browser_factory=CommissionBrowser,
                     limit=1, max_attempts=1, job_ids=[job['id']])
    result = run()
    app = store.db.execute('SELECT id,state FROM applications').fetchone()
    assert result['confirmed'] == result['attempts'] == 1 and result['outcomes'] == {'confirmed': 1}
    assert app['state'] == 'confirmed' and promotions == [app['id']]
    assert accepted == [job['id']] and seen == [('GET', url)]
    assert store.db.execute('SELECT submitted FROM runs').fetchone()[0] == 1
    held = store.db.execute("SELECT detail FROM events WHERE kind='workday_capability_held'").fetchone()
    assert held and 'private-promotion-diagnostic' not in held[0]
    assert not operational_evidence(store, NVIDIA, VERSION)
    assert not store.db.execute('SELECT 1 FROM adapter_verifications').fetchone()
    message, = mailbox.messages_sent
    body = message.get_body(preferencelist=('plain',)).get_content()
    assert 'Confirmed submissions: 1' in body and 'Applied successfully' in body
    assert 'private-promotion-diagnostic' not in body
    assert run()['attempts'] == 0
    assert accepted == [job['id']] and seen == [('GET', url)] and len(mailbox.messages_sent) == 1
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0] == 1
