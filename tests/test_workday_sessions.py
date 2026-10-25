import copy
import json
import shutil
from types import SimpleNamespace
import zipfile

import pytest

from hireme.util import Blocked
from hireme.workday_profiles import NVIDIA, PROFILE_READ
from hireme import workday_sessions as sessions


def profile(store, *, data=None, status=200):
    return SimpleNamespace(request=SimpleNamespace(url=PROFILE_READ, method='GET', post_data_buffer=None),
        status=status, json=lambda: data if data is not None else {'emailAddress': store.facts()['email']['value']})


def cookies():
    return [{'name': name, 'value': 'synthetic-' + name, 'domain': NVIDIA, 'path': '/',
             'expires': -1, 'httpOnly': True, 'secure': True, 'sameSite': site}
            for name, site in sessions.COOKIE_SITES.items()]


def native_cookies(csrf_site):
    values = cookies()
    next(cookie for cookie in values if cookie['name'] == 'CALYPSO_CSRF_TOKEN')['sameSite'] = csrf_site
    return values


class Context:
    def __init__(self, values=None):
        self.values = cookies() if values is None else values
        self.added = []
    def cookies(self, origins):
        assert origins == ['https://' + NVIDIA]
        return copy.deepcopy(self.values)
    def add_cookies(self, values):
        self.added.extend(copy.deepcopy(values))


@pytest.mark.parametrize('verified_owner', [True, False])
@pytest.mark.parametrize('csrf_site', ['Lax', 'Strict'])
def test_login_retains_only_the_verified_applicant_and_always_closes_browser(store, monkeypatch, verified_owner, csrf_site):
    from contextlib import nullcontext
    from hireme.cli import main

    context = Context(native_cookies(csrf_site))
    context.closed = False
    page = SimpleNamespace(goto=lambda url: None)
    context.pages = [page]
    def close():
        context.closed = True
    context.close = close
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *args, **kwargs: context))
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: nullcontext(playwright))
    monkeypatch.setattr('builtins.input', lambda: '')
    monkeypatch.setattr('hireme.util.public_host', lambda host: True)
    monkeypatch.setattr(sessions, 'read_profile', lambda page: profile(store, data={
        'emailAddress': store.facts()['email']['value'] if verified_owner else 'someone-else@candidate.invalid'}))

    result = main(['--data-dir', str(store.root), 'login', 'https://' + NVIDIA + '/en-US/NVIDIAExternalCareerSite'])

    assert context.closed
    assert result == (0 if verified_owner else 2)
    assert (NVIDIA in store.settings()['signed_in_portals']) is verified_owner
    cache = store.root/'browser'/'workday-session-nvidia.json'
    assert cache.exists() is verified_owner
    if verified_owner:
        assert cache.stat().st_mode & 0o777 == 0o600


def cached(store):
    context = Context()
    binding = sessions.capture(store, context, profile(store))
    path = store.root/'browser'/'workday-session-nvidia.json'
    return context, binding, path


@pytest.mark.parametrize('csrf_site', ['Lax', 'Strict'])
def test_private_session_restores_exact_attributes_without_refreshing_owner_evidence(store, monkeypatch, csrf_site):
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000)
    original = Context(native_cookies(csrf_site))
    binding = sessions.capture(store, original, profile(store))
    path = store.root/'browser'/'workday-session-nvidia.json'
    before = path.read_bytes()
    changes = store.db.total_changes
    restored = Context([])
    monkeypatch.setattr(sessions.time, 'time', lambda: 1050)
    assert sessions.restore(store, restored)
    assert restored.added == original.values
    assert path.read_bytes() == before and store.db.total_changes == changes
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(before)['profile_binding'] == binding.profile_binding
    assert not store.db.execute('SELECT 1 FROM adapter_verifications').fetchone()


@pytest.mark.parametrize('name,site', [
    ('CALYPSO_CSRF_TOKEN', 'None'), ('CALYPSO_CSRF_TOKEN', 'Unexpected'),
    ('CALYPSO_SESSION', 'Strict'), ('CALYPSO_SESSION', 'None'),
    ('PLAY_SESSION', 'Strict'), ('PLAY_SESSION', 'None'), ('wday_vps_cookie', 'Strict')])
def test_strict_csrf_support_does_not_accept_other_attribute_changes(store, name, site):
    values = native_cookies('Strict')
    next(cookie for cookie in values if cookie['name'] == name)['sameSite'] = site
    with pytest.raises(Blocked, match='workday_session_unverified'):
        sessions.capture(store, Context(values), profile(store))
    assert not (store.root/'browser'/'workday-session-nvidia.json').exists()


def test_status_distinguishes_bound_profile_from_cached_owner_without_reading_employer_or_mutating(store, monkeypatch):
    from hireme.workday import status
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000)
    _, _, path = cached(store)
    before = path.read_bytes()
    changes = store.db.total_changes
    row = next(row for row in status(store)['tenants'] if row['host'] == NVIDIA)
    assert row['session_bound']
    assert row['cached_owner_evidence'] == {'source': 'cached_native_owner_read', 'owner_matches': True,
        'verified_at': 1000, 'expires_at': 1000 + sessions.MAX_AGE_SECONDS, 'fresh_owner_read_required': True}
    assert not row['enabled'] and not row['operationally_verified']
    assert 'synthetic-' not in json.dumps(row) and store.facts()['email']['value'] not in json.dumps(row)
    assert path.read_bytes() == before and store.db.total_changes == changes
    monkeypatch.setattr(sessions.time, 'time', lambda: 1001 + sessions.MAX_AGE_SECONDS)
    row = next(row for row in status(store)['tenants'] if row['host'] == NVIDIA)
    assert row['session_bound'] and not row['cached_owner_evidence']['owner_matches']
    assert row['cached_owner_evidence']['verified_at'] is None
    assert row['cached_owner_evidence']['expires_at'] is None
    assert path.read_bytes() == before and store.db.total_changes == changes


def test_status_cached_owner_expiry_honors_native_cookie_expiry(store, monkeypatch):
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000)
    values = cookies();values[0]['expires'] = 1050
    sessions.capture(store, Context(values), profile(store))
    assert sessions.cached_evidence(store)['expires_at'] == 1050
    monkeypatch.setattr(sessions.time, 'time', lambda: 1051)
    assert not sessions.cached_evidence(store)['owner_matches']


@pytest.mark.parametrize('change', ['old', 'future', 'owner', 'marker', 'permissions', 'symlink', 'corrupt', 'version', 'oversize'])
def test_invalid_cache_never_injects_cookies_or_changes_its_verified_time(store, monkeypatch, change):
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000)
    _, _, path = cached(store)
    data = json.loads(path.read_text())
    if change == 'old':monkeypatch.setattr(sessions.time, 'time', lambda: 1001 + sessions.MAX_AGE_SECONDS)
    elif change == 'future':monkeypatch.setattr(sessions.time, 'time', lambda: 999)
    elif change == 'owner':store.put_facts({'email': 'different@candidate.invalid'})
    elif change == 'marker':(path.parent/'workday-binding.json').unlink()
    elif change == 'permissions':path.chmod(0o644)
    elif change == 'symlink':
        other = path.with_name('other-private-cache');path.rename(other);path.symlink_to(other)
    elif change == 'corrupt':path.write_text('{broken')
    elif change == 'version':data['version'] = True;path.write_text(json.dumps(data))
    elif change == 'oversize':path.write_text('x' * 131073)
    before = path.read_bytes()
    context = Context([])
    assert not sessions.restore(store, context)
    assert not context.added and path.read_bytes() == before
    evidence = sessions.cached_evidence(store)
    assert not evidence['owner_matches'] and evidence['verified_at'] is None and evidence['expires_at'] is None
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['parent_domain', 'leading_dot', 'path', 'not_secure', 'not_httponly',
    'same_site', 'expired', 'zero_expiry', 'duplicate', 'partitioned', 'missing', 'control_value'])
def test_unknown_cookie_scope_or_attributes_never_enter_cache(store, change):
    values = cookies()
    if change == 'parent_domain':values[0]['domain'] = '.myworkdayjobs.com'
    elif change == 'leading_dot':values[0]['domain'] = '.' + NVIDIA
    elif change == 'path':values[0]['path'] = '/other'
    elif change == 'not_secure':values[0]['secure'] = False
    elif change == 'not_httponly':values[0]['httpOnly'] = False
    elif change == 'same_site':values[0]['sameSite'] = 'None'
    elif change == 'expired':values[0]['expires'] = 1
    elif change == 'zero_expiry':values[0]['expires'] = 0
    elif change == 'duplicate':values[-1] = copy.deepcopy(values[0])
    elif change == 'partitioned':values[0]['partitionKey'] = 'https://example.invalid'
    elif change == 'missing':values = values[1:]
    elif change == 'control_value':values[0]['value'] = 'header\r\ninjection'
    with pytest.raises(Blocked, match='workday_session_unverified'):
        sessions.capture(store, Context(values), profile(store))
    assert not (store.root/'browser'/'workday-session-nvidia.json').exists()


@pytest.mark.parametrize('data,status', [({}, 200), ({'emailAddress': 'other@candidate.invalid'}, 200), ({}, 403)])
def test_native_anonymous_or_wrong_owner_read_forgets_cache_without_recapturing(store, data, status):
    _, _, path = cached(store)
    with pytest.raises(Blocked):sessions.capture(store, Context(), profile(store, data=data, status=status))
    assert not path.exists()
    assert not sessions.restore(store, Context([]))


def test_session_cache_is_excluded_from_portable_backup_and_explicit_logout_removes_it(store, tmp_path):
    from hireme.backup import create_backup
    _, _, path = cached(store)
    archive = tmp_path/'portable.zip'
    create_backup(store, archive)
    with zipfile.ZipFile(archive) as source:
        assert all('browser' not in name and 'workday-session' not in name for name in source.namelist())
        assert b'synthetic-CALYPSO_SESSION' not in b''.join(source.read(name) for name in source.namelist())
    assert sessions.forget(store) and not path.exists()
    assert not sessions.forget(store)


@pytest.mark.parametrize('csrf_site', ['Lax', 'Strict'])
def test_guarded_real_chromium_session_cookie_survives_only_verified_private_restore(store, csrf_site):
    from playwright.sync_api import sync_playwright
    origin = 'https://' + NVIDIA
    html = "<html lang='en-US'><script>window.workday={tenant:'nvidia',siteId:'NVIDIAExternalCareerSite',token:'synthetic-fresh-csrf'}</script></html>"
    reads = []
    def guard(route):
        request = route.request
        assert request.method == 'GET', 'The session regression permits no remote writes'
        if request.url == PROFILE_READ:
            reads.append(request.headers)
            present = 'CALYPSO_SESSION=synthetic-CALYPSO_SESSION' in request.headers.get('cookie', '')
            return route.fulfill(status=200, content_type='application/json',
                body=json.dumps({'emailAddress': store.facts()['email']['value']} if present else {}))
        if request.url == origin + '/en-US/NVIDIAExternalCareerSite':
            return route.fulfill(status=200, content_type='text/html', body=html)
        return route.abort()
    options = {'headless':True, 'service_workers':'block'}
    channel = store.settings()['browser_channel']
    if channel == 'chrome':options['channel'] = 'chrome'
    elif channel == 'system-chromium':
        options['executable_path'] = shutil.which('chromium') or shutil.which('chromium-browser')
        assert options['executable_path'], 'Install the configured system Chromium for this worker'
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(store.root/'browser'), **options)
        try:
            context.route('**/*', guard)
            context.add_cookies(native_cookies(csrf_site))
            page = context.pages[0];page.goto(origin+'/en-US/NVIDIAExternalCareerSite')
            response = sessions.read_profile(page)
            sessions.capture(store, context, response)
            assert response.json()['emailAddress'] == store.facts()['email']['value']
        finally:context.close()
        context = playwright.chromium.launch_persistent_context(str(store.root/'browser'), **options)
        try:
            context.route('**/*', guard)
            assert not context.cookies([origin]), 'A fresh Chromium context drops native session-only cookies'
            assert sessions.restore(store, context)
            retained = next(cookie for cookie in context.cookies([origin]) if cookie['name'] == 'CALYPSO_CSRF_TOKEN')
            assert retained['sameSite'] == csrf_site
            page = context.pages[0];page.goto(origin+'/en-US/NVIDIAExternalCareerSite')
            response = sessions.read_profile(page)
            assert response.json()['emailAddress'] == store.facts()['email']['value']
            assert reads[-1]['x-calypso-csrf-token'] == 'synthetic-fresh-csrf'
            assert len(reads) == 2
            assert not store.db.execute('SELECT 1 FROM adapter_verifications').fetchone()
        finally:context.close()



@pytest.mark.parametrize('status,retained',[(500,True),(401,False),(403,False)])
def test_failed_owner_read_never_renews_cache_and_auth_rejection_removes_even_non_json(store,status,retained):
    _,_,path=cached(store)
    before=path.read_bytes()
    response=profile(store,status=status)
    def unreadable():raise ValueError('Synthetic non-JSON response')
    response.json=unreadable
    with pytest.raises(Blocked):sessions.capture(store,Context(),response)
    assert path.exists() is retained
    if retained:assert path.read_bytes()==before



@pytest.mark.parametrize('last_response',['valid','anonymous','forbidden'])
def test_controller_ready_refreshes_verified_native_cookie_rotation_or_invalidates_cache(store,job,monkeypatch,last_response):
    from hireme.util import digest
    from hireme.workday import WorkdayController
    from hireme.workday_profiles import REVIEWED_TRANSPORTS
    nv_job={**job,'host':NVIDIA,'company':'NVIDIA','url':'https://'+NVIDIA+'/en-US/NVIDIAExternalCareerSite/job/US-CA/Fixture_JR1'}
    nv_job['id']=digest(nv_job['url']);store.upsert_job(nv_job)
    responses=[profile(store),profile(store,data={} if last_response!='valid' else None,status=403 if last_response=='forbidden' else 200)]
    native_profile=SimpleNamespace(read_session=lambda controller:responses.pop(0))
    monkeypatch.setitem(REVIEWED_TRANSPORTS,NVIDIA,native_profile)
    context=Context();browser=SimpleNamespace(store=store,context=context)
    controller=WorkdayController(browser,nv_job)
    monkeypatch.setattr(sessions.time,'time',lambda:1000)
    controller._ready()
    path=store.root/'browser'/'workday-session-nvidia.json'
    assert json.loads(path.read_text())['verified_at']==1000
    context.values[0]['value']='synthetic-rotated-native-cookie'
    monkeypatch.setattr(sessions.time,'time',lambda:1100)
    if last_response=='valid':
        controller._ready()
        data=json.loads(path.read_text())
        assert data['verified_at']==1100
        assert data['cookies'][0]['value']=='synthetic-rotated-native-cookie'
    else:
        with pytest.raises(Blocked):controller._ready()
        assert not path.exists()
    assert not store.db.execute('SELECT 1 FROM adapter_verifications').fetchone()
