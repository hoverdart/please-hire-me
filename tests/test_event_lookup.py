from hireme.accounts import account_key
from hireme.job_holds import hold, ready
from hireme.store import Store


QUERY = "SELECT seq FROM events WHERE kind='account_credentials_saved' AND subject=? ORDER BY seq DESC LIMIT 1"


def bounded_lookup(store, key):
    # Bound SQLite work rather than wall-clock timing. An unrelated event
    # history must not be walked for each held employer account.
    steps = 0
    def progress():
        nonlocal steps
        steps += 1000
        return steps >= 5000
    store.db.set_progress_handler(progress, 1000)
    try:
        return store.db.execute(QUERY, (key,)).fetchone()
    finally:
        store.db.set_progress_handler(None, 0)


def test_account_revision_lookup_is_bounded_and_keeps_employer_scope(store, job):
    key = account_key('https://' + job['host'], store.company(job['company']))
    store.event('account_credentials_saved', key, {'revision': 'first'})
    first = bounded_lookup(store, key)['seq']
    hold(store, job, 'account_credentials_unavailable')
    with store.transaction():
        store.db.executemany('INSERT INTO events(timestamp,kind,subject,detail) VALUES(?,?,?,?)',
            [('2026-10-04T00:00:00+00:00', 'job_held', 'unrelated-employer', '{}')] * 10000)
    store.event('account_credentials_saved', 'another-account', {})
    store.event('job_held', key, {})
    assert bounded_lookup(store, key)['seq'] == first
    assert bounded_lookup(store, 'missing-account') is None
    assert not ready(store, job)
    store.event('account_credentials_saved', key, {'revision': 'second'})
    assert bounded_lookup(store, key)['seq'] > first
    assert ready(store, job)


def test_existing_ledger_gains_event_lookup_without_changing_history(store):
    store.event('account_credentials_saved', 'fixture-account', {})
    with store.transaction():
        store.db.executemany('INSERT INTO events(timestamp,kind,subject,detail) VALUES(?,?,?,?)',
            [('2026-10-04T00:00:00+00:00', 'job_held', 'unrelated-employer', '{}')] * 10000)
    history = [tuple(row) for row in store.db.execute('SELECT * FROM events ORDER BY seq')]
    facts, settings = store.facts(), store.settings()
    store.db.execute('DROP INDEX events_kind_subject_seq')
    upgraded = Store(store.root)
    try:
        assert bounded_lookup(upgraded, 'fixture-account')
        assert bounded_lookup(upgraded, 'missing-account') is None
        assert [tuple(row) for row in upgraded.db.execute('SELECT * FROM events ORDER BY seq')] == history
        assert upgraded.facts() == facts and upgraded.settings() == settings
    finally:
        upgraded.close()
