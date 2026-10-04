"""Owner-authorized Gmail access. Inbox content never reaches the model."""
from __future__ import annotations

import base64
import json
import re
import time
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from pathlib import Path

from .util import Blocked, atomic_json, private_dir

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.send']
GREENHOUSE_SENDERS = {'greenhouse.io', 'greenhouse-mail.io', 'us.greenhouse-mail.io', 'eu.greenhouse-mail.io'}


def greenhouse_employer_name(job, checkpoint=None, deadline=None):
    """Use this board's public employer name, not an acronym guessed from mail."""
    from urllib.parse import urlsplit
    from .net import Network
    parsed=urlsplit(job.get('url',''))
    if (parsed.scheme!='https' or parsed.hostname!=job.get('host') or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.hostname not in {
                'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}):return None
    match=re.fullmatch(r'/([A-Za-z0-9_-]{1,100})/jobs/\d+',parsed.path)
    if not match:return None
    data=Network(checkpoint=checkpoint,deadline=deadline).json('https://boards-api.greenhouse.io/v1/boards/'+match[1])
    name=data.get('name') if isinstance(data,dict) else None
    return name.strip() if isinstance(name,str) and 1<=len(name.strip())<=200 and not re.search(r'[\r\n\x00]',name) else None


def owner_email(store):
    value = store.facts().get('email', {}).get('value', '')
    if not value:
        raise Blocked('gmail_owner_missing', 'Confirm your application email first')
    return value.casefold()


def _secret_path(store, name):
    path = private_dir(store.root / 'integrations') / name
    if path.is_symlink():
        raise ValueError('Unsafe integration file')
    return path


def import_client(store, path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Choose the Google Desktop OAuth client JSON, up to 64 KiB')
    data = json.loads(path.read_text())
    client = data.get('installed', {})
    if not client.get('client_id') or not client.get('client_secret'):
        raise ValueError('Create a Desktop app OAuth client in Google Cloud')
    if client.get('auth_uri') not in ('https://accounts.google.com/o/oauth2/auth', 'https://accounts.google.com/o/oauth2/v2/auth') or client.get('token_uri') not in ('https://oauth2.googleapis.com/token', 'https://accounts.google.com/o/oauth2/token'):
        raise ValueError('OAuth endpoints must be the standard Google endpoints')
    atomic_json(_secret_path(store, 'gmail-client.json'), data)
    store.event('gmail_client_imported', 'gmail', {})


def _service(credentials):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    return build('gmail','v1',http=AuthorizedHttp(credentials,http=httplib2.Http(timeout=15)),cache_discovery=False)


def connect(store):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise Blocked('gmail_dependencies_missing', 'Install please-hire-me[gmail]') from None
    path = _secret_path(store, 'gmail-client.json')
    if not path.is_file():
        raise Blocked('gmail_client_missing', 'Import Google Desktop OAuth credentials first')
    expected = owner_email(store)
    flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    credentials = flow.run_local_server(host='127.0.0.1', port=0, open_browser=True,
                                         access_type='offline', prompt='consent', login_hint=expected,timeout_seconds=300)
    if not credentials.refresh_token or not credentials.has_scopes(SCOPES):
        raise Blocked('gmail_offline_access_missing', 'Authorize read and send access with offline access')
    service = _service(credentials)
    actual = service.users().getProfile(userId='me').execute()['emailAddress'].casefold()
    if actual != expected:
        raise Blocked('gmail_account_mismatch', 'Connect the confirmed application email account')
    atomic_json(_secret_path(store, 'gmail-token.json'), json.loads(credentials.to_json()))
    atomic_json(_secret_path(store, 'gmail-account.json'), {'email': actual})
    store.event('gmail_connected', 'gmail', {'email': actual})
    return {'email': actual, 'connected': True}


def status(store):
    token = _secret_path(store, 'gmail-token.json')
    account = _secret_path(store, 'gmail-account.json')
    try:email = json.loads(account.read_text()).get('email') if account.is_file() else None
    except (OSError,ValueError,TypeError,AttributeError):email=None
    return {'client_configured': _secret_path(store, 'gmail-client.json').is_file(),
            'connected': token.is_file() and email == store.facts().get('email', {}).get('value', '').casefold(),
            'email': email}


def disconnect(store):
    for name in ('gmail-token.json', 'gmail-account.json'):
        _secret_path(store, name).unlink(missing_ok=True)
    store.event('gmail_disconnected', 'gmail', {})


class GmailClient:
    def __init__(self, store, service=None):
        self.store = store
        self.email = owner_email(store)
        if service is None:
            try:
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request
                from googleapiclient.discovery import build
            except ImportError:
                raise Blocked('gmail_dependencies_missing', 'Install please-hire-me[gmail]') from None
            path = _secret_path(store, 'gmail-token.json')
            if not path.is_file() or not status(store)['connected']:
                raise Blocked('gmail_not_connected', 'Run hireme gmail connect locally')
            data = json.loads(path.read_text())
            if data.get('token_uri') not in ('https://oauth2.googleapis.com/token', 'https://accounts.google.com/o/oauth2/token'):
                raise Blocked('gmail_credentials_invalid')
            credentials = Credentials.from_authorized_user_info(data)
            if not credentials.has_scopes(SCOPES):
                raise Blocked('gmail_scopes_missing')
            try:
                if not credentials.valid:
                    if not credentials.refresh_token:
                        raise Blocked('gmail_reconnect_required')
                    credentials.refresh(lambda *args,**kwargs:Request()(*args,**{**kwargs,'timeout':15}))
                    atomic_json(path, json.loads(credentials.to_json()))
                service = _service(credentials)
            except Blocked:
                raise
            except Exception:
                raise Blocked('gmail_reconnect_required', 'Refresh failed; reconnect Gmail locally') from None
        self.service = service
        actual = service.users().getProfile(userId='me').execute()['emailAddress'].casefold()
        if actual != self.email:
            raise Blocked('gmail_account_mismatch')

    def find_code(self, company, since, challenge_id, length=8):
        self.store.checkpoint()
        # Fixed ATS sender domains and a time window; no model-authored mailbox queries.
        query = f'after:{int(since)-5} ' + '{' + ' '.join('from:@' + d for d in sorted(GREENHOUSE_SENDERS)) + '}'
        messages = self.service.users().messages()
        candidates = messages.list(userId='me', q=query, maxResults=10).execute().get('messages', [])
        deadline=time.monotonic()+15
        for item in candidates:
            if time.monotonic()>deadline:break
            self.store.checkpoint()
            if self.store.db.execute('SELECT 1 FROM mail_consumptions WHERE message_id=?', (item['id'],)).fetchone():
                continue
            message = messages.get(userId='me', id=item['id'], format='full').execute()
            code = verification_code(message, self.email, company, since, length)
            if code:
                # Reserve once before the browser can act; never persist the OTP itself.
                with self.store.transaction():
                    used = self.store.db.execute('INSERT OR IGNORE INTO mail_consumptions VALUES(?,?,?)',
                                                  (item['id'], challenge_id, time.time())).rowcount
                if used:
                    return code
        return None

    def send_report(self, subject, body, message_id):
        if '\n' in subject or '\r' in subject or len(subject) > 200:
            raise ValueError('Invalid report subject')
        message = EmailMessage()
        message['To'] = self.email
        message['From'] = self.email
        message['Subject'] = subject
        message['Message-ID'] = message_id
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')
        return self.service.users().messages().send(userId='me', body={'raw': raw}).execute()['id']


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_endtag(self, tag):
        self.parts.append('\n')
    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'div', 'p', 'td'):
            self.parts.append('\n')


def _body(part, depth=0):
    if depth > 12 or len(part.get('parts', [])) > 100:
        return ''
    data = part.get('body', {}).get('data', '')
    if data and len(data) <= 200000 and part.get('mimeType') in ('text/plain', 'text/html'):
        try:
            text = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', errors='replace')
        except (ValueError, TypeError):
            return ''
        if part.get('mimeType') == 'text/html':
            parser = _HTMLText();parser.feed(text);text = ''.join(parser.parts)
        return text
    return '\n'.join(_body(child, depth+1) for child in part.get('parts', []))[:200000]


def verification_code(message, recipient, company, since, length=8):
    try:
        received = int(message['internalDate']) / 1000
    except (ValueError, KeyError, TypeError):
        return None
    if not since-5 <= received <= time.time()+60 or time.time()-since > 15*60:
        return None
    headers = {}
    for h in message.get('payload', {}).get('headers', []):
        headers.setdefault(h.get('name', '').casefold(), h.get('value', ''))
    sender = parseaddr(headers.get('from', ''))[1].casefold()
    domain = sender.rsplit('@', 1)[-1]
    if domain not in GREENHOUSE_SENDERS:
        return None
    recipients = {a.casefold() for _, a in getaddresses([headers.get('to', '')])}
    if recipient.casefold() not in recipients:
        return None
    auth = headers.get('authentication-results', '')
    if not auth.lstrip().startswith('mx.google.com;') or not re.search(
            r'dkim=pass\b[^;]*(?:header\.d=|header\.i=@)' + re.escape(domain) + r'(?:\s|;|$)', auth, re.I):
        return None
    text = headers.get('subject', '') + '\n' + _body(message.get('payload', {}))
    company_compact=''.join(re.findall(r'[a-z0-9]+',company.casefold()))
    words=re.findall(r'[a-z0-9]+',text.casefold())
    # Board slugs can omit spaces/punctuation. Match complete contiguous words,
    # never a substring of another employer name or disconnected text fragments.
    if not company_compact or not any(''.join(words[start:end])==company_compact
            for start in range(len(words)) for end in range(start+1,min(start+11,len(words)+1))):
        return None
    if not re.search(r'application', text, re.I) or length not in (6, 8):
        return None
    codes = set(re.findall(r'(?:verification|security|confirmation|one[- ]time)\s+code\s*(?:is\s*)?[:\-]?\s*([A-Z0-9]{' + str(length) + r'})(?![A-Z0-9])', text, re.I))
    codes.update(re.findall(r'Copy and paste this code into the security code field on your application:\s*([A-Z0-9]{'+str(length)+r'})(?![A-Z0-9])',text,re.I))
    return next(iter(codes)) if len(codes) == 1 else None
