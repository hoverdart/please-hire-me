import json

import pytest

from hireme.run_ledger import search_runs


def test_batch_history_pages_all_records_and_counts_requests_per_run(store):
    for index in range(62):
        store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',
                         (f'run-{index:03}', '2026-10-03T12:00:00+00:00', None, 'finished', 0, '{}'))
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',
                     ('old', '2000-01-01T00:00:00+00:00', None, 'blocked', 0,
                      json.dumps({'reason': 'Café 100%_ synthetic blocker'})))
    for provider in ('claude-cli', 'openai-api'):
        store.db.execute('INSERT INTO model_requests(timestamp,run_id,provider) VALUES(?,?,?)', ('2026-10-03T12:00:00+00:00', 'old', provider))
    store.db.execute('INSERT INTO model_requests(timestamp,run_id,provider) VALUES(?,?,?)', ('2026-10-03T12:00:00+00:00', 'different-run', 'codex-cli'))
    assert len(store.snapshot()['runs']) == 30
    first = search_runs(store)
    assert first['total'] == 63 and len(first['runs']) == 25
    last = search_runs(store, offset=999)
    assert last['offset'] == 50 and len(last['runs']) == 13
    assert last['runs'][-1]['id'] == 'old'
    match = search_runs(store, search='CAFÉ 100%_')
    assert match['total'] == 1 and match['runs'][0]['model_requests_used'] == 2
    assert search_runs(store, status='finished')['total'] == 62
    assert search_runs(store, status='blocked')['total'] == 1
    assert search_runs(store, status='paused')['total'] == 0


def test_malformed_or_missing_history_details_remain_searchable(store):
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', ('broken', '2026-10-03', None, 'interrupted', 0, 'Synthetic old raw note'))
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', ('empty', '2026-10-03', None, 'running', 0, ''))
    assert search_runs(store, search='old raw note')['runs'][0]['id'] == 'broken'
    assert search_runs(store, status='running')['runs'][0]['detail'] == ''


@pytest.mark.parametrize('kwargs', [{'search': None}, {'search': 'x' * 201}, {'status': 'unknown'},
                                  {'offset': True}, {'offset': -1}, {'offset': 1000001}, {'limit': 0}, {'limit': 101}])
def test_batch_history_validates_input(store, kwargs):
    with pytest.raises(ValueError): search_runs(store, **kwargs)
