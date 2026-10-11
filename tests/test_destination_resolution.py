"""DNS outages stay retryable while private and mixed addresses stay denied."""
import socket
from types import SimpleNamespace

import pytest

from hireme.browser import Browser
from hireme.net import Network
from hireme.util import Blocked, public_host
from tests.test_browser import ats, local_job


@pytest.mark.parametrize('addresses,expected', [
    (['8.8.8.8', '2606:4700:4700::1111'], True),
    (['127.0.0.1'], False),
    (['8.8.8.8', '10.0.0.1'], False),
    ([], False),
])
def test_public_destination_requires_every_address_to_be_public(monkeypatch, addresses, expected):
    monkeypatch.setattr('hireme.util.socket.getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, '', (ip, 443)) for ip in addresses])
    assert public_host('fixture.invalid') is expected


def test_dns_failure_is_distinct_from_a_private_destination(monkeypatch):
    def fail(*args, **kwargs): raise socket.gaierror(socket.EAI_AGAIN, 'Temporary lookup failure')
    monkeypatch.setattr('hireme.util.socket.getaddrinfo', fail)
    assert public_host('fixture.invalid') is None


@pytest.mark.parametrize('allowed,reason', [(None, 'network_error'), (False, 'private_network_denied')])
def test_unresolved_or_private_discovery_never_sends_a_request(monkeypatch, allowed, reason):
    monkeypatch.setattr('hireme.net.public_host', lambda host: allowed)
    network = Network()
    def forbidden(*args, **kwargs): raise AssertionError('A denied destination must never receive a request')
    network.opener = SimpleNamespace(open=forbidden)
    with pytest.raises(Blocked, match=reason): network.fetch('https://fixture.invalid/job')


@pytest.mark.parametrize('allowed,code,reason', [
    (None, 'ERR_FAILED', 'navigation_failed'),
    (False, 'ERR_FAILED', 'posting_navigation_review'),
    (True, 'ERR_FAILED', 'posting_navigation_review'),
    (None, 'ERR_CERT_DATE_INVALID', 'posting_navigation_review'),
])
def test_only_proven_dns_outage_makes_route_failure_retryable(store, ats, monkeypatch, allowed, code, reason):
    from playwright.sync_api import Error
    with Browser(store, test_url=ats[0]) as browser:
        def fail(*args, **kwargs):
            browser.host_cache['127.0.0.1'] = allowed
            raise Error('net::' + code)
        monkeypatch.setattr(browser.page, 'goto', fail)
        with pytest.raises(Blocked, match=reason): browser.apply(local_job(store, ats))
    assert not ats[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_next_application_re_resolves_only_unavailable_destinations(store, ats, monkeypatch):
    from playwright.sync_api import Error
    with Browser(store, test_url=ats[0]) as browser:
        browser.host_cache = {'unavailable.invalid': None, 'private.invalid': False, 'public.invalid': True}
        def fail(*args, **kwargs):
            assert browser.host_cache == {'private.invalid': False, 'public.invalid': True}
            raise Error('net::ERR_FAILED')
        monkeypatch.setattr(browser.page, 'goto', fail)
        with pytest.raises(Blocked, match='posting_navigation_review'): browser.apply(local_job(store, ats))
    assert not ats[1]
