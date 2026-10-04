"""Private credentials, durable auth intent, and native employer account forms.

Passwords never enter the ledger, events, model context, dashboard snapshots,
or portable backups. Browser auth grants are distinct from application intent.
"""
from __future__ import annotations

import json
import secrets
import re
from urllib.parse import urlsplit, urlunsplit

from .util import Blocked, atomic_json, digest, now, private_dir


def account_key(origin, company):
    p = urlsplit(origin)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password
            or p.port not in (None, 443) or p.path not in ('', '/') or p.query or p.fragment):
        raise ValueError('An exact HTTPS account origin is required')
    if not isinstance(company, str) or not company.strip():
        raise ValueError('An employer account scope is required')
    return digest([p.hostname.lower(), company.strip().casefold()])


class AccountVault:
    def __init__(self, store):
        self.store = store
        self.directory = private_dir(store.root / 'integrations' / 'accounts')

    def save_supplied(self, origin, company, email, password, confirmation):
        """Store owner-supplied credentials without creating an employer account.

        Existing credentials and auth histories are immutable through this path;
        password changes and recovery remain separate, manual operations.
        """
        from .store import worker_lock
        if not isinstance(origin,str) or len(origin)>2048 or any(c in origin for c in ('\x00','\r','\n')):
            raise ValueError('Provide an exact HTTPS employer account website')
        if not isinstance(company,str) or not 1<=len(company.strip())<=200:
            raise ValueError('Provide the employer name, up to 200 characters')
        company=self.store.company(company)
        key=account_key(origin,company)
        if (not isinstance(password,str) or not 20<=len(password)<=128
                or any(c in password for c in ('\x00','\r','\n'))):
            raise ValueError('Use a password of 20–128 characters without line breaks')
        try:password.encode('utf-8')
        except UnicodeError:raise ValueError('Use readable characters in the password') from None
        if confirmation!=password:raise ValueError('The passwords must match')
        with worker_lock(self.store.root):
            fact=self.store.facts().get('email',{})
            if not fact.get('confirmed') or email!=fact.get('value'):
                raise ValueError('Use your confirmed applicant email from Your facts')
            if self.store.settings()['live_enabled']:
                raise ValueError('Pause submissions before saving employer credentials')
            path=self.directory/(key+'.json')
            if path.is_symlink() or self.store.db.execute('SELECT 1 FROM employer_accounts WHERE id=?',(key,)).fetchone():
                raise ValueError('Credentials or account history already exist for this employer; use manual account recovery')
            if path.exists():
                existing=self.credentials(origin,company)
                if not secrets.compare_digest(existing['password'].encode('utf-8'),password.encode('utf-8')):
                    raise ValueError('Credentials or account history already exist for this employer; use manual account recovery')
                # A lost local acknowledgement can be retried with the same
                # payload. Never replace a password or repeat an employer write.
                if self.store.db.execute("SELECT 1 FROM events WHERE kind='account_credentials_saved' AND subject=? LIMIT 1",(key,)).fetchone():
                    return {'saved':True,'account_created':False}
            else:atomic_json(path,{'email':email,'password':password})
            self.store.event('account_credentials_saved',key,{'source':'user','account_created':False})
        return {'saved':True,'account_created':False}

    def credentials(self, origin, company, *, create=False):
        key = account_key(origin, company)
        path = self.directory / (key + '.json')
        if path.is_symlink():
            raise Blocked('account_credentials_unavailable')
        email = self.store.facts().get('email', {}).get('value')
        if not email:
            raise Blocked('account_identity_unconfirmed')
        if path.exists():
            try:
                if not path.is_file() or path.stat().st_size > 4096:
                    raise ValueError('Invalid credential file')
                value = json.loads(path.read_text())
                valid = (isinstance(value, dict) and value.get('email') == email
                         and isinstance(value.get('password'), str)
                         and 20 <= len(value['password']) <= 128)
            except (OSError, ValueError):
                valid = False
            if not valid:
                raise Blocked('account_credentials_unavailable')
            return value
        if not create:
            raise Blocked('account_credentials_unavailable')
        # A restored ledger must not silently generate a different password.
        if self.store.db.execute('SELECT 1 FROM employer_accounts WHERE id=?', (key,)).fetchone():
            raise Blocked('account_credentials_unavailable')
        value = {'email': email, 'password': 'Aa1!' + secrets.token_urlsafe(24)}
        atomic_json(path, value)
        return value

    def begin_creation(self, origin, company):
        """Commit intent before an adapter may send an account-creation POST."""
        key = account_key(origin, company)
        self.credentials(origin, company)
        with self.store.transaction():
            self.store.checkpoint()
            if self.store.db.execute('SELECT 1 FROM employer_accounts WHERE id=?', (key,)).fetchone():
                raise Blocked('account_creation_held')
            self.store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
                                  (key, origin, company, 'creating', now()))
            self.store.event('account_creation_intent', key, {})
        return key

    def finish_creation(self, key, *, confirmed):
        self._finish(key,confirmed,'creating','account_creation_result')

    def begin_signin(self, key):
        with self.store.transaction():
            self.store.checkpoint()
            row=self.store.db.execute('SELECT state FROM employer_accounts WHERE id=?',(key,)).fetchone()
            if not row or row['state']!='confirmed':
                raise Blocked('account_creation_held')
            self.store.db.execute("UPDATE employer_accounts SET state='signing_in',updated=? WHERE id=?",(now(),key))
            self.store.event('account_signin_intent',key,{})

    def finish_signin(self, key, *, confirmed):
        self._finish(key,confirmed,'signing_in','account_signin_result')

    def _finish(self,key,confirmed,expected,event):
        if not isinstance(confirmed, bool):
            raise ValueError('An explicit account confirmation is required')
        with self.store.transaction():
            row = self.store.db.execute('SELECT state FROM employer_accounts WHERE id=?', (key,)).fetchone()
            if not row or row['state'] != expected:
                raise Blocked('account_creation_held')
            state = 'confirmed' if confirmed else 'uncertain'
            self.store.db.execute('UPDATE employer_accounts SET state=?,updated=? WHERE id=?', (state, now(), key))
            self.store.event(event, key, {'state': state})

    def reconcile(self, key, note):
        if not isinstance(note,str) or not 10<=len(note.strip())<=2000:
            raise ValueError('Describe how you verified the account and signed-in session')
        from .store import worker_lock
        with worker_lock(self.store.root), self.store.transaction():
            row=self.store.db.execute('SELECT * FROM employer_accounts WHERE id=?',(key,)).fetchone()
            if not row or row['state']!='uncertain':
                raise ValueError('Only an uncertain account can be reconciled')
            self.credentials(row['origin'],row['company'])
            self.store.db.execute("UPDATE employer_accounts SET state='confirmed',updated=? WHERE id=?",(now(),key))
            self.store.event('account_manually_confirmed',key,{'note':note.strip()})


def complete_native_account(browser, job):
    """Handle a same-origin, native HTML account form after posting eligibility.

    JS/SSO flows, extra required fields, agreement checkboxes, verification and
    ambiguous success remain held. A single exact POST is authorized; this is
    never application-submit authorization.
    """
    store=browser.store
    store.checkpoint()
    if not store.settings()['employer_accounts'] or not store.settings()['live_enabled']:
        raise Blocked('account_automation_disabled')
    store._check_budget(job,store.settings())
    if browser.denied_write:
        raise Blocked('unapproved_draft_write')
    page=browser.page
    if page.locator('iframe[src*="bframe"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]').count():
        raise Blocked('captcha_blocked')
    from .answers import REFUSE
    if REFUSE.search(page.locator('body').inner_text()):
        raise Blocked('human_work_sample')
    forms=page.locator('form').filter(has=page.locator('input[type=password]'))
    if forms.count()!=1:
        raise Blocked('unsupported_account_form')
    form=forms.first
    # Agreements can be accepted implicitly by the create/sign-in button,
    # without a checkbox. Inspect them before obtaining or entering passwords.
    if re.search(r'\b(?:by\s+(?:clicking|creating|registering|signing|submitting|continuing)|(?:you|i)\s+(?:agree|accept)|terms\s+of\s+(?:use|service)|user\s+agreement)\b',form.inner_text(),re.I):
        raise Blocked('account_agreement_review','Review the employer account agreement in the dedicated browser before proceeding')
    info=form.evaluate("""f=>({action:f.action,method:f.method,enctype:f.enctype,
       inputs:Array.from(f.elements).map(e=>({name:e.name,type:e.type,value:e.value,
         disabled:e.disabled,label:Array.from(e.labels||[]).map(l=>l.textContent.trim()).join(' ')}))})""")
    action=urlsplit(info['action']);current=urlsplit(page.url)
    local=bool(browser.test_url and page.url.startswith(browser.test_url))
    if (info['method'].lower()!='post' or info['enctype']!='application/x-www-form-urlencoded'
            or action.netloc!=current.netloc or action.scheme!=current.scheme
            or action.username or action.password or action.query or action.fragment
            or (not local and (action.scheme!='https' or action.hostname!=browser.current_host))):
        raise Blocked('unsupported_account_destination')
    fields=[f for f in info['inputs'] if not f['disabled'] and f['type'] not in ('hidden','submit','button')]
    passwords=[f for f in fields if f['type']=='password']
    emails=[f for f in fields if f['type'] in ('email','text') and re.fullmatch(r'(?:email(?: address)?|username)',f['label'],re.I)]
    if (len(passwords) not in (1,2) or len(emails)!=1 or len(fields)!=len(passwords)+1
            or any(not f['name'] for f in fields) or len({f['name'] for f in fields})!=len(fields)):
        raise Blocked('unsupported_account_form')
    creating=len(passwords)==2
    expected=re.compile(r'^(?:create account|register|sign up)$' if creating else r'^(?:sign in|log in)$',re.I)
    buttons=form.get_by_role('button',name=expected)
    if buttons.count()!=1 or buttons.first.evaluate('(e)=>e.type')!='submit':
        raise Blocked('unsupported_account_form')
    origin=urlunsplit(('https',current.netloc,'','','')) if not local else 'https://fixture.invalid'
    # Scope is stable across applications for this employer on this exact host.
    company=store.company(job['company'])
    vault=AccountVault(store)
    key=account_key(origin,company)
    row=store.db.execute('SELECT state FROM employer_accounts WHERE id=?',(key,)).fetchone()
    if row and row['state']!='confirmed':
        raise Blocked('account_creation_held')
    if creating and row:
        raise Blocked('account_creation_held')
    if not creating and not row:
        raise Blocked('account_credentials_unavailable')
    # Browser errors can include typed values in their call logs. Keep every
    # credential-bearing operation behind a sanitized pre-write boundary.
    try:
        credentials=vault.credentials(origin,company,create=creating)
        controls=form.locator('input').all()
        for f in fields:
            matches=[el for el in controls if el.get_attribute('name')==f['name']]
            if len(matches)!=1:
                raise Blocked('unsupported_account_form')
            matches[0].fill(credentials['password'] if f['type']=='password' else credentials['email'])
        if not form.evaluate('(f)=>f.checkValidity()'):
            raise Blocked('account_password_policy')
        pairs=form.evaluate('(f)=>Array.from(new FormData(f).entries())')
        # Submit-button values are intentionally unsupported; no extra hidden writes.
        if buttons.first.get_attribute('name'):
            raise Blocked('unsupported_account_form')
    except Blocked:
        raise
    except Exception:
        raise Blocked('account_fields_unavailable','Account fields could not be filled or validated; inspect the dedicated browser before retrying') from None
    store.checkpoint()
    if not store.settings()['employer_accounts'] or not store.settings()['live_enabled']:
        raise Blocked('account_automation_disabled')
    if creating:
        vault.begin_creation(origin,company)
    else:
        vault.begin_signin(key)
    browser.auth_write={'url':info['action'],'pairs':pairs,'used':False}
    confirmed=False
    try:
        store.checkpoint()
        buttons.first.click()
        browser._wait_ready()
        store.checkpoint()
        body=page.locator('body').inner_text()
        confirmed=(browser.auth_write['used'] and not browser.denied_write
                   and urlsplit(page.url).netloc==current.netloc
                   and not page.locator('input[type=password]').count()
                   and bool(re.search(r'\b(?:sign out|log out|logout)\b',body,re.I)))
        if creating:
            confirmed=confirmed and bool(re.search(r'account (?:successfully )?created|registration (?:complete|successful)',body,re.I))
    except Blocked:
        raise
    except Exception:
        raise Blocked('account_result_uncertain') from None
    finally:
        browser.auth_write=None
        if creating:
            vault.finish_creation(key,confirmed=bool(confirmed))
        else:
            vault.finish_signin(key,confirmed=bool(confirmed))
    if not confirmed:
        raise Blocked('account_result_uncertain')
    store.event('account_session_ready',key,{})
