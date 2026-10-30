import time
import json
import hashlib
from types import SimpleNamespace

import pytest

from hireme.captcha_handoff import Handoff, request, _image
from hireme.captcha_solver import FixtureSolver, FixtureChallenge
from hireme.util import Blocked


@pytest.fixture
def handoff(store,monkeypatch):
    monkeypatch.setattr('hireme.backup_agent._notify',lambda:None)
    monkeypatch.setattr('hireme.human_verification.current_wait',lambda _:{'phase':'challenge','application_id':'app'})
    h=Handoff(SimpleNamespace(store=store,aid='app'),time.time()+60)
    store.db.execute('UPDATE captcha_handoffs SET result=?',(json.dumps({'sequence':0,'revision':'fixture-revision','box':{'x':10,'y':10,'width':100,'height':100}}),))
    yield h
    h.close()


def test_single_authenticated_controller_with_disconnect_and_no_reclaim(store,handoff):
    claim=request(store,{'action':'claim'})
    with pytest.raises(ValueError):request(store,{'action':'claim'})
    with pytest.raises(ValueError):request(store,{'action':'heartbeat','controller':'foreign'})
    token=claim['controller']
    assert request(store,{'action':'heartbeat','controller':token})['active']
    assert token not in str(tuple(store.db.execute('SELECT * FROM captcha_handoffs').fetchone()))
    request(store,{'action':'disconnect','controller':token})
    with pytest.raises(Blocked):handoff.pump(False)
    with pytest.raises(ValueError):request(store,{'action':'claim'})


def test_expired_lease_and_dispatched_final_can_never_control_or_reopen(store,handoff):
    token=request(store,{'action':'claim'})['controller']
    store.db.execute('UPDATE captcha_handoffs SET heartbeat=?',(time.time()-21,))
    with pytest.raises(ValueError):request(store,{'action':'heartbeat','controller':token})
    with pytest.raises(Blocked):handoff.pump(False)
    handoff.pump(True)
    with pytest.raises(ValueError):request(store,{'action':'claim'})


def test_not_ready_frames_still_commit_heartbeats(store,handoff,monkeypatch):
    clock=[time.time()]
    monkeypatch.setattr('hireme.captcha_handoff.time.time',lambda:clock[0])
    token=request(store,{'action':'claim'})['controller']
    clock[0]+=19
    assert request(store,{'action':'frame','controller':token})['ready'] is False
    clock[0]+=19
    assert request(store,{'action':'frame','controller':token})['ready'] is False


def test_a_stale_client_revision_refreshes_without_disconnect_or_consuming_sequence(store,handoff):
    token=request(store,{'action':'claim'})['controller']
    reply=request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':1,'frame_revision':'old-image'})
    assert reply['stale_click'] and not reply['accepted']
    assert request(store,{'action':'heartbeat','controller':token})['active']
    row=store.db.execute('SELECT state,command,result FROM captcha_handoffs').fetchone()
    assert row['state']=='waiting' and row['command']=='' and json.loads(row['result'])['sequence']==0


def test_hcaptcha_prefers_challenge_over_checkbox_and_stale_click_is_not_executed(store,handoff):
    actions=[];box={'x':10,'y':10,'width':100,'height':100}
    def frame(kind):
        return SimpleNamespace(is_visible=lambda:True,get_attribute=lambda _:f'https://hcaptcha.invalid/hcaptcha.html#frame={kind}',
            bounding_box=lambda:box,screenshot=lambda **kw:kind.encode())
    frames=[frame('checkbox'),frame('challenge')]
    handoff.browser.page=SimpleNamespace(locator=lambda _:SimpleNamespace(count=lambda:2,nth=lambda i:frames[i]),
        mouse=SimpleNamespace(click=lambda *xy:actions.append(xy)))
    token=request(store,{'action':'claim'})['controller']
    request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':1,'frame_revision':'fixture-revision'})
    handoff.pump(False)
    assert not actions
    actual=request(store,{'action':'frame','controller':token})
    assert actual['stale_click'] and actual['frame_revision']==hashlib.sha256(b'challenge').hexdigest()
    request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':2,'frame_revision':actual['frame_revision']})
    handoff.pump(False)
    assert len(actions)==1


def test_interaction_replay_bounds_and_queued_command(store,handoff):
    token=request(store,{'action':'claim'})['controller']
    for value in [-1,0,1,float('nan')]:
        with pytest.raises(ValueError):request(store,{'action':'click','controller':token,'x':value,'y':0.5,'sequence':1,'frame_revision':'fixture-revision'})
    request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':1,'frame_revision':'fixture-revision'})
    with pytest.raises(ValueError):request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':1,'frame_revision':'fixture-revision'})
    store.db.execute("UPDATE captcha_handoffs SET command=''")
    with pytest.raises(ValueError):request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':1,'frame_revision':'fixture-revision'})
    handoff.pump(True)
    assert store.db.execute('SELECT command FROM captcha_handoffs').fetchone()[0]==''


def test_final_dispatch_during_box_read_is_deferred_and_cannot_receive_stale_click(store,handoff):
    from hireme.browser import Browser
    b=Browser(store);b.aid='app';b.attempted=True;b.current_host='job-boards.greenhouse.io'
    grant=SimpleNamespace(used=False,boundary=SimpleNamespace(check=lambda:None));b.native_submission=grant
    handoff.browser=b
    token=request(store,{'action':'claim'})['controller']
    request(store,{'action':'click','controller':token,'x':0.5,'y':0.5,'sequence':1,'frame_revision':'fixture-revision'})
    actions=[]
    route=SimpleNamespace(request=SimpleNamespace(method='POST',url='https://job-boards.greenhouse.io/submit',post_data_buffer=b'final'),
        abort=lambda:actions.append('abort'),continue_=lambda **kw:actions.append('transport'))
    def box():
        grant.used=True;b._continue_route(route)
        assert actions==[]
        return {'x':10,'y':10,'width':100,'height':100}
    frame=SimpleNamespace(is_visible=lambda:True,bounding_box=box,get_attribute=lambda _: '')
    b.page=SimpleNamespace(locator=lambda _:SimpleNamespace(count=lambda:1,nth=lambda _:frame),
        mouse=SimpleNamespace(click=lambda *xy:actions.append('stale_click')))
    handoff.pump(grant)
    assert actions==['transport'] and not b.captcha_command_active


@pytest.mark.parametrize('change',['disabled','foreign_site','replay','expired','invalid','outage','dispatched','cost'])
def test_fixture_solver_fails_closed_and_never_contacts_employers(change,tmp_path):
    challenge=FixtureChallenge('http://127.0.0.1:1234','public-test-key','nonce')
    def provider(c,**limits):
        assert set(limits)=={'timeout','max_cost'}
        if change=='outage':raise OSError('fixture outage')
        return {'token':'' if change=='invalid' else 'fixture-token','issued_at':time.time()-(121 if change=='expired' else 0),
                'nonce':c.nonce,'cost':1 if change=='cost' else 0.001}
    solver=FixtureSolver(provider,enabled=change!='disabled',authorized_fixture=True,journal_root=tmp_path/'solver')
    if change=='foreign_site':challenge=FixtureChallenge('https://jobs.ashbyhq.com','key','nonce')
    if change=='replay':assert solver.solve(challenge)['token']=='fixture-token'
    with pytest.raises(Blocked):solver.solve(challenge,final_dispatched=change=='dispatched')


def test_fixture_solver_crash_reservation_and_replay_survive_a_fresh_instance(tmp_path):
    challenge=FixtureChallenge('http://127.0.0.1:1234','public-test-key','nonce')
    options={'enabled':True,'authorized_fixture':True,'journal_root':tmp_path/'solver','max_seconds':0.01}
    def slow(*args,**kwargs):
        time.sleep(0.05)
        return {'token':'late','cost':0.001,'issued_at':time.time(),'nonce':'nonce'}
    with pytest.raises(Blocked,match='timed out'):FixtureSolver(slow,**options).solve(challenge)
    def forbidden(*args,**kwargs):pytest.fail('A crashed/late experiment must not call the provider again')
    with pytest.raises(Blocked):FixtureSolver(forbidden,**options).solve(challenge)
    with pytest.raises(Blocked):FixtureSolver(forbidden,**options).solve(FixtureChallenge(challenge.origin,challenge.site_key,'other-nonce'))
