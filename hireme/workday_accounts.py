"""Bounded Workday account UI actions, separate from application write grants."""
from __future__ import annotations

from datetime import datetime
import json
import re
import secrets
import time
from urllib.parse import parse_qsl,urlsplit

from .accounts import AccountVault,account_key
from .approval_boundary import ApprovalBoundary
from .util import Blocked,digest,now
from .workday_site import site_for_url


def nonce_read(agent,request):
    if not agent.job:return False
    try:site=site_for_url(agent.job['url']);parsed=urlsplit(request.url)
    except (ValueError,TypeError):return False
    return (request.method=='GET' and request.resource_type in {'xhr','fetch'} and parsed.scheme=='https'
        and parsed.hostname==site.host and parsed.port in (None,443) and not parsed.username and not parsed.password
        and not parsed.query and not parsed.fragment
        and bool(re.fullmatch(r'/(?:[a-z]{2}-[A-Z]{2}/)?'+re.escape(site.site)+r'/login',parsed.path)))


def remember_nonce(agent,request,data):
    if not nonce_read(agent,request) or not isinstance(data,dict):return
    value=data.get('sessionSecureToken')
    if isinstance(value,str) and 1<=len(value)<=16384 and not re.search(r'[\x00-\x20\x7f]',value):
        site=site_for_url(agent.job['url'])
        agent.account_tokens[(site.host,site.site)]=value


class AuthGrant:
    """One source-bound native login/register POST; no generic account access."""
    def __init__(self,agent,kind,credential):
        self.agent=agent;self.store=agent.store;self.site=site_for_url(agent.job['url'])
        if kind not in {'login','register'}:raise ValueError('Unknown Workday account operation')
        self.kind=kind;self.credential=credential;self.used=False;self.status=None;self.result=None
        self.key=account_key('https://'+self.site.host,self.site.tenant)
        self.boundary=ApprovalBoundary(self.store,agent.job,require_live=True)
        self.generation=self.store.control_generation()
        self.context=agent.page.evaluate('''()=>({tenant:workday.tenant,site:workday.siteId,
            secure:sessionStorage.getItem('sessionSecureToken'),location:location.pathname+location.search})''')
        if (self.context.get('tenant')!=self.site.tenant or self.context.get('site')!=self.site.site
                or urlsplit(agent.page.url).hostname!=self.site.host):raise Blocked('account_identity_unconfirmed')

    def allow(self,request):
        self.boundary.check()
        if (self.used or self.agent.preparing or not self.store.settings()['employer_accounts']
                or self.generation!=self.store.control_generation()):return False
        account=self.store.db.execute('SELECT state FROM employer_accounts WHERE id=?',(self.key,)).fetchone()
        if not account or account['state']!=('creating' if self.kind=='register' else 'signing_in'):return False
        parsed=urlsplit(request.url)
        if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!=self.site.host
                or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment
                or not re.fullmatch(r'/(?:[a-z]{2}-[A-Z]{2}/)?'+re.escape(self.site.site)+'/'+self.kind,parsed.path)):
            return False
        from .workday_account_policy import credentials
        current=credentials(self.store,self.agent.job)
        if current!=self.credential:raise Blocked('account_credentials_unavailable','The saved account credentials changed')
        if not request.headers.get('content-type','').startswith('application/x-www-form-urlencoded'):return False
        try:
            raw=request.post_data_buffer or b''
            if len(raw)>16384:return False
            pairs=parse_qsl(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True)
            body=dict(pairs)
            if len(body)!=len(pairs) or not {'username','password'}<=set(body)<={'username','password','sessionSecureToken','successRedirectUri'}:return False
            if (body['username']!=self.credential['email'] or not secrets.compare_digest(
                    body['password'].encode(),self.credential['password'].encode())):return False
            token=getattr(self.agent,'account_tokens',{}).get((self.site.host,self.site.site),self.context.get('secure'))
            if 'sessionSecureToken' in body and (not token or body['sessionSecureToken']!=token):return False
            if 'successRedirectUri' in body and (self.kind!='register' or body['successRedirectUri']!=self.context['location']):return False
            query=parse_qsl(parsed.query,keep_blank_values=True)
            if query and (self.kind!='register' or query!=[('redirect',self.context['location'])]):return False
        except (ValueError,UnicodeError,TypeError):return False
        self.used=True
        return True

    def capture(self,status,data):
        self.status=status
        if not isinstance(data,dict):return
        # Interpret only fixed protocol markers. Raw messages may contain the
        # applicant email or credential material and never leave this object.
        if data.get('type')=='ERROR':self.result='rejected'
        elif 200<=status<300 and (data.get('type')=='SUCCESS' or data.get('widget') in {'root','redirect'}):self.result='accepted'
        self.data=data


def _checkpoint(agent):
    agent.store.checkpoint()
    if not agent.store.settings()['live_enabled'] or not agent.store.settings()['employer_accounts']:
        raise Blocked('account_automation_disabled')
    site=site_for_url(agent.job['url']);parsed=urlsplit(agent.page.url)
    if parsed.scheme!='https' or parsed.hostname!=site.host or parsed.port not in (None,443):raise Blocked('unexpected_redirect')
    frames=agent.page.locator('iframe[src*="bframe"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]')
    if frames.evaluate_all('''nodes=>nodes.some(e=>e.checkVisibility({visibilityProperty:true,opacityProperty:true})&&
        e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0)'''):raise Blocked('captcha_blocked')


def _root(page):
    roots=page.locator('[data-automation-id="signInContent"]:visible')
    if roots.count()==1:return roots
    dialogs=page.locator('[data-automation-id="popUpDialog"]:visible')
    if dialogs.count()==1:return dialogs
    forms=page.locator('form[data-automation-id="signInFormo"]:visible')
    if forms.count()==1:return forms.locator('xpath=..')
    raise Blocked('unsupported_account_form','The Workday account form is not uniquely identified')


def _fill(root,automation,value):
    control=root.locator('[data-automation-id="'+automation+'"]:visible')
    if control.count()!=1:raise Blocked('unsupported_account_form')
    try:
        control.fill(value)
        if control.input_value()!=value:raise Blocked('account_credentials_unavailable')
    except Blocked:raise
    except Exception:raise Blocked('account_credentials_unavailable','The account fields could not accept the saved credentials') from None


def _navigate_control(agent,automation,name):
    _checkpoint(agent)
    root=_root(agent.page)
    control=root.locator('[data-automation-id="'+automation+'"]:visible')
    if control.count()!=1:control=root.get_by_role('button',name=name,exact=True)
    if control.count()!=1:raise Blocked('unsupported_account_form')
    control.click();agent.page.wait_for_timeout(300)


def _entry(agent):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        _checkpoint(agent)
        if agent.page.locator('[data-automation-id="email"]:visible').count():return
        choice=agent.page.get_by_role('button',name=re.compile(r'^sign in with email$',re.I))
        if choice.count()==1:choice.click()
        else:
            entry=agent.page.locator('[data-automation-id="utilityButtonSignIn"]:visible')
            if entry.count()==1:entry.click()
        agent.page.wait_for_timeout(250)
    raise Blocked('unsupported_account_form','Workday did not present an email/password account form')


def _state(store,key,state,event,detail=None):
    with store.transaction():
        store.db.execute('UPDATE employer_accounts SET state=?,updated=? WHERE id=?',(state,now(),key))
        store.event(event,key,detail or {'state':state})


def _since(store,key):
    row=store.db.execute("SELECT timestamp FROM events WHERE kind='account_creation_intent' AND subject=? ORDER BY seq DESC LIMIT 1",(key,)).fetchone()
    if not row:raise Blocked('account_creation_held')
    return datetime.fromisoformat(row['timestamp']).timestamp()


def _submit(agent,kind,credential,key):
    _checkpoint(agent);root=_root(agent.page)
    _fill(root,'email',credential['email']);_fill(root,'password',credential['password'])
    if kind=='register':
        _fill(root,'verifyPassword',credential['password'])
        agreement=root.locator('[data-automation-id="createAccountCheckbox"]:visible')
        if agreement.count()>1:raise Blocked('account_agreement_review','The account creation agreement changed')
        if agreement.count()==1:agreement.check()
        elif root.locator('input[type=checkbox]:visible,[role=checkbox]:visible').count():
            raise Blocked('account_agreement_review','An unrecognized account agreement needs review')
    # Never expose credential fields or the raw transport to the application
    # planner. The public click-filter runs Workday's own verification client.
    button=root.get_by_role('button',name='Create Account' if kind=='register' else 'Sign In',exact=True)
    if button.count()!=1:raise Blocked('unsupported_account_form')
    grant=AuthGrant(agent,kind,credential)
    if kind=='register':AccountVault(agent.store).begin_creation('https://'+grant.site.host,grant.site.tenant)
    else:_state(agent.store,key,'signing_in','account_signin_intent',{})
    agent.account_operation=grant
    try:
        _checkpoint(agent);grant.boundary.check();button.click()
        deadline=time.monotonic()+getattr(agent,'account_operation_timeout',20)
        while time.monotonic()<deadline:
            _checkpoint(agent)
            if grant.status is not None:break
            agent.page.wait_for_timeout(100)
        if grant.result=='accepted':
            _state(agent.store,key,'activation_pending' if kind=='register' else 'signin_acknowledged',
                   'workday_account_operation_acknowledged',{'operation':kind,'status':grant.status})
            return grant
        if grant.result=='rejected':
            # An explicit rejection is not a successful creation. A native
            # existing-account diagnostic may use the supplied login once.
            message=str(getattr(grant,'data',{}).get('message',''))
            exists=kind=='register' and bool(re.search(r'already (?:have|has|exists)|account (?:already )?exists',message,re.I))
            _state(agent.store,key,'existing_account' if exists else 'rejected','workday_account_operation_rejected',{'operation':kind,'status':grant.status,'existing_account':exists})
            if exists:return grant
            raise Blocked('account_registration_rejected' if kind=='register' else 'account_signin_rejected','The employer rejected the account operation; verify its saved credentials')
        _state(agent.store,key,'uncertain','workday_account_operation_uncertain',{'operation':kind})
        raise Blocked('account_result_uncertain','Workday did not acknowledge the account operation; another creation is held')
    except Blocked:
        row=agent.store.db.execute('SELECT state FROM employer_accounts WHERE id=?',(key,)).fetchone()
        if row and row['state'] in {'creating','signing_in'}:_state(agent.store,key,'uncertain','workday_account_operation_uncertain',{'operation':kind})
        raise
    except Exception:
        _state(agent.store,key,'uncertain','workday_account_operation_uncertain',{'operation':kind})
        raise Blocked('account_result_uncertain','The account operation was interrupted; check the employer account before another creation') from None
    finally:
        agent.account_operation=None


def _owned(agent,key):
    from .browser_identity import verify_workday_account
    if not verify_workday_account(agent):return False
    _state(agent.store,key,'confirmed','workday_account_owner_verified',{'owner_hash':digest(agent.store.facts()['email']['value'].casefold())})
    agent.owner_verified=True
    try:agent.page.wait_for_function("()=>![...document.querySelectorAll('input[type=password]')].some(e=>e.checkVisibility())",timeout=5000)
    except Exception:raise Blocked('account_blocked','The confirmed Workday session did not leave its sign-in screen') from None
    return True


def _activate(agent,key,return_url):
    from .workday_account_mail import find_activation
    since=_since(agent.store,key)
    deadline=time.monotonic()+getattr(agent,'account_verification_timeout',min(180,max(30,agent.store.settings()['gmail_code_wait_seconds'])))
    while time.monotonic()<deadline:
        _checkpoint(agent)
        link=find_activation(agent.store,agent.job,since,'workday-account:'+key)
        if link:
            _state(agent.store,key,'activating','workday_account_activation_intent',{})
            try:
                response=agent.page.goto(link,wait_until='domcontentloaded',timeout=30000)
                _checkpoint(agent)
                if not response or not 200<=response.status<400:raise Blocked('account_verification_pending')
                text=agent.page.locator('body').inner_text()
                if re.search(r'(?:link|token).{0,40}(?:expired|invalid)|unable to activate|activation failed',text,re.I):
                    raise Blocked('account_verification_pending','The employer did not activate this account')
                _state(agent.store,key,'activation_acknowledged','workday_account_activation_acknowledged',{'status':response.status})
                agent.page.goto(return_url,wait_until='domcontentloaded',timeout=30000)
                return
            except Exception as error:
                _state(agent.store,key,'uncertain','workday_account_activation_uncertain',{})
                if isinstance(error,Blocked):raise
                raise Blocked('account_result_uncertain','The account activation was interrupted; another activation is held') from None
        agent.page.wait_for_timeout(1000)
    raise Blocked('account_verification_pending','Waiting for the employer account activation email; creation will not be repeated')


def authenticate(agent):
    """Called only after exact posting verification and applicant eligibility."""
    _checkpoint(agent)
    if agent.preparing or not getattr(agent,'account_posting_verified',False):raise Blocked('account_blocked')
    if getattr(agent,'workday_account_attempted',False):raise Blocked('account_blocked','The account flow did not establish the confirmed applicant session')
    agent.workday_account_attempted=True
    from . import workday_account_policy as policy
    site=site_for_url(agent.job['url']);key=account_key('https://'+site.host,site.tenant)
    credential=policy.credentials(agent.store,agent.job,create=True)
    return_url=agent.page.url
    row=agent.store.db.execute('SELECT state FROM employer_accounts WHERE id=?',(key,)).fetchone()
    if row and _owned(agent,key):return True
    if row and row['state'] in {'uncertain','creating','signing_in','activating','rejected'}:
        raise Blocked('account_result_uncertain','The existing account operation needs verification; another creation is held')
    if row and row['state']=='activation_pending':
        _activate(agent,key,return_url)
        if _owned(agent,key):return True
    _entry(agent)
    if not row:
        if not policy.status(agent.store)['enabled']:raise Blocked('workday_registration_not_configured')
        if not _root(agent.page).locator('[data-automation-id="verifyPassword"]:visible').count():
            _navigate_control(agent,'createAccountLink','Create Account')
        grant=_submit(agent,'register',credential,key)
        if _owned(agent,key):return True
        if grant.result!='rejected':
            _activate(agent,key,return_url)
            if _owned(agent,key):return True
        _entry(agent)
    root=_root(agent.page)
    if root.locator('[data-automation-id="verifyPassword"]:visible').count():
        _navigate_control(agent,'signInLink','Sign In')
    _submit(agent,'login',credential,key)
    if not _owned(agent,key):raise Blocked('account_identity_unconfirmed','The Workday session could not be bound to the confirmed applicant')
    return True
