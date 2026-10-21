import csv
import io
import json

import pytest

from hireme.backup import create_backup, restore_backup
from hireme.ledger import export_csv, summary
from hireme.outcome_ledger import search_outcomes
from hireme.store import Store, worker_lock
from hireme.util import Blocked, digest, now


def prior(job, state='unknown', *, identity='other-worker'):
    return dict(id=digest(identity),job_id=job['id'],company_key='acme',state=state,
                package='{}',hash=digest('original package'),created=now(),updated=now(),
                attempted=now(),confirmation='Original employer outcome',screenshot=None)


def retain(store, records):
    store.update_settings({'live_enabled':False})
    return store.retain_applications(records,origin='sv-pi:sha256:'+digest('private snapshot'))


def test_parallel_attempts_keep_original_rows_and_block_both_same_job_and_company(store, job, package):
    aid=store.prepare(job,package)
    original=store.application_record(aid)
    old=prior(job)
    assert retain(store,[old])==1
    assert store.application_record(aid)==original
    assert store.application_record(old['id'])==old
    assert len(store.application_history())==2
    store.update_settings({'live_enabled':True})
    with pytest.raises(Blocked,match='duplicate_or_uncertain'):store.begin_submit(aid)
    other={**job,'id':digest('different req'),'url':job['url']+'-2'}
    store.upsert_job(other)
    with pytest.raises(Blocked,match='company_uncertain'):store._check_budget(other,store.settings())
    with pytest.raises(Blocked):store.begin_submit(old['id'])
    with pytest.raises(ValueError):store.decide_job(job['id'],'manually_applied')


def test_retained_reconciliation_does_not_replace_current_receipt_or_allow_retry(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed','Current native receipt')
    current=store.application_record(aid);status=store.db.execute('select status from jobs').fetchone()[0]
    old=prior(job);retain(store,[old])
    assert search_outcomes(store)['applications'][0]['id']==old['id']
    assert any(a['id']==old['id'] for a in store.snapshot()['applications'])
    store.reconcile(old['id'],False,'Official read shows this earlier application was never submitted')
    assert store.application_record(aid)==current
    assert store.db.execute('select status from jobs').fetchone()[0]==status
    event=store.db.execute("select detail from events where kind='historical_application_reconciled'").fetchone()
    assert json.loads(event[0])['previous_application']==old
    with pytest.raises(ValueError):store.retry_not_submitted(old['id'],'Try this historical application again')
    store.update_settings({'live_enabled':True})
    with pytest.raises(Blocked,match='duplicate_or_uncertain'):store.prepare(job,package)


@pytest.mark.parametrize('state,reason',[('confirmed','company_limit'),('unknown','company_uncertain'),('awaiting_verification','company_verification_pending')])
def test_retained_attempts_enforce_limits_after_alias_changes(store,job,state,reason):
    old=prior(job,state);retain(store,[old]);store.update_settings({'max_per_company':1,'company_aliases':{'Acme':'Renamed Acme'}})
    other={**job,'id':digest('another req'),'url':job['url']+'-2','company':'Renamed Acme'};store.upsert_job(other)
    with pytest.raises(Blocked,match=reason):store._check_budget(other,store.settings())


def test_import_requires_idle_pause_and_is_atomic_idempotent_and_identity_safe(store,job):
    old=prior(job)
    with pytest.raises(ValueError,match='Pause'):store.retain_applications([old],origin='saved snapshot')
    store.update_settings({'live_enabled':False})
    with worker_lock(store.root):
        with pytest.raises(Blocked):store.retain_applications([old],origin='saved snapshot')
    with pytest.raises(ValueError):store.retain_applications([old,{**prior(job,identity='bad'),'job_id':'missing'}],origin='saved snapshot')
    assert store.db.execute('select count(*) from archived_applications').fetchone()[0]==0
    assert retain(store,[old])==1
    assert retain(store,[old])==0
    with pytest.raises(ValueError,match='conflicts'):retain(store,[{**old,'confirmation':'changed receipt'}])
    with pytest.raises(ValueError,match='identity'):store.put_facts({'email':'another@candidate.invalid'})


def test_archived_crash_recovery_cannot_resume_verification_or_override_current_job(store,job,package):
    aid=store.prepare(job,package);old=prior(job,'submitting');retain(store,[old])
    store.db.execute('insert into verification_challenges values(?,?,?,?,?,?,?,?)',(old['id'],'ashby',job['url'],'Acme',0,8,'verifying',1))
    store.recover()
    assert store.application_record(old['id'])['state']=='unknown'
    assert store.application_record(aid)['state']=='prepared'
    assert store.db.execute('select state from verification_challenges').fetchone()[0]=='held'


def test_retained_history_survives_backup_and_export_without_leaking_packages(store,job,tmp_path):
    old=prior(job,'confirmed');old['package']=json.dumps({'private':'candidate data'})
    retain(store,[old])
    assert summary(store)['submitted_today']==1
    data=export_csv(store);assert b'candidate data' not in data
    rows=list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    assert len(rows)==1 and rows[0]['Attempted']==old['attempted']
    archive=tmp_path/'history.zip';create_backup(store,archive)
    restored_root=tmp_path/'restored';restore_backup(archive,restored_root);restored=Store(restored_root)
    try:
        assert restored.application_record(old['id'])==old
        assert not restored.settings()['live_enabled']
        assert restored.db.execute('select origin from archived_applications').fetchone()[0].startswith('sv-pi:sha256:')
    finally:restored.close()


def test_historical_preparation_is_never_an_execution_reservation(store,job):
    old=prior(job,'prepared');old['attempted']=None;retain(store,[old])
    with pytest.raises(Blocked,match='application_changed'):
        store._check_budget(job,store.settings(),application_id=old['id'])


def test_import_rejects_ambiguous_timestamp_without_partially_retaining_records(store,job):
    old=prior(job);old['created']='2026-10-03T12:00:00'
    with pytest.raises(ValueError,match='timezone'):retain(store,[old])
    assert not store.application_history()


def test_coverage_counts_jobs_once_and_old_reconciliation_cannot_credit_new_cycle(store,job,package):
    from hireme.coverage import coverage,cycle_funnel
    store.db.execute('insert into runs(id,started,status,detail) values(?,?,?,?)',('current-cycle',now(),'finished','{}'))
    store.event('application_started',job['id'],{'run_id':'current-cycle'})
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed','New native receipt')
    store.event('application_finished',job['id'],{'run_id':'current-cycle','outcome':'confirmed'})
    old=prior(job);retain(store,[old])
    group=next(g for g in coverage(store)['groups'] if g['source']=='test')
    assert (group['discovered'],group['attempted'],group['confirmed'],group['uncertain'])==(1,1,1,1)
    store.reconcile(old['id'],False,'Official account read reconciled the older independent attempt')
    funnel=cycle_funnel(store)
    assert funnel['confirmed']==1 and funnel['not_submitted']==0 and funnel['reconciled']==0
