"""Portable application truth, independent of browser implementation.

Evidence records are append-only ledger events. Reconciliation never supplies
retry authority, and missing mail or a missing request log never proves failure.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .util import application_identity, digest, now

VERSION = 1
KINDS = ('submit_intent','verification_intent','native_submission_write',
         'native_submission_response','ashby_submission_write','ashby_submission_response',
         'native_submission_not_dispatched','submission_response','submission_error',
         'unknown','confirmed','not_submitted','crash_recovered','human_reconciliation',
         'post_submit_write_blocked','human_verification','submission_receipt_verified')


def ats_for(url):
    host=urlsplit(url).hostname or ''
    if host in {'jobs.lever.co','jobs.eu.lever.co'}:return 'lever'
    if host=='jobs.ashbyhq.com':return 'ashby'
    if host=='greenhouse.io' or host.endswith('.greenhouse.io'):return 'greenhouse'
    if host.endswith('.myworkdayjobs.com'):return 'workday'
    if host.endswith('.icims.com'):return 'icims'
    if host=='careers.amd.com':
        from .icims import identity
        if identity(url):return 'icims'
    return 'other'


def event_history(store, aid):
    # Use the existing kind/subject index, not a scan of the entire event log.
    rows=store.db.execute('SELECT seq,timestamp,kind,detail FROM events WHERE subject=? AND kind IN ('+
        ','.join('?' for _ in KINDS)+') ORDER BY seq',(aid,*KINDS))
    return [{**dict(row),'detail':json.loads(row['detail'])} for row in rows]


def native_verdict(events, url):
    """Only explicit, ordered transport evidence can repair an uncertain result."""
    intents=[e for e in events if e['kind']=='submit_intent']
    if len(intents)!=1:return None
    after=[e for e in events if e['seq']>intents[0]['seq']]
    writes=[e for e in after if e['kind'] in {'native_submission_write','ashby_submission_write'}]
    replies=[e for e in after if e['kind'] in {'native_submission_response','ashby_submission_response'}]
    closed=[e for e in after if e['kind']=='native_submission_not_dispatched']
    if closed and not writes and not replies and ats_for(url)=='lever':
        if all(e['detail'].get('platform')=='lever' for e in closed):
            return ('not_submitted','closed_native_grant',closed[-1]['timestamp'])
    if len(writes)!=1 or len(replies)!=1 or closed or replies[0]['seq']<=writes[0]['seq']:return None
    proof=replies[0]['detail'];platform=ats_for(url)
    # Lever's acknowledged reply is itself the fresh, native HTML receipt.
    # Ashby's FormSubmitSuccess is an acknowledgement only: preserve a
    # separate observed receipt before it can settle an uncertain outcome.
    receipt_verified=platform=='lever' or platform=='ashby' and any(
        e['kind']=='submission_receipt_verified' and e['seq']>replies[0]['seq']
        and e['detail'].get('platform')==platform and e['detail'].get('fresh') is True
        and e['detail'].get('posting_identity')==list(application_identity(url))
        and e['detail'].get('request_hash')==proof.get('request_hash')
        and isinstance(e['detail'].get('receipt_sha256'),str)
        and len(e['detail']['receipt_sha256'])==64 for e in after)
    if (proof.get('acknowledged') is True and proof.get('evidence_version')==VERSION
            and receipt_verified
            and proof.get('platform')==platform and proof.get('posting_identity')==list(application_identity(url))
            and isinstance(proof.get('http_status'),int) and 200<=proof['http_status']<300
            and isinstance(proof.get('response_sha256'),str) and len(proof['response_sha256'])==64
            and isinstance(proof.get('request_hash'),str) and len(proof['request_hash'])==64
            and writes[0]['detail'].get('request_hash')==proof['request_hash']):
        return ('confirmed','native_ats_acknowledgement',replies[0]['timestamp'])
    return None


def snapshot(store, aid, *, include_mail=False):
    row=store.db.execute('''SELECT a.id,a.job_id,a.state,a.hash,a.attempted,a.updated,a.confirmation,a.screenshot,
        j.url,j.company,j.title FROM application_records a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.id=?''',(aid,)).fetchone()
    if not row:raise ValueError('Unknown application attempt')
    row=dict(row);events=event_history(store,aid);url=row['url'] or ''
    proof=native_verdict(events,url)
    fields={'platform','acknowledged','evidence_version','posting_identity','http_status',
            'response_sha256','request_hash','receipt_sha256','fresh','rejection_code','reason','stage','type','host','method','status','phase'}
    # Never copy request bodies, CAPTCHA tokens, query strings or arbitrary paths.
    evidence={'events':[{'seq':e['seq'],'kind':e['kind'],'timestamp':e['timestamp'],
                         'detail':{k:v for k,v in e['detail'].items() if k in fields},
                         'record_hash':digest(e)} for e in events],
              'package_hash':row['hash'],'confirmation_text':row['confirmation'],
              'screenshot':None,'mail':[]}
    name=row['screenshot']
    if name and Path(name).name==name:
        path=store.root/'screenshots'/name
        present=path.is_file() and not path.is_symlink()
        try:checksum=hashlib.sha256(path.read_bytes()).hexdigest() if present else None
        except OSError:present=False;checksum=None
        evidence['screenshot']={'filename':name,'present':present,
            'sha256':checksum}
    if include_mail and url:
        from .prior_applications import matches
        for mail in matches(store,{'id':row['job_id'],'url':url,'company':row['company'],'title':row['title']}):
            try:elapsed=(datetime.fromisoformat(mail['received'])-datetime.fromisoformat(row['attempted'])).total_seconds()
            except (TypeError,ValueError):continue
            if -300<=elapsed<=7*86400:
                evidence['mail'].append({k:mail[k] for k in ('id','message_id','received','subject','match_reason','sender_verified','state')})
    status='unresolved' if row['state'] in {'unknown','awaiting_verification'} else row['state']
    method=proof[1] if proof and proof[0]==status else 'human_review' if any(e['kind']=='human_reconciliation' for e in events) else 'adapter_confirmation' if status=='confirmed' else 'adapter_rejection' if status=='not_submitted' else 'insufficient_evidence'
    return {'version':VERSION,'attempt_id':aid,'posting_id':list(application_identity(url)) if url else None,
            'job_id':row['job_id'],'job_url':url,'ats':ats_for(url),'status':status,
            'attempted_at':row['attempted'],'evidence':evidence,'verification_method':method,
            'verified_at':(proof[2] if proof and proof[0]==status else row['updated']) if status in {'confirmed','not_submitted'} else None}


def record(store, aid):
    bundle=snapshot(store,aid)
    store.event('application_evidence',aid,bundle)
    return bundle


def reconcile_unknowns(store, *, ats=None, scan_mail=False):
    """Audit every selected unknown; retain insufficient evidence explicitly."""
    from .prior_applications import scan,require_recent_check
    from .util import Blocked
    rows=list(store.db.execute("""SELECT a.id,j.payload FROM application_records a
        LEFT JOIN jobs j ON j.id=a.job_id WHERE a.state='unknown' ORDER BY a.attempted,a.id"""))
    selected=[(r['id'],json.loads(r['payload']) if r['payload'] else None) for r in rows]
    selected=[(aid,j) for aid,j in selected if not ats or j and ats_for(j['url']) in ats]
    mail_checks={};results=[];mail_service_failure=None
    for aid,job in selected:
        store.checkpoint()
        company=job['company'] if job else ''
        if company not in mail_checks:
            mail_checks[company]={'state':'not_scanned'}
            if scan_mail and job and mail_service_failure:
                mail_checks[company]={'state':'deferred','reason':mail_service_failure}
            elif scan_mail and job:
                try:
                    # Bounded resumable history scan, including its catch-up page.
                    for _ in range(8):
                        scan(store,company=company)
                        try:require_recent_check(store,job)
                        except Blocked:continue
                        break
                    require_recent_check(store,job)
                    mail_checks[company]={'state':'checked','checked_at':now()}
                except Exception as error:
                    if isinstance(error,Blocked) and error.reason in {'paused','cycle_timeout'}:raise
                    mail_checks[company]={'state':'unavailable','reason':error.reason if isinstance(error,Blocked) else type(error).__name__}
                    from .prior_applications import MAIL_SERVICE_FAILURES
                    if isinstance(error,Blocked) and error.reason in MAIL_SERVICE_FAILURES:mail_service_failure=error.reason
        with store.transaction():
            current=store.application_record(aid)
            if not current or current['state']!='unknown':continue
            bundle=snapshot(store,aid,include_mail=True)
            verdict=native_verdict(event_history(store,aid),bundle['job_url'])
            if verdict:
                status,method,verified=verdict
                historical=bool(store.db.execute('SELECT 1 FROM archived_applications WHERE id=?',(aid,)).fetchone())
                table='archived_applications' if historical else 'applications'
                note='Reconciled from retained native transport evidence: '+method
                store.db.execute('UPDATE '+table+' SET state=?,confirmation=?,updated=? WHERE id=?',(status,note,now(),aid))
                if not historical:store.db.execute('UPDATE jobs SET status=?,reason=?,updated=? WHERE id=?',(status,note,now(),current['job_id']))
                bundle.update(status=status,verification_method=method,verified_at=verified)
            bundle['evidence']['mail_check']=mail_checks[company]
            bundle['assessed_at']=now()
            bundle['retry_allowed']=False
            bundle['reason']=('Native transport proof' if verdict else
                'Receipt candidate requires independent verification' if bundle['evidence']['mail'] else
                'No reliable terminal proof; missing receipts and request logs do not establish failure')
            store.event('application_reconciliation',aid,bundle)
            store.event('application_evidence',aid,bundle)
            results.append(bundle)
    return {'version':VERSION,'assessed_at':now(),'counts':{state:sum(r['status']==state for r in results)
            for state in ('confirmed','not_submitted','unresolved')},'attempts':results}
