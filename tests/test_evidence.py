import pytest

from hireme.ledger import search_jobs


def test_compact_views_omit_packages_but_exact_record_is_unchanged(store, job, package):
    aid = store.prepare(job, package)
    original = store.application_record(aid)
    assert 'package' in store.snapshot()['applications'][0]
    assert 'package' not in store.snapshot(include_packages=False)['applications'][0]
    assert 'package' not in search_jobs(store, include_packages=False)['applications'][0]
    assert search_jobs(store)['applications'][0] == original
    assert store.application_record(aid) == original
    assert store.application_record('missing') is None


@pytest.mark.parametrize('identifier', ['', 'x' * 101, None])
def test_record_lookup_rejects_invalid_identifiers(store, identifier):
    with pytest.raises(ValueError): store.application_record(identifier)
