import json
from pathlib import Path

import pytest

from hireme import opportunity_search
from hireme.store import Store, worker_lock
from hireme.util import Blocked, now


def fixture_sweeps(monkeypatch, action):
    monkeypatch.setattr(opportunity_search, 'sweep_lists', action)
    monkeypatch.setattr(opportunity_search, 'sweep_portals', lambda *args: None)
    monkeypatch.setattr(opportunity_search, 'sweep_boards', lambda *args, **kwargs: None)


def test_find_opportunities_without_onboarding_or_submission(tmp_path, monkeypatch):
    from hireme.discovery import posting
    store = Store(tmp_path / 'private')
    original = store.settings()
    generation = store.control_generation()
    def collect(store, net):
        assert net.checkpoint == store.checkpoint
        store.upsert_job(posting('https://jobs.lever.co/acme/one', 'Acme', 'Engineer', 'US', 'fixture'))
    fixture_sweeps(monkeypatch, collect)
    monkeypatch.setattr('hireme.worker.cycle', lambda *a, **k: pytest.fail('Search cannot apply'))
    monkeypatch.setattr('hireme.browser.Browser.__init__', lambda *a, **k: pytest.fail('Search cannot open browser'))
    result = opportunity_search.find_opportunities(store, Path('.'))
    assert result['added'] == 1 and result['submissions'] == 0
    assert store.settings() == original and store.control_generation() == generation
    assert store.db.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0
    run = store.db.execute('SELECT * FROM runs').fetchone()
    assert run['status'] == 'finished' and run['submitted'] == 0
    assert json.loads(run['detail'])['mode'] == 'discovery'
    store.close()


def test_pause_stops_search_keeps_collected_jobs_and_remains_paused(store, job, monkeypatch):
    store.update_settings({'live_enabled': False})
    def collect(store, net):
        store.update_settings({'live_enabled': False})
        net.checkpoint()
    fixture_sweeps(monkeypatch, collect)
    with pytest.raises(Blocked, match='paused'):
        opportunity_search.find_opportunities(store, Path('.'))
    assert not store.settings()['live_enabled']
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
    assert store.db.execute('SELECT status FROM runs').fetchone()[0] == 'paused'
    assert store.discovery_generation is None


def test_search_respects_shared_lock_and_does_not_recover_history(store, monkeypatch):
    fixture_sweeps(monkeypatch, lambda *a: pytest.fail('Must not collect'))
    with worker_lock(store.root):
        with pytest.raises(Blocked, match='worker_busy'):
            opportunity_search.find_opportunities(store, Path('.'))
    store.db.execute("INSERT INTO runs(id,started,status) VALUES('interrupted',?,'running')", (now(),))
    with pytest.raises(Blocked, match='recovery_needed'):
        opportunity_search.find_opportunities(store, Path('.'))
    assert store.db.execute("SELECT status FROM runs WHERE id='interrupted'").fetchone()[0] == 'running'
    assert store.settings()['live_enabled']


def test_search_error_records_failure_without_changing_submission_settings(store, monkeypatch):
    fixture_sweeps(monkeypatch, lambda *a: (_ for _ in ()).throw(RuntimeError('fixture failed')))
    with pytest.raises(RuntimeError, match='fixture failed'):
        opportunity_search.find_opportunities(store, Path('.'))
    row = store.db.execute('SELECT * FROM runs').fetchone()
    assert row['status'] == 'blocked' and row['finished']
    assert json.loads(row['detail'])['mode'] == 'discovery'
    assert store.settings()['live_enabled'] and store.discovery_generation is None


def test_pause_before_thread_start_or_followed_by_resume_still_cancels_search(store, monkeypatch):
    fixture_sweeps(monkeypatch, lambda *a: pytest.fail('Cancelled search cannot make requests'))
    generation = store.control_generation()
    store.update_settings({'live_enabled': False})
    store.update_settings({'live_enabled': True})
    with pytest.raises(Blocked, match='paused'):
        opportunity_search.find_opportunities(store, Path('.'), requested_generation=generation)
    assert store.settings()['live_enabled']
    assert store.db.execute('SELECT status FROM runs').fetchone()[0] == 'paused'


def test_search_deadline_skips_remaining_sources(store, monkeypatch):
    times = iter([0, 2, 2, 2])
    monkeypatch.setattr(opportunity_search.time, 'monotonic', lambda: next(times))
    fixture_sweeps(monkeypatch, lambda *a: None)
    monkeypatch.setattr(opportunity_search, 'sweep_portals', lambda *a: pytest.fail('Deadline reached'))
    monkeypatch.setattr(opportunity_search, 'sweep_boards', lambda *a, **k: pytest.fail('Deadline reached'))
    result = opportunity_search.find_opportunities(store, Path('.'), deadline_seconds=1)
    assert result['time_limit_reached']


def test_search_summary_counts_latest_result_once_per_source_and_keeps_jobs(store, job, monkeypatch):
    from hireme.discovery import source_result
    def collect(store, net):
        source_result(store, 'fixture:empty', [])
        source_result(store, 'fixture:partial', [job])
        source_result(store, 'fixture:partial', error='Later page failed')
        source_result(store, 'fixture:recovered', error='First attempt failed')
        source_result(store, 'fixture:recovered', [])
    fixture_sweeps(monkeypatch, collect)
    result = opportunity_search.find_opportunities(store, Path('.'))
    assert result['sources_checked'] == 3 and result['sources_failed'] == 1
    assert result['added'] == 0 and store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
    assert store.discovery_results is None
    assert json.loads(store.db.execute('SELECT detail FROM runs').fetchone()[0])['sources_failed'] == 1


def test_stopped_search_summary_retains_completed_source_checks(store, monkeypatch):
    from hireme.discovery import source_result
    def collect(store, net):
        source_result(store, 'fixture:done', [])
        store.update_settings({'live_enabled': False})
        net.checkpoint()
    fixture_sweeps(monkeypatch, collect)
    with pytest.raises(Blocked, match='paused'):
        opportunity_search.find_opportunities(store, Path('.'))
    detail = json.loads(store.db.execute('SELECT detail FROM runs').fetchone()[0])
    assert detail['sources_checked'] == 1 and detail['sources_failed'] == 0
    assert store.discovery_results is None


@pytest.mark.parametrize('deadline', [0, -1, True, 1801, '300'])
def test_search_validates_time_limit(store, deadline):
    with pytest.raises(ValueError):
        opportunity_search.find_opportunities(store, Path('.'), deadline_seconds=deadline)
