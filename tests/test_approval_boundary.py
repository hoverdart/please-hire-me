"""Applicant edits during expensive validation must revoke pending writes."""
import json
from types import SimpleNamespace

import pytest

from hireme.approval_boundary import ApprovalBoundary
from hireme.browser import Browser
from hireme.util import Blocked
from hireme.util import digest
from tests.test_ashby_drafts import draft, request, submission_fixture


@pytest.mark.parametrize('operation', ['draft', 'final'])
@pytest.mark.parametrize('change', ['pause', 'pause_resume', 'facts', 'decision', 'policy', 'writing_settings', 'cover_letters', 'documents'])
def test_validation_cannot_keep_authority_after_applicant_changes(store, job, package, monkeypatch, operation, change):
    if change == 'cover_letters':
        store.update_settings({'tailored_writing': True, 'cover_letters': True})
    if operation == 'draft':
        d, target = draft(store, job, package)
        r, data = request()
        before = d.remaining
        consume = lambda: d.allow(r, data, 'late-save')
    else:
        d, grant, r, data = submission_fixture(store, job, package)
        target = d.job
        store.begin_submit(grant.aid)
        consume = lambda: grant.allow(r, data)

    def change_during_validation(*args, **kwargs):
        if change in {'pause', 'pause_resume'}:
            store.update_settings({'live_enabled': False})
            if change == 'pause_resume':
                store.update_settings({'live_enabled': True})
        elif change == 'facts':
            store.put_facts({'phone': '5557654321'})
        elif change == 'decision':
            # A post-intent decision is normally rejected by the public API;
            # the transport check must still reject a concurrent persisted hold.
            store.db.execute('INSERT INTO job_decisions VALUES(?,?,?,?)',
                             (target['id'], 'skipped', 'discovered', '2026-10-20'))
        elif change == 'policy':
            store.update_settings({'max_per_day': store.settings()['max_per_day']+1})
        elif change == 'writing_settings':
            store.update_settings({'tailored_writing': not store.settings()['tailored_writing'], 'cover_letters': False})
        elif change == 'cover_letters':
            store.update_settings({'cover_letters': False})
        else:
            store.db.execute("UPDATE documents SET hash='changed' WHERE kind='resume'")

    monkeypatch.setattr('hireme.answers.validate_package', change_during_validation)
    with pytest.raises(Blocked):
        consume()
    if operation == 'draft':
        assert d.remaining == before and not d.pending
    else:
        assert not grant.used and grant.request_signature is None
    assert not store.db.execute("SELECT 1 FROM events WHERE kind IN ('ashby_draft_write','ashby_submission_write')").fetchone()


def test_profile_and_deadline_are_rechecked_at_transport(store, job, package, monkeypatch):
    binding = ['original']
    clock = [10]
    monkeypatch.setattr('hireme.approval_boundary.time.monotonic', lambda: clock[0])
    boundary = ApprovalBoundary(store, job, package, deadline=20, profile_binding=lambda: binding[0])
    binding[0] = 'replacement'
    with pytest.raises(Blocked, match='account_blocked'):
        boundary.check()
    binding[0] = 'original'
    clock[0] = 20
    with pytest.raises(Blocked, match='cycle_timeout'):
        boundary.check()


def test_ashby_post_intent_fails_closed_without_native_grant(store, monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host', lambda host: True)
    browser = Browser(store)
    browser.current_host = 'jobs.ashbyhq.com'
    browser.attempted = True
    writes = []
    data = {'operationName': 'UnreviewedWrite', 'query': 'mutation UnreviewedWrite { submitOtherApplication }', 'variables': {}}
    route = SimpleNamespace(request=SimpleNamespace(method='POST', url='https://jobs.ashbyhq.com/api/non-user-graphql', post_data=json.dumps(data)),
                            abort=lambda: writes.append('blocked'), continue_=lambda: writes.append('sent'))
    browser._route(route)
    assert writes == ['blocked'] and browser.denied_write
    assert not browser._native_submission_acknowledged()


def test_final_source_revalidation_cannot_extend_submit_deadline(store, job, package, monkeypatch):
    _, grant, request, data = submission_fixture(store, job, package)
    store.begin_submit(grant.aid)
    clock = [0]
    checks = [0]
    grant.write_deadline = 10
    monkeypatch.setattr('hireme.ashby_submission.time.monotonic', lambda: clock[0])
    original = grant.boundary.check
    def check():
        result = original()
        checks[0] += 1
        if checks[0] == 3:
            clock[0] = 11
        return result
    grant.boundary.check = check
    assert not grant.allow(request, data)
    assert checks[0] == 3 and not grant.used


def test_query_envelope_cannot_select_a_hidden_mutation(store, monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host', lambda host: True)
    browser = Browser(store)
    browser.current_host = 'jobs.ashbyhq.com'
    writes = []
    data = {'operationName': 'Unsafe', 'query': 'query Read { version } mutation Unsafe { unreviewedWrite }', 'variables': {}}
    route = SimpleNamespace(request=SimpleNamespace(method='POST', url='https://jobs.ashbyhq.com/api/non-user-graphql', post_data=json.dumps(data)),
                            abort=lambda: writes.append('blocked'), continue_=lambda: writes.append('sent'))
    browser._route(route)
    assert writes == ['blocked'] and browser.denied_write


def test_upload_rechecks_after_native_payload_encoding(store, job, package, monkeypatch):
    from tests.test_lever_resume import setup_upload
    browser, route, _ = setup_upload(store, monkeypatch)
    browser.write_boundary = ApprovalBoundary(store, job, package)
    original = browser.lever_resume.grant
    def encode(*args):
        value = original(*args)
        store.update_settings({'live_enabled': False})
        return value
    browser.lever_resume.grant = encode
    browser._route(route)
    assert route.action == 'abort' and browser.denied_write


def test_native_resume_grant_is_one_use(store, monkeypatch):
    from tests.test_lever_resume import setup_upload
    browser, route, _ = setup_upload(store, monkeypatch)
    browser._route(route)
    assert route.action == 'continue'
    route.action = None
    browser._route(route)
    assert route.action == 'abort'


def other_posting(store,job,*,company=None):
    url='https://jobs.ashbyhq.com/synthetic/99999999-9999-4999-8999-999999999999'
    other={**job,'id':digest(url),'url':url,'company':company or job['company']}
    store.upsert_job(other)
    return other


def test_dashboard_other_manual_application_during_validation_revokes_the_final_write(store,job,package,monkeypatch):
    store.update_settings({'max_per_company':1})
    d,grant,r,data=submission_fixture(store,job,package)
    other=other_posting(store,d.job)
    store.begin_submit(grant.aid)
    from hireme.answers import validate_package
    def concurrent_dashboard_decision(*args,**kwargs):
        validate_package(*args,**kwargs)
        store.decide_job(other['id'],'manually_applied')
    monkeypatch.setattr('hireme.answers.validate_package',concurrent_dashboard_decision)
    with pytest.raises(Blocked,match='company_limit'):grant.allow(r,data)
    assert not grant.used and grant.request_signature is None
    assert store.application_record(grant.aid)['state']=='submitting'
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_submission_write'").fetchone()


@pytest.mark.parametrize('state',['prepared','submitting','awaiting_verification'])
def test_late_budget_excludes_only_the_current_known_application_reservation(store,job,package,state):
    store.update_settings({'max_per_company':1})
    d,grant,r,data=submission_fixture(store,job,package)
    if state!='prepared':store.begin_submit(grant.aid)
    if state=='awaiting_verification':store.finish(grant.aid,state,'Synthetic native verification required')
    assert grant.boundary.check()
    if state=='submitting':assert grant.allow(r,data)
    if state=='awaiting_verification':
        other=other_posting(store,d.job)
        with pytest.raises(Blocked,match='company_verification_pending'):
            store._check_budget(other,store.settings())


def test_other_company_manual_application_still_consumes_the_global_daily_budget(store,job,package):
    store.update_settings({'target_per_day':1,'max_per_day':1})
    d,grant,_,_=submission_fixture(store,job,package)
    other=other_posting(store,d.job,company='Different synthetic company')
    store.begin_submit(grant.aid)
    store.decide_job(other['id'],'manually_applied')
    with pytest.raises(Blocked,match='daily_limit'):grant.boundary.check()


@pytest.mark.parametrize('state,reason',[('submitting','company_uncertain'),('unknown','company_uncertain'),('awaiting_verification','company_verification_pending')])
def test_current_reservation_exclusion_never_hides_another_active_same_company_row(store,job,package,state,reason):
    d,grant,_,_=submission_fixture(store,job,package)
    other=other_posting(store,d.job)
    proof={**d.package,'job_id':other['id'],'url':other['url']}
    other_aid=store.prepare(other,proof)
    store.begin_submit(grant.aid)
    # Model another durable reservation/outcome becoming visible during final
    # validation; only our explicit current ID may be excluded from its budget.
    store.db.execute('UPDATE applications SET state=? WHERE id=?',(state,other_aid))
    with pytest.raises(Blocked,match=reason):grant.boundary.check()


@pytest.mark.parametrize('which',['missing','another_job','unknown','confirmed'])
def test_budget_exclusion_rejects_a_wrong_or_no_longer_pending_application(store,job,package,which):
    d,grant,_,_=submission_fixture(store,job,package)
    aid=grant.aid
    if which=='missing':aid='missing-application'
    elif which=='another_job':
        other=other_posting(store,d.job,company='Different synthetic company')
        proof={**d.package,'job_id':other['id'],'url':other['url']}
        aid=store.prepare(other,proof)
    else:
        store.begin_submit(aid)
        store.finish(aid,which,'Synthetic terminal outcome')
    with pytest.raises(Blocked,match='application_changed'):
        store._check_budget(d.job,store.settings(),application_id=aid)
