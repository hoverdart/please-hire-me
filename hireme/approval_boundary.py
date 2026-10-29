"""Revalidate applicant authority immediately before a reviewed write leaves.

This helper grants no transport permission. Each ATS still owns its exact
request matcher and acknowledgement decoder. An already transmitted request
must retain its intent and outcome even if the applicant subsequently pauses.
"""
from __future__ import annotations

import json
import time

from .util import Blocked, digest


SETTINGS = ('live_enabled', 'onboarding_complete', 'roles', 'seniority', 'locations',
            'summer_2027_locations', 'school_locations', 'min_annual_usd',
            'min_hourly_usd', 'max_years_required', 'min_fit_score', 'skip_companies',
            'interview_companies', 'company_aliases', 'max_per_company',
            'company_cooldown_days', 'max_per_day', 'timezone', 'headless',
            'human_verification_wait_seconds', 'human_verification_handoff', 'remote_drafts', 'employer_accounts',
            'contextual_preferences', 'signed_in_portals', 'tailored_writing', 'cover_letters', 'gmail_prior_applications', 'gmail_confirmed_aliases')


def source_fingerprint(store):
    """Only source identities/revisions and digests; never an audit of values."""
    sources = {'facts': digest(store.facts())}
    for table, columns in (
        ('answers', 'id,revision'), ('documents', 'kind,hash,filename'),
        ('materials', 'id,revision,confirmed'), ('templates', 'id,revision')):
        sources[table] = sorted(tuple(row) for row in store.db.execute('SELECT '+columns+' FROM '+table))
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='confirmed_history'").fetchone():
        sources['history'] = sorted(tuple(row) for row in store.db.execute('SELECT * FROM confirmed_history'))
    return digest(sources)


class ApprovalBoundary:
    def __init__(self, store, job, package=None, *, settings=(), deadline=None,
                 profile_binding=None, require_live=True, application_id=None,reserve_retry=True):
        self.store, self.job, self.package = store, dict(job), package
        self.generation = store.control_generation()
        current = store.settings()
        self.settings = {key: current.get(key) for key in dict.fromkeys((*SETTINGS, *settings))}
        self.sources = source_fingerprint(store)
        self.package_hash = digest(package) if package is not None else None
        self.deadline, self.profile_binding = deadline, profile_binding
        self.binding = profile_binding() if profile_binding is not None else None
        self.require_live = require_live
        self.application_id = application_id
        row = store.db.execute('SELECT payload FROM jobs WHERE id=?', (job['id'],)).fetchone()
        self.job_hash = digest(json.loads(row[0])) if row else None
        self.check()
        if reserve_retry and require_live and package is not None:
            from .retry_authorization import check_package
            check_package(store,job,package,claim=True)

    def check(self, *, application_id=None):
        self.store.checkpoint()
        current = self.store.settings()
        if (self.store.control_generation() != self.generation
                or self.require_live and not current['live_enabled']
                or any(current.get(key) != value for key, value in self.settings.items())):
            raise Blocked('paused', 'Application approval boundaries changed before transport')
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise Blocked('cycle_timeout', 'The approved write expired')
        self.store.check_job_decision(self.job['id'])
        row = self.store.db.execute('SELECT payload FROM jobs WHERE id=?', (self.job['id'],)).fetchone()
        if (digest(json.loads(row[0])) if row else None) != self.job_hash:
            raise Blocked('application_changed', 'The approved posting changed')
        if self.package is not None and digest(self.package) != self.package_hash:
            raise Blocked('application_changed', 'The approved application package changed')
        if self.package is not None:
            from .retry_authorization import check_package
            check_package(self.store,self.job,self.package)
        if self.package is not None and self.package.get('facts_hash') != digest(self.store.facts()):
            raise Blocked('facts_changed')
        if source_fingerprint(self.store) != self.sources:
            raise Blocked('stale_answer', 'Approved sources changed before transport')
        if self.profile_binding is not None and self.profile_binding() != self.binding:
            raise Blocked('account_blocked', 'The approved browser profile changed')
        if self.application_id is not None and application_id not in (None,self.application_id):
            raise Blocked('application_changed','The current application reservation changed')
        from .policy import eligible
        eligible(self.job,current,self.store.facts())
        self.store._check_budget(self.job,current,
            application_id=self.application_id if application_id is None else application_id)
        if self.require_live:
            from .prior_applications import require_recent_check
            require_recent_check(self.store,self.job)
        return True
