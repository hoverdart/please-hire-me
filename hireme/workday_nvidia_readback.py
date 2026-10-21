"""Strict projections of the reviewed, owned NVIDIA native GET resources.

These functions prove fields only from the supplied native readback. They do not
perform requests, decode composed addresses, or fill gaps from resume prose.
"""
from __future__ import annotations

from datetime import date, datetime
import re

from .util import Blocked
from .workday_native import checked_object, contains_saved, identifier


ALIASES = {'names':'name', 'address':'addresses', 'phonenumber':'phonenumbers',
           'workexperience':'workexperiences', 'education':'educations', 'webaddress':'webaddresses'}
DATE_FIELDS = {'workexperiences':('startDate','endDate'),
               'educations':('firstYearAttended','lastYearAttended')}


def _hold():
    raise Blocked('remote_draft_uncertain', 'The native readback does not uniquely prove every approved field')


def _object(value):
    try:
        return checked_object(value)
    except Blocked:
        _hold()


def _id(value):
    try:
        return identifier(value)
    except Blocked:
        _hold()


def _precision(precision, field):
    value = precision.get(field) if isinstance(precision,dict) else precision
    if value is None:
        return None
    if not isinstance(value,str) or value.upper() not in {'YYYY','YYYY-MM','YYYY-MM-DD'}:
        _hold()
    return value.upper()


def _date(value, precision=None):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{4}(?:-[0-9]{2}){0,2}',value):
        _hold()
    parts = value.split('-')
    try:
        if not 1 <= int(parts[0]) <= 9999:
            raise ValueError()
        if len(parts)>=2 and not 1 <= int(parts[1]) <= 12:
            raise ValueError()
        if len(parts)==3:
            date.fromisoformat(value)
    except ValueError:
        _hold()
    if precision is None:
        return value
    count = {'YYYY':1,'YYYY-MM':2,'YYYY-MM-DD':3}[precision]
    if len(parts)<count:
        _hold()
    return '-'.join(parts[:count])


def _split_date(record, prefix, precision):
    year, month = record.get(prefix+'Year'), record.get(prefix+'Month')
    if year is None and month is None:
        return None
    if (not isinstance(year,str) or not re.fullmatch(r'[0-9]{4}',year)
            or type(month) is not int or not 1<=month<=12):
        _hold()
    value = _date(year+'-'+str(month).zfill(2))
    if precision is None:
        return None
    # This GET supplies no day. A full-day metadata field cannot be proved by it.
    if precision!='YYYY-MM':
        _hold()
    return value


def _records(data, key='data'):
    value = _object(data)
    records = value.get(key)
    if not isinstance(records,list):
        _hold()
    if key=='data' and (type(value.get('total')) is not int or value['total']!=len(records)):
        _hold()
    seen = set()
    for record in records:
        _object(record)
        remote = _id(record.get('id'))
        if remote in seen:
            _hold()
        seen.add(remote)
    return records


def _copy(record, fields):
    return {key:record[key] for key in fields if key in record}


def _terms(package):
    if package.get('termsAndConditionsUpdated') is not False:
        _hold()
    moments = []
    for field in ('termsAndConditionsLockedMoment','termsAndConditionsConsentMoment'):
        raw = package.get(field)
        if (not isinstance(raw,str) or not re.fullmatch(
                r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})',raw)
                or raw[-1]!='Z' and (int(raw[-5:-3])>23 or int(raw[-2:])>59)):
            _hold()
        try:
            value = datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError:
            _hold()
        if value.tzinfo is None or value.utcoffset() is None:
            _hold()
        moments.append(value)
    if moments[1]<=moments[0]:
        _hold()
    return {'acceptTermsAndAgreements':True,
            **_copy(package,('termsAndConditions','termsAndConditionsConsentMoment',
                            'termsAndConditionsLockedMoment','termsAndConditionsUpdated'))}


def normalize(resource, data, *, precision=None):
    """Return only the source-observed wire fields, retaining native IDs."""
    resource = ALIASES.get(resource,resource)
    if resource in {'name','package','source','uspersonalinfo','termsandconditions'}:
        value = _object(data)
        if resource=='package':
            return dict(value)
        if resource=='name':
            result = _copy(value,('legalName','preferredName'))
            if not result:
                _hold()
            for name in result.values():
                if name is not None:
                    _id(_object(name).get('id'))
            return result
        if resource=='source':
            result = _copy(value,('source','sourcePopulatedFromUrl','referredBy'))
            if 'source' in result:
                _id(_object(result['source']).get('id'))
            return [result]
        if resource=='termsandconditions':
            return [_terms(value)]
        result = _copy(value,('gender','hispanicOrLatino'))
        for actual, single, many in (('ethnicity','ethnicity','ethnicityMulti'),
                                      ('veteranStatuses','veteranStatus',None)):
            if actual not in value:
                continue
            refs = value[actual]
            if not isinstance(refs,list):
                _hold()
            for ref in refs:
                _id(_object(ref).get('id'))
            if len({_id(ref['id']) for ref in refs})!=len(refs):
                _hold()
            if refs:
                result[single] = refs[0]
            if many:
                result[many] = refs
        return [result]
    if resource=='resumeattachments':
        return [_copy(record,('id','file','fileName','fileLength','contentType'))
                for record in _records(data,'attachments')]
    fields = {
        'addresses':('id','addressLine1','addressLine2','city','country','countryRegion','postalCode'),
        'phonenumbers':('id','phoneNumber','countryPhoneCode','phoneType','extension'),
        'previousworker':('id','candidateIsPreviousWorker','manager','email','employeeID','location'),
        'webaddresses':('id','url'),
        'educations':('id','degree','fieldOfStudy','firstYearAttended','lastYearAttended','gradeAverage','school','schoolName'),
        'workexperiences':('id','jobTitle','location','currentlyWorkHere'),
    }.get(resource)
    if fields is None:
        _hold()
    result = []
    for record in _records(data):
        item = _copy(record,fields)
        if resource=='educations':
            for field in DATE_FIELDS[resource]:
                if field in item and item[field] not in (None,''):
                    _date(item[field])
        elif resource=='workexperiences':
            for actual, wire in (('jobHistoryCompanyName','companyName'),
                                 ('responsibilitiesAndAchievements','roleDescription')):
                if actual in record:
                    item[wire] = record[actual]
            for field,prefix in (('startDate','start'),('endDate','end')):
                native = _split_date(record,prefix,_precision(precision,field))
                if native is not None:
                    item[field] = native
        result.append(item)
    return result


def match(resource, data, expected, *, precision=None):
    """Require exactly one record proving every approved value with strict types."""
    canonical = ALIASES.get(resource,resource)
    if not isinstance(expected,dict) or not expected:
        _hold()
    wanted = dict(expected)
    if canonical=='source' and 'referredBy' in wanted and wanted['referredBy'] in ('',None,{}):
        wanted.pop('referredBy')
    if not wanted:
        _hold()
    date_precisions = {}
    for field in DATE_FIELDS.get(canonical,()):
        if field in wanted and wanted[field] not in (None,''):
            native_precision = _precision(precision,field)
            if native_precision is None:
                _hold()
            wanted[field] = _date(wanted[field],native_precision)
            date_precisions[field] = native_precision
    normalized = normalize(canonical,data,precision=precision)
    records = [normalized] if isinstance(normalized,dict) else normalized
    matches = []
    for record in records:
        actual = dict(record)
        comparable = True
        for field,native_precision in date_precisions.items():
            try:
                if field not in actual:
                    _hold()
                actual[field] = _date(actual[field],native_precision)
            except Blocked:
                # An unrelated native history entry may omit an optional date;
                # it is not a match and cannot prove this approved record.
                comparable = False
                break
        if comparable and contains_saved(actual,wanted):
            matches.append(record)
    if len(matches)!=1:
        _hold()
    return matches[0]
