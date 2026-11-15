import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit

import pytest

from hireme.accounts import AccountVault
from hireme.browser_agent import AdaptiveBrowser
from hireme.util import Blocked,digest
from hireme.workday_account_policy import configure
from hireme.workday_accounts import authenticate

ORIGIN='https://acme.wd1.myworkdayjobs.com'
URL=ORIGIN+'/External/job/US/Synthetic_R12345'
SECRET='Example42!xyz'
TOKEN='a'*96


def auth_html():
    return '''<!doctype html><h1>Software Engineer Intern Summer 2027</h1>
    <div data-automation-id="signInContent"><h2>Sign In</h2><div id=auth></div></div>
    <script>window.workday={tenant:'acme',siteId:'External',token:'fixture-csrf'};
    function render(create=false){
      document.getElementById('auth').innerHTML=`<form data-automation-id=signInFormo>
        <label>Email<input data-automation-id=email></label><label>Password<input data-automation-id=password type=password></label>
        ${create?'<label>Verify<input data-automation-id=verifyPassword type=password></label><label><input data-automation-id=createAccountCheckbox type=checkbox>I agree to creating this account</label>':''}
        <div role=button tabindex=0 aria-label="${create?'Create Account':'Sign In'}" data-automation-id=click_filter>${create?'Create Account':'Sign In'}</div>
        <button aria-hidden=true style="display:none" type=submit>${create?'Create Account':'Sign In'}</button>
        <button type=button data-automation-id="${create?'signInLink':'createAccountLink'}">${create?'Sign In':'Create Account'}</button></form>`;
      document.querySelector('button[type=button]').onclick=()=>render(!create);
      document.querySelector('[role=button]').onclick=async()=>{
        const root=document.querySelector('form');if(create&&!root.querySelector('input[type=checkbox]').checked)return;
        const config=await(await fetch('/External/login')).json();
        const body=new URLSearchParams({username:root.querySelector('[data-automation-id=email]').value,
          password:root.querySelector('[data-automation-id=password]').value,sessionSecureToken:config.sessionSecureToken});
        try{const data=await(await fetch('/en-US/External/'+(create?'register':'login'),{method:'POST',
          headers:{'Content-Type':'application/x-www-form-urlencoded'},body})).json();
          if(data.type==='SUCCESS'){
            if(create)document.getElementById('auth').innerHTML='<p>Check your email to activate your account.</p>';
            else document.body.innerHTML='<h1>Your application</h1><p>Signed in</p>';
          }}catch{}
      };
    }render();</script>'''


@pytest.fixture
def account_browser(store,monkeypatch):
    job={'id':digest(URL),'url':URL,'host':'acme.wd1.myworkdayjobs.com','company':'Acme',
         'title':'Software Engineer Intern Summer 2027','description':'Build Python and TypeScript software.',
         'location':'San Francisco, United States','source':'fixture'}
    store.upsert_job(job);store.update_settings({'live_enabled':False,'allowed_application_ats':['workday']})
    configure(store,SECRET,SECRET,accept_account_terms=True)
    store.update_settings({'live_enabled':True,'employer_accounts':True,'gmail_verification':True})
    state={'writes':[],'activated':False,'logged_in':False,'nonce':0,'mode':'normal','activation_gets':0,'unexpected':[]}
    owner=store.facts()['email']['value']
    class Response:
        def __init__(self,status,data,ctype='application/json'):
            self.status=status;self.data=data;self.headers={'content-type':ctype}
        def body(self):return (json.dumps(self.data) if self.headers['content-type']=='application/json' else self.data).encode()
        def json(self):return self.data
    def reply(request):
        parsed=urlsplit(request.url)
        if parsed.path=='/favicon.ico':return Response(204,'','text/html')
        if request.method=='GET' and parsed.path.endswith('/userprofile'):
            return Response(200,{'emailAddress':owner}) if state['logged_in'] else Response(401,{})
        if request.method=='GET' and parsed.path=='/External/login':
            state['nonce']+=1;return Response(200,{'sessionSecureToken':'nonce-'+str(state['nonce'])})
        if request.method=='GET' and parsed.path=='/External/activate/'+TOKEN+'/':
            assert store.db.execute("SELECT state FROM employer_accounts").fetchone()[0]=='activating'
            assert store.db.execute('SELECT 1 FROM mail_consumptions').fetchone()
            state['activation_gets']+=1;state['activated']=True;return Response(200,auth_html(),'text/html')
        if request.method=='GET' and request.url==URL:return Response(200,auth_html(),'text/html')
        if request.method=='POST' and parsed.path in {'/en-US/External/register','/en-US/External/login'}:
            action=parsed.path.rsplit('/',1)[-1];body=parse_qs(request.post_data)
            assert body=={'username':[owner],'password':[SECRET],'sessionSecureToken':['nonce-'+str(state['nonce'])]}
            assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]==('creating' if action=='register' else 'signing_in')
            state['writes'].append(action)
            if state['mode']=='lost_ack':return Response(503,{})
            if action=='login':
                assert state['activated'];state['logged_in']=True
            return Response(200,{'type':'SUCCESS'})
        state['unexpected'].append((request.method,parsed.hostname,parsed.path))
        return Response(400,{'type':'ERROR'})
    def mail():
        body=base64.urlsafe_b64encode(('<a href="'+ORIGIN+'/External/activate/'+TOKEN+'/">Activate</a>').encode()).decode()
        return {'internalDate':str(int(time.time()*1000)),'payload':{'headers':[
            {'name':'From','value':'noreply@otp.workday.com'},{'name':'To','value':owner},
            {'name':'Authentication-Results','value':'mx.google.com; dkim=pass header.i=@otp.workday.com;'}],
            'body':{'data':body}}}
    class Messages:
        def list(self,**kwargs):return SimpleNamespace(execute=lambda:{'messages':[{'id':'activation-message'}]} if state['mode']!='no_mail' else {})
        def get(self,**kwargs):return SimpleNamespace(execute=mail)
    api=Messages()
    monkeypatch.setattr('hireme.gmail.GmailClient',lambda selected:SimpleNamespace(email=owner,
        service=SimpleNamespace(users=lambda:SimpleNamespace(messages=lambda:api))))
    monkeypatch.setattr('hireme.browser_agent.public_host',lambda host:True)
    with AdaptiveBrowser(store) as browser:
        browser.job=job;browser.allowed_origin=ORIGIN;browser.current_host=job['host'];browser.preparing=False
        browser.account_posting_verified=True;browser.owner_verified=False
        browser.account_operation_timeout=.5;browser.account_verification_timeout=.05
        def route(real):
            class Transport:
                request=real.request
                def abort(self):real.abort()
                def fetch(self,**kwargs):return reply(self.request)
                def fulfill(self,*,response):real.fulfill(status=response.status,headers=response.headers,body=response.body())
                def continue_(self,**kwargs):self.fulfill(response=reply(self.request))
            browser._route(Transport())
        browser.page.route('**/*',route);browser.page.goto(URL)
        yield browser,state


def test_workday_registration_activation_and_signin_are_autonomous_and_separate_from_submission(store,account_browser):
    browser,state=account_browser
    assert authenticate(browser)
    assert state['writes']==['register','login'] and state['activation_gets']==1 and not state['unexpected']
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='confirmed'
    assert browser.owner_verified and browser.account_operation is None
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    assert SECRET not in json.dumps(store.snapshot()) and SECRET.encode() not in store.path.read_bytes()
    assert TOKEN.encode() not in store.path.read_bytes()


def test_known_account_signs_in_without_creating_or_activating_again(store,account_browser):
    browser,state=account_browser
    assert authenticate(browser)
    state['logged_in']=False;browser.owner_verified=False;browser.workday_account_attempted=False
    browser.page.goto(URL)
    assert authenticate(browser)
    assert state['writes']==['register','login','login'] and state['activation_gets']==1


def test_lost_creation_acknowledgement_never_repeats_registration(store,account_browser):
    browser,state=account_browser;state['mode']='lost_ack'
    with pytest.raises(Blocked,match='account_result_uncertain'):authenticate(browser)
    assert state['writes']==['register']
    browser.workday_account_attempted=False
    with pytest.raises(Blocked,match='account_result_uncertain'):authenticate(browser)
    assert state['writes']==['register'] and store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='uncertain'


def test_missing_mail_keeps_creation_pending_instead_of_resending_it(store,account_browser):
    browser,state=account_browser;state['mode']='no_mail'
    with pytest.raises(Blocked,match='account_verification_pending'):authenticate(browser)
    assert state['writes']==['register'] and state['activation_gets']==0
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='activation_pending'
    state['mode']='normal';browser.workday_account_attempted=False
    assert authenticate(browser)
    assert state['writes']==['register','login']


def test_pause_after_account_intent_prevents_transport_and_keeps_uncertainty(store,account_browser,monkeypatch):
    browser,state=account_browser;begin=AccountVault.begin_creation
    def paused(vault,*args):
        key=begin(vault,*args);store.update_settings({'live_enabled':False});return key
    monkeypatch.setattr(AccountVault,'begin_creation',paused)
    with pytest.raises(Blocked):authenticate(browser)
    assert not state['writes'] and store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='uncertain'


def test_account_grant_requires_intent_exact_values_and_one_dispatch(store,account_browser):
    from urllib.parse import urlencode
    from hireme.workday_accounts import AuthGrant
    from hireme.workday_account_policy import credentials
    browser,state=account_browser;credential=credentials(store,browser.job,create=True)
    grant=AuthGrant(browser,'register',credential)
    def request(**changes):
        body={'username':credential['email'],'password':credential['password']};body.update(changes)
        return SimpleNamespace(method='POST',url=ORIGIN+'/en-US/External/register',
            headers={'content-type':'application/x-www-form-urlencoded'},post_data_buffer=urlencode(body).encode())
    assert not grant.allow(request())
    AccountVault(store).begin_creation(ORIGIN,'acme')
    for changes in ({'username':'different@candidate.invalid'},{'password':'ChangedSecret42!'},
                    {'extra':'unexpected'},{'sessionSecureToken':'unobserved-token'}):
        assert not grant.allow(request(**changes))
    assert grant.allow(request()) and not grant.allow(request())
    assert not state['writes']


def test_workday_account_to_verified_application_uses_production_browser_flow(store,account_browser,monkeypatch):
    """Closed HTTPS employer + Gmail; exercise the real auth and final UI grants."""
    browser,state=account_browser
    old_html=auth_html
    remote='b'*32
    app_html='''<h1>Software Engineer Intern Summer 2027</h1><h2>Application</h2>
      <label>Resume<input id=resume name=resume type=file required></label>
      <button onclick="(async()=>{let data=new FormData();data.append('resume',document.getElementById('resume').files[0]);
        let response=await fetch('/wday/cxs/acme/jobapplication/REMOTE/finalize',{method:'POST',body:data});
        if(response.ok)document.body.innerHTML='&lt;h1>Your application has been received.&lt;/h1>';
      })()">Submit application</button>'''.replace('REMOTE',remote)
    def html():
        original=old_html().replace("document.body.innerHTML='<h1>Your application</h1><p>Signed in</p>';",
            'document.body.innerHTML='+json.dumps(app_html).replace('<',r'\u003c')+';')
        # Public posting has a native entry button; sign-in appears only after it.
        return original+'''<div data-automation-id=jobPostingDescription>Build Python and TypeScript software in San Francisco, United States.</div><p>R12345</p>
          <button id=entry data-automation-id=utilityButtonSignIn>Apply Manually</button><style>[data-automation-id=signInContent]{display:none}</style>
          <script>entry.onclick=()=>{document.querySelector('[data-automation-id=signInContent]').style.display='block';entry.remove();};</script>'''
    monkeypatch.setattr('tests.test_workday_accounts.auth_html',html)
    # Existing fixture transport remains the network boundary for account writes.
    # These exact official posting/receipt reads and finalizer add the application.
    def official(route):
        route.fulfill(status=200,json={'jobPostingInfo':{'externalUrl':URL,'jobPostingId':'Synthetic_R12345',
          'jobPostingSiteId':'External','jobReqId':'R12345','title':browser.job['title'],'posted':True,'canApply':True}})
    browser.page.route(ORIGIN+'/wday/cxs/acme/External/job/US/Synthetic_R12345',official)
    finals=[]
    def final(route):
        assert store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
        assert store.db.execute("SELECT 1 FROM draft_operations WHERE operation='ui:submit' AND state='intent'").fetchone()
        raw=route.request.post_data_buffer
        doc=store.db.execute("SELECT filename FROM documents WHERE kind='resume'").fetchone()[0]
        assert (store.root/'documents'/doc).read_bytes() in raw
        finals.append(route.request.url)
        route.fulfill(status=200,json={'id':remote})
    # Route through production authorization before the closed upstream reply.
    final_url=ORIGIN+'/wday/cxs/acme/jobapplication/'+remote+'/finalize'
    def final_transport(real):
        class Transport:
            request=real.request
            def abort(self):real.abort()
            def fetch(self,**kwargs):
                final(SimpleNamespace(request=self.request,fulfill=lambda **kwargs:None))
                return SimpleNamespace(status=200,headers={'content-type':'application/json'},body=lambda:json.dumps({'id':remote}).encode())
            def fulfill(self,*,response):real.fulfill(status=response.status,headers=response.headers,body=response.body())
        browser._route(Transport())
    browser.page.route(final_url,final_transport)
    browser.page.route(ORIGIN+'/wday/calypso/cxs/candidatehome/acme/External/application/'+remote+'/config',
      lambda route:route.fulfill(status=200,json={'application':{'applicationId':remote,'jobPostingAnchorId':'Synthetic_R12345',
        'jobRequisitionId':'R12345','postingTitle':browser.job['title'],'draft':False,'active':True,
        'applicationType':'EXTERNAL','status':'Application Received','dateApplied':'November 15, 2026'}}))
    store.update_settings({'remote_drafts':True})
    browser.operation_timeout=2;browser.receipt_timeout=2
    assert browser.apply(browser.job)=='confirmed'
    assert len(finals)==1 and state['writes']==['register','login'] and state['activation_gets']==1
    from hireme.application_evidence import snapshot
    assert snapshot(store,browser.aid)['verification_method']=='verified_ui_application_readback'


def test_employer_without_an_account_agreement_can_register(store,account_browser,monkeypatch):
    browser,state=account_browser;original=auth_html
    def without_agreement():
        return original().replace('<label><input data-automation-id=createAccountCheckbox type=checkbox>I agree to creating this account</label>','').replace("if(create&&!root.querySelector('input[type=checkbox]').checked)return;",'')
    monkeypatch.setattr('tests.test_workday_accounts.auth_html',without_agreement)
    browser.page.goto(URL)
    assert authenticate(browser)
    assert state['writes']==['register','login'] and browser.owner_verified


def test_unknown_account_agreement_is_held_before_registration(store,account_browser,monkeypatch):
    browser,state=account_browser;original=auth_html
    monkeypatch.setattr('tests.test_workday_accounts.auth_html',lambda:original().replace('data-automation-id=createAccountCheckbox','data-automation-id=unknownAgreement'))
    browser.page.goto(URL)
    with pytest.raises(Blocked,match='account_agreement_review'):authenticate(browser)
    assert state['writes']==[] and not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()


def test_explicit_uncertain_registration_recovery_only_signs_in_once(store,account_browser):
    browser,state=account_browser;state['mode']='lost_ack'
    with pytest.raises(Blocked,match='account_result_uncertain'):authenticate(browser)
    state['mode']='normal';state['activated']=True
    browser.workday_account_attempted=False
    assert authenticate(browser,reconcile=True)
    assert state['writes']==['register','login'] and state['activation_gets']==0
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='confirmed'


def test_interrupted_recovery_never_accumulates_registration_or_password_attempts(store,account_browser):
    browser,state=account_browser;state['mode']='lost_ack'
    with pytest.raises(Blocked):authenticate(browser)
    browser.workday_account_attempted=False
    with pytest.raises(Blocked):authenticate(browser,reconcile=True)
    browser.workday_account_attempted=False
    with pytest.raises(Blocked):authenticate(browser,reconcile=True)
    assert state['writes']==['register','login']


def test_native_account_controls_have_time_to_finish_mounting(store,account_browser,monkeypatch):
    browser,state=account_browser;original=auth_html
    def delayed():
        return original().replace("document.querySelector('[role=button]').onclick=async()=>{",
          "const readyAt=performance.now()+500;document.querySelector('[role=button]').onclick=async()=>{if(performance.now()<readyAt)return;")
    monkeypatch.setattr('tests.test_workday_accounts.auth_html',delayed)
    browser.page.goto(URL)
    assert authenticate(browser)
    assert state['writes']==['register','login']
