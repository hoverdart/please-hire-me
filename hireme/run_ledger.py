"""Bounded search of batch history and its durable model-request counts."""
from __future__ import annotations

import json


def _search_text(identifier, status, detail):
    try: detail = json.dumps(json.loads(detail or '{}'), ensure_ascii=False)
    except (ValueError, TypeError): pass
    return f'{identifier} {status} {detail or ""}'.casefold()


def search_runs(store, search='', status='all', offset=0, limit=25):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if status not in ('all', 'running', 'finished', 'paused', 'blocked', 'interrupted'):
        raise ValueError('Choose a recorded batch status')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid batch-history page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    conditions = []; parameters = []
    if status != 'all': conditions.append('r.status=?'); parameters.append(status)
    term = search.strip().casefold()
    if term:
        store.db.create_function('run_search', 3, _search_text, deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("run_search(r.id,r.status,r.detail) LIKE ? ESCAPE '\\'")
        parameters.append('%' + escaped + '%')
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    total = store.db.execute('SELECT COUNT(*) FROM runs r' + where, parameters).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = store.db.execute('''SELECT r.*,(SELECT COUNT(*) FROM model_requests m WHERE m.run_id=r.id) AS model_requests_used
        FROM runs r''' + where + ' ORDER BY r.started DESC,r.id LIMIT ? OFFSET ?', (*parameters, limit, offset))
    return {'runs': [dict(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}
