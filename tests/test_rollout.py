from pathlib import Path
import pytest
from hireme.rollout import commission,check
from hireme.util import Blocked,digest
from hireme.worker import cycle


def test_dry_run_never_dispatches_even_if_caller_requests_live(store,job):
    store.update_settings({'allowed_application_ats':['lever','ashby']})
    commission(store,'dry_run')
    class Reader:
        def __init__(self,s):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):assert live is False;return 'prepared'
    result=cycle(store,Path('.'),discover=False,limit=1,browser_factory=Reader)
    assert result['mode']=='prepare' and result['confirmed']==0
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_controlled_run_and_code_pin(store,job,monkeypatch):
    store.update_settings({'allowed_application_ats':['lever','ashby']})
    commission(store,'controlled')
    with pytest.raises(Blocked,match='rollout_controlled'):check(store,live=True,job_ids=[])
    assert check(store,live=True,job_ids=[job['id']])
    monkeypatch.setattr('hireme.rollout.code_fingerprint',lambda:'changed')
    with pytest.raises(Blocked,match='rollout_stale'):check(store,live=True,job_ids=[job['id']])


@pytest.mark.parametrize('stage',['dry_run','controlled','restricted'])
def test_paused_scheduled_run_does_not_require_current_rollout_authority(store,job,monkeypatch,stage):
    import json
    store.update_settings({'allowed_application_ats':['lever','ashby'],'rollout_stage':stage,'live_enabled':False})
    def forbidden(*args,**kwargs):pytest.fail('A paused schedule tried discovery, inference or browser work')
    monkeypatch.setattr('hireme.worker.sweep_lists',forbidden)
    monkeypatch.setattr('hireme.worker.sweep_portals',forbidden)
    monkeypatch.setattr('hireme.worker.sweep_boards',forbidden)
    with pytest.raises(Blocked,match='^paused$'):
        cycle(store,Path('.'),browser_factory=forbidden)
    run=store.db.execute('SELECT * FROM runs ORDER BY started DESC LIMIT 1').fetchone()
    assert run['status']=='paused' and run['submitted']==0
    assert json.loads(run['detail'])['attempts']==0
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='rollout_commissioned'").fetchone()
    # Pausing never repairs or waives the evidence required for a live run.
    store.update_settings({'live_enabled':True})
    with pytest.raises(Blocked,match='rollout_stale'):
        cycle(store,Path('.'),browser_factory=forbidden)


def test_valid_dry_run_cannot_turn_a_paused_scheduled_run_into_preparation(store,job):
    store.update_settings({'allowed_application_ats':['lever','ashby']})
    commission(store,'dry_run');store.update_settings({'live_enabled':False})
    class Forbidden:
        def __init__(self,*args):pytest.fail('A normal paused schedule must not prepare applications')
    with pytest.raises(Blocked,match='^paused$'):
        cycle(store,Path('.'),discover=False,browser_factory=Forbidden)
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_explicit_preparation_while_paused_still_requires_a_current_rollout_pin(store,job,monkeypatch):
    store.update_settings({'allowed_application_ats':['lever','ashby']})
    commission(store,'dry_run');store.update_settings({'live_enabled':False})
    class Reader:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,item,live=True):assert live is False;return 'prepared'
    result=cycle(store,Path('.'),discover=False,live=False,limit=1,browser_factory=Reader)
    assert result['mode']=='prepare' and result['prepared']==1 and result['confirmed']==0
    monkeypatch.setattr('hireme.rollout.code_fingerprint',lambda:'changed')
    with pytest.raises(Blocked,match='rollout_stale'):
        cycle(store,Path('.'),discover=False,live=False,limit=1,browser_factory=Reader)


def test_paused_cli_returns_success_even_when_its_rollout_pin_is_stale(store,capsys):
    from hireme.cli import main
    store.update_settings({'allowed_application_ats':['lever','ashby'],'rollout_stage':'dry_run','live_enabled':False})
    assert main(['--data-dir',str(store.root),'run','--no-discovery'])==0
    assert capsys.readouterr().err.strip()=='paused'


def test_resume_during_paused_start_cannot_bypass_a_stale_rollout_pin(store,monkeypatch):
    store.update_settings({'allowed_application_ats':['lever','ashby'],'rollout_stage':'controlled','live_enabled':False})
    recover=store.recover
    def resume_during_recovery():
        recover();store.update_settings({'live_enabled':True})
    monkeypatch.setattr(store,'recover',resume_during_recovery)
    class Forbidden:
        def __init__(self,*args):pytest.fail('Resuming raced past rollout validation')
    with pytest.raises(Blocked,match='^paused$'):
        cycle(store,Path('.'),discover=False,browser_factory=Forbidden)
    assert store.settings()['live_enabled'] is True
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    with pytest.raises(Blocked,match='rollout_stale'):
        cycle(store,Path('.'),discover=False,browser_factory=Forbidden)


def test_restricted_rollout_rejects_local_confirmed_flag_without_native_proof(store,job,package):
    store.update_settings({'allowed_application_ats':['lever','ashby']})
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed','Synthetic button click')
    with pytest.raises(ValueError,match='verified native'):commission(store,'restricted')


def test_uncertain_outcome_pauses_before_next_role(store,job,package):
    store.update_settings({'stop_on_uncertain_outcome':True,'uncertain_application_scope':'posting'})
    other={**job,'id':digest('other'),'url':job['url']+'-other'};store.upsert_job(other)
    visited=[]
    class Browser:
        def __init__(self,s):self.s=s
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def apply(self,j,live=True):
            visited.append(j['id']);p={**package,'job_id':j['id'],'url':j['url']}
            aid=self.s.prepare(j,p);self.s.begin_submit(aid);self.s.finish(aid,'unknown');return 'unknown'
    with pytest.raises(Blocked,match='uncertain_outcome_stop'):
        cycle(store,Path('.'),discover=False,browser_factory=Browser)
    assert len(visited)==1 and not store.settings()['live_enabled']


def test_queue_review_keeps_unknown_attempt_and_uncertain_draft(store,job,package,monkeypatch):
    from hireme.queue_review import revalidate
    from hireme.job_holds import hold
    hold(store,job,'missing_answers')
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    store.db.execute("UPDATE jobs SET status='blocked' WHERE id=?",(job['id'],))
    monkeypatch.setattr('hireme.posting_inspection.inspect',lambda *a:pytest.fail('Attempted posting was reopened'))
    report=revalidate(store)
    assert report['counts']=={'duplicate_or_uncertain':1}
    assert store.application_record(aid)['state']=='unknown'


def test_queue_review_refreshes_then_requires_form_preparation(store,job,monkeypatch):
    from hireme.queue_review import revalidate
    from hireme.job_holds import hold
    hold(store,job,'missing_answers');seen=[]
    def inspect(s,j):seen.append(j['id']);return {**j,'description':j['description']+' New employer text.'}
    monkeypatch.setattr('hireme.posting_inspection.inspect',inspect)
    report=revalidate(store)
    assert seen==[job['id']] and report['forms_pending']==1
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM job_holds').fetchone()
