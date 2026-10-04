"""Bounded read-only search of the latest check for every discovery source."""
from __future__ import annotations


def search_sources(store, search='', status='all', offset=0, limit=25):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if status not in ('all', 'ok', 'error'):
        raise ValueError('Choose all, available or unavailable sources')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid source page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    conditions = []; parameters = []
    if status != 'all': conditions.append('status=?'); parameters.append(status)
    term = search.strip().casefold()
    if term:
        store.db.create_function('source_search', 2, lambda identifier, error: f'{identifier} {error}'.casefold(), deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("source_search(id,error) LIKE ? ESCAPE '\\'")
        parameters.append('%' + escaped + '%')
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    total = store.db.execute('SELECT COUNT(*) FROM sources' + where, parameters).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = store.db.execute('SELECT id,status,checked,error FROM sources' + where +
                            ' ORDER BY (status=\'error\') DESC,checked DESC,id LIMIT ? OFFSET ?', (*parameters, limit, offset))
    counts = dict(store.db.execute('SELECT status,COUNT(*) FROM sources GROUP BY status'))
    return {'sources': [dict(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit,
            'summary': {'total': sum(counts.values()), 'available': counts.get('ok', 0), 'unavailable': counts.get('error', 0)}}
