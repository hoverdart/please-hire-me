import pytest

from hireme.outcome_ledger import search_outcomes


def insert_outcome(store, aid, jid, state='unknown', stamp='2026-10-03T12:00:00+00:00'):
    store.db.execute('''INSERT INTO applications(id,job_id,company_key,state,package,hash,created,updated,attempted)
                        VALUES(?,?,?,?,?,?,?,?,?)''',
                     (aid, jid, 'fixture-company', state, '{"private_answer":"never fetched by review"}', 'fixture', stamp, stamp, stamp))


def test_review_pages_all_pending_outcomes_oldest_first_without_packages(store, job):
    for index in range(60): insert_outcome(store, f'unknown-{index:03}', f'missing-job-{index}')
    insert_outcome(store, 'older', job['id'], 'awaiting_verification', '2000-01-01T00:00:00+00:00')
    insert_outcome(store, 'done', 'confirmed-job', 'confirmed')
    insert_outcome(store, 'manual', 'manual-job', 'not_submitted')
    insert_outcome(store, 'inflight', 'inflight-job', 'submitting')
    page = search_outcomes(store)
    assert page['total'] == 61 and len(page['applications']) == 25
    assert page['applications'][0]['id'] == 'older'
    assert page['applications'][0]['company'] == 'Acme' and page['applications'][0]['url'] == job['url']
    assert all('package' not in row for row in page['applications'])
    last = search_outcomes(store, offset=999)
    assert last['offset'] == 50 and len(last['applications']) == 11
    assert search_outcomes(store, status='unknown')['total'] == 60
    assert search_outcomes(store, status='awaiting_verification')['total'] == 1


def test_outcome_search_matches_unicode_literals_and_orphaned_company_key(store, job):
    store.db.execute('UPDATE jobs SET company=? WHERE id=?', ('Café 100%_ Labs', job['id']))
    insert_outcome(store, 'special', job['id'])
    insert_outcome(store, 'orphan', 'missing-job')
    result = search_outcomes(store, search='CAFÉ 100%_')
    assert result['total'] == 1 and result['applications'][0]['id'] == 'special'
    assert search_outcomes(store, search='fixture-company')['total'] == 2
    assert search_outcomes(store, search='never fetched')['total'] == 0


@pytest.mark.parametrize('kwargs', [{'search': None}, {'search': 'x' * 201}, {'status': 'confirmed'},
                                  {'offset': True}, {'offset': -1}, {'offset': 1000001}, {'limit': 0}, {'limit': 101}])
def test_review_outcome_input_is_validated(store, kwargs):
    with pytest.raises(ValueError): search_outcomes(store, **kwargs)
