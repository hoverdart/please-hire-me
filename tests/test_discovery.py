import json
import pytest
from hireme.discovery import posting,source_result,sweep_lists
from hireme.util import Blocked,digest


def test_feed_destination_cannot_be_lookalike_or_private():
    for url in ['https://jobs.lever.co.attacker.invalid/acme/1','https://127.0.0.1/submit','https://attacker.invalid/jobs.lever.co/acme/1']:
        with pytest.raises(ValueError):posting(url,'Acme','Software Engineer','US','test')


def test_source_failure_keeps_candidates(store,job):
    source_result(store,'test',[job]);source_result(store,'test',error='timeout')
    assert store.db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
    assert store.db.execute('SELECT status FROM sources').fetchone()[0]=='error'


def test_list_feed_failure_records_error_without_watermark(store):
    class Net:
        context=None
        def json(self,*a,**k):raise TimeoutError()
        def fetch(self,*a,**k):raise TimeoutError()
    sweep_lists(store,Net())
    assert store.db.execute("SELECT count(*) FROM sources WHERE status='error'").fetchone()[0]==8
    assert not (store.root/'last_list_sweep.txt').exists()


def test_remote_shell_metacharacters_never_become_source_code(store):
    url='https://jobs.lever.co/acme/id-1'
    j=posting(url,"Acme'; print('no')",'Software Engineer','US','test')
    store.upsert_job(j)
    assert store.db.execute('SELECT company FROM jobs').fetchone()[0]==j['company']


def test_rediscovery_refreshes_display_fields_without_resetting_history(store, job, package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown','Synthetic uncertain outcome')
    original=dict(store.db.execute('SELECT * FROM jobs').fetchone())
    attempt=dict(store.db.execute('SELECT * FROM applications').fetchone())
    updated={**job,'id':'different-import-id','company':'Acme New Name','title':'Updated Software Engineer Intern',
             'location':'Boston, United States','source':'updated-source','description':'Updated public description'}
    assert store.upsert_job(updated)==job['id']
    row=dict(store.db.execute('SELECT * FROM jobs').fetchone())
    assert row['id']==job['id'] and row['first_seen']==original['first_seen'] and row['status']=='unknown'
    assert row['company']==updated['company'] and row['title']==updated['title'] and row['source']=='updated-source'
    assert row['company_key']==store.company(updated['company'])
    payload=json.loads(row['payload'])
    assert payload.pop('_listing_hash')==digest({k:updated.get(k,'') for k in ('url','company','title','location','description')})
    assert payload=={**updated,'id':job['id']}
    assert dict(store.db.execute('SELECT * FROM applications').fetchone())==attempt
    history=store.application_history()[0]
    assert attempt['company_key'] in history['company_keys'] and row['company_key'] in history['company_keys']
    from hireme.ledger import search_jobs,export_csv
    assert search_jobs(store,search='Updated Software Engineer')['jobs'][0]['title']==updated['title']
    assert updated['title'] in export_csv(store).decode('utf-8-sig')
    with pytest.raises(Blocked,match='company_uncertain|duplicate_or_uncertain'):
        store.prepare({**updated,'id':job['id']},package)


def test_rediscovery_preserves_manual_hold_and_does_not_mutate_input(store,job):
    store.block(job['id'],'company_blocked','Owner exclusion')
    store.upsert_job({**job,'title':'Corrected title'})
    row=store.db.execute('SELECT * FROM jobs').fetchone()
    assert row['status']=='blocked' and row['reason']=='company_blocked: Owner exclusion'
    assert job['title']=='Software Engineer Intern Summer 2027'


def test_greenhouse_custom_link_uses_official_requisition():
    from hireme.discovery import probe
    class Net:
        def json(self,url):
            return {'jobs':[{'id':123,'absolute_url':'https://stripe.com/jobs/123',
                            'title':'Software Engineer Intern','location':{'name':'US'}}]}
    result=probe(Net(),'gh','stripe')
    assert result[0]['url']=='https://job-boards.greenhouse.io/stripe/jobs/123'


def test_paused_network_does_not_open_any_request(monkeypatch):
    import pytest
    from hireme.net import Network
    from hireme.util import Blocked,digest
    def paused():raise Blocked('paused')
    n=Network(checkpoint=paused)
    monkeypatch.setattr(n.opener,'open',lambda *args,**kwargs:pytest.fail('Paused discovery must not send a request'))
    with pytest.raises(Blocked,match='paused'):n.fetch('https://jobs.lever.co/acme')


def test_discovery_retry_cannot_send_after_deadline(monkeypatch):
    import urllib.error
    from hireme import net
    monkeypatch.setattr(net, 'public_host', lambda host: True)
    clock = [9]
    monkeypatch.setattr(net.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(net.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    network = net.Network(deadline=10)
    requests = []
    def unavailable(request, **kwargs):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, 503, 'fixture unavailable', {}, None)
    monkeypatch.setattr(network.opener, 'open', unavailable)
    with pytest.raises(Blocked, match='discovery_deadline'):
        network.fetch('https://jobs.lever.co/acme')
    assert len(requests) == 1
