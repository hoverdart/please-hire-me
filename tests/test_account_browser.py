import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from hireme.accounts import AccountVault
from hireme.browser import Browser
from hireme.util import Blocked, digest


@pytest.fixture
def account_site(request):
    mode=getattr(request,'param','register')
    writes=[]
    application=(Path(__file__).parent/'fixtures/application.html').read_text()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,html):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(html.encode())
        def do_GET(self):
            if self.path=='/posting':
                self.respond('<h1>Software Engineer Intern Summer 2027</h1><p>Build Python and TypeScript software.</p><a href="/account">Apply</a>')
            else:
                confirm='' if mode=='login' else '<label>Confirm password<input name="confirm" type="password" required></label>'
                extra='<label>Accept terms<input type="checkbox" name="terms" required></label>' if mode=='terms' else '<p>By creating an account you agree to the Terms of Service.</p>' if mode=='implicit-terms' else ''
                action='http://attacker.invalid/register' if mode=='redirect' else '/register'
                button='Sign in' if mode=='login' else 'Create account'
                document=f'<form method="post" action="{action}"><label>Email<input name="email" type="email" required></label><label>Password<input name="password" type="password" required></label>{confirm}{extra}<button type="submit">{button}</button></form>'
                if mode=='maxlength':document=document.replace('type="password"','type="password" maxlength="20"')
                if mode=='replace-email':document+="<script>document.querySelector('[name=email]').addEventListener('input',event=>event.target.value='different@candidate.invalid')</script>"
                if mode=='serialize-password':document+="<script>document.querySelector('form').addEventListener('formdata',event=>event.formData.set('password','Different-password-only42!'))</script>"
                self.respond(document)
        def do_POST(self):
            data=self.rfile.read(int(self.headers['Content-Length']));writes.append((self.path,data))
            if self.path=='/register':
                if mode=='uncertain':self.respond('<p>Enter verification code</p><input aria-label="Code">')
                else:self.respond(application.replace('<body>','<body><p>Account created. <a href="/logout">Sign out</a></p>'))
            else:self.respond('OK')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    job={'id':digest(base+'/posting'),'url':base+'/posting','host':'127.0.0.1','company':'Synthetic ATS',
         'title':'Software Engineer Intern Summer 2027','location':'San Francisco, United States','source':'fixture'}
    yield base,job,writes
    server.shutdown();server.server_close()


def test_registration_and_application_have_separate_write_intents(store,account_site):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    with Browser(store,test_url=base) as browser:
        assert browser.apply(job)=='confirmed'
    assert [path for path,_ in writes]==['/register','/submit']
    submitted=parse_qs(writes[0][1].decode())
    assert submitted['email']==['test@candidate.invalid']
    assert submitted['password']==submitted['confirm']
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='confirmed'
    secret=submitted['password'][0]
    assert secret not in str([dict(row) for row in store.db.execute('SELECT * FROM events')])
    assert secret not in store.db.execute('SELECT package FROM applications').fetchone()[0]


@pytest.mark.parametrize('enabled,live',[(False,True),(True,False)])
def test_disabled_and_prepare_modes_do_not_create_accounts(store,account_site,enabled,live):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':enabled})
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='account_blocked'):browser.apply(job,live=live)
    assert not writes


def test_native_registration_uses_owner_supplied_password_exactly(store,account_site):
    base,job,writes=account_site
    store.update_settings({'live_enabled':False})
    secret='Synthetic-user-chosen-password42!'
    AccountVault(store).save_supplied('https://fixture.invalid',job['company'],store.facts()['email']['value'],secret,secret)
    store.update_settings({'employer_accounts':True,'live_enabled':True})
    with Browser(store,test_url=base) as browser:assert browser.apply(job)=='confirmed'
    registration=next(body for path,body in writes if 'register' in path)
    assert parse_qs(registration.decode())['password']==[secret]
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='confirmed'
    assert secret.encode() not in store.path.read_bytes()


@pytest.mark.parametrize('account_site',['implicit-terms'],indirect=True)
def test_implicit_account_agreement_stops_before_credentials_or_remote_writes(store,account_site,monkeypatch):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    def forbidden(*args,**kwargs):raise AssertionError('Credentials accessed before account agreement review')
    monkeypatch.setattr(AccountVault,'credentials',forbidden)
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='account_agreement_review'):browser.apply(job)
    assert not writes and not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()


@pytest.mark.parametrize('account_site',['terms','redirect'],indirect=True)
def test_unsupported_forms_never_send_credentials(store,account_site):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='unsupported_account'):browser.apply(job)
    assert not writes


@pytest.mark.parametrize('account_site',['uncertain'],indirect=True)
def test_verification_holds_account_without_retry_or_application(store,account_site):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='account_result_uncertain'):browser.apply(job)
        with pytest.raises(Blocked,match='account_creation_held'):browser.apply(job)
    assert len(writes)==1
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='uncertain'
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('account_site',['login'],indirect=True)
def test_sign_in_reuses_stored_password_without_creation(store,account_site):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    vault=AccountVault(store)
    vault.credentials('https://fixture.invalid',store.company(job['company']),create=True)
    key=vault.begin_creation('https://fixture.invalid',store.company(job['company']))
    vault.finish_creation(key,confirmed=True)
    with Browser(store,test_url=base) as browser:assert browser.apply(job)=='confirmed'
    assert len(writes)==2
    assert store.db.execute('SELECT count(*) FROM employer_accounts').fetchone()[0]==1


def test_auth_write_grant_is_exact_payload_and_single_use(store):
    store.update_settings({'employer_accounts':True})
    browser=Browser(store)
    browser.auth_write={'url':'https://jobs.lever.co/account/register','pairs':[['email','test@candidate.invalid'],['password','secret']],'used':False}
    class Request:
        method='POST'
        def __init__(self,url,body):self.url=url;self.post_data=body
    class Route:
        def __init__(self,url,body):self.request=Request(url,body);self.action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    wrong=Route(browser.auth_write['url'],'email=attacker&password=secret');browser._route(wrong);assert wrong.action=='abort'
    right=Route(browser.auth_write['url'],'email=test%40candidate.invalid&password=secret');browser._route(right);assert right.action=='continue'
    repeat=Route(browser.auth_write['url'],right.request.post_data);browser._route(repeat);assert repeat.action=='abort'


def test_pause_after_intent_stops_account_post(store,account_site,monkeypatch):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    store.run_generation=store.control_generation()
    original=AccountVault.begin_creation
    def pause(vault,*args):
        key=original(vault,*args)
        vault.store.update_settings({'live_enabled':False})
        return key
    monkeypatch.setattr(AccountVault,'begin_creation',pause)
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='paused'):browser.apply(job)
    assert not writes
    assert store.db.execute('SELECT state FROM employer_accounts').fetchone()[0]=='uncertain'


@pytest.mark.parametrize('failure_stage',['fill','validation','serialization'])
def test_secret_field_errors_are_sanitized_before_account_intent(store,account_site,monkeypatch,failure_stage):
    from playwright.sync_api import Locator
    base,job,writes=account_site
    secret='Synthetic-error-password-only42!'
    store.update_settings({'live_enabled':False})
    AccountVault(store).save_supplied('https://fixture.invalid',job['company'],store.facts()['email']['value'],secret,secret)
    store.update_settings({'employer_accounts':True,'live_enabled':True})
    original_fill=Locator.fill;original_evaluate=Locator.evaluate
    def fill(locator,value,*args,**kwargs):
        if failure_stage=='fill' and value==secret:raise RuntimeError('Call log: fill '+secret)
        return original_fill(locator,value,*args,**kwargs)
    def evaluate(locator,expression,*args,**kwargs):
        target='checkValidity' if failure_stage=='validation' else 'new FormData'
        if failure_stage!='fill' and target in expression:raise RuntimeError('Call log: '+secret)
        return original_evaluate(locator,expression,*args,**kwargs)
    monkeypatch.setattr(Locator,'fill',fill);monkeypatch.setattr(Locator,'evaluate',evaluate)
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='account_fields_unavailable') as error:browser.apply(job)
        assert browser.auth_write is None
    assert secret not in str(error.value)
    assert not writes and not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert secret.encode() not in store.path.read_bytes()


@pytest.mark.parametrize('account_site',['maxlength','replace-email','serialize-password'],indirect=True)
def test_transformed_credentials_stop_before_registration_intent(store,account_site):
    base,job,writes=account_site
    secret='Synthetic-user-chosen-password42!'
    store.update_settings({'live_enabled':False})
    vault=AccountVault(store)
    vault.save_supplied('https://fixture.invalid',job['company'],store.facts()['email']['value'],secret,secret)
    store.update_settings({'employer_accounts':True,'live_enabled':True})
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='account_fields_changed') as error:browser.apply(job)
        assert browser.auth_write is None
    assert secret not in str(error.value)
    assert not writes and not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert vault.credentials('https://fixture.invalid',store.company(job['company']))['password']==secret
    assert secret.encode() not in store.path.read_bytes()


@pytest.mark.parametrize('account_site',['register'],indirect=True)
def test_changed_applicant_identity_stops_before_creation_intent(store,account_site,monkeypatch):
    base,job,writes=account_site
    store.update_settings({'employer_accounts':True})
    vault=AccountVault(store);origin='https://fixture.invalid';company=store.company(job['company'])
    vault.credentials(origin,company,create=True)
    original=AccountVault.credentials
    def change_email(vault,*args,**kwargs):
        value=original(vault,*args,**kwargs)
        vault.store.put_facts({'email':'changed@candidate.invalid'})
        return value
    monkeypatch.setattr(AccountVault,'credentials',change_email)
    with Browser(store,test_url=base) as browser:
        with pytest.raises(Blocked,match='account_identity_unconfirmed'):browser.apply(job)
    assert not writes and not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='account_creation_intent'").fetchone()
