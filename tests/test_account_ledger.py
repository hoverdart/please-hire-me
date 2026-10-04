import pytest

from hireme.account_ledger import search_accounts


def test_account_review_reaches_all_old_holds_and_searches_unicode_literal_text(store):
    for index in range(120):
        store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
            (f'confirmed-{index}', 'https://jobs.lever.co', f'Recent company {index}', 'confirmed', '2026-10-03T00:00:00+00:00'))
    for index in range(30):
        store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
            (f'held-{index}', 'https://jobs.lever.co', f'Held company {index}', 'uncertain', '2020-01-01T00:00:00+00:00'))
    store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
        ('old-special', 'https://jobs.lever.co', 'Café 100%_ Research', 'uncertain', '2019-01-01T00:00:00+00:00'))
    first = search_accounts(store); second = search_accounts(store, offset=25)
    assert first['total'] == 31 and len(first['accounts']) == 25
    assert len(second['accounts']) == 6 and any(row['id'] == 'old-special' for row in second['accounts'])
    assert not {row['id'] for row in first['accounts']} & {row['id'] for row in second['accounts']}
    assert search_accounts(store, status='all')['total'] == 151
    assert search_accounts(store, search='CAFÉ 100%_')['accounts'][0]['id'] == 'old-special'
    assert search_accounts(store, search="' OR 1=1 --")['total'] == 0
    assert set(first['accounts'][0]) == {'id', 'origin', 'company', 'state', 'updated'}
    assert search_accounts(store, offset=1000)['offset'] == 25


@pytest.mark.parametrize('arguments', [{'search': 'x' * 201}, {'status': 'credentials'}, {'offset': -1}, {'offset': True}, {'limit': 0}, {'limit': 101}])
def test_account_review_rejects_unbounded_or_unknown_requests(store, arguments):
    with pytest.raises(ValueError): search_accounts(store, **arguments)
