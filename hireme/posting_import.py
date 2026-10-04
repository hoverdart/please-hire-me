"""Validate spreadsheet postings completely before an atomic metadata import."""
import csv
import io
import hashlib

from .discovery import posting

MAX_BYTES = 1024 * 1024
MAX_ROWS = 500
HEADERS = {'company': 'company', 'role': 'title', 'title': 'title',
           'application url': 'url', 'official application url': 'url', 'url': 'url',
           'location': 'location', 'posting text': 'description', 'description': 'description'}


def parse_postings(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('Choose a CSV up to 1 MiB with at most 500 postings')
    try: text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('Save the spreadsheet as a UTF-8 comma-separated CSV, then try again.') from None
    if '\x00' in text: raise ValueError('Choose a text CSV, rather than an Excel workbook or other binary file.')
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    rows, seen, errors = [], set(), []
    try:
        header = next(reader, [])
        fields = [HEADERS.get(name.strip().casefold()) for name in header]
        known = [name for name in fields if name]
        if len(known) != len(set(known)):
            raise ValueError('Use one column per field; duplicate Company, Role or URL columns are ambiguous.')
        if not {'company', 'title', 'url'} <= set(known):
            raise ValueError('CSV needs Company, Role and Application URL columns. Location and Posting text are optional.')
        for values in reader:
            if not any(value.strip() for value in values): continue
            if len(rows) + len(errors) >= MAX_ROWS:
                raise ValueError('Import at most 500 postings at a time. Split this spreadsheet into smaller CSVs.')
            line = reader.line_num
            try:
                if len(values) != len(header): raise ValueError('The number of cells does not match the header.')
                values = {key: value.strip() for key, value in zip(fields, values) if key}
                if any(not values[key] for key in ('company','title','url')):
                    raise ValueError('Company, Role and Application URL need a value.')
                for key, limit in (('company',200),('title',500),('url',4000),('location',1000),('description',100000)):
                    if len(values.get(key,'')) > limit: raise ValueError(f'{key.title()} is too long.')
                try:
                    job = posting(values['url'], values['company'], values['title'], values.get('location',''), 'user_csv', values.get('description',''))
                except ValueError:
                    raise ValueError('Use an official HTTPS application link from a supported hiring service or employer portal, without login credentials.') from None
                if job['url'] in seen: raise ValueError('This application URL is already included in the CSV.')
                seen.add(job['url']); rows.append(job)
            except ValueError as error:
                errors.append(f'Line {line}: {error}')
        if errors:
            detail = '\n'.join(errors[:20])
            if len(errors) > 20: detail += f'\n{len(errors)-20} more rows need correction.'
            raise ValueError('No postings were saved. Fix these rows and check again:\n' + detail)
        if not rows: raise ValueError('The CSV has no postings. Add at least one row below the header.')
        return rows
    except csv.Error:
        raise ValueError('Cannot read this CSV. Save a UTF-8 comma-separated file with properly quoted cells.') from None


def preview_postings(store, data):
    rows = parse_postings(data)
    existing = sum(bool(store.db.execute('SELECT 1 FROM jobs WHERE url=?', (row['url'],)).fetchone()) for row in rows)
    return {'total': len(rows), 'new': len(rows)-existing, 'existing': existing,
            'hash': hashlib.sha256(data).hexdigest(),
            'sample': [{key: row[key] for key in ('company','title','location')} for row in rows[:10]]}


def import_postings(store, data, expected_hash):
    rows = parse_postings(data)
    if not isinstance(expected_hash, str) or expected_hash != hashlib.sha256(data).hexdigest():
        raise ValueError('This CSV does not match the checked preview. Check the file again before adding postings.')
    with store.transaction():
        existing = sum(bool(store.db.execute('SELECT 1 FROM jobs WHERE url=?', (row['url'],)).fetchone()) for row in rows)
        for row in rows: store.upsert_job(row)
        store.event('postings_imported', 'csv', {'total': len(rows), 'new': len(rows)-existing, 'existing': existing})
    return {'total': len(rows), 'new': len(rows)-existing, 'existing': existing}
