"""Ashby's compound EducationHistory, bound to confirmed structured history.

Reviewed renderer: frontend b05a9cc / index-o5i0R3Lt.js (2026-10-27 PDT),
SHA256 4c45530ae3151579609daa61f9573a485e5ba7cc19f8c7bc741186269e7fcfbe.
The month picker serializes YYYY-MM-01; this is month precision, not a
claim that the applicant started on the first day of that month.
"""
import json
import re
import time
import calendar

from .util import Blocked, digest

KEYS=('schoolName','degree','major','startDate','endDate')


def school_choice(name,options):
    from .answers import _option_value
    # A canonical record for "Other" does not identify this institution.
    try:value=_option_value('school',name,[o for o in options if o.strip().casefold()!='other'])
    except Blocked:raise Blocked('mapping_review','Education school needs an exact canonical choice') from None
    if not isinstance(value,str) or not value:
        raise Blocked('mapping_review','Education school needs an exact canonical choice')
    return value


def schema(metadata):
    result={k:metadata.get(k) for k in (*KEYS,'isMany','isRepeatable','minRepeat','maxRepeat')}
    if (metadata.get('type')!='EducationHistory' or result['schoolName']!='required'
            or result['isMany'] is not False
            or any(result[k] not in {'required','optional','excluded'} for k in KEYS)
            or type(result['isRepeatable']) is not bool
            or type(result['minRepeat']) is not int or not 0<=result['minRepeat']<=10
            or result['maxRepeat'] is not None and
                (type(result['maxRepeat']) is not int or not 1<=result['maxRepeat']<=10)):
        raise Blocked('ashby_draft_unsupported','Education history definition changed')
    return result


def resolve(store,field):
    spec=schema({'type':'EducationHistory',**field['education_schema']});entries=store.history_entries('education')
    if not entries and not field.get('required'):return None
    if (not entries or len(entries)<max(1,spec['minRepeat']) or len(entries)>10
            or not spec['isRepeatable'] and len(entries)!=1
            or spec['maxRepeat'] is not None and len(entries)>spec['maxRepeat']):
        raise Blocked('history_confirmation_required','Confirm education records that fit this form')
    values=[]
    for entry in entries:
        from .workday_history import _validate
        try:facts=_validate('education',entry['values'])
        except ValueError:raise Blocked('history_confirmation_required','Education history needs valid confirmed dates') from None
        school=school_choice(facts['institution'],field['school_options'])
        item={'schoolName':school}
        for native,source in [('degree','degree'),('major','field_of_study')]:
            if spec[native]=='excluded':continue
            value=facts.get(source,'')
            if spec[native]=='required' and not value:raise Blocked('history_confirmation_required',native)
            if value:item[native]=value
        for native,source in [('startDate','start'),('endDate','end')]:
            if spec[native]=='excluded':continue
            # Expected graduation is not a completed education end date.
            value=facts.get(source)
            if spec[native]=='required' and not value and not (native=='endDate' and facts.get('current') is True):
                raise Blocked('history_confirmation_required',native)
            if value:item[native]=value+'-01'
        if spec['endDate']!='excluded':
            if type(facts.get('current')) is not bool:raise Blocked('history_confirmation_required','Current education status')
            item['isCurrent']=facts['current']
            if item['isCurrent']:item.pop('endDate',None)
        values.append(item)
    return {'field':field,'value':json.dumps(values,sort_keys=True,separators=(',',':')),
            'provenance':{'ashby_education_history':[[e['id'],e['revision'],digest(e['values'])] for e in entries],
                          'date_precision':'month'}}


def wire(draft,field,metadata,value):
    if schema(metadata)!=field.get('education_schema'):
        raise Blocked('ashby_draft_unsupported','Education definition changed')
    values=json.loads(value)
    for item in values:
        choices=draft.schools.get(item['schoolName'],set())
        if len(choices)!=1:raise Blocked('ashby_draft_unsupported','Education school identity is ambiguous')
        item['canonicalSchoolId']=next(iter(choices))
    return values


def wrapper(browser,field):
    # Paths and identifiers come from the observed official form definition.
    return browser.page.locator('[data-field-entry-id='+json.dumps(field['ashby_entry_id'])+']')


def rows(browser,field):
    root=wrapper(browser,field)
    if root.count()!=1:raise Blocked('form_changed','Education history wrapper changed')
    entries=root.locator('.ashby-application-form-input-education-entry')
    return entries if field['education_schema']['isRepeatable'] else root


def validate_controls(browser,field):
    spec=field['education_schema'];path=field['ashby_path'];entries=rows(browser,field)
    if not 1<=entries.count()<=10:raise Blocked('unsupported_widget','Education row count changed')
    for row in entries.all():
        expected=1
        if row.locator('input[role=combobox].ashby-application-form-input-autocomplete').count()!=1:
            raise Blocked('unsupported_widget','Education school control changed')
        for key in ('degree','major'):
            count=row.locator('[id='+json.dumps(path+'-'+key)+']').count()
            if count!=int(spec[key]!='excluded'):raise Blocked('unsupported_widget','Education text control changed')
            expected+=count
        for key in ('startDate','endDate'):
            controls=row.locator('[id='+json.dumps(path+'-'+key)+'] select');count=controls.count()
            if count!=2*int(spec[key]!='excluded'):raise Blocked('unsupported_widget','Education date control changed')
            if count:
                month=controls.nth(0).locator('option').evaluate_all('(ns)=>ns.filter(n=>n.value).map(n=>[n.value,n.textContent.trim()])')
                years=controls.nth(1).locator('option').evaluate_all('(ns)=>ns.filter(n=>n.value).map(n=>n.value)')
                if (month!=[[str(i),calendar.month_name[i]] for i in range(1,13)]
                        or not years or len(set(years))!=len(years) or any(not re.fullmatch(r'\d{4}',y) for y in years)):
                    raise Blocked('unsupported_widget','Education month/year picker changed')
            expected+=count
        current=row.locator('input[type=checkbox][id='+json.dumps(path+'-isCurrent')+']').count()
        if current!=int(spec['endDate']!='excluded'):raise Blocked('unsupported_widget','Education status control changed')
        if row.locator('input,select,textarea').count()!=expected+current:
            raise Blocked('unsupported_widget','Unreviewed education control')
    if wrapper(browser,field).locator('input,select,textarea').count()!=sum(
            r.locator('input,select,textarea').count() for r in entries.all()):
        raise Blocked('unsupported_widget','Unreviewed control outside education rows')


def snapshot(browser,fields):
    output=[];seen=set()
    for field in fields:
        path=field.get('ashby_path');entry=field.get('ashby_entry_id')
        matches=[form['entries'][path] for render,form in browser.ashby_draft.forms.items()
                 if path in form['entries'] and entry==render+'_'+path] if path else []
        if len(matches)!=1 or matches[0].get('type')!='EducationHistory':output.append(field);continue
        metadata=matches[0]
        if all(metadata.get(k)=='excluded' for k in KEYS[1:]):output.append(field);continue
        if entry in seen:continue
        seen.add(entry);spec=schema(metadata)
        virtual={**field,'label':metadata['title'],'type':'ashby_education','required':not metadata.get('isNullable',True),
                 'options':[],'value':'','education_schema':spec,'school_options':[]}
        validate_controls(browser,virtual)
        title=wrapper(browser,virtual).locator(':scope > .ashby-application-form-question-title')
        normalize=lambda s:re.sub(r'[\s*✱]+',' ',s).strip().casefold()
        if title.count()!=1 or normalize(title.inner_text())!=normalize(metadata['title']):
            raise Blocked('form_changed','Education question changed')
        draft=browser.ashby_draft
        histories=browser.store.history_entries('education')
        lookup=(entry,tuple(h['values']['institution'] for h in histories))
        if lookup in draft.education_lookups:
            virtual['school_options']=draft.education_lookups[lookup]
            output.append(virtual);continue
        if draft.package:raise Blocked('form_changed','Education lookup changed after approval')
        # This pre-approval read follows the existing school picker policy.
        # Never type in a control during the final verification snapshot.
        control=rows(browser,virtual).first.locator('input[role=combobox]');original=control.input_value()
        prior=browser.preparing;browser.preparing=True
        choices=set();touched=False
        try:
            for history in histories:
                name=history['values']['institution']
                if name not in draft.school_searches:
                    # Typing can clear a native selection. A restored server
                    # value then needs a fresh exact save, not a stale ACK.
                    draft.education_edited.add((entry[:-len(path)-1],path))
                    touched=True
                    if control.input_value()==name:control.fill('')
                    control.fill(name)
                    deadline=time.monotonic()+8
                    while name not in draft.school_searches and time.monotonic()<deadline:
                        browser.store.checkpoint();browser.page.wait_for_timeout(100)
                    if name not in draft.school_searches:
                        raise Blocked('posting_fetch_failed','Education school search did not complete')
                    browser.store.event('ashby_school_lookup',draft.job['id'],{'query_hash':digest(name)})
                choices.add(school_choice(name,sorted(draft.school_searches[name])))
                control.press('Escape')
            if touched:
                control.fill(original);control.press('Escape');browser.page.wait_for_timeout(400)
        finally:browser.preparing=prior
        # Lookup menus vary by search text. Bind the stable selected canonical
        # names, rather than making unrelated search results part of the form.
        virtual['school_options']=sorted(choices)
        draft.education_lookups[lookup]=virtual['school_options']
        output.append(virtual)
    return output


def verify(browser,answer):
    field=answer['field'];path=field['ashby_path'];values=json.loads(answer['value'])
    validate_controls(browser,field);entries=rows(browser,field)
    if entries.count()!=len(values):raise Blocked('field_verification_failed','Education row count')
    for row,item in zip(entries.all(),values):
        if row.locator('input[role=combobox]').input_value()!=item['schoolName']:
            raise Blocked('field_verification_failed','Education school')
        for key in ('degree','major'):
            if (field['education_schema'][key]!='excluded'
                    and row.locator('[id='+json.dumps(path+'-'+key)+']').input_value()!=item.get(key,'')):
                raise Blocked('field_verification_failed','Education '+key)
        for key in ('startDate','endDate'):
            if field['education_schema'][key]=='excluded':continue
            selects=row.locator('[id='+json.dumps(path+'-'+key)+'] select')
            actual=[s.input_value() for s in selects.all()]
            expected=[str(int(item[key][5:7])),item[key][:4]] if key in item else ['','']
            if actual!=expected:raise Blocked('field_verification_failed','Education '+key)
        if 'isCurrent' in item and row.locator('input[type=checkbox]').is_checked()!=item['isCurrent']:
            raise Blocked('field_verification_failed','Education current status')


def fill(browser,answer):
    from .ats_widgets import select_combobox_exact
    field=answer['field'];values=json.loads(answer['value']);path=field['ashby_path']
    prior=browser.preparing;browser.preparing=True
    try:
        entries=rows(browser,field)
        if not 1<=entries.count()<=10:raise Blocked('unsupported_widget','Education row count changed')
        while entries.count()<len(values):
            before=entries.count();browser.store.checkpoint()
            button=wrapper(browser,field).locator('button.ashby-application-form-input-education-add')
            if button.count()!=1 or not button.is_enabled():raise Blocked('unsupported_widget','Education add is unavailable')
            button.click()
            if entries.count()!=before+1:raise Blocked('unsupported_widget','Education add did not complete')
        while entries.count()>len(values):
            before=entries.count();browser.store.checkpoint()
            button=entries.last.locator('button.ashby-application-form-input-education-entry-delete')
            if button.count()!=1 or not button.is_enabled():raise Blocked('unsupported_widget','Education delete is unavailable')
            button.click()
            if entries.count()!=before-1:raise Blocked('unsupported_widget','Education delete did not complete')
        validate_controls(browser,field)
        for row,item in zip(entries.all(),values):
            browser.store.checkpoint()
            select_combobox_exact(browser,row.locator('input[role=combobox]'),
                                  {'label':'School','options':field['school_options']},item['schoolName'])
            for key in ('degree','major'):
                if key in item:row.locator('[id='+json.dumps(path+'-'+key)+']').fill(item[key])
            if 'isCurrent' in item:
                checkbox=row.locator('input[type=checkbox]')
                # An untouched unchecked control has no isCurrent property in
                # the native object. Explicitly materialize the confirmed bool.
                if checkbox.is_checked()==item['isCurrent']:checkbox.set_checked(not item['isCurrent'])
                checkbox.set_checked(item['isCurrent'])
            for key in ('startDate','endDate'):
                if key not in item:continue
                controls=row.locator('[id='+json.dumps(path+'-'+key)+'] select')
                selected=[str(int(item[key][5:7])),item[key][:4]]
                for control,value in zip(controls.all(),selected):
                    option=control.locator('option[value='+json.dumps(value)+']')
                    if option.count()!=1 or not control.is_enabled() or not option.is_enabled():
                        raise Blocked('mapping_review','Education date is outside the employer picker')
                    control.select_option(value)
        browser.page.wait_for_timeout(500)
        verify(browser,answer)
    finally:browser.preparing=prior
    if not prior:
        # A native blur flushes the complete aggregate, never a partial grant.
        blur=rows(browser,field).first.locator('input[id$="-degree"],input[id$="-major"]')
        if blur.count():blur.first.focus();blur.first.blur()
        else:
            # The month picker updates its row object and debounces a save.
            picker=rows(browser,field).first.locator('select:not([disabled])')
            if not picker.count() or not picker.first.input_value():
                raise Blocked('ashby_draft_unsupported','Education save trigger is unavailable')
            picker.first.select_option(picker.first.input_value())
