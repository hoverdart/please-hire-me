import json
import pytest
from hireme import platform_connections as pc
from hireme.store import Store
from hireme.util import Blocked, digest, now
from hireme.ledger import search_jobs


def test_connections_default_off_and_additive_reopen(store, job, package):
    aid=store.prepare(job,package)
    before=dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())
    root=store.root
    assert all(not c['enabled'] and not any(v['available'] for v in c['capabilities'].values()) for c in pc.list_connections(store))
    reopened=Store(root)
    try:
        assert dict(reopened.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())==before
        assert reopened.db.execute('SELECT id FROM jobs WHERE url=?',(job['url'],)).fetchone()[0]==job['id']
        assert reopened.db.execute('SELECT COUNT(*) FROM application_artifacts').fetchone()[0]==0
    finally:reopened.close()


@pytest.mark.parametrize('data',[{}, {'enabled':1}, {'enabled':'false'}, {'live_verified':True}, {'discovery_enabled':None}])
def test_configuration_cannot_install_verification(store,data):
    with pytest.raises(ValueError):pc.configure(store,'handshake',data)


def test_toggles_are_independent_and_discovery_does_not_open_browser(store,monkeypatch):
    pc.configure(store,'handshake',{'enabled':True,'native_apply_enabled':True})
    assert not pc.configuration(store,'handshake')['discovery_enabled']
    with pytest.raises(Blocked,match='connection_disabled'):pc.discover(store,'handshake')
    pc.configure(store,'handshake',{'discovery_enabled':True})
    monkeypatch.setattr('hireme.platform_browser.PlatformBrowser',lambda *a:pytest.fail('unverified flow opened browser'))
    with pytest.raises(Blocked,match='connection_access_unverified'):pc.discover(store,'handshake')
    assert 'bulk collection' in pc.status(store,'handshake')['capabilities']['discovery']['message']


@pytest.mark.parametrize('scope,live,version',[('fixture',True,1),('live',False,1),('live',True,2),('live',1,1)])
def test_fixture_stale_or_session_evidence_cannot_enable_capability(store,scope,live,version):
    evidence={'version':version,'connection_id':'handshake','proof_scope':scope,'live_verified':live,'access_authorized':True,'evidence_hash':'a'*64}
    store.db.execute('INSERT INTO connection_flows VALUES(?,?,?,?,?,?)',('handshake','submission',version,'sig',json.dumps(evidence),now()))
    assert not pc.flows(store,'handshake','submission')


def test_external_requisition_dedup_preserves_ids_and_all_origins(store,job,package):
    aid=store.prepare(job,package)
    for platform,url in [('handshake','https://app.joinhandshake.com/jobs/123'),('workatastartup','https://workatastartup.com/jobs/456')]:
        assert pc.import_listing(store,url,job['company'],job['title'],external_url=job['url']+'/apply?utm_source=x')==job['id']
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    assert store.application_record(aid)['package']==json.dumps(package)
    result=search_jobs(store,source='handshake',destination='external',include_packages=False)
    assert result['total']==1 and len(result['jobs'][0]['origins'])==2
    assert search_jobs(store,destination='native')['total']==0
    assert 'package' not in result['applications'][0]


def test_similar_titles_do_not_dedup_and_conflicts_do_not_overwrite(store,job):
    native=pc.import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'])
    assert native!=job['id']
    with pytest.raises(Blocked,match='identity_review'):
        pc.import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'],external_url=job['url'])
    with pytest.raises(Blocked,match='identity_review'):
        pc.import_listing(store,'https://workatastartup.com/jobs/456','Different Employer',job['title'],external_url=job['url'])
    assert store.db.execute('SELECT company FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='Acme'
    assert store.db.execute('SELECT job_id FROM job_origins').fetchone()[0]==native


def test_filters_validate_and_paginate(store,job):
    for number in range(7):pc.import_listing(store,f'https://workatastartup.com/jobs/{number}',job['company'],job['title'])
    store.db.execute("UPDATE jobs SET score=80 WHERE source='workatastartup'")
    page=search_jobs(store,source='workatastartup',destination='native',min_fit=70,offset=2,limit=3)
    assert page['total']==7 and len(page['jobs'])==3 and all(j['connection_id']=='workatastartup' for j in page['jobs'])
    for kwargs in ({'source':'linkedin'},{'destination':'madeup'},{'min_fit':101}):
        with pytest.raises(ValueError):search_jobs(store,**kwargs)


def test_disconnect_retains_history_and_removes_only_its_profile(store,job,package):
    aid=store.prepare(job,package);pc.configure(store,'handshake',{'enabled':True})
    private=pc.profile_path(store,'handshake');(private/'session').write_text('fixture')
    other=pc.profile_path(store,'workatastartup');(other/'session').write_text('fixture')
    pc.disconnect(store,'handshake')
    assert not private.exists() and other.exists() and store.application_record(aid)
    assert not pc.status(store,'handshake')['enabled']


def test_partial_discovery_failure_keeps_jobs_and_other_connections_run(store,job,monkeypatch):
    for connection in pc.PLATFORMS:pc.configure(store,connection,{'enabled':True,'discovery_enabled':True})
    calls=[]
    def discover(s,c):
        calls.append(c)
        pc.import_listing(s,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'])
        if c=='handshake':raise Blocked('connection_sign_in_required')
        return {'jobs':1}
    monkeypatch.setattr(pc,'discover',discover)
    pc.sweep_connections(store)
    assert calls==['handshake','workatastartup'] and store.db.execute('SELECT COUNT(*) FROM job_origins').fetchone()[0]==1
    assert store.db.execute("SELECT status FROM sources WHERE id='connection:handshake'").fetchone()[0]=='error'


def test_legacy_schema_upgrade_preserves_confirmed_application(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed','Historical confirmation')
    before=store.application_record(aid)
    for table in ['platform_connections','connection_flows','connection_conflicts','job_origins','application_artifacts','platform_documents','connection_receipts','saved_view_connections']:
        store.db.execute('DROP TABLE '+table)
    reopened=Store(store.root)
    try:
        assert reopened.application_record(aid)==before
        assert len(pc.list_connections(reopened))==2 and not pc.status(reopened,'handshake')['enabled']
    finally:reopened.close()


def test_live_verification_is_bound_to_dedicated_profile_and_disconnect_revokes_it(store):
    pc.profile_path(store,'handshake')
    evidence={'version':1,'connection_id':'handshake','proof_scope':'live','live_verified':True,'access_authorized':True,'evidence_hash':'a'*64,'profile_binding':pc.profile_binding(store,'handshake')}
    store.db.execute('INSERT INTO connection_flows VALUES(?,?,?,?,?,?)',('handshake','inspection',1,'sig',json.dumps(evidence),now()))
    assert pc.status(store,'handshake')['capabilities']['inspection']['available']
    pc.disconnect(store,'handshake');pc.profile_path(store,'handshake')
    assert not pc.status(store,'handshake')['capabilities']['inspection']['available']


def test_changed_connection_dependencies_release_only_unattempted_holds(store,job,package):
    from hireme.job_holds import hold,ready
    native_id=pc.import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'])
    native=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(native_id,)).fetchone()[0])
    hold(store,native,'connection_disabled')
    assert not ready(store,native)
    pc.configure(store,'handshake',{'enabled':True})
    assert ready(store,native)
    hold(store,native,'connection_identity_review')
    pc.configure(store,'handshake',{'native_apply_enabled':True})
    assert not ready(store,native)
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    assert not ready(store,job)


def test_conflict_candidate_is_reviewable_without_changing_history(store,job,package):
    aid=store.prepare(job,package)
    store.begin_submit(aid)
    before=store.application_record(aid)
    status_before=store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
    with pytest.raises(Blocked,match='identity_review'):
        pc.import_listing(store,'https://app.joinhandshake.com/jobs/91','Different Company','Different Role',external_url=job['url'])
    candidate=search_jobs(store)['jobs'][0]['identity_conflicts'][0]['candidate']
    assert candidate['company']=='Different Company' and candidate['listing_url'].endswith('/91')
    assert store.application_record(aid)==before
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]==status_before


@pytest.mark.parametrize('url',['https://jobs.lever.co/acme','https://boards.greenhouse.io/acme/jobs','https://jobs.ashbyhq.com/acme'])
def test_external_board_links_do_not_become_canonical_jobs(store,url):
    with pytest.raises(Blocked,match='destination_review'):
        pc.import_listing(store,'https://app.joinhandshake.com/jobs/99','Acme','Role',external_url=url)
    assert store.db.execute('SELECT COUNT(*) FROM job_origins').fetchone()[0]==0


def test_sign_in_thread_start_failure_can_be_retried(store,monkeypatch):
    import threading
    monkeypatch.setattr(threading.Thread,'start',lambda self: (_ for _ in ()).throw(RuntimeError('unavailable')))
    with pytest.raises(RuntimeError):pc.start_connect(store,'handshake')
    assert pc.configuration(store,'handshake')['session_state']!='connecting'
