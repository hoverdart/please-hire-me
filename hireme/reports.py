"""Durable per-run reports; mail delivery never retries an application."""
from __future__ import annotations

import json
import re
import uuid

from .gmail import GmailClient, owner_email
from .store import worker_lock
from .util import now


FOOTER = ('\n\nQuestions that keep coming up can be answered once for every employer: Your facts → Add context in your own words.\n'
          'Open the job links above on your phone to review or apply. Confirmed submissions are already applied. '
          'For uncertain submissions, check with the employer before applying again.\n'
          'After applying yourself, return to Application desk on the Pi, find the job, and choose Applied manually. '
          'This records your application and stops automatic retries. Jobs with uncertain submission records need reconciliation first.\n'
          'Open Application desk on the Pi: http://127.0.0.1:8766 (through Raspberry Pi Connect).\n')


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
    lines = []
    for q in store.db.execute('SELECT label,reason FROM questions WHERE job_id=? AND resolved=0 ORDER BY rowid', (job_id,)):
        reason = REASON_GUIDANCE.get(q['reason'], (q['reason'].replace('_', ' ').capitalize(),))[0]
        line = f"{_short(q['label'])} — {reason}"
        if q['reason'] == 'writing_unsupported':
            for e in store.db.execute("SELECT detail FROM events WHERE kind='writing_reviewed' AND subject=? ORDER BY seq DESC LIMIT 5", (job_id,)):
                review = json.loads(e['detail'])
                if review.get('question') == q['label'] and review.get('supported') is False:
                    line += f" (review: {_short(review.get('reason', ''), 200)})"
                    break
        lines.append(line)
    return lines


def queue_report(store, run_id):
    run = store.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not run or not run['finished']:
        raise ValueError('Only completed or stopped runs can be reported')
    email = owner_email(store)
    from .presentation import REASON_GUIDANCE, WAIT_REASONS
    groups = {'Applied successfully': [], 'Blocked — review or apply manually': [], 'Other outcomes — review before retrying': [],
              'Waiting for a company or daily limit — no action needed': []}
    terminal={}
    for e in store.db.execute("SELECT subject,detail FROM events WHERE kind='application_finished' AND timestamp>=? ORDER BY seq", (run['started'],)):
        detail = json.loads(e['detail'])
        if detail.get('run_id') != run_id:
            continue
        terminal[e['subject']]=detail
    for job_id,detail in terminal.items():
        job = store.db.execute('SELECT company,title,url FROM jobs WHERE id=?', (job_id,)).fetchone()
        if job:
            group = ('Applied successfully' if detail['outcome'] == 'confirmed' else
                     'Waiting for a company or daily limit — no action needed' if detail.get('reason') in WAIT_REASONS else
                     'Blocked — review or apply manually' if detail['outcome'] == 'blocked' else
                     'Other outcomes — review before retrying')
            questions = _questions(store, job_id)
            reason = REASON_GUIDANCE.get(detail.get('reason'), (detail.get('reason'),))[0]
            groups[group].append(f"{job['company']} — {job['title']}: "
                                 + (reason if detail['outcome'] == 'blocked' and reason else
                                    detail['outcome'] + (f" ({reason})" if reason else ''))
                                 + (''.join('\n  • ' + line for line in questions) if questions
                                    else f"\n{_short(detail['detail'], 300)}" if detail.get('detail') else '')
                                 + f"\n{job['url']}")
    outcomes = [heading + '\n\n' + '\n\n'.join(items) for heading, items in groups.items() if items]
    detail = json.loads(run['detail']) if run['detail'].startswith('{') else {'reason': run['detail']}
    body = (f"Application batch {run_id}\nStarted: {run['started']}\nFinished: {run['finished']}\n"
            f"Status: {run['status']}\nMode: {detail.get('mode', 'live')}\n"
            f"Confirmed submissions: {run['submitted']}\nAttempts: {detail.get('attempts', 0)}\n\n"
            + ('\n\n'.join(outcomes) or 'No application outcomes recorded.'))
    if detail.get('reason'):
        body += '\n\nStopped because: ' + detail['reason']
    body += FOOTER
    enabled = store.settings()['gmail_reports']
    with store.transaction():
        store.db.execute('INSERT OR IGNORE INTO report_outbox VALUES(?,?,?,?,?,?,?,0,NULL,NULL,NULL)',
                         (run_id, email, f"Application batch: {run['submitted']} confirmed — {run['status']}",
                          body, f'<{uuid.uuid4().hex}@please-hire-me.local>', 'pending' if enabled else 'disabled', now()))
    return run_id


def _combined(store, rows):
    """One message for every batch finished since the last report, oldest first."""
    if len(rows) == 1:
        return rows[0]['subject'], rows[0]['body']
    confirmed = sum(r[0] for r in store.db.execute(
        'SELECT submitted FROM runs WHERE id IN (' + ','.join('?' for _ in rows) + ')', [r['id'] for r in rows]))
    sections = [r['body'][:-len(FOOTER)] if r['body'].endswith(FOOTER) else r['body'].rstrip() for r in rows]
    body = (f'{len(rows)} application batches finished since the last report.\n\n'
            + ('\n\n' + '─' * 24 + '\n\n').join(sections) + FOOTER)
    return f'Application batches: {confirmed} confirmed across {len(rows)} batches', body


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
