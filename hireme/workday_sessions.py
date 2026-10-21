"""Private continuation of a verified NVIDIA browser sign-in.

Chromium drops session-only cookies when a persistent context is relaunched.
This cache preserves their native attributes within one private browser profile;
it never replaces the fresh employer owner read required by the controller.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import replace
from urllib.parse import urlsplit

from .util import Blocked, atomic_json, digest
from .workday_profiles import (NVIDIA, NVIDIA_SITE, PROFILE_READ, bind_session,
                               native_profile_read, owner_hash, profile_binding)

VERSION = 1
MAX_AGE_SECONDS = 12 * 60 * 60
# Same-context native owner proof observed on 2026-10-21: exact tenant host,
# root path, Secure/HttpOnly, session expiry -1; no cookie values are fixtures.
COOKIE_SITES = {'CALYPSO_CSRF_TOKEN': 'Lax', 'CALYPSO_SESSION': 'Lax',
                'PLAY_SESSION': 'Lax', 'wday_vps_cookie': 'None'}
REQUIRED_COOKIES = {'CALYPSO_CSRF_TOKEN', 'CALYPSO_SESSION', 'PLAY_SESSION'}
COOKIE_KEYS = {'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'}


def _path(store):
    return store.root / 'browser' / 'workday-session-nvidia.json'


def _safe_path(store):
    path = _path(store)
    return not (store.root.is_symlink() or path.parent.is_symlink() or path.is_symlink())


def _owner(store):
    fact = store.facts().get('email', {})
    confirmed = fact.get('confirmed')
    return owner_hash(store) if type(confirmed) in (bool, int) and confirmed == 1 else None


def _cookies(values, current):
    if not isinstance(values, list) or not 3 <= len(values) <= 4:
        raise ValueError('Missing native session cookies')
    names = set()
    for cookie in values:
        if not isinstance(cookie, dict) or set(cookie) != COOKIE_KEYS:
            raise ValueError('Unknown cookie attributes')
        name = cookie['name']
        expiry = cookie['expires']
        if (not isinstance(name, str) or name not in COOKIE_SITES or name in names
                or cookie['domain'] != NVIDIA or cookie['path'] != '/'
                or cookie['httpOnly'] is not True or cookie['secure'] is not True
                or cookie['sameSite'] != COOKIE_SITES[name]
                or not isinstance(cookie['value'], str) or not 1 <= len(cookie['value']) <= 16384
                or re.search(r'[\x00-\x20\x7f]', cookie['value'])
                or type(expiry) not in (int, float) or not math.isfinite(expiry)
                or not (expiry == -1 or expiry > current)):
            raise ValueError('Native cookie scope or expiry changed')
        names.add(name)
    if not REQUIRED_COOKIES <= names:
        raise ValueError('Missing native session cookies')
    return values


def forget(store):
    """Remove only this profile's private cache after logout or invalid identity."""
    if not _safe_path(store):
        return False
    path = _path(store)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        pass
    return False


def read_profile(page):
    """Use the guarded browser transport and the newly hydrated native CSRF."""
    parsed = urlsplit(page.url)
    if (parsed.scheme != 'https' or parsed.hostname != NVIDIA or parsed.port not in (None, 443)
            or parsed.username or parsed.password):
        raise Blocked('workday_session_unverified', 'Open the dedicated NVIDIA careers browser')
    try:
        page.wait_for_function("""() => window.workday?.tenant === 'nvidia'
            && window.workday?.siteId === 'NVIDIAExternalCareerSite' && !!window.workday?.token""", timeout=15000)
        with page.expect_response(lambda response: response.url == PROFILE_READ
                                  and response.request.method == 'GET', timeout=20000) as pending:
            page.evaluate("""async url => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 15000);
                try {
                    const response = await fetch(url, {method:'GET', credentials:'same-origin',
                        headers:{accept:'application/json', 'Accept-Language':document.documentElement.lang,
                            'X-CALYPSO-CSRF-TOKEN':window.workday.token}, signal:controller.signal});
                    await response.text();
                } finally {clearTimeout(timer)}
            }""", PROFILE_READ)
        return pending.value
    except Exception:
        raise Blocked('workday_session_unverified', 'The employer sign-in could not be verified') from None


def capture(store, context, profile_response):
    """Capture only after a fresh, exact employer read identifies the applicant."""
    if not _safe_path(store) or not _owner(store):
        raise Blocked('workday_session_unverified', 'A confirmed applicant and private browser profile are required')
    try:
        session = bind_session(store, profile_response)
    except Blocked:
        request = profile_response.request
        if native_profile_read(NVIDIA, request.url, request.method,
                               getattr(request, 'post_data_buffer', None)):
            if profile_response.status in (401, 403):
                forget(store)
            elif profile_response.status == 200:
                try:
                    if isinstance(profile_response.json(), dict):
                        forget(store)
                except (ValueError, TypeError):
                    pass
        raise
    current = time.time()
    try:
        # Ignore analytics/challenge cookies. An unexpected scope for any native
        # auth cookie is rejected rather than rewritten to another domain/path.
        values = [cookie for cookie in context.cookies(['https://' + NVIDIA])
                  if cookie.get('name') in COOKIE_SITES]
        _cookies(values, current)
        atomic_json(_path(store), {'version': VERSION, 'host': NVIDIA, 'site': NVIDIA_SITE,
            'owner_hash': session.owner_hash, 'profile_binding': session.profile_binding,
            'verified_at': current, 'cookies': values})
    except (ValueError, TypeError, AttributeError, OSError):
        raise Blocked('workday_session_unverified', 'The native sign-in cookies could not be retained safely') from None
    # This binding lives only in this browser process. A profile marker survives
    # logout, whereas a native authentication-cookie change must revoke every
    # pending write even when the next applicant uses the same local profile.
    epoch=digest(sorted((cookie['name'],cookie['value'],cookie['domain'],cookie['path'])
                        for cookie in values if cookie['name'] in REQUIRED_COOKIES))
    def same_auth():
        try:
            current_values=[cookie for cookie in context.cookies(['https://'+NVIDIA])
                            if cookie.get('name') in COOKIE_SITES]
            _cookies(current_values,time.time())
            return digest(sorted((cookie['name'],cookie['value'],cookie['domain'],cookie['path'])
                for cookie in current_values if cookie['name'] in REQUIRED_COOKIES))==epoch
        except Exception:return False
    return replace(session,auth_check=same_auth)


def _cached(store):
    """Read valid private evidence without changing it or establishing authority."""
    if not _safe_path(store) or not _owner(store):
        return None
    path = _path(store)
    try:
        info = path.stat()
        if (not path.is_file() or info.st_size > 131072 or info.st_mode & 0o077
                or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
            return None
        data = json.loads(path.read_text())
        current = time.time()
        if (not isinstance(data, dict) or set(data) != {'version', 'host', 'site', 'owner_hash',
                'profile_binding', 'verified_at', 'cookies'} or type(data['version']) is not int or data['version'] != VERSION
                or data['host'] != NVIDIA or data['site'] != NVIDIA_SITE
                or data['owner_hash'] != _owner(store)
                or not data['profile_binding'] or data['profile_binding'] != profile_binding(store, NVIDIA)
                or type(data['verified_at']) not in (int, float) or not math.isfinite(data['verified_at'])
                or not 0 <= current - data['verified_at'] <= MAX_AGE_SECONDS):
            return None
        _cookies(data['cookies'], current)
        return data
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None


def cached_evidence(store):
    """Nonsecret cached owner proof; every application still needs a fresh read."""
    data = _cached(store)
    result = {'source': 'cached_native_owner_read', 'owner_matches': bool(data),
              'verified_at': None, 'expires_at': None, 'fresh_owner_read_required': True}
    if data:
        result['verified_at'] = data['verified_at']
        result['expires_at'] = min([data['verified_at'] + MAX_AGE_SECONDS] +
                                  [cookie['expires'] for cookie in data['cookies'] if cookie['expires'] != -1])
    return result


def restore(store, context):
    """Call after route guards are installed; restoration does not verify sign-in."""
    data = _cached(store)
    if data:
        try:
            context.add_cookies(data['cookies'])
        except Exception:
            return False
        return True
    return False
