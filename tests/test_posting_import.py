import csv
import io

import pytest

from hireme.ledger import export_csv
from hireme.posting_import import parse_postings, preview_postings, import_postings


def csv_data(rows, header=('Company','Role','Application URL','Location','Posting text')):
    output = io.StringIO(newline=''); writer = csv.writer(output)
    writer.writerow(header); writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


def test_preview_and_import_preserve_attempted_identity_and_durable_history(store, job, package):
    aid = store.prepare(job, package); store.begin_submit(aid); store.finish(aid, 'unknown')
    before = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    first = store.db.execute('SELECT first_seen FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
    data = csv_data([['Acme', 'Updated role', job['url']+'/apply?utm_source=fixture', 'US', 'Quoted, multiline\nPython context'],
                     ['Café Labs', 'Engineering Intern', 'https://jobs.lever.co/synthetic/second', '', '']])
    changes = store.db.total_changes
    result = preview_postings(store, data)
    assert result['total'] == 2 and result['new'] == 1 and result['existing'] == 1
    assert store.db.total_changes == changes and store.settings()['live_enabled']
    imported = import_postings(store, data, result['hash'])
    assert imported == {'total':2, 'new':1, 'existing':1}
    row = store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
    assert row['status'] == 'unknown' and row['first_seen'] == first and row['title'] == 'Updated role'
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == before
    assert store.settings()['live_enabled'] and not store.db.execute('SELECT * FROM model_requests').fetchone()
    assert import_postings(store, data, result['hash']) == {'total':2, 'new':0, 'existing':2}


def test_exported_ledger_can_be_imported_without_claiming_its_status_or_score(store, job):
    store.db.execute("UPDATE jobs SET status='blocked',score=97,reason='company_blocked' WHERE id=?", (job['id'],))
    data = export_csv(store)
    if isinstance(data, str): data = data.encode('utf-8')
    parsed = parse_postings(data)
    assert parsed[0]['company'] == 'Acme' and 'status' not in parsed[0] and 'score' not in parsed[0]
    preview = preview_postings(store, data); import_postings(store, data, preview['hash'])
    row = store.db.execute('SELECT status,score,reason FROM jobs WHERE id=?', (job['id'],)).fetchone()
    assert tuple(row) == ('blocked',97,'company_blocked')


@pytest.mark.parametrize('data, message', [
    (b'\xff', 'UTF-8'), (b'\x00binary', 'binary'), (b'x'*(1024*1024+1), '1 MiB'),
    (b'', '1 MiB'), (b'Company;Role;Application URL\n', 'columns'),
    (csv_data([], ('Company','Role','Title','URL')), 'duplicate'),
    (csv_data([]), 'no postings'),
    (csv_data([['Acme','Role','https://jobs.lever.co/acme/one','',''], ['Bad','Role','http://jobs.lever.co/bad/two','','']]), 'Line 3'),
    (csv_data([['Acme','Role','https://unknown.invalid/job','','']]), 'official HTTPS'),
    (csv_data([['Acme','Role','https://secret:password@jobs.lever.co/acme/job','','']]), 'without login credentials'),
    (csv_data([['','Role','https://jobs.lever.co/acme/job','','']]), 'need a value'),
    (csv_data([['Acme','Role','https://jobs.lever.co/acme/job']]), 'cells'),
    (csv_data([['x'*201,'Role','https://jobs.lever.co/acme/job','','']]), 'too long'),
    (b'Company,Role,URL\n"unterminated,role,url', 'quoted'),
])
def test_invalid_csv_never_partially_imports(store, data, message):
    before = store.snapshot(); changes = store.db.total_changes
    with pytest.raises(ValueError, match=message): import_postings(store, data, 'unreviewed')
    assert store.snapshot() == before and store.db.total_changes == changes


def test_canonical_duplicates_and_row_limit_are_rejected(store):
    row = ['Acme','Role','https://jobs.lever.co/acme/job','','']
    with pytest.raises(ValueError, match='already included'):
        preview_postings(store, csv_data([row, [*row[:2], row[2]+'/apply?utm_source=duplicate', '', '']]))
    rows = [['Synthetic', 'Role', f'https://jobs.lever.co/synthetic/{index}', '', ''] for index in range(501)]
    with pytest.raises(ValueError, match='at most 500'): preview_postings(store, csv_data(rows))


def test_changed_file_is_rejected_and_database_failure_rolls_back_whole_import(store, monkeypatch):
    rows = [['Synthetic','Role',f'https://jobs.lever.co/synthetic/{index}','',''] for index in range(2)]
    data = csv_data(rows); preview = preview_postings(store, data)
    before = store.snapshot()
    with pytest.raises(ValueError, match='checked preview'): import_postings(store, data+b'\n', preview['hash'])
    original = store.upsert_job; calls = []
    def failing(job):
        calls.append(job)
        if len(calls) == 2: raise RuntimeError('Synthetic storage failure')
        return original(job)
    monkeypatch.setattr(store, 'upsert_job', failing)
    with pytest.raises(RuntimeError): import_postings(store, data, preview['hash'])
    assert store.snapshot() == before
