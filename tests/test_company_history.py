import json
from datetime import datetime, timedelta, timezone

import pytest

from hireme.cli import main
from hireme.util import Blocked, digest


def next_role(store, job, package, company='Acme Technologies'):
    other = {**job, 'id': digest('alias-regression-next-role'), 'url': job['url'] + '-next', 'company': company}
    store.upsert_job(other)
    return other, {**package, 'job_id': other['id'], 'url': other['url']}


@pytest.mark.parametrize('state,reason', [('submitting','company_uncertain'), ('unknown','company_uncertain'), ('awaiting_verification','company_verification_pending')])
def test_alias_edit_does_not_hide_an_uncertain_or_unverified_attempt(store, job, package, state, reason):
    aid = store.prepare(job, package); store.begin_submit(aid)
    if state != 'submitting': store.finish(aid, state)
    original = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'}})
    other, draft = next_role(store, job, package)
    with pytest.raises(Blocked, match=reason): store.prepare(other, draft)
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == original


@pytest.mark.parametrize('limit,age,reason', [(1,30,'company_limit'), (5,0,'company_same_day'), (5,1,'company_cooldown')])
def test_alias_edit_preserves_company_limits_and_local_day_rules(store, job, package, limit, age, reason):
    aid = store.prepare(job, package); store.begin_submit(aid); store.finish(aid, 'confirmed')
    stamp = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat(timespec='seconds')
    store.db.execute('UPDATE applications SET attempted=?,created=? WHERE id=?', (stamp, stamp, aid))
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'}, 'max_per_company': limit})
    other, draft = next_role(store, job, package)
    with pytest.raises(Blocked, match=reason): store.prepare(other, draft)


def test_removing_alias_keeps_original_company_and_duplicate_cli_history(store, job, package, capsys):
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'}})
    aid = store.prepare(job, package); store.begin_submit(aid); store.finish(aid, 'unknown')
    store.update_settings({'company_aliases': {}})
    other, draft = next_role(store, job, package, company='Acme')
    with pytest.raises(Blocked, match='company_uncertain'): store.prepare(other, draft)
    assert main(['--data-dir', str(store.root), 'duplicate', 'Acme']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['applications'][0]['id'] == aid
    assert set(result['applications'][0]) == {'id', 'state', 'created', 'attempted'}
    # Recorded association with the old canonical company remains conservative evidence.
    assert main(['--data-dir', str(store.root), 'duplicate', 'Acme Technologies']) == 1


def test_unrelated_company_is_not_held_by_other_company_alias_edit(store, job, package):
    aid = store.prepare(job, package); store.begin_submit(aid); store.finish(aid, 'unknown')
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'}})
    other, draft = next_role(store, job, package, company='Unrelated Labs')
    store.prepare(other, draft)
    assert len(store.application_history(states=('prepared',))) == 1
