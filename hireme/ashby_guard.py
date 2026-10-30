"""Crash fence for a single live Ashby guest-draft attempt.

Ashby's public guest form does not expose owner-bound readback of an old
render. Once a mutation may have left this process, only this live session
may continue; after interruption the entire requisition remains held.
"""
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

from .util import Blocked, application_identity, digest, now


@dataclass(frozen=True)
class ScreeningSnapshot:
    """Conservative alias holds captured inside one screening transaction.

    This is only a selection optimization, never write authorization. Live
    apply and transport checks always query the current durable ledger.
    """
    job_ids: frozenset
    postings: frozenset

    def blocks(self, job):
        if job['id'] in self.job_ids:
            return True
        if job.get('host')!='jobs.ashbyhq.com':
            return False
        identity=application_identity(job.get('url',''))
        return identity[0]=='ashby' and identity[2] in self.postings


def screening_snapshot(store):
    """Read alias holds once per locked screening batch, without caching writes."""
    rows=store.db.execute("""SELECT j.id,j.url FROM jobs j JOIN remote_draft_steps d ON d.job_id=j.id
        WHERE d.step LIKE 'ashby:%' AND d.state IN ('intent','unknown')
        UNION SELECT j.id,j.url FROM jobs j JOIN job_holds h ON h.job_id=j.id
        WHERE h.reason='ashby_draft_save_failed'""")
    ids=set();postings=set()
    for row in rows:
        ids.add(row['id'])
        identity=application_identity(row['url'])
        if identity[0]=='ashby':postings.add(identity[2])
    return ScreeningSnapshot(frozenset(ids),frozenset(postings))


def _posting(store, job_id):
    row=store.db.execute('SELECT url FROM jobs WHERE id=?',(job_id,)).fetchone()
    identity=application_identity(row['url']) if row else None
    return identity[2] if identity and identity[0]=='ashby' else None


def uncertain_rows(store, job_id):
    """Find this job's unresolved steps and matching Ashby posting aliases.

    UUID equality only adds a safety hold; it never authorizes navigation or
    merges historical job identities. Invalid URLs keep exact-ID behavior.
    """
    rows=list(store.db.execute("SELECT id,job_id,state FROM remote_draft_steps WHERE job_id=? AND state IN ('intent','unknown')",(job_id,)))
    posting=_posting(store,job_id)
    if posting:
        aliases=store.db.execute("""SELECT d.id,d.job_id,d.state,j.url FROM remote_draft_steps d
            JOIN jobs j ON j.id=d.job_id WHERE d.job_id!=? AND d.step LIKE 'ashby:%'
            AND d.state IN ('intent','unknown')""",(job_id,))
        for row in aliases:
            identity=application_identity(row['url'])
            if identity[0]=='ashby' and identity[2]==posting:rows.append(row)
    return rows


def uncertain(store, job_id):
    # This separate projection is used for Ashby-specific user guidance.
    if store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step LIKE 'ashby:%' AND state IN ('intent','unknown') LIMIT 1",(job_id,)).fetchone():return True
    posting=_posting(store,job_id)
    if not posting:return False
    rows=store.db.execute("""SELECT j.url FROM remote_draft_steps d JOIN jobs j ON j.id=d.job_id
        WHERE d.step LIKE 'ashby:%' AND d.state IN ('intent','unknown')""")
    for row in rows:
        identity=application_identity(row['url'])
        if identity[0]=='ashby' and identity[2]==posting:return True
    return False


def legacy_failed(store,job_id):
    if store.db.execute("SELECT 1 FROM job_holds WHERE job_id=? AND reason='ashby_draft_save_failed'",(job_id,)).fetchone():return True
    posting=_posting(store,job_id)
    if not posting:return False
    rows=store.db.execute("""SELECT j.url FROM job_holds h JOIN jobs j ON j.id=h.job_id
        WHERE h.reason='ashby_draft_save_failed'""")
    for row in rows:
        identity=application_identity(row['url'])
        if identity[0]=='ashby' and identity[2]==posting:return True
    return False


def check(store, job_id, owned=None):
    rows=uncertain_rows(store,job_id)
    if owned:
        if len(rows)!=1 or rows[0]['id']!=owned or rows[0]['state']!='intent' or legacy_failed(store,job_id):
            raise Blocked('ashby_draft_uncertain','The saved guest draft cannot be safely resumed')
    elif rows or legacy_failed(store,job_id):
        raise Blocked('ashby_draft_uncertain','The saved guest draft cannot be safely resumed')


def begin(store, job, package, request, overrides):
    # A savepoint inside an outer transaction is not a durable write-ahead
    # record. Fail closed rather than letting a request escape before COMMIT.
    if store.db.in_transaction:
        raise Blocked('ashby_draft_uncertain','Cannot commit a draft intent before transport')
    body=overrides.get('post_data',getattr(request,'post_data_buffer',None)) or b''
    if isinstance(body,str):body=body.encode()
    record=digest([job['id'],'ashby:attempt',uuid.uuid4().hex])
    secret=secrets.token_bytes(32)
    request_hash=hmac.new(secret,body,hashlib.sha256).hexdigest()
    sources_hash=hmac.new(secret,digest(package).encode(),hashlib.sha256).hexdigest()
    with store.transaction():
        check(store,job['id'])
        store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
            (record,job['id'],'jobs.ashbyhq.com','ashby:attempt',request_hash,sources_hash,'intent',now(),now(),''))
        store.event('remote_step_intent',job['id'],{'step':'ashby:attempt','tenant':'jobs.ashbyhq.com','record_id':record})
    return record


def finish(store, owned):
    """Close the live session without claiming the remote draft was absent."""
    if owned:
        store.db.execute("UPDATE remote_draft_steps SET state='unknown',updated=? WHERE id=? AND step='ashby:attempt' AND state='intent'",(now(),owned))
