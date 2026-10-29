import json

import pytest

from hireme.ashby_legacy_guard import MARKER, protect_legacy_ashby
from hireme.util import digest, now


@pytest.fixture
def ashby_job(store,job):
    url='https://jobs.ashbyhq.com/acme/11111111-1111-4111-8111-111111111111'
    value={**job,'id':digest(url),'url':url,'host':'jobs.ashbyhq.com'}
    store.upsert_job(value)
    return value


def run(store,mode,run_id='r'):
    detail=json.dumps({'mode':mode}) if mode is not None else 'broken-json'
    store.db.execute("INSERT INTO runs(id,started,status,detail) VALUES(?,?,'finished',?)",(run_id,now(),detail))


def event(store,job,kind,detail):
    store.event(kind,job['id'],detail)


@pytest.mark.parametrize('mode',['live',None,'unknown','PREPARE'])
def test_live_or_unknown_started_attempt_is_held(store,ashby_job,mode):
    run(store,mode);event(store,ashby_job,'application_started',{'run_id':'r'})
    protect_legacy_ashby(store)
    row=store.db.execute('SELECT step,state FROM remote_draft_steps').fetchone()
    assert tuple(row)==('ashby:legacy','unknown')
    assert store.db.execute('SELECT COUNT(*) FROM events WHERE kind=?',(MARKER,)).fetchone()[0]==1


def test_missing_run_and_unattributed_field_evidence_are_held(store,ashby_job):
    event(store,ashby_job,'application_started',{'run_id':'missing'})
    event(store,ashby_job,'field_answered',{'value':'private synthetic value'})
    protect_legacy_ashby(store)
    row=store.db.execute('SELECT * FROM remote_draft_steps').fetchone()
    assert row['state']=='unknown' and 'private synthetic value' not in repr(tuple(row))


def test_paired_prepare_only_attempt_is_not_held(store,ashby_job):
    run(store,'prepare');event(store,ashby_job,'application_started',{'run_id':'r'})
    event(store,ashby_job,'field_answered',{'value':'fixture'});event(store,ashby_job,'field_filling',{})
    event(store,ashby_job,'application_finished',{'run_id':'r','outcome':'prepared'})
    protect_legacy_ashby(store)
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


@pytest.mark.parametrize('ending',['unpaired','wrong_run','write','direct'])
def test_ambiguous_or_observed_write_is_held_even_near_prepare(store,ashby_job,ending):
    run(store,'prepare')
    if ending!='direct':event(store,ashby_job,'application_started',{'run_id':'r'})
    event(store,ashby_job,'field_filling',{})
    if ending=='wrong_run':event(store,ashby_job,'application_finished',{'run_id':'other'})
    if ending=='write':
        event(store,ashby_job,'ashby_draft_write',{'value_hash':digest('fixture')})
        event(store,ashby_job,'application_finished',{'run_id':'r'})
    protect_legacy_ashby(store)
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='unknown'


def test_idempotent_and_new_events_not_reclassified(store,ashby_job):
    event(store,ashby_job,'application_started',{})
    protect_legacy_ashby(store);protect_legacy_ashby(store)
    assert store.db.execute('SELECT COUNT(*) FROM remote_draft_steps').fetchone()[0]==1
    assert store.db.execute('SELECT COUNT(*) FROM events WHERE kind=?',(MARKER,)).fetchone()[0]==1


@pytest.mark.parametrize('archived',[False,True])
def test_prior_outcome_preserved(store,ashby_job,package,archived):
    proof={**package,'job_id':ashby_job['id'],'url':ashby_job['url']}
    aid=store.prepare(ashby_job,proof)
    store.db.execute("UPDATE applications SET state='unknown' WHERE id=?",(aid,))
    if archived:
        store.db.execute("INSERT INTO archived_applications SELECT *, 'fixture' FROM applications WHERE id=?",(aid,))
        store.db.execute('DELETE FROM applications WHERE id=?',(aid,))
    event(store,ashby_job,'application_started',{})
    protect_legacy_ashby(store)
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
    assert store.db.execute('SELECT state FROM application_records WHERE id=?',(aid,)).fetchone()[0]=='unknown'


def test_marker_and_fences_rollback_together_when_outer_transaction_fails(store,ashby_job):
    event(store,ashby_job,'application_started',{})
    with pytest.raises(RuntimeError):
        with store.transaction():
            protect_legacy_ashby(store)
            raise RuntimeError('synthetic interruption')
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
    assert not store.db.execute('SELECT 1 FROM events WHERE kind=?',(MARKER,)).fetchone()
    protect_legacy_ashby(store)
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='unknown'


def test_worker_recovery_projects_old_activity_before_a_new_attempt(store,ashby_job):
    event(store,ashby_job,'application_started',{})
    store.recover()
    assert store.db.execute('SELECT state FROM remote_draft_steps WHERE job_id=?',(ashby_job['id'],)).fetchone()[0]=='unknown'
    assert store.db.execute('SELECT reason FROM job_holds WHERE job_id=?',(ashby_job['id'],)).fetchone()[0]=='ashby_draft_uncertain'


def test_direct_live_apply_migrates_old_activity_before_navigation(store,ashby_job):
    from hireme.browser import Browser
    from hireme.util import Blocked
    event(store,ashby_job,'application_started',{})
    browser=Browser(store)
    with pytest.raises(Blocked,match='ashby_draft_uncertain'):
        browser.apply(ashby_job,live=True)
    assert store.db.execute('SELECT state FROM remote_draft_steps WHERE job_id=?',(ashby_job['id'],)).fetchone()[0]=='unknown'
