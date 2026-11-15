"""Fresh Workday activation links; mail content and tokens never enter models."""
from __future__ import annotations

import base64
from email.utils import getaddresses,parseaddr
from html import unescape
from html.parser import HTMLParser
import re
import time
from urllib.parse import parse_qsl,urlsplit

from .util import Blocked
from .workday_site import site_for_url

SENDERS={'otp.workday.com'}
MAX_AGE=24*3600


class Links(HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            url=dict(attrs).get('href')
            if isinstance(url,str):self.urls.append(url)


def _parts(part,depth=0):
    if depth>8:return
    encoded=part.get('body',{}).get('data')
    if isinstance(encoded,str) and len(encoded)<=2000000:
        try:yield base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)).decode('utf-8',errors='replace')
        except (ValueError,UnicodeError):pass
    for child in part.get('parts',[])[:50]:yield from _parts(child,depth+1)


def activation_url(url,site):
    """Observed /SITE/activate/TOKEN only, never reset-password or another tenant."""
    try:
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname!=site.host or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.fragment or len(url)>4096):return False
        if not re.fullmatch(r'/(?:[a-z]{2}-[A-Z]{2}/)?'+re.escape(site.site)+r'/activate/[A-Za-z0-9_-]{32,512}/?',parsed.path):return False
        pairs=parse_qsl(parsed.query,keep_blank_values=True)
        if len(pairs)>1 or any(key!='redirect' for key,_ in pairs):return False
        for _,value in pairs:
            target=urlsplit(value)
            if (target.fragment or target.username or target.password or target.port not in (None,443)
                    or target.scheme not in ('','https') or target.netloc and target.hostname!=site.host):return False
            if not re.match(r'^/(?:[a-z]{2}-[A-Z]{2}/)?'+re.escape(site.site)+r'(?:/|$)',target.path):return False
        return True
    except (ValueError,TypeError):return False


def activation_link(message,recipient,job,since,*,clock=None):
    current=time.time() if clock is None else clock
    try:received=int(message['internalDate'])/1000;site=site_for_url(job['url'])
    except (ValueError,KeyError,TypeError):return None
    if not since-5<=received<=current+60 or not 0<=current-since<=MAX_AGE:return None
    headers={}
    for header in message.get('payload',{}).get('headers',[]):
        headers.setdefault(header.get('name','').casefold(),header.get('value',''))
    domain=parseaddr(headers.get('from',''))[1].rsplit('@',1)[-1].casefold()
    if domain not in SENDERS:return None
    recipients={email.casefold() for _,email in getaddresses([headers.get('to','')])}
    if recipient.casefold() not in recipients:return None
    auth=headers.get('authentication-results','')
    if (not auth.lstrip().startswith('mx.google.com;') or not re.search(
            r'dkim=pass\b[^;]*(?:header\.d=|header\.i=@)'+re.escape(domain)+r'(?:\s|;|$)',auth,re.I)):return None
    candidates=set()
    for text in _parts(message.get('payload',{})):
        parser=Links()
        try:parser.feed(text)
        except Exception:continue
        urls=parser.urls+re.findall(r'https://[^\s<>"\']+',unescape(text))
        for url in urls:
            url=unescape(url)
            if activation_url(url,site):candidates.add(url)
    return next(iter(candidates)) if len(candidates)==1 else None


def find_activation(store,job,since,challenge_id,*,client=None):
    if not store.settings()['gmail_verification']:raise Blocked('account_verification_pending','Enable email verification to activate the new Workday account')
    from .gmail import GmailClient
    client=client or GmailClient(store)
    if client.email!=store.facts()['email']['value'].casefold():raise Blocked('gmail_account_mismatch')
    messages=client.service.users().messages()
    query=f'after:{int(since)-5} from:(otp.workday.com)'
    rows=messages.list(userId='me',q=query,maxResults=20,includeSpamTrash=True).execute().get('messages',[])
    for row in rows:
        store.checkpoint()
        if store.db.execute('SELECT 1 FROM mail_consumptions WHERE message_id=?',(row['id'],)).fetchone():continue
        message=messages.get(userId='me',id=row['id'],format='full').execute()
        link=activation_link(message,client.email,job,since)
        if link:
            with store.transaction():
                reserved=store.db.execute('INSERT OR IGNORE INTO mail_consumptions VALUES(?,?,?)',
                    (row['id'],challenge_id,time.time())).rowcount
            if reserved:return link
    return None
