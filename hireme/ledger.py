"""Read-only application summaries and spreadsheet-friendly ledger exports."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .presentation import QUIET_REASONS, job_display, attention_sql

EXPORT_COLUMNS = ('Company', 'Role', 'Location', 'Status', 'Fit score', 'Application URL',
                  'First discovered', 'Last updated', 'Attempted', 'Reason')



def summary(store, at=None):
    """Count the complete ledger, including records outside dashboard page limits."""
    local = (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(store.settings()['timezone']))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    # Stored timestamps are UTC ISO strings with second resolution, like util.now().
    bounds = tuple(value.astimezone(timezone.utc).isoformat(timespec='seconds') for value in (start, end))
    counts = dict(store.db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status'))
    submitted = store.db.execute("""SELECT COUNT(*) FROM applications
        WHERE state='confirmed' AND attempted>=? AND attempted<?""", bounds).fetchone()[0]
    model_requests = store.db.execute('SELECT COUNT(*) FROM model_requests WHERE timestamp>=? AND timestamp<?', bounds).fetchone()[0]
    condition, parameters = attention_sql('j')
    from .presentation import actionable_question_sql
    question_condition,question_parameters=actionable_question_sql('q')
    attention = store.db.execute(f"""SELECT COUNT(*) FROM (
        SELECT q.job_id FROM questions q WHERE {question_condition}
        UNION SELECT j.id FROM jobs j WHERE {condition}
        UNION SELECT job_id FROM applications WHERE state IN ('unknown','awaiting_verification')
    )""", (*question_parameters,*parameters)).fetchone()[0]
    accounts = store.db.execute("SELECT COUNT(*) FROM employer_accounts WHERE state='uncertain'").fetchone()[0]
    questions = store.db.execute('SELECT COUNT(*) FROM questions q WHERE '+question_condition,question_parameters).fetchone()[0]
    held = store.db.execute(f"SELECT COUNT(*) FROM jobs j WHERE j.status='blocked' AND {condition}", parameters).fetchone()[0]
    return {'job_count': sum(counts.values()), 'status_counts': counts, 'submitted_today': submitted,
            'attention_count': attention + accounts, 'question_count': questions, 'held_count': held,
            'model_requests_today': model_requests, 'model_usage':model_usage(store,bounds), 'local_date': local.date().isoformat()}


def model_usage(store,bounds):
    """Only provider-reported tokens; missing metadata is not a zero-cost call."""
    groups={}
    for row in store.db.execute('''SELECT r.provider,m.model,m.success,m.usage FROM model_requests r
        LEFT JOIN model_request_metadata m ON m.request_id=r.id
        WHERE r.timestamp>=? AND r.timestamp<? ORDER BY r.provider,m.model''',bounds):
        key=(row['provider'],row['model'] or '')
        g=groups.setdefault(key,{'provider':key[0],'model':key[1],'requests':0,'successful':0,
            'failed':0,'outcome_unavailable':0,'measured_requests':0,'usage_unavailable':0,'tokens':{}})
        g['requests']+=1
        g['successful' if row['success']==1 else 'failed' if row['success']==0 else 'outcome_unavailable']+=1
        try:usage=json.loads(row['usage'] or '{}')
        except (ValueError,TypeError):usage={}
        valid={k:v for k,v in usage.items() if k in {'input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'} and type(v) is int and v>=0} if isinstance(usage,dict) else {}
        for k,v in valid.items():g['tokens'][k]=g['tokens'].get(k,0)+v
        measured='input_tokens' in valid and 'output_tokens' in valid
        g['measured_requests']+=int(measured);g['usage_unavailable']+=int(not measured)
    return list(groups.values())


def spreadsheet_text(value):
    """Keep untrusted posting text from becoming a formula when opened in a spreadsheet."""
    text = '' if value is None else str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def export_csv(store):
    """Export job metadata only; personal answers and credentials stay out of this file."""
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(EXPORT_COLUMNS)
    for job in store.db.execute('''SELECT j.*,a.attempted FROM jobs j
        LEFT JOIN applications a ON a.job_id=j.id ORDER BY j.first_seen DESC,j.id'''):
        try: location = json.loads(job['payload']).get('location', '')
        except (ValueError, TypeError): location = ''
        writer.writerow([spreadsheet_text(value) for value in (
            job['company'], job['title'], location, job_display(dict(job))['status_label'],
            job['score'], job['url'], job['first_seen'], job['updated'], job['attempted'], job['reason'])])
    # UTF-8 BOM helps common spreadsheet apps recognize non-ASCII employer names.
    return output.getvalue().encode('utf-8-sig')


def search_jobs(store, search='', status='all', sort='recent', offset=0, limit=50, include_packages=True):
    """Search the complete ledger with bounded pages and exact display-status semantics."""
    from .presentation import NOT_MATCH_REASONS, WAIT_REASONS
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid opportunity page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    ordering = {
        'recent': 'j.first_seen DESC,j.id',
        'fit': 'j.score DESC,j.first_seen DESC,j.id',
        'company': 'j.company COLLATE NOCASE,j.title COLLATE NOCASE,j.id',
    }
    if sort not in ordering:
        raise ValueError('Choose recent, fit, or company sorting')
    allowed = {'all', 'confirmed', 'blocked', 'unknown', 'awaiting_verification', 'discovered',
               'manually_applied', 'skipped', 'not_match', 'waiting', 'prepared', 'submitting', 'rejected', 'not_submitted'}
    if status not in allowed:
        raise ValueError('Unknown opportunity status')
    clauses = []; parameters = []
    code = "TRIM(SUBSTR(j.reason,1,CASE WHEN INSTR(j.reason,':')>0 THEN INSTR(j.reason,':')-1 ELSE LENGTH(j.reason) END))"
    if status in ('blocked', 'not_match', 'waiting'):
        reasons = sorted(QUIET_REASONS if status == 'blocked' else NOT_MATCH_REASONS if status == 'not_match' else WAIT_REASONS)
        placeholders = ','.join('?' for _ in reasons)
        clauses.append(f"j.status='blocked' AND {code} {'NOT IN' if status == 'blocked' else 'IN'} ({placeholders})")
        parameters.extend(reasons)
    elif status != 'all':
        clauses.append('j.status=?'); parameters.append(status)
    term = search.strip().casefold()
    if term:
        # Treat % and _ as literal search characters, not SQL wildcard instructions.
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        def searchable(company, title, payload):
            try: location = json.loads(payload).get('location', '')
            except (ValueError, TypeError, AttributeError): location = ''
            return f'{company} {title} {location}'.casefold()
        # Python parsing keeps location search independent of optional SQLite JSON extensions.
        store.db.create_function('search_text', 3, searchable, deterministic=True)
        clauses.append("search_text(j.company,j.title,j.payload) LIKE ? ESCAPE '\\'")
        parameters.append('%' + escaped + '%')
    where = ' WHERE ' + ' AND '.join('(' + clause + ')' for clause in clauses) if clauses else ''
    total = store.db.execute('SELECT COUNT(*) FROM jobs j' + where, parameters).fetchone()[0]
    rows = store.db.execute('SELECT j.* FROM jobs j' + where + ' ORDER BY ' + ordering[sort] + ' LIMIT ? OFFSET ?',
                            [*parameters, limit, offset])
    jobs = [{**dict(row), **job_display(dict(row))} for row in rows]
    from .company_controls import annotate_companies
    jobs = annotate_companies(store, jobs)
    from .job_holds import annotate
    jobs = annotate(store, jobs)
    applications = []
    if jobs:
        from .store import APPLICATION_METADATA
        placeholders = ','.join('?' for _ in jobs)
        applications = [dict(row) for row in store.db.execute(
            f"SELECT {'*' if include_packages else APPLICATION_METADATA} FROM applications WHERE job_id IN ({placeholders})", [job['id'] for job in jobs])]
    return {'jobs': jobs, 'applications': applications, 'total': total, 'offset': offset, 'limit': limit}
