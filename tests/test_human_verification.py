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
