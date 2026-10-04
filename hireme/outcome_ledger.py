"""Review unresolved submission metadata without loading application packages."""
from __future__ import annotations

from .store import APPLICATION_METADATA


def search_outcomes(store, search='', status='pending', offset=0, limit=25):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if status not in ('pending', 'unknown', 'awaiting_verification'):
        raise ValueError('Choose unresolved outcomes or email verification')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid outcome page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    conditions = ["a.state IN ('unknown','awaiting_verification')"]; parameters = []
    if status != 'pending': conditions.append('a.state=?'); parameters.append(status)
    term = search.strip().casefold()
    if term:
        store.db.create_function('outcome_search', 3,
            lambda company, title, recorded: ' '.join(str(v or '') for v in (company, title, recorded)).casefold(), deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("outcome_search(j.company,j.title,a.company_key) LIKE ? ESCAPE '\\'")
        parameters.append('%' + escaped + '%')
    joined = ' FROM applications a LEFT JOIN jobs j ON j.id=a.job_id'
    where = ' WHERE ' + ' AND '.join(conditions)
    total = store.db.execute('SELECT COUNT(*)' + joined + where, parameters).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit * limit))
    columns = ','.join('a.' + column for column in APPLICATION_METADATA.split(','))
    rows = store.db.execute('SELECT ' + columns + ',j.company,j.title,j.url' + joined + where +
                            ' ORDER BY COALESCE(a.attempted,a.created),a.id LIMIT ? OFFSET ?', (*parameters, limit, offset))
    return {'applications': [dict(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}
