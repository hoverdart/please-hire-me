import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from hireme.discovery import posting, source_result


def test_unchanged_discovery_hold_does_not_repeat_events_or_retry_count(store):
    job=posting('https://jobs.lever.co/acme/senior','Acme','Senior Engineering Manager',
                'United States','lv:acme','Manage engineers; ten years experience required.')
    source_result(store,'lv:acme',[job])
    before=dict(store.db.execute('SELECT * FROM job_holds').fetchone())
    events=[tuple(row) for row in store.db.execute("SELECT * FROM events WHERE kind IN ('blocked','job_held')")]
    assert len(events)==2
    for _ in range(3):source_result(store,'lv:acme',[job])
    assert dict(store.db.execute('SELECT * FROM job_holds').fetchone())==before
    assert [tuple(row) for row in store.db.execute("SELECT * FROM events WHERE kind IN ('blocked','job_held')")]==events
    source_result(store,'lv:acme',[{**job,'title':'Staff Product Designer'}])
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind IN ('blocked','job_held')").fetchone()[0]==4


@pytest.mark.parametrize('feature',['gmail_reports','gmail_verification','gmail_feedback'])
def test_every_enabled_gmail_feature_requires_owner_bound_configuration(store,monkeypatch,feature):
    from hireme.release_check import _integrations
    monkeypatch.setattr('hireme.release_check.shutil.which',lambda _: '/synthetic/claude')
    settings={**store.settings(), 'gmail_reports':False,'gmail_verification':False,'gmail_feedback':False,feature:True}
    result=_integrations(store,settings)
    assert result['gmail']['enabled'] and not result['ready']


@pytest.mark.parametrize('code,kind',[(404,'not_found'),(410,'not_found'),(429,'rate_limited'),(503,'upstream_unavailable'),(403,'access_review')])
def test_discovery_classifies_http_failures_without_retaining_request_data(store,code,kind):
    error=HTTPError('https://example.invalid/?secret=sensitive-test-value',code,
                    'sensitive-test-value',{'Retry-After':'7200'},None)
    source_result(store,'synthetic',error=error)
    row=dict(store.db.execute('SELECT * FROM sources').fetchone())
    payload=json.loads(row['payload'])
    assert payload['http_status']==code and payload['failure_kind']==kind
    assert payload['failure_count']==1 and payload['next_check']>0
    assert 'sensitive-test-value' not in json.dumps(row)
    if code==429:
        import time
        assert payload['next_check']>=time.time()+7190


def test_discovery_backoff_skips_recent_failure_and_success_releases_it(store,monkeypatch):
    from hireme import discovery
    source_result(store,'gh:acme',error=HTTPError('https://example.invalid/',404,'synthetic',{},None))
    monkeypatch.setattr(discovery,'board_sources',lambda *a,**kw:[('gh','acme')])
    visited=[]
    monkeypatch.setattr(discovery,'probe',lambda *args:visited.append(args) or [])
    discovery.sweep_boards(store,Path('.'))
    assert not visited
    source_result(store,'gh:acme',[])
    row=store.db.execute('SELECT status,payload FROM sources').fetchone()
    assert row['status']=='ok' and json.loads(row['payload'])['failure_count']==0
    assert json.loads(row['payload'])['last_success']
