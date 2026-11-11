import json
import time

import pytest

from hireme.config import validate_settings
from hireme.human_verification import current_wait
from hireme.store import worker_lock
from hireme.util import now


@pytest.mark.parametrize('seconds',[-1,601,True,1.5,'300',None])
def test_human_wait_requires_a_bounded_integer(seconds):
    with pytest.raises(ValueError):validate_settings({'human_verification_wait_seconds':seconds})


def test_human_wait_is_off_by_default_and_requires_a_visible_browser():
    assert validate_settings({})['human_verification_wait_seconds']==0
    assert validate_settings({'headless':False,'human_verification_wait_seconds':600})['human_verification_wait_seconds']==600
    with pytest.raises(ValueError,match='background'):
        validate_settings({'headless':True,'human_verification_wait_seconds':60})


def waiting_application(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid)
    store.db.execute("INSERT INTO runs(id,started,status,detail) VALUES(?,?,'running','{}')",('fixture-run',now()))
    detail={'phase':'challenge','run_id':'fixture-run','job_id':job['id'],'expires_at':time.time()+60}
    store.event('human_verification',aid,detail)
    return aid,detail


@pytest.fixture
def running_worker(store):
    with worker_lock(store.root):yield


def test_dashboard_wait_identifies_only_the_current_existing_attempt(store,job,package,running_worker):
    aid,detail=waiting_application(store,job,package)
    actual=current_wait(store)
    assert actual=={'application_id':aid,'job_id':job['id'],'company':job['company'],'title':job['title'],
                    'phase':'challenge','expires_at':detail['expires_at']}
    store.event('human_verification',aid,{**detail,'phase':'checking_receipt'})
    assert current_wait(store)['phase']=='checking_receipt'
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]=='submitting'


def test_wait_requires_a_live_worker_lock_and_counts_the_current_action_once(store,job,package):
    from hireme.ledger import summary
    aid,detail=waiting_application(store,job,package)
    assert current_wait(store) is None
    with worker_lock(store.root):
        assert summary(store)['attention_count']==1
        assert summary(store)['status_counts'].get('discovered',0)==0
        assert summary(store)['status_counts']['submitting']==1
        store.event('human_verification',aid,{**detail,'phase':'checking_receipt'})
        assert summary(store)['attention_count']==0
        store.finish(aid,'unknown','Synthetic unverified receipt')
        assert current_wait(store) is None and summary(store)['attention_count']==1


@pytest.mark.parametrize('state',['prepared','submitting','unknown','confirmed','awaiting_verification','not_submitted'])
def test_desktop_recheck_can_retry_preflight_but_never_an_existing_attempt(store,job,package,state):
    from hireme.job_holds import hold,recheck
    hold(store,job,'human_verification_unavailable')
    aid=store.prepare(job,package)
    if state!='prepared':
        store.begin_submit(aid)
        if state!='submitting':store.finish(aid,state,'Synthetic outcome')
    if state=='prepared':
        assert recheck(store,job['id'])=={'ready':True,'reason':''}
        assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]=='prepared'
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    else:
        with pytest.raises(ValueError,match='existing submission'):recheck(store,job['id'])
        assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]==state


@pytest.mark.parametrize('change',['expired','unbounded','boolean','not_finite','finished','run','job','unknown','paused','stopped','malformed','not_an_object'])
def test_dashboard_never_offers_a_stale_or_unbound_human_wait(store,job,package,running_worker,change):
    aid,detail=waiting_application(store,job,package)
    if change=='expired':detail['expires_at']=time.time()-1
    elif change=='unbounded':detail['expires_at']=time.time()+3600
    elif change=='boolean':detail['expires_at']=True
    elif change=='not_finite':detail['expires_at']=float('nan')
    elif change=='finished':detail['phase']='finished'
    elif change=='run':detail['run_id']='other-run'
    elif change=='job':detail['job_id']='other-job'
    elif change=='unknown':store.finish(aid,'unknown','Synthetic uncertain outcome')
    elif change=='paused':store.update_settings({'live_enabled':False})
    elif change=='stopped':store.db.execute("UPDATE runs SET status='finished'")
    elif change=='malformed':store.db.execute("UPDATE events SET detail='{' WHERE kind='human_verification'")
    else:store.db.execute("UPDATE events SET detail='[]' WHERE kind='human_verification'")
    if change not in {'unknown','paused','stopped','malformed','not_an_object'}:store.event('human_verification',aid,detail)
    assert current_wait(store) is None


def test_disappearing_challenge_keeps_human_deadline_until_native_dispatch(store,job,monkeypatch):
    from types import SimpleNamespace
    from hireme.browser import Browser
    from hireme.util import Blocked
    store.update_settings({'human_verification_handoff':False})
    b=Browser(store);b.aid='synthetic-wait';b.human_verification_seconds=180
    elapsed=[0.0];checks=[];grant=SimpleNamespace(used=False,boundary=SimpleNamespace(check=lambda:None),failed=False)
    b.native_submission=grant
    def guard(*args,**kwargs):
        checks.append(elapsed[0])
        if elapsed[0]==0:raise Blocked('captcha_blocked')
        if elapsed[0]>=90:
            grant.used=True
            return 'Thank you for applying! Your application has been received.'
        return 'Application form'
    b._guard=guard
    b._native_submission_acknowledged=lambda:grant.used
    b.page=SimpleNamespace(wait_for_timeout=lambda ms:elapsed.__setitem__(0,elapsed[0]+ms/1000),
        locator=lambda _:SimpleNamespace(count=lambda:0))
    monkeypatch.setattr('hireme.browser.time.monotonic',lambda:elapsed[0])
    result=b._wait_submission_outcome(job)
    assert 'received' in result and elapsed[0]>=90
    phases=[json.loads(r[0])['phase'] for r in store.db.execute("SELECT detail FROM events WHERE subject='synthetic-wait' AND kind='human_verification' ORDER BY seq")]
    assert phases==['challenge','checking_receipt','finished']


def test_disappearing_challenge_does_not_extend_human_deadline(store,job,monkeypatch):
    from types import SimpleNamespace
    from hireme.browser import Browser
    from hireme.util import Blocked
    store.update_settings({'human_verification_handoff':False})
    b=Browser(store);b.aid='synthetic-timeout';b.human_verification_seconds=120
    elapsed=[0.0];grant=SimpleNamespace(used=False,boundary=SimpleNamespace(check=lambda:None),failed=False)
    b.native_submission=grant
    def guard(*args,**kwargs):
        if elapsed[0]==0:raise Blocked('captcha_blocked')
        return 'Application form'
    b._guard=guard
    b.page=SimpleNamespace(wait_for_timeout=lambda ms:elapsed.__setitem__(0,elapsed[0]+ms/1000))
    monkeypatch.setattr('hireme.browser.time.monotonic',lambda:elapsed[0])
    with pytest.raises(Blocked,match='captcha_blocked'):b._wait_submission_outcome(job)
    assert elapsed[0]==120 and not grant.used


@pytest.mark.parametrize('used,keys,response,expected',[(False,set(),False,True),(True,set(),False,False),(False,{'request'},False,False),(False,set(),True,False)])
def test_only_live_undispatched_lever_controller_can_prove_no_submission(store,used,keys,response,expected):
    from types import SimpleNamespace
    from hireme.browser import Browser
    from hireme.lever_submission import LeverSubmission
    b=Browser(store);b.aid='fixture-proof'
    grant=object.__new__(LeverSubmission);grant.aid=b.aid;grant.used=used;grant.request_keys=keys;grant.response_seen=response
    grant.deadline=100;grant.boundary=SimpleNamespace(deadline=100);b.native_submission=grant
    assert b._close_undispatched_lever_submit() is expected
    assert (grant.boundary.deadline==0) is expected
    assert bool(store.db.execute("SELECT 1 FROM events WHERE kind='native_submission_not_dispatched'").fetchone()) is expected
