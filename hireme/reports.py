"""Durable per-run reports; mail delivery never retries an application."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime,time,timezone
from zoneinfo import ZoneInfo

from .gmail import GmailClient, owner_email
from .store import worker_lock
from .util import now, digest


FOOTER = ('\n\nQuestions that keep coming up can be answered once for every employer: Your facts → Add context in your own words.\n'
          'Open the job links above on your phone to review or apply. Confirmed submissions are already applied. '
          'For uncertain submissions, check with the employer before applying again.\n'
          'After applying yourself, return to HireMeMaybe, find the job, and choose Applied manually. '
          'This records your application and stops automatic retries. Jobs with uncertain submission records need reconciliation first.\n'
          'Open HireMeMaybe on the computer running the worker using your saved dashboard link.\n')


def _short(text, limit=140):
    """One line, cut at the first question or sentence when the label is long."""
    text = ' '.join(str(text).split()).rstrip(' *')
    if len(text) <= limit:
        return text
    match = re.match(r'(.{20,%d}?[?.:])\s' % limit, text)
    return match[1] + ' …' if match else text[:limit].rstrip() + '…'


def _questions(store, job_id):
    """Each unresolved question with a readable reason, and a failed draft's review."""
    from .presentation import REASON_GUIDANCE
    lines = {}
    for q in store.db.execute('SELECT label,reason FROM questions WHERE job_id=? AND resolved=0 ORDER BY rowid', (job_id,)):
        reason = REASON_GUIDANCE.get(q['reason'], (q['reason'].replace('_', ' ').capitalize(),))[0]
        line = f"{_short(q['label'])} — {reason}"
        if q['reason'] == 'writing_unsupported':
            for e in store.db.execute("SELECT detail FROM events WHERE kind='writing_reviewed' AND subject=? ORDER BY seq DESC LIMIT 5", (job_id,)):
                review = json.loads(e['detail'])
                if review.get('question') == q['label'] and review.get('supported') is False:
                    line += f" (review: {_short(review.get('reason', ''), 200)})"
                    break
        # One line per question; an older widget or wording of it is superseded.
        lines.pop(' '.join(q['label'].casefold().split()), None)
        lines[' '.join(q['label'].casefold().split())] = line
    return list(lines.values())


def _receipt(store,job_id,started):
    row=store.db.execute("SELECT * FROM application_records WHERE job_id=? AND state='confirmed' AND updated>=? ORDER BY updated DESC LIMIT 1",(job_id,started)).fetchone()
    if not row:return ''
    package=json.loads(row['package'])
    resumes=sorted({doc['hash'] for doc in package.get('documents',[]) if doc.get('hash') and
                    (doc.get('kind')=='resume' or re.search(r'resume|cv',doc.get('field',{}).get('label',''),re.I))})
    return (f"\n  Application record: {row['id']}\n  Confirmed at: {row['updated']}"
            + ''.join('\n  Resume SHA-256: '+checksum for checksum in resumes)
            + '\n  Next action: wait for employer follow-up; do not apply again.')


def _recruiting_next_steps(store,email):
    """Reviewed messages only; suggestions cannot manufacture a recruiting stage."""
    owner=digest(email)
    rows=list(store.db.execute('''SELECT stage.category,stage.received,j.company,j.title,j.url FROM (
        SELECT application_id,category,received,ROW_NUMBER() OVER (PARTITION BY application_id
            ORDER BY (category='acknowledgement'),received DESC,id DESC) AS position
        FROM recruiting_feedback WHERE owner_hash=? AND state='confirmed' AND application_id IS NOT NULL
    ) stage JOIN application_records a ON a.id=stage.application_id JOIN jobs j ON j.id=a.job_id
    WHERE stage.position=1 AND stage.category IN ('assessment','interview') ORDER BY stage.received DESC LIMIT 50''',(owner,)))
    pending=store.db.execute("SELECT count(*) FROM recruiting_feedback WHERE owner_hash=? AND state='pending'",(owner,)).fetchone()[0]
    sections=[]
    for category,heading,action in [('assessment','Assessments — check status and complete required work','Review the employer assessment and its deadline.'),
                                   ('interview','Interviews — prepare or update status','Review the invitation, confirm scheduling and prepare.')]:
        entries=[row for row in rows if row['category']==category]
        if entries:sections.append(heading+'\n\n'+'\n\n'.join(
            f"{row['company']} — {row['title']}\nReviewed employer update: {row['received']}\nNext action: {action}\n{row['url']}" for row in entries))
    if pending:sections.append(f'Employer messages awaiting your review: {pending}. Review and link them before treating a suggested assessment or interview as confirmed.')
    return sections


def _outstanding(store):
    """Keep earlier unresolved work visible without replaying applications."""
    from .job_holds import ELIGIBILITY_REVIEWS
    manual={};uncertain={}
    for row in store.db.execute('''SELECT h.*,j.company,j.title,j.url FROM job_holds h JOIN jobs j ON j.id=h.job_id
        WHERE (h.category IN ('information','mapping','documents','account','review','connection','unsupported')
        OR (h.category='transient' AND h.retry_at IS NULL AND h.retry_count>=2)
        OR h.reason IN ('''+','.join('?' for _ in ELIGIBILITY_REVIEWS)+'''))
        AND NOT EXISTS (SELECT 1 FROM job_decisions d WHERE d.job_id=h.job_id)
        AND NOT EXISTS (SELECT 1 FROM application_records a WHERE a.job_id=h.job_id AND a.state='confirmed')
        ORDER BY j.score DESC,h.updated''',tuple(sorted(ELIGIBILITY_REVIEWS))):
        item=dict(row)
        # These are consequences of an earlier attempt, not separate human
        # tasks for every newly discovered role at that company. The original
        # uncertain/verification-pending record is reported below.
        if item['reason'] in {'company_uncertain','company_verification_pending'}:continue
        if item['reason']=='model_budget_exhausted':
            evidence=json.loads(item['evidence'])
            fields=evidence.get('information_fields',evidence.get('fields',[]))
            if not any(field['reason']!='model_budget_exhausted' for field in fields):continue
        manual[item['job_id']]=item
    for row in store.db.execute('''SELECT a.job_id,a.state,a.updated,j.company,j.title,j.url FROM application_records a
        JOIN jobs j ON j.id=a.job_id WHERE a.state IN ('unknown','submitting','awaiting_verification') ORDER BY a.updated DESC'''):
        uncertain.setdefault(row['job_id'],dict(row))
    for job_id in uncertain:manual.pop(job_id,None)
    return manual,uncertain


def queue_report(store, run_id):
    run = store.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not run or not run['finished']:
        raise ValueError('Only completed or stopped runs can be reported')
    email = owner_email(store)
    from .presentation import REASON_GUIDANCE, WAIT_REASONS
    waiting = 'Waiting for a company or daily limit — no action needed'
    groups = {'Applied successfully — employer-confirmed': [], 'Blocked — review or apply manually': [], 'Other outcomes — review before retrying': [],
              waiting: [], 'Waiting for the model allowance — retried automatically': []}
    terminal={}
    for e in store.db.execute("SELECT subject,detail FROM events WHERE kind='application_finished' AND timestamp>=? ORDER BY seq", (run['started'],)):
        detail = json.loads(e['detail'])
        if detail.get('run_id') != run_id:
            continue
        terminal[e['subject']]=detail
    outstanding,held_outcomes=_outstanding(store)
    for job_id,detail in terminal.items():
        job = store.db.execute('SELECT company,title,url FROM jobs WHERE id=?', (job_id,)).fetchone()
        if job:
            manual_questions=job_id in outstanding or store.db.execute("SELECT 1 FROM questions WHERE job_id=? AND resolved=0 AND reason!='model_budget_exhausted' LIMIT 1",(job_id,)).fetchone()
            receipt=_receipt(store,job_id,run['started']) if detail['outcome']=='confirmed' else ''
            group = ('Applied successfully — employer-confirmed' if detail['outcome'] == 'confirmed' and receipt else
                     'Other outcomes — review before retrying' if detail['outcome']=='confirmed' else
                     waiting if detail.get('reason') in WAIT_REASONS else
                     'Waiting for the model allowance — retried automatically' if detail.get('reason') == 'model_budget_exhausted' and not manual_questions else
                     'Blocked — review or apply manually' if detail['outcome'] == 'blocked' else
                     'Other outcomes — review before retrying')
            questions = _questions(store, job_id)
            reason = REASON_GUIDANCE.get(detail.get('reason'), (detail.get('reason'),))[0]
            uncertainty = ('Submission outcome is uncertain. Check with the employer before applying again.'
                           if detail['outcome'] in {'unknown','submitting'} else
                           'Email verification is pending. Complete verification before applying again.'
                           if detail['outcome']=='awaiting_verification' else '')
            groups[group].append(f"{job['company']} — {job['title']}: "
                                 + (reason if detail['outcome'] == 'blocked' and reason else
                                    detail['outcome'] + (f" ({reason})" if reason else ''))
                                 + ('\n'+uncertainty if uncertainty else '')
                                 + (''.join('\n  • ' + line for line in questions) if questions
                                    else f"\n{_short(detail['detail'], 300)}" if detail.get('detail') else '')
                                 + receipt
                                 + f"\n{job['url']}")
    outcomes = [heading + '\n\n' + '\n\n'.join(items) for heading, items in groups.items() if items]
    detail = json.loads(run['detail']) if run['detail'].startswith('{') else {'reason': run['detail']}
    human=len(groups['Blocked — review or apply manually'])+len(set(outstanding)-terminal.keys())
    unconfirmed=len(groups['Other outcomes — review before retrying'])+len(set(held_outcomes)-terminal.keys())
    recruiting=_recruiting_next_steps(store,email)
    earlier=[]
    from .presentation import REASON_GUIDANCE
    for records,heading,default in [(outstanding,'Outstanding human interventions from earlier batches','Review this application in the dashboard.'),
                                    (held_outcomes,'Earlier unconfirmed submissions — verify before any retry','Check the existing employer record; do not apply again.')]:
        items=[item for job_id,item in records.items() if job_id not in terminal]
        if items:
            lines=[]
            for item in items[:20]:
                action=REASON_GUIDANCE.get(item.get('reason'),('',default))[1]
                if item.get('category')=='transient':action='Automatic retries are exhausted. Review the problem in the dashboard and use Recheck after repairing it.'
                lines.append(f"{item['company']} — {item['title']}\nNext action: {action}"
                    + ''.join('\n  • '+question for question in _questions(store,item['job_id']))+f"\n{item['url']}")
            earlier.append(heading+f' ({len(items)}; showing up to 20)\n\n'+'\n\n'.join(lines))
    attention=bool(outstanding or held_outcomes or recruiting)
    attention_signature=digest({'manual':[(key,item['dependency']) for key,item in sorted(outstanding.items())],
        'uncertain':[(key,item['state'],item['updated']) for key,item in sorted(held_outcomes.items())],'recruiting':recruiting})
    body = (f"Confirmed submissions: {run['submitted']}\nHuman interventions: {human}\nUnconfirmed results to verify: {unconfirmed}\n\n"
            + ('\n\n'.join(recruiting)+'\n\n' if recruiting else '')
            + f"Application batch {run_id}\nStarted: {run['started']}\nFinished: {run['finished']}\n"
            f"Status: {run['status']}\nMode: {detail.get('mode', 'live')}\n"
            + ('\n\n'.join(outcomes) or 'No application outcomes recorded.')
            + ('\n\n'+'\n\n'.join(earlier) if earlier else ''))
    body+=f"\n\nDiagnostics\nBrowser sessions started: {detail.get('attempts',0)}. These are not completed applications."
    if detail.get('reason'):
        body += '\n\nStopped because: ' + detail['reason']
    mail_failures={reason:count for reason,count in detail.get('reasons',{}).items()
                   if reason in {'prior_application_mail_unavailable','prior_application_company_review'}}
    if mail_failures:
        body+='\n\nEmail-history checks need attention:\n'
        body+='\n'.join(f'{count} roles held: '+REASON_GUIDANCE[reason][1] for reason,count in mail_failures.items())
    body += FOOTER
    enabled = store.settings()['gmail_reports']
    # Idle schedules remain local history. Completed screening and paused runs
    # with no attempts add no application update to email. Failures/setup stops,
    # legacy runs without an explicit count, and any actual outcome still report.
    idle=run['status'] in {'finished','paused'} and detail.get('attempts')==0 and not terminal and run['submitted']==0 and not mail_failures
    # Unchanged outstanding tasks merit one reminder per local day, rather
    # than disappearing when all new application work is held. A new task or
    # reviewed recruiting stage can produce a fresh actionable update sooner.
    day=datetime.now(ZoneInfo(store.settings()['timezone'])).date()
    start=datetime.combine(day,time(),ZoneInfo(store.settings()['timezone'])).astimezone(timezone.utc).isoformat()
    already_reported=any(json.loads(row[0]).get('signature')==attention_signature for row in store.db.execute('''
        SELECT e.detail FROM events e JOIN report_outbox o ON o.id=e.subject
        WHERE e.kind='batch_attention_report' AND e.timestamp>=? AND o.recipient=?
        AND o.state IN ('pending','sending','sent','uncertain')''',(start,email)))
    if idle and attention and not already_reported:idle=False
    state='suppressed' if idle else 'pending' if enabled else 'disabled'
    with store.transaction():
        inserted=store.db.execute('INSERT OR IGNORE INTO report_outbox VALUES(?,?,?,?,?,?,?,0,NULL,NULL,NULL)',
                         (run_id, email, f"Application update: {run['submitted']} confirmed; {human} need you; {unconfirmed} to verify",
                          body, f'<{uuid.uuid4().hex}@please-hire-me.local>', state, now()))
        if inserted.rowcount and state=='pending' and attention:store.event('batch_attention_report',run_id,{'signature':attention_signature})
    return run_id


def recover_reports(store):
    """Queue abandoned batches once, without inferring acceptance or retrying.

    Store.recover must run first. A missing terminal event can use only the
    current application bound to this browser attempt's durable submit intent;
    a later attempt for the same job cannot supply its outcome.
    """
    recovered=0
    runs=list(store.db.execute("""SELECT r.* FROM runs r LEFT JOIN report_outbox o ON o.id=r.id
        WHERE r.status='interrupted' AND o.id IS NULL ORDER BY r.started,r.id"""))
    for run in runs:
        try:
            with store.transaction():
                started={};terminal={}
                for event in store.db.execute("""SELECT seq,kind,subject,detail FROM events
                        WHERE timestamp>=? AND kind IN ('application_started','application_finished') ORDER BY seq""",
                        (run['started'],)):
                    detail=json.loads(event['detail'])
                    if detail.get('run_id')!=run['id']:continue
                    if event['kind']=='application_started':started[event['subject']]=event['seq']
                    else:terminal[event['subject']]=detail
                if not started:continue
                for job_id,sequence in started.items():
                    if job_id in terminal:continue
                    following=store.db.execute("""SELECT MIN(seq) FROM events
                        WHERE kind='application_started' AND seq>?""",(sequence,)).fetchone()[0]
                    end=following or 2**63-1
                    intent=store.db.execute("""SELECT e.seq,e.subject,a.job_id,a.state FROM events e
                        LEFT JOIN application_records a ON a.id=e.subject WHERE e.kind='submit_intent'
                        AND e.seq>? AND e.seq<? ORDER BY e.seq DESC LIMIT 1""",(sequence,end)).fetchone()
                    # Explicit retry may remove the old row or reuse its
                    # deterministic ID. Its preserved audit still binds the
                    # old intent; the newer row must not supply acceptance.
                    matches=bool(intent and intent['job_id']==job_id)
                    if intent and intent['job_id'] is None:
                        for retry in store.db.execute("SELECT detail FROM events WHERE kind='application_retry_requested' AND subject=?",(intent['subject'],)):
                            prior=json.loads(retry['detail']).get('previous_application',{})
                            if prior.get('id')==intent['subject'] and prior.get('job_id')==job_id:matches=True
                    recorded=(store.db.execute("""SELECT kind FROM events WHERE subject=? AND seq>? AND seq<?
                        AND kind IN ('confirmed','unknown','awaiting_verification','not_submitted')
                        ORDER BY seq DESC LIMIT 1""",(intent['subject'],intent['seq'],end)).fetchone() if matches else None)
                    later=store.db.execute("""SELECT 1 FROM events WHERE kind='application_started'
                        AND subject=? AND seq>? LIMIT 1""",(job_id,sequence)).fetchone()
                    current=(intent['state'] if matches and not later else None)
                    outcome=(recorded['kind'] if recorded else current if current in
                             {'confirmed','unknown','awaiting_verification','not_submitted'} else
                             'unknown' if matches else 'blocked')
                    detail={'run_id':run['id'],'outcome':outcome,'recovered':True,
                            'detail':('The worker stopped before the outcome was recorded. Verify the existing attempt before applying again.'
                                      if outcome in {'unknown','awaiting_verification'} else
                                      'The worker stopped during this attempt. Its saved application record supplies this outcome.'
                                      if matches else 'The worker stopped before its outcome was recorded. Review the saved application history.')}
                    store.event('application_finished',job_id,detail)
                    terminal[job_id]=detail
                detail=json.loads(run['detail']) if run['detail'].startswith('{') else {}
                mode=detail.get('mode','live')
                outcomes={}
                for result in terminal.values():
                    outcome=result['outcome'];outcomes[outcome]=outcomes.get(outcome,0)+1
                confirmed=outcomes.get('confirmed',0) if mode=='live' else 0
                detail.update(attempts=len(started),outcomes=outcomes,confirmed=confirmed,
                              prepared=outcomes.get('prepared',0) if mode=='prepare' else 0,
                              reason='Worker stopped before completing this batch; outcomes were recovered from saved application records.')
                store.db.execute('UPDATE runs SET submitted=?,detail=? WHERE id=?',
                                 (confirmed,json.dumps(detail),run['id']))
                queue_report(store,run['id'])
                recovered+=1
        except Exception as error:
            # A malformed old record cannot block a new cycle. The transaction
            # leaves that report absent so a later recovery can try again.
            store.event('batch_report_recovery_failed',run['id'],{'type':type(error).__name__})
    return recovered


def _combined(store, rows):
    """One message for every batch finished since the last report, oldest first."""
    if len(rows) == 1:
        return rows[0]['subject'], rows[0]['body']
    confirmed = sum(r[0] for r in store.db.execute(
        'SELECT submitted FROM runs WHERE id IN (' + ','.join('?' for _ in rows) + ')', [r['id'] for r in rows]))
    sections = [r['body'][:-len(FOOTER)] if r['body'].endswith(FOOTER) else r['body'].rstrip() for r in rows]
    body = (f'Confirmed submissions: {confirmed}\nReview the next actions below.\n\n'
            + ('\n\n' + '─' * 24 + '\n\n').join(sections) + FOOTER)
    return f'Recruiting update: {confirmed} confirmed — review next actions', body


def flush_reports(store, client_factory=GmailClient, limit=25):
    if not store.settings()['gmail_reports']:
        return {'sent': 0, 'enabled': False}
    with worker_lock(store.root, 'reports'):
        # A process disappearing after send authorization may have delivered mail.
        store.db.execute("UPDATE report_outbox SET state='uncertain',last_error='Process stopped during mail send' WHERE state='sending'")
        rows = list(store.db.execute("SELECT * FROM report_outbox WHERE state='pending' ORDER BY created LIMIT ?", (limit,)))
        if not rows:
            return {'sent': 0, 'enabled': True}
        try:
            client = client_factory(store)
        except Exception as e:
            store.db.execute("UPDATE report_outbox SET last_error=? WHERE state='pending'", (getattr(e, 'reason', type(e).__name__),))
            return {'sent': 0, 'enabled': True, 'error': getattr(e, 'reason', type(e).__name__)}
        for row in rows:
            if row['recipient'] != client.email:
                store.db.execute("UPDATE report_outbox SET state='held',last_error='Owner email changed' WHERE id=?", (row['id'],))
        rows = [row for row in rows if row['recipient'] == client.email]
        if not rows:
            return {'sent': 0, 'enabled': True}
        ids = [row['id'] for row in rows]
        marks = ','.join('?' for _ in ids)
        subject, body = _combined(store, rows)
        with store.transaction():
            store.db.execute(f"UPDATE report_outbox SET state='sending',attempts=attempts+1,last_error=NULL WHERE id IN ({marks})", ids)
        try:
            receipt = client.send_report(subject, body, rows[-1]['message_id'])
        except Exception as e:
            store.db.execute(f"UPDATE report_outbox SET state='uncertain',last_error=? WHERE id IN ({marks})", (type(e).__name__, *ids))
            for rid in ids:
                store.event('batch_report_uncertain', rid, {})
            return {'sent': 0, 'enabled': True, 'batches': 0}
        store.db.execute(f"UPDATE report_outbox SET state='sent',provider_id=?,sent=? WHERE id IN ({marks})", (receipt, now(), *ids))
        for rid in ids:
            store.event('batch_report_sent', rid, {'provider_id': receipt, 'combined': len(ids)})
    return {'sent': 1, 'enabled': True, 'batches': len(ids)}


def report_status(store):
    return [dict(r) for r in store.db.execute('SELECT id,state,created,sent,last_error FROM report_outbox ORDER BY created DESC LIMIT 30')]
