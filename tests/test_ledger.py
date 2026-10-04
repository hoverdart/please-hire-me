import csv
import io
import json
from datetime import datetime, timezone

import pytest

from hireme.discovery import posting
from hireme.ledger import export_csv, summary
from hireme.util import digest


def application(store, job, timestamp, state='confirmed'):
    store.db.execute('''INSERT INTO applications
        (id,job_id,company_key,state,package,hash,created,updated,attempted)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        (digest(job['id']), job['id'], store.company(job['company']), state,
         '{"answers":[{"value":"Private candidate answer"}]}', 'fixture', timestamp, timestamp, timestamp))


@pytest.mark.parametrize(('day', 'timestamps', 'expected'), [
    # New York's spring clock change makes this local day 23 hours long.
    ('2026-03-08T12:00:00+00:00', ['2026-03-08T04:59:59+00:00', '2026-03-08T05:00:00+00:00',
                                 '2026-03-09T03:59:59+00:00', '2026-03-09T04:00:00+00:00'], 2),
    # The autumn clock change makes this local day 25 hours long.
    ('2026-11-01T12:00:00+00:00', ['2026-11-01T03:59:59+00:00', '2026-11-01T04:00:00+00:00',
                                 '2026-11-02T04:59:59+00:00', '2026-11-02T05:00:00+00:00'], 2),
])
def test_summary_counts_local_day_across_clock_changes(store, day, timestamps, expected):
    store.update_settings({'timezone': 'America/New_York'})
    for index, timestamp in enumerate(timestamps):
        job = posting(f'https://jobs.lever.co/clock/req-{index}', f'Clock {index}', 'Intern', 'US', 'fixture')
        store.upsert_job(job); application(store, job, timestamp)
        store.db.execute('INSERT INTO model_requests(timestamp,run_id,provider) VALUES(?,?,?)',
                         (timestamp, 'fixture-run', 'claude-cli' if index % 2 else 'openai-api'))
    assert summary(store, datetime.fromisoformat(day))['submitted_today'] == expected
    assert summary(store, datetime.fromisoformat(day))['model_requests_today'] == expected


def test_summary_counts_complete_ledger_and_deduplicates_attention(store):
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for index in range(510):
        job = posting(f'https://jobs.lever.co/large/req-{index}', f'Company {index}', 'Intern', 'US', 'fixture')
        store.upsert_job(job); application(store, job, stamp)
    assert len(store.snapshot()['jobs']) == 500
    result = summary(store)
    assert result['job_count'] == result['submitted_today'] == 510
    store.block(job['id'], 'unknown_fact')
    store.db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,0)', ('question', job['id'], job['host'], 'Question', '[]', 'unknown_fact'))
    store.db.execute("UPDATE applications SET state='unknown' WHERE job_id=?", (job['id'],))
    store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)', ('account', 'https://jobs.lever.co', 'Company', 'uncertain', stamp))
    assert summary(store)['attention_count'] == 2


def test_export_complete_unicode_csv_without_answers_and_neutralizes_formulas(store):
    companies = ['Café Labs', '=HYPERLINK("https://example.invalid")', '  +SUM(1,1)', '@example', '\t=1+1']
    for index, company in enumerate(companies):
        job = posting(f'https://jobs.lever.co/export/req-{index}', company, 'Software, Research', 'New York\nRemote', 'fixture')
        store.upsert_job(job); application(store, job, '2026-01-01T00:00:00+00:00')
    data = export_csv(store)
    assert data.startswith(b'\xef\xbb\xbf') and b'Private candidate answer' not in data
    rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
    assert len(rows) == 5
    by_company = {row['Company']: row for row in rows}
    assert 'Café Labs' in by_company
    assert all("'" + company in by_company for company in companies[1:])
    assert all(row['Fit score'] == '0' for row in rows)
    assert all(row['Role'] == 'Software, Research' for row in rows)
    assert all(row['Location'] == 'New York\nRemote' for row in rows)


def test_export_cli_keeps_existing_files(store, tmp_path, capsys):
    from hireme.cli import main
    path = tmp_path / 'ledger.csv'; path.write_text('keep this file')
    assert main(['--data-dir', str(store.root), 'export-ledger', str(path)]) == 2
    assert path.read_text() == 'keep this file'
    assert 'not overwritten' in capsys.readouterr().err


def test_search_finds_unicode_and_literal_wildcards_beyond_display_cap(store):
    from hireme.ledger import search_jobs
    for index in range(510):
        job = posting(f'https://jobs.lever.co/paging/req-{index}', f'Company {index}', 'Intern', 'US', 'fixture')
        store.upsert_job(job)
        store.db.execute('UPDATE jobs SET score=90 WHERE id=?', (job['id'],))
    target = posting('https://jobs.lever.co/paging/old', 'Café 100%', 'Research_Intern', 'Remote (US)', 'fixture')
    store.upsert_job(target)
    assert target['id'] not in {row['id'] for row in store.snapshot()['jobs']}
    assert search_jobs(store, search='CAFÉ')['jobs'][0]['id'] == target['id']
    assert search_jobs(store, search='100%')['total'] == 1
    assert search_jobs(store, search='_Intern')['total'] == 1
    assert search_jobs(store, search="' OR 1=1 --")['total'] == 0
    page = search_jobs(store, offset=500)
    assert page['total'] == 511 and len(page['jobs']) == 11


def test_search_display_status_filters_keep_policy_states_unchanged(store):
    from hireme.ledger import search_jobs
    for index, reason in enumerate(['captcha_blocked', 'location_mismatch', 'company_same_day: already submitted']):
        job = posting(f'https://jobs.lever.co/status/req-{index}', f'Company {index}', 'Intern', 'US', 'fixture')
        store.upsert_job(job); store.block(job['id'], *reason.split(': ', 1))
    for status in ['blocked', 'not_match', 'waiting']:
        result = search_jobs(store, status=status)
        assert result['total'] == 1 and result['jobs'][0]['display_status'] == status
    assert store.db.execute("SELECT COUNT(*) FROM jobs WHERE status='blocked'").fetchone()[0] == 3


@pytest.mark.parametrize('changes', [{'sort': 'score; DROP TABLE jobs'}, {'status': 'bogus'}, {'offset': -1},
                                     {'limit': 0}, {'limit': 101}, {'search': 'x' * 201}])
def test_search_rejects_invalid_requests(store, changes):
    from hireme.ledger import search_jobs
    with pytest.raises(ValueError): search_jobs(store, **changes)


def test_snapshot_keeps_old_uncertain_outcomes_and_question_context_visible(store):
    for index in range(510):
        job = posting(f'https://jobs.lever.co/attention/req-{index}', f'Company {index}', 'Intern', 'US', 'fixture')
        store.upsert_job(job); application(store, job, '2026-10-03T12:00:00+00:00')
        store.db.execute('UPDATE jobs SET score=90 WHERE id=?', (job['id'],))
    held = posting('https://jobs.lever.co/attention/held', 'Held company', 'Intern', 'US', 'fixture')
    store.upsert_job(held); application(store, held, '2020-01-01T00:00:00+00:00', 'unknown')
    store.db.execute("UPDATE jobs SET status='unknown',first_seen='2020-01-01T00:00:00+00:00' WHERE id=?", (held['id'],))
    question_job = posting('https://jobs.lever.co/attention/question', 'Question company', 'Intern', 'US', 'fixture')
    store.upsert_job(question_job); store.ask(question_job['id'], question_job['host'], 'Your preference?', [])
    snapshot = store.snapshot()
    assert held['id'] in {job['id'] for job in snapshot['jobs']}
    assert question_job['id'] in {job['id'] for job in snapshot['jobs']}
    assert held['id'] in {record['job_id'] for record in snapshot['applications']}


def test_old_unresolved_accounts_and_source_failures_are_not_buried_by_recent_successes(store):
    for index in range(120):
        store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
                         (f'recent-account-{index}', 'https://jobs.lever.co', f'Company {index}', 'confirmed', '2026-10-03T00:00:00+00:00'))
        store.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                         (f'recent-source-{index}', 'ok', '2026-10-03T00:00:00+00:00', '', '{}'))
    store.db.execute('INSERT INTO employer_accounts VALUES(?,?,?,?,?)',
                     ('old-held-account', 'https://jobs.lever.co', 'Held company', 'uncertain', '2020-01-01T00:00:00+00:00'))
    store.db.execute('INSERT INTO sources VALUES(?,?,?,?,?)',
                     ('old-failed-source', 'error', '2020-01-01T00:00:00+00:00', 'Synthetic source failure', '{}'))
    snapshot = store.snapshot(include_packages=False)
    assert len(snapshot['employer_accounts']) == len(snapshot['sources']) == 100
    assert snapshot['employer_accounts'][0]['id'] == 'old-held-account'
    assert snapshot['sources'][0]['id'] == 'old-failed-source'
    assert summary(store)['attention_count'] == 1
