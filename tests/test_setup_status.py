from hireme import setup_status


def test_browser_checks_are_cached_but_explicit_verification_is_fresh(monkeypatch):
    calls = []; clock = [100.0]
    monkeypatch.setattr(setup_status, '_BROWSER_CACHE', {})
    monkeypatch.setattr(setup_status.time, 'monotonic', lambda: clock[0])
    def detect(channel): calls.append(channel); return len(calls) > 1
    monkeypatch.setattr(setup_status, '_detect_browser', detect)
    assert not setup_status.browser_available('chromium')
    assert not setup_status.browser_available('chromium') and len(calls) == 1
    assert setup_status.browser_available('chromium', force=True) and len(calls) == 2
    clock[0] += 61
    assert setup_status.browser_available('chromium') and len(calls) == 3


def test_browser_cache_distinguishes_installation_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(setup_status, '_BROWSER_CACHE', {})
    monkeypatch.setattr(setup_status, '_detect_browser', lambda channel: calls.append(channel) or True)
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', '/tmp/synthetic-one')
    setup_status.browser_available('chromium'); setup_status.browser_available('chromium')
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', '/tmp/synthetic-two')
    setup_status.browser_available('chromium')
    setup_status.browser_available('system-chromium')
    assert calls == ['chromium', 'chromium', 'system-chromium']


def test_start_verification_does_not_reuse_cached_browser_result(store, monkeypatch):
    browser_flags = []; provider_flags = []
    monkeypatch.setattr(setup_status, 'browser_available', lambda channel, force=False: browser_flags.append(force) or True)
    monkeypatch.setattr(setup_status, 'status', lambda s, verify=False: provider_flags.append(verify) or {'ready': True})
    setup_status.readiness(store); setup_status.readiness(store, verify=True)
    assert browser_flags == provider_flags == [False, True]
