"""One native Ashby submit request for an already approved, acknowledged draft.

Human verification must not extend the browser's permission to save new answers
or submit other forms. This grant contains no CAPTCHA solution and never retries.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit

from .ashby_drafts import UUID, same, verified_definition_identifier
from .util import Blocked, digest


# Reviewed public client operations, including their read-only fragments.
SINGLE_QUERY='d864727b5ae0be0e85510892f568d41013b931c09cd6cc4996763dc12873aa7a'
MULTIPLE_QUERY='15686c0685d88037f73c7c7000e0714455bc118c808c56af965ee6c32042f376'
COMMON={'organizationHostedJobsPageName','jobPostingId','recaptchaToken','sourceAttributionCode',
        'viewedAutomatedProcessingLegalNoticeRuleId','deviceFingerprint','applicationRequestId'}
SINGLE={'formRenderIdentifier','formDefinitionIdentifier','actionIdentifier'}
MULTIPLE={'applicationFormRenderIdentifier','applicationFormDefinitionIdentifier','applicationFormActionIdentifier','surveyIdentifiers'}
BOUNDARIES=('live_enabled','onboarding_complete','roles','seniority','locations','summer_2027_locations',
            'school_locations','min_annual_usd','min_hourly_usd','max_years_required','min_fit_score',
            'skip_companies','interview_companies','company_aliases','max_per_company','company_cooldown_days',
            'max_per_day','timezone','headless','human_verification_wait_seconds')


class AshbySubmission:
    def __init__(self,draft,application_id,attribution=None):
        self.draft=draft;self.store=draft.store;self.aid=application_id;self.used=False
        self.acknowledged=False;self.failed=False;self.response_seen=False;self.request_signature=None
        self.write_deadline=time.monotonic()+45
        if not draft.identity or not draft.complete:
            raise Blocked('ashby_submission_unsupported','The approved employer draft is not acknowledged')
        row=self.store.db.execute('SELECT * FROM applications WHERE id=?',(application_id,)).fetchone()
        if not row or row['state']!='prepared' or row['job_id']!=draft.job['id']:
            raise Blocked('ashby_submission_unsupported','There is no unattempted approved application')
        self.package=json.loads(row['package']);self.package_hash=row['hash']
        if digest(self.package)!=self.package_hash or not same(self.package,draft.package):
            raise Blocked('ashby_submission_unsupported','The approved application changed')
        from .answers import validate_package
        from .approval_boundary import ApprovalBoundary
        self.boundary=ApprovalBoundary(self.store,draft.job,self.package,application_id=application_id)
        validate_package(self.store,draft.job,self.package)
        self.tenant,self.posting=draft.identity
        context=draft.submission_contexts.get(self.tenant)
        if not isinstance(context,dict) or not isinstance(context.get('surveys'),list) or len(context['surveys'])>10:
            raise Blocked('ashby_submission_unsupported','The official submission forms are unverified')
        self.context=json.loads(json.dumps(context))
        self.application=self._form(context.get('application'))
        self.surveys=[self._form(form) for form in context['surveys']]
        renders=[form[0] for form in [self.application,*self.surveys]]
        if len(renders)!=len(set(renders)) or any(render not in renders for render,_ in draft.grants):
            raise Blocked('ashby_submission_unsupported','The approved answers name different submission forms')
        self._acknowledged_values()
        self.notice=context.get('legal_notice_rule_id')
        if self.notice is not None and (not isinstance(self.notice,str) or not re.fullmatch(UUID,self.notice,re.I)):
            raise Blocked('ashby_submission_unsupported','The employer notice identifier is unverified')
        if attribution is not None and (not isinstance(attribution,str) or len(attribution)>1000):
            raise Blocked('ashby_submission_unsupported','The posting attribution is unverified')
        self.attribution=attribution
        settings=self.store.settings();self.boundaries={key:settings[key] for key in BOUNDARIES}
        self.generation=self.store.control_generation()
        self.boundary.check()

    def _form(self,form):
        if not isinstance(form,dict):raise Blocked('ashby_submission_unsupported','A submission form is missing')
        render=form.get('id');definition=form.get('sourceFormDefinitionId');controls=form.get('formControls')
        if (not isinstance(render,str) or not re.fullmatch(UUID,render,re.I)
                or not verified_definition_identifier(definition,self.posting)
                or not isinstance(controls,list) or len(controls)!=1 or not isinstance(controls[0],dict)):
            raise Blocked('ashby_submission_unsupported','The official submission control is unverified')
        control=controls[0];action=control.get('identifier')
        if (not isinstance(action,str) or not re.fullmatch(UUID,action,re.I) or control.get('title')!='Submit'
                or control.get('__typename')!='FormControl'
                or set(control)-{'identifier','title','__typename'}):
            raise Blocked('ashby_submission_unsupported','The official submission action needs review')
        remembered=self.draft.forms.get(render,{})
        if remembered.get('definition')!=definition or not same(remembered.get('controls'),controls):
            raise Blocked('ashby_submission_unsupported','The acknowledged submission form changed')
        return render,definition,action

    def _acknowledged_values(self):
        attachment_check=getattr(self.draft,'attachment_check',None)
        if attachment_check:attachment_check()
        for (render,path),grant in self.draft.grants.items():
            form=self.draft.forms.get(render,{})
            values=form.get('values',{})
            if (grant['definition']!=form.get('definition') or path not in values
                    or not same(values[path],grant['value'])):
                raise Blocked('ashby_submission_unsupported','An approved field acknowledgement changed')

    def allow(self,request,data):
        if (self.used or time.monotonic()>=self.write_deadline
                or not isinstance(data,dict) or set(data)!={'operationName','query','variables'}):return False
        parsed=urlsplit(request.url)
        if (request.method!='POST' or parsed.scheme!='https' or parsed.hostname!='jobs.ashbyhq.com'
                or parsed.path!='/api/non-user-graphql' or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.fragment):return False
        multiple=bool(self.surveys)
        operation='ApiSubmitMultipleFormsAction' if multiple else 'ApiSubmitSingleApplicationFormAction'
        if parse_qsl(parsed.query,keep_blank_values=True) not in ([],[('op',operation)]):return False
        query=data.get('query');variables=data.get('variables')
        if (data.get('operationName')!=operation or not isinstance(query,str) or len(query)>50000
                or hashlib.sha256(re.sub(r'\s+','',query).encode()).hexdigest()!=(MULTIPLE_QUERY if multiple else SINGLE_QUERY)
                or not isinstance(variables,dict) or set(variables)-(COMMON|(MULTIPLE if multiple else SINGLE))):return False
        render,definition,action=self.application
        tenant=variables.get('organizationHostedJobsPageName')
        if not isinstance(tenant,str) or tenant.casefold()!=self.tenant:return False
        expected={'jobPostingId':self.posting,
                  'sourceAttributionCode':self.attribution,'viewedAutomatedProcessingLegalNoticeRuleId':self.notice,
                  'applicationRequestId':None}
        if multiple:
            expected.update(applicationFormRenderIdentifier=render,applicationFormDefinitionIdentifier=definition,
                applicationFormActionIdentifier=action,surveyIdentifiers=[
                    {'formRenderId':r,'sourceFormDefinitionId':d,'actionIdentifier':a} for r,d,a in self.surveys])
        else:expected.update(formRenderIdentifier=render,formDefinitionIdentifier=definition,actionIdentifier=action)
        if any(not same(variables.get(key),value) for key,value in expected.items()):return False
        # These values are produced by the employer's verification client, never
        # by the model. Their contents are not written to events or the ledger.
        token=variables.get('recaptchaToken');fingerprint=variables.get('deviceFingerprint')
        if not isinstance(token,str) or not 0<len(token)<=20000:return False
        if fingerprint is not None and (not isinstance(fingerprint,str) or len(fingerprint)>2000):return False
        if not self.valid_intent() or time.monotonic()>=self.write_deadline:return False
        self.boundary.check()
        if time.monotonic()>=self.write_deadline:return False
        self.used=True
        self.request_signature=(request.url,request.method,digest(data))
        self.store.event('ashby_submission_write',self.aid,{'operation':operation,'survey_forms':len(self.surveys)})
        return True

    def observe_response(self,response):
        if not self.used or self.response_seen:return
        request=response.request
        try:data=json.loads(request.post_data or '')
        except (ValueError,TypeError):return
        if (request.url,request.method,digest(data))!=self.request_signature:return
        # response.json() can yield to an outcome poll on a slow worker. Keep
        # this response pending until its native decoder publishes a result.
        self.response_seen=True
        try:
            result=response.json()
            operation='submitMultipleFormsAction' if self.surveys else 'submitApplicationFormAction'
            value=result.get('data',{}).get(operation,{})
            messages=value.get('messages')
            if messages is None:messages={}
            surveys=value.get('surveyFormResults') if self.surveys else []
            verified=(200<=response.status<300 and not result.get('errors')
                and (value.get('applicationFormResult') or {}).get('__typename')=='FormSubmitSuccess'
                and isinstance(surveys,list) and len(surveys)==len(self.surveys)
                and all(isinstance(form,dict) and form.get('__typename')=='FormSubmitSuccess' for form in surveys)
                and isinstance(messages,dict) and not messages.get('blockMessageForCandidateHtml'))
            self.acknowledged=verified;self.failed=not verified
        except (ValueError,TypeError,AttributeError):pass
        finally:
            self.failed=not self.acknowledged
            self.store.event('ashby_submission_response',self.aid,{'acknowledged':self.acknowledged})

    def valid_intent(self,check_answers=True):
        """Recheck approval while a person is working, without granting a write."""
        self.boundary.check()
        settings=self.store.settings()
        if (not settings['live_enabled'] or self.store.control_generation()!=self.generation
                or any(settings[key]!=value for key,value in self.boundaries.items())):
            raise Blocked('paused','Application boundaries changed during verification')
        self.store.check_job_decision(self.draft.job['id'])
        row=self.store.db.execute('SELECT state,job_id,hash,package FROM applications WHERE id=?',(self.aid,)).fetchone()
        if (not row or row['state']!='submitting' or row['job_id']!=self.draft.job['id']
                or row['hash']!=self.package_hash or not same(json.loads(row['package']),self.package)
                or not self.draft.complete or self.draft.identity!=(self.tenant,self.posting)
                or not same(self.draft.submission_contexts.get(self.tenant),self.context)):return False
        if self.package.get('facts_hash')!=digest(self.store.facts()):raise Blocked('facts_changed')
        if check_answers:
            from .answers import validate_package
            validate_package(self.store,self.draft.job,self.package)
        self._form(self.context['application'])
        for form in self.context['surveys']:self._form(form)
        self._acknowledged_values()
        self.boundary.check()
        return True
