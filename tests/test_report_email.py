"""HTML batch reports, delivery states and mailbox reconciliation. Synthetic mail only."""
import base64
import email
import email.policy
import json
import re
import time

import pytest

from hireme import reports
from hireme.gmail import GmailClient, InvalidReportMessage
from hireme.report_email import render_html, safe_link
from hireme.reports import flush_reports, preview_report, queue_report, report_status, requeue_report
from hireme.util import Blocked


def run(store, run_id='run-one', detail='{"attempts":1}', submitted=0):
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',
                     (run_id, '2026-01-01T00:00:00Z', '2026-01-01T00:00:05Z', 'finished', submitted, detail))
    queue_report(store, run_id)


class Mailbox:
    """A synthetic Gmail double that records accepted messages by Message-ID."""
    email = 'test@candidate.invalid'
    accepted = {}
    behavior = 'accept'
    calls = 0

    def __init__(self, store):
        pass

    def send_report(self, subject, body, message_id, html=None):
        type(self).calls += 1
        if self.behavior == 'timeout_after_accept':
            self.accepted[message_id] = (subject, body, html)
            raise TimeoutError()
        if self.behavior == 'timeout_before_accept':
            raise TimeoutError()
        if isinstance(self.behavior, Exception):
            raise self.behavior
        self.accepted[message_id] = (subject, body, html)
        return 'gmail-' + str(len(self.accepted))

    def find_sent_report(self, message_id):
        return 'gmail-found' if message_id in self.accepted else None


@pytest.fixture
def mailbox():
    Mailbox.accepted = {}
    Mailbox.behavior = 'accept'
    Mailbox.calls = 0
    return Mailbox


class HttpError(Exception):
    def __init__(self, status):
        self.resp = type('Resp', (), {'status': status})()


def test_safe_links_exclude_local_credentialed_and_non_https_destinations():
    assert safe_link('https://jobs.lever.co/acme/req-123') == 'https://jobs.lever.co/acme/req-123'
    for url in ('http://jobs.lever.co/acme', 'https://localhost:8765/#token=abc', 'https://127.0.0.1/x',
                'https://user:pw@jobs.lever.co/a', 'javascript:alert(1)', 'https://pi.local/app', 'https://intranet/x',
                'https://jobs.lever.co/a?token=secret', 'https://jobs.lever.co:8443/a', 'https://jobs.lever.co/a\nBcc: x',
                'https://192.168.1.4/a', None, 'https://[::1]/a'):
        assert safe_link(url) is None


def test_html_escapes_untrusted_posting_text_and_has_no_remote_assets(store, job):
    hostile = '<img src=x onerror=alert(1)>"\'><script>steal()</script>'
    store.db.execute('UPDATE jobs SET company=?,title=? WHERE id=?', (hostile, hostile, job['id']))
    store.db.execute("INSERT INTO questions VALUES('q1',?,?,?,'[]','missing_fact',0)", (job['id'], job['host'], hostile))
    store.event('application_finished', job['id'], {'run_id': 'run-one', 'outcome': 'blocked', 'reason': 'captcha',
                                                    'detail': hostile})
    run(store)
    html = preview_report(store)['html']
    assert '<script' not in html and '<img' not in html and html.count('&lt;img src=x onerror=alert(1)&gt;') >= 3
    assert '&lt;script&gt;steal()&lt;/script&gt;' in html
    # No images, trackers, stylesheets, forms or scripts: only inline styles and posting anchors.
    assert not re.search(r'<(img|script|link|iframe|form|object|embed|video|style)\b', html, re.I)
    assert not re.search(r'url\(|@import|<[^>]*\ssrc=', html, re.I)
    assert set(re.findall(r'href="([^"]+)"', html)) == {job['url']}
    assert 'localhost' not in html and '127.0.0.1' not in html and 'token' not in html.casefold()
    assert 'open HireMeMaybe on the computer running the worker' in html


def test_report_separates_confirmed_from_uncertain_and_blocked(store, job, package):
    from hireme.util import digest
    outcomes = {}
    for name in ('confirmed', 'unknown', 'blocked'):
        url = job['url'] + '-' + name
        item = {**job, 'id': digest(url), 'url': url, 'company': name.title() + ' Co'}
        store.upsert_job(item)
        outcomes[name] = item
    aid = store.prepare(outcomes['confirmed'], {**package, 'job_id': outcomes['confirmed']['id'], 'url': outcomes['confirmed']['url']})
    store.db.execute("INSERT INTO runs VALUES('run-one','2026-01-01T00:00:00Z','2099-01-01T00:00:05Z','finished',1,'{\"attempts\":3}')")
    store.begin_submit(aid);store.finish(aid, 'confirmed', 'Synthetic employer receipt')
    store.event('application_finished', outcomes['confirmed']['id'], {'run_id': 'run-one', 'outcome': 'confirmed'})
    store.event('application_finished', outcomes['unknown']['id'], {'run_id': 'run-one', 'outcome': 'unknown'})
    store.event('application_finished', outcomes['blocked']['id'], {'run_id': 'run-one', 'outcome': 'blocked', 'reason': 'captcha',
                                                                    'detail': 'Complete the employer CAPTCHA'})
    queue_report(store, 'run-one')
    model = json.loads(store.db.execute('SELECT model FROM report_models').fetchone()[0])
    by_tone = {group['tone']: [item['company'] for item in group['items']] for group in model['groups'] if group['items']}
    assert by_tone == {'confirmed': ['Confirmed Co'], 'uncertain': ['Unknown Co'], 'attention': ['Blocked Co']}
    confirmed = next(group for group in model['groups'] if group['tone'] == 'confirmed')['items'][0]
    assert confirmed['receipt']['id'] == aid and confirmed['receipt']['resumes']
    html = preview_report(store)['html']
    assert html.index('Confirmed Co') < html.index('Blocked Co') < html.index('Unknown Co')
    assert 'Submission outcome is uncertain. Check with the employer before applying again.' in html
    assert 'Complete the employer CAPTCHA' in html and 'Resume SHA-256' in html
    text = preview_report(store)['text']
    for company in ('Confirmed Co', 'Unknown Co', 'Blocked Co'):
        assert company in text


def test_known_unsent_and_prepared_results_are_separate_from_uncertain_applications(store,job,package):
    from hireme.util import digest
    for outcome in ('prepared','not_submitted','unknown','awaiting_verification','blocked'):
        url=job['url']+'-'+outcome
        item={**job,'id':digest(url),'url':url,'company':outcome+' Co'}
        store.upsert_job(item)
        if outcome!='blocked':
            aid=store.prepare(item,{**package,'job_id':item['id'],'url':url})
            if outcome!='prepared':
                store.begin_submit(aid);store.finish(aid,outcome,'Synthetic result')
        store.event('application_finished',item['id'],{'run_id':'run-one','outcome':outcome})
    before=[dict(row) for row in store.db.execute('SELECT * FROM application_records ORDER BY id')]
    run(store,detail='{"attempts":5}')
    model=json.loads(store.db.execute('SELECT model FROM report_models').fetchone()[0])
    assert model['summary']=={'confirmed':0,'human':2,'unconfirmed':2}
    groups={group['heading']:group for group in model['groups'] if group['items']}
    assert [item['company'] for item in groups['Other outcomes — review before retrying']['items']]==[
        'unknown Co','awaiting_verification Co']
    prepared=groups['Prepared locally — nothing submitted']
    assert prepared['tone']=='prepared' and prepared['items'][0]['company']=='prepared Co'
    refused=groups['Not submitted — review the saved result']
    assert refused['tone']=='attention' and refused['items'][0]['company']=='not_submitted Co'
    assert refused['items'][0]['status']=='Not submitted.'
    preview=preview_report(store)
    for body in (preview['text'],preview['html']):
        assert 'Prepared locally — nothing submitted' in body
        assert 'Not submitted — review the saved result' in body
        assert 'Email verification is pending.' in body
    assert 'Human interventions: 2\nUnconfirmed results to verify: 2' in preview['text']
    assert [dict(row) for row in store.db.execute('SELECT * FROM application_records ORDER BY id')]==before


def test_gmail_message_is_multipart_with_plain_text_first_and_injection_refused(store):
    from tests.test_gmail import Service, Response
    service = Service('test@candidate.invalid', None)
    client = GmailClient(store, service)
    client.send_report('Batch complete', 'Summary only', '<abc@please-hire-me.local>', '<html><body><p>Summary &amp; more</p></body></html>')
    message = email.message_from_bytes(base64.urlsafe_b64decode(service.sent[0]['raw']), policy=email.policy.default)
    parts = [part.get_content_type() for part in message.iter_parts()]
    assert message.get_content_type() == 'multipart/alternative' and parts == ['text/plain', 'text/html']
    assert message['To'] == message['From'] == 'test@candidate.invalid' and not message['Bcc'] and not message['Cc']
    assert message.get_body(('plain',)).get_content().strip() == 'Summary only'
    for subject, identifier in (('Bad\rBcc: a@b.invalid', '<abc@please-hire-me.local>'), ('Bad\x00', '<abc@please-hire-me.local>'),
                                ('Fine', '<abc@x.local>\r\nBcc: a@b.invalid'), ('Fine', 'no-brackets')):
        with pytest.raises(ValueError):
            client.send_report(subject, 'body', identifier)
    assert len(service.sent) == 1


def test_accepted_send_carries_html_and_is_labelled_provider_accepted(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    assert report_status(store)[0]['delivery'] == 'queued'
    assert flush_reports(store, mailbox) == {'sent': 1, 'enabled': True, 'batches': 1}
    (subject, body, html), = mailbox.accepted.values()
    assert html.startswith('<!DOCTYPE html>') and 'Application update' in html and body.startswith('Confirmed submissions: 0')
    status = report_status(store)[0]
    assert status['state'] == 'sent' and status['delivery'] == 'provider_accepted'
    assert 'Inbox placement is not verified' in status['explanation']


def test_timeout_after_dispatch_is_reconciled_from_the_mailbox_without_a_second_send(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    mailbox.behavior = 'timeout_after_accept'
    assert flush_reports(store, mailbox)['uncertain'] == 1
    assert report_status(store)[0]['delivery'] == 'uncertain'
    mailbox.behavior = 'accept'
    result = flush_reports(store, mailbox)
    assert result['reconciled'] == {'accepted': 1, 'not_found': 0, 'waiting': 0} and result['sent'] == 0
    assert mailbox.calls == 1 and len(mailbox.accepted) == 1
    status = report_status(store)[0]
    assert status['state'] == 'sent' and status['delivery'] == 'reconciled'
    assert flush_reports(store, mailbox) == {'sent': 0, 'enabled': True} and mailbox.calls == 1


def test_absent_uncertain_send_remains_protected_and_late_acceptance_is_reconciled(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    mailbox.behavior = 'timeout_before_accept'
    flush_reports(store, mailbox)
    mailbox.behavior = 'accept'
    # Mailbox search can lag a real send; absence shortly afterwards settles nothing.
    assert flush_reports(store, mailbox)['reconciled'] == {'accepted': 0, 'not_found': 0, 'waiting': 1}
    assert report_status(store)[0]['state'] == 'uncertain' and mailbox.calls == 1
    with pytest.raises(ValueError, match='reconciled first'):
        requeue_report(store, 'run-one')
    store.db.execute('UPDATE report_dispatch SET started=?', (time.time() - reports.RECONCILE_AFTER_SECONDS - 1,))
    assert flush_reports(store, mailbox)['reconciled'] == {'accepted': 0, 'not_found': 1, 'waiting': 0}
    status = report_status(store)[0]
    assert status['state'] == 'uncertain' and status['delivery'] == 'uncertain'
    assert 'does not prove the send failed' in status['error_message']
    with pytest.raises(ValueError, match='reconciled first'):
        requeue_report(store, 'run-one')
    # Delayed search visibility can still establish acceptance without another send.
    identifier = store.db.execute('SELECT message_id FROM report_outbox').fetchone()[0]
    mailbox.accepted[identifier] = ('Delayed search result', '', None)
    assert flush_reports(store, mailbox)['reconciled']['accepted'] == 1
    assert report_status(store)[0]['delivery'] == 'reconciled' and mailbox.calls == 1


def test_legacy_absent_search_results_never_authorize_a_resend(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    mailbox.behavior = 'timeout_before_accept'
    flush_reports(store, mailbox)
    store.db.execute("UPDATE report_outbox SET state='failed',last_error='not_found_after_uncertain_send'")
    store.db.execute("UPDATE report_dispatch SET outcome='absent',started=0")
    assert report_status(store)[0]['state'] == 'uncertain'
    with pytest.raises(ValueError, match='reconciled first'):
        requeue_report(store, 'run-one')
    flush_reports(store, mailbox)
    assert report_status(store)[0]['state'] == 'uncertain' and mailbox.calls == 1
    assert store.db.execute('SELECT outcome FROM report_dispatch').fetchone()[0] is None


def test_backup_restore_preserves_document_preferences_and_uncertain_mail(store, mailbox, tmp_path):
    from hireme.backup import create_backup, restore_backup
    from hireme.store import Store
    from hireme.voice import preferences, save_preferences
    store.update_settings({'gmail_reports': True, 'resume_style': {'template': 'modern'},
                           'allowed_application_ats': ['ashby', 'icims']})
    save_preferences(store, {'notes': 'Keep my wording direct.'}, 0)
    run(store)
    mailbox.behavior = 'timeout_after_accept'
    flush_reports(store, mailbox)
    dispatch = dict(store.db.execute('SELECT * FROM report_dispatch').fetchone())
    archive = tmp_path / 'history.zip'
    create_backup(store, archive)
    restored_root = tmp_path / 'restored'
    restore_backup(archive, restored_root)
    restored = Store(restored_root)
    try:
        assert not restored.settings()['live_enabled']
        assert restored.settings()['resume_style'] == {'template': 'modern'}
        assert restored.settings()['allowed_application_ats'] == ['ashby', 'icims']
        assert preferences(restored)[0]['notes'] == 'Keep my wording direct.'
        assert dict(restored.db.execute('SELECT * FROM report_dispatch').fetchone()) == dispatch
        assert preview_report(restored)['html'] == preview_report(store)['html']
        assert flush_reports(restored, mailbox)['reconciled']['accepted'] == 1
        assert mailbox.calls == 1
    finally:
        restored.close()


def test_report_search_excludes_unsent_drafts(store):
    from tests.test_gmail import Service, Response

    class SearchMailbox(Service):
        def list(self, **kwargs):
            self.search = kwargs
            return Response({'messages': [] if kwargs.get('labelIds') == ['SENT'] else [{'id': 'draft'}]})

    service = SearchMailbox('test@candidate.invalid', None)
    client = GmailClient(store, service)
    assert client.find_sent_report('<' + 'a' * 32 + '@please-hire-me.local>') is None
    assert service.search['labelIds'] == ['SENT'] and service.search['includeSpamTrash']


def test_mailbox_lookup_failure_leaves_the_send_uncertain(store, mailbox, monkeypatch):
    store.update_settings({'gmail_reports': True})
    run(store)
    mailbox.behavior = 'timeout_after_accept'
    flush_reports(store, mailbox)
    monkeypatch.setattr(mailbox, 'find_sent_report', lambda self, message_id: (_ for _ in ()).throw(TimeoutError()))
    store.db.execute('UPDATE report_dispatch SET started=0')
    assert flush_reports(store, mailbox)['reconciled'] == {'accepted': 0, 'not_found': 0, 'waiting': 1}
    assert report_status(store)[0]['state'] == 'uncertain' and mailbox.calls == 1


def uncertain_backlog(store,count,*,prefix='uncertain',recipient='test@candidate.invalid',started=0):
    """Seed already dispatched synthetic messages without sending any mail."""
    from hireme.util import digest
    ids=[]
    for index in range(count):
        rid=f'{prefix}-{index:03}'
        wire=f'<{digest(rid)[:32]}@please-hire-me.local>'
        created=f'2026-01-01T00:{index//60:02}:{index%60:02}+00:00'
        store.db.execute('''INSERT INTO report_outbox
            (id,recipient,subject,body,message_id,state,created,attempts)
            VALUES(?,?,?,?,?,'uncertain',?,1)''',(rid,recipient,'Synthetic report','Body',wire,created))
        store.db.execute('INSERT INTO report_dispatch VALUES(?,?,?,NULL,NULL)',(rid,wire,started))
        ids.append((rid,wire))
    return ids


@pytest.mark.parametrize('lookup',['not_found','not_visible_yet','lookup_error'])
def test_reconciliation_rotates_past_a_full_batch_of_unresolved_messages(store,mailbox,monkeypatch,lookup):
    store.update_settings({'gmail_reports':True})
    ids=uncertain_backlog(store,60,started=time.time() if lookup=='not_visible_yet' else 0)
    calls=[];visible={}
    def find(self,message_id):
        calls.append(message_id)
        if message_id in visible:return visible[message_id]
        if lookup=='lookup_error':raise TimeoutError()
        return None
    monkeypatch.setattr(mailbox,'find_sent_report',find)
    flush_reports(store,mailbox)
    assert len(calls)==50 and len(set(calls))==50
    assert store.db.execute('SELECT count(*) FROM report_dispatch WHERE checked IS NOT NULL').fetchone()[0]==50
    visible[ids[-1][1]]='accepted-late-message'
    result=flush_reports(store,mailbox)
    assert result['reconciled']['accepted']==1 and len(calls)<=100
    assert dict(store.db.execute('SELECT state,provider_id FROM report_outbox WHERE id=?',(ids[-1][0],)).fetchone())=={
        'state':'sent','provider_id':'accepted-late-message'}
    assert store.db.execute("SELECT count(*) FROM report_outbox WHERE state='uncertain'").fetchone()[0]==59
    assert mailbox.calls==0 and not store.db.execute("SELECT 1 FROM report_outbox WHERE state='pending'").fetchone()
    # Late positive evidence for a previously checked row still gets a turn.
    visible[ids[0][1]]='accepted-earlier-message'
    flush_reports(store,mailbox)
    assert store.db.execute('SELECT state FROM report_outbox WHERE id=?',(ids[0][0],)).fetchone()[0]=='sent'
    assert mailbox.calls==0


def test_reports_for_a_previous_owner_cannot_starve_current_owner_reconciliation(store,mailbox):
    store.update_settings({'gmail_reports':True})
    uncertain_backlog(store,55,prefix='old-owner',recipient='other@candidate.invalid')
    current,=uncertain_backlog(store,1,prefix='current-owner')
    mailbox.accepted[current[1]]=('Previously dispatched report','','')
    result=flush_reports(store,mailbox)
    assert result['reconciled']['accepted']==1 and mailbox.calls==0
    assert store.db.execute('SELECT state FROM report_outbox WHERE id=?',(current[0],)).fetchone()[0]=='sent'
    assert store.db.execute("SELECT count(*) FROM report_outbox WHERE recipient='other@candidate.invalid' AND state='uncertain'").fetchone()[0]==55
    assert store.db.execute("SELECT count(*) FROM report_dispatch WHERE report_id LIKE 'old-owner-%' AND checked IS NOT NULL").fetchone()[0]==0


def test_reconciliation_requires_the_wire_identifier_and_shares_a_combined_lookup(store,mailbox,monkeypatch):
    store.update_settings({'gmail_reports':True})
    ids=uncertain_backlog(store,3)
    wire='<'+'a'*32+'@please-hire-me.local>'
    store.db.execute('UPDATE report_dispatch SET message_id=?',(wire,))
    # A legacy uncertainty without recorded dispatch evidence stays held.
    store.db.execute('DELETE FROM report_dispatch WHERE report_id=?',(ids[-1][0],))
    calls=[]
    def find(self,message_id):
        calls.append(message_id)
        return 'accepted-combined-message'
    monkeypatch.setattr(mailbox,'find_sent_report',find)
    result=flush_reports(store,mailbox)
    assert result['reconciled']['accepted']==2 and calls==[wire] and mailbox.calls==0
    assert store.db.execute('SELECT state FROM report_outbox WHERE id=?',(ids[-1][0],)).fetchone()[0]=='uncertain'
    with pytest.raises(ValueError,match='reconciled first'):requeue_report(store,ids[-1][0])


@pytest.mark.parametrize('receipt',[True,1,{'id':'not-an-identifier'},['unexpected'],''])
def test_invalid_mailbox_lookup_keeps_uncertainty_and_does_not_block_a_new_report(store,mailbox,monkeypatch,receipt):
    store.update_settings({'gmail_reports':True})
    run(store)
    mailbox.behavior='timeout_before_accept'
    flush_reports(store,mailbox)
    run(store,'run-two')
    monkeypatch.setattr(mailbox,'find_sent_report',lambda self,message_id:receipt)
    mailbox.behavior='accept'
    result=flush_reports(store,mailbox)
    assert result['sent']==1 and result['reconciled']=={'accepted':0,'not_found':0,'waiting':1}
    assert store.db.execute("SELECT state FROM report_outbox WHERE id='run-one'").fetchone()[0]=='uncertain'
    assert store.db.execute("SELECT state FROM report_outbox WHERE id='run-two'").fetchone()[0]=='sent'
    assert mailbox.calls==2


@pytest.mark.parametrize('error,reason,state', [
    (HttpError(401), 'gmail_reconnect_required', 'pending'),
    (HttpError(403), 'gmail_reconnect_required', 'pending'),
    (HttpError(429), 'gmail_rate_limited', 'pending'),
    (Blocked('gmail_scopes_missing'), 'gmail_scopes_missing', 'pending'),
    (HttpError(400), 'gmail_rejected_400', 'failed'),
    (HttpError(503), 'HttpError', 'uncertain'),
    (ValueError('response was not JSON'), 'ValueError', 'uncertain'),
    (InvalidReportMessage('local encoding failed'), 'invalid_message', 'failed'),
    (ConnectionResetError(), 'ConnectionResetError', 'uncertain'),
])
def test_only_provider_refusals_are_retried_and_ambiguous_failures_are_not(store, mailbox, error, reason, state):
    store.update_settings({'gmail_reports': True})
    run(store)
    mailbox.behavior = error
    flush_reports(store, mailbox)
    row = store.db.execute('SELECT state,last_error,attempts FROM report_outbox').fetchone()
    assert (row['state'], row['last_error'], row['attempts']) == (state, reason, 1)
    mailbox.behavior = 'accept'
    mailbox.find_sent_report = lambda self, message_id: None
    try:
        flush_reports(store, mailbox)
    finally:
        del mailbox.find_sent_report
    assert mailbox.calls == (2 if state == 'pending' else 1)


def test_refused_sends_stop_after_a_bounded_number_of_attempts(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    mailbox.behavior = HttpError(429)
    for _ in range(reports.MAX_SEND_ATTEMPTS + 3):
        flush_reports(store, mailbox)
    assert mailbox.calls == reports.MAX_SEND_ATTEMPTS
    status = report_status(store)[0]
    assert status['state'] == 'failed' and status['delivery'] == 'failed' and 'slow down' in status['error_message']
    previous = store.db.execute('SELECT message_id FROM report_outbox').fetchone()[0]
    assert requeue_report(store, 'run-one') == {'queued': True}
    assert store.db.execute('SELECT message_id FROM report_outbox').fetchone()[0] != previous
    mailbox.behavior = 'accept'
    assert flush_reports(store, mailbox)['sent'] == 1


def test_worker_stopping_mid_send_is_uncertain_and_never_resent(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    store.db.execute("UPDATE report_outbox SET state='sending',attempts=1")
    assert flush_reports(store, mailbox) == {'sent': 0, 'enabled': True}
    assert store.db.execute('SELECT state FROM report_outbox').fetchone()[0] == 'uncertain' and mailbox.calls == 0


def test_combined_batches_render_one_html_document_with_each_batch(store, mailbox):
    store.update_settings({'gmail_reports': True})
    for index, submitted in enumerate((0, 2)):
        run(store, f'run-{index}', '{"attempts":1}', submitted)
    assert flush_reports(store, mailbox)['batches'] == 2
    (subject, body, html), = mailbox.accepted.values()
    assert html.count('<!DOCTYPE html>') == 1 and 'Batch run-0' in html and 'Batch run-1' in html
    assert re.search(r'>2</div>\s*<div[^>]*>Confirmed by the employer', html)
    assert {row['delivery'] for row in report_status(store)} == {'provider_accepted'}


def test_older_reports_without_a_saved_model_still_send_as_plain_text(store, mailbox):
    store.update_settings({'gmail_reports': True})
    run(store)
    store.db.execute('DELETE FROM report_models')
    assert flush_reports(store, mailbox)['sent'] == 1
    assert next(iter(mailbox.accepted.values()))[2] is None
    assert preview_report(store)['html'] is None


def test_rendering_is_bounded_for_long_and_missing_values():
    model = {'summary': {'confirmed': 1, 'human': 0, 'unconfirmed': 0}, 'run': {'id': 'r'},
             'groups': [{'heading': 'Applied', 'tone': 'made-up', 'items': [
                 {'company': 'A' * 5000, 'title': None, 'url': 'https://jobs.lever.co/a', 'questions': ['q' * 5000] * 100}]}]}
    html = render_html([model], 'Subject')
    assert len(html) < 60000 and 'A' * 201 not in html and html.count('<li') == 30


@pytest.mark.parametrize('width',[320,390,1280])
def test_email_long_unbroken_fields_wrap_without_horizontal_overflow(width):
    from playwright.sync_api import sync_playwright
    model={'summary':{},'run':{'id':'r'},'groups':[{'heading':'Needs you','items':[
        {'company':'A'*200,'title':'B'*300,'questions':['C'*500],'action':'D'*600,
         'receipt':{'id':'e'*64,'confirmed_at':'2026-11-11T09:00:00Z','resumes':['f'*64]}}]}]}
    with sync_playwright() as p:
        browser=p.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':width,'height':1000})
            page.set_content(render_html([model],'Long content check'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        finally:browser.close()
