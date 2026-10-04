"""Bounded review of employer-account metadata; credentials stay in the private vault."""
from __future__ import annotations


def search_accounts(store, search='', status='uncertain', offset=0, limit=25):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if status not in ('uncertain', 'all'):
        raise ValueError('Choose accounts needing verification or all accounts')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid employer-account page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    conditions = []; parameters = []
    if status == 'uncertain': conditions.append("state='uncertain'")
    term = search.strip().casefold()
    if term:
        store.db.create_function('account_search', 2, lambda company, origin: f'{company} {origin}'.casefold(), deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("account_search(company,origin) LIKE ? ESCAPE '\\'")
        parameters.append('%' + escaped + '%')
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    total = store.db.execute('SELECT COUNT(*) FROM employer_accounts' + where, parameters).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = store.db.execute('SELECT id,origin,company,state,updated FROM employer_accounts' + where +
        " ORDER BY (state='uncertain') DESC,updated DESC,id LIMIT ? OFFSET ?", (*parameters, limit, offset))
    return {'accounts': [dict(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}
