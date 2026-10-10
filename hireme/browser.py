from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

from .answers import resolve,REFUSE,field_key
from .discovery import ATS_HOSTS,PORTAL_HOSTS
from .util import Blocked,digest,private_dir,public_host,safe_document

ASHBY_UPLOAD_HOST='ashbyhq-infra-prd-main-app-uploaded-files-us-east-1.s3.us-east-1.amazonaws.com'
UPLOAD_HOSTS={ASHBY_UPLOAD_HOST,'grnhse-prod-jben-us-west-2.s3.us-west-2.amazonaws.com',
              'grnhse-prod-jben-us-east-1.s3.us-east-1.amazonaws.com',
              'grnhse-prod-jben-eu-west-1.s3.eu-west-1.amazonaws.com'}
CONTROLS='input:not([type=hidden]):not([type=submit]):not([type=button]),textarea,select,[role=combobox]:not(input):not(select),button.ashby-application-form-input-yesno-option'
SNAPSHOT=r"""selector => {
 const controls=Array.from(document.querySelectorAll(selector)); const out=[]; const seen=new Set();
 function labelText(n) {
  if(!n)return '';const copy=n.cloneNode(true);
  copy.querySelectorAll('input,select,textarea,button,[role=combobox]').forEach(x=>x.remove());
  return copy.textContent.trim();
 }
 function reference(el) {return {id:el.id||'',name:el.name||'',tag:el.tagName.toLowerCase()};}
 function details(el) {
  const wrapper=el.closest('[data-field-path],.ashby-application-form-field-entry,.application-question,.field,.field-wrapper');
  const descriptions=Array.from(wrapper?.querySelectorAll('.ashby-application-form-question-description,.field-description,.helper-text,.body__secondary')||[]);
  for(const id of (el.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean)){
   // React-select placeholders and validation/live-region text change after
   // entry. They are widget state rather than employer question wording.
   if(/-(?:placeholder|error|live-region)$/.test(id))continue;
   const node=document.getElementById(id);if(node&&!node.matches('[role=alert],[role=log],.select__placeholder')&&!descriptions.includes(node))descriptions.push(node);
  }
  const help_text=descriptions.map(n=>n.innerText||n.textContent).join(' ').replace(/\s+/g,' ').trim();
  const help_links=descriptions.flatMap(n=>Array.from(n.querySelectorAll('a[href]')).map(a=>({text:a.textContent.trim(),url:a.href})));
  const date_format=el.type==='date'?'YYYY-MM-DD':el.classList.contains('ashby-application-form-input-date')?'MM/DD/YYYY':null;
  return {...(help_text?{help_text}:{}),...(help_links.length?{help_links}: {}),...(date_format?{date_format}: {})};
 }
 function leverLabel(el){
  const question=el.closest('.application-question');
  const heading=labelText(question?.querySelector('.application-label'));
  const card=question?.closest('.application-form[data-qa="additional-cards"]');
  const generic=/^(?:select|choose) one[\s*✱:]*$/i;
  if(generic.test(heading) && card){
   const title=labelText(card.querySelector('h4[data-qa="card-name"]'));
   const questions=Array.from(card.querySelectorAll('.application-question'));
   const sourceCard=/^how did you (?:first )?hear (?:about|of)\b/i.test(title);
   if(title && (questions.length===1 || sourceCard && questions.filter(q=>generic.test(labelText(q.querySelector('.application-label')))).length===1))return title+(/[\*✱]/.test(heading)?'*':'');
  }
  return heading;
 }
 function label(el) {
  const ids=(el.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
  const aria=ids.map(id=>document.getElementById(id)?.innerText||'').join(' ').trim();
  const direct=Array.from(el.labels||[]).map(labelText).join(' ').trim();
  const field=el.closest('fieldset'); const legend=field?.querySelector('legend')?.innerText||field?.querySelector('.ashby-application-form-question-title')?.innerText;
  const wrapper=el.closest('[class*=form-field],[class*=field-entry],[class*=application-question],.field');
  const leverHeading=leverLabel(el);
  return (el.getAttribute('description')||el.getAttribute('aria-label')||aria||leverHeading||direct||legend||labelText(wrapper?.querySelector('label'))||el.getAttribute('placeholder')||'').trim();
 }
 controls.forEach((el,index)=>{
  if(!el.getClientRects().length && el.type!=='file')return;
  if(el.matches('.ashby-application-form-input-yesno-option')){
   const group=el.closest('.ashby-application-form-input-yesno');
   if(!group||seen.has(group))return;seen.add(group);
   const entry=el.closest('.ashby-application-form-field-entry');
   const heading=entry?.querySelector('.ashby-application-form-question-title');
   const buttons=controls.filter(x=>x.matches('.ashby-application-form-input-yesno-option')&&x.closest('.ashby-application-form-input-yesno')===group);
   out.push({index,indices:buttons.map(x=>controls.indexOf(x)),ref:reference(el),refs:buttons.map(reference),label:heading?.textContent.trim()||'',...details(el),type:'yesno',options:buttons.map(x=>x.textContent.trim()),required:!!heading?.className.includes('_required_'),maxlength:-1,value:buttons.find(x=>x.getAttribute('aria-pressed')==='true')?.textContent.trim()||'',multiple:false});
   return;
  }
  if(el.disabled || el.closest('[aria-hidden=true]') || (el.readOnly && el.tabIndex<0))return;
  let type=el.tagName==='SELECT'?'select':el.tagName==='TEXTAREA'?'textarea':el.getAttribute('role')==='combobox'?'combobox':el.type||'text';
  let indices=[index]; let question=label(el); let options=[]; let value=el.value||'';
  let required=el.required||el.getAttribute('aria-required')==='true'||/[\*✱]/.test(question);
  const ashbyHeading=el.closest('fieldset')?.querySelector('.ashby-application-form-question-title')||el.closest('.ashby-application-form-field-entry')?.querySelector('.ashby-application-form-question-title');
  required=required||!!ashbyHeading?.className.includes('_required_');
  if(type==='file'){
   const upload=el.closest('.file-upload');
   if(upload)required=required||/\*/.test(upload.innerText.split('\n')[0]);
   const identity=(el.id+' '+el.name).toLowerCase();
   if(/transcript/.test(identity))question='Transcript';
   else if(/resume|\bcv\b/.test(identity))question='Resume/CV';
   else if(/cover.?letter/.test(identity))question='Cover letter';
  }
  const ashbyGroup=type==='checkbox' ? el.closest('fieldset.ashby-application-form-input-checkbox-group') : null;
  const leverGroup=type==='checkbox' ? el.closest('.application-question') : null;
  const checkboxGroup=ashbyGroup || leverGroup && el.name && controls.filter(x=>x.type==='checkbox'&&x.name===el.name&&x.closest('.application-question')===leverGroup).length>1 || type==='checkbox' && el.name && el.getAttribute('description') && controls.filter(x=>x.type==='checkbox'&&x.name===el.name&&x.getAttribute('description')===el.getAttribute('description')).length>1;
  if(type==='radio'||checkboxGroup){
   const name=ashbyGroup||el.name; if(!name||seen.has(name))return;seen.add(name);
   const group=controls.filter(x=>x.type===el.type&&(ashbyGroup?x.closest('fieldset.ashby-application-form-input-checkbox-group')===ashbyGroup:x.name===name));
   if(checkboxGroup)type='checkbox-group';
   indices=group.map(x=>controls.indexOf(x)); options=group.map(x=>Array.from(x.labels||[]).map(l=>l.innerText).join(' ').trim()||x.value);
   const parent=el.closest('fieldset');
   if(ashbyGroup)required=required||!!ashbyGroup.querySelector('label[class*=_required_]');
   question=ashbyGroup?.querySelector('.ashby-application-form-question-title')?.innerText||el.getAttribute('description')||parent?.querySelector('legend')?.innerText||leverLabel(el)||el.closest('[class*=field],[class*=question]')?.querySelector('label')?.innerText||question;
   // Ashby's SMS radios are nested inside the Phone field. The enclosing
   // heading describes the phone input, not this separate consent control.
   if(type==='radio'&&el.closest('.ashby-application-form-texting-consent-description'))question='Consent to receiving text messages';
   value=group.filter(x=>x.checked).map(x=>Array.from(x.labels||[]).map(l=>l.innerText).join(' ').trim()||x.value).join('; ');
  }else if(type==='select'){options=Array.from(el.options).filter(o=>o.value&&!o.disabled).map(o=>o.textContent.trim())}
  else if(type==='checkbox'){options=['Yes','No'];value=el.checked?'Yes':'No'}
  const entrySelector='.education--form,.employment--form,.experience--form,[data-automation-id="educationSection"],[data-automation-id="education"],[data-automation-id="workExperienceSection"],[data-automation-id="workExperience"]';
  const entry=el.closest(entrySelector);
  const entryPeers=entry ? Array.from(document.querySelectorAll(entrySelector)).filter(x=>x.getClientRects().length&&x.tagName===entry.tagName&&(entry.getAttribute('data-automation-id') ? x.getAttribute('data-automation-id')===entry.getAttribute('data-automation-id') : x.className===entry.className)) : [];
  out.push({index,indices,...(type==='select'?{option_values:Array.from(el.options).filter(o=>o.value&&!o.disabled).map(o=>o.value)}:{}),ref:reference(el),refs:indices.map(i=>reference(controls[i])),label:question.replace(/\s+/g,' ').trim(),...details(el),type,options,
   ...(el.closest('.education--form,[data-automation-id="educationSection"],[data-automation-id="education"]') ? {section:'education'} : el.closest('.employment--form,.experience--form,[data-automation-id="workExperienceSection"],[data-automation-id="workExperience"]') ? {section:'employment'} : {}),
   ...(entryPeers.length>1 ? {section_entry:entryPeers.indexOf(entry)} : {}),
   required:required||/[\*✱]/.test(question),
   maxlength:el.maxLength||-1,min:el.getAttribute('min'),max:el.getAttribute('max'),step:el.getAttribute('step'),step_base:type==='number'?el.getAttribute('value'):null,pattern:el.getAttribute('pattern'),value,multiple:!!el.multiple});
 });return out;
}"""
CONFIRMED=re.compile(r"thank you for (?:your interest|applying|submitting)|application (?:has been |was )?(?:successfully )?(?:submitted|received)|we (?:have |have successfully )?received your application",re.I)

def submission_receipt(text):
    # Some Greenhouse employers replace the default receipt. Require both the
    # completed-application acknowledgement and the promised applicant review;
    # a generic thank-you or invitation to apply is not enough.
    return bool(CONFIRMED.search(text) or (
        re.search(r"thanks again for applying[!.]",text,re.I) and
        re.search(r"our talent team will carefully review your qualifications and experience",text,re.I)))
# Model outcomes that may succeed on a later attempt, unlike missing applicant information.
PROVIDER_FAILURES=('provider_timeout','provider_error','provider_invalid_output','writing_unsupported')


def _model_dependent(field, reason):
    if reason in ('stale_writing_context','writing_upgrade_needed','unsupported_or_stale_sample'):return True
    # Only a field without its own confirmed-fact rule could have been answered from context.
    return reason=='missing_fact' and field_key(field['label']) in (None,'school','degree','major','skills','location','city','state','alternate_email')


def _prefilled(field):
    # A standalone checkbox always reports Yes/No; unchecked is its empty state.
    return bool(field['value']) and not (field['type']=='checkbox' and field['value']=='No')


REJECTED=re.compile(r"we couldn.t submit your application[\s\S]*your application submission was flagged as possible spam",re.I)
LOGIN=re.compile(r"sign in to (?:apply|continue)|log in to (?:apply|continue)|create (?:an |your )account|verify your (?:email|identity)|enter (?:the |your )?(?:verification|one.time|security) code",re.I)


class Browser:
    def __init__(self,store,test_url=None):
        self.store=store; self.test_url=test_url; self.context=None; self.playwright=None
        self.page=None; self.aid=None; self.attempted=False; self.current_host=""; self.host_cache={}; self.denied_write=False; self.denied_request=None; self.upload_payloads={}; self.uploaded_files=set()
        self.auth_write=None;self.ashby_file_handles={};self.ashby_attached_files=set()
        self.workday_grant=None
        self.submission_requests={}

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.playwright=sync_playwright().start()
        s=self.store.settings(); profile=getattr(self,'profile_directory',None) or private_dir(self.store.root/"browser")
        kwargs={"headless":s["headless"],"accept_downloads":False,"service_workers":"block"}
        if getattr(self,'interactive',False):kwargs['headless']=False
        if s["browser_channel"]=="chrome": kwargs["channel"]="chrome"
        elif s["browser_channel"]=="system-chromium":
            executable=shutil.which('chromium') or shutil.which('chromium-browser')
            if not executable:
                self.playwright.stop();raise Blocked('browser_unavailable','Install the system Chromium package')
            kwargs['executable_path']=executable
        try:self.context=self.playwright.chromium.launch_persistent_context(str(profile),**kwargs)
        except Exception:
            self.playwright.stop(); raise Blocked("browser_unavailable","Install Chrome or choose Chromium in settings; close the dedicated sign-in window")
        self.context.set_default_timeout(15000)
        self.context.route("**/*",self._route)
        self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
        for p in self.context.pages:
            if p!=self.page:p.close()
        self._popup_handler=lambda p:p.close() if p!=self.page else None
        self.context.on("page",self._popup_handler)
        self.page.on("response",self._upload_response)
        self.page.on("dialog",lambda d:d.dismiss())
        self.page.on("download",lambda d:d.cancel())
        return self

    def __exit__(self,*exc):
        if self.context:
            with contextlib.suppress(Exception):self.context.close()
        if self.playwright:self.playwright.stop()

    def _route(self,route):
        if not self.attempted:
            try:self.store.checkpoint()
            except Blocked:return route.abort()
        url=route.request.url; p=urlsplit(url)
        # Workday job search uses POST for a read. Recognize it before a
        # one-use remote-write grant so background searches cannot consume it.
        from .workday import search_read
        if search_read(self.current_host,url,route.request.method,getattr(route.request,'post_data_buffer',None)):
            if p.hostname not in self.host_cache:self.host_cache[p.hostname]=public_host(p.hostname)
            return route.continue_() if self.host_cache[p.hostname] else route.abort()
        if self.workday_grant and route.request.method not in ('GET','HEAD','OPTIONS') and p.hostname==self.current_host:
            try:approved=self.workday_grant.consume(url,route.request.method,route.request.post_data_buffer or b'')
            except Blocked:approved=False
            if approved:return route.continue_()
            self.denied_write=True
            self.denied_request={'host':p.hostname,'path':p.path,'method':route.request.method}
            return route.abort()
        if self.auth_write and route.request.method not in ('GET','HEAD','OPTIONS'):
            grant=self.auth_write
            settings=self.store.settings()
            if not settings['employer_accounts'] or not settings['live_enabled']:
                return route.abort()
            pairs=parse_qsl(route.request.post_data or '',keep_blank_values=True)
            approved=(not grant['used'] and route.request.method=='POST'
                      and url==grant['url'] and sorted(pairs)==sorted(tuple(pair) for pair in grant['pairs']))
            if approved:
                grant['used']=True
                return route.continue_()
            self.denied_write=True
            return route.abort()
        if self.test_url and url.startswith(self.test_url):
            self._record_submission_request(route.request)
            return route.continue_()
        host=p.hostname
        if p.scheme!="https" or not host or p.port not in (None,443):return route.abort()
        if host not in self.host_cache:self.host_cache[host]=public_host(host)
        if not self.host_cache[host]:return route.abort()
        # Workday's public shell loads its client from shard-specific vendor
        # hosts. Permit static reads for configured tenants, never auth/draft
        # endpoints or another tenant shard. This grants no account writes.
        from .workday import TENANTS
        if self.current_host in TENANTS:
            shard=self.current_host.split('.')[1]
            if host in {shard+'.myworkday.com',shard+'.myworkdaycdn.com'}:
                static=bool(re.fullmatch(r'/wday/asset/[A-Za-z0-9._/-]+',p.path)) and '..' not in p.path.split('/')
                return route.continue_() if route.request.method in ('GET','HEAD','OPTIONS') and static and not p.username and not p.password else route.abort()
        if self.current_host in TENANTS and host==self.current_host and route.request.method not in ('GET','HEAD','OPTIONS'):
            # Marking a browser attempt does not authorize arbitrary Workday
            # account, draft or final writes. Exact grants above are required.
            self.denied_write=True
            self.denied_request={'host':host,'path':p.path,'method':route.request.method}
            return route.abort()
        if host in UPLOAD_HOSTS:
            payload=getattr(route.request,'post_data_buffer',None) or b''
            approved=self.current_host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'} and route.request.method=='POST' and any(data in payload or h.encode() in payload for h,data in self.upload_payloads.items())
            approved=approved or (host==ASHBY_UPLOAD_HOST and self.current_host=='jobs.ashbyhq.com'
                                  and route.request.method=='POST' and any(data in payload or (h+'.pdf').encode() in payload for h,data in self.upload_payloads.items()))
            return route.continue_() if approved else route.abort()
        # No arbitrary website can receive personal values through an injected pixel or redirect.
        asset_hosts={"www.google.com","www.gstatic.com","fonts.googleapis.com","fonts.gstatic.com",
          "www.recaptcha.net","recaptcha.google.com","cdn.jsdelivr.net","cdnjs.cloudflare.com",
          "static.ashbyhq.com","api.ashbyhq.com","app.ashbyhq.com","cdn.ashbyprd.com","storage.googleapis.com","cdn.greenhouse.io",
          "job-boards.cdn.greenhouse.io","job-boards.eu.cdn.greenhouse.io",
          "api-geocode-earth-proxy.greenhouse.io","boards-api.greenhouse.io","boards.cdn.greenhouse.io","email-address-validator.us.greenhouse.io","email-address-validator.eu.greenhouse.io","api.lever.co","static.lever.co",
          "cdn.lever.co","lever-client-assets.s3.amazonaws.com","lever-client-logos.s3.us-west-2.amazonaws.com",
          "assets.workable.com","apply.workable.com"}
        recruiting_asset=bool(re.fullmatch(r"s[0-9]+-recruiting\.cdn\.greenhouse\.io",host))
        family={self.current_host}
        if 'greenhouse.io' in self.current_host:family|={'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}
        if host not in family|asset_hosts and not recruiting_asset:return route.abort()
        # Pages may read their standard assets; form writes stay on the current ATS family.
        if route.request.method not in ("GET","HEAD","OPTIONS"):
            allowed={self.current_host}
            if self.current_host in {"boards.greenhouse.io","job-boards.greenhouse.io","boards.eu.greenhouse.io","job-boards.eu.greenhouse.io"}:
                allowed|={"boards-api.greenhouse.io","boards.greenhouse.io","job-boards.greenhouse.io"}
            if self.current_host=="jobs.ashbyhq.com":allowed|={"api.ashbyhq.com","storage.googleapis.com"}
            if self.current_host in {"jobs.lever.co","jobs.eu.lever.co"}:allowed|={"api.lever.co"}
            # CAPTCHA endpoints may evaluate passive scoring, but no challenge solving is attempted.
            allowed|={"www.google.com","www.recaptcha.net","recaptcha.google.com"}
            if host not in allowed:return route.abort()
            if not self.attempted and host not in {"www.google.com","www.recaptcha.net","recaptcha.google.com"}:
                payload=route.request.post_data or ""
                reading=False;data=None
                try:
                    data=json.loads(payload)
                    queries=data if isinstance(data,list) else [data]
                    reading=all(isinstance(q,dict) and isinstance(q.get('query'),str) and re.match(r'^\s*query\b',q['query']) for q in queries)
                except (ValueError,TypeError):pass
                if host==self.current_host=='jobs.ashbyhq.com' and p.path=='/api/non-user-graphql' and isinstance(data,dict):
                    variables=data.get('variables',{})
                    if (data.get('operationName')=='ApiCreateFileUploadHandle'
                            and isinstance(data.get('query'),str)
                            and re.match(r'^\s*mutation\s+ApiCreateFileUploadHandle\b',data['query'])
                            and isinstance(variables,dict)):
                        filename=variables.get('filename')
                        uploading_document=next((blob for h,blob in self.upload_payloads.items() if filename==h+'.pdf'),None)
                        reading=bool(uploading_document and variables.get('contentType')=='application/pdf'
                                     and variables.get('contentLength')==len(uploading_document))
                if (host==self.current_host=='jobs.ashbyhq.com' and p.path=='/api/non-user-graphql'
                        and isinstance(data,dict) and data.get('operationName')=='ApiSetFormValueToFile'
                        and isinstance(data.get('query'),str)
                        and re.match(r'^\s*mutation\s+ApiSetFormValueToFile\b',data['query'])):
                    variables=data.get('variables',{})
                    handle=variables.get('fileHandle') if isinstance(variables,dict) else None
                    reading=isinstance(handle,str) and self.ashby_file_handles.get(handle) in self.uploaded_files
                # Upload-only requests are permitted on known ATS upload paths, never arbitrary mutations.
                reading=reading or p.path=='/uncacheable_attributes/presigned_fields'
                passive_check=p.path.startswith("/cdn-cgi/challenge-platform/")
                uploading=bool(re.search(r'/(?:upload|uploads|files|attachments|documents)(?:/|\?|$)',p.path,re.I))
                if not reading and not uploading and not passive_check:
                    # Ashby autosaves even untouched/null fields on form hydration,
                    # and saves the texting selection in its form-render draft.
                    # Suppress the save without treating it as a submission attempt;
                    # local control values are still verified before final submit.
                    autosave=(host==self.current_host=='jobs.ashbyhq.com'
                              and p.path=='/api/non-user-graphql'
                              and bool(queries if isinstance(data,(dict,list)) else [])
                              and all(isinstance(q,dict) and (
                                  isinstance(q.get('query'),str) and re.match(r'^\s*query\b',q['query']) or
                                  q.get('operationName') in {'ApiSetFormValue','ApiSubmitCandidateTextingConsent'} and isinstance(q.get('query'),str)
                                  and re.match(r'^\s*mutation\s+'+re.escape(q['operationName'])+r'\b',q['query'])
                              ) for q in queries))
                    detail={'host':host,'path':p.path,'method':route.request.method}
                    if isinstance(data,dict):detail['operation']=str(data.get('operationName',''))[:100]
                    elif isinstance(data,list):detail['operations']=[str(x.get('operationName',''))[:100] for x in data if isinstance(x,dict)]
                    if autosave:
                        self.store.event('draft_autosave_suppressed',None,detail)
                    else:
                        self.denied_write=True;self.denied_request=detail
                        self.store.event('request_blocked',None,detail)
                    return route.abort()
        self._record_submission_request(route.request)
        return route.continue_()

    def _record_submission_request(self,request):
        if self.aid and self.attempted and request.method not in ('GET','HEAD','OPTIONS') and urlsplit(request.url).hostname==self.current_host:
            key=self._request_key(request)
            # Context routing and page response callbacks can wrap the same
            # request differently. Correlate immutable transport content; drop
            # ambiguous overlapping duplicates rather than misattribute them.
            self.submission_requests[key]=self.aid if key not in self.submission_requests else None

    @staticmethod
    def _request_key(request):
        return (request.url,request.method,hashlib.sha256(getattr(request,'post_data_buffer',None) or b'').hexdigest())

    def _upload_response(self,response):
        request=response.request
        application_id=self.submission_requests.pop(self._request_key(request),None)
        if application_id:
            # Transport success is diagnostic evidence, never a receipt. Do not
            # persist query strings, headers, submitted answers or response bodies.
            with contextlib.suppress(Exception):
                self.store.event('submission_response',application_id,{'stage':'after_submit','host':urlsplit(request.url).hostname,
                    'path':urlsplit(request.url).path,'method':request.method,'status':response.status})
        if (request.method=='POST' and self.current_host=='jobs.ashbyhq.com'
                and urlsplit(request.url).hostname=='jobs.ashbyhq.com'
                and urlsplit(request.url).path=='/api/non-user-graphql' and 200<=response.status<300):
            try:
                query=json.loads(request.post_data or '{}')
                if not isinstance(query,dict) or query.get('operationName') not in ('ApiCreateFileUploadHandle','ApiSetFormValueToFile'):return
                result=response.json()
                variables=query.get('variables',{})
                if query.get('operationName')=='ApiCreateFileUploadHandle':
                    h=next((h for h in self.upload_payloads if variables.get('filename')==h+'.pdf'),None)
                    handle=result.get('data',{}).get('fileUploadHandle',{}).get('handle')
                    if h and isinstance(handle,str):self.ashby_file_handles[handle]=h
                elif query.get('operationName')=='ApiSetFormValueToFile':
                    h=self.ashby_file_handles.get(variables.get('fileHandle'))
                    if h in self.uploaded_files and not result.get('errors') and result.get('data',{}).get('setFormValueToFile'):
                        self.ashby_attached_files.add(h)
            except Exception:pass  # Late responses may arrive while the context closes.
        if request.method!='POST' or urlsplit(request.url).hostname not in UPLOAD_HOSTS or not 200<=response.status<300:return
        payload=request.post_data_buffer or b''
        for h,data in self.upload_payloads.items():
            if data in payload or h.encode() in payload:self.uploaded_files.add(h)

    def _wait_ready(self):
        self.page.wait_for_function("""() => document.body &&
          !document.querySelector('[aria-busy="true"]') &&
          (document.querySelector('form,input:not([type=hidden]),textarea,select') ||
           Array.from(document.querySelectorAll('a,button')).some(e=>/^apply/i.test(e.textContent.trim())) ||
           /no longer|expired|not found|sign in|log in|captcha/i.test(document.body.innerText))""",timeout=20000)
        # Network idle is a bounded hydration aid, not a requirement on analytics-heavy sites.
        with contextlib.suppress(Exception):self.page.wait_for_load_state('networkidle',timeout=4000)

    def _guard(self,job,allow_verification=False):
        if not self.attempted:self.store.checkpoint()
        if self.denied_write:raise Blocked("unapproved_draft_write","Unrecognized pre-submit request blocked: "+json.dumps(self.denied_request or {},sort_keys=True))
        url=self.page.url; host=urlsplit(url).hostname
        if self.test_url and url.startswith(self.test_url):pass
        elif host!=self.current_host:
            raise Blocked("unexpected_redirect",url)
        if self.page.locator('input[type=password]').count():raise Blocked("account_blocked")
        if self.page.locator('iframe[src*="bframe"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]').count():
            raise Blocked("captcha_blocked")
        text=self.page.locator('body').inner_text(timeout=5000)
        if re.search(r"(?:job|position|posting).{0,40}(?:no longer available|no longer accepting|has expired|has been filled|(?:was )?not found)",text,re.I):raise Blocked("expired_posting")
        if LOGIN.search(text) and not (allow_verification and self._email_verification(text)):raise Blocked("account_or_verification_blocked")
        if REFUSE.search(text):raise Blocked("human_work_sample")
        return text

    def _wait_submission_outcome(self,job,accept_verification=True):
        # ATS processing routinely exceeds one second, especially on the Pi.
        deadline=time.monotonic()+45
        while True:
            text=self._guard(job,allow_verification=True)
            if REJECTED.search(text):return text
            if submission_receipt(text) and not self.page.locator('input[type=email]').count():return text
            if accept_verification and self._email_verification(text):return text
            if self.page.locator('[aria-invalid=true]').count():return text
            if time.monotonic()>=deadline:return text
            self.page.wait_for_timeout(500)

    def _email_verification(self, text):
        return bool(re.search(r'verification code was sent.{0,300}to submit your application',text,re.I|re.S)
                    and self.page.get_by_label('Security code',exact=True).count())

    def _outcome_screenshot(self,path,**kwargs):
        try:
            self.page.screenshot(path=str(path),type='jpeg',full_page=True,timeout=5000,**kwargs)
            return path.name
        except Exception as e:
            self.store.event('screenshot_failed',self.aid,{'type':type(e).__name__})
            return ''

    def _continue_email_verification(self,job,submit):
        if not self.store.settings()['gmail_verification']:
            self.store.event('verification_held',self.aid,{'reason':'gmail_verification_disabled'})
            return 'awaiting_verification'
        greenhouse={'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}
        if not self.test_url and job['host'] not in greenhouse:return 'awaiting_verification'
        from .gmail import GmailClient,greenhouse_employer_name
        mail_company=job['company']
        if not self.test_url:
            try:
                name=greenhouse_employer_name(job,self.store.checkpoint,time.monotonic()+15)
                if name:
                    mail_company=name
                    self.store.event('verification_employer_resolved',self.aid,{'company':name,'job_id':job['id']})
            except Exception as e:
                if isinstance(e,Blocked) and e.reason=='paused':raise
                self.store.event('verification_employer_lookup_failed',self.aid,{'reason':getattr(e,'reason',type(e).__name__)})
        challenge=self.store.db.execute('SELECT * FROM verification_challenges WHERE application_id=?',(self.aid,)).fetchone()
        try:client=GmailClient(self.store)
        except Blocked as e:
            if e.reason=='paused':raise
            self.store.event('verification_held',self.aid,{'reason':e.reason});return 'awaiting_verification'
        except Exception as e:
            self.store.event('verification_held',self.aid,{'reason':type(e).__name__});return 'awaiting_verification'
        deadline=time.monotonic()+self.store.settings()['gmail_code_wait_seconds']
        code=None
        while time.monotonic()<deadline:
            self.store.checkpoint()
            try:code=client.find_code(mail_company,challenge['requested'],self.aid,challenge['code_length'])
            except Blocked as e:
                if e.reason=='paused':raise
                self.store.event('verification_held',self.aid,{'reason':e.reason});return 'awaiting_verification'
            except Exception as e:
                self.store.event('verification_held',self.aid,{'reason':type(e).__name__});return 'awaiting_verification'
            if code:break
            self.page.wait_for_timeout(1000)
        if not code:
            self.store.event('verification_held',self.aid,{'reason':'verification_mail_not_found'})
            return 'awaiting_verification'
        self._guard(job,allow_verification=True)
        control=self.page.get_by_label('Security code',exact=True)
        if control.count()!=1:raise Blocked('verification_form_changed')
        # Segmented Greenhouse widgets advance focus on keystrokes, not bulk fills.
        control.fill('')
        control.press_sequentially(code)
        owner=control.locator('xpath=ancestor::form[1]')
        verification_scope=owner if owner.count()==1 else self.page
        verification_submit=verification_scope.get_by_role('button',name=re.compile(r'^submit application$',re.I))
        if verification_submit.count()!=1:raise Blocked('verification_form_changed')
        deadline=time.monotonic()+5
        while not verification_submit.is_enabled() and time.monotonic()<deadline:
            self.store.checkpoint();self.page.wait_for_timeout(100)
        self.store.event('verification_widget_ready',self.aid,{'submit_enabled':verification_submit.is_enabled(),
                         'input_max_length':control.evaluate('(e)=>e.maxLength')})
        if not verification_submit.is_enabled():
            self.store.event('verification_held',self.aid,{'reason':'verification_submit_disabled'})
            return 'awaiting_verification'
        self.store.checkpoint()
        self.store.begin_verification(self.aid)
        verification_submit.click(timeout=15000)
        text=self._wait_submission_outcome(job,accept_verification=False)
        screenshot=self.store.root/'screenshots'/(self.aid+'-verified.jpg')
        # A rejected code may remain visible. Do not persist it in screenshots/text.
        screenshot_kwargs={'mask':[owner if owner.count()==1 else control.locator('xpath=..')]} if control.count() else {}
        screenshot_name=self._outcome_screenshot(screenshot,**screenshot_kwargs)
        text=re.sub(r'\s*'.join(re.escape(c) for c in code),'[verification code redacted]',text)
        confirmed=submission_receipt(text) and not self.page.locator('input[type=email]').count()
        outcome='confirmed' if confirmed else 'awaiting_verification' if self._email_verification(text) else 'unknown'
        self.store.finish(self.aid,outcome,text,screenshot_name)
        return outcome

    def _snapshot(self):
        fields=self.page.evaluate(SNAPSHOT,CONTROLS)
        # Custom dropdown option enumeration is a read task, before any personal value is filled.
        for f in fields:
            self.store.checkpoint()
            if f['type']=='combobox':
                el=self._control(f)
                try:
                    self._open_combobox(el); self.page.wait_for_timeout(200)
                    menu=self._menu(el)
                    if field_key(f['label']) not in ('school','location'):
                        # ATS dropdowns may hydrate after opening. An empty early
                        # read must not turn a choice into a free-text fact.
                        with contextlib.suppress(Exception):menu.get_by_role('option').first.wait_for(state='visible',timeout=5000)
                        if not menu.get_by_role('option').count():
                            # A menu can miss the first click while the form hydrates; reopen once.
                            el.press('Escape');self.page.wait_for_timeout(300)
                            self._open_combobox(el);self.page.wait_for_timeout(200);menu=self._menu(el)
                            with contextlib.suppress(Exception):menu.get_by_role('option').first.wait_for(state='visible',timeout=8000)
                    f['options']=self._option_labels(menu.get_by_role('option'),f)
                    selected=self._option_labels(menu.get_by_role('option',selected=True),f)
                    ashby_autocomplete='ashby-application-form-input-autocomplete' in (el.get_attribute('class') or '')
                    # Ashby marks the keyboard-highlighted suggestion selected;
                    # only the input value establishes a committed selection.
                    if len(selected)==1 and not ashby_autocomplete:f['value']=selected[0]
                    elif not f['value']:
                        f['value']=el.evaluate("""e=>{
                          for(let n=e.parentElement,depth=0;n&&depth<5;n=n.parentElement,depth++){
                            const chips=n.querySelectorAll('[class*=multi-value__label],[class*=multiValueLabel]');
                            if(chips.length)return Array.from(chips).map(x=>x.textContent.trim()).join('\\n');
                            const values=n.querySelectorAll('[class*=singleValue],[class*=single-value]');
                            if(values.length===1){
                              const flag=values[0].querySelector('[class*=iti__flag]');
                              const code=flag&&Array.from(flag.classList).find(c=>/^iti__[a-z]{2}$/.test(c));
                              if(code){
                                const menu=document.getElementById(e.getAttribute('aria-controls'));
                                const options=Array.from((menu||document).querySelectorAll('[role=option]')).filter(o=>o.getClientRects().length&&o.querySelector('.'+code));
                                if(options.length===1)return options[0].textContent.trim();
                              }
                              return values[0].textContent.trim();
                            }
                          }return e.textContent.trim();}""")
                    chips=el.evaluate("""e=>{
                        for(let n=e.parentElement,depth=0;n&&depth<5;n=n.parentElement,depth++){
                            const labels=Array.from(n.querySelectorAll('[class*=multi-value__label],[class*=multiValueLabel]'))
                                .filter(x=>x.getClientRects().length).map(x=>x.textContent.trim()).filter(Boolean);
                            if(labels.length)return labels;
                        }return [];}""")
                    if chips:
                        # React-select hides committed multi-select choices from
                        # its menu. Those visible chips remain valid choices.
                        f['options']=list(dict.fromkeys([*f['options'],*chips]))
                        f['value']='\n'.join(chips)
                    if field_key(f['label']) in ('school','location','major') and el.evaluate('(e)=>e.tagName==="INPUT"'):
                        key=field_key(f['label']);fact=self.store.facts().get(key)
                        if fact:
                            original=el.input_value()
                            found=list(f['options'])
                            # Greenhouse's discipline menu exposes only its first
                            # 100 choices until searched. A combined major may
                            # need the offered Other choice beyond that window.
                            queries=(fact['value'], 'Other') if key=='major' else (fact['value'],fact['value'].split(',')[0] if key=='location' else 'Berkeley' if 'berkeley' in fact['value'].casefold() else fact['value'])
                            for query in dict.fromkeys(queries):
                                self.store.checkpoint();el.fill(query);self.page.wait_for_timeout(1200)
                                found.extend(self._option_labels(self._menu(el).get_by_role('option'),f))
                            el.fill(original)
                            f['options']=sorted(set(found))
                    el.press('Escape')
                except Blocked:raise
                except Exception:raise Blocked('unsupported_widget',f['label'])
        return fields

    @staticmethod
    def _option_labels(options,field):
        # Canonical school names are distinct from country/domain annotations.
        # Preserve exact names; never match a school by a substring of metadata.
        school=field_key(field['label'])=='school'
        return options.evaluate_all("""(nodes,school)=>nodes.map(n=>{
          const name=school?n.querySelector('[class*=canonicalSchoolResultName]'):null;
          return (name||n).textContent.trim();
        }).filter(Boolean)""",school)

    @staticmethod
    def _shape(fields):
        from decimal import Decimal, InvalidOperation
        shapes=[]
        for f in fields:
            shape={k:v for k,v in f.items() if k not in ('value','index','indices','ref','refs') and not (k=='options' and f['type']=='combobox')}
            if f['type']=='number':
                # React mirrors the entered value into the value attribute.
                # Compare the allowed numeric lattice, not its changing origin;
                # min takes precedence and step=any has no lattice at all.
                try:
                    if str(f.get('step')).casefold()=='any':base=None
                    else:
                        step=Decimal(f.get('step') or '1')
                        origin=Decimal(f.get('min') or f.get('step_base') or '0')
                        if not step.is_finite() or step<=0 or not origin.is_finite():raise InvalidOperation
                        base=str(((origin%step+step)%step).normalize())
                    shape['step_base']=base
                except (InvalidOperation,ValueError,TypeError):pass
            shapes.append(shape)
        return shapes

    @classmethod
    def _additional_fields(cls, before, after):
        """Only monotonic additions may restart local field resolution."""
        from collections import Counter
        def signatures(fields):
            return Counter(digest({'shape':shape,'controls':[
                {k:ref[k] for k in ('id','name','tag') if ref.get(k)}
                for ref in field.get('refs',[field.get('ref',{})])]})
                for field,shape in zip(fields,cls._shape(fields)))
        old,new=signatures(before),signatures(after)
        return len(after)>len(before) and not old-new

    def _menu(self, el):
        for name in ('aria-controls','aria-owns'):
            ident=el.get_attribute(name)
            if ident:
                menu=self.page.locator('[id='+json.dumps(ident.split()[0])+']')
                if menu.count()==1:return menu
        return self.page

    @staticmethod
    def _open_combobox(el):
        el.click()
        # Ashby's static autocomplete choices open through the adjacent toggle;
        # focusing the search input alone can leave the popup empty.
        if 'ashby-application-form-input-autocomplete' in (el.get_attribute('class') or '') and el.get_attribute('aria-expanded')=='false':
            toggle=el.locator('xpath=..').locator('button')
            if toggle.count()==1:toggle.click()

    def _control(self, field, option=None):
        ref=field.get('refs',[field.get('ref',{})])[option] if option is not None else field.get('ref',{})
        if ref.get('id'):
            locator=self.page.locator('[id='+json.dumps(ref['id'])+']')
            if locator.count()==1:return locator
        if ref.get('name'):
            locator=self.page.locator(ref.get('tag','input')+'[name='+json.dumps(ref['name'])+']')
            if locator.count()==1:return locator
        # Reacquire anonymous controls by current semantic shape instead of stale indices.
        fresh=self.page.evaluate(SNAPSHOT,CONTROLS)
        matches=[f for f in fresh if f['label']==field['label'] and f['type']==field['type']]
        if len(matches)!=1:raise Blocked('form_changed',field['label'])
        index=matches[0]['indices'][option] if option is not None else matches[0]['index']
        return self.page.locator(CONTROLS).nth(index)

    def _application_scope(self, answers):
        forms=set()
        for answer in answers:
            if answer['provenance'].get('fact_key') not in {'email','full_name','first_name','last_name'}:continue
            owner=self._control(answer['field']).locator('xpath=ancestor::form[1]')
            if owner.count()==1:forms.add(owner.evaluate('(e)=>Array.from(document.forms).indexOf(e)'))
        if len(forms)>1:raise Blocked('unsupported_form','Applicant controls belong to different forms')
        return self.page.locator('form').nth(next(iter(forms))) if forms else self.page

    def _fill(self,answer):
        f=answer['field']; value=answer['value']; controls=self.page.locator(CONTROLS)
        el=self._control(f)
        if f['type']=='select':
            from .ats_widgets import select_exact
            select_exact(el,f,value)
        elif f['type']=='yesno':self._control(f,f['options'].index(value)).click()
        elif f['type']=='radio':self._control(f,f['options'].index(value)).check()
        elif f['type']=='checkbox-group':
            from .answers import selections
            chosen=selections(value,f['options'])
            if not chosen:raise Blocked('option_mismatch',f['label'])
            for i,option in enumerate(f['options']):self._control(f,i).set_checked(option in chosen)
        elif f['type']=='checkbox':el.set_checked(value=='Yes')
        elif f['type']=='combobox':
            from .ats_widgets import select_combobox_exact
            select_combobox_exact(self,el,f,value)
        else:
            if f['type']!='textarea' and '\n' in value:raise Blocked('invalid_single_line_answer',f['label'])
            el.fill(value)

    @staticmethod
    def _filled_number_fields(before,after,answers):
        """Ignore only a blank numeric default mirrored from our verified entry."""
        result=[]
        for fresh in after:
            previous=[f for f in before if f['label']==fresh['label'] and f['type']==fresh['type'] and f.get('ref')==fresh.get('ref')]
            entries=[a for a in answers if a['field'] in previous]
            if (fresh['type']=='number' and len(previous)==len(entries)==1
                    and previous[0].get('step_base') in (None,'','undefined')
                    and not previous[0].get('step') and not fresh.get('step')
                    and fresh.get('step_base')==fresh.get('value')==entries[0]['value']):
                fresh={**fresh,'step_base':previous[0].get('step_base')}
            result.append(fresh)
        return result

    def _pending_verification_fields(self,fields,documents,body):
        completed={d['field']['label'] for d in documents if d['hash'] in self.uploaded_files and d['filename'] in body}
        return [f for f in fields if not (f['type']=='file' and f['label'] in completed)]

    def _verify(self,answers,documents,fields):
        fresh=self._snapshot()
        body=self.page.locator('body').inner_text()
        def remaining(items):return self._pending_verification_fields(items,documents,body)
        compared=self._filled_number_fields(fields,fresh,answers)
        if digest(self._shape(remaining(compared)))!=digest(self._shape(remaining(fields))):
            self.store.event('form_changed',self.current_host,{'before':self._shape(remaining(fields)),'after':self._shape(remaining(fresh))})
            raise Blocked('form_changed')
        for a in answers:
            f=a['field']; el=self._control(f); value=a['value']
            if f['type']=='select':actual=el.locator('option:checked').inner_text().strip()
            elif f['type']=='radio':
                actual=next((f['options'][i] for i,index in enumerate(f['indices']) if self._control(f,i).is_checked()),'')
            elif f['type']=='checkbox-group':
                actual='; '.join(f['options'][i] for i in range(len(f['indices'])) if self._control(f,i).is_checked())
            elif f['type']=='checkbox':actual='Yes' if el.is_checked() else 'No'
            elif f['type']=='yesno':actual=next((x['value'] for x in fresh if x['label']==f['label'] and x['type']=='yesno'),'')
            elif f['type']=='combobox':actual=next((x['value'] for x in fresh if x['label']==f['label'] and x['type']=='combobox'),'')
            else:actual=el.input_value()
            if a['provenance'].get('fact_key')=='phone':
                actual=re.sub(r'[^0-9]','',actual);value=re.sub(r'[^0-9]','',value)
            if actual!=value:raise Blocked('field_verification_failed',f['label'])
        for d in documents:
            if self.current_host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'} and d['hash'] not in self.uploaded_files:
                raise Blocked('upload_verification_failed','Greenhouse has not acknowledged the approved PDF upload')
            if self.current_host=='jobs.ashbyhq.com' and (d['hash'] not in self.uploaded_files or d['hash'] not in self.ashby_attached_files):
                raise Blocked('upload_verification_failed','The approved PDF did not receive a successful upload response')
            if d['hash'] in self.uploaded_files and d['filename'] in body:continue
            el=self._control(d['field'])
            sizes=el.evaluate('(e)=>Array.from(e.files||[]).map(f=>f.size)')
            expected=safe_document(self.store.root/'documents'/d['filename'],self.store.root/'documents').stat().st_size
            if sizes!=[expected] and not (d['hash'] in self.uploaded_files and d['filename'] in self.page.locator('body').inner_text()):raise Blocked('upload_verification_failed')
        if self.page.locator('[aria-invalid=true]').count() or not self.page.evaluate('''() => Array.from(document.forms).every(f=>Array.from(f.elements).every(e=>{
            if(e.type==='checkbox' && e.name && e.getAttribute('description')){
                const group=Array.from(f.elements).filter(x=>x.type==='checkbox'&&x.name===e.name&&x.getAttribute('description')===e.getAttribute('description'));
                // Greenhouse marks each option required, but requires a group answer.
                if(group.length>1)return !group.some(x=>x.required)||group.some(x=>x.checked);
            }
            return !e.checkValidity||e.checkValidity();
        }))'''):raise Blocked('invalid_fields')

    def apply(self,job,live=True):
        self.workday_grant=None
        self.aid=None; self.attempted=False; self.denied_write=False; self.denied_request=None; self.upload_payloads={}; self.uploaded_files=set();self.auth_write=None;self.ashby_file_handles={};self.ashby_attached_files=set()
        self.store.check_job_decision(job['id'])
        self.current_host=job['host']
        if self.test_url:self.current_host=urlsplit(self.test_url).hostname
        elif job['host'] not in ATS_HOSTS|PORTAL_HOSTS:raise Blocked('unapproved_destination')
        from .ats_adapters import adapter_for
        adapter=adapter_for(job)
        navigation,verified_posting=adapter.navigation(job,checkpoint=self.store.checkpoint,deadline=getattr(self.store,'run_deadline',None)) if not self.test_url else (job['url'],None)
        try:
            self.page.goto(navigation,wait_until='domcontentloaded',timeout=45000)
            if not self.test_url and urlsplit(self.page.url).hostname!=urlsplit(navigation).hostname and hasattr(adapter,'embedded'):
                # The board forwarded to a custom careers site. Nothing was filled;
                # open the same application on the ATS's own embedded form.
                navigation,verified_posting=adapter.embedded(job,checkpoint=self.store.checkpoint,deadline=getattr(self.store,'run_deadline',None))
                self.current_host=urlsplit(navigation).hostname
                self.page.goto(navigation,wait_until='domcontentloaded',timeout=45000)
        except Exception as error:
            # Only the initial read navigation is classified for delayed retry.
            from playwright.sync_api import TimeoutError as NavigationTimeout, Error as NavigationError
            self.store.checkpoint()
            code=re.search(r'net::(ERR_[A-Z_]+)',str(error))
            transient={'ERR_NAME_NOT_RESOLVED','ERR_CONNECTION_RESET','ERR_CONNECTION_CLOSED','ERR_TIMED_OUT','ERR_NETWORK_CHANGED','ERR_EMPTY_RESPONSE','ERR_ADDRESS_UNREACHABLE'}
            if isinstance(error,NavigationTimeout) or isinstance(error,NavigationError) and code and code[1] in transient:
                raise Blocked('navigation_failed','Initial posting navigation failed before filling or submission') from error
            if isinstance(error,NavigationError):
                raise Blocked('posting_navigation_review','Initial posting read failed ('+(code[1] if code else 'browser navigation error')+'); review the job link before retrying') from error
            raise
        from playwright.sync_api import TimeoutError as RenderingTimeout
        try:self._wait_ready()
        except RenderingTimeout:
            self.store.checkpoint()
            raise Blocked('posting_fetch_failed','Initial posting did not render application or sign-in controls before timeout; no fields were filled') from None
        text=self._guard(job)
        # Screening must inspect the posting, not questions in its application.
        # For example HP IQ asks about graduating before September; that is a
        # question to answer, not evidence of a mandatory eligibility cutoff.
        posting_text=adapter.posting_text(text,verified_posting)
        # Portal host registration is not proof of a session; inspect the current page too.
        if job['host'] in PORTAL_HOSTS and job['host'] not in self.store.settings()['signed_in_portals'] and not self.store.settings()['employer_accounts'] and job['host'] not in {'www.deshaw.com','explore.jobs.netflix.net','career.mlp.com','jobs.uber.com','www.rentec.com'}:
            raise Blocked('account_blocked','Sign in through the dedicated browser and register this portal')
        # Read the actual posting again before policy checks: list feeds are not eligibility proof.
        from .policy import eligible
        job={**job,'description':posting_text,'answer_scope':job['host']+'|'+self.store.company(job['company'])}
        eligible(job,self.store.settings(),self.store.facts())
        self.store.upsert_job(job)
        all_answers=[]; all_docs=[]; steps=[]; refreshes=0; retained_docs=[]
        for step in range(8):
            try:self._guard(job)
            except Blocked as e:
                if e.reason=='account_blocked' and live and self.store.settings()['employer_accounts']:
                    from .accounts import complete_native_account
                    complete_native_account(self,job)
                    continue
                raise
            fields=self._snapshot()
            if not fields:
                apply=self.page.get_by_role('button',name=re.compile(r'^(?:apply(?: now| for this job)?|application)$',re.I))
                if apply.count()!=1:
                    apply=self.page.get_by_role('link',name=re.compile(r'^(?:apply(?: now| for this job)?|application)$',re.I))
                    # Lever repeats one "Apply for this job" link above and below
                    # the posting; links to the same destination are one entry.
                    targets=set(apply.evaluate_all('(links)=>links.map(e=>e.closest("form")?"":e.href)')) if apply.count()>1 else set()
                    if len(targets)==1 and '' not in targets:apply=apply.first
                if apply.count()!=1 or apply.evaluate('(e)=>!!e.closest("form")'):raise Blocked('unsupported_form','No unambiguous navigation-only application entry')
                apply.click();self._wait_ready();continue
            dropdowns=[f for f in fields if f['type']=='combobox' and field_key(f['label']) not in ('school','location')]
            if len(dropdowns)>=2 and not any(f['options'] for f in dropdowns):
                raise Blocked('posting_fetch_failed','No dropdown on the application loaded its choices; nothing was submitted')
            answers=[]; documents=list(retained_docs); pending=[]; budget_error=None; asked=set()
            for f in fields:
                self.store.checkpoint()
                if not f['label']:
                    if f['required']:raise Blocked('unlabeled_required_field')
                    continue
                if f['type']=='file':
                    kind='transcript' if re.search(r'transcript',f['label'],re.I) else 'resume' if re.search(r'resume|cv',f['label'],re.I) else 'cover_letter' if re.search(r'cover.?letter',f['label'],re.I) else None
                    doc=self.store.db.execute('SELECT * FROM documents WHERE kind=?',(kind,)).fetchone()
                    if kind=='cover_letter' and f['required']:
                        try:
                            from .letters import generate_cover_letter
                            from .provider import LazyProvider
                            doc=generate_cover_letter(self.store,job,LazyProvider(self.store.settings()['model_timeout_seconds'],store=self.store,checkpoint=self.store.checkpoint,observer=lambda stage,detail:self.store.event(stage,job['id'],detail)))
                        except Blocked as e:
                            if e.reason in ('paused','cycle_timeout','model_budget_exhausted','provider_rate_limited'):raise
                            self.store.event('document_blocked',job['id'],{'label':f['label'],'reason':e.reason,'detail':e.detail})
                            pending.append((f,e.reason));continue
                    if not doc:
                        if f['required']:pending.append((f,'missing_document'))
                        continue
                    documents.append({**dict(doc),'field':f});continue
                try:
                    from .provider import LazyProvider
                    provider=None if budget_error else LazyProvider(self.store.settings()['model_timeout_seconds'],store=self.store,checkpoint=self.store.checkpoint,observer=lambda stage,detail:self.store.event(stage,job['id'],detail))
                    answer_context={**job,'previous_answers':answers,'form_questions':[x['label'] for x in fields],'previous_templates':[x['provenance']['template_id'] for x in answers if 'template_id' in x['provenance']], 'previous_writing':[{'question':x['field']['label'],'answer':x['value']} for x in answers if 'sample_parts' in x['provenance'] or 'template_id' in x['provenance']]}
                    a=resolve(self.store,job['answer_scope'],f,provider,context=answer_context)
                    if a:
                        if f['required'] and a['value']=='No' and 'graduation_window_revision' in a['provenance'] and re.search(r'\bi confirm\b',f['label'],re.I):
                            raise Blocked('graduation_mismatch',f['label'])
                        answers.append(a)
                        self.store.event('field_answered',job['id'],{'label':f['label'],'value':a['value'],'provenance':a['provenance']})
                    elif _prefilled(f):raise Blocked('unknown_prefilled_value',f['label'])
                except Blocked as e:
                    if e.reason in ('human_work_sample','paused','cycle_timeout','provider_rate_limited','graduation_mismatch'):raise
                    reason=e.reason
                    if reason=='model_budget_exhausted':budget_error=e
                    # Without a model, a saved draft cannot be refreshed and approved
                    # context cannot be consulted. That is a wait, not a missing fact.
                    elif budget_error and _model_dependent(f,reason):reason='model_budget_exhausted'
                    self.store.event('field_blocked',job['id'],{'label':f['label'],'options':f['options'],'required':f['required'],'reason':reason})
                    if not f['required'] and not _prefilled(f):
                        self.store.resolve_known_question(job['answer_scope'],f['label'],f['options'],field=f,context=job);continue
                    asked.add(self.store.ask(job['id'],job['answer_scope'],f['label'],f['options'],reason,field=f,context=answer_context))
                    pending.append((f,reason))
            if pending:
                # This attempt's questions supersede earlier attempts' wording and widgets.
                self.store.retire_questions(job['id'],asked)
                labels='; '.join(f['label'] for f,_ in pending)
                if budget_error:raise Blocked('model_budget_exhausted',labels+' — '+budget_error.detail)
                # A failed model call is not missing applicant information. Use
                # the bounded delayed retry rather than an indefinite fact hold.
                failures=[reason for _,reason in pending if reason in PROVIDER_FAILURES]
                if len(failures)==len(pending):raise Blocked(failures[0],labels)
                raise Blocked('missing_answers',labels)
            for d in documents:
                path=safe_document(self.store.root/'documents'/d['filename'],self.store.root/'documents')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=d['hash']:raise Blocked('document_tampered')
                self.upload_payloads[d['hash']]=path.read_bytes()
                if d in retained_docs:continue
                self._control(d['field']).set_input_files(str(path))
            if documents:
                with contextlib.suppress(Exception):self.page.wait_for_load_state('networkidle',timeout=8000)
                if self.current_host in {'jobs.ashbyhq.com','boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}:
                    deadline=time.monotonic()+20
                    while any(d['hash'] not in self.uploaded_files for d in documents) and time.monotonic()<deadline:
                        self.store.checkpoint();self.page.wait_for_timeout(250)
            for a in answers:
                self.store.checkpoint()
                self.store.event('field_filling',job['id'],{'label':a['field']['label']})
                self._fill(a)
            try:self._verify(answers,documents,fields)
            except Blocked as error:
                if error.reason!='form_changed':raise
                fresh=self._snapshot()
                compared=self._filled_number_fields(fields,fresh,answers)
                body=self.page.locator('body').inner_text()
                before_pending=self._pending_verification_fields(fields,documents,body)
                after_pending=self._pending_verification_fields(compared,documents,body)
                if refreshes>=3 or not self._additional_fields(before_pending,after_pending):raise
                # An acknowledged upload may remove its file input while a
                # conditional question appears. Keep that verified document in
                # the next pass and its final package; require its acknowledgement
                # and visible filename again during verification.
                retained_docs=[d for d in documents if d['hash'] in self.uploaded_files and d['filename'] in body
                               and not any(f['type']=='file' and f['label']==d['field']['label'] for f in fresh)]
                self._guard(job)
                refreshes+=1
                self.store.event('conditional_fields_revealed',job['id'],{'refresh':refreshes,'added':len(after_pending)-len(before_pending)})
                continue
            self._guard(job)
            all_answers.extend(answers);all_docs.extend(documents)
            steps.append({'step':step,'fields':fields,'url':self.page.url})
            scope=self._application_scope(answers)
            submit=scope.get_by_role('button',name=re.compile(r'^(submit(?: application)?|send application|apply|finish|review & apply)$',re.I))
            if submit.count()!=1:
                nxt=scope.get_by_role('button',name=re.compile(r'^(next|continue|save and continue)$',re.I))
                if nxt.count()!=1:raise Blocked('unsupported_submit','No unique final submit or next button')
                # Save durable draft Q&A before any portal step may save data remotely.
                self.store.event('draft_step',job['id'],{'answers':answers,'documents':documents,'url':self.page.url})
                raise Blocked('multi_step_requires_adapter','This portal step can save data remotely; finish through the dashboard job link')
            package={'job_id':job['id'],'url':job['url'],'answers':all_answers,'documents':all_docs,
                     'facts_hash':digest(self.store.facts()),'steps':steps}
            self.aid=self.store.prepare(job,package)
            before=private_dir(self.store.root/'screenshots')/(self.aid+'-before.jpg')
            self.page.screenshot(path=str(before),type='jpeg',full_page=True)
            if not live:return 'prepared'
            if not submit.is_enabled():raise Blocked('submit_disabled')
            # The committed intent is immediately before the only final click.
            self._guard(job)
            self._verify(answers,documents,fields)
            self.store.checkpoint()
            self.store.begin_submit(self.aid); self.attempted=True
            requested=time.time()
            stage='submit_click'
            try:
                submit.click(timeout=15000)
                stage='outcome_wait'
                text=self._wait_submission_outcome(job)
                stage='outcome_evidence'
                screenshot=self.store.root/'screenshots'/(self.aid+'-after.jpg')
                screenshot_name=self._outcome_screenshot(screenshot)
                confirmed=submission_receipt(text) and not self.page.locator('input[type=email]').count()
                outcome='not_submitted' if REJECTED.search(text) else 'confirmed' if confirmed else 'awaiting_verification' if self._email_verification(text) else 'unknown'
                # Employer errors/receipts often appear below a long posting.
                evidence=text if outcome=='awaiting_verification' or len(text)<=4000 else text[:2000]+'\n…\n'+text[-2000:]
                if outcome=='awaiting_verification':
                    self.store.db.execute('INSERT OR REPLACE INTO verification_challenges VALUES(?,?,?,?,?,?,?,0)',
                        (self.aid,'greenhouse',self.page.url,job['company'],requested,8,'pending'))
                self.store.finish(self.aid,outcome,evidence,screenshot_name)
                if outcome=='awaiting_verification':
                    stage='email_verification'
                    return self._continue_email_verification(job,submit)
                return outcome
            except Exception as e:
                self.store.event('submission_error',self.aid,{'stage':stage,'type':type(e).__name__})
                outcome=self.store.db.execute('SELECT state FROM applications WHERE id=?',(self.aid,)).fetchone()
                if outcome and outcome[0]=='submitting':self.store.finish(self.aid,'unknown',f'{type(e).__name__}: outcome requires verification')
                if isinstance(e,Blocked) and e.reason=='paused':raise
                raise Blocked('submission_unknown')
        raise Blocked('unsupported_form','Step limit reached')
