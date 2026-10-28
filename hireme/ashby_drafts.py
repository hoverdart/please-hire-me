"""Exact, source-bound Ashby field saves; never a final submission grant."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit,parse_qsl

from .util import Blocked, digest

# Reviewed public ApiSetFormValue operation, including its read-only fragments.
# A vendor query change needs review instead of granting another mutation.
SET_VALUE_QUERY = '8dd80bd14717a189f21c3fbd38f8c6a5fff93c3e559b22d790b55195cfc41171'
VARIABLES = {'organizationHostedJobsPageName','formRenderIdentifier','path','value','formDefinitionIdentifier'}
LOCATION_QUERY = 'a9629d2e62c696a6a2770d1807830ab05137bee768b05f11e07ddd205afdfa9b'
SCHOOL_QUERY = 'aa259f3f4c3aab159bf8c2cd8a358317b549ec948b1d5aa87d31a98b634b7ca7'
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'


def same(left, right):
    return json.dumps(left, sort_keys=True, separators=(',',':')) == json.dumps(right, sort_keys=True, separators=(',',':'))


def official_choices(metadata, field, selected):
    """React choice controls save metadata values, not the inputs' default 'on'."""
    rows=metadata.get('selectableValues');mapping={};values=set()
    if not isinstance(rows,list) or not 0<len(rows)<=500:
        raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choices are unverified')
    for row in rows:
        if not isinstance(row,dict):raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choice changed')
        if row.get('isArchived') is not None and type(row['isArchived']) is not bool:
            raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choice status changed')
        if row.get('isArchived') is True:continue
        label=row.get('label');value=row.get('value')
        if not isinstance(label,str) or not 0<len(label)<=1000 or not isinstance(value,str) or not 0<len(value)<=1000:
            raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choices are ambiguous')
        # Visible radio/checkbox labels are trimmed by the form snapshot.
        # Preserve the original wire value, including its whitespace.
        label=label.strip()
        if not label or label in mapping or value in values:
            raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choices are ambiguous')
        mapping[label]=value;values.add(value)
    options=field.get('options',[])
    if (not selected or len(set(options))!=len(options) or any(label not in mapping for label in options)
            or any(label not in options for label in selected)):
        raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choice labels changed')
    return [mapping[label] for label in options if label in selected]


def verified_definition_identifier(value, posting_id):
    """Retain the official wire string; composite IDs must name this posting."""
    if not isinstance(value,str) or len(value)>512:return False
    if re.fullmatch(UUID,value,re.I):return True
    def unique(pairs):
        output={}
        for key,item in pairs:
            if key in output:raise ValueError('Duplicate identifier field')
            output[key]=item
        return output
    try:identifier=json.loads(value,object_pairs_hook=unique)
    except (ValueError,TypeError,RecursionError):return False
    return (isinstance(identifier,dict)
            and set(identifier)=={'kind','formDefinitionId','jobPostingId','jobBoardSuperType'}
            and identifier['kind']=='CompositeFormDefinitionId-JobPostingApplicationFormV2'
            and isinstance(identifier['formDefinitionId'],str)
            and bool(re.fullmatch(UUID,identifier['formDefinitionId'],re.I))
            and identifier['jobPostingId']==posting_id
            and identifier['jobBoardSuperType']=='External')


class AshbyDraft:
    def __init__(self, store, job):
        self.store=store
        parsed=urlsplit(job['url'])
        match=re.fullmatch(r'/([A-Za-z0-9_-]+)/([0-9a-f-]{36})(?:/application)?/?',parsed.path,re.I)
        self.identity=(match[1].casefold(),match[2].lower()) if match and parsed.hostname=='jobs.ashbyhq.com' else None
        self.job=job;self.forms={};self.grants={};self.pending={};self.saved=set();self.failed=False
        self.package=None;self.remaining=0
        self.postings={};self.submission_contexts={};self.locations={};self.schools={};self.navigation_url=job['url']
        self.texting_contexts={};self.texting=None
        self.school_searches={};self.education_lookups={};self.education_edited=set()

    def bind_page(self, url):
        """Follow a tenant rename only after the same official posting was read.

        This changes the session's form destination, never the durable job ID,
        company budget or stored posting URL.
        """
        parsed=urlsplit(url)
        match=re.fullmatch(r'/([A-Za-z0-9_-]+)/([0-9a-f-]{36})(?:/application)?/?',parsed.path,re.I)
        if (not self.identity or not match or parsed.scheme!='https' or parsed.hostname!='jobs.ashbyhq.com'
                or parsed.port not in (None,443) or parsed.username or parsed.password
                or match[2].lower()!=self.identity[1]):
            raise Blocked('unexpected_redirect','The Ashby posting destination changed')
        tenant=match[1].casefold()
        if tenant!=self.identity[0]:
            if parsed.query or parsed.fragment or tenant not in self.postings or self.package:
                raise Blocked('unexpected_redirect','The renamed Ashby posting is unverified')
            self.forms=self.postings[tenant];self.identity=(tenant,self.identity[1])
            self.locations={};self.schools={}
            self.school_searches={};self.education_lookups={};self.education_edited=set()
        self.navigation_url='https://jobs.ashbyhq.com/'+match[1]+'/'+match[2]

    def _remember_form(self, form, *, acknowledged=False):
        if not isinstance(form,dict):return
        render=form.get('id');definition=form.get('sourceFormDefinitionId')
        if not isinstance(render,str) or not re.fullmatch(r'[0-9a-f-]{36}',render,re.I):return
        if not verified_definition_identifier(definition,self.identity[1] if self.identity else None):return
        entries={};values={}
        for section in form.get('sections',[]):
            if section.get('isHidden'):continue
            for entry in section.get('fieldEntries',[]):
                field=entry.get('field',{});path=field.get('path')
                if isinstance(path,str) and entry.get('id')==render+'_'+path and not entry.get('isHidden'):
                    entries[path]=field
                    value=entry.get('fieldValue')
                    if isinstance(value,dict) and 'value' in value:
                        values[path]=value['value']
        self.forms[render]={'definition':definition,'entries':entries,'controls':form.get('formControls'),
                            'values':values if not form.get('errorMessages') and not form.get('formErrors') else {}}
        from .ashby_texting import saved_consent
        self.forms[render]['texting_value']=saved_consent(form) if not form.get('errorMessages') and not form.get('formErrors') else None
        from .ashby_attachments import file_values
        self.forms[render]['file_values']=file_values(form)
        if acknowledged and self.identity:
            # Ashby can rotate its opaque Submit action after saving a field.
            # Only a verified save acknowledgement may refresh the submission
            # context, and only for that exact known form and definition. The
            # submission grant still validates the action and all saved values.
            context=self.submission_contexts.get(self.identity[0],{})
            for known in [context.get('application'),*context.get('surveys',[])]:
                if (isinstance(known,dict) and known.get('id')==render
                        and known.get('sourceFormDefinitionId')==definition):
                    known['formControls']=json.loads(json.dumps(form.get('formControls')))

    def observe_posting(self, request, response):
        if not self.identity or response.status!=200:return
        parsed=urlsplit(request.url)
        if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!='jobs.ashbyhq.com'
                or parsed.path!='/api/non-user-graphql' or parsed.port not in (None,443)
                or parsed.username or parsed.password):return
        try:
            data=json.loads(request.post_data or '{}');variables=data.get('variables',{})
            if (data.get('operationName')!='ApiJobPosting' or not isinstance(data.get('query'),str)
                    or not re.match(r'^\s*query\s+ApiJobPosting\b',data['query'])
                    or str(variables.get('jobPostingId','')).lower()!=self.identity[1]):return
            tenant=variables.get('organizationHostedJobsPageName')
            if not isinstance(tenant,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',tenant):return
            result=response.json()
            posting=result.get('data',{}).get('jobPosting',{})
            if result.get('errors') or str(posting.get('id','')).lower()!=self.identity[1]:return
            from .ashby_texting import CONTEXT_KEYS
            application=posting.get('applicationForm')
            self.texting_contexts[tenant.casefold()]={
                **{key:posting.get(key) for key in CONTEXT_KEYS},
                'render':application.get('id') if isinstance(application,dict) else None}
            def submission_form(form):
                if not isinstance(form,dict):return None
                return {key:form.get(key) for key in ('id','sourceFormDefinitionId','formControls')}
            notice=posting.get('automatedProcessingLegalNotice')
            self.submission_contexts[tenant.casefold()]={
                'application':submission_form(posting.get('applicationForm')),
                'surveys':[submission_form(form) for form in posting.get('surveyForms',[])],
                'legal_notice_rule_id':(notice.get('automatedProcessingLegalNoticeRuleId') if isinstance(notice,dict)
                                        else None if notice is None else False)}
            if tenant.casefold()==self.identity[0]:
                for form in [posting.get('applicationForm'),*posting.get('surveyForms',[])]:self._remember_form(form)
                self.postings[tenant.casefold()]=self.forms
            else:
                # A response alone cannot redirect the browser or grant a save.
                current=self.forms;self.forms={}
                try:
                    for form in [posting.get('applicationForm'),*posting.get('surveyForms',[])]:self._remember_form(form)
                    if self.forms:self.postings[tenant.casefold()]=self.forms
                finally:self.forms=current
        except (ValueError,TypeError,AttributeError):return

    def observe_locations(self, request, response):
        """Remember exact choices from the reviewed, read-only vendor lookup."""
        if not self.identity or response.status!=200:return
        parsed=urlsplit(request.url)
        if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!='jobs.ashbyhq.com'
                or parsed.path!='/api/non-user-graphql' or parsed.port not in (None,443)
                or parsed.username or parsed.password
                or parse_qsl(parsed.query,keep_blank_values=True) not in ([],[('op','ApiAutocompleteGeoLocation')])):return
        try:
            data=json.loads(request.post_data or '{}')
            if (set(data)!={'operationName','query','variables'} or data['operationName']!='ApiAutocompleteGeoLocation'
                    or not isinstance(data['query'],str)
                    or hashlib.sha256(re.sub(r'\s+','',data['query']).encode()).hexdigest()!=LOCATION_QUERY):return
            variables=data['variables']
            if (not isinstance(variables,dict) or set(variables)!={'text','locationTypes'}
                    or not isinstance(variables['text'],str) or len(variables['text'])>500):return
            result=response.json()
            if result.get('errors'):return
            suggestions=result.get('data',{}).get('result',{}).get('suggestions')
            if not isinstance(suggestions,list) or len(suggestions)>100:return
            # Keep each lookup's type filter: a city answer cannot borrow a
            # provider ID returned exclusively by a country-only widget.
            types=json.dumps(variables['locationTypes'],sort_keys=True)
            choices=self.locations.setdefault(types,{})
            for suggestion in suggestions:
                name=suggestion.get('name');path=suggestion.get('geoLocationPath')
                identifier=path[-1].get('providerLocationId') if isinstance(path,list) and path and isinstance(path[-1],dict) else None
                if isinstance(name,str) and 0<len(name)<=500 and isinstance(identifier,str) and 0<len(identifier)<=500:
                    if len(choices)<500 or name in choices:choices.setdefault(name,set()).add(identifier)
        except (ValueError,TypeError,AttributeError,KeyError):return

    def observe_schools(self, request, response):
        """Bind canonical school IDs to exact names from the official lookup."""
        if not self.identity or response.status!=200:return
        parsed=urlsplit(request.url)
        if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!='jobs.ashbyhq.com'
                or parsed.path!='/api/non-user-graphql' or parsed.port not in (None,443)
                or parsed.username or parsed.password
                or parse_qsl(parsed.query,keep_blank_values=True) not in ([],[('op','ApiSearchSchoolByCanonicalName')])):return
        try:
            data=json.loads(request.post_data or '{}')
            if (set(data)!={'operationName','query','variables'} or data['operationName']!='ApiSearchSchoolByCanonicalName'
                    or not isinstance(data['query'],str)
                    or hashlib.sha256(re.sub(r'\s+','',data['query']).encode()).hexdigest()!=SCHOOL_QUERY):return
            variables=data['variables']
            if (not isinstance(variables,dict) or set(variables)!={'name','organizationHostedJobsPageName'}
                    or not isinstance(variables['name'],str) or len(variables['name'])>500
                    or not isinstance(variables['organizationHostedJobsPageName'],str)
                    or variables['organizationHostedJobsPageName'].casefold()!=self.identity[0]):return
            result=response.json()
            if result.get('errors'):return
            schools=result.get('data',{}).get('result')
            if not isinstance(schools,list) or len(schools)>100:return
            observed={}
            for school in schools:
                if not isinstance(school,dict):return
                name=school.get('name');identifier=school.get('id')
                if not (isinstance(name,str) and 0<len(name)<=500
                        and isinstance(identifier,str) and 0<len(identifier)<=500):return
                observed.setdefault(name,set()).add(identifier)
            if len(self.schools.keys()|observed.keys())>500:return
            for name,identifiers in observed.items():self.schools.setdefault(name,set()).update(identifiers)
            self.school_searches[variables['name']]=observed
        except (ValueError,TypeError,AttributeError,KeyError):return

    def enable(self, browser, answers, package):
        from .answers import selections,validate_package
        from .approval_boundary import ApprovalBoundary
        boundary=ApprovalBoundary(self.store,self.job,package)
        validate_package(self.store,self.job,package)
        self.store._check_budget(self.job,self.store.settings())
        grants={};saved=set();texting=[]
        for answer in answers:
            field=answer['field'];path=field.get('ashby_path');entry=field.get('ashby_entry_id','')
            if field.get('ashby_texting_consent'):
                texting.append(answer);continue
            if not path:
                if field.get('required'):raise Blocked('ashby_draft_unsupported','Required field has no verified Ashby path')
                continue
            matches=[(render,form) for render,form in self.forms.items()
                     if entry==render+'_'+path and path in form['entries']]
            if len(matches)!=1:raise Blocked('ashby_draft_unsupported','The current Ashby form identity is unverified')
            render,form=matches[0];metadata=form['entries'][path]
            # Bind the reviewed question to the official form response, not just
            # an arbitrary path asserted by a browser-generated request.
            normalize=lambda value:re.sub(r'[\s*✱]+',' ',value).strip().casefold()
            if normalize(metadata.get('title',''))!=normalize(field['label']):
                raise Blocked('ashby_draft_unsupported','Ashby question identity changed')
            value=answer['value'];kind=field['type']
            if kind=='ashby_education':
                from .ashby_education import wire
                value=wire(self,field,metadata,value)
            elif kind=='combobox' and metadata.get('type')=='EducationHistory':
                # This renderer saves one structured entry even when it only
                # asks for a university. Additional history fields need their
                # own approved values and a reviewed encoding.
                if (answer.get('provenance',{}).get('fact_key')!='school'
                        or normalize(metadata.get('title','')) not in {'college/university','college / university','school','university'}
                        or metadata.get('schoolName')!='required'
                        or any(metadata.get(key)!='excluded' for key in ('degree','major','startDate','endDate'))
                        or type(metadata.get('minRepeat')) is not int or not 0<=metadata['minRepeat']<=1
                        or metadata.get('maxRepeat') is not None and (type(metadata['maxRepeat']) is not int or metadata['maxRepeat']<1)):
                    raise Blocked('ashby_draft_unsupported','Ashby education history requires additional verified fields')
                choices=set(self.schools.get(value,set()))
                restored=form['values'].get(path)
                if (isinstance(restored,list) and len(restored)==1 and isinstance(restored[0],dict)
                        and set(restored[0])=={'canonicalSchoolId','schoolName'} and restored[0]['schoolName']==value
                        and isinstance(restored[0]['canonicalSchoolId'],str) and 0<len(restored[0]['canonicalSchoolId'])<=500):
                    choices.add(restored[0]['canonicalSchoolId'])
                if len(choices)!=1:raise Blocked('ashby_draft_unsupported','Ashby school choice is unverified or ambiguous')
                value=[{'canonicalSchoolId':next(iter(choices)),'schoolName':value}]
            elif kind=='combobox' and metadata.get('type')=='Location':
                if answer.get('provenance',{}).get('fact_key')!='location':
                    raise Blocked('ashby_draft_unsupported','Ashby location has no confirmed location source')
                choices=self.locations.get(json.dumps(metadata.get('locationTypes'),sort_keys=True),{}).get(value,set())
                # An existing exact official value can also restore a selection
                # without another autocomplete request.
                restored=form['values'].get(path)
                if (isinstance(restored,dict) and set(restored)=={'text','providerLocationId'}
                        and restored['text']==value and isinstance(restored['providerLocationId'],str)
                        and 0<len(restored['providerLocationId'])<=500):choices=choices|{restored['providerLocationId']}
                if len(choices)!=1:raise Blocked('ashby_draft_unsupported','Ashby location choice is unverified or ambiguous')
                value={'text':value,'providerLocationId':next(iter(choices))}
            elif kind in {'yesno','checkbox'} and value in {'Yes','No'}:value=value=='Yes'
            elif kind=='radio' and metadata.get('type')=='ValueSelect':
                value=official_choices(metadata,field,[value])[0]
            elif kind=='checkbox-group' and metadata.get('type')=='MultiValueSelect':
                value=official_choices(metadata,field,selections(value,field['options']))
            elif kind=='select' and field.get('option_values'):
                value=field['option_values'][field['options'].index(value)]
            elif kind=='radio':
                value=browser._control(field,field['options'].index(value)).get_attribute('value')
            elif kind=='checkbox-group':
                chosen=selections(value,field['options'])
                value=[browser._control(field,i).get_attribute('value') for i,label in enumerate(field['options']) if label in chosen]
            elif kind not in {'text','textarea','email','tel','url','date','number','combobox','select'}:
                raise Blocked('ashby_draft_unsupported','Ashby field value encoding needs review')
            if value is None or isinstance(value,list) and any(item is None for item in value):
                raise Blocked('ashby_draft_unsupported',field['label']+' — Ashby choice encoding needs review')
            key=(render,path)
            if key in grants:raise Blocked('ashby_draft_unsupported','Duplicate Ashby field path')
            grants[key]={'definition':form['definition'],'value':value}
            # React suppresses autosaves when a restored value already equals
            # the server's savedValue. The verified official form response is
            # an acknowledgement of that exact value; a DOM prefill is not.
            if key not in self.education_edited and path in form['values'] and same(form['values'][path],value):saved.add(key)
        if len(texting)>1:raise Blocked('ashby_draft_unsupported','Multiple Ashby texting controls need review')
        from .ashby_texting import AshbyTexting
        consent=AshbyTexting(self,texting[0],answers,grants) if texting else None
        boundary.check()
        self.boundary=boundary
        self.grants=grants;self.package=package;self.remaining=4*len(grants);self.saved=saved;self.failed=False;self.texting=consent
        for _,path in sorted(saved):
            self.store.event('ashby_draft_saved',self.job['id'],{'path':path,'source':'official_form'})

    def allow(self, request, data, key):
        if not self.package or not self.identity or not isinstance(data,dict):return False
        if set(data)!={'operationName','query','variables'}:return False
        from .ashby_texting import OPERATION
        if data.get('operationName')==OPERATION:
            return self.texting.allow(request,data,key) if self.texting else False
        parsed=urlsplit(request.url)
        if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!='jobs.ashbyhq.com'
                or parsed.path!='/api/non-user-graphql' or parsed.port not in (None,443)
                or parsed.username or parsed.password):return False
        query_options=parse_qsl(parsed.query,keep_blank_values=True)
        if query_options not in ([],[('op','ApiSetFormValue')]):return False
        if data.get('operationName')!='ApiSetFormValue' or not isinstance(data.get('query'),str):return False
        query=hashlib.sha256(re.sub(r'\s+','',data['query']).encode()).hexdigest()
        if query!=SET_VALUE_QUERY:return False
        variables=data.get('variables')
        if not isinstance(variables,dict) or set(variables)!=VARIABLES:return False
        field=(variables.get('formRenderIdentifier'),variables.get('path'))
        grant=self.grants.get(field)
        if (not grant or str(variables.get('organizationHostedJobsPageName','')).casefold()!=self.identity[0]
                or variables.get('formDefinitionIdentifier')!=grant['definition']
                or not same(variables.get('value'),grant['value']) or self.remaining<=0):return False
        self.authorize_save()
        self.remaining-=1;self.pending[key]=field
        self.store.event('ashby_draft_write',self.job['id'],{'path':field[1],'value_hash':digest(grant['value'])})
        return True

    def authorize_save(self):
        self.boundary.check()
        from .answers import validate_package
        validate_package(self.store,self.job,self.package)
        self.store._check_budget(self.job,self.store.settings())
        self.boundary.check()

    def observe_save(self, response, key):
        if self.texting and self.texting.observe_save(response,key):
            self.failed=self.failed or self.texting.failed
            return
        field=self.pending.pop(key,None)
        if field is None:return
        try:
            result=response.json();form=result.get('data',{}).get('setFormValue',{})
            grant=self.grants.get(field)
            if (not 200<=response.status<300 or result.get('errors') or not grant
                    or form.get('id')!=field[0] or form.get('sourceFormDefinitionId')!=grant['definition']
                    or form.get('errorMessages') or form.get('formErrors')):
                self.failed=True;return
            entries=[entry for section in form.get('sections',[]) for entry in section.get('fieldEntries',[])
                     if entry.get('id')==field[0]+'_'+field[1] and entry.get('field',{}).get('path')==field[1]]
            if len(entries)!=1 or not same((entries[0].get('fieldValue') or {}).get('value'),grant['value']):
                self.failed=True;return
            # A verified save can reveal employer-authored conditional fields.
            # Those still need new approved answers before they receive grants.
            self._remember_form(form,acknowledged=True)
            self.saved.add(field)
            self.store.event('ashby_draft_saved',self.job['id'],{'path':field[1]})
        except (ValueError,TypeError,AttributeError):self.failed=True

    @property
    def complete(self):
        return (bool(self.package) and not self.failed and not self.pending and set(self.grants)<=self.saved
                and (self.texting is None or self.texting.complete))
