from pathlib import Path
import json
import pytest
from hireme.job_holds import ELIGIBILITY_INPUTS,hold,ready
from hireme.presentation import NOT_MATCH_REASONS
from hireme.util import Blocked,digest
from hireme.worker import cycle


def test_all_eligibility_reasons_declare_dependencies():
    assert set(ELIGIBILITY_INPUTS)==NOT_MATCH_REASONS


@pytest.mark.parametrize('reason',sorted(NOT_MATCH_REASONS))
def test_unrelated_phone_edit_does_not_release_eligibility_hold(store,job,reason):
    hold(store,job,reason)
    assert not ready(store,job)
    store.put_facts({'phone':'5557654321','pronouns':'he/him'})
    assert not ready(store,job)


@pytest.mark.parametrize('reason,facts,settings',[
    ('location_mismatch',{}, {'locations':['United States']}),
    ('location_mismatch',{'summer_2027_relocate':'Yes'},{}),
    ('graduation_mismatch',{'graduation':'2029-05'},{}),
    ('experience_mismatch',{'professional_years':'2'},{}),
    ('sponsorship_mismatch',{'needs_sponsorship':'Yes'},{}),
    ('citizenship_mismatch',{'us_person':'No'},{}),
    ('start_window_mismatch',{'earliest_start':'2027-06'},{}),
    ('low_fit',{'skills':'Python, TypeScript, Go'},{}),
    ('role_mismatch',{}, {'roles':['backend engineer']}),
    ('internship_out_of_scope',{}, {'seniority':['new-grad']}),
    ('fulltime_out_of_scope',{}, {'seniority':['internship']}),
    ('company_blocked',{}, {'skip_companies':['Acme']}),
    ('compensation_mismatch',{}, {'min_annual_usd':120000}),
])
def test_relevant_edit_releases_for_revalidation(store,job,reason,facts,settings):
    hold(store,job,reason)
    if facts:store.put_facts(facts)
    if settings:store.update_settings(settings)
    assert ready(store,job)


def test_same_fact_value_and_unrelated_mapping_version_keep_exclusion(store,job,monkeypatch):
    hold(store,job,'graduation_mismatch')
    store.put_facts({'graduation':'2028-05'})
    monkeypatch.setattr('hireme.job_holds.MAPPING_VERSION',999)
    assert not ready(store,job)
    from hireme.policy import ELIGIBILITY_VERSION
    monkeypatch.setattr('hireme.policy.ELIGIBILITY_VERSION',ELIGIBILITY_VERSION+1)
    assert ready(store,job)


@pytest.mark.parametrize('change',[{'min_years':3},{'compensation':{'currency':'USD','period':'year','min':130000}}])
def test_structured_posting_constraints_release_eligibility_hold(store,job,change):
    hold(store,job,'experience_mismatch')
    store.upsert_job({**job,**change})
    assert ready(store,job)


def test_posting_change_releases_but_uncertainty_still_excludes(store,job,package):
    hold(store,job,'graduation_mismatch')
    store.upsert_job({**job,'description':'Updated graduation requirement'})
    assert ready(store,job)
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.put_facts({'graduation':'2029-05'})
    assert not ready(store,job)


def test_legacy_eligibility_fingerprint_gets_one_policy_recheck(store,job):
    hold(store,job,'location_mismatch')
    store.db.execute("UPDATE job_holds SET dependency='legacy-fingerprint' WHERE job_id=?",(job['id'],))
    assert ready(store,job)
    hold(store,job,'location_mismatch')
    assert not ready(store,job)


def test_large_excluded_queue_groups_writes_and_phone_edit_consumes_no_attempts(store):
    store.update_settings({'locations':['San Francisco Bay Area']})
    for index in range(240):
        url=f'https://jobs.lever.co/synthetic/excluded-{index}'
        store.upsert_job({'id':digest(url),'url':url,'host':'jobs.lever.co','company':'Synthetic',
            'title':'Software Engineer Intern','location':'London, United Kingdom','description':'Build Python software','source':'fixture'})
    class ForbiddenBrowser:
        def __init__(self,*args):raise AssertionError('An excluded job reached the browser')
    statements=[];screening_transactions=[]
    def trace(sql):
        statements.append(sql)
        if sql.startswith('INSERT INTO events') and "'job_screening_blocked'" in sql:
            screening_transactions.append(store.db.in_transaction)
    store.db.set_trace_callback(trace)
    result=cycle(store,Path.cwd(),discover=False,browser_factory=ForbiddenBrowser)
    store.db.set_trace_callback(None)
    assert result['attempts']==0 and result['eligible']==0
    assert store.db.execute('SELECT count(*) FROM job_holds').fetchone()[0]==240
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='job_screening_blocked'").fetchone()[0]==240
    assert len(screening_transactions)==240 and all(screening_transactions)
    assert 1<=sum(sql=='BEGIN IMMEDIATE' for sql in statements)<240
    store.put_facts({'phone':'5557654321'})
    second=cycle(store,Path.cwd(),discover=False,browser_factory=ForbiddenBrowser)
    assert second['attempts']==0 and second['eligible']==0 and not second['reasons']
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_browser_and_model_reservation_stay_outside_screening_transactions(store,job):
    class PreparedBrowser:
        def __init__(self,s):self.store=s
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,posting,live=True):
            assert not self.store.db.in_transaction and not live
            self.store.reserve_model_request()
            return 'prepared'
    result=cycle(store,Path.cwd(),discover=False,live=False,browser_factory=PreparedBrowser,limit=1)
    assert result['attempts']==1 and result['prepared']==1
    assert store.db.execute('SELECT count(*) FROM model_requests').fetchone()[0]==1


def test_pause_between_screening_batches_preserves_committed_decisions(store,monkeypatch):
    from contextlib import contextmanager
    for index in range(220):
        url=f'https://jobs.lever.co/synthetic/pause-{index}'
        store.upsert_job({'id':digest(url),'url':url,'host':'jobs.lever.co','company':'Synthetic',
            'title':'Software Engineer Intern','location':'London, United Kingdom','description':'Build Python software','source':'fixture'})
    original=store.transaction;paused=False
    @contextmanager
    def transaction():
        nonlocal paused
        with original():yield
        count=store.db.execute("SELECT count(*) FROM events WHERE kind='job_screening_blocked'").fetchone()[0]
        if not paused and count>=100:
            paused=True
            store.update_settings({'live_enabled':False})
    monkeypatch.setattr(store,'transaction',transaction)
    class ForbiddenBrowser:
        def __init__(self,*args):raise AssertionError('Cancelled screening reached a browser')
    with pytest.raises(Blocked,match='paused'):
        cycle(store,Path.cwd(),discover=False,browser_factory=ForbiddenBrowser)
    assert not store.settings()['live_enabled']
    count=store.db.execute('SELECT count(*) FROM job_holds').fetchone()[0]
    assert 100<=count<220
    run=store.db.execute('SELECT status,detail FROM runs').fetchone()
    assert run['status']=='paused' and json.loads(run['detail'])['attempts']==0
