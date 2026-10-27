import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme import prior_applications as prior
from hireme.util import Blocked, digest


@pytest.fixture(autouse=True)
def saved_gmail_connection(store,monkeypatch):
    from hireme.util import atomic_json
    atomic_json(store.root/'integrations/gmail-account.json',{'email':'test@candidate.invalid'})
    atomic_json(store.root/'integrations/gmail-token.json',{})
    monkeypatch.setattr(prior,'public_company_name',lambda job,**kwargs:job['company'])


@pytest.fixture
def optiver(store,job):
    url='https://job-boards.greenhouse.io/optiverus/jobs/8885299001'
    value={**job,'company':'Optiver','title':'Software Engineer Intern (Summer 2027 - Chicago)',
           'url':url,'id':digest(url),'host':'job-boards.greenhouse.io'}
    store.upsert_job(value)
    return value


def mail(job, *, body=None, subject='Your application', identity='mail-one', recipient='test@candidate.invalid', age=90*86400, html=False):
    body=body or f'We received your application for the {job["title"]} position at {job["company"]}.'
    return {'id':identity,'internalDate':str(int((time.time()-age)*1000)),'payload':{
        'mimeType':'text/html' if html else 'text/plain','headers':[
            {'name':'From','value':'no-reply@optiver.us'},{'name':'To','value':recipient},{'name':'Subject','value':subject},
            {'name':'Authentication-Results','value':'mx.google.com; dkim=pass header.i=@gh-mail.optiver.us; spf=pass'}],
        'body':{'data':base64.urlsafe_b64encode(body.encode()).decode()}}}


def test_old_external_application_is_held_without_inventing_a_local_submission(store,optiver):
    assert prior.ingest(store,mail(optiver))
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    with pytest.raises(Blocked,match='prior_application_email'):store._check_budget(optiver,store.settings())
    row=prior.listing(store)['messages'][0]
    assert row['sender_verified']==1 and row['state']=='pending' and row['candidates'][0]['id']==optiver['id']
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


@pytest.mark.parametrize('body',[
    'Your application has been received. Our software engineers and traders work together. Meet a software engineer turned tech lead.',
    'Your application has been received. If you are selected, we may invite you to an interview.',
])
def test_company_only_receipt_does_not_block_every_role(store,optiver,body):
    assert prior.ingest(store,mail(optiver,subject='Prepare for your application process with Optiver',body=body))
    assert not prior.matches(store,optiver)
    store._check_budget(optiver,store.settings())


@pytest.mark.parametrize('change', ['2028', 'Austin', 'Quantitative Research Intern','Software Engineer','Senior Software Engineer'])
def test_different_year_location_or_role_does_not_match(store,optiver,change):
    assert prior.ingest(store,mail(optiver))
    title=(optiver['title'].replace('2027','2028') if change=='2028' else
           optiver['title'].replace('Chicago','Austin') if change=='Austin' else change)
    other={**optiver,'id':digest(change),'url':optiver['url']+'-other','title':title}
    store.upsert_job(other)
    assert not prior.matches(store,other)


def test_optiver_style_assessment_reminder_is_prior_application_evidence(store,optiver):
    text=(f'We are writing you with regard to your application for the {optiver["title"]} position at Optiver.\n'
          'Please complete the assessment within seven days. Our records indicate that you have not yet completed it.')
    assert prior.ingest(store,mail(optiver,subject='Take the next step in your Optiver application',body=text))
    assert prior.matches(store,optiver)[0]['category']=='assessment'


def test_inert_html_posting_link_matches_canonical_alias_without_fetching_it(store,optiver):
    text=f'<p>Optiver: We received your application.</p><a href="{optiver["url"].replace("job-boards", "boards")}?utm_source=email">Application details</a>'
    assert prior.ingest(store,mail(optiver,body=text,html=True))
    assert prior.matches(store,optiver)[0]['match_reason']=='Exact posting link in application email'


@pytest.mark.parametrize('kind',['wrong_recipient','self_sent','authentication','advertisement','forwarded','future'])
def test_unrelated_or_sensitive_mail_cannot_create_a_hold(store,optiver,kind):
    item=mail(optiver)
    if kind=='wrong_recipient':item=mail(optiver,recipient='another@candidate.invalid')
    elif kind=='self_sent':item['payload']['headers'][0]['value']='test@candidate.invalid'
    elif kind=='authentication':item=mail(optiver,subject='Your login code to Optiver assessment portal',body='Your login code is SECRET123')
    elif kind=='advertisement':item=mail(optiver,body='Optiver is hiring. Submit your application and explore our internships.')
    elif kind=='forwarded':item=mail(optiver,subject='Fwd: Your Optiver application')
    else:item['internalDate']=str(int((time.time()+1000)*1000))
    assert not prior.ingest(store,item)
    assert prior.listing(store)['total']==0


def test_confirming_company_only_mail_links_exact_role_without_rewriting_an_unknown_attempt(store,optiver,package):
    package={**package,'job_id':optiver['id'],'url':optiver['url']}
    aid=store.prepare(optiver,package);store.begin_submit(aid);store.finish(aid,'unknown')
    original=store.application_record(aid)
    assert prior.ingest(store,mail(optiver,body='Optiver: your application has been received.'))
    row=prior.listing(store)['messages'][0]
    prior.review(store,{'id':row['id'],'revision':1,'state':'confirmed','job_id':optiver['id']})
    assert prior.matches(store,optiver)[0]['match_reason']=='Reviewed email link'
    assert store.application_record(aid)==original
    with pytest.raises(ValueError,match='changed'):prior.review(store,{'id':row['id'],'revision':1,'state':'dismissed'})
    prior.review(store,{'id':row['id'],'revision':2,'state':'dismissed'})
    assert not prior.matches(store,optiver) and store.application_record(aid)==original
    with pytest.raises(Blocked):store.begin_submit(aid)


def test_mail_holds_survive_disabled_scanning_and_release_only_their_own_dependency(store,optiver):
    from hireme.job_holds import hold,ready
    assert prior.ingest(store,mail(optiver))
    hold(store,optiver,'prior_application_email')
    assert not ready(store,optiver)
    store.update_settings({'gmail_prior_applications':False})
    with pytest.raises(Blocked):prior.check_saved(store,optiver)
    row=prior.listing(store)['messages'][0]
    prior.review(store,{'id':row['id'],'revision':1,'state':'dismissed'})
    assert ready(store,optiver)


def test_owner_binding_duplicate_ingestion_and_company_aliases(store,optiver):
    assert prior.ingest(store,mail(optiver)) and not prior.ingest(store,mail(optiver))
    store.update_settings({'company_aliases':{'Optiver':'Optiver Group','Optiver US':'Optiver Group'}})
    other={**optiver,'company':'Optiver US'}
    assert prior.matches(store,other)
    store.put_facts({'email':'other@candidate.invalid'})
    assert not prior.matches(store,optiver)


class Response:
    def __init__(self,value):self.value=value
    def execute(self):return self.value


class Mailbox:
    def __init__(self,job):self.job=job;self.lists=[];self.reads=[];self.pages=[['one','two']]
    def users(self):return self
    def messages(self):return self
    def list(self,**kwargs):
        self.lists.append(kwargs);page=int(kwargs.get('pageToken','0'))
        return Response({'messages':[{'id':identity} for identity in self.pages[page]],
                         **({'nextPageToken':str(page+1)} if page+1<len(self.pages) else {})})
    def get(self,**kwargs):self.reads.append(kwargs);return Response(mail(self.job,identity=kwargs['id']))


def test_full_history_scan_is_opt_in_and_resumes_pages_without_model_or_attachment_access(store,optiver):
    service=Mailbox(optiver);service.pages=[['one'],['two']]
    factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service)
    with pytest.raises(Blocked,match='disabled'):prior.scan(store,client_factory=lambda _:pytest.fail('Disabled scan accessed Gmail'))
    store.update_settings({'gmail_prior_applications':True})
    first=prior.scan(store,company='Optiver',client_factory=factory)
    with pytest.raises(Blocked,match='check_required'):prior.require_recent_check(store,optiver)
    second=prior.scan(store,company='Optiver',client_factory=factory)
    assert first['more_available'] and not second['more_available'] and first['added']==second['added']==1
    assert service.lists[1]['pageToken']=='1' and all('after:' not in options['q'] for options in service.lists)
    assert all(options['maxResults']==prior.MAX_MESSAGES for options in service.lists)
    assert all(options['format']=='full' for options in service.reads)
    prior.require_recent_check(store,optiver)
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_failed_read_keeps_cursor_and_prevents_submission_without_leaking_error(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver)
    def fail(**kwargs):raise RuntimeError('Bearer SECRET_REQUEST')
    service.get=fail
    with pytest.raises(Blocked,match='mail_unavailable') as error:
        prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert 'SECRET_REQUEST' not in str(error.value)
    assert json.loads(store.db.execute('SELECT pending FROM prior_application_scans').fetchone()[0])==['one','two']
    with pytest.raises(Blocked):prior.require_recent_check(store,optiver)


def test_identity_change_during_mail_read_stops_before_retaining_evidence(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);original=service.get
    def disable(**kwargs):store.update_settings({'gmail_prior_applications':False});return original(**kwargs)
    service.get=disable
    with pytest.raises(Blocked,match='scan_changed'):
        prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert prior.listing(store)['total']==0


def test_worker_checks_mail_before_opening_browser_or_recording_an_attempt(store,optiver,monkeypatch):
    from hireme.worker import cycle
    store.update_settings({'gmail_prior_applications':True})
    service=Mailbox(optiver)
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    def forbidden(*args):pytest.fail('A known prior application reached the browser')
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=forbidden)
    assert result['attempts']==0 and result['confirmed']==0
    assert store.db.execute('SELECT COUNT(*) FROM applications').fetchone()[0]==0
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='application_started'").fetchone()
    assert store.db.execute('SELECT reason FROM jobs WHERE id=?',(optiver['id'],)).fetchone()[0].startswith('prior_application_email')
    assert 'Optiver' not in store.db.execute('SELECT body FROM report_outbox').fetchone()[0]


def test_final_intent_requires_fresh_completed_company_scan(store,optiver,package):
    package={**package,'job_id':optiver['id'],'url':optiver['url']}
    aid=store.prepare(optiver,package)
    store.update_settings({'gmail_prior_applications':True})
    with pytest.raises(Blocked,match='check_required'):store.begin_submit(aid)
    service=Mailbox(optiver);service.pages=[[]]
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    store.db.execute('UPDATE prior_application_scans SET completed=?',(time.time()-prior.FRESH_SECONDS-1,))
    with pytest.raises(Blocked,match='check_required'):store.begin_submit(aid)
    assert store.application_record(aid)['state']=='prepared'


def test_failed_refresh_revokes_previous_scan_freshness(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);service.pages=[[]]
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    prior.require_recent_check(store,optiver)
    def disconnected(_):raise Blocked('gmail_reconnect_required')
    with pytest.raises(Blocked):prior.scan(store,company='Optiver',client_factory=disconnected)
    with pytest.raises(Blocked):prior.require_recent_check(store,optiver)


def test_disconnecting_gmail_cannot_reuse_a_recent_check(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);service.pages=[[]]
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    from hireme.gmail import disconnect
    disconnect(store)
    with pytest.raises(Blocked):prior.require_recent_check(store,optiver)


def test_restore_preserves_mail_holds_but_invalidates_source_machine_freshness(store,optiver,tmp_path):
    from hireme.backup import create_backup,restore_backup
    from hireme.store import Store
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver)
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    create_backup(store,tmp_path/'history.zip');restore_backup(tmp_path/'history.zip',tmp_path/'restored')
    restored=Store(tmp_path/'restored')
    try:
        assert len(prior.matches(restored,optiver))==2
        assert restored.db.execute('SELECT completed FROM prior_application_scans').fetchone()[0] is None
        with pytest.raises(Blocked):prior.require_recent_check(restored,optiver)
        assert not restored.settings()['live_enabled']
    finally:restored.close()


def test_incomplete_company_check_does_not_open_browser_or_pin_next_cycle(store,optiver,monkeypatch):
    from hireme.worker import cycle
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);service.pages=[[],[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    def forbidden(*args):pytest.fail('Incomplete mailbox scan opened a browser')
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=forbidden)
    assert result['attempts']==0 and result['reasons']['prior_application_scan_incomplete']==1
    assert service.lists[0]['q'].find('after:')==-1


def test_scan_after_job_discovery_can_bind_a_previously_unmatched_message(store,optiver):
    store.db.execute('DELETE FROM jobs WHERE id=?',(optiver['id'],))
    assert prior.ingest(store,mail(optiver))
    assert store.db.execute('SELECT company_key FROM prior_application_mail').fetchone()[0]==''
    store.upsert_job(optiver);store.update_settings({'gmail_prior_applications':True})
    service=Mailbox(optiver);service.pages=[['mail-one']]
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert prior.matches(store,optiver)


def test_alias_edits_do_not_erase_the_original_company_named_in_email(store,optiver):
    store.update_settings({'company_aliases':{'Optiver':'Optiver Group'}})
    assert prior.ingest(store,mail(optiver))
    assert store.db.execute('SELECT company_key FROM prior_application_mail').fetchone()[0]=='optiver'
    store.update_settings({'company_aliases':{'Optiver':'Another Optiver Group'}})
    assert prior.matches(store,optiver)


def test_deleted_mail_advances_the_saved_cursor_without_discarding_other_receipts(store,optiver):
    from googleapiclient.errors import HttpError
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);original=service.get
    def missing(**kwargs):
        if kwargs['id']=='one':raise HttpError(SimpleNamespace(status=404,reason='not found'),b'{}')
        return original(**kwargs)
    service.get=missing
    result=prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert result['processed']==2 and result['added']==1 and not result['more_available']
    assert prior.matches(store,optiver)


def test_scope_query_cannot_turn_a_company_name_into_gmail_operators(store):
    query=prior._query(store,'Acme" OR from:me {secret}')
    assert query.count('-from:me')==1 and '"Acme OR from me secret"' in query


def test_mismatched_provider_identity_cannot_finish_a_scan(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver)
    with pytest.raises(Blocked,match='account_mismatch'):
        prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='another@candidate.invalid',service=service))
    assert not service.lists and prior.listing(store)['total']==0
    with pytest.raises(Blocked):prior.require_recent_check(store,optiver)


def test_complete_mailbox_check_allows_a_new_role_and_suppresses_scanning_during_preparation(store,optiver,monkeypatch):
    from hireme.worker import cycle
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    visited=[]
    class Browser:
        def __init__(self,_):pass
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def apply(self,job,live=True):visited.append((job['id'],live));return 'prepared'
    result=cycle(store,Path('.'),discover=False,live=False,job_ids=[optiver['id']],browser_factory=Browser)
    assert result['prepared']==1 and not service.lists
    result=cycle(store,Path('.'),discover=False,live=True,job_ids=[optiver['id']],browser_factory=Browser)
    assert result['attempts']==1 and service.lists and visited[-1][1] is True


def test_opaque_board_slug_is_bound_to_official_company_name_before_mail_search(store,optiver,monkeypatch):
    slug={**optiver,'company':'optiverus'};store.upsert_job(slug)
    store.update_settings({'gmail_prior_applications':True})
    monkeypatch.setattr(prior,'public_company_name',lambda *args,**kwargs:'Optiver')
    service=Mailbox(optiver)
    prior.scan(store,company='optiverus',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert '"Optiver"' in service.lists[0]['q'] and '"optiverus"' in service.lists[0]['q']
    assert prior.matches(store,slug)
    assert prior.listing(store)['messages'][0]['candidates'][0]['company']=='optiverus'


def test_unverified_board_name_cannot_be_guessed_from_a_prefix(store,optiver,monkeypatch):
    slug={**optiver,'company':'optiverus'};store.upsert_job(slug);store.update_settings({'gmail_prior_applications':True})
    monkeypatch.setattr(prior,'public_company_name',lambda *a,**kw:None)
    with pytest.raises(Blocked,match='company_review'):prior.preflight(store,slug)
    assert not store.db.execute('SELECT 1 FROM prior_application_company_names').fetchone()


def test_restart_scans_old_mail_again_without_resurrecting_a_dismissed_match(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver)
    factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service)
    prior.scan(store,company='Optiver',client_factory=factory)
    row=prior.listing(store)['messages'][0];prior.review(store,{'id':row['id'],'revision':1,'state':'dismissed'})
    prior.scan(store,company='Optiver',restart=True,client_factory=factory)
    assert 'after:' not in service.lists[-1]['q']
    assert store.db.execute("SELECT COUNT(*) FROM prior_application_mail WHERE state='dismissed'").fetchone()[0]==1
    assert prior.listing(store)['total']==2


def test_unknown_company_identity_never_matches_an_unbound_role_at_another_employer(store,optiver):
    store.db.execute('DELETE FROM jobs')
    assert prior.ingest(store,mail(optiver))
    other={**optiver,'id':digest('unknown-company'),'company':'東京','url':optiver['url']+'-other'}
    store.upsert_job(other)
    assert not prior.matches(store,other)


def test_punctuation_only_company_does_not_inspect_unrelated_public_boards(store,optiver,monkeypatch):
    monkeypatch.setattr(prior,'public_company_name',lambda *a,**kw:pytest.fail('An empty company key widened the public search'))
    prior._resolve_company_search(store,'!!!')


def test_invalid_company_filter_cannot_widen_the_mailbox_scan(store,optiver):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver)
    with pytest.raises(ValueError):
        prior.scan(store,company='!!!',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert not service.lists
