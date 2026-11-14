import pytest

from hireme.recovery import worker_status, recover_interrupted
from hireme.store import worker_lock
from hireme.util import Blocked


def interrupted(store, job, package):
    application = store.prepare(job, package); store.begin_submit(application)
    store.db.execute("INSERT INTO runs(id,started,status) VALUES('stale-run','2020-01-01T00:00:00+00:00','running')")
    store.db.execute("INSERT INTO employer_accounts VALUES('stale-account','https://jobs.lever.co','Acme','creating','2020-01-01T00:00:00+00:00')")
    return application


def test_stale_records_are_distinct_from_live_worker_and_recover_without_retry(store, job, package):
    application = interrupted(store, job, package)
    activity = worker_status(store)
    assert not activity['running'] and activity['recovery_needed']
    assert activity['unfinished_submissions'] == activity['unfinished_accounts'] == activity['interrupted_runs'] == 1
    result = recover_interrupted(store)
    assert result['paused'] and not store.settings()['live_enabled']
    assert store.db.execute('SELECT state FROM applications WHERE id=?', (application,)).fetchone()[0] == 'unknown'
    assert store.db.execute("SELECT state FROM employer_accounts WHERE id='stale-account'").fetchone()[0] == 'uncertain'
    assert store.db.execute("SELECT status FROM runs WHERE id='stale-run'").fetchone()[0] == 'interrupted'
    assert not worker_status(store)['recovery_needed']
    assert not store.db.execute("SELECT * FROM events WHERE kind='application_started'").fetchone()


def test_live_worker_lock_blocks_recovery_without_pausing_or_rewriting_intent(store, job, package):
    application = interrupted(store, job, package)
    generation = store.control_generation()
    with worker_lock(store.root):
        assert worker_status(store)['running'] and not worker_status(store)['recovery_needed']
        with pytest.raises(Blocked, match='worker_busy'): recover_interrupted(store)
    assert store.settings()['live_enabled'] and store.control_generation() == generation
    assert store.db.execute('SELECT state FROM applications WHERE id=?', (application,)).fetchone()[0] == 'submitting'


def test_recovery_cli_is_explicit_and_paused(store, job, package, capsys):
    from hireme.cli import main
    interrupted(store, job, package)
    assert main(['--data-dir', str(store.root), 'recover']) == 0
    assert 'Applications remain paused' in capsys.readouterr().out
    assert not store.settings()['live_enabled']


def test_interrupted_draft_is_visible_and_recovery_never_retries_it(store, job, package):
    from hireme.job_holds import annotate, hold, ready, recheck
    store.prepare(job, package)
    store.db.execute("INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)",
        ('draft-intent',job['id'],job['host'],'ashby:attempt','hash','sources','intent','2020','2020',''))
    hold(store,job,'network_error')
    store.block(job['id'],'network_error')
    displayed=annotate(store,[{**job,'status':'blocked'}])[0]['hold']
    assert displayed['reason']=='ashby_draft_uncertain' and displayed['retry_at'] is None
    assert 'cannot currently read back' in displayed['next_action']
    assert recheck(store,job['id'])=={'ready':False,'reason':'ashby_draft_uncertain'}
    assert worker_status(store)['unfinished_drafts']==1
    assert recover_interrupted(store)['unfinished_drafts']==1
    assert store.db.execute("SELECT state FROM remote_draft_steps WHERE id='draft-intent'").fetchone()[0]=='unknown'
    assert not worker_status(store)['recovery_needed'] and not ready(store,job,at=10**9)


@pytest.mark.parametrize('step,reason',[('ashby:attempt','ashby_draft_uncertain'),('application:fixture','remote_draft_uncertain')])
@pytest.mark.parametrize('decision',['skipped','manually_applied'])
def test_recovery_preserves_existing_draft_holds_and_exclusions_without_counting_retries(store,job,package,step,reason,decision):
    from hireme.job_holds import hold,ready
    aid=store.prepare(job,package)
    store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('draft-unknown',job['id'],job['host'],step,'hash','sources','unknown','2020','2020',''))
    store.block(job['id'],reason)
    hold(store,job,reason,stage='recovery')
    store.decide_job(job['id'],decision)
    before=dict(store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone())
    job_before=dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
    app_before=store.application_record(aid)
    for _ in range(3):store.recover()
    assert dict(store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone())==before
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())==job_before
    assert store.application_record(aid)==app_before
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='unknown'
    assert not ready(store,job)


@pytest.mark.parametrize('step,reason',[('ashby:attempt','ashby_draft_uncertain'),('application:fixture','remote_draft_uncertain')])
def test_recovery_repairs_a_missing_hold_even_when_the_job_already_shows_uncertainty(store,job,step,reason):
    store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('draft-unknown',job['id'],job['host'],step,'hash','sources','unknown','2020','2020',''))
    store.block(job['id'],reason)
    assert not store.db.execute('SELECT 1 FROM job_holds').fetchone()
    store.recover()
    saved=dict(store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone())
    assert saved['reason']==reason and saved['retry_at'] is None
    store.recover()
    assert dict(store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone())==saved
