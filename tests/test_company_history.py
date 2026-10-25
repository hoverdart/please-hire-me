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
    store.update_settings({'company_aliases': {'Acme': 'Acme Technologies'},
                           'max_per_company': limit, 'company_cooldown_days': 90})
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


def test_new_settings_have_no_company_count_or_waiting_limit():
    from hireme.config import validate_settings
    settings = validate_settings({})
    assert settings['max_per_company'] == settings['company_cooldown_days'] == 0
    # An upgrade must not silently discard another applicant's saved preferences.
    saved = validate_settings({}, {'max_per_company': 2, 'company_cooldown_days': 90})
    assert saved['max_per_company'] == 2 and saved['company_cooldown_days'] == 90


@pytest.mark.parametrize('key', ['max_per_company', 'company_cooldown_days'])
@pytest.mark.parametrize('value', [-1, True, False, 0.5, None, 86401])
def test_optional_company_limits_reject_invalid_values(key, value):
    from hireme.config import validate_settings
    with pytest.raises(ValueError, match=key):
        validate_settings({key: value})


def test_unlimited_company_policy_allows_multiple_distinct_roles_on_the_same_day(store, job, package):
    for index in range(5):
        other = {**job, 'id': digest(['unlimited-role', index]), 'url': job['url'] + '-' + str(index)}
        store.upsert_job(other)
        draft = {**package, 'job_id': other['id'], 'url': other['url']}
        aid = store.prepare(other, draft)
        store.begin_submit(aid)
        store.finish(aid, 'confirmed', 'Synthetic employer receipt')
    assert store.db.execute("SELECT COUNT(*) FROM applications WHERE state='confirmed'").fetchone()[0] == 5


def test_disabling_company_limits_preserves_the_overall_daily_limit(store, job, package):
    store.update_settings({'max_per_company': 0, 'company_cooldown_days': 0,
                           'max_per_day': 1, 'target_per_day': 1})
    aid = store.prepare(job, package)
    store.begin_submit(aid)
    store.finish(aid, 'confirmed', 'Synthetic employer receipt')
    other, draft = next_role(store, job, package, company=job['company'])
    with pytest.raises(Blocked, match='daily_limit'):
        store.prepare(other, draft)
