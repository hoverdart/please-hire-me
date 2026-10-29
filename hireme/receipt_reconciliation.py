"""Read-only receipt candidates. No inference here changes submission authority."""
from datetime import datetime, timezone
from email.utils import getaddresses
import re

from .gmail import owner_email, _body
from .prior_applications import _prior_evidence, _posting_key, _urls
from .recruiting_feedback import _headers, AUTHENTICATION_MAIL


def scan_candidates(store,*,client_factory=None,cursor=''):
    """One bounded read-only page. A cursor continues review, never authorizes apply."""
    from .gmail import GmailClient
    if not isinstance(cursor,str) or len(cursor)>4096:raise ValueError('Invalid receipt cursor')
    client=(client_factory or GmailClient)(store)
    if client.email.casefold()!=owner_email(store):raise ValueError('Receipt mailbox owner changed')
    messages=client.service.users().messages()
    options={'userId':'me','q':'in:anywhere','maxResults':25,'includeSpamTrash':True}
    if cursor:options['pageToken']=cursor
    page=messages.list(**options).execute()
    items=page.get('messages',[]);next_page=page.get('nextPageToken','')
    if (not isinstance(items,list) or len(items)>25 or not isinstance(next_page,str) or len(next_page)>4096
            or next_page and next_page==cursor):raise ValueError('Incomplete receipt page')
    found=[]
    for item in items:
        store.checkpoint()
        identity=item.get('id') if isinstance(item,dict) else None
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',identity):raise ValueError('Invalid receipt identity')
        message=messages.get(userId='me',id=identity,format='full').execute()
        if message.get('id')!=identity:raise ValueError('Receipt identity changed')
        found.extend(candidates(store,message))
    return {'candidates':found,'next_page':next_page,'more_available':bool(next_page),'auto_confirmation_enabled':False}


def candidates(store, message):
    """An exact URL and plausible time still require independent employer review.

    Sender claims and Authentication-Results alone cannot prove a receipt. No
    employer-specific rule has been independently commissioned for auto-confirm.
    """
    headers=_headers(message);owner=owner_email(store)
    owners={owner,*(a.casefold() for a in store.settings()['gmail_confirmed_aliases'])}
    recipients={address.casefold() for _,address in getaddresses([headers[k] for k in ('to','cc','delivered-to') if headers.get(k)])}
    if not recipients.intersection(owners):return []
    try:received=int(message['internalDate'])/1000
    except (KeyError,ValueError,TypeError,OverflowError):return []
    if not 0<received<=datetime.now(timezone.utc).timestamp()+300:return []
    subject=headers.get('subject','');body=_body(message.get('payload',{}))[:100000]
    if AUTHENTICATION_MAIL.search(subject+'\n'+body) or not _prior_evidence(subject+'\n'+body):return []
    urls=_urls(message.get('payload',{}))+re.findall(r'https://[^\s<>"\']+',body)
    keys={_posting_key(url.rstrip('.,;:!?)')) for url in urls[:100]}
    result=[]
    for row in store.db.execute('''SELECT a.id,a.attempted,a.state,j.url FROM application_records a
            JOIN jobs j ON j.id=a.job_id WHERE a.attempted IS NOT NULL'''):
        try:elapsed=received-datetime.fromisoformat(row['attempted']).timestamp()
        except (ValueError,TypeError):continue
        if not -300<=elapsed<=7*86400 or _posting_key(row['url']) not in keys:continue
        result.append({'application_id':row['id'],'message_id':message.get('id'),
            'state':row['state'],'exact_posting':True,'owner_recipient':True,
            'decision':'review','auto_confirmed':False,'reason':'Independently verify employer receipt and forwarding provenance'})
    return result
