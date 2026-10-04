"""Search all unanswered questions with employer context and bounded pages."""
from __future__ import annotations


def search_questions(store, search='', offset=0, limit=25):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid question page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a page size between 1 and 100')
    from .presentation import actionable_question_sql
    condition,values=actionable_question_sql('q')
    where = ' WHERE '+condition; parameters = list(values)
    term = search.strip().casefold()
    if term:
        store.db.create_function('question_search', 4,
            lambda label, reason, company, title: ' '.join(str(v or '') for v in (label, reason, company, title)).casefold(), deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where += " AND question_search(q.label,q.reason,j.company,j.title) LIKE ? ESCAPE '\\'"
        parameters.append('%' + escaped + '%')
    joined = ' FROM questions q LEFT JOIN jobs j ON j.id=q.job_id'
    total = store.db.execute('SELECT COUNT(*)' + joined + where, parameters).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = store.db.execute('SELECT q.*,j.company,j.title,j.url' + joined + where +
                            ' ORDER BY q.rowid LIMIT ? OFFSET ?', (*parameters, limit, offset))
    questions=[]
    import json
    for row in rows:
        question=dict(row)
        metadata=store.db.execute('SELECT context FROM question_contexts WHERE id=?',(row['id'],)).fetchone()
        if metadata:question['field_context']=json.loads(metadata['context'])
        questions.append(question)
    return {'questions': questions, 'total': total, 'offset': offset, 'limit': limit}
