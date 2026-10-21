"""Commission ownership is durable, bounded and lost on process termination."""
import json
from pathlib import Path
import select
import subprocess
import sys
import uuid

import pytest

from hireme.store import worker_lock
from hireme.util import Blocked, now
from hireme.workday import commission
from hireme.workday_commission_state import BOUND, FINISH, OWNER, START, begin, bind_run, finish
from hireme.worker import cycle


class RecordingBrowser:
    writes = []
    observed_runs = []
    def __init__(self,store):
        self.store=store
    def __enter__(self):
        owner=getattr(self.store,OWNER,None)
        if owner is not None:
            assert owner['run_id']==self.store.active_run_id
            assert self.store.db.execute('SELECT 1 FROM events WHERE kind=?',(BOUND,)).fetchone()
            self.observed_runs.append(owner['run_id'])
        return self
    def __exit__(self,*args):
        return False
    def apply(self,job,live=True):
        # A local stand-in for the employer write boundary; native browser
        # protocol acceptance is covered by its closed integration fixture.
        self.writes.append((job['id'],self.store.active_run_id))
        return 'prepared'


@pytest.fixture
def commissioning(store,job,monkeypatch):
    RecordingBrowser.writes=[]
    RecordingBrowser.observed_runs=[]
    store.update_settings({'remote_drafts':True})
    monkeypatch.setattr('hireme.browser.Browser',RecordingBrowser)
    monkeypatch.setattr('hireme.workday._selected_job',lambda selected,job_id:job if job_id==job['id'] else None)
    monkeypatch.setattr('hireme.worker.eligible',lambda *args:(100,[]))
    return job


def event_details(store,kind):
    return [json.loads(row[0]) for row in store.db.execute('SELECT detail FROM events WHERE kind=? ORDER BY seq',(kind,))]


def bound_start(store,job_id):
    with worker_lock(store.root):
        begin(store,job_id,browser_factory=RecordingBrowser)
    run_id=uuid.uuid4().hex
    store.active_run_id=run_id
    store.db.execute("INSERT INTO runs(id,started,status,detail) VALUES(?,?,'running','{}')",(run_id,now()))
    return run_id


def test_selected_commission_binds_actual_run_before_browser_and_always_pauses(store,commissioning):
    result=commission(store,Path('.'),commissioning['id'])
    run_id=store.db.execute('SELECT id FROM runs').fetchone()[0]
    assert result['attempts']==1
    assert RecordingBrowser.writes==[(commissioning['id'],run_id)]
    assert RecordingBrowser.observed_runs==[run_id]
    assert not store.settings()['live_enabled'] and not hasattr(store,OWNER)
    assert event_details(store,BOUND)==[{'version':1,'job_id':commissioning['id'],'run_id':run_id}]
    terminal,=event_details(store,FINISH)
    assert terminal['state']=='finished' and terminal['run_id']==run_id
    generation=store.control_generation()
    store.recover()
    assert store.control_generation()==generation and len(event_details(store,FINISH))==1


def test_marker_never_persists_raw_process_owner_token(store,commissioning):
    with worker_lock(store.root):
        begin(store,commissioning['id'],browser_factory=RecordingBrowser)
    token=getattr(store,OWNER)['token']
    rows=list(store.db.execute('SELECT subject,detail FROM events WHERE kind=?',(START,)))
    assert len(rows)==1 and token not in rows[0]['subject']+rows[0]['detail']
    store.recover()
    assert store.settings()['live_enabled'] and not event_details(store,FINISH)
    finish(store,state='failed')
    assert not store.settings()['live_enabled'] and not hasattr(store,OWNER)


@pytest.mark.parametrize('change',[
    {'job_ids':{'other-job'}},{'job_ids':None},{'live':False},{'discover':True},
    {'limit':2},{'limit':True},{'max_attempts':2},{'browser_factory':object},
])
def test_changed_dispatch_parameters_pause_before_any_write(store,commissioning,change):
    rid=bound_start(store,commissioning['id'])
    values={'job_ids':{commissioning['id']},'live':True,'discover':False,'limit':1,
            'max_attempts':1,'browser_factory':RecordingBrowser,**change}
    with pytest.raises(Blocked) as raised:
        bind_run(store,rid,**values)
    assert raised.value.reason=='workday_unapproved_operation'
    assert not store.settings()['live_enabled'] and not hasattr(store,OWNER)
    assert event_details(store,FINISH)[0]['reason']=='commission_scope_changed'
    assert not RecordingBrowser.writes


def test_same_store_ordinary_cycle_cannot_reuse_unbound_owner_token(store,commissioning):
    with worker_lock(store.root):
        begin(store,commissioning['id'],browser_factory=RecordingBrowser)
    with pytest.raises(Blocked) as raised:
        cycle(store,Path('.'),discover=False,live=True,browser_factory=RecordingBrowser,limit=1,max_attempts=1)
    assert raised.value.reason=='workday_unapproved_operation'
    assert not store.settings()['live_enabled'] and not RecordingBrowser.writes
    assert len(event_details(store,FINISH))==1


def test_malformed_run_identifier_pauses_without_a_sql_binding_escape(store,commissioning):
    bound_start(store,commissioning['id'])
    with pytest.raises(Blocked):
        bind_run(store,[],job_ids={commissioning['id']},live=True,discover=False,
                 limit=1,max_attempts=1,browser_factory=RecordingBrowser)
    assert not store.settings()['live_enabled'] and event_details(store,FINISH)[0]['state']=='recovered'


def test_owner_pause_resume_cannot_revive_the_commission_fence(store,commissioning):
    rid=bound_start(store,commissioning['id'])
    bind_run(store,rid,job_ids={commissioning['id']},live=True,discover=False,limit=1,max_attempts=1,browser_factory=RecordingBrowser)
    store.update_settings({'live_enabled':False})
    store.update_settings({'live_enabled':True})
    store.recover()
    assert not store.settings()['live_enabled'] and not hasattr(store,OWNER)
    assert event_details(store,FINISH)[0]['state']=='recovered'


def test_setup_failure_closes_fence_and_leaves_no_browser_or_pending_authority(store,commissioning):
    config=json.loads(store.db.execute('SELECT value FROM config WHERE id=1').fetchone()[0])
    config['onboarding_complete']=False
    store.db.execute('UPDATE config SET value=? WHERE id=1',(json.dumps(config),))
    with pytest.raises(Blocked) as raised:
        commission(store,Path('.'),commissioning['id'])
    assert raised.value.reason=='setup_incomplete'
    assert not RecordingBrowser.writes and not store.settings()['live_enabled']
    assert not hasattr(store,OWNER)
    terminal,=event_details(store,FINISH)
    assert terminal['state']=='failed' and terminal['run_id']


def test_cycle_failure_after_fence_start_always_records_terminal_pause(store,commissioning,monkeypatch):
    def failed_cycle(*args,**kwargs):
        raise RuntimeError('private-fixture-diagnostic')
    monkeypatch.setattr('hireme.worker.cycle',failed_cycle)
    with pytest.raises(RuntimeError):
        commission(store,Path('.'),commissioning['id'])
    assert not store.settings()['live_enabled'] and not hasattr(store,OWNER)
    terminal,=event_details(store,FINISH)
    assert terminal=={'version':1,'job_id':commissioning['id'],'run_id':None,'state':'failed','reason':'commission_failed'}
    assert 'private-fixture-diagnostic' not in json.dumps(event_details(store,FINISH))


def test_failure_immediately_after_begin_still_pauses_and_closes_its_own_fence(store,commissioning,monkeypatch):
    def failed_begin(*args,**kwargs):
        begin(*args,**kwargs)
        raise RuntimeError('synthetic-post-commit-setup-failure')
    monkeypatch.setattr('hireme.workday_commission_state.begin',failed_begin)
    with pytest.raises(RuntimeError):
        commission(store,Path('.'),commissioning['id'])
    assert not store.settings()['live_enabled'] and not hasattr(store,OWNER)
    assert event_details(store,FINISH)[0]['state']=='failed'
    assert not RecordingBrowser.writes


def test_sigkill_loses_owner_then_scheduled_cycle_recovers_pause_before_browser(store,commissioning):
    script='''import sys,time,uuid
from pathlib import Path
from hireme.store import Store,worker_lock
from hireme.util import now
from hireme.workday_commission_state import begin,bind_run
class OwnedBrowser:pass
s=Store(Path(sys.argv[1]))
with worker_lock(s.root):
 begin(s,sys.argv[2],browser_factory=OwnedBrowser)
 rid=uuid.uuid4().hex;s.active_run_id=rid
 s.db.execute("INSERT INTO runs(id,started,status,detail) VALUES(?,?,'running','{}')",(rid,now()))
 bind_run(s,rid,job_ids={sys.argv[2]},live=True,discover=False,limit=1,max_attempts=1,browser_factory=OwnedBrowser)
 print('bound',flush=True)
 time.sleep(60)
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(store.root),commissioning['id']],
                             cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        readable,_,_=select.select([process.stdout],[],[],10)
        assert readable and process.stdout.readline().strip()=='bound'
        assert store.settings()['live_enabled']
        # An overlapping explicit command cannot pause or steal a live owner's
        # fence merely because its attempt to acquire the worker lock failed.
        with pytest.raises(Blocked) as raised:
            commission(store,Path('.'),commissioning['id'])
        assert raised.value.reason=='worker_busy' and store.settings()['live_enabled']
        process.kill();process.wait(timeout=5)
        assert not hasattr(store,OWNER)
        with pytest.raises(Blocked) as raised:
            cycle(store,Path('.'),discover=False,live=True,browser_factory=RecordingBrowser,limit=1,max_attempts=1)
        assert raised.value.reason=='paused'
        assert not RecordingBrowser.writes and not store.settings()['live_enabled']
        terminal,=event_details(store,FINISH)
        assert terminal['state']=='recovered' and terminal['run_id'] and terminal['reason']=='commission_owner_lost'
        assert store.db.execute("SELECT count(*) FROM runs WHERE status='interrupted'").fetchone()[0]==1
        generation=store.control_generation();store.recover()
        assert store.control_generation()==generation and len(event_details(store,FINISH))==1
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        process.stdout.close();process.stderr.close()
