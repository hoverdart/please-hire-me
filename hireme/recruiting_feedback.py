"""Owner-bound Gmail feedback suggestions; review never changes submission authority."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

from .gmail import GmailClient, _body, owner_email
from .util import Blocked, application_identity, digest, now

CATEGORIES = ('acknowledgement', 'assessment', 'interview', 'rejection', 'offer', 'unclassified')
QUERY_TERMS = '{subject:application subject:interview subject:assessment subject:offer subject:candidacy subject:opportunity}'
MAX_MESSAGES = 10
AUTHENTICATION_MAIL = re.compile(
    r'\b(?:verification|security|confirmation|one[- ]time|login|sign[- ]in|authentication)\s+(?:code|passcode)\b'
    r'|\b(?:verify|confirm)\s+(?:your\s+)?(?:email(?: address)?|account|identity)\b',re.I)


def classify(subject, body):
    text = subject + '\n' + body
    patterns = (
        ('rejection', r'not (?:be )?(?:moving|proceeding) (?:forward|with|further)|not (?:been )?selected|unable to (?:offer|move forward)|pursu(?:e|ing) other candidates|regret to inform|decided not to'),
        ('offer', r'(?:pleased|delighted|excited) to (?:offer|extend)|offer of employment|employment offer|your offer letter'),
        ('interview', r'invite you to (?:an? |the )?interview|schedule (?:an? |your |the )?interview|interview invitation|next interview|interview availability'),
        ('assessment', r'(?:complete|take|invited to|invitation to) (?:an? |the |our )?(?:coding |technical |online )?(?:assessment|challenge|test)|assessment invitation'),
        ('acknowledgement', r'(?:received|reviewing) your application|your application (?:has been|was) (?:received|submitted)|thank you for applying'),
    )
    def stated(category, pattern):
        for clause in re.split(r'[.!?;\n]+', text):
            # Conditional boilerplate is not a recruiting decision. Keep it as
            # an unreviewed suggestion rather than inventing a later stage.
            if re.search(r'\b(?:if|unless|whether)\b|\bshould you\b', clause, re.I):
                continue
            for match in re.finditer(pattern, clause, re.I):
                before, after = clause[:match.start()], clause[match.end():]
                if re.search(r'\b(?:may|might|could|would)\b', before, re.I):
                    continue
                if category != 'rejection' and (
                    re.search(r'\b(?:not|never|no)\b', before, re.I)
                    or re.match(r'\s+(?:is|was|will be)\s+not\b', after, re.I)
                ):
                    continue
                return True
        return False
    matches = [category for category, pattern in patterns if stated(category, pattern)]
    # A receipt quoted in a later invitation should not erase the newer stage.
    meaningful = [category for category in matches if category != 'acknowledgement']
    return meaningful[0] if len(meaningful) == 1 else 'unclassified' if meaningful else matches[0] if matches else 'unclassified'


def _owner(store):
    return digest(owner_email(store))


def _headers(message):
    result = {}
    for header in message.get('payload', {}).get('headers', []):
        name, value = header.get('name'), header.get('value')
        if isinstance(name, str) and isinstance(value, str): result.setdefault(name.casefold(), value)
    return result


def _candidates(store, text, received):
    urls = set()
    for url in re.findall(r'https://[^\s<>"\']+', text):
        try: urls.add(application_identity(url.rstrip('.,;:!?)')))
        except ValueError: pass
    exact, role, company = [], [], []
    for row in store.db.execute('''SELECT a.id,a.attempted,a.state,j.company,j.title,j.url FROM application_records a
        JOIN jobs j ON j.id=a.job_id WHERE a.attempted IS NOT NULL AND a.attempted<=? ORDER BY a.attempted DESC''', (received,)):
        item = {key: row[key] for key in ('id', 'company', 'title', 'attempted', 'state')}
        if application_identity(row['url']) in urls:
            exact.append(item); continue
        name = row['company'].strip()
        if len(name) < 3 or not re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I): continue
        company.append(item)
        if row['title'] and row['title'].casefold() in text.casefold(): role.append(item)
    if exact: return exact[:50], 'Exact posting URL; review the message and attempt'
    if role: return role[:50], 'Company and role mentioned; review the attempt'
    return company[:50], 'Company mentioned; select the correct attempt'


def ingest(store, message):
    """Parse inert text only. Do not follow links, fetch attachments, or call models."""
    owner = owner_email(store)
    owner_hash = digest(owner)
    headers = _headers(message)
    sender = parseaddr(headers.get('from', ''))[1].casefold()[:320]
    if sender == owner: return False  # Self-sent batch reports and replies are not employer feedback.
    recipients = {address.casefold() for _, address in getaddresses([headers[key] for key in ('to', 'cc', 'delivered-to') if headers.get(key)])}
    if owner not in recipients: return False
    message_id = message.get('id')
    if not isinstance(message_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message_id): return False
    try: received_epoch = int(message.get('internalDate', '0')) / 1000
    except (ValueError, TypeError, OverflowError): return False
    if not 0 < received_epoch <= time.time() + 300: return False
    received = datetime.fromtimestamp(received_epoch, timezone.utc).isoformat(timespec='seconds')
    subject = ' '.join(headers.get('subject', '').split())[:300]
    body = _body(message.get('payload', {}))[:100000]
    if AUTHENTICATION_MAIL.search(subject+'\n'+body):return False
    candidates, reason = _candidates(store, subject + '\n' + body, received)
    if not candidates: return False
    domain = sender.rsplit('@', 1)[-1]
    authentication = headers.get('authentication-results', '')
    verified = bool(domain and authentication.lstrip().startswith('mx.google.com;') and re.search(
        r'dkim=pass\b[^;]*(?:header\.d=|header\.i=@)' + re.escape(domain) + r'(?:\s|;|$)', authentication, re.I))
    identity = digest([owner_hash, message_id])
    excerpt = ' '.join(body.split())[:600]
    with store.transaction():
        if owner_email(store) != owner: return False
        inserted = store.db.execute('''INSERT OR IGNORE INTO recruiting_feedback
            (id,owner_hash,message_id,received,sender,subject,excerpt,category,candidates,match_reason,sender_verified,created,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (identity, owner_hash, message_id, received, sender, subject,
            excerpt, classify(subject, body), json.dumps(candidates), reason, int(verified), now(), now())).rowcount
    return bool(inserted)


def sync(store, client_factory=None):
    from .store import worker_lock
    with worker_lock(store.root, 'feedback'):
        return _sync(store, client_factory)


def _sync(store, client_factory=None):
    if not store.settings()['gmail_feedback']:
        raise Blocked('gmail_feedback_disabled', 'Enable recruiting feedback in Email automation first.')
    owner_hash = _owner(store)
    try:
        client = (client_factory or GmailClient)(store)
        if client.email.casefold() != owner_email(store): raise Blocked('gmail_account_mismatch')
        messages = client.service.users().messages()
        state = store.db.execute('SELECT * FROM recruiting_sync WHERE owner_hash=?', (owner_hash,)).fetchone()
        watermark = state['watermark'] if state else time.time() - 30 * 86400
        pending = json.loads(state['pending']) if state else []
        next_page = state['next_page'] if state else ''
        query = state['query'] if state else ''
        if not pending:
            if not next_page: query = f'after:{int(max(time.time()-30*86400,watermark-86400))} -from:me ' + QUERY_TERMS
            options = {'userId':'me','q':query,'maxResults':MAX_MESSAGES}
            if next_page: options['pageToken'] = next_page
            page = messages.list(**options).execute()
            pending = [item['id'] for item in page.get('messages', [])[:MAX_MESSAGES]
                       if isinstance(item, dict) and isinstance(item.get('id'), str)]
            next_page = page.get('nextPageToken', '')
            if not isinstance(next_page, str) or len(next_page) > 4096: raise ValueError('Invalid page token')
        def save():
            store.db.execute('INSERT OR REPLACE INTO recruiting_sync VALUES(?,?,?,?,?,?)',
                (owner_hash, watermark, query, json.dumps(pending), next_page, now()))
        save()
        added = processed = 0
        deadline = time.monotonic() + 30
        for message_id in list(pending):
            if processed >= MAX_MESSAGES or time.monotonic() >= deadline: break
            if _owner(store) != owner_hash or not store.settings()['gmail_feedback']:
                raise Blocked('gmail_feedback_changed', 'Your email identity or feedback preference changed. Sync stopped.')
            existing = store.db.execute('SELECT 1 FROM recruiting_feedback WHERE owner_hash=? AND message_id=?', (owner_hash, message_id)).fetchone()
            if not existing:
                from googleapiclient.errors import HttpError
                try: message = messages.get(userId='me', id=message_id, format='full').execute()
                except HttpError as error:
                    if error.resp.status != 404: raise
                    # Deleted mail must not pin every later message behind its cursor.
                    pending.remove(message_id); processed += 1; save()
                    continue
                if message.get('id') != message_id: raise ValueError('Message identity mismatch')
                if _owner(store) != owner_hash or not store.settings()['gmail_feedback']:
                    raise Blocked('gmail_feedback_changed', 'Your email identity or feedback preference changed. Sync stopped.')
                try:
                    added += int(ingest(store, message))
                    watermark = max(watermark, min(int(message.get('internalDate','0'))/1000, time.time()))
                except (ValueError, TypeError, OverflowError): pass  # malformed mail grants no authority
            pending.remove(message_id); processed += 1; save()
        return {'processed':processed,'added':added,'more_available':bool(pending or next_page)}
    except Blocked: raise
    except Exception:
        raise Blocked('gmail_feedback_sync_failed', 'Recruiting mail could not be synced. Check Gmail access and try again; saved reviews are preserved.') from None


def listing(store, state='pending', offset=0):
    if state not in {'all','pending','confirmed','dismissed'} or type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Choose a feedback status and valid page')
    owner_hash = _owner(store)
    terms, args = 'owner_hash=?', [owner_hash]
    if state != 'all': terms += ' AND state=?'; args.append(state)
    total = store.db.execute('SELECT COUNT(*) FROM recruiting_feedback WHERE '+terms, args).fetchone()[0]
    rows = store.db.execute('SELECT * FROM recruiting_feedback WHERE '+terms+' ORDER BY received DESC,id LIMIT 25 OFFSET ?', [*args,offset])
    records = []
    for row in rows:
        item = {key:row[key] for key in ('id','message_id','received','sender','subject','excerpt','category','match_reason','sender_verified','state','application_id','revision')}
        item['candidates'] = json.loads(row['candidates'])
        records.append(item)
    counts = dict(store.db.execute('SELECT state,COUNT(*) FROM recruiting_feedback WHERE owner_hash=? GROUP BY state', (owner_hash,)))
    return {'messages':records,'total':total,'offset':offset,'limit':25,'counts':counts}


def summary(store):
    try: owner_hash=_owner(store)
    except Blocked:return {'pending':0,'stages':{}}
    pending=store.db.execute("SELECT COUNT(*) FROM recruiting_feedback WHERE owner_hash=? AND state='pending'",(owner_hash,)).fetchone()[0]
    stages=dict(store.db.execute('''SELECT category,COUNT(*) FROM (
        SELECT category,ROW_NUMBER() OVER (PARTITION BY application_id
            ORDER BY (category='acknowledgement'),received DESC,id DESC) AS position
        FROM recruiting_feedback WHERE owner_hash=? AND state='confirmed' AND application_id IS NOT NULL
    ) WHERE position=1 GROUP BY category''',(owner_hash,)))
    return {'pending':pending,'stages':stages}


def for_job(store,job_id):
    try: owner_hash=_owner(store)
    except Blocked:return []
    return [dict(row) for row in store.db.execute('''SELECT f.id,f.application_id,f.category,f.received,f.subject
        FROM recruiting_feedback f JOIN application_records a ON a.id=f.application_id
        WHERE f.owner_hash=? AND f.state='confirmed' AND a.job_id=? ORDER BY f.received DESC,f.id LIMIT 50''',
        (owner_hash,job_id))]


def review(store, data):
    if (not isinstance(data, dict) or set(data) - {'id','revision','state','category','application_id'}
            or type(data.get('revision')) is not int or data.get('state') not in {'confirmed','dismissed'}):
        raise ValueError('Choose a feedback message, revision and review result')
    with store.transaction():
        row = store.db.execute('SELECT * FROM recruiting_feedback WHERE id=? AND owner_hash=?', (data.get('id'),_owner(store))).fetchone()
        if not row or row['revision'] != data['revision']:
            raise ValueError('This feedback changed or is unavailable. Refresh before reviewing.')
        category = data.get('category', row['category'])
        application_id = data.get('application_id')
        if category not in CATEGORIES: raise ValueError('Choose a recruiting feedback category')
        if data['state'] == 'confirmed':
            if category == 'unclassified': raise ValueError('Choose the actual feedback category before confirming')
            candidates = json.loads(row['candidates'])
            if application_id not in {candidate['id'] for candidate in candidates}:
                raise ValueError('Choose one of the matching application attempts')
            application = store.db.execute('SELECT attempted FROM application_records WHERE id=?',(application_id,)).fetchone()
            if not application or not application['attempted']: raise ValueError('The application attempt is unavailable')
        else: application_id = None
        store.db.execute('UPDATE recruiting_feedback SET state=?,category=?,application_id=?,revision=revision+1,updated=? WHERE id=?',
            (data['state'],category,application_id,now(),row['id']))
        store.event('recruiting_feedback_reviewed', row['id'], {'state':data['state'],'category':category,'application_id':application_id})
    return {'saved':True}
