"""Bounded, read-only search of the applicant's private writing-source library."""
from __future__ import annotations

import os
import re
import stat

from .materials import MAX_FILE


def original_available(store, row):
    """Cheap readability check for UI guidance; backup still verifies full hashes."""
    name=row['filename']; digest=row['hash']
    if not isinstance(name,str) or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest): return False
    if not re.fullmatch(re.escape(digest)+r'\.(?:pdf|docx|pptx|txt|md)',name): return False
    parent=store.root/'materials'; path=parent/name
    if parent.is_symlink() or path.is_symlink(): return False
    try:
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(descriptor,'rb') as source:
            info=os.fstat(source.fileno())
            return bool(stat.S_ISREG(info.st_mode) and 0<info.st_size<=MAX_FILE and (stat.S_IMODE(info.st_mode)&0o077)==0 and (info.st_mode&stat.S_IRUSR) and source.read(1))
    except (OSError,ValueError): return False


def search_materials(store, search='', status='all', offset=0, limit=20):
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError('Search must be 200 characters or fewer')
    if status not in ('all', 'approved', 'review', 'personal', 'style', 'reference'):
        raise ValueError('Choose a supported source approval filter')
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid material page')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Choose a source page size between 1 and 100')
    conditions = []; parameters = []
    if status in ('approved', 'review'):
        conditions.append('confirmed=?'); parameters.append(int(status == 'approved'))
    elif status != 'all':
        conditions.extend(['confirmed=1', 'role=?']); parameters.append(status)
    term = search.strip().casefold()
    if term:
        store.db.create_function('material_search', 3, lambda name, text, kind: f'{name} {text} {kind}'.casefold(), deterministic=True)
        escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append("material_search(original_name,text,kind) LIKE ? ESCAPE '\\'")
        parameters.append('%' + escaped + '%')
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    total = store.db.execute('SELECT COUNT(*) FROM materials' + where, parameters).fetchone()[0]
    offset = min(offset, max(0, (total - 1) // limit * limit))
    rows = store.db.execute('SELECT * FROM materials' + where + ' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?', (*parameters, limit, offset))
    return {'materials': [{**dict(row), 'original_available': original_available(store,row)} for row in rows], 'total': total, 'offset': offset,
            'library_total': store.db.execute('SELECT COUNT(*) FROM materials').fetchone()[0],
            'search': search.strip(), 'status': status}
