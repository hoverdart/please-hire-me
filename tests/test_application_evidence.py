import json
import pytest
from hireme.application_evidence import snapshot,reconcile_unknowns,record,ats_for
from hireme.util import Blocked,application_identity,digest


def unknown(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown','Synthetic uncertain outcome')
    return aid


def test_unknown_audit_is_explicit_and_cannot_retry_without_proof(store,job,package):
    aid=unknown(store,job,package)
    store.event('submission_response',aid,{'host':'api.hcaptcha.com','status':200,'path':'/private-token'})
    result=reconcile_unknowns(store,ats=['lever'])
    assert result['counts']=={'confirmed':0,'not_submitted':0,'unresolved':1}
    bundle=result['attempts'][0]
    assert bundle['verified_at'] is None and not bundle['retry_allowed']
    assert '/private-token' not in json.dumps(bundle)
    assert store.application_record(aid)['state']=='unknown'
    with pytest.raises(Blocked):store.prepare(job,package)
    assert len(reconcile_unknowns(store)['attempts'])==1


@pytest.mark.parametrize('url',[
    'https://notgreenhouse.io/jobs/123', 'https://greenhouse.io.attacker.invalid/jobs/123',
    'https://noticims.com/jobs/123', 'https://icims.com.attacker.invalid/jobs/123',
])
def test_ats_scope_never_accepts_lookalike_hosts(url):
    assert ats_for(url)=='other'


@pytest.mark.parametrize('receipt',['fresh','missing','stale','wrong_request','wrong_posting','not_fresh'])
def test_ashby_acknowledgement_requires_its_own_fresh_receipt(store,job,package,receipt):
    ashby={**job,'id':digest('ashby-receipt-fixture'),'host':'jobs.ashbyhq.com',
           'url':'https://jobs.ashbyhq.com/acme/12345678-1234-4234-8234-123456789abc'}
    store.upsert_job(ashby)
    aid=unknown(store,ashby,{**package,'job_id':ashby['id'],'url':ashby['url']})
    request=digest('ashby final request')
    store.event('ashby_submission_write',aid,{'request_hash':request})
    evidence={'platform':'ashby','posting_identity':list(application_identity(ashby['url'])),
              'request_hash':request,'receipt_sha256':digest('fresh visible receipt'),'fresh':True}
    if receipt=='stale':store.event('submission_receipt_verified',aid,evidence)
    store.event('ashby_submission_response',aid,{
        'platform':'ashby','acknowledged':True,'evidence_version':1,
        'posting_identity':list(application_identity(ashby['url'])),
        'http_status':200,'request_hash':request,'response_sha256':digest('FormSubmitSuccess')})
    if receipt not in {'missing','stale'}:
        if receipt=='wrong_request':evidence['request_hash']=digest('other request')
        if receipt=='wrong_posting':evidence['posting_identity']=['url','https://other.invalid/']
        if receipt=='not_fresh':evidence['fresh']=False
        store.event('submission_receipt_verified',aid,evidence)
    result=reconcile_unknowns(store)
    assert result['counts']['confirmed']==(1 if receipt=='fresh' else 0)
    assert store.application_record(aid)['state']==('confirmed' if receipt=='fresh' else 'unknown')


@pytest.mark.parametrize('mutation',['none','wrong_posting','no_write','bad_status','unacknowledged','unbound_hash','duplicate_write'])
def test_only_bound_native_receipt_reconciles(store,job,package,mutation):
    aid=unknown(store,job,package)
    request=digest('transport')
    if mutation!='no_write':store.event('native_submission_write',aid,{'platform':'lever','request_hash':request})
    proof={'platform':'lever','acknowledged':True,'evidence_version':1,'posting_identity':list(application_identity(job['url'])),
           'http_status':200,'request_hash':request,'response_sha256':digest('employer receipt')}
    if mutation=='wrong_posting':proof['posting_identity']=['url','https://unrelated.example/']
    if mutation=='bad_status':proof['http_status']=500
    if mutation=='unacknowledged':proof['acknowledged']=False
    if mutation=='unbound_hash':proof['request_hash']=digest('other request')
    if mutation=='duplicate_write':store.event('native_submission_write',aid,{'platform':'lever','request_hash':request})
    store.event('native_submission_response',aid,proof)
    result=reconcile_unknowns(store)
    expected='confirmed' if mutation=='none' else 'unresolved'
    assert result['attempts'][0]['status']==expected
    assert store.application_record(aid)['state']==('confirmed' if expected=='confirmed' else 'unknown')
    assert not store.db.execute('SELECT 1 FROM retry_authorizations').fetchone()


@pytest.mark.parametrize('dispatched',[False,True])
def test_explicit_closed_grant_is_required_to_prove_no_submission(store,job,package,dispatched):
    aid=unknown(store,job,package)
    store.event('native_submission_not_dispatched',aid,{'platform':'lever'})
    if dispatched:store.event('native_submission_write',aid,{'platform':'lever'})
    bundle=reconcile_unknowns(store)['attempts'][0]
    assert bundle['status']==('unresolved' if dispatched else 'not_submitted')
    with pytest.raises(Blocked):store.prepare(job,package)


def test_contract_recorded_at_intent_and_outcome(store,job,package):
    aid=unknown(store,job,package)
    rows=list(store.db.execute("SELECT detail FROM events WHERE kind='application_evidence' AND subject=? ORDER BY seq",(aid,)))
    bundles=[json.loads(r[0]) for r in rows]
    assert [b['status'] for b in bundles]==['submitting','unresolved']
    assert all(set(b)>={'attempt_id','posting_id','ats','status','evidence','verification_method','verified_at'} for b in bundles)


def test_posting_scope_releases_unrelated_role_but_not_same_posting(store,job,package):
    aid=unknown(store,job,package)
    other={**job,'id':digest('different-job'),'url':job['url']+'-other'};store.upsert_job(other)
    with pytest.raises(Blocked,match='company_uncertain'):store._check_budget(other,store.settings())
    store.update_settings({'uncertain_application_scope':'posting'})
    store._check_budget(other,store.settings())
    with pytest.raises(Blocked,match='duplicate_or_uncertain'):store._check_budget(job,store.settings())
    store.update_settings({'max_per_company':1})
    with pytest.raises(Blocked,match='company_limit'):store._check_budget(other,store.settings())


def test_ats_scope_applies_to_final_submission_and_boundary(store,job,package):
    from hireme.approval_boundary import ApprovalBoundary
    aid=store.prepare(job,package);boundary=ApprovalBoundary(store,job,package,application_id=aid)
    store.update_settings({'allowed_application_ats':['ashby']})
    with pytest.raises(Blocked,match='ats_out_of_scope'):store.begin_submit(aid)
    with pytest.raises(Blocked,match='paused'):boundary.check()
    assert store.application_record(aid)['state']=='prepared'
