import pytest
from hireme.source_ledger import search_sources


def test_complete_source_pages_prioritize_failures_and_search_literal_unicode(store):
    for index in range(110):
        store.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                         (f'source-{index:03}', 'ok', '2026-10-03T12:00:00+00:00', '', '{"unused":"private fixture"}'))
    store.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                     ('old-Café-100%_', 'error', '2000-01-01T00:00:00+00:00', 'Synthetic timeout', '{}'))
    page = search_sources(store)
    assert page['total'] == 111 and len(page['sources']) == 25
    assert page['summary'] == {'total': 111, 'available': 110, 'unavailable': 1}
    assert page['sources'][0]['id'] == 'old-Café-100%_'
    assert all('payload' not in row for row in page['sources'])
    assert search_sources(store, search='CAFÉ-100%_')['total'] == 1
    assert search_sources(store, search='Synthetic timeout', status='error')['total'] == 1
    assert search_sources(store, search='CAFÉ', status='ok')['total'] == 0
    last = search_sources(store, offset=999)
    assert last['offset'] == 100 and len(last['sources']) == 11
    assert not search_sources(store, search='missing', offset=999)['sources']


@pytest.mark.parametrize('options', [{'search': 'x' * 201}, {'search': None}, {'status': 'broken'},
                                    {'offset': -1}, {'offset': True}, {'limit': 0}, {'limit': 101}])
def test_source_search_rejects_invalid_queries(store, options):
    with pytest.raises(ValueError): search_sources(store, **options)
