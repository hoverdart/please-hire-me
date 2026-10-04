"""Named opportunity filters, kept in the applicant's private ledger."""
import re
import uuid

from .util import now

STATUSES = {'manually_applied','skipped','all','confirmed','blocked','unknown','awaiting_verification','discovered','not_match','waiting','prepared'}
SORTS = {'recent','fit','company'}


def list_views(store):
    return [dict(row) for row in store.db.execute('SELECT id,name,search,status,sort,updated FROM saved_views ORDER BY name_key,id')]


def _filters(data):
    if not isinstance(data, dict): raise ValueError('Provide opportunity filters')
    search, status, sort = data.get('search',''), data.get('status','all'), data.get('sort','recent')
    if not isinstance(search, str) or len(search) > 200: raise ValueError('Search must be 200 characters or fewer')
    if not isinstance(status, str) or status not in STATUSES: raise ValueError('Choose an available opportunity status')
    if not isinstance(sort, str) or sort not in SORTS: raise ValueError('Choose recent, fit or company sorting')
    return search.strip(), status, sort


def change_view(store, data):
    if not isinstance(data, dict): raise ValueError('Provide a saved-view action')
    action = data.get('action')
    if action not in ('save','replace','delete'): raise ValueError('Choose save, replace or remove')
    if action == 'save':
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 48 or any(ord(c) < 32 for c in name):
            raise ValueError('Name the view with 1–48 characters')
        name = name.strip()
    else:
        key = data.get('id')
        if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key): raise ValueError('Choose an existing saved view')
    if action != 'delete': filters = _filters(data)
    with store.transaction():
        if action == 'save':
            if store.db.execute('SELECT 1 FROM saved_views WHERE name_key=?', (name.casefold(),)).fetchone():
                raise ValueError('A view already has this name. Choose another name or replace its filters below.')
            if store.db.execute('SELECT COUNT(*) FROM saved_views').fetchone()[0] >= 20:
                raise ValueError('Keep up to 20 saved views. Remove one before saving another.')
            key = uuid.uuid4().hex
            store.db.execute('INSERT INTO saved_views VALUES(?,?,?,?,?,?,?,?)', (key,name,name.casefold(),*filters,now(),now()))
        else:
            if not store.db.execute('SELECT 1 FROM saved_views WHERE id=?', (key,)).fetchone(): raise ValueError('This saved view is no longer available. Refresh the desk and try again.')
            if action == 'delete': store.db.execute('DELETE FROM saved_views WHERE id=?', (key,))
            else: store.db.execute('UPDATE saved_views SET search=?,status=?,sort=?,updated=? WHERE id=?', (*filters,now(),key))
        store.event('saved_view_'+action, key, {})
    return {'views': list_views(store)}
