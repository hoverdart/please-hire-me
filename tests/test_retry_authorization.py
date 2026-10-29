import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hireme.approval_boundary import ApprovalBoundary
from hireme.job_holds import safe_state
from hireme.store import Store
from hireme.util import Blocked


def authorize(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.reconcile(aid,False,'Independently verified that no application was submitted')
    before=store.application_record(aid)
    store.retry_not_submitted(aid,'Applicant explicitly approved this exact retry package')
    return aid,before


def test_retry_preserves_complete_history_and_reserves_once(store,job,package):
    aid,before=authorize(store,job,package)
    assert store.application_record(aid)==before and safe_state(store,job['id'])
    with pytest.raises(ValueError):store.retry_not_submitted(aid,'Requesting the same retry again')
    new=store.prepare(job,package)
    assert new!=aid and store.application_record(aid)==before
    store.begin_submit(new)
    assert store.db.execute('SELECT state FROM retry_authorizations').fetchone()[0]=='consumed'
    assert not safe_state(store,job['id'])
    assert store.db.execute('SELECT COUNT(*) FROM application_records').fetchone()[0]==2


def test_claim_is_exclusive_and_interruption_does_not_reopen(store,job,package):
    authorize(store,job,package)
    ApprovalBoundary(store,job,package)
    other=Store(store.root)
    try:
        assert not safe_state(other,job['id'])
        with pytest.raises(Blocked):other.prepare(job,package)
        with pytest.raises(Blocked):ApprovalBoundary(other,job,package)
    finally:other.close()


def test_a_new_process_cannot_start_the_reserved_attempt(store,job,package):
    authorize(store,job,package)
    aid=store.prepare(job,package)
    other=Store(store.root)
    try:
        with pytest.raises(Blocked):other.begin_submit(aid)
        assert other.application_record(aid)['state']=='prepared'
    finally:other.close()


def test_unused_changed_approval_can_be_explicitly_renewed(store,job,package):
    aid,_=authorize(store,job,package)
    store.put_facts({'location':'Updated confirmed location'})
    from hireme.util import digest
    revised={**package,'facts_hash':digest(store.facts())}
    store.renew_retry_authorization(aid,'Explicit approval of the reviewed changed package',revised)
    new=store.prepare(job,revised)
    assert new!=aid
    with pytest.raises(ValueError):store.renew_retry_authorization(aid,'Cannot reopen the reserved retry',revised)
    assert store.db.execute("SELECT 1 FROM events WHERE kind='retry_authorization_renewed'").fetchone()


def test_source_and_package_changes_hold_retry(store,job,package):
    authorize(store,job,package)
    altered={**package,'steps':[]}
    with pytest.raises(Blocked):store.prepare(job,altered)
    store.put_facts({'location':'Changed confirmed location'})
    assert not safe_state(store,job['id'])
    with pytest.raises(Blocked):ApprovalBoundary(store,job,package)


def test_first_retry_can_bind_a_new_explicitly_approved_package(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'not_submitted')
    original=store.application_record(aid)
    store.put_facts({'location':'Updated confirmed location'})
    from hireme.util import digest
    updated={**package,'facts_hash':digest(store.facts())}
    store.retry_not_submitted(aid,'Explicitly approved the revised package after reconciliation',updated)
    assert store.application_record(aid)==original
    assert store.prepare(job,updated)!=aid


def test_explicit_retry_exempts_only_its_own_definitive_rejection(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'not_submitted','Employer rejected this attempt as possible spam')
    store.retry_not_submitted(aid,'Independently reviewed rejection and approved one exact retry')
    following=store.prepare(job,package)
    assert following!=aid
    store.begin_submit(following)
    with pytest.raises(Blocked):store.prepare(job,package)
    store.finish(following,'not_submitted','Employer rejected this later attempt as possible spam')
    with pytest.raises(Blocked,match='retry_authorization_changed'):store.prepare(job,package)
    assert store.application_record(aid)['state']=='not_submitted'


def test_retry_does_not_release_an_unrelated_company_rejection(store,job,package):
    from hireme.util import digest
    other={**job,'id':digest('other-rejected-posting'),'url':job['url']+'-other'}
    store.upsert_job(other)
    other_package={**package,'job_id':other['id'],'url':other['url']}
    old=store.prepare(other,other_package);store.begin_submit(old);store.finish(old,'not_submitted','Employer rejected as possible spam')
    # Create the target's already-recorded definitive outcome without relaxing
    # the earlier employer hold or dispatching any fixture transport.
    aid=digest('target-not-submitted')
    import json
    from hireme.util import now
    store.db.execute("INSERT INTO applications VALUES(?,?,?,'not_submitted',?,?,?,?,?,?,?)",
        (aid,job['id'],store.company(job['company']),json.dumps(package),digest(package),now(),now(),now(),'Synthetic definitive rejection',None))
    store.retry_not_submitted(aid,'Approved the target only after independent reconciliation')
    with pytest.raises(Blocked,match='company_submission_rejected'):store.prepare(job,package)
    assert store.application_record(old)['state']=='not_submitted'
    assert store.db.execute('SELECT state FROM retry_authorizations').fetchone()[0]=='available'


def test_archive_and_authorization_rollback_atomically(store,job,package,monkeypatch):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'not_submitted')
    monkeypatch.setattr(store,'event',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):store.retry_not_submitted(aid,'Explicitly approved retry after verification')
    assert store.application_record(aid)['state']=='not_submitted'
    assert not store.db.execute('SELECT 1 FROM archived_applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM retry_authorizations').fetchone()


@pytest.mark.parametrize('state',['unknown','awaiting_verification','confirmed','submitting','prepared'])
def test_uncertain_or_unreconciled_outcomes_never_authorize(store,job,package,state):
    aid=store.prepare(job,package)
    if state!='prepared':
        store.begin_submit(aid)
        if state!='submitting':store.finish(aid,state)
    with pytest.raises(ValueError):store.retry_not_submitted(aid,'Requesting a retry without reconciled evidence')
    assert not store.db.execute('SELECT 1 FROM retry_authorizations').fetchone()
