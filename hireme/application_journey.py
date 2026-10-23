"""Read-only account of one application attempt and its reviewed recruiting feedback."""
from __future__ import annotations

import json
from contextlib import contextmanager

from .gmail import owner_email
from .util import Blocked, digest, now


def capture_context(store, job):
    """Journal posting identity without changing the approved answer package."""
    return {**{key: job.get(key, '') for key in ('id', 'url', 'company', 'title', 'host', 'source', 'location')},
            'origins': [dict(row) for row in store.db.execute('''SELECT connection_id,platform_job_id,listing_url
                FROM job_origins WHERE job_id=? ORDER BY connection_id,platform_job_id''', (job['id'],))]}


def _context(store, record):
    for event in store.db.execute('''SELECT kind,timestamp,detail FROM events
        WHERE subject=? AND kind IN ('submit_intent','prepared')
        ORDER BY (kind='submit_intent') DESC,seq DESC LIMIT 10''', (record['id'],)):
        try: detail = json.loads(event['detail'])
        except (ValueError, TypeError): continue
        context = detail.get('job_context') if isinstance(detail, dict) else None
        if (isinstance(context, dict) and context.get('id') == record['job_id']
                and detail.get('hash', detail.get('package_hash')) == record['hash']):
            return context, event['kind'], event['timestamp']
    # Older ledgers did not snapshot posting identity. Never call current metadata historical proof.
    row = store.db.execute('SELECT * FROM jobs WHERE id=?', (record['job_id'],)).fetchone()
    if not row: return {'id':record['job_id']}, 'unavailable', None
    return {key:row[key] for key in ('id','url','company','title','host','source')}, 'current_job_metadata', None


def _documents(store, record):
    try: package = json.loads(record['package'])
    except (ValueError, TypeError): return [], 'unavailable'
    if not isinstance(package, dict): return [], 'unavailable'
    if digest(package) != record['hash']: return [], 'mismatch'
    documents = package.get('documents', [])
    if not isinstance(documents, list): return [], 'unavailable'
    result = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict): continue
        item = {key:document.get(key) if isinstance(document.get(key),str) else None
                for key in ('kind','hash','filename','artifact_id')}
        item['generated'] = document.get('generated') is True
        item.update(index=index, revision=None)
        if item['artifact_id']:
            artifact = store.db.execute('''SELECT revision FROM application_artifacts
                WHERE id=? AND job_id=? AND kind=? AND hash=? AND filename=?''',
                (item['artifact_id'],record['job_id'],item['kind'],item['hash'],item['filename'])).fetchone()
            if artifact: item['revision'] = artifact['revision']
        result.append(item)
    return result, 'verified'


def _feedback(store, application_id):
    try: owner_hash = digest(owner_email(store))
    except Blocked: return [], [], False, None
    confirmed, pending = [], []
    rows = store.db.execute('''SELECT id,message_id,received,subject,category,updated,state,candidates,application_id
        FROM recruiting_feedback WHERE owner_hash=? AND
        ((state='confirmed' AND application_id=?) OR (state='pending' AND INSTR(candidates,?)>0))
        ORDER BY received DESC,id DESC LIMIT 101''', (owner_hash,application_id,json.dumps(application_id)))
    rows = list(rows)
    for row in rows[:100]:
        item = {key:row[key] for key in ('id','message_id','received','subject','category','updated')}
        if row['state'] == 'confirmed':
            confirmed.append(item)
        else:
            try: candidates = json.loads(row['candidates'])
            except (ValueError, TypeError): continue
            if isinstance(candidates, list) and any(isinstance(c,dict) and c.get('id')==application_id for c in candidates):
                pending.append(item)
    latest = store.db.execute('''SELECT category FROM recruiting_feedback
        WHERE owner_hash=? AND state='confirmed' AND application_id=?
        ORDER BY (category='acknowledgement'),received DESC,id DESC LIMIT 1''',(owner_hash,application_id)).fetchone()
    return confirmed, pending, len(rows)>100, latest['category'] if latest else None


@contextmanager
def _snapshot(store):
    nested = store.db.in_transaction
    if not nested: store.db.execute('BEGIN')
    try: yield
    finally:
        if not nested: store.db.execute('ROLLBACK')


def _next_action(record, stage, pending, integrity):
    if record['state'] in {'unknown','submitting','awaiting_verification'}:
        return {'kind':'verify_submission','label':'Verify the existing submission',
                'detail':'Inspect the employer receipt or verification step. Recruiting email does not resolve this outcome; do not submit again.'}
    if integrity != 'verified':
        return {'kind':'inspect_evidence','label':'Inspect the recorded package',
                'detail':'Package integrity could not be verified. Preserve this record and inspect a private history backup.'}
    if record['state']=='prepared':
        return {'kind':'review_draft','label':'Review the prepared application',
                'detail':'This is a draft, not a submitted application. Current policy, sources and limits are checked again before execution.'}
    if pending:
        return {'kind':'review_feedback','label':'Review matching recruiting mail',
                'detail':'Confirm the message category and exact attempt before treating it as recruiting progress.'}
    actions = {
        'assessment':('Complete the assessment','Open the original message and check its instructions and deadline. Assessments remain applicant work.'),
        'interview':('Prepare for the interview','Open the original message to confirm the time, format and next step.'),
        'offer':('Review the offer','Read the original terms and decide how to respond.'),
        'rejection':('Review the recruiting outcome','No automatic retry or strategy change follows from a rejection.'),
    }
    if stage in actions:
        label, detail = actions[stage]
        return {'kind':stage,'label':label,'detail':detail}
    if record['state'] in {'not_submitted','rejected'}:
        return {'kind':'inspect_outcome','label':'Review the submission outcome',
                'detail':'This attempt did not produce a confirmed submission. Inspect its recorded evidence; it is not automatically retried.'}
    return {'kind':'await_feedback','label':'Await recruiting feedback',
            'detail':'No later reviewed recruiting stage is recorded. Missing feedback is not a rejection.'}


def journey(store, application_id):
    if not isinstance(application_id, str) or not 1 <= len(application_id) <= 200:
        raise ValueError('Choose an existing application attempt')
    # One consistent read view across the ledger, immutable materials and reviewed mail.
    with _snapshot(store):
        record = store.application_record(application_id)
        if not record: return None
        context, origin, captured = _context(store, record)
        documents, integrity = _documents(store, record)
        reviewed, pending, more, stage = _feedback(store, application_id)
        timeline = [{'kind':'prepared','at':record['created'],'label':'Application package recorded'}]
        if record['attempted']:
            timeline.append({'kind':'submission_intent','at':record['attempted'],'label':'Final submission intent recorded'})
        timeline.append({'kind':'submission_outcome','at':record['updated'],'label':'Recorded submission outcome: '+record['state']})
        timeline.extend({'kind':'recruiting_feedback','at':item['received'],'label':'Reviewed '+item['category'],
                         'subject':item['subject'],'reviewed_at':item['updated'],'feedback_id':item['id']} for item in reviewed)
        order = {'prepared':0,'submission_intent':1,'submission_outcome':2,'recruiting_feedback':3}
        timeline.sort(key=lambda item:(item['at'],order[item['kind']]))
        return {'version':1,'as_of':now(),'application_id':application_id,'job_id':record['job_id'],
                'posting':context,'posting_context_source':origin,'posting_context_captured':captured,
                'submission':{key:record[key] for key in ('state','created','updated','attempted','confirmation','screenshot')},
                'package_hash':record['hash'],'package_integrity':integrity,'documents':documents,
                'platform_receipt':record.get('connection_receipt'),'reviewed_feedback':reviewed,
                'pending_feedback':pending,'more_feedback_available':more,'recruiting_stage':stage,
                'next_action':_next_action(record,stage,pending,integrity),'timeline':timeline}
