from pathlib import Path
import json
import pytest
from hireme.worker import cycle
from hireme.util import Blocked,digest


def test_cycle_budget_is_code_not_prompt(store,job,package):
    for i in range(15):
        j={**job,'id':digest(i),'url':job['url']+str(i),'company':'Company '+str(i)};store.upsert_job(j)
    class FakeBrowser:
        def __init__(self,s):self.s=s
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):
            p={**package,'job_id':j['id'],'url':j['url']}
            aid=self.s.prepare(j,p);self.s.begin_submit(aid);self.s.finish(aid,'confirmed')
            return 'confirmed'
    result=cycle(store,Path('.'),discover=False,limit=7,browser_factory=FakeBrowser)
    assert result['confirmed']==7
    assert store.db.execute("SELECT count(*) FROM applications WHERE state='confirmed'").fetchone()[0]==7


def test_unknown_outcome_distinct_from_blocked(store,job,package):
    class FakeBrowser:
        def __init__(self,s):self.s=s
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):
            aid=self.s.prepare(j,package);self.s.begin_submit(aid);self.s.finish(aid,'unknown');raise Blocked('submission_unknown')
    cycle(store,Path('.'),discover=False,browser_factory=FakeBrowser)
    assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='unknown'


def test_pause_during_application_stops_cycle_and_retains_count(store,job,package):
    class FakeBrowser:
        def __init__(self,s):self.s=s
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):
            self.s.update_settings({'live_enabled':False})
            self.s.checkpoint()
    with pytest.raises(Blocked,match='paused'):
        cycle(store,Path('.'),discover=False,browser_factory=FakeBrowser)
    run=store.db.execute('SELECT * FROM runs').fetchone()
    assert run['status']=='paused' and json.loads(run['detail'])['attempts']==1
    assert not store.settings()['live_enabled']


def test_pause_then_resume_does_not_revive_old_cycle(store):
    store.run_generation=store.control_generation()
    store.update_settings({'live_enabled':False})
    store.update_settings({'live_enabled':True})
    with pytest.raises(Blocked,match='paused'):store.checkpoint()


def test_preparation_is_not_reported_as_confirmed_submission(store,job):
    class FakeBrowser:
        def __init__(self,s):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):assert not live;return 'prepared'
    result=cycle(store,Path('.'),discover=False,live=False,limit=1,browser_factory=FakeBrowser)
    assert result['confirmed']==0 and result['prepared']==1 and result['shortfall']==0
    assert store.db.execute('SELECT submitted FROM runs').fetchone()[0]==0


def test_observed_cycle_has_hard_attempt_limit_and_job_selection(store,job):
    selected=[]
    for i in range(5):
        j={**job,'id':digest(i),'url':job['url']+str(i),'company':'Company '+str(i)}
        store.upsert_job(j)
        selected.append(j['id'])
    visited=[]
    class FakeBrowser:
        def __init__(self,s):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):visited.append(j['id']);raise Blocked('missing_answers')
    result=cycle(store,Path('.'),discover=False,max_attempts=2,job_ids=set(selected[:2]),browser_factory=FakeBrowser)
    assert result['attempts']==2 and set(visited)==set(selected[:2]) and result['confirmed']==0


def test_preparation_can_start_paused_without_enabling_submissions(store, job, package):
    store.update_settings({'live_enabled': False})
    class FakeBrowser:
        def __init__(self, s): self.s = s
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def apply(self, selected, live=True):
            assert not live
            self.s.checkpoint(); self.s.prepare(selected, package)
            return 'prepared'
    result = cycle(store, Path('.'), discover=False, live=False, limit=1, browser_factory=FakeBrowser)
    assert result['prepared'] == 1
    assert not store.settings()['live_enabled'] and store.preparation_generation is None
    row = store.db.execute('SELECT * FROM runs').fetchone()
    assert row['submitted'] == 0 and json.loads(row['detail'])['mode'] == 'prepare'
    assert store.db.execute('SELECT state FROM applications').fetchone()[0] == 'prepared'


@pytest.mark.parametrize('limit', [1, 2])
def test_pause_during_preparation_preserves_prepared_count_and_stops_future_work(store, job, package, limit):
    store.update_settings({'live_enabled': False})
    second = {**job, 'id': digest('second-preparation'), 'url': job['url'] + '-second', 'company': 'Other Company'}
    store.upsert_job(second)
    visited = []
    class FakeBrowser:
        def __init__(self, s): self.s = s
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def apply(self, selected, live=True):
            visited.append(selected['id'])
            self.s.prepare(selected, {**package, 'job_id': selected['id'], 'url': selected['url']})
            self.s.update_settings({'live_enabled': False})
            return 'prepared'
    with pytest.raises(Blocked, match='paused'):
        cycle(store, Path('.'), discover=False, live=False, limit=limit, browser_factory=FakeBrowser)
    row = store.db.execute('SELECT * FROM runs').fetchone(); detail = json.loads(row['detail'])
    assert len(visited) == 1 and row['status'] == 'paused' and row['submitted'] == 0
    assert detail['prepared'] == 1 and detail['confirmed'] == 0
    assert store.db.execute("SELECT COUNT(*) FROM applications WHERE state='prepared'").fetchone()[0] == 1
    assert not store.settings()['live_enabled'] and store.preparation_generation is None


def test_resume_cannot_revive_cancelled_preparation_or_reserve_another_request(store):
    store.preparation_generation = store.control_generation()
    store.update_settings({'live_enabled': False}); store.update_settings({'live_enabled': True})
    with pytest.raises(Blocked, match='paused'): store.reserve_model_request()
    assert store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0


def test_preparation_requested_before_pause_cannot_start_later(store,monkeypatch):
    store.update_settings({'live_enabled':False})
    generation=store.control_generation()
    store.update_settings({'live_enabled':False})
    monkeypatch.setattr('hireme.worker.sweep_lists',lambda s:pytest.fail('Cancelled queued preparation must not discover'))
    with pytest.raises(Blocked,match='paused'):
        cycle(store,Path('.'),live=False,requested_generation=generation)
    row=store.db.execute('SELECT * FROM runs').fetchone()
    assert row['status']=='paused' and row['submitted']==0
    assert store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==0
    assert store.preparation_generation is None and store.run_deadline is None


def test_time_budget_stops_discovery_before_the_next_source(store, monkeypatch):
    clock = [100.0]; visited = []
    monkeypatch.setattr('hireme.worker.time.monotonic', lambda: clock[0])
    store.update_settings({'cycle_timeout_seconds': 1})
    def lists(s):
        visited.append('lists'); clock[0] += 1
    monkeypatch.setattr('hireme.worker.sweep_lists', lists)
    monkeypatch.setattr('hireme.worker.sweep_portals', lambda s: visited.append('portals'))
    monkeypatch.setattr('hireme.worker.sweep_boards', lambda s, r: visited.append('boards'))
    with pytest.raises(Blocked, match='cycle_timeout'):
        cycle(store, Path('.'))
    run = store.db.execute('SELECT * FROM runs').fetchone()
    assert visited == ['lists'] and run['status'] == 'blocked' and run['submitted'] == 0
    assert store.settings()['live_enabled'] and store.run_deadline is None
    assert store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0


@pytest.mark.parametrize('live', [True, False])
def test_time_budget_preserves_completed_work_and_stops_the_next_application(store, job, package, monkeypatch, live):
    clock = [100.0]; visited = []
    monkeypatch.setattr('hireme.worker.time.monotonic', lambda: clock[0])
    store.update_settings({'cycle_timeout_seconds': 1, 'live_enabled': live})
    store.upsert_job({**job, 'id': digest('timed-second'), 'url': job['url'] + '-second', 'company': 'Other Company'})
    class FakeBrowser:
        def __init__(self, s): self.s = s
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def apply(self, selected, live=True):
            visited.append(selected['id'])
            aid = self.s.prepare(selected, {**package, 'job_id': selected['id'], 'url': selected['url']})
            if live:
                self.s.begin_submit(aid); self.s.finish(aid, 'confirmed')
            clock[0] += 1
            return 'confirmed' if live else 'prepared'
    with pytest.raises(Blocked, match='cycle_timeout'):
        cycle(store, Path('.'), discover=False, live=live, limit=2, browser_factory=FakeBrowser)
    run = store.db.execute('SELECT * FROM runs').fetchone(); detail = json.loads(run['detail'])
    assert len(visited) == 1 and run['status'] == 'blocked' and run['submitted'] == int(live)
    assert detail['confirmed' if live else 'prepared'] == 1
    assert store.db.execute('SELECT state FROM applications').fetchone()[0] == ('confirmed' if live else 'prepared')
    assert store.settings()['live_enabled'] == live and store.run_deadline is None
    # The deadline belongs to this run; it must not block later owner actions.
    store.checkpoint()


def test_expired_budget_cannot_reserve_a_model_request_and_pause_has_priority(store, monkeypatch):
    monkeypatch.setattr('hireme.store.time.monotonic', lambda: 10.0)
    store.run_deadline = 10.0
    with pytest.raises(Blocked, match='cycle_timeout'): store.reserve_model_request()
    assert store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0] == 0
    store.run_generation = store.control_generation()
    store.update_settings({'live_enabled': False})
    with pytest.raises(Blocked, match='paused'): store.checkpoint()
