"""Private applicant notes, deliberately separate from application sources."""
from .util import now

MAX_NOTE_LENGTH = 4000


class NoteConflict(ValueError):
    pass


def _job(store, job_id):
    if not isinstance(job_id, str) or not 0 < len(job_id) <= 128:
        raise ValueError('Choose an existing opportunity')
    if not store.db.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
        raise ValueError('This opportunity is no longer available')


def get_note(store, job_id):
    _job(store, job_id)
    row = store.db.execute('SELECT job_id,body,revision,updated FROM job_notes WHERE job_id=?', (job_id,)).fetchone()
    return dict(row) if row else {'job_id': job_id, 'body': '', 'revision': 0, 'updated': None}


def save_note(store, data):
    if not isinstance(data, dict):
        raise ValueError('Provide an opportunity and its note')
    job_id, body, expected = data.get('job_id'), data.get('body'), data.get('revision')
    if not isinstance(body, str) or len(body) > MAX_NOTE_LENGTH or '\0' in body:
        raise ValueError('Keep notes to 4,000 characters without null characters')
    if type(expected) is not int or not 0 <= expected <= 2147483647:
        raise ValueError('Reload the saved note before saving changes')
    if not body.strip():
        body = ''
    with store.transaction():
        current = get_note(store, job_id)
        # A retry of an already successful write is harmless, including clears.
        if current['body'] == body:
            return {'note': current, 'changed': False}
        if current['revision'] != expected:
            raise NoteConflict('This note changed in another tab. Your draft is still here. Copy any text you want to keep, then discard edits and reload the saved note.')
        revision, updated = expected + 1, now()
        # Keep a revision even after clearing, preventing stale tabs from
        # recreating a deleted note without seeing its newer version.
        store.db.execute('''INSERT INTO job_notes(job_id,body,revision,updated) VALUES(?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET body=excluded.body,revision=excluded.revision,updated=excluded.updated''',
            (job_id, body, revision, updated))
        store.event('opportunity_note_saved' if body else 'opportunity_note_cleared', job_id, {'revision': revision})
        return {'note': {'job_id': job_id, 'body': body, 'revision': revision, 'updated': updated}, 'changed': True}
