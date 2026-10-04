import pytest

from hireme.posting_check import check_saved_posting
from hireme.util import digest


def test_saved_posting_check_uses_current_preferences_while_paused_without_writes(store,job):
    store.update_settings({'live_enabled':False})
    before=store.snapshot(); changes=store.db.total_changes
    result=check_saved_posting(store,job['id'])
    assert result['result']=='matches' and result['score']>0
    assert store.snapshot()==before and store.db.total_changes==changes
    store.update_settings({'locations':['London']})
    result=check_saved_posting(store,job['id'])
    assert result['result']=='held' and result['reason']=='location_mismatch'
    assert result['label']=='Outside your chosen locations'
    assert not store.db.execute('SELECT * FROM applications').fetchone()
    assert not store.db.execute('SELECT * FROM model_requests').fetchone()


@pytest.mark.parametrize('state',['confirmed','unknown','awaiting_verification','not_submitted'])
def test_saved_posting_check_preserves_recorded_outcomes(store,job,package,state):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown' if state=='not_submitted' else state)
    if state=='not_submitted':store.reconcile(aid,False,'Synthetic verified non-submission')
    before=store.snapshot();changes=store.db.total_changes
    result=check_saved_posting(store,job['id'])
    assert result['result']=='recorded_outcome' and result['score'] is None
    assert store.snapshot()==before and store.db.total_changes==changes


def test_saved_posting_check_includes_company_uncertainty_and_missing_setup(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    second={**job,'id':digest('second-check'),'url':job['url']+'-second'}
    store.upsert_job(second)
    assert check_saved_posting(store,second['id'])['reason'].startswith('company_uncertain')
    store.put_facts({},clear_keys=['needs_sponsorship'])
    result=check_saved_posting(store,second['id'])
    assert result['result']=='needs_setup' and 'sponsor' in result['detail'].lower()


@pytest.mark.parametrize('identifier',[None,'','x'*101,'missing'])
def test_saved_posting_check_requires_an_existing_identifier(store,identifier):
    with pytest.raises(ValueError):check_saved_posting(store,identifier)
