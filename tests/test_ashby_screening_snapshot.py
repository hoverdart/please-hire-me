from pathlib import Path

import pytest

from hireme.ashby_guard import check, screening_snapshot
from hireme.job_holds import hold, ready
from hireme.util import Blocked, digest
from tests.test_ashby_alias_guard import marker, posting


def test_snapshot_matches_uncertain_aliases_and_legacy_holds(store,job):
    original=posting(store,job,identifier='abcdef12-1111-4111-8111-111111111111')
    alias=posting(store,job,'RENAMED',identifier='ABCDEF12-1111-4111-8111-111111111111',suffix='/application')
    legacy=posting(store,job,'other',identifier='abcdef12-2222-4222-8222-222222222222')
    legacy_alias=posting(store,job,'new',identifier='abcdef12-2222-4222-8222-222222222222')
    unrelated=posting(store,job,'safe',identifier='abcdef12-3333-4333-8333-333333333333')
    marker(store,original,'ashby:legacy');hold(store,legacy,'ashby_draft_save_failed')
    snapshot=screening_snapshot(store)
    for blocked in [original,alias,legacy,legacy_alias]:
        assert snapshot.blocks(blocked) and not ready(store,blocked,ashby_snapshot=snapshot)
    assert not snapshot.blocks(unrelated) and not snapshot.blocks(job)
    assert ready(store,unrelated,ashby_snapshot=snapshot) and ready(store,job,ashby_snapshot=snapshot)


def test_snapshot_is_immutable_but_fresh_transport_and_next_snapshot_see_new_hold(store,job):
    original=posting(store,job)
    alias=posting(store,job,'renamed')
    previous=screening_snapshot(store)
    assert not previous.blocks(alias)
    marker(store,original)
    assert not previous.blocks(alias)
    with pytest.raises(Blocked,match='ashby_draft_uncertain'):check(store,alias['id'])
    refreshed=screening_snapshot(store)
    assert refreshed.blocks(alias) and not ready(store,alias,ashby_snapshot=refreshed)


def test_snapshot_keeps_malformed_exact_ids_and_ignores_acknowledged_steps(store,job):
    malformed=posting(store,job,identifier='not-a-uuid')
    other=posting(store,job,'other',identifier='not-a-uuid')
    completed=posting(store,job,identifier='abcdef12-4444-4444-8444-444444444444')
    marker(store,malformed);marker(store,completed,state='acknowledged')
    snapshot=screening_snapshot(store)
    assert snapshot.blocks(malformed) and not ready(store,malformed,ashby_snapshot=snapshot)
    assert not snapshot.blocks(other) and not snapshot.blocks(completed)


def test_worker_uses_one_screening_snapshot_per_transaction_batch(store,job,monkeypatch):
    import hireme.worker as worker
    for number in range(101):
        url=f'https://jobs.lever.co/synthetic/{number}'
        store.upsert_job({**job,'id':digest(url),'url':url,'company':f'Synthetic {number}'})
    seen=[]
    real_ready=worker.ready
    def observe(selected_store,selected_job,at=None,ashby_snapshot=None):
        assert selected_store.db.in_transaction and ashby_snapshot is not None
        seen.append(ashby_snapshot)
        return real_ready(selected_store,selected_job,at=at,ashby_snapshot=ashby_snapshot)
    monkeypatch.setattr(worker,'ready',observe)
    monkeypatch.setattr(worker,'eligible',lambda *args:(1,{}))
    worker.cycle(store,Path('.'),discover=False,live=False,limit=0)
    assert len(seen)==102
    # 100 is the upper bound; time-based yielding can create smaller batches.
    assert len({id(snapshot) for snapshot in seen})>=2
    assert len({id(snapshot) for snapshot in seen})<len(seen)
