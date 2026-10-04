"""Review and withdraw exact user-confirmed answers without rewriting history."""
from __future__ import annotations


def list_answers(store, search='', offset=0, limit=25):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid saved-answer page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    term = search.strip().casefold()
    parameters = ()
    where = ''
    if term:
        store.db.create_function('answer_search', 3,
            lambda question, host, value: f'{question} {host} {value}'.casefold(), deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where = " WHERE answer_search(question,host,value) LIKE ? ESCAPE '\\'"
        parameters = ('%' + escaped + '%',)
    total = store.db.execute('SELECT COUNT(*) FROM answers' + where, parameters).fetchone()[0]
    # Adjust an obsolete last page after withdrawal rather than rendering a false empty list.
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = store.db.execute('SELECT * FROM answers' + where + ' ORDER BY updated DESC,id LIMIT ? OFFSET ?',
                            (*parameters, limit, offset))
    return {'answers': [dict(row) for row in rows], 'total': total, 'offset': offset, 'limit': limit}


def revoke_answer(store, answer_id):
    if not isinstance(answer_id, str) or not answer_id or len(answer_id) > 100:
        raise ValueError('Choose an existing saved answer')
    with store.transaction():
        if not store.db.execute('SELECT 1 FROM answers WHERE id=?', (answer_id,)).fetchone():
            raise ValueError('Saved answer not found')
        store.db.execute('DELETE FROM answers WHERE id=?', (answer_id,))
        store.discard_prepared()
        store.event('answer_revoked', answer_id, {})
    store.export_config()
