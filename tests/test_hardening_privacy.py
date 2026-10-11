"""Failure telemetry and batch mail must not retain browser exception payloads."""
import json
from pathlib import Path

import pytest

from hireme.util import digest
from hireme.worker import cycle


SECRET = 'fixture-password-and-signed-upload-token'


def persisted_diagnostics(store):
    return '\n'.join(str(tuple(row)) for table in ('events', 'runs', 'report_outbox', 'job_holds')
                     for row in store.db.execute('SELECT * FROM ' + table))


def test_browser_exception_is_private_and_next_application_continues(store, job):
    other = {**job, 'id': digest('privacy-second'), 'company': 'Second Company', 'url': job['url'] + '-second'}
    store.upsert_job(other)
    visited = []

    class FailingBrowser:
        def __init__(self, store): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def apply(self, selected, live=True):
            visited.append(selected['id'])
            raise RuntimeError('Locator.fill("' + SECRET + '") call log')

    result = cycle(store, Path('.'), discover=False, live=False, browser_factory=FailingBrowser)
    assert set(visited) == {job['id'], other['id']}
    assert result['attempts'] == 2 and result['reasons'] == {'browser_error': 2}
    diagnostics = persisted_diagnostics(store)
    assert SECRET not in diagnostics and 'Locator.fill' not in diagnostics
    assert 'RuntimeError' in diagnostics
    events = [json.loads(row[0]) for row in store.db.execute(
        "SELECT detail FROM events WHERE kind='application_finished'")]
    assert all(event['outcome'] == 'error' and event['error'] == 'RuntimeError' for event in events)
    assert store.db.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0


def test_discovery_exception_does_not_enter_run_history_or_batch_email(store, monkeypatch):
    def fail(*args): raise RuntimeError('Authorization: Bearer ' + SECRET)
    monkeypatch.setattr('hireme.worker.sweep_lists', fail)
    with pytest.raises(RuntimeError, match=SECRET):
        cycle(store, Path('.'), live=False)
    run = store.db.execute('SELECT * FROM runs').fetchone()
    assert run['status'] == 'blocked' and json.loads(run['detail'])['reason'] == 'RuntimeError'
    report = store.db.execute('SELECT body FROM report_outbox').fetchone()[0]
    assert 'Stopped because: RuntimeError' in report
    assert SECRET not in persisted_diagnostics(store)


def test_browser_startup_exception_does_not_leak_into_persisted_history(store, job):
    class FailingBrowser:
        def __init__(self, store): pass
        def __enter__(self): raise RuntimeError(SECRET)
        def __exit__(self, *args): pass
    with pytest.raises(RuntimeError, match=SECRET):
        cycle(store, Path('.'), discover=False, live=False, browser_factory=FailingBrowser)
    assert SECRET not in persisted_diagnostics(store)
