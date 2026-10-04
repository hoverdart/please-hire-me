"""Explicit company-boundary changes; attempted evidence is never rewritten."""
from __future__ import annotations

import json

from .config import validate_settings
from .util import now, company_normalizer


def skip_company(store, job_id):
    if not isinstance(job_id, str) or not job_id or len(job_id) > 100:
        raise ValueError('Choose an existing opportunity')
    with store.transaction():
        job = store.db.execute('SELECT company FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job: raise ValueError('Opportunity not found')
        settings = store.settings()
        normalize = company_normalizer(settings['company_aliases'])
        company = job['company']
        key = normalize(company)
        existing = {normalize(name) for name in settings['skip_companies']}
        if key not in existing:
            settings = validate_settings({'skip_companies': [*settings['skip_companies'], company]}, settings)
            store.db.execute('UPDATE config SET value=? WHERE id=1', (json.dumps(settings),))
        candidates = store.db.execute("""SELECT id,company FROM jobs WHERE status IN ('discovered','blocked','prepared')
            AND NOT EXISTS(SELECT 1 FROM applications a WHERE a.job_id=jobs.id AND a.state!='prepared')""")
        ids = [row['id'] for row in candidates if normalize(row['company']) == key]
        stamp = now()
        for jid in ids:
            store.db.execute("UPDATE jobs SET status='blocked',reason='company_blocked',updated=? WHERE id=?", (stamp, jid))
            store.db.execute("DELETE FROM applications WHERE job_id=? AND state='prepared'", (jid,))
            store.db.execute('UPDATE questions SET resolved=1 WHERE job_id=?', (jid,))
        store.event('company_skipped', key, {'company': company, 'opportunities_held': len(ids)})
    store.export_config()
    return {'company': company, 'message': f'Future applications at {company} are skipped. Use Include this company again in opportunity details to remove the exclusion. Past attempts stay recorded.'}


def annotate_companies(store, jobs):
    settings = store.settings()
    normalize = company_normalizer(settings['company_aliases'])
    skipped = {normalize(name) for name in settings['skip_companies']}
    return [{**job, 'company_skipped': normalize(job['company']) in skipped} for job in jobs]


def allow_company(store, job_id):
    if not isinstance(job_id, str) or not job_id or len(job_id) > 100:
        raise ValueError('Choose an existing opportunity')
    with store.transaction():
        job = store.db.execute('SELECT company FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job: raise ValueError('Opportunity not found')
        settings = store.settings()
        normalize = company_normalizer(settings['company_aliases'])
        key = normalize(job['company'])
        remaining = [name for name in settings['skip_companies'] if normalize(name) != key]
        removed = len(settings['skip_companies']) - len(remaining)
        if removed:
            settings = validate_settings({'skip_companies': remaining}, settings)
            store.db.execute('UPDATE config SET value=? WHERE id=1', (json.dumps(settings),))
        store.event('company_allowed', key, {'company': job['company'], 'exclusions_removed': removed})
    store.export_config()
    return {'company': job['company'], 'removed': removed,
            'message': f"{job['company']} is no longer excluded. The next batch will reevaluate unattempted opportunities. Past attempts, company limits and uncertain outcomes stay recorded."}
