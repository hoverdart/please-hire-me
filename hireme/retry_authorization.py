"""One-use authority for an explicitly reconciled, exact approved retry."""
import uuid
import json

from .approval_boundary import source_fingerprint
from .util import Blocked, digest


def authorization(store, job_id):
    return store.db.execute("""SELECT a.* FROM retry_authorizations a WHERE a.job_id=?
        AND NOT EXISTS(SELECT 1 FROM retry_authorizations b WHERE b.previous_id=a.application_id)
        ORDER BY a.previous_id LIMIT 1""",(job_id,)).fetchone()


def source_revision(store,job_id):
    row=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    return digest([source_fingerprint(store),json.loads(row[0]) if row else None])


def permits(store, previous, job_id, application_id=None):
    auth=store.db.execute('SELECT * FROM retry_authorizations WHERE previous_id=? AND job_id=?',(previous['id'],job_id)).fetchone()
    if (not auth or auth['previous_id']!=previous['id'] or previous['state']!='not_submitted'
            or previous['job_id']!=job_id):
        return False
    if auth['state']=='consumed' and auth['application_id']:
        following=store.application_record(auth['application_id'])
        if following and following['state']=='not_submitted' and following['id']!=previous['id']:
            return permits(store,following,job_id,application_id)
    if auth['source_revision']!=source_revision(store,job_id):return False
    if auth['state']=='available':return True
    owners=getattr(store,'retry_owners',{})
    return (auth['state'] in {'reserved','consumed'}
        and owners.get(job_id)==auth['owner']
        and (application_id is None or application_id==auth['application_id']))


def check_package(store,job,package,*,claim=False):
    auth=authorization(store,job['id'])
    if not auth:return
    previous=store.application_record(auth['previous_id'])
    if not previous or not permits(store,previous,job['id']) or digest(package)!=auth['package_hash']:
        raise Blocked('retry_authorization_changed','Explicitly approve the exact package before retrying this reconciled attempt')
    if claim and auth['state']=='available':
        if store.db.in_transaction:
            raise Blocked('retry_authorization_changed','Retry ownership must commit before employer transport')
        owner=uuid.uuid4().hex
        with store.transaction():
            changed=store.db.execute("UPDATE retry_authorizations SET state='reserved',owner=? WHERE previous_id=? AND state='available'",(owner,auth['previous_id'])).rowcount
            if changed!=1:raise Blocked('retry_authorization_changed','Another session owns this retry')
        if not hasattr(store,'retry_owners'):store.retry_owners={}
        store.retry_owners[job['id']]=owner
        return True
    return False


def safe_state(store,job_id):
    return all(permits(store,dict(row),job_id) for row in store.db.execute(
        "SELECT id,job_id,state FROM application_records WHERE job_id=? AND state!='prepared'",(job_id,)))
