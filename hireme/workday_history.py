"""Applicant-confirmed structured history; resume prose never becomes a fact."""
from __future__ import annotations

import json
import re
import uuid

from .util import Blocked, digest, now


FIELDS = {
    'employment': {'organization', 'title', 'location', 'start', 'end', 'current', 'description'},
    'education': {'institution', 'degree', 'field_of_study', 'location', 'start', 'end', 'expected_end', 'current', 'gpa'},
    # Personal, course and research projects for resumes and writing. Application
    # forms' employment and education sections never read this kind.
    'project': {'name', 'role', 'url', 'start', 'end', 'current', 'description'},
}


def _validate(kind, values):
    if kind not in FIELDS or not isinstance(values, dict) or not values or set(values)-FIELDS[kind]:
        raise ValueError('Choose education, employment or project and known history fields')
    result = {}
    for key, value in values.items():
        if key == 'current':
            if type(value) is not bool:
                raise ValueError('Current history status must be explicitly true or false')
        elif not isinstance(value, str) or not value.strip() or len(value) > (8000 if key == 'description' else 1000):
            raise ValueError('History values must be nonempty text')
        else:
            value = value.strip()
        if key in {'start', 'end', 'expected_end'} and not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', value):
            raise ValueError('History dates must use an exact YYYY-MM month')
        result[key] = value
    required = {'employment': {'organization', 'title'}, 'education': {'institution'}, 'project': {'name', 'description'}}[kind]
    if not required <= set(result):
        raise ValueError('History needs an employer and title, an institution, or a project name and description')
    if 'url' in result and not re.fullmatch(r'https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>"\']*)?', result['url']):
        raise ValueError('Project links must be complete https addresses')
    if result.get('current') is True and 'end' in result:
        raise ValueError('A current entry cannot also have an end date')
    if 'end' in result and 'expected_end' in result:
        raise ValueError('Choose either an actual or expected education end date')
    for key in ('end', 'expected_end'):
        if 'start' in result and key in result and result['start'] > result[key]:
            raise ValueError('History end cannot precede its start')
    return result


def entries(store, kind=None, confirmed_only=True):
    if kind is not None and kind not in FIELDS:
        raise ValueError('Choose education, employment or project')
    terms, parameters = [], []
    if kind is not None:
        terms.append('kind=?'); parameters.append(kind)
    if confirmed_only:
        terms.append('confirmed=1')
    query = 'SELECT * FROM confirmed_history'+(' WHERE '+' AND '.join(terms) if terms else '')+' ORDER BY created,id'
    return [{**dict(row), 'values': json.loads(row['payload']), 'confirmed': bool(row['confirmed'])}
            for row in store.db.execute(query, parameters)]


def put(store, kind, values, *, source, confirmed=False, entry_id=None):
    values = _validate(kind, values)
    if not isinstance(source, str) or not source.strip() or len(source) > 2000 or type(confirmed) is not bool:
        raise ValueError('History needs its source and an explicit confirmation flag')
    entry_id = uuid.uuid4().hex if entry_id is None else entry_id
    if not isinstance(entry_id, str) or not re.fullmatch(r'[a-f0-9]{32}', entry_id):
        raise ValueError('Invalid history entry identity')
    with store.transaction():
        old = store.db.execute('SELECT kind,revision,created FROM confirmed_history WHERE id=?', (entry_id,)).fetchone()
        if old and old['kind'] != kind:
            raise ValueError('A history entry cannot change its kind')
        revision, created = (old['revision']+1, old['created']) if old else (1, now())
        store.db.execute('INSERT OR REPLACE INTO confirmed_history VALUES(?,?,?,?,?,?,?,?)',
            (entry_id, kind, json.dumps(values), source.strip(), int(confirmed), revision, created, now()))
        store.event('history_changed', entry_id, {'kind': kind, 'revision': revision, 'confirmed': confirmed})
    return next(row for row in entries(store, kind, False) if row['id'] == entry_id)


def withdraw(store, entry_id):
    with store.transaction():
        row = store.db.execute('SELECT kind FROM confirmed_history WHERE id=?', (entry_id,)).fetchone()
        if not row:
            raise ValueError('History entry not found')
        store.db.execute('UPDATE confirmed_history SET confirmed=0,revision=revision+1,updated=? WHERE id=?', (now(), entry_id))
        store.event('history_confirmation_withdrawn', entry_id, {'kind': row['kind']})


def fingerprint(store):
    return digest([(row['id'], row['kind'], row['revision'], row['payload'])
                   for row in store.db.execute('SELECT id,kind,revision,payload FROM confirmed_history WHERE confirmed=1 ORDER BY id')])


def _reviewed_option(store,row,key,value,field,provider):
    if key not in {'degree','institution','field_of_study'} or not field.get('required'):return None
    options=field.get('options',[])
    mapping=digest(['history_option',row['id'],row['revision'],key,value,
        field['label'],field.get('type'),sorted(options),field.get('help_text',''),field.get('help_links',[])])
    saved=store.db.execute('SELECT value FROM option_mappings WHERE id=?',(mapping,)).fetchone()
    if saved:return saved['value'] if options.count(saved['value'])==1 else None
    if not provider or not hasattr(provider,'map_option'):return None
    choice=provider.map_option(field,{'key':'history:'+row['id']+':'+key,'label':key.replace('_',' '),'value':value})
    if not isinstance(choice,str) or options.count(choice)!=1:return None
    store.db.execute('INSERT INTO option_mappings VALUES(?,?,?,?,?)',
        (mapping,'history:'+row['id']+':'+key,row['revision'],choice,now()))
    return choice


def resolve_field(store, field, provider=None):
    """Bind each answer to a confirmed entry/key; a DOM row cannot create a claim."""
    entry_id, key = field.get('history_entry_id'), field.get('history_key')
    row = next((row for row in entries(store) if row['id'] == entry_id), None)
    if not row or key not in row['values'] or field.get('section') != row['kind']:
        raise Blocked('history_confirmation_required', field.get('label', 'History entry'))
    if field.get('type') not in {'text', 'textarea', 'number', 'select', 'combobox', 'checkbox', 'radio'}:
        raise Blocked('unsupported_widget', field.get('label', 'History entry'))
    value = row['values'][key]
    if key == 'current':
        value = 'Yes' if value else 'No'
    elif key in {'start', 'end', 'expected_end'}:
        label = field.get('label', '').casefold()
        if field.get('history_date_part')=='month_number':
            value=str(int(value[5:])) if field['type']=='number' else value[5:]
        elif re.search(r'\byear\b', label) and not re.search(r'\bmonth\b', label):
            value = value[:4]
        elif re.search(r'\bmonth\b', label) and not re.search(r'\byear\b', label):
            import calendar
            value = str(int(value[5:])) if field['type'] == 'number' else calendar.month_name[int(value[5:])]
    options = field.get('options', [])
    if options:
        matches = [option for option in options if option.casefold().strip() == value.casefold().strip()]
        if not matches and key in {'degree','institution','field_of_study'}:
            from .answers import _option_value
            try:mapped=_option_value({'degree':'degree','institution':'school','field_of_study':'major'}[key],value,options)
            except Blocked as error:
                if error.reason!='option_mismatch':raise
                mapped=_reviewed_option(store,row,key,value,field,provider)
            matches=[mapped] if mapped in options else []
        if len(matches) != 1:
            raise Blocked('mapping_review', field.get('label', 'History entry'))
        value = matches[0]
    elif field['type'] in {'select', 'combobox', 'radio'}:
        raise Blocked('unsupported_widget', field.get('label', 'History entry'))
    if field.get('maxlength', -1) > 0 and len(value) > field['maxlength']:
        raise Blocked('answer_too_long', field.get('label', 'History entry'))
    from .field_context import validate_numeric
    validate_numeric(value, field)
    return {'field': field, 'value': value, 'provenance': {'history_entry_id': entry_id,
        'history_key': key, 'revision': row['revision'], 'entry_hash': digest(row['values'])}}


def validate_answer(store, answer):
    if resolve_field(store, answer['field']) != answer:
        raise Blocked('history_changed', answer['field'].get('label', 'History entry'))
