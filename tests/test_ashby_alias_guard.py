from types import SimpleNamespace

import pytest

from hireme.ashby_guard import begin, check, uncertain
from hireme.job_holds import annotate, hold, ready, recheck
from hireme.util import Blocked, digest


def posting(store, job, tenant='acme', identifier='11111111-1111-4111-8111-111111111111', suffix=''):
    url=f'https://jobs.ashbyhq.com/{tenant}/{identifier}{suffix}'
    result={**job,'id':digest(url),'url':url,'host':'jobs.ashbyhq.com'}
    store.upsert_job(result)
    return result


def marker(store, job, step='ashby:attempt', state='unknown'):
    store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
        (digest([job['id'],step]),job['id'],'jobs.ashbyhq.com',step,'request','sources',state,'2020','2020',''))


@pytest.mark.parametrize('step',['ashby:attempt','ashby:legacy'])
@pytest.mark.parametrize('state',['intent','unknown'])
def test_unresolved_alias_blocks_selection_recheck_and_direct_apply(store,job,step,state):
    original=posting(store,job)
    alias=posting(store,job,'RENAMED',identifier='11111111-1111-4111-8111-111111111111'.upper(),suffix='/application')
    marker(store,original,step,state)
    assert uncertain(store,alias['id']) and not ready(store,alias)
    assert recheck(store,alias['id'])=={'ready':False,'reason':'ashby_draft_uncertain'}
    with pytest.raises(Blocked,match='ashby_draft_uncertain'):check(store,alias['id'])
    hold(store,alias,'network_error')
    visible=annotate(store,[{**alias,'status':'blocked'}])[0]['hold']
    assert visible['reason']=='ashby_draft_uncertain' and visible['retry_at'] is None


def test_other_and_invalid_postings_do_not_gain_alias_identity(store,job):
    original=posting(store,job);marker(store,original)
    other=posting(store,job,identifier='22222222-2222-4222-8222-222222222222')
    malformed=posting(store,job,identifier='11111111-1111-4111-8111-11111111111x')
    assert not uncertain(store,other['id']) and not uncertain(store,malformed['id'])
    check(store,other['id']);check(store,malformed['id'])
    marker(store,malformed)
    assert uncertain(store,malformed['id'])
    with pytest.raises(Blocked):check(store,malformed['id'])


def test_legacy_failure_hold_blocks_alias(store,job):
    original=posting(store,job);alias=posting(store,job,'renamed')
    hold(store,original,'ashby_draft_save_failed')
    assert not ready(store,alias)
    assert recheck(store,alias['id'])=={'ready':False,'reason':'ashby_draft_uncertain'}
    with pytest.raises(Blocked):check(store,alias['id'])


def test_owned_session_stops_if_alias_fence_appears(store,job):
    original=posting(store,job);alias=posting(store,job,'renamed')
    request=SimpleNamespace(post_data_buffer=b'fixture')
    owned=begin(store,original,{},request,{})
    check(store,original['id'],owned)
    marker(store,alias)
    with pytest.raises(Blocked):check(store,original['id'],owned)


@pytest.mark.parametrize('change',['unsaved','wrong_url'])
def test_direct_live_apply_requires_saved_exact_posting(store,job,change):
    from hireme.browser import Browser
    saved=posting(store,job)
    supplied={**saved}
    if change=='unsaved':supplied['id']='unsaved'
    else:supplied['url']=supplied['url'].replace('/acme/','/renamed/')
    browser=Browser.__new__(Browser)
    browser.store=store;browser.host_cache={};browser.test_url=None
    with pytest.raises(Blocked,match='ashby_draft_uncertain'):
        browser.apply(supplied,live=True)
