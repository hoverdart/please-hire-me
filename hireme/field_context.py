"""Context-scoped semantic bindings and canonical answer presentation."""
from __future__ import annotations
import calendar
import json
import re
from decimal import Decimal,InvalidOperation
from .util import Blocked, digest, now

MAPPING_VERSION = 3
ADAPTER_VERSION = 2

def field_context(host, field, context=None):
    context = context or {}
    options = field.get('options', [])
    values = field.get('option_values', options)
    if len(values) != len(options):
        raise Blocked('invalid_option_metadata', field['label'])
    constraints={key:field.get(key) for key in ('required','min','max','step','pattern','multiple')}
    if field.get('type')=='number':constraints['step_base']=field.get('step_base')
    return {'version': MAPPING_VERSION, 'ats': ats(host), 'scope': host,
            'employer': context.get('company', ''),
            'role': context.get('title',''), 'job_location': context.get('location',''), 'section': field.get('section', ''),
            'label': ' '.join(field['label'].casefold().split()).rstrip(' *?:'),
            'widget': field.get('type', ''), 'maxlength': field.get('maxlength', -1),
            'constraints': constraints,
            'options': sorted(zip(options, values))}

def ats(host):
    host = host.split('|', 1)[0]
    if 'greenhouse' in host: return 'Greenhouse'
    if 'ashby' in host: return 'Ashby'
    if 'lever' in host: return 'Lever'
    if 'myworkdayjobs' in host or 'myworkdaysite' in host: return 'Workday'
    return 'Other'

def source_revision(store, key=None, template_id=None):
    source = store.facts().get(key) if key else next((t for t in store.templates() if t['id'] == template_id), None)
    return source['revision'] if source else None

def binding(store, host, field, context):
    fingerprint = digest(field_context(host, field, context))
    row = store.db.execute('SELECT * FROM field_bindings_v2 WHERE id=?', (fingerprint,)).fetchone()
    if row and row['source_revision'] == source_revision(store, row['fact_key'], row['template_id']):
        return dict(row)
    return None

def save_binding(store, host, field, context, key=None, template_id=None):
    revision = source_revision(store, key, template_id)
    if revision is None or bool(key) == bool(template_id): raise Blocked('invalid_binding_source')
    data = field_context(host, field, context)
    store.db.execute('INSERT OR REPLACE INTO field_bindings_v2 VALUES(?,?,?,?,?,?,?)',
        (digest(data), json.dumps(data), key, template_id, revision, MAPPING_VERSION, now()))

def present(key, value, field):
    """Format confirmed facts without deriving citizenship or legal status."""
    label = field['label'].casefold()
    if key in {'college_start', 'graduation'} and re.fullmatch(r'\d{4}-\d{2}', value):
        year, month = value.split('-')
        if not 1 <= int(month) <= 12: raise Blocked('invalid_date_answer', field['label'])
        if re.search(r'\byear\b', label): return year
        if re.search(r'\bmonth\b', label): return str(int(month)) if field.get('type')=='number' else calendar.month_name[int(month)]
    if key == 'gpa' and field.get('type') == 'number': return value.split('/', 1)[0].strip()
    if key == 'phone' and re.search(r'(?:country|dial|calling).*(?:code)|country.*phone', label):
        # +1 is a calling code shared by multiple countries, never a country answer.
        if re.fullmatch(r'\+?1\d{10}', re.sub(r'[ ()-]', '', value)): return '+1'
        raise Blocked('phone_country_review', field['label'])
    if key == 'phone' and (field.get('phone_component') == 'national' or field.get('maxlength') == 10):
        digits = re.sub(r'\D', '', value)
        if len(digits) == 11 and digits.startswith('1'): return digits[1:]
        raise Blocked('phone_country_review', field['label'])
    return value


def validate_numeric(value,field):
    if field.get('type')!='number':return
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',value):raise Blocked('numeric_answer_needed',field['label'])
    try:
        number=Decimal(value)
        lower=Decimal(field['min']) if field.get('min') else None
        upper=Decimal(field['max']) if field.get('max') else None
        if any(v is not None and not v.is_finite() for v in (lower,upper)):raise InvalidOperation
        if lower is not None and number<lower or upper is not None and number>upper:raise Blocked('numeric_answer_out_of_range',field['label'])
        if field.get('step') and str(field['step']).casefold()!='any':
            step=Decimal(field['step'])
            if not step.is_finite() or step<=0:raise InvalidOperation
            base=lower if lower is not None else Decimal(field.get('step_base') or '0')
            if not base.is_finite():raise InvalidOperation
            if (number-base)%step:raise Blocked('numeric_answer_out_of_range',field['label'])
    except (InvalidOperation,ValueError,TypeError):raise Blocked('mapping_review',field['label']) from None
