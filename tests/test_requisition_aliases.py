"""Alternate ATS links cannot bypass durable submission history."""
import pytest
from hireme.util import Blocked, application_identity, digest

REQ = '12345678-1234-1234-1234-123456789abc'


@pytest.mark.parametrize('first,second', [
    (f'https://jobs.ashbyhq.com/acme/{REQ}', f'https://jobs.ashbyhq.com/acme/{REQ}/application?embed=true'),
    (f'https://jobs.lever.co/acme/{REQ}', f'https://jobs.lever.co/acme/{REQ}/apply?utm_source=test'),
    ('https://boards.greenhouse.io/acme/jobs/123', 'https://job-boards.greenhouse.io/embed/job_app?for=acme&token=123'),
    ('https://boards.eu.greenhouse.io/acme/jobs/123', 'https://job-boards.eu.greenhouse.io/acme/jobs/123?gh_src=test'),
])
def test_known_ats_requisition_aliases_share_identity(first, second):
    assert application_identity(first) == application_identity(second)


@pytest.mark.parametrize('url', [
    f'https://jobs.ashbyhq.com/other/{REQ}',
    'https://jobs.ashbyhq.com/acme/87654321-1234-1234-1234-123456789abc',
    f'https://jobs.ashbyhq.com/acme/{REQ}/unknown-step',
    f'https://jobs.lever.co/acme/{REQ}',
])
def test_unknown_or_distinct_requisitions_are_not_merged(url):
    assert application_identity(url) != application_identity(f'https://jobs.ashbyhq.com/acme/{REQ}')


@pytest.mark.parametrize('state', ['confirmed', 'unknown', 'awaiting_verification', 'not_submitted', 'submitting'])
def test_alias_cannot_retry_after_company_cooldown_or_name_change(store, job, package, state):
    first = {**job, 'id':digest('first-alias'), 'url':f'https://jobs.ashbyhq.com/acme/{REQ}', 'host':'jobs.ashbyhq.com'}
    store.upsert_job(first)
    aid = store.prepare(first, {**package, 'job_id':first['id'], 'url':first['url']})
    store.db.execute("UPDATE applications SET state=?,attempted='2020-01-01T00:00:00+00:00' WHERE id=?", (state, aid))
    second = {**first, 'id':digest('second-alias'), 'url':first['url']+'/application?embed=true', 'company':'Different feed name'}
    store.upsert_job(second)
    original = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    with pytest.raises(Blocked, match='duplicate_or_uncertain'):
        store.prepare(second, {**package, 'job_id':second['id'], 'url':second['url']})
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == original
    assert store.db.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 1


def test_manually_applied_alias_is_also_protected(store, job):
    first = {**job, 'id':digest('manual-alias'), 'url':f'https://jobs.ashbyhq.com/acme/{REQ}', 'host':'jobs.ashbyhq.com'}
    store.upsert_job(first)
    store.db.execute("INSERT INTO job_decisions VALUES(?,'manually_applied','discovered','2020-01-01T00:00:00+00:00')", (first['id'],))
    second = {**first, 'id':digest('another-alias'), 'url':first['url']+'/application', 'company':'Other feed name'}
    store.upsert_job(second)
    with pytest.raises(Blocked, match='duplicate_or_uncertain'):
        store._check_budget(second, store.settings())


def test_unsubmitted_preparation_is_not_a_recorded_attempt(store, job, package):
    first = {**job, 'id':digest('draft-alias'), 'url':f'https://jobs.ashbyhq.com/acme/{REQ}', 'host':'jobs.ashbyhq.com'}
    store.upsert_job(first); store.prepare(first, {**package, 'job_id':first['id'], 'url':first['url']})
    second = {**first, 'id':digest('draft-alias-two'), 'url':first['url']+'/application'}
    store.upsert_job(second)
    store._check_budget(second, store.settings())


def test_ashby_board_case_cannot_create_another_requisition_identity(store,job,package):
    first={**job,'id':digest('case-original'),'url':f'https://jobs.ashbyhq.com/Serval/{REQ}','host':'jobs.ashbyhq.com'}
    store.upsert_job(first)
    aid=store.prepare(first,{**package,'job_id':first['id'],'url':first['url']})
    store.db.execute("UPDATE applications SET state='confirmed',attempted='2020-01-01T00:00:00+00:00' WHERE id=?",(aid,))
    second={**first,'id':digest('case-alias'),'company':'Another feed name','url':f'https://jobs.ashbyhq.com/serval/{REQ}/application'}
    store.upsert_job(second)
    assert application_identity(first['url'])==application_identity(second['url'])
    with pytest.raises(Blocked,match='duplicate_or_uncertain'):store._check_budget(second,store.settings())


@pytest.mark.parametrize('state',['confirmed','unknown','awaiting_verification','not_submitted','submitting'])
def test_ashby_tenant_rename_cannot_retry_a_recorded_uuid(store,job,package,state):
    first={**job,'id':digest('before-rename'),'url':f'https://jobs.ashbyhq.com/acme/{REQ}','host':'jobs.ashbyhq.com'}
    store.upsert_job(first)
    aid=store.prepare(first,{**package,'job_id':first['id'],'url':first['url']})
    store.db.execute("UPDATE applications SET state=?,attempted='2020-01-01T00:00:00+00:00' WHERE id=?",(state,aid))
    second={**first,'id':digest('after-rename'),'company':'Renamed company','url':f'https://jobs.ashbyhq.com/renamed/{REQ}'}
    store.upsert_job(second)
    with pytest.raises(Blocked,match='duplicate_or_uncertain'):store._check_budget(second,store.settings())
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]==state
