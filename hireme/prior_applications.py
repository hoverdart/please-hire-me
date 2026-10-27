"""Mailbox evidence of earlier applications, including those outside this worker.

This evidence can only hold work. It never creates a confirmed submission,
resolves an uncertain attempt, follows an email link or enters model context.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit

from .gmail import GmailClient, _body, owner_email
from .recruiting_feedback import AUTHENTICATION_MAIL, _headers, classify
from .util import Blocked, application_identity, company_key, company_normalizer, digest, now

MAX_MESSAGES = 25
FRESH_SECONDS = 15 * 60
QUERY = '-from:me {subject:application subject:applying subject:candidacy subject:assessment subject:interview subject:offer}'
PRIOR = re.compile(r'\b(?:your application|you(?: have)? applied|thank you for applying|thanks for applying|received your application|reviewing your application)\b', re.I)
ROLE = re.compile(r'\b(?:application\s+(?:for|to)|appl(?:ied|ying)\s+(?:for|to)|(?:position|role)\s+(?:of|as))\b', re.I)


def _normalized(value):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold()))


def _owner(store):
    return digest(owner_email(store))


def _posting_key(url):
    return digest(application_identity(url))


def _role_key(store, job):
    return digest([company_key(job['company']), _normalized(job['title'])])


def _role_matches(title,text):
    if len(title)<12 or len(title.split())<2:return False
    for match in re.finditer(r'(?<!\w)'+re.escape(title)+r'(?!\w)',text):
        before=text[:match.start()].rstrip();after=text[match.end():].lstrip()
        # A complete role phrase is required. "Software Engineer" cannot
        # consume "Senior Software Engineer" or "Software Engineer Intern".
        prefix=(not before or re.search(r'\b(?:(?:application|appl(?:ied|ying))\s+(?:for|to)(?:\s+the)?|(?:position|role)\s+(?:of|as)|your application)$',before))
        suffix=not after or bool(re.match(r'(?:position|role|at|application)\b',after))
        if prefix and suffix:return True
    return False


def _company_keys(store, company):
    aliases=store.settings()['company_aliases'];normalize=company_normalizer(aliases)
    canonical = normalize(company)
    keys = {canonical, company_key(company)}
    for key, value in aliases.items():
        if normalize(key) == canonical: keys.update((company_key(key), company_key(value)))
    for row in store.db.execute('SELECT company_key,email_company_key FROM prior_application_company_names'):
        if keys & set(row):keys.update(row)
    return {key for key in keys if key}


def public_company_name(job,checkpoint=None,deadline=None):
    from .gmail import greenhouse_employer_name
    return greenhouse_employer_name(job,checkpoint=checkpoint,deadline=deadline)


def resolve_company(store,job):
    """Bind a feed's opaque board slug using official public employer metadata."""
    if 'greenhouse.io' not in job.get('host',''):return
    ck=company_key(job['company'])
    if store.company(job['company'])!=ck:return  # Explicit applicant-supplied alias.
    if store.db.execute('SELECT 1 FROM prior_application_company_names WHERE company_key=?',(ck,)).fetchone():return
    try:name=public_company_name(job,checkpoint=store.checkpoint,deadline=getattr(store,'run_deadline',None))
    except Exception:
        store.checkpoint()
        raise Blocked('prior_application_company_review','The official employer name could not be verified for the email search. Retry or add a confirmed company alias.') from None
    if not isinstance(name,str) or not 1<=len(name.strip())<=200 or not company_key(name):
        raise Blocked('prior_application_company_review','Confirm the employer name through a company alias before its email history can be checked.')
    source='https://boards-api.greenhouse.io/v1/boards/'+urlsplit(job['url']).path.strip('/').split('/')[0]
    store.db.execute('INSERT OR IGNORE INTO prior_application_company_names VALUES(?,?,?,?,?)',(ck,company_key(name),name,source,now()))


def _resolve_company_search(store,company):
    if not company or not company_key(company):return
    # Prefixes only choose public boards to inspect. They never establish a
    # company match; the returned official employer name supplies that binding.
    rows=store.db.execute("""SELECT payload FROM jobs WHERE company_key LIKE ?
        AND host IN ('boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io')
        GROUP BY company_key LIMIT 6""",(company_key(company)+'%',))
    for row in rows:
        try:resolve_company(store,json.loads(row['payload']))
        except Blocked as error:
            if error.reason!='prior_application_company_review':raise


class _Links(HTMLParser):
    def __init__(self): super().__init__(); self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a' and len(self.urls) < 100:
            self.urls.extend(value for name, value in attrs if name == 'href' and isinstance(value, str))


def _urls(part, depth=0):
    if depth > 12 or not isinstance(part, dict): return []
    links = _Links()
    # _body strips markup; decode just this HTML part to retain inert hrefs.
    if part.get('mimeType') == 'text/html':
        import base64
        encoded = part.get('body', {}).get('data', '')
        if isinstance(encoded, str) and len(encoded) <= 200000:
            try: links.feed(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8', errors='replace'))
            except (ValueError, TypeError): pass
    result = links.urls
    for child in part.get('parts', [])[:100]: result.extend(_urls(child, depth+1))
    return result[:100]


def ingest(store, message, *, company=None):
    """Retain a bounded receipt passage, never an entire message or attachment."""
    owner = owner_email(store); headers = _headers(message)
    sender = parseaddr(headers.get('from', ''))[1].casefold()
    recipients = {address.casefold() for _, address in getaddresses([headers[k] for k in ('to', 'cc', 'delivered-to') if headers.get(k)])}
    if sender == owner or owner not in recipients: return False
    identity = message.get('id')
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity): return False
    try: received = int(message.get('internalDate', '0')) / 1000
    except (ValueError, TypeError, OverflowError): return False
    if not 0 < received <= time.time()+300: return False
    subject = ' '.join(headers.get('subject', '').split())[:300]
    body = _body(message.get('payload', {}))[:100000]
    if AUTHENTICATION_MAIL.search(subject+'\n'+body) or re.match(r'\s*(?:re|fw|fwd):', subject, re.I): return False
    body = re.split(r'\n(?:On .{0,300}wrote:|[- ]*Original Message[- ]*)', body, maxsplit=1, flags=re.I)[0]
    body = '\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('>'))
    category = classify(subject, body)
    if not PRIOR.search(subject+'\n'+body) or category == 'unclassified': return False
    passages = [line.strip()[:2000] for line in body.splitlines() if ROLE.search(line)][:6]
    text = _normalized(subject+'\n'+body)
    aliases=store.settings()['company_aliases'];normalize=company_normalizer(aliases)
    candidates = [company] if company else [row[0] for row in store.db.execute('SELECT DISTINCT company FROM jobs')]
    names=set();bindings=list(store.db.execute('SELECT company_key,email_company_key,name FROM prior_application_company_names'))
    for name in candidates:
        labels={name}
        for key,value in aliases.items():
            if normalize(key)==normalize(name):labels.update((key,value))
        labels.update(row['name'] for row in bindings if row['company_key'] in {company_key(name),normalize(name)})
        names.update(company_key(label) for label in labels if len(company_key(label))>=3
                     and ' '+_normalized(label)+' ' in ' '+text+' ')
    # Keep a name actually present in the email, rather than a mutable alias
    # target. Later alias edits can supplement this original identity.
    ck = max(names,key=lambda key:(len(key),key)) if len({normalize(name) for name in names})==1 else ''
    urls = re.findall(r'https://[^\s<>"\']+', body) + _urls(message.get('payload', {}))
    keys = sorted({_posting_key(url.rstrip('.,;:!?)')) for url in urls[:100] if url.startswith('https://')})
    domain = sender.rsplit('@', 1)[-1]
    authentication = headers.get('authentication-results', '')
    verified = bool('@' in sender and authentication.lstrip().startswith('mx.google.com;') and re.search(
        r'dkim=pass\b[^;]*(?:header\.d=|header\.i=@)(?:[a-z0-9-]+\.)*'+re.escape(domain)+r'(?:\s|;|$)', authentication, re.I))
    excerpt = (passages[0] if passages else next((line.strip() for line in body.splitlines() if PRIOR.search(line)), subject))
    excerpt = re.sub(r'https://\S+', '[link]', excerpt)[:600]
    # URLs and account-like strings are unnecessary in the searchable passage.
    role_text = '\n'.join(_normalized(re.sub(r'https://\S+|[\w.+-]+@[\w.-]+', ' ',part)) for part in [subject,*passages])[:12000]
    with store.transaction():
        if owner_email(store) != owner: return False
        if ck:
            store.db.execute("UPDATE prior_application_mail SET company_key=?,revision=revision+1,updated=? WHERE owner_hash=? AND message_id=? AND company_key=''",
                             (ck,now(),digest(owner),identity))
        return bool(store.db.execute('''INSERT OR IGNORE INTO prior_application_mail
            (id,owner_hash,message_id,received,sender,subject,excerpt,role_text,posting_keys,company_key,
             category,sender_verified,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (digest([digest(owner),identity]),digest(owner),identity,
             datetime.fromtimestamp(received,timezone.utc).isoformat(timespec='seconds'),sender[:320],subject,
             excerpt,role_text,json.dumps(keys),ck,category,int(verified),now(),now())).rowcount)


def matches(store, job):
    try: owner = _owner(store)
    except Blocked: return []
    keys = _company_keys(store, job['company']); posting = _posting_key(job['url']); title = _normalized(job['title'])
    marks = ','.join('?' for _ in keys) or 'NULL'
    rows = store.db.execute(f'''SELECT * FROM prior_application_mail WHERE owner_hash=? AND state!='dismissed'
        AND (company_key IN ({marks}) OR instr(posting_keys,?)>0 OR job_id=? OR selected_posting_key=?)''',
        (owner,*sorted(keys),posting,job['id'],posting))
    result=[]
    for row in rows:
        if row['state']=='confirmed':
            reason = 'Reviewed email link' if (row['job_id']==job['id'] or row['selected_posting_key']==posting
                                               or row['selected_role_key'] in {digest([key,title]) for key in keys}) else None
        elif posting in json.loads(row['posting_keys']): reason = 'Exact posting link in application email'
        elif any(_role_matches(title,line) for line in row['role_text'].splitlines()): reason = 'Company and exact role in application email'
        else: reason = None
        if reason: result.append({**dict(row),'match_reason':reason})
    return result


def check_saved(store, job):
    if not store.db.execute('SELECT 1 FROM prior_application_mail LIMIT 1').fetchone():return
    if matches(store,job):
        raise Blocked('prior_application_email', 'Email indicates an earlier application for this role. Review Past applications in Connections before applying again.')


def _scope(store, company):
    return store.company(company) if company else '*'


def _query(store, company):
    if not company: return QUERY
    labels = {company}
    for key,value in store.settings()['company_aliases'].items():
        if store.company(key)==store.company(company): labels.update((key,value))
    keys=_company_keys(store,company)
    labels.update(row['name'] for row in store.db.execute('SELECT company_key,email_company_key,name FROM prior_application_company_names')
                  if row['company_key'] in keys or row['email_company_key'] in keys)
    terms = ['"'+' '.join(re.findall(r'[^\W_]+',label))+'"' for label in sorted(labels)[:10]]
    return QUERY+' {'+' '.join(terms)+'}'


def scan(store, *, company=None, restart=False, client_factory=None):
    """One resumable page. The first scan has no date cutoff, including archived mail."""
    from .store import worker_lock
    if company is not None and (not isinstance(company,str) or not 1<=len(company.strip())<=200 or not company_key(company)): raise ValueError('Choose a company name with letters or digits, up to 200 characters')
    if type(restart) is not bool: raise ValueError('Choose whether to restart the scan')
    if not store.settings()['gmail_prior_applications']: raise Blocked('gmail_prior_applications_disabled','Enable checking Gmail for past applications in Email automation first.')
    with worker_lock(store.root,'prior-mail'):
        owner = _owner(store); scope = _scope(store,company)
        try:
            store.checkpoint()
            _resolve_company_search(store,company)
            query = _query(store,company)
            state=store.db.execute('SELECT * FROM prior_application_scans WHERE owner_hash=? AND company_key=?',(owner,scope)).fetchone()
            if state and (restart or not state['query'].endswith(query)): state=None
            pending=json.loads(state['pending']) if state and state['completed'] is None else []
            next_page=state['next_page'] if state and state['completed'] is None else ''
            started=state['started'] if state and state['completed'] is None else time.time()
            if state and state['completed'] is not None: query=f'after:{int(state["started"])-86400} '+query
            elif state: query=state['query']
            def valid_owner():
                store.checkpoint()
                if _owner(store)!=owner or not store.settings()['gmail_prior_applications']: raise Blocked('prior_application_scan_changed','Email identity or preferences changed; scan stopped.')
            def save(completed=None):
                with store.transaction():
                    valid_owner()
                    store.db.execute('INSERT OR REPLACE INTO prior_application_scans VALUES(?,?,?,?,?,?,?,?)',
                        (owner,scope,query,json.dumps(pending),next_page,started,completed,now()))
            # Invalidate any previous freshness proof before a new remote read.
            save()
            client=(client_factory or GmailClient)(store)
            valid_owner()
            if digest(client.email.casefold())!=owner: raise Blocked('gmail_account_mismatch')
            messages=client.service.users().messages()
            if not pending:
                options={'userId':'me','q':query,'maxResults':MAX_MESSAGES}
                if next_page: options['pageToken']=next_page
                page=messages.list(**options).execute();valid_owner()
                pending=[item['id'] for item in page.get('messages',[])[:MAX_MESSAGES]
                         if isinstance(item,dict) and isinstance(item.get('id'),str)]
                next_page=page.get('nextPageToken','')
                if not isinstance(next_page,str) or len(next_page)>4096: raise ValueError('Invalid cursor')
                save()
            processed=added=0;deadline=time.monotonic()+30
            for identity in list(pending):
                if time.monotonic()>=deadline: break
                valid_owner()
                existing=store.db.execute('SELECT company_key FROM prior_application_mail WHERE owner_hash=? AND message_id=?',(owner,identity)).fetchone()
                if not existing or company and not existing['company_key']:
                    from googleapiclient.errors import HttpError
                    try: message=messages.get(userId='me',id=identity,format='full').execute()
                    except HttpError as error:
                        if error.resp.status!=404: raise
                        pending.remove(identity);processed+=1;save();continue
                    valid_owner()
                    if message.get('id')!=identity: raise ValueError('Message identity mismatch')
                    added+=int(ingest(store,message,company=company))
                pending.remove(identity);processed+=1;save()
            more=bool(pending or next_page)
            if not more: save(time.time())
            return {'processed':processed,'added':added,'more_available':more,'scope':company or 'All companies'}
        except Blocked: raise
        except Exception:
            raise Blocked('prior_application_mail_unavailable','Gmail history could not be checked. Reconnect or retry the scan; existing email holds are preserved.') from None


def require_recent_check(store,job):
    if not store.settings()['gmail_prior_applications']: return
    from .gmail import status
    if not status(store)['connected']:
        raise Blocked('prior_application_check_required','Reconnect the confirmed Gmail account before checking past applications.')
    try: owner=_owner(store)
    except Blocked: raise Blocked('prior_application_check_required','Connect the confirmed Gmail account and check past applications before applying.') from None
    row=store.db.execute('SELECT completed,query FROM prior_application_scans WHERE owner_hash=? AND company_key=?',
                         (owner,_scope(store,job['company']))).fetchone()
    if (not row or row['completed'] is None or not 0<=time.time()-row['completed']<=FRESH_SECONDS
            or not row['query'].endswith(_query(store,job['company']))):
        raise Blocked('prior_application_check_required','A current company email-history check is required before this application.')


def preflight(store,job):
    check_saved(store,job)
    if not store.settings()['gmail_prior_applications']: return
    checked=getattr(store,'prior_mail_checked',None);scope=_scope(store,job['company'])
    if checked is not None and scope in checked:
        require_recent_check(store,job);return
    if checked is not None and len(checked)>=getattr(store,'prior_mail_check_limit',store.settings()['max_attempts_per_cycle']):
        raise Blocked('prior_application_check_budget','This batch reached its company email-check limit. Saved scan progress will continue next cycle.')
    if checked is not None:checked.add(scope)
    resolve_company(store,job)
    check_saved(store,job)
    try: require_recent_check(store,job);return
    except Blocked: pass
    result=scan(store,company=job['company'])
    check_saved(store,job)
    if result['more_available']: raise Blocked('prior_application_scan_incomplete','More application mail remains to scan for this company. Continue the scan before applying.')
    require_recent_check(store,job)


def listing(store, *, offset=0):
    if type(offset) is not int or not 0<=offset<=1000000: raise ValueError('Choose a valid mail page')
    owner=_owner(store)
    store.db.create_function('prior_role_matches',2,lambda title,text:int(any(_role_matches(_normalized(title),line) for line in text.splitlines())),deterministic=True)
    total=store.db.execute('SELECT COUNT(*) FROM prior_application_mail WHERE owner_hash=?',(owner,)).fetchone()[0]
    records=[]
    for row in store.db.execute('SELECT * FROM prior_application_mail WHERE owner_hash=? ORDER BY received DESC,id LIMIT 25 OFFSET ?',(owner,offset)):
        candidates=[]
        if row['company_key']:
            keys=_company_keys(store,row['company_key']);marks=','.join('?' for _ in keys)
            candidates=[dict(job) for job in store.db.execute(f'''SELECT id,company,title,url FROM jobs WHERE company_key IN ({marks})
                ORDER BY (id=?) DESC,prior_role_matches(title,?) DESC,updated DESC LIMIT 50''',
                (*sorted(keys),row['job_id'] or '', row['role_text']))]
        item={key:row[key] for key in ('id','message_id','received','sender','subject','excerpt','category','sender_verified','state','job_id','revision')}
        item['candidates']=candidates;records.append(item)
    return {'messages':records,'total':total,'offset':offset,'limit':25}


def review(store,data):
    if (not isinstance(data,dict) or set(data)-{'id','revision','state','job_id'} or type(data.get('revision')) is not int
            or data.get('state') not in {'confirmed','dismissed'}): raise ValueError('Choose a message and review result')
    from .store import worker_lock
    with worker_lock(store.root),store.transaction():
        row=store.db.execute('SELECT * FROM prior_application_mail WHERE id=? AND owner_hash=?',(data.get('id'),_owner(store))).fetchone()
        if not row or row['revision']!=data['revision']: raise ValueError('This email record changed. Refresh before reviewing.')
        job=None
        if data['state']=='confirmed':
            job=store.db.execute('SELECT * FROM jobs WHERE id=?',(data.get('job_id'),)).fetchone()
            if not job or not row['company_key'] or row['company_key'] not in _company_keys(store,job['company']): raise ValueError('Choose a saved role at the matching company')
        store.db.execute('UPDATE prior_application_mail SET state=?,job_id=?,selected_posting_key=?,selected_role_key=?,revision=revision+1,updated=? WHERE id=?',
            (data['state'],job['id'] if job else None,_posting_key(job['url']) if job else None,_role_key(store,job) if job else None,now(),row['id']))
        store.event('prior_application_mail_reviewed',row['id'],{'state':data['state'],'job_id':job['id'] if job else None})
    return {'saved':True}
