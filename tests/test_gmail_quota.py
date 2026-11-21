import json
from types import SimpleNamespace

import httplib2
import pytest

from hireme import gmail_quota
from hireme.store import Store
from hireme.util import Blocked


@pytest.fixture
def clock(monkeypatch):
    value=SimpleNamespace(current=1000.)
    monkeypatch.setattr(gmail_quota.time,'time',lambda:value.current)
    monkeypatch.setattr(gmail_quota.time,'sleep',lambda seconds:setattr(value,'current',value.current+seconds))
    return value


@pytest.mark.parametrize('method,query,units',[
    ('messages.get','',60),('messages.get','?format=full',60),('messages.get','?format=raw',60),
    ('messages.get','?format=metadata',20),('messages.get','?format=minimal',20),
    ('drafts.get','?format=full',60),('messages.list','',5),('getProfile','',1),
    ('history.list','',2),('messages.send','',100),('unrecognized','',100),
])
def test_current_method_costs_never_treat_full_message_bodies_as_cheap_reads(method,query,units):
    assert gmail_quota.units_for('gmail.users.'+method,'https://gmail.googleapis.com/gmail/v1/users/me/messages/item'+query)==units


def test_independent_clients_share_a_nonbursting_budget_and_restart_does_not_reset_it(store,clock):
    gmail_quota.reserve(store,60)
    assert clock.current==1000
    second=Store(store.root)
    try:
        gmail_quota.reserve(second,60)
        assert clock.current==pytest.approx(1000.8)
        gmail_quota.reserve(store,5)
        assert clock.current==pytest.approx(1001.6)
    finally:second.close()
    assert set(dict(store.db.execute('SELECT * FROM gmail_api_quota').fetchone()))=={'id','next_allowed'}


def test_real_google_request_builder_paces_before_transport_without_persisting_its_payload(store,clock):
    calls=[]
    class Http:
        def request(self,uri,method,body=None,headers=None,**kwargs):
            calls.append((clock.current,uri,method,body))
            return httplib2.Response({'status':'200'}),b'{"id":"response"}'
    builder=gmail_quota.request_builder(store)
    uri='https://gmail.googleapis.com/gmail/v1/users/me/messages/fixture-private-id?format=full'
    for _ in range(2):
        request=builder(Http(),lambda response,body:json.loads(body),uri,methodId='gmail.users.messages.get')
        assert request.execute()=={'id':'response'}
    assert [call[0] for call in calls]==pytest.approx([1000,1000.8])
    assert all(call[1:]==(uri,'GET',None) for call in calls)
    assert b'fixture-private-id' not in store.path.read_bytes()


def test_pause_during_quota_wait_sends_nothing_and_keeps_the_consumed_reservation(store,clock,monkeypatch):
    gmail_quota.reserve(store,60);calls=[]
    def checkpoint():
        if clock.current>1000:raise Blocked('paused')
    monkeypatch.setattr(store,'checkpoint',checkpoint)
    class Http:
        def request(self,*args,**kwargs):calls.append(args);raise AssertionError('Paused transport')
    request=gmail_quota.request_builder(store)(Http(),lambda *args:None,
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/item?format=full',methodId='gmail.users.messages.get')
    with pytest.raises(Blocked,match='paused'):request.execute()
    assert not calls and store.db.execute('SELECT next_allowed FROM gmail_api_quota').fetchone()[0]>clock.current


def test_quota_wait_cannot_hold_a_local_write_transaction_open(store,clock):
    with store.transaction(),pytest.raises(Blocked,match='gmail_budget_in_transaction'):
        gmail_quota.reserve(store,60)
