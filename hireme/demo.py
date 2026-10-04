"""A disposable sample workspace; never touches the applicant's private data."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from .discovery import posting, source_result
from .store import Store
from .util import now, private_dir, write_private_blob


DEMO_READINESS = {
    'platform': 'Sample workspace', 'architecture': 'read-only', 'python': 'not connected',
    'browser_ready': True, 'provider': {'ready': False, 'message': 'No model connected in this preview.'},
    'missing': [], 'deployment': 'local', 'supported_platform': True,
}


def seed(store):
    store.update_settings({'onboarding_complete': True, 'live_enabled': False, 'timezone': 'America/New_York',
                           'target_per_day': 12, 'max_per_day': 20, 'target_per_cycle': 3,
                           'max_per_cycle': 5, 'provider': 'codex-cli',
                           'company_aliases': {'Cedar Labs Inc': 'Cedar Labs'}})
    from .saved_views import change_view
    for name,status in (('Ready to evaluate','discovered'),('Needs an answer','blocked'),('Uncertain outcomes','unknown')):
        change_view(store,{'action':'save','name':name,'search':'','status':status,'sort':'fit'})
    entries = [
        ('Cedar Labs', 'Software Engineer Intern', 'New York, NY', 'confirmed', 92, ''),
        ('Meridian', 'New Grad Software Engineer', 'Remote (US)', 'confirmed', 87, ''),
        ('Atlas Research', 'Research Engineering Intern', 'Seattle, WA', 'blocked', 89, 'An application question needs your answer.'),
        ('Northstar', 'Software Engineer, Early Career', 'San Francisco, CA', 'unknown', 84, 'Synthetic unclear submission outcome. No real application was sent.'),
        ('Fieldwork', 'Machine Learning Intern', 'Remote (US)', 'discovered', 82, ''),
        ('Openwater', 'Developer Intern', 'New York, NY', 'discovered', 78, ''),
    ]
    stamp = now()
    from .letters import render_letter
    sample_pdf = render_letter('Sample Applicant', 'Read-only synthetic preview', 'Cedar Labs', 'Software Engineer Intern',
        'This is an invented sample letter for the read-only workspace.\n\nIt demonstrates how recorded document downloads work. These statements do not describe a real applicant.\n\nNo application was sent. Start your own private workspace to prepare documents from your confirmed experience.')
    sample_hash = hashlib.sha256(sample_pdf).hexdigest()
    write_private_blob(private_dir(store.root / 'documents') / (sample_hash + '.pdf'), sample_pdf)
    for index, (company, title, location, status, score, reason) in enumerate(entries):
        # Names and requisitions are invented. Links are omitted from the frontend in demo mode.
        job = posting(f'https://jobs.lever.co/synthetic-preview/req-{index}', company, title, location,
                      'demo', 'Invented example opportunity for the read-only workspace preview.')
        store.upsert_job(job)
        store.db.execute('UPDATE jobs SET status=?,score=?,reason=? WHERE id=?', (status, score, reason, job['id']))
        if status in ('confirmed', 'unknown'):
            store.db.execute('''INSERT INTO applications
                (id,job_id,company_key,state,package,hash,created,updated,attempted,confirmation)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (f'demo-application-{index}', job['id'], store.company(company), status,
                 json.dumps({'answers': [{'field': {'label': 'Experience'},
                    'value': 'This is a synthetic answer in the sample workspace.',
                    'provenance': {'template_id': 'demo'}}], 'documents': [{'kind': 'cover_letter', 'hash': sample_hash, 'filename': sample_hash + '.pdf', 'generated': True}] if index == 0 else []}), 'demo', stamp, stamp, stamp,
                 'Synthetic confirmation — no application was sent.' if status == 'confirmed' else 'Synthetic unclear outcome — no application was sent.'))
            if index == 0:
                qid = store.ask(job['id'], job['host'] + '|' + store.company(company),
                                'Which engineering track interests you?', ['Infrastructure', 'Product engineering'])
                store.answer_question(qid, 'Product engineering')
        if status == 'blocked':
            store.db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,0)',
                             ('demo-question', job['id'], job['host'], 'Which engineering team interests you most?',
                              '["Infrastructure", "Product engineering", "Research"]', 'An exact personal answer is needed.'))
    started = (datetime.now(timezone.utc) - timedelta(minutes=18)).isoformat(timespec='seconds')
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',
                     ('demo-batch', started, stamp, 'finished', 2, json.dumps({
                         'confirmed': 2, 'attempts': 3, 'target': 3, 'shortfall': 1,
                         'outcomes': {'confirmed': 2, 'blocked': 1}, 'reason': 'Sample batch. No applications were sent.'})))
    store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
                     ('demo-account', 'https://jobs.lever.co', 'Openwater', 'uncertain', stamp))
    source_result(store, 'ash:synthetic-preview', [])
    source_result(store, 'simplify:new-grad', error='Sample source temporarily unavailable. No live source was checked.')
    search_start = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(timespec='seconds')
    search_end = (datetime.now(timezone.utc) - timedelta(minutes=42)).isoformat(timespec='seconds')
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',
                     ('demo-discovery', search_start, search_end, 'finished', 0,
                      json.dumps({'mode': 'discovery', 'added': 2, 'sources_checked': 2, 'sources_failed': 1,
                                  'time_limit_reached': False, 'reason': 'Sample search. No live sources were contacted.'})))


def run(repo: Path, port=8767, open_browser=False):
    from .server import serve
    with TemporaryDirectory(prefix='hireme-preview-') as directory:
        root = Path(directory) / 'workspace'
        store = Store(root)
        try: seed(store)
        finally: store.close()
        serve(root, repo, port, demo=True, open_browser=open_browser)
