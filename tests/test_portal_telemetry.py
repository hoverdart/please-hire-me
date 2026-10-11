"""Discard known portal telemetry without granting candidate-write permission."""
from types import SimpleNamespace
import pytest
from hireme.browser import Browser


@pytest.mark.parametrize('host', ['careers.qualcomm.com', 'explore.jobs.netflix.net', 'career.mlp.com'])
def test_portal_telemetry_is_aborted_and_real_draft_writes_stay_blocked(store, monkeypatch, host):
    monkeypatch.setattr('hireme.browser.public_host', lambda host: True)
    browser=Browser(store);browser.current_host=host
    request=SimpleNamespace(url='https://'+host+'/vslog', method='POST', post_data='private-fixture-payload')
    class Route:
        def __init__(self):self.request=request;self.action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    route=Route();browser._route(route)
    assert route.action=='abort' and not browser.denied_write
    assert not store.db.execute("SELECT 1 FROM events WHERE detail LIKE '%private-fixture-payload%'").fetchone()
    request.url='https://'+host+'/candidate/draft'
    browser._route(route)
    assert route.action=='abort' and browser.denied_write


def test_identical_path_on_an_unrecognized_ats_remains_a_blocker(store, monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host', lambda host: True)
    browser=Browser(store);browser.current_host='jobs.lever.co'
    class Route:
        request=SimpleNamespace(url='https://jobs.lever.co/vslog', method='POST', post_data='{}')
        action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    route=Route();browser._route(route)
    assert route.action=='abort' and browser.denied_write
