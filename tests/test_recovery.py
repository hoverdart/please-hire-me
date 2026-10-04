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
