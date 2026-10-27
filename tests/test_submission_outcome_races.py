"""Bounded observation of a transmitted application during native navigation."""
import json
from types import SimpleNamespace

from playwright.sync_api import Error as BrowserError
import pytest

from hireme.browser import Browser
from hireme.util import Blocked
from tests.test_pipeline_integration import Posting, RECEIPT, harness


NAVIGATION='Locator.evaluate_all: Execution context was destroyed, most likely because of a navigation'


@pytest.mark.parametrize('operation',[
    'ApiSubmitMultipleFormsAction','ApiSetFormValue','synthetic-private-operation',
    {'private':'synthetic-private-operation'},['synthetic-private-operation'],
])
def test_denied_ashby_request_before_final_transport_retains_private_safe_diagnostics(store,job,package,operation):
    import hashlib
    import re
    browser=Browser(store);browser.current_host='jobs.ashbyhq.com';browser.attempted=True
    browser.aid=store.prepare(job,package);store.begin_submit(browser.aid)
    browser.ashby_submission=SimpleNamespace(used=False,allow=lambda *args:False)
    browser.host_cache['jobs.ashbyhq.com']=True
    query='mutation Synthetic { private(value: "synthetic-private-query") }'
    data={'operationName':operation,'query':query,'variables':{'token':'synthetic-private-token'}}
    raw=json.dumps(data)
    route=SimpleNamespace(request=SimpleNamespace(
        url='https://jobs.ashbyhq.com/api/non-user-graphql?token=synthetic-private-url',
        method='POST',post_data=raw,post_data_buffer=raw.encode(),headers={}))
    aborted=[];continued=[]
    route.abort=lambda:aborted.append(True)
    route.continue_=lambda **kwargs:continued.append(True)
    browser._route(route)
    browser._route(route)
    for _ in range(2):
        with pytest.raises(Blocked,match='unapproved_draft_write'):browser._guard(job)
    events=list(store.db.execute("SELECT detail FROM events WHERE kind='post_submit_write_blocked'"))
    assert len(events)==1 and len(aborted)==2 and not continued
    detail=json.loads(events[0][0])
    assert detail['final_transport_used'] is False
    assert detail['query_sha256']==hashlib.sha256(re.sub(r'\s+','',query).encode()).hexdigest()
    assert detail.get('operation')==(operation if operation in ('ApiSubmitMultipleFormsAction','ApiSetFormValue') else None)
    assert all(secret not in events[0][0] for secret in
        ('synthetic-private-operation','synthetic-private-query','synthetic-private-token','synthetic-private-url'))
    assert not browser.ashby_submission.used
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(browser.aid,)).fetchone()[0]=='submitting'
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_submission_write'").fetchone()


def polling_browser(store,job):
    # A bounded unit seam for the observation loop, never employer evidence.
    browser=Browser(store)
    browser.current_host=job['host'];browser.attempted=True
    browser.native_submission=SimpleNamespace(used=True,failed=False,response_seen=True,
        boundary=SimpleNamespace(check=lambda:None),receipt_matches=lambda url:True)
    waits=[]
    browser.page=SimpleNamespace(url=job['url'],locator=lambda selector:SimpleNamespace(count=lambda:0),
        wait_for_timeout=waits.append)
    return browser,waits


@pytest.mark.parametrize('attempted,used,error',[
    (False,True,NAVIGATION),(True,False,NAVIGATION),
    (True,True,'Target page, context or browser has been closed'),
    (True,True,'An unrelated native page error'),
])
def test_outcome_navigation_retry_does_not_hide_unsent_or_other_browser_errors(store,job,attempted,used,error):
    browser,waits=polling_browser(store,job)
    browser.attempted=attempted;browser.native_submission.used=used
    def guard(*args,**kwargs):raise BrowserError(error)
    browser._guard=guard
    with pytest.raises(BrowserError,match=error):browser._wait_submission_outcome(job)
    assert waits==[]


def test_repeated_outcome_navigation_errors_keep_the_original_deadline(store,job,monkeypatch):
    browser,waits=polling_browser(store,job)
    clock=[0];calls=[]
    monkeypatch.setattr('hireme.browser.time.monotonic',lambda:clock[0])
    def guard(*args,**kwargs):calls.append(True);raise BrowserError(NAVIGATION)
    def wait(milliseconds):waits.append(milliseconds);clock[0]+=10
    browser._guard=guard;browser.page.wait_for_timeout=wait
    with pytest.raises(BrowserError,match='Execution context was destroyed'):
        browser._wait_submission_outcome(job)
    assert len(calls)==6 and waits==[100]*5 and clock[0]==50


@pytest.mark.parametrize('region,verification',[('us',False),('eu',False),('us',True)])
def test_real_native_greenhouse_pipeline_reobserves_receipt_after_navigation_context_replacement(store,monkeypatch,region,verification):
    posting=Posting('greenhouse','Synthetic native navigation '+region+str(verification),
        'verification_confirmed' if verification else 'confirmed',region=region)
    network=harness(store,monkeypatch,[posting])
    if verification:
        from hireme.gmail import GmailClient
        store.update_settings({'gmail_verification':True})
        monkeypatch.setattr('hireme.gmail.GmailClient',lambda selected:GmailClient(selected,network.mailbox))
        monkeypatch.setattr(GmailClient,'find_code',lambda self,*args:'AB12CD34')
    guard=Browser._guard;interrupted=[]
    def guard_during_confirmation(browser,*args,**kwargs):
        grant=browser.native_submission
        if (browser.attempted and grant and grant.used and not interrupted
                and (not verification or grant.verification_used)):
            interrupted.append(True)
            raise BrowserError(NAVIGATION)
        return guard(browser,*args,**kwargs)
    monkeypatch.setattr(Browser,'_guard',guard_during_confirmation)
    result=network.run()
    assert interrupted and result['attempts']==result['confirmed']==1,result
    assert store.db.execute('SELECT state FROM applications').fetchone()[0]=='confirmed'
    assert network.last_browser.native_submission.acknowledged
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='submit_intent'").fetchone()[0]==1
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='native_submission_write'").fetchone()[0]==(2 if verification else 1)
    message,=network.mailbox.messages_sent
    assert 'Confirmed submissions: 1' in message.get_body(preferencelist=('plain',)).get_content()
    before=list(posting.requests)
    assert network.run()['attempts']==0 and posting.requests==before and not network.unexpected


@pytest.mark.parametrize('host',['jobs.ashbyhq.com','jobs.lever.co','job-boards.greenhouse.io'])
def test_post_intent_denial_is_persisted_when_it_arrives_during_guard_dom_read(store,job,package,host):
    # The native route itself supplies the denied write; this unit seam controls
    # only when the DOM read yields. It does not count a synthetic DOM receipt.
    browser=Browser(store);browser.current_host=host;browser.attempted=True
    browser.aid=store.prepare(job,package);store.begin_submit(browser.aid)
    grant=SimpleNamespace(used=True,allow=lambda *args:False,grant=lambda *args:False)
    if host=='jobs.ashbyhq.com':browser.ashby_submission=grant
    else:browser.native_submission=grant
    browser.host_cache[host]=True
    route=SimpleNamespace(request=SimpleNamespace(url='https://'+host+'/unapproved-cleanup?token=synthetic-private',
        method='POST',post_data='synthetic cleanup',post_data_buffer=b'synthetic cleanup',headers={}))
    aborted=[];continued=[]
    route.abort=lambda:aborted.append(True)
    route.continue_=lambda **kwargs:continued.append(True)
    yielded=[]
    def frames_read(expression):
        if not yielded:
            assert not browser.denied_write
            assert store.db.execute("SELECT count(*) FROM events WHERE kind='post_submit_write_blocked'").fetchone()[0]==0
            yielded.append(True);browser._route(route)
        return False
    def locator(selector):
        return SimpleNamespace(count=lambda:0,evaluate_all=frames_read,inner_text=lambda **kwargs:RECEIPT)
    browser.page=SimpleNamespace(url='https://'+host+'/fixture',locator=locator)
    assert browser._guard(job)==RECEIPT
    assert browser._guard(job)==RECEIPT
    assert aborted==[True] and continued==[]
    events=list(store.db.execute("SELECT detail FROM events WHERE kind='post_submit_write_blocked'"))
    assert len(events)==1
    assert json.loads(events[0][0])=={'host':host,'path':'/unapproved-cleanup','method':'POST'}
