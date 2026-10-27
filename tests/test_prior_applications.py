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
    row=prior.listing(store)['messages'][0]
    prior.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','job_id':slug['id']})
    assert prior.matches(store,slug)[0]['match_reason']=='Reviewed email link'


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


def test_browser_startup_blocker_stops_the_batch_without_a_phantom_attempt(store,optiver):
    from hireme.worker import cycle
    class Browser:
        def __init__(self,_):pass
        def __enter__(self):raise Blocked('browser_unavailable')
        def __exit__(self,*_):pass
    with pytest.raises(Blocked,match='browser_unavailable'):
        cycle(store,Path('.'),discover=False,live=False,job_ids=[optiver['id']],browser_factory=Browser)
    run=store.db.execute('SELECT status,detail FROM runs').fetchone()
    assert run['status']=='blocked' and json.loads(run['detail'])['attempts']==0
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='application_started'").fetchone()


def test_email_review_rejects_an_unrelated_company_with_the_same_role_title(store,optiver,job):
    other={**job,'title':optiver['title']};store.upsert_job(other)
    assert prior.ingest(store,mail(optiver))
    row=prior.listing(store)['messages'][0]
    with pytest.raises(ValueError,match='matching company'):
        prior.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','job_id':other['id']})


@pytest.mark.parametrize('receipt',[
    'Thanks for applying for the {title} position at Optiver.',
    'You applied for the {title} position at Optiver.',
    'You have applied for the {title} position at Optiver.',
    'We received your application for the {title} position at Optiver and will contact you if there is a fit.',
    'We received your application for the {title} position at Optiver. Please complete the assessment. We invite you to an interview.',
])
def test_prior_receipt_does_not_require_an_unambiguous_recruiting_stage(store,optiver,receipt):
    assert prior.ingest(store,mail(optiver,body=receipt.format(title=optiver['title'])))
    assert prior.matches(store,optiver)
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


class ReachedBrowser:
    def __init__(self,store):self.store=store
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def apply(self,job,live=True):
        self.store.event('fixture_browser_reached',job['id'],{})
        return 'prepared'


@pytest.mark.parametrize('reason',['gmail_reconnect_required','gmail_not_connected','gmail_account_mismatch'])
def test_worker_recovers_from_mail_connection_errors_without_a_permanent_review_hold(store,optiver,monkeypatch,reason):
    from hireme.worker import cycle
    store.update_settings({'gmail_prior_applications':True})
    def unavailable(_):raise Blocked(reason,'PRIVATE_PROVIDER_DETAIL')
    monkeypatch.setattr(prior,'GmailClient',unavailable)
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==0 and result['reasons']=={'prior_application_mail_unavailable':1}
    row=store.db.execute('SELECT category,evidence FROM job_holds').fetchone()
    assert row['category']=='limits' and 'PRIVATE_PROVIDER_DETAIL' not in row['evidence']
    service=Mailbox(optiver);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==1 and store.db.execute("SELECT 1 FROM events WHERE kind='fixture_browser_reached'").fetchone()


def test_competing_dashboard_scan_holds_only_until_the_lock_is_released(store,optiver,monkeypatch):
    from hireme.worker import cycle
    from hireme.store import worker_lock
    store.update_settings({'gmail_prior_applications':True})
    service=Mailbox(optiver);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    with worker_lock(store.root,'prior-mail'):
        result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==0 and result['reasons']=={'prior_application_mail_unavailable':1}
    assert store.db.execute('SELECT category FROM job_holds').fetchone()[0]=='limits'
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==1


def test_old_mail_refresh_hold_is_recheckable_but_never_releases_an_uncertain_attempt(store,optiver,package):
    from hireme.job_holds import hold,ready,recheck
    hold(store,optiver,'gmail_reconnect_required',stage='screening')
    assert ready(store,optiver) and recheck(store,optiver['id'])['ready']
    hold(store,optiver,'gmail_reconnect_required',stage='screening')
    package={**package,'job_id':optiver['id'],'url':optiver['url']}
    aid=store.prepare(optiver,package);store.begin_submit(aid);store.finish(aid,'unknown')
    assert not ready(store,optiver)
    with pytest.raises(ValueError,match='existing submission outcome'):recheck(store,optiver['id'])
    assert store.application_record(aid)['state']=='unknown'


def test_url_vendor_and_board_slug_are_not_competing_employer_names(store,optiver,job,monkeypatch):
    slug={**optiver,'company':'optiverus'};store.upsert_job(slug)
    store.upsert_job({**job,'company':'Greenhouse'})
    monkeypatch.setattr(prior,'public_company_name',lambda *a,**k:'Optiver')
    prior.resolve_company(store,slug)
    body=f'We received your application for the {optiver["title"]} position at Optiver.\nDetails: {optiver["url"]}'
    assert prior.ingest(store,mail(optiver,body=body))
    repost={**slug,'url':slug['url']+'0','id':digest('reposted-optiver')};store.upsert_job(repost)
    assert prior.matches(store,repost)[0]['match_reason']=='Company and exact role in application email'
    row=prior.listing(store)['messages'][0]
    assert any(candidate['id']==repost['id'] for candidate in row['candidates'])


def test_company_scan_repairs_legacy_inferred_employer_without_overwriting_reviews(store,optiver):
    assert prior.ingest(store,mail(optiver))
    store.db.execute("UPDATE prior_application_mail SET company_key='greenhouse'")
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);service.pages=[['mail-one']]
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert service.reads and prior.matches(store,optiver)
    row=prior.listing(store)['messages'][0]
    prior.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','job_id':optiver['id']})
    before=dict(store.db.execute('SELECT * FROM prior_application_mail').fetchone())
    assert not prior.ingest(store,mail(optiver),company='Another company')
    assert dict(store.db.execute('SELECT * FROM prior_application_mail').fetchone())==before


def test_reviewed_prior_application_survives_a_primary_email_change_without_exposing_old_mail(store,optiver):
    assert prior.ingest(store,mail(optiver))
    row=prior.listing(store)['messages'][0]
    prior.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','job_id':optiver['id']})
    store.put_facts({'email':'new@candidate.invalid'})
    with pytest.raises(Blocked,match='prior_application_email'):prior.check_saved(store,optiver)
    assert prior.listing(store)['total']==0
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_mail_budget_finishes_normally_and_a_failed_company_does_not_starve_fresh_roles(store,optiver,monkeypatch):
    from hireme.worker import cycle
    other={**optiver,'company':'NewCo','id':digest('new-company-role'),'url':'https://job-boards.greenhouse.io/newco/jobs/12345'}
    store.upsert_job(other);store.update_settings({'gmail_prior_applications':True,'max_attempts_per_cycle':1})
    monkeypatch.setattr('hireme.worker.eligible',lambda job,*args:(90 if job['id']==optiver['id'] else 60,{}))
    monkeypatch.setattr(prior,'public_company_name',lambda job,**kwargs:None if job['id']==optiver['id'] else job['company'])
    service=Mailbox(optiver);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==0 and result['reasons']=={'prior_application_company_review':1,'prior_application_check_budget':1}
    row=store.db.execute('SELECT id,status,detail FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
    assert row['status']=='finished' and json.loads(row['detail'])['shortfall']==result['target']
    assert store.db.execute('SELECT state FROM report_outbox WHERE id=?',(row['id'],)).fetchone()[0]=='disabled'
    service.pages=[[]];service.lists.clear()
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==1 and '"NewCo"' in service.lists[0]['q']
    assert store.db.execute("SELECT subject FROM events WHERE kind='fixture_browser_reached'").fetchone()[0]==other['id']
    monkeypatch.setattr(prior,'public_company_name',lambda job,**kwargs:job['company'])
    prior.scan(store,company='Optiver',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert store.db.execute("SELECT subject FROM events WHERE kind='fixture_browser_reached' ORDER BY seq DESC LIMIT 1").fetchone()[0]==optiver['id']


def test_expanded_receipt_search_restarts_old_completed_cursors(store,optiver,monkeypatch):
    store.update_settings({'gmail_prior_applications':True});service=Mailbox(optiver);service.pages=[[]]
    factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service)
    with monkeypatch.context() as old:
        old.setattr(prior,'QUERY','-from:me {subject:application subject:applying subject:candidacy subject:assessment subject:interview subject:offer}')
        prior.scan(store,company='Optiver',client_factory=factory)
    service.pages=[['new-receipt']]
    prior.scan(store,company='Optiver',client_factory=factory)
    assert 'after:' not in service.lists[-1]['q'] and prior.matches(store,optiver)


@pytest.mark.parametrize('body',[
    'If you have applied for the {title} position at Optiver, we will review your application.',
    'We have not received your application for the {title} position at Optiver.',
    'Start your application for the {title} position at Optiver.',
    "We haven't received your application for the {title} position at Optiver.",
    "Your application for the {title} position at Optiver wasn't submitted.",
    "You haven't applied for the {title} position at Optiver.",
])
def test_hypothetical_or_explicitly_missing_applications_are_not_receipts(store,optiver,body):
    assert not prior.ingest(store,mail(optiver,body=body.format(title=optiver['title'])))
    assert not prior.matches(store,optiver)


@pytest.mark.parametrize('body',[
    'Unfortunately, your application was not selected.',
    'We regret to inform you that your application has not been successful.',
    "Unfortunately, your application wasn't selected.",
])
def test_rejections_remain_prior_application_evidence(store,optiver,body):
    assert prior.ingest(store,mail(optiver,subject=f'Your application for the {optiver["title"]} position at Optiver',body=body))
    assert prior.matches(store,optiver)[0]['category']=='rejection'


def test_uncertain_re_attribution_cannot_erase_an_existing_role_hold(store,optiver):
    assert prior.ingest(store,mail(optiver))
    body=f'We received your application for the {optiver["title"]} position.\nDetails: {optiver["url"]}'
    assert not prior.ingest(store,mail(optiver,body=body),company='Optiver')
    repost={**optiver,'id':digest('repost-with-new-id'),'url':optiver['url']+'0'};store.upsert_job(repost)
    assert prior.matches(store,repost)


def test_a_company_filter_cannot_override_two_employers_named_in_a_message(store,optiver,job):
    store.upsert_job({**job,'company':'NewCo'})
    assert prior.ingest(store,mail(optiver))
    body=f'We received your application for the {optiver["title"]} position at Optiver. NewCo is also mentioned.'
    assert not prior.ingest(store,mail(optiver,body=body),company='NewCo')
    assert prior.matches(store,optiver)
    assert store.db.execute('SELECT company_key FROM prior_application_mail').fetchone()[0]=='optiver'


def test_incomplete_mail_scan_continues_before_continuously_fresh_lower_ranked_roles(store,optiver,monkeypatch):
    from hireme.worker import cycle
    other={**optiver,'company':'NewCo','id':digest('fresh-other-company'),'url':'https://job-boards.greenhouse.io/newco/jobs/12345'}
    store.upsert_job(other);store.update_settings({'gmail_prior_applications':True,'max_attempts_per_cycle':1})
    monkeypatch.setattr('hireme.worker.eligible',lambda job,*args:(90 if job['id']==optiver['id'] else 60,{}))
    service=Mailbox(optiver);service.pages=[[],[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    first=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert first['attempts']==0 and first['reasons']['prior_application_scan_incomplete']==1
    assert store.db.execute('SELECT state FROM report_outbox ORDER BY rowid DESC LIMIT 1').fetchone()[0]=='suppressed'
    second=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert second['attempts']==1 and service.lists[-1]['pageToken']=='1'
    assert store.db.execute("SELECT subject FROM events WHERE kind='fixture_browser_reached'").fetchone()[0]==optiver['id']


@pytest.mark.parametrize('incomplete',[False,True])
def test_roles_at_one_company_share_the_actual_scan_failure_and_one_check_slot(store,optiver,monkeypatch,incomplete):
    from hireme.worker import cycle
    other={**optiver,'id':digest('same-company-other-role'),'url':optiver['url']+'0','title':'Different Software Intern Summer 2027'}
    store.upsert_job(other);store.update_settings({'gmail_prior_applications':True})
    calls=[];service=Mailbox(optiver);service.pages=[[],[]]
    def client(_):
        calls.append(1)
        if not incomplete:raise Blocked('gmail_reconnect_required')
        return SimpleNamespace(email='test@candidate.invalid',service=service)
    monkeypatch.setattr(prior,'GmailClient',client)
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    reason='prior_application_scan_incomplete' if incomplete else 'prior_application_mail_unavailable'
    assert result['attempts']==0 and result['reasons']=={reason:2} and len(calls)==1


def test_mail_failure_reports_attention_without_inventing_an_application_attempt(store,optiver,monkeypatch):
    from hireme.worker import cycle
    store.update_settings({'gmail_prior_applications':True,'gmail_reports':True})
    monkeypatch.setattr('hireme.reports.flush_reports',lambda store:None)
    def unavailable(_):raise Blocked('gmail_reconnect_required')
    monkeypatch.setattr(prior,'GmailClient',unavailable)
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=ReachedBrowser)
    report=store.db.execute('SELECT state,body FROM report_outbox ORDER BY created DESC LIMIT 1').fetchone()
    assert result['attempts']==0 and report['state']=='pending'
    assert 'Email-history checks need attention' in report['body']
    assert 'Attempts: 0' in report['body'] and 'Optiver' not in report['body']
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('company,extra',[('Remote',''),('Current','Explore our current openings.'),('Ramp','Our team will ramp up next month.')])
def test_incidental_employer_words_cannot_release_a_first_scan_duplicate(store,optiver,job,monkeypatch,company,extra):
    from hireme.worker import cycle
    target={**optiver,'title':'Software Engineer Intern (Remote)'};store.upsert_job(target)
    other={**job,'company':company,'title':target['title']};store.upsert_job(other)
    store.update_settings({'gmail_prior_applications':True})
    service=Mailbox(target);service.pages=[['receipt']]
    def receipt(**kwargs):return Response(mail(target,identity=kwargs['id'],body=f'We received your application for the {target["title"]} position at Optiver. {extra}'))
    service.get=receipt
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    result=cycle(store,Path('.'),discover=False,job_ids=[target['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==0 and result['reasons']['prior_application_email']==1
    row=prior.listing(store)['messages'][0]
    assert any(candidate['id']==target['id'] for candidate in row['candidates'])
    prior.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','job_id':target['id']})
    assert prior.matches(store,target) and not prior.matches(store,other)


def test_two_employers_in_one_passage_stay_held_until_exact_role_review(store,optiver,job):
    other={**job,'company':'NewCo','title':optiver['title']};store.upsert_job(other)
    assert prior.ingest(store,mail(optiver,body=f'We received your application for the {optiver["title"]} position at Optiver and NewCo.'))
    assert prior.matches(store,optiver) and prior.matches(store,other)
    row=prior.listing(store)['messages'][0]
    assert {candidate['id'] for candidate in row['candidates']}=={optiver['id'],other['id']}
    prior.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','job_id':optiver['id']})
    assert prior.matches(store,optiver) and not prior.matches(store,other)


def test_waiting_priority_check_failure_does_not_abort_unrelated_fresh_work(store,optiver,monkeypatch):
    from hireme.worker import cycle
    from hireme.job_holds import hold
    other={**optiver,'company':'NewCo','id':digest('other-after-status-error'),'url':'https://job-boards.greenhouse.io/newco/jobs/12345'}
    store.upsert_job(other);store.update_settings({'gmail_prior_applications':True,'max_attempts_per_cycle':1})
    hold(store,optiver,'prior_application_mail_unavailable',stage='screening')
    original=prior.require_recent_check
    def check(store,job):
        if job['id']==optiver['id']:raise RuntimeError('CORRUPT_LOCAL_STATUS')
        return original(store,job)
    monkeypatch.setattr(prior,'require_recent_check',check)
    service=Mailbox(other);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==1
    assert store.db.execute("SELECT subject FROM events WHERE kind='fixture_browser_reached'").fetchone()[0]==other['id']


def test_backup_preserves_ambiguous_employer_holds_and_review_candidates(store,optiver,job,tmp_path):
    from hireme.backup import create_backup,restore_backup
    from hireme.store import Store
    store.upsert_job({**job,'company':'Remote'})
    target={**optiver,'title':'Software Engineer Intern (Remote)'};store.upsert_job(target)
    assert prior.ingest(store,mail(target))
    assert store.db.execute('SELECT company_key FROM prior_application_mail').fetchone()[0]==''
    create_backup(store,tmp_path/'ambiguous.zip');restore_backup(tmp_path/'ambiguous.zip',tmp_path/'restored-ambiguous')
    restored=Store(tmp_path/'restored-ambiguous')
    try:
        assert prior.matches(restored,target)
        assert any(candidate['id']==target['id'] for candidate in prior.listing(restored)['messages'][0]['candidates'])
        assert restored.db.execute('SELECT count(*) FROM prior_application_mail_companies').fetchone()[0]==2
    finally:restored.close()


@pytest.mark.parametrize('wording',[
    'We received your application for {title}. Our team will review it shortly.',
    "Thanks for applying for {title}, and we'll be in touch.",
    'We received your application for our {title} position.',
])
@pytest.mark.parametrize('title',['Software Engineer Intern','Sr. Software Engineer Intern','SWE Intern','Intern'])
def test_complete_roles_match_sentence_boundaries_articles_abbreviations_and_short_titles(store,optiver,title,wording):
    target={**optiver,'title':title};store.upsert_job(target)
    assert prior.ingest(store,mail(target,subject='Thank you for your interest in Optiver',body=wording.format(title=title)+'\nOptiver Recruiting'))
    assert prior.matches(store,target)


def test_punctuation_does_not_truncate_seniority_year_or_location_qualifiers(store,optiver):
    body='We received your application for Software Engineer Intern, Summer 2027 - Chicago at Optiver. Our team will review it shortly.'
    assert prior.ingest(store,mail(optiver,body=body))
    assert prior.matches(store,optiver)
    generic={**optiver,'id':digest('generic-intern-role'),'title':'Software Engineer Intern','url':optiver['url']+'0'}
    store.upsert_job(generic)
    assert not prior.matches(store,generic)


def test_short_employer_name_remains_a_possible_prior_application(store,optiver):
    target={**optiver,'company':'HP'};store.upsert_job(target)
    assert prior.ingest(store,mail(target))
    assert prior.matches(store,target)


def test_full_text_receipt_query_does_not_depend_on_employer_subject_wording():
    assert 'subject:' not in prior.QUERY
    assert all('"'+phrase+'"' in prior.QUERY for phrase in ['your application','you applied','you have applied','thank you for applying','thanks for applying'])


def test_alias_ceiling_never_discards_the_employer_being_checked(store):
    store.update_settings({'company_aliases':{**{f'Alpha {i}':'Zeta Group' for i in range(20)},'Zeta':'Zeta Group'}})
    query=prior._query(store,'Zeta')
    assert '"Zeta"' in query and '"Zeta Group"' in query


@pytest.mark.parametrize('body',[
    'Your application was not successful.',
    'We are not reviewing your application further.',
])
def test_an_unsuccessful_application_still_counts_when_its_stage_is_unclassified(store,optiver,body):
    assert prior.ingest(store,mail(optiver,subject=f'Your application for the {optiver["title"]} position at Optiver',body=body))
    assert prior.matches(store,optiver)
