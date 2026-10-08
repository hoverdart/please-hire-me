"""Optional applicant-owned job platforms, with independently verified capabilities.

Enabling a connection never manufactures transport/access verification. Flow records
are installed by release validation, not by dashboard settings or a successful login.
"""
from __future__ import annotations

import json
import re
import shutil
import secrets
from urllib.parse import urlsplit

from .util import Blocked, canonical_url, digest, now, private_dir, atomic_json

PLATFORMS = {
    'handshake': {'name': 'Handshake', 'host': 'app.joinhandshake.com',
                  'login_url': 'https://app.joinhandshake.com/access',
                  'search_url': 'https://app.joinhandshake.com/job-search',
                  'saved_url': 'https://app.joinhandshake.com/saved-jobs'},
    'workatastartup': {'name': 'Work at a Startup', 'host': 'www.workatastartup.com',
                       'login_url': 'https://www.workatastartup.com/application',
                       'search_url': 'https://www.workatastartup.com/companies',
                       'saved_url': 'https://www.workatastartup.com/companies?tab=saved'},
}
VERSION = 1
MAX_PAGES = 5
MAX_JOBS = 100
CAPABILITIES = ('discovery', 'inspection', 'preparation', 'submission', 'verification')


def platform(value):
    if value not in PLATFORMS:
        raise ValueError('Choose Handshake or Work at a Startup')
    return PLATFORMS[value]


def profile_path(store, connection_id):
    platform(connection_id)
    directory = private_dir(private_dir(store.root / 'platform-browser') / connection_id)
    marker = directory / 'connection-binding.json'
    if not marker.exists():
        atomic_json(marker, {'binding': secrets.token_hex(32)})
    if not profile_binding(store, connection_id):
        raise ValueError('Connection profile identity cannot be read')
    return directory


def profile_binding(store, connection_id):
    marker = store.root / 'platform-browser' / connection_id / 'connection-binding.json'
    if marker.is_symlink() or marker.parent.is_symlink() or marker.parent.parent.is_symlink():
        return None
    try:
        value = json.loads(marker.read_text())['binding']
        return value if isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value) else None
    except (OSError,ValueError,KeyError,TypeError):
        return None


def configuration(store, connection_id):
    platform(connection_id)
    row = store.db.execute('SELECT * FROM platform_connections WHERE id=?', (connection_id,)).fetchone()
    return dict(row) if row else {'id': connection_id, 'enabled': 0, 'discovery_enabled': 0,
        'native_apply_enabled': 0, 'checked': None, 'session_state': 'not_connected', 'detail': ''}


def flows(store, connection_id, capability):
    platform(connection_id)
    if capability not in CAPABILITIES:
        raise ValueError('Unknown connection capability')
    result = []
    for row in store.db.execute('SELECT * FROM connection_flows WHERE connection_id=? AND capability=? AND version=?',
                               (connection_id, capability, VERSION)):
        try:
            evidence = json.loads(row['evidence'])
            if not isinstance(evidence, dict) or evidence.get('version') != VERSION:
                continue
            # Fixture-only evidence and mere session checks cannot enable production writes.
            if evidence.get('access_authorized') is not True or evidence.get('live_verified') is not True or evidence.get('proof_scope') != 'live':
                continue
            if evidence.get('connection_id') != connection_id or not re.fullmatch(r'[a-f0-9]{64}', str(evidence.get('evidence_hash', ''))):
                continue
            binding = profile_binding(store, connection_id)
            if not binding or evidence.get('profile_binding') != binding:
                continue
            result.append({**dict(row), 'evidence': evidence})
        except (ValueError, TypeError):
            continue
    return result


def status(store, connection_id):
    spec = platform(connection_id)
    config = configuration(store, connection_id)
    capabilities = {}
    for capability in CAPABILITIES:
        verified = flows(store, connection_id, capability)
        message = ('Available for validated flows' if verified else
            'Platform access and flow validation required' if capability == 'discovery' else
            'Native flow validation required before automatic applications')
        if connection_id == 'handshake' and capability == 'discovery' and not verified:
            message = 'Automatic search unavailable: Handshake restricts bulk collection. An authorized access route is required.'
        capabilities[capability] = {'available': bool(verified), 'message': message,
            'version': VERSION, 'verified_flows': len(verified)}
    return {**config, 'enabled': bool(config['enabled']), 'discovery_enabled': bool(config['discovery_enabled']),
        'native_apply_enabled': bool(config['native_apply_enabled']), 'name': spec['name'],
        'login_url': spec['login_url'], 'connect_command': f'hireme connection {connection_id} connect',
        'capabilities': capabilities, 'session_ready': config['session_state'] == 'ready'}


def list_connections(store):
    return [status(store, connection_id) for connection_id in PLATFORMS]


def configure(store, connection_id, data):
    platform(connection_id)
    keys = {'enabled', 'discovery_enabled', 'native_apply_enabled'}
    if not isinstance(data, dict) or not data or set(data) - keys or any(type(v) is not bool for v in data.values()):
        raise ValueError('Provide boolean connection controls')
    from .store import worker_lock
    with worker_lock(store.root), store.transaction():
        store.db.execute('INSERT OR IGNORE INTO platform_connections(id) VALUES(?)', (connection_id,))
        for key, value in data.items():
            store.db.execute(f'UPDATE platform_connections SET {key}=? WHERE id=?', (int(value), connection_id))
        store.event('connection_settings', connection_id, data)
    return status(store, connection_id)


def session_result(store, connection_id, ready, detail=''):
    platform(connection_id)
    store.db.execute('INSERT OR IGNORE INTO platform_connections(id) VALUES(?)', (connection_id,))
    store.db.execute('UPDATE platform_connections SET session_state=?,checked=?,detail=? WHERE id=?',
                     ('ready' if ready else 'sign_in_required', now(), detail[:500], connection_id))


def connect(store, connection_id):
    """Interactive sign-in on the machine running the worker; no credential copying."""
    from .store import worker_lock
    from playwright.sync_api import sync_playwright
    spec = platform(connection_id)
    with worker_lock(store.root), worker_lock(store.root, 'browser'), sync_playwright() as playwright:
        options = {'headless': False, 'accept_downloads': False}
        channel = store.settings()['browser_channel']
        if channel == 'chrome':
            options['channel'] = 'chrome'
        elif channel == 'system-chromium':
            options['executable_path'] = shutil.which('chromium') or shutil.which('chromium-browser')
            if not options['executable_path']:
                raise ValueError('Install system Chromium first')
        context = playwright.chromium.launch_persistent_context(str(profile_path(store, connection_id)), **options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(spec['login_url'], wait_until='domcontentloaded')
            print('Sign in yourself. On a Pi, use its desktop through Pi Connect. Press Enter when finished.')
            input()
        finally:
            context.close()
    # Login completion is a claim until an independent read verifies the session.
    session_result(store, connection_id, False, 'Sign-in window closed. Check the connection to verify the session.')
    return check(store, connection_id)


def start_connect(store, connection_id):
    """Dashboard sign-in owns a visible dedicated window, never terminal input."""
    import threading
    from .store import Store, worker_lock
    from .platform_browser import PlatformBrowser
    platform(connection_id)
    with worker_lock(store.root), store.transaction():
        if configuration(store, connection_id)['session_state'] == 'connecting':
            raise ValueError('A sign-in window is already open. Finish or close it first.')
        store.db.execute('INSERT OR IGNORE INTO platform_connections(id) VALUES(?)', (connection_id,))
        store.db.execute("UPDATE platform_connections SET session_state='connecting',checked=?,detail=? WHERE id=?",
            (now(), 'Finish sign-in in the dedicated browser, then close that window and check the connection.', connection_id))
    root = store.root
    def run():
        worker = Store(root)
        try:
            with worker_lock(root), worker_lock(root, 'browser'):
                with PlatformBrowser(worker, connection_id, interactive=True) as browser:
                    browser.page.goto(platform(connection_id)['login_url'], wait_until='domcontentloaded')
                    import time
                    deadline = time.monotonic() + 600
                    while not browser.page.is_closed() and time.monotonic() < deadline:
                        browser.page.wait_for_timeout(250)
                session_result(worker, connection_id, False, 'Sign-in window closed. Check the connection to verify it.')
        except Exception:
            session_result(worker, connection_id, False, 'Could not complete the sign-in window. Close other worker browsers or use the terminal connect command on the Pi desktop.')
        finally:
            worker.close()
    try:
        threading.Thread(target=run, name='hireme-platform-sign-in', daemon=True).start()
    except Exception:
        session_result(store, connection_id, False, 'Could not open sign-in. Try connecting again on the worker desktop.')
        raise
    return {'started': True, 'message': 'Sign in in the dedicated browser window. Close it when finished, then check the connection.'}


def disconnect(store, connection_id):
    from .store import worker_lock
    platform(connection_id)
    with worker_lock(store.root), worker_lock(store.root, 'browser'), store.transaction():
        directory = store.root / 'platform-browser' / connection_id
        if directory.is_symlink() or directory.parent.is_symlink():
            raise ValueError('Unsafe connection browser directory')
        if directory.exists():
            shutil.rmtree(directory)
        store.db.execute('INSERT OR IGNORE INTO platform_connections(id) VALUES(?)', (connection_id,))
        store.db.execute("UPDATE platform_connections SET enabled=0,discovery_enabled=0,native_apply_enabled=0,session_state='not_connected',checked=?,detail='' WHERE id=?", (now(), connection_id))
        store.db.execute('DELETE FROM platform_documents WHERE connection_id=?', (connection_id,))
        store.event('connection_disconnected', connection_id, {})
    return status(store, connection_id)


def check(store, connection_id):
    from .store import worker_lock
    from .platform_browser import PlatformBrowser
    platform(connection_id)
    with worker_lock(store.root), worker_lock(store.root, 'browser'):
        with PlatformBrowser(store, connection_id) as browser:
            ready = browser.check_session()
            session_result(store, connection_id, ready, '' if ready else 'Sign in in the dedicated browser on the worker machine.')
    return status(store, connection_id)


def platform_listing(url):
    if not isinstance(url,str) or not 1<=len(url)<=4000:
        raise ValueError('Use a specific platform job link')
    parsed = urlsplit(canonical_url(url))
    if parsed.hostname == 'app.joinhandshake.com':
        match = re.fullmatch(r'/(?:stu/)?(?:jobs|job-search)/(\d+)/?', parsed.path)
        if match:
            return 'handshake', match[1], f'https://app.joinhandshake.com/jobs/{match[1]}'
    if parsed.hostname in {'workatastartup.com', 'www.workatastartup.com'}:
        match = re.fullmatch(r'/jobs/(\d+)/?', parsed.path)
        if match:
            return 'workatastartup', match[1], f'https://www.workatastartup.com/jobs/{match[1]}'
    raise ValueError('Use a specific Handshake or Work at a Startup job link')


def import_listing(store, url, company, title, location='', description='', external_url=None, requirements=None):
    from .discovery import posting
    connection_id, native_id, listing_url = platform_listing(url)
    if not all(isinstance(x, str) for x in (company, title, location, description)) or not company.strip() or not title.strip():
        raise ValueError('Provide the company and role')
    if len(description) > 100000 or len(company) > 200 or len(title) > 500 or len(location) > 1000:
        raise ValueError('Posting metadata is too long')
    if requirements is not None and (not isinstance(requirements,list) or len(requirements)>50 or any(not isinstance(r,dict) for r in requirements)):
        raise ValueError('Invalid platform requirements')
    if external_url:
        # Only verified adapter reads supply external_url; the public import API does not accept it.
        _external_requisition(external_url)
        job = posting(external_url, company, title, location, connection_id, description, requirements=requirements or [])
    else:
        job = {'id': digest(listing_url), 'url': listing_url, 'host': platform(connection_id)['host'],
            'company': company, 'title': title, 'location': location, 'description': description,
            'source': connection_id, 'connection_id': connection_id, 'platform_job_id': native_id,
            'application_kind': 'introduction' if connection_id == 'workatastartup' else 'native_documents',
            'requirements': requirements or []}
    conflict = None
    with store.transaction():
        previous = store.db.execute('SELECT job_id FROM job_origins WHERE connection_id=? AND platform_job_id=?', (connection_id, native_id)).fetchone()
        if previous:
            previous_url = store.db.execute('SELECT url FROM jobs WHERE id=?', (previous['job_id'],)).fetchone()[0]
            if previous_url != job['url']:
                conflict = (previous['job_id'], 'This listing now has a different application destination. Review both records before applying.')
        existing = store.db.execute('SELECT id,company,title FROM jobs WHERE url=?', (job['url'],)).fetchone()
        if not conflict and existing and (store.company(existing['company']) != store.company(company) or ' '.join(existing['title'].casefold().split()) != ' '.join(title.casefold().split())):
            conflict = (existing['id'], 'The canonical requisition conflicts with the saved company or role. Review the identity before combining listings.')
        if conflict:
            # Persist the candidate without changing identity, source wording or prior evidence.
            store.db.execute('INSERT INTO connection_conflicts VALUES(?,?,?,?,?,?) ON CONFLICT(connection_id,platform_job_id) DO UPDATE SET job_id=excluded.job_id,candidate=excluded.candidate,detail=excluded.detail,observed=excluded.observed',
                (connection_id,native_id,conflict[0],json.dumps({**job,'listing_url':listing_url}),conflict[1],now()))
            from .job_holds import hold, safe_state
            saved = json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(conflict[0],)).fetchone()[0])
            if safe_state(store,conflict[0]):
                hold(store,saved,'connection_identity_review',stage='discovery')
                store.block(conflict[0],'connection_identity_review',conflict[1])
        else:
            key = store.upsert_job(job)
            store.db.execute('INSERT INTO job_origins VALUES(?,?,?,?,?) ON CONFLICT(connection_id,platform_job_id) DO UPDATE SET observed=excluded.observed',
                             (connection_id, native_id, key, listing_url, now()))
            store.db.execute('DELETE FROM connection_conflicts WHERE connection_id=? AND platform_job_id=?',(connection_id,native_id))
    if conflict:
        raise Blocked('connection_identity_review',conflict[1])
    return key


def _external_requisition(url):
    """A reviewed employer link must identify a requisition, never a whole board."""
    parsed = urlsplit(canonical_url(url))
    host = parsed.hostname or ''
    parts = parsed.path.strip('/').split('/')
    valid = False
    if host in {'jobs.lever.co','jobs.eu.lever.co','jobs.ashbyhq.com','jobs.smartrecruiters.com'}:
        valid = len(parts) == 2 and all(parts) and parts[1] not in {'apply','jobs','search'}
    elif host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}:
        valid = len(parts)==3 and parts[1]=='jobs' and parts[2].isdigit()
    elif host == 'apply.workable.com':
        valid = len(parts)==3 and parts[1]=='j' and bool(parts[2])
    else:
        # Other existing portals need their individually reviewed requisition route.
        valid = bool(re.search(r'/(?:job|jobs|position|positions|requisition)/[^/]+',parsed.path,re.I))
    if not valid:
        raise Blocked('connection_destination_review','The external link does not identify a supported employer requisition. Review the exact job destination.')


def annotate_jobs(store, jobs):
    if not jobs:
        return jobs
    ids = [j['id'] for j in jobs]
    origins = {}
    for row in store.db.execute('SELECT * FROM job_origins WHERE job_id IN (' + ','.join('?' for _ in ids) + ')', ids):
        origins.setdefault(row['job_id'], []).append(dict(row))
    conflicts = {}
    for row in store.db.execute('SELECT * FROM connection_conflicts WHERE job_id IN (' + ','.join('?' for _ in ids) + ')',ids):
        conflicts.setdefault(row['job_id'],[]).append({**dict(row),'candidate':json.loads(row['candidate'])})
    for job in jobs:
        job['identity_conflicts'] = conflicts.get(job['id'],[])
        payload = json.loads(job['payload']) if isinstance(job.get('payload'), str) else job.get('payload', job)
        job['origins'] = origins.get(job['id'], [])
        job['application_destination'] = job['host']
        job['requirements'] = payload.get('requirements', [])
        connection_id = payload.get('connection_id')
        job['connection_id'] = connection_id
        job['application_capability'] = status(store, connection_id)['capabilities']['submission'] if connection_id in PLATFORMS else {'available': job['host'] in {'jobs.lever.co', 'boards.greenhouse.io', 'job-boards.greenhouse.io', 'jobs.ashbyhq.com', 'jobs.eu.lever.co', 'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'}, 'message': 'Existing employer application pipeline; posting and form checks still apply'}
        if payload.get('application_kind') == 'introduction' and job.get('status') == 'confirmed':
            job['status_label'] = 'Introduction sent'
    return jobs


def discover(store, connection_id):
    from .platform_browser import PlatformBrowser
    from .discovery import source_result
    config = configuration(store, connection_id)
    if not config['enabled'] or not config['discovery_enabled']:
        raise Blocked('connection_disabled', 'Enable this connection and automatic search first.')
    if not flows(store, connection_id, 'discovery'):
        raise Blocked('connection_access_unverified', status(store, connection_id)['capabilities']['discovery']['message'])
    count = 0; failures = 0
    with PlatformBrowser(store, connection_id) as browser:
        for listing in browser.discover():
            store.checkpoint()
            try:
                import_listing(store, **listing)
                count += 1
            except (Blocked, ValueError) as error:
                failures += 1
                store.event('connection_listing_held', connection_id, {'url': listing['url'], 'reason': error.reason if isinstance(error, Blocked) else 'invalid_listing'})
    source_result(store, 'connection:' + connection_id, [])
    store.db.execute('UPDATE sources SET payload=? WHERE id=?', (json.dumps({'jobs':count,'held_listings':failures}), 'connection:' + connection_id))
    store.event('connection_discovered', connection_id, {'jobs': count, 'held_listings': failures})
    return {'jobs': count, 'held_listings': failures}


def sweep_connections(store):
    from .discovery import source_result
    from .store import worker_lock
    for connection_id in PLATFORMS:
        config = configuration(store, connection_id)
        if not config['enabled'] or not config['discovery_enabled']:
            continue
        store.checkpoint()
        try:
            with worker_lock(store.root, 'browser'):
                discover(store, connection_id)
        except Blocked as error:
            if error.reason in {'paused', 'cycle_timeout', 'model_budget_exhausted'}:
                raise
            source_result(store, 'connection:' + connection_id, error=error.reason + ': ' + error.detail)
        except Exception:
            source_result(store, 'connection:' + connection_id, error='Platform search failed. Check this connection before retrying.')
