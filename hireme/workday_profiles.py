"""Source-reviewed native implementations and machine-bound execution evidence.

Implementation availability is separate from a fresh owner-bound authenticated
session and a genuine commissioned save/upload/submission acceptance chain.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import secrets
import sqlite3
from urllib.parse import urlsplit

from .util import Blocked, atomic_json, digest


NVIDIA = 'nvidia.wd5.myworkdayjobs.com'
NVIDIA_SITE = 'NVIDIAExternalCareerSite'
PUBLIC_CLIENT_EVIDENCE = {
    'jobs_url': 'https://wd5.myworkdaycdn.com/wday/asset/candidate-experience-jobs/cx-jobs.min.js',
    'jobs_sha256': '26c8934979acc813006eba0a7527f1d2a729ae9222a479348edd2193ee4fdc2a',
    'apply_url': 'https://wd5.myworkdaycdn.com/wday/asset/candidate-experience-apply-flow/candidate-experience-apply-flow.min.js',
    'apply_sha256': 'f574202edc509dcaa8a93e5f9e90db7c6a90707d8c2d8bc83cfb95816f2e7b2f',
    'proof_scope': 'public_client_reads_only',
}
# Explicit overrides support closed fixture profiles and disabling a lane.
# Database flags and login settings can never add a transport implementation.
REVIEWED_TRANSPORTS = {}
PROFILE_READ = f'https://{NVIDIA}/wday/calypso/cxs/candidatehome/nvidia/{NVIDIA_SITE}/userprofile'


def owner_hash(store):
    value = store.facts().get('email', {}).get('value')
    return digest(value.strip().casefold()) if isinstance(value, str) and value.strip() else None


def native_profile_read(host, url, method, payload=None):
    return host == NVIDIA and method == 'GET' and url == PROFILE_READ and payload in (None, b'', '')


def profile_binding(store, host):
    """Read only; missing/restored browser profiles never receive a new marker."""
    marker = store.root/'browser'/'workday-binding.json'
    if marker.is_symlink() or marker.parent.is_symlink() or store.root.is_symlink():
        return None
    try:
        if marker.stat().st_size > 16384 or marker.stat().st_mode & 0o077:
            return None
        data = json.loads(marker.read_text()).get(host, {})
        value, owner = data.get('binding'), data.get('owner_hash')
        if (not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                or owner != owner_hash(store) or not owner):
            return None
        return digest([host, owner, value])
    except (OSError, ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class SessionBinding:
    host: str
    owner_hash: str
    profile_binding: str
    auth_check: object = None

    def current(self, store):
        return (self.owner_hash == owner_hash(store) and self.profile_binding == profile_binding(store,self.host)
                and (self.auth_check is None or self.auth_check() is True))


def bind_session(store, response):
    """Explicitly bind a reviewed authenticated GET, never a signed-in setting."""
    request = response.request
    if not native_profile_read(NVIDIA, request.url, request.method, getattr(request, 'post_data_buffer', None)):
        raise Blocked('workday_session_unverified', 'The authenticated profile read is unrecognized')
    try:
        data = response.json()
        email = data.get('emailAddress')
        if (response.status != 200 or not isinstance(email, str) or data.get('errors')
                or digest(email.strip().casefold()) != owner_hash(store)):
            raise Blocked('workday_identity_mismatch', 'Sign in as the confirmed applicant')
    except (ValueError, TypeError, AttributeError):
        raise Blocked('workday_session_unverified') from None
    marker = store.root/'browser'/'workday-binding.json'
    if marker.is_symlink() or marker.parent.is_symlink():
        raise Blocked('workday_session_unverified', 'The browser profile binding is unsafe')
    try:
        existing = json.loads(marker.read_text()) if marker.exists() else {}
        if not isinstance(existing, dict):
            raise ValueError()
    except (OSError, ValueError):
        raise Blocked('workday_session_unverified', 'Review the existing browser binding') from None
    binding = profile_binding(store, NVIDIA)
    if not binding:
        existing[NVIDIA] = {'binding': secrets.token_hex(32), 'owner_hash': owner_hash(store)}
        atomic_json(marker, existing)
        binding = profile_binding(store, NVIDIA)
    if not binding:
        raise Blocked('workday_session_unverified')
    return SessionBinding(NVIDIA, owner_hash(store), binding)


def transport(host):
    if host in REVIEWED_TRANSPORTS:return REVIEWED_TRANSPORTS[host]
    if host!=NVIDIA:return None
    return _nvidia_transport()


def _nvidia_transport():
    # Lazy imports avoid the native controller's source-evidence import cycle.
    # Installation supplies source-reviewed code only. Live machine capability
    # still requires a confirmed owner/profile/step-chain-bound receipt.
    from .workday_flow import NvidiaNativeProfile
    from .workday_nvidia import NvidiaNativeDriver
    global _NVIDIA_TRANSPORT
    if _NVIDIA_TRANSPORT is None:_NVIDIA_TRANSPORT=NvidiaNativeProfile(NvidiaNativeDriver)
    return _NVIDIA_TRANSPORT


_NVIDIA_TRANSPORT=None


def operational_evidence(store, host, version):
    profile = transport(host)
    binding, owner = profile_binding(store, host), owner_hash(store)
    if not profile or not binding or not owner:
        return []
    results = []
    for row in store.db.execute('SELECT * FROM adapter_verifications WHERE tenant=? AND version=?', (host, version)):
        if row['signature'] != profile.signature:
            continue
        app = store.db.execute('SELECT a.state,j.host FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?', (row['application_id'],)).fetchone()
        if not app or app['state'] != 'confirmed' or app['host'] != host:
            continue
        event = store.db.execute("SELECT detail FROM events WHERE kind='workday_operational_verified' AND subject=? ORDER BY seq DESC LIMIT 1", (row['application_id'],)).fetchone()
        try:
            evidence = json.loads(event['detail']) if event else {}
            if (evidence.get('profile_binding') == binding and evidence.get('owner_hash') == owner
                    and evidence.get('flow_signature') == profile.signature and evidence.get('version') == version
                    and evidence.get('proof_scope') == 'live'
                    and re.fullmatch(r'[a-f0-9]{64}', str(evidence.get('receipt_hash', '')))):
                from .workday_state import check_receipt_chain
                check_receipt_chain(store,row['application_id'],evidence)
                results.append(dict(row))
        except (TypeError,ValueError,AttributeError,sqlite3.Error):
            continue
    return results
