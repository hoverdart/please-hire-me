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


def same(left, right):
    return json.dumps(left, sort_keys=True, separators=(',',':')) == json.dumps(right, sort_keys=True, separators=(',',':'))


class AshbyDraft:
    def __init__(self, store, job):
        self.store=store
        parsed=urlsplit(job['url'])
        match=re.fullmatch(r'/([A-Za-z0-9_-]+)/([0-9a-f-]{36})(?:/application)?/?',parsed.path,re.I)
        self.identity=(match[1].casefold(),match[2].lower()) if match and parsed.hostname=='jobs.ashbyhq.com' else None
        self.job=job;self.forms={};self.grants={};self.pending={};self.saved=set();self.failed=False
        self.package=None;self.remaining=0

    def _remember_form(self, form):
        if not isinstance(form,dict):return
        render=form.get('id');definition=form.get('sourceFormDefinitionId')
        if not all(isinstance(v,str) and re.fullmatch(r'[0-9a-f-]{36}',v,re.I) for v in (render,definition)):return
        entries={}
        for section in form.get('sections',[]):
            if section.get('isHidden'):continue
            for entry in section.get('fieldEntries',[]):
                field=entry.get('field',{});path=field.get('path')
                if isinstance(path,str) and entry.get('id')==render+'_'+path and not entry.get('isHidden'):
                    entries[path]=field
        self.forms[render]={'definition':definition,'entries':entries}

    def observe_posting(self, request, response):
        if not self.identity or response.status!=200:return
        parsed=urlsplit(request.url)
        if request.method!='POST' or parsed.hostname!='jobs.ashbyhq.com' or parsed.path!='/api/non-user-graphql':return
        try:
            data=json.loads(request.post_data or '{}');variables=data.get('variables',{})
            if (data.get('operationName')!='ApiJobPosting' or not isinstance(data.get('query'),str)
                    or not re.match(r'^\s*query\s+ApiJobPosting\b',data['query'])
                    or str(variables.get('organizationHostedJobsPageName','')).casefold()!=self.identity[0]
                    or str(variables.get('jobPostingId','')).lower()!=self.identity[1]):return
            result=response.json()
            posting=result.get('data',{}).get('jobPosting',{})
            if result.get('errors') or str(posting.get('id','')).lower()!=self.identity[1]:return
            for form in [posting.get('applicationForm'),*posting.get('surveyForms',[])]:
                self._remember_form(form)
        except (ValueError,TypeError,AttributeError):return

    def enable(self, browser, answers, package):
        from .answers import selections,validate_package
        validate_package(self.store,self.job,package)
        self.store._check_budget(self.job,self.store.settings())
        grants={}
        for answer in answers:
            field=answer['field'];path=field.get('ashby_path');entry=field.get('ashby_entry_id','')
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
            if kind in {'yesno','checkbox'} and value in {'Yes','No'}:value=value=='Yes'
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
                raise Blocked('ashby_draft_unsupported','Ashby choice encoding needs review')
            key=(render,path)
            if key in grants:raise Blocked('ashby_draft_unsupported','Duplicate Ashby field path')
            grants[key]={'definition':form['definition'],'value':value}
        self.grants=grants;self.package=package;self.remaining=4*len(grants);self.saved=set();self.failed=False

    def allow(self, request, data, key):
        if not self.package or not self.identity or not isinstance(data,dict):return False
        if set(data)!={'operationName','query','variables'}:return False
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
        if not self.store.settings()['live_enabled']:raise Blocked('paused')
        from .answers import validate_package
        validate_package(self.store,self.job,self.package)
        self.store._check_budget(self.job,self.store.settings())
        self.remaining-=1;self.pending[key]=field
        self.store.event('ashby_draft_write',self.job['id'],{'path':field[1],'value_hash':digest(grant['value'])})
        return True

    def observe_save(self, response, key):
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
            self._remember_form(form)
            self.saved.add(field)
            self.store.event('ashby_draft_saved',self.job['id'],{'path':field[1]})
        except (ValueError,TypeError,AttributeError):self.failed=True

    @property
    def complete(self):
        return bool(self.package) and not self.failed and not self.pending and set(self.grants)<=self.saved
