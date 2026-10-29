from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import time
from urllib.parse import urlsplit, parse_qsl

from .answers import resolve,page_requires_manual_work,field_key
from .discovery import ATS_HOSTS,PORTAL_HOSTS
from .util import Blocked,digest,private_dir,public_host,safe_document,textarea_value

ASHBY_UPLOAD_HOST='ashbyhq-infra-prd-main-app-uploaded-files-us-east-1.s3.us-east-1.amazonaws.com'
LEVER_VERIFICATION_TIMEOUT=5000
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
  const texting=el.type==='radio'&&el.closest('.ashby-application-form-texting-consent-description');
  const consentBody=texting&&texting.querySelector('[class*="_consentBody_"]');
  if(consentBody)descriptions.splice(0,descriptions.length,consentBody);
  // Lever separates a single checkbox's heading from the actual declaration
  // in its label. Approval must bind that wording and its policy links, too.
  if(el.type==='checkbox' && wrapper?.matches('.application-question') && wrapper.querySelectorAll('input[type=checkbox]').length===1){
   for(const node of Array.from(el.labels||[])){
    if(labelText(node) && labelText(node)!==leverLabel(el) && !descriptions.some(n=>n.contains(node)))descriptions.push(node);
   }
  }
  for(const id of (el.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean)){
   // React-select placeholders and validation/live-region text change after
   // entry. They are widget state rather than employer question wording.
   if(/-(?:placeholder|error|live-region)$/.test(id))continue;
   const node=document.getElementById(id);if(node&&!node.matches('[role=alert],[role=log],.select__placeholder')&&!descriptions.includes(node))descriptions.push(node);
  }
  const help_text=descriptions.map(n=>n.innerText||n.textContent).join(' ').replace(/\s+/g,' ').trim();
  const help_links=descriptions.flatMap(n=>Array.from(n.querySelectorAll('a[href]')).map(a=>({text:a.textContent.trim(),url:a.href})));
  const date_format=el.type==='date'?'YYYY-MM-DD':el.classList.contains('ashby-application-form-input-date')?'MM/DD/YYYY':null;
  const ashby=el.closest('[data-field-path][data-field-entry-id]');
  return {...(help_text?{help_text}:{}),...(help_links.length?{help_links}: {}),...(date_format?{date_format}: {}),
    ...(ashby?{ashby_path:ashby.dataset.fieldPath,ashby_entry_id:ashby.dataset.fieldEntryId}:{}),
    ...(texting&&['communicationConsent','smsConsent'].includes(el.name)?{ashby_texting_consent:'sms'}:{})};
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
  const leverLocation=el.matches('input.location-input[name=location]')&&
    !!el.parentElement.querySelector('input[type=hidden][name=selectedLocation]')&&!!el.parentElement.querySelector('.dropdown-results');
  if(leverLocation)type='combobox';
  let indices=[index]; let question=label(el); let options=[]; let value=el.value||'';
  let required=el.required||el.getAttribute('aria-required')==='true'||/[\*✱]/.test(question);
  const ashbyHeading=el.closest('fieldset')?.querySelector('.ashby-application-form-question-title')||el.closest('.ashby-application-form-field-entry')?.querySelector('.ashby-application-form-question-title');
  required=required||!!ashbyHeading?.className.includes('_required_');
  let uploadHeading=false;
  if(type==='file'){
   const upload=el.closest('.file-upload');
   if(upload){
    required=required||upload.getAttribute('aria-required')==='true'||/\*/.test(upload.innerText.split('\n')[0]);
    // Greenhouse's hidden input label says only "Attach". The containing
    // upload group names the actual requested document, including custom files.
    const ids=(upload.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
    const headings=ids.map(id=>document.getElementById(id));
    if((!question || /^attach[\s*✱:]*$/i.test(question)) && upload.getAttribute('role')==='group' &&
       upload.checkVisibility({opacityProperty:true,visibilityProperty:true}) && !upload.closest('[hidden],[aria-hidden="true"],[inert]') &&
       upload.querySelectorAll('input[type=file]').length===1 &&
       headings.length && headings.every(n=>n&&upload.contains(n)&&!n.contains(el))){
     const heading=headings.map(labelText).join(' ').trim();if(heading){question=heading;uploadHeading=true;}
    }
   }
   const identity=(el.id+' '+el.name).toLowerCase();
   if(/transcript/.test(identity)){question='Transcript';uploadHeading=false;}
   else if(/resume|\bcv\b/.test(identity)){question='Resume/CV';uploadHeading=false;}
   else if(/cover.?letter/.test(identity)){question='Cover letter';uploadHeading=false;}
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
   if(type==='radio'&&el.closest('.ashby-application-form-texting-consent-description')){
    question=el.name==='whatsAppConsent'?'Consent to receiving WhatsApp messages':'Consent to receiving text messages';
   }
   value=group.filter(x=>x.checked).map(x=>Array.from(x.labels||[]).map(l=>l.innerText).join(' ').trim()||x.value).join('; ');
  }else if(type==='select'){options=Array.from(el.options).filter(o=>o.value&&!o.disabled).map(o=>o.textContent.trim())}
  else if(type==='checkbox'){options=['Yes','No'];value=el.checked?'Yes':'No'}
  const entrySelector='.education--form,.employment--form,.employment-form,.experience--form,[data-automation-id="educationSection"],[data-automation-id="education"],[data-automation-id="workExperienceSection"],[data-automation-id="workExperience"]';
  const entry=el.closest(entrySelector);
  const entryPeers=entry ? Array.from(document.querySelectorAll(entrySelector)).filter(x=>x.getClientRects().length&&x.tagName===entry.tagName&&(entry.getAttribute('data-automation-id') ? x.getAttribute('data-automation-id')===entry.getAttribute('data-automation-id') : x.className===entry.className)) : [];
  out.push({index,indices,...(leverLocation?{lever_location:true}:{}),...(uploadHeading?{upload_heading:true}:{}),...(type==='select'?{option_values:Array.from(el.options).filter(o=>o.value&&!o.disabled).map(o=>o.value)}:{}),ref:reference(el),refs:indices.map(i=>reference(controls[i])),label:question.replace(/\s+/g,' ').trim(),...details(el),type,options,
   ...((type==='radio'&&el.closest('.ashby-application-form-texting-consent-description')
        || ['checkbox','radio','checkbox-group'].includes(type)&&/^jobs(?:\.eu)?\.lever\.co$/i.test(location.hostname))
       ?{option_values:indices.map(i=>controls[i].value)}:{}),
   ...(el.closest('.education--form,[data-automation-id="educationSection"],[data-automation-id="education"]') ? {section:'education'} : el.closest('.employment--form,.employment-form,.experience--form,[data-automation-id="workExperienceSection"],[data-automation-id="workExperience"]') ? {section:'employment'} : {}),
   ...(entryPeers.length>1 ? {section_entry:entryPeers.indexOf(entry)} : {}),
   required:required||/[\*✱]/.test(question),
   maxlength:el.maxLength||-1,min:el.getAttribute('min'),max:el.getAttribute('max'),step:el.getAttribute('step'),step_base:type==='number'?el.getAttribute('value'):null,pattern:el.getAttribute('pattern'),value,multiple:!!el.multiple});
 });return out;
}"""
CONFIRMED=re.compile(
    r"(?:^|[.!?\n])\s*(?:"
    r"thank you for (?:applying\b|submitting (?:your |the |an )?application\b)|"
    r"(?:your |the )?application (?:has been |was )?(?:successfully )?(?:submitted|received)\b|"
    r"we (?:have |have successfully |successfully )?received your application\b|"
    r"you have successfully applied\b)",re.I)
INCOMPLETE_SUBMISSION=re.compile(
    r"\b(?:your |the )?application (?:(?:has |was |is )?(?:not |never )|(?:hasn|wasn|isn)['’]t )(?:been )?(?:successfully )?(?:submitted|received|complete)\b|"
    r"\b(?:your |the )?application (?:could not|couldn['’]t|cannot|can['’]t) be (?:successfully )?(?:submitted|received|processed)\b|"
    r"\b(?:application )?submission (?:has |was |is )?(?:failed|unsuccessful)\b|"
    r"\b(?:your |the |application )?submission (?:has |was |is )?(?:not |never )(?:been )?(?:successful|completed)\b|"
    r"\b(?:could not|couldn.t|unable to|failed to) (?:submit|process|receive) (?:your |the )?application\b|"
    r"\b(?:error|problem) (?:while )?(?:processing|submitting) (?:your |the )?application\b|"
    r"\b(?:please|to|must) (?:complete|submit|finish) (?:your |the )application\b",re.I)

def submission_receipt(text,before=''):
    # Some Greenhouse employers replace the default receipt. Require both the
    # completed-application acknowledgement and the promised applicant review;
    # a generic thank-you or invitation to apply is not enough.
    if INCOMPLETE_SUBMISSION.search(text):return False
    # Text already present in the posting cannot prove that this click succeeded.
    # Compare acknowledged phrases, ignoring layout and capitalization changes.
    markers=lambda value:{' '.join(match[0].lstrip('.!?\n').casefold().split()) for match in CONFIRMED.finditer(value)}
    return bool(markers(text)-markers(before) or (
        re.search(r"thanks again for applying[!.]",text,re.I) and
        re.search(r"our talent team will carefully review your qualifications and experience",text,re.I) and
        not re.search(r"thanks again for applying[!.]",before,re.I)))
# Model outcomes that may succeed on a later attempt, unlike missing applicant information.
PROVIDER_FAILURES=('provider_timeout','provider_error','provider_invalid_output','writing_unsupported')


_DOCUMENT_REQUEST=re.compile(
    r'(?:(?:please\s+)?(?:upload|attach|provide|submit)\s+)?'
    r'(?:(?:a|an|the|your|copy|of|most\s+recent|latest|current|official|unofficial|academic|college|university)\s+)*'
    r'(?:transcripts?|resumes?(?:\s*(?:/|or)\s*cvs?)?|cvs?(?:\s*(?:/|or)\s*resumes?)?|cover[\s_-]*letters?)'
    r'(?:\s*\((?:pdf|pdf only|required)\))?',re.I)


def _document_kind(field):
    label=' '.join(field['label'].split()).strip(' *✱:')
    kinds=[kind for kind,pattern in (('transcript',r'\btranscripts?\b'),('resume',r'\b(?:resumes?|cvs?)\b'),('cover_letter',r'\bcover.?letters?\b'))
           if re.search(pattern,label,re.I)]
    # A mention in another request is not permission to disclose that document.
    if len(kinds)!=1 or not _DOCUMENT_REQUEST.fullmatch(label):return None
    if field.get('upload_heading') and not field['required']:return None
    return kinds[0]


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
        self.auth_write=None;self.ashby_file_handles={};self.ashby_attached_files=set();self.lever_resume=None
        self.ashby_attachment_state=None;self.post_submit_denial_recorded=False
        self.workday_grant=None
        self.workday_read_scope=None
        self.submission_requests={}
        self.ashby_draft=None;self.ashby_submission=None
        self.native_submission=None;self.greenhouse_uploads={};self.greenhouse_upload_fields={};self.greenhouse_native_options={};self.greenhouse_upload_pending={}
        self.greenhouse_native_locations={}
        self.write_boundary=None
        self.preparing=False
        self.visible=False;self.human_verification_seconds=0
        self.receipt_baseline=''

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.playwright=sync_playwright().start()
        s=self.store.settings(); profile=getattr(self,'profile_directory',None) or private_dir(self.store.root/"browser")
        kwargs={"headless":s["headless"],"accept_downloads":False,"service_workers":"block"}
        if getattr(self,'interactive',False) or getattr(self,'session_check',False):kwargs['headless']=False
        self.visible=not kwargs['headless']
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
        if not getattr(self,'profile_directory',None):
            from .workday_sessions import restore
            restore(self.store,self.context)
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
        if (self.preparing and p.hostname in {'email-address-validator.us.greenhouse.io','email-address-validator.eu.greenhouse.io'}
                and p.path=='/address/validate'):
            self.store.event('preparation_write_suppressed',None,{'host':p.hostname,'path':p.path,'method':route.request.method})
            return route.abort()
        ashby_read=False
        if (self.current_host==p.hostname=='jobs.ashbyhq.com' and p.scheme=='https'
                and p.port in (None,443) and not p.username and not p.password
                and p.path=='/api/non-user-graphql' and route.request.method=='POST'):
            try:
                data=json.loads(route.request.post_data or '')
                queries=data if isinstance(data,list) else [data]
                ashby_read=bool(queries) and all(isinstance(q,dict) and isinstance(q.get('query'),str)
                    and re.match(r'^\s*query\b',q['query'])
                    and not re.search(r'\b(?:mutation|subscription)\b',q['query']) for q in queries)
            except (ValueError,TypeError):pass
        if route.request.method not in ('GET','HEAD','OPTIONS') and not ashby_read and self.write_boundary:
            try:self.write_boundary.check(application_id=self.aid if self.attempted else None)
            except Blocked:
                self.denied_write=True
                self.denied_request={'host':p.hostname,'path':p.path,'method':route.request.method,'reason':'approval_changed'}
                self._record_post_submit_denial()
                return route.abort()
        # Workday job search uses POST for a read. Recognize it before a
        # one-use remote-write grant so background searches cannot consume it.
        from .workday import search_read
        if search_read(self.current_host,url,route.request.method,getattr(route.request,'post_data_buffer',None)):
            if p.hostname not in self.host_cache:self.host_cache[p.hostname]=public_host(p.hostname)
            return self._continue_route(route) if self.host_cache[p.hostname] else route.abort()
        from .workday_native import native_read
        try:
            reviewed_read=native_read(self.current_host,url,route.request.method,
                getattr(route.request,'post_data_buffer',None) or b'',
                getattr(route.request,'headers',{}),scope=self.workday_read_scope)
        except Blocked:reviewed_read=False
        if reviewed_read:
            if p.hostname not in self.host_cache:self.host_cache[p.hostname]=public_host(p.hostname)
            return self._continue_route(route,_reviewed_read=True) if self.host_cache[p.hostname] else route.abort()
        if self.preparing and route.request.method not in ('GET','HEAD','OPTIONS'):
            # A preparation can render approved local values, but must not send
            # a resume parser, attachment, autosave, consent or final mutation.
            try:
                data=json.loads(route.request.post_data or '')
                queries=data if isinstance(data,list) else [data]
                reading=(p.hostname==self.current_host=='jobs.ashbyhq.com'
                         and p.path=='/api/non-user-graphql' and route.request.method=='POST'
                         and bool(queries) and all(isinstance(q,dict) and isinstance(q.get('query'),str)
                             and re.match(r'^\s*query\b',q['query'])
                             and not re.search(r'\b(?:mutation|subscription)\b',q['query']) for q in queries))
            except (ValueError,TypeError):reading=False
            if not reading:
                self.store.event('preparation_write_suppressed',None,{'host':p.hostname,'path':p.path,'method':route.request.method})
                return route.abort()
        if self.workday_grant and route.request.method not in ('GET','HEAD','OPTIONS') and p.hostname==self.current_host:
            try:
                consume=getattr(self.workday_grant,'consume_request',None)
                approved=(consume(route.request) if consume else
                          self.workday_grant.consume(url,route.request.method,route.request.post_data_buffer or b''))
            except Blocked:approved=False
            if approved:return self._continue_route(route,**approved) if isinstance(approved,dict) else self._continue_route(route)
            self.denied_write=True
            self.denied_request={'host':p.hostname,'path':p.path,'method':route.request.method}
            return route.abort()
        if self.auth_write and route.request.method not in ('GET','HEAD','OPTIONS'):
            grant=self.auth_write
            if grant.get('boundary'):
                try:grant['boundary'].check()
                except Blocked:return route.abort()
            settings=self.store.settings()
            if not settings['employer_accounts'] or not settings['live_enabled']:
                return route.abort()
            pairs=parse_qsl(route.request.post_data or '',keep_blank_values=True)
            approved=(not grant['used'] and route.request.method=='POST'
                      and url==grant['url'] and sorted(pairs)==sorted(tuple(pair) for pair in grant['pairs']))
            if approved:
                grant['used']=True
                return self._continue_route(route)
            self.denied_write=True
            return route.abort()
        if self.test_url and url.startswith(self.test_url):
            self._record_submission_request(route.request)
            return self._continue_route(route)
        host=p.hostname
        if p.scheme!="https" or not host or p.port not in (None,443):return route.abort()
        if host not in self.host_cache:self.host_cache[host]=public_host(host)
        if not self.host_cache[host]:return route.abort()
        # Known career-site telemetry is discarded, never sent. Its blocked
        # background POST must not masquerade as a candidate draft write.
        if (host==self.current_host and host in {'careers.qualcomm.com','explore.jobs.netflix.net','career.mlp.com'}
                and p.path=='/vslog' and route.request.method=='POST'):
            return route.abort()
        # Workday's public shell loads its client from shard-specific vendor
        # hosts. Permit static reads for configured tenants, never auth/draft
        # endpoints or another tenant shard. This grants no account writes.
        from .workday import TENANTS
        if self.current_host in TENANTS:
            shard=self.current_host.split('.')[1]
            if host in {shard+'.myworkday.com',shard+'.myworkdaycdn.com'}:
                static=bool(re.fullmatch(r'/wday/asset/[A-Za-z0-9._/-]+',p.path)) and '..' not in p.path.split('/')
                return self._continue_route(route) if route.request.method in ('GET','HEAD','OPTIONS') and static and not p.username and not p.password else route.abort()
        if self.current_host in TENANTS and host==self.current_host and route.request.method not in ('GET','HEAD','OPTIONS'):
            # Marking a browser attempt does not authorize arbitrary Workday
            # account, draft or final writes. Exact grants above are required.
            self.denied_write=True
            self.denied_request={'host':host,'path':p.path,'method':route.request.method}
            return route.abort()
        if host in UPLOAD_HOSTS:
            if self.attempted and self.current_host=='jobs.ashbyhq.com':return route.abort()
            if self.current_host=='jobs.ashbyhq.com':
                from .ashby_attachments import grant_upload
                try:grant=grant_upload(self,route.request,self._request_key(route.request))
                except Blocked:grant=False
                return (self._continue_route(route,**grant) if isinstance(grant,dict) else self._continue_route(route)) if grant else route.abort()
            if self.current_host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}:
                from .greenhouse_submission import grant_upload
                try:grant=grant_upload(self,route.request,self._request_key(route.request))
                except Blocked:grant=False
                return (self._continue_route(route,**grant) if isinstance(grant,dict) else self._continue_route(route)) if grant else route.abort()
            return route.abort()
        # No arbitrary website can receive personal values through an injected pixel or redirect.
        asset_hosts={"www.google.com","www.gstatic.com","fonts.googleapis.com","fonts.gstatic.com",
          "www.recaptcha.net","recaptcha.google.com","cdn.jsdelivr.net","cdnjs.cloudflare.com",
          "static.ashbyhq.com","api.ashbyhq.com","app.ashbyhq.com","cdn.ashbyprd.com","storage.googleapis.com","cdn.greenhouse.io",
          "job-boards.cdn.greenhouse.io","job-boards.eu.cdn.greenhouse.io",
          "api-geocode-earth-proxy.greenhouse.io","boards-api.greenhouse.io","boards.cdn.greenhouse.io","email-address-validator.us.greenhouse.io","email-address-validator.eu.greenhouse.io","api.lever.co","static.lever.co",
          "cdn.lever.co","lever-client-assets.s3.amazonaws.com","lever-client-logos.s3.us-west-2.amazonaws.com",
          "assets.workable.com","apply.workable.com"}
        recruiting_asset=bool(re.fullmatch(r"s[0-9]+-recruiting\.cdn\.greenhouse\.io",host))
        # Lever's own submit handler depends on this verification SDK. Allow
        # its native transport, never solve or interact with a visible challenge.
        # Vendor CDN subdomains vary: https://docs.hcaptcha.com/#content-security-policy-settings
        hcaptcha=(self.current_host in {'jobs.lever.co','jobs.eu.lever.co'}
                  and (host=='hcaptcha.com' or host.endswith('.hcaptcha.com'))
                  and not p.username and not p.password
                  and route.request.method in {'GET','HEAD','OPTIONS','POST'})
        family={self.current_host}
        if 'greenhouse.io' in self.current_host:family|={'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}
        if host not in family|asset_hosts and not recruiting_asset and not hcaptcha:return route.abort()
        # Pages may read their standard assets; form writes stay on the current ATS family.
        if route.request.method not in ("GET","HEAD","OPTIONS"):
            allowed={self.current_host}
            if self.current_host in {"boards.greenhouse.io","job-boards.greenhouse.io","boards.eu.greenhouse.io","job-boards.eu.greenhouse.io"}:
                allowed|=({'boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}
                          if '.eu.greenhouse.io' in self.current_host else
                          {'boards-api.greenhouse.io','boards.greenhouse.io','job-boards.greenhouse.io'})
            if self.current_host=="jobs.ashbyhq.com":allowed|={"api.ashbyhq.com","storage.googleapis.com"}
            if self.current_host in {"jobs.lever.co","jobs.eu.lever.co"}:allowed|={"api.lever.co"}
            # CAPTCHA endpoints may evaluate passive scoring, but no challenge solving is attempted.
            allowed|={"www.google.com","www.recaptcha.net","recaptcha.google.com"}
            if host not in allowed and not hcaptcha:return route.abort()
            if self.attempted and self.current_host=='jobs.ashbyhq.com' and host not in {'www.google.com','www.recaptcha.net','recaptcha.google.com'}:
                try:data=json.loads(route.request.post_data or '')
                except (ValueError,TypeError):data=None
                queries=data if isinstance(data,list) else [data]
                reading=(host=='jobs.ashbyhq.com' and p.path=='/api/non-user-graphql'
                         and route.request.method=='POST' and bool(queries)
                         and all(isinstance(q,dict) and isinstance(q.get('query'),str)
                                 and re.match(r'^\s*query\b',q['query'])
                                 and not re.search(r'\b(?:mutation|subscription)\b',q['query']) for q in queries))
                if not reading:
                    if self.denied_write:return route.abort()
                    try:
                        if self.ashby_submission and self.ashby_submission.allow(route.request,data):
                            self._record_submission_request(route.request)
                            return self._continue_route(route)
                    except Blocked:pass
                    self.denied_write=True
                    self.denied_request={'host':host,'path':p.path,'method':route.request.method}
                    if isinstance(data,dict):
                        # Retain only known operation names and a query digest;
                        # variables, tokens and arbitrary client strings stay private.
                        operation=data.get('operationName')
                        if isinstance(operation,str) and operation in {'ApiSubmitSingleApplicationFormAction','ApiSubmitMultipleFormsAction',
                                'ApiSetFormValue','ApiCreateFileUploadHandle','ApiSetFormValueToFile',
                                'ApiSubmitCandidateTextingConsent'}:
                            self.denied_request['operation']=operation
                        query=data.get('query')
                        if isinstance(query,str) and len(query)<=50000:
                            self.denied_request['query_sha256']=hashlib.sha256(re.sub(r'\s+','',query).encode()).hexdigest()
                    self._record_post_submit_denial()
                    return route.abort()
            if self.attempted and self.native_submission and not hcaptcha and host not in {'www.google.com','www.recaptcha.net','recaptcha.google.com'}:
                try:
                    grant=self.native_submission.grant(route.request,self._request_key(route.request))
                    if grant:
                        self._record_submission_request(route.request)
                        return self._continue_route(route,**grant) if isinstance(grant,dict) else self._continue_route(route)
                except Blocked:pass
                self.denied_write=True
                self.denied_request={'host':host,'path':p.path,'method':route.request.method}
                self._record_post_submit_denial()
                return route.abort()
            if not self.attempted and host not in {"www.google.com","www.recaptcha.net","recaptcha.google.com"} and not hcaptcha:
                if self.lever_resume:
                    grant=self.lever_resume.grant(route.request,self._request_key(route.request))
                    if grant:return self._continue_route(route,**grant)
                payload=route.request.post_data or ""
                reading=False;data=None
                try:
                    data=json.loads(payload)
                    queries=data if isinstance(data,list) else [data]
                    reading=bool(queries) and all(isinstance(q,dict) and isinstance(q.get('query'),str)
                        and re.match(r'^\s*query\b',q['query'])
                        and not re.search(r'\b(?:mutation|subscription)\b',q['query']) for q in queries)
                except (ValueError,TypeError):pass
                if (host==self.current_host=='jobs.ashbyhq.com' and p.path=='/api/non-user-graphql'
                        and isinstance(data,dict) and data.get('operationName') in {'ApiCreateFileUploadHandle','ApiSetFormValueToFile'}):
                    from .ashby_attachments import grant_attachment
                    try:
                        if grant_attachment(self,route.request,data,self._request_key(route.request)):
                            return self._continue_route(route)
                    except Blocked:pass
                    self.denied_write=True
                    self.denied_request={'host':host,'path':p.path,'method':route.request.method}
                    return route.abort()
                if host==self.current_host=='jobs.ashbyhq.com' and p.path=='/api/non-user-graphql' and self.ashby_draft:
                    try:
                        if self.ashby_draft.allow(route.request,data,self._request_key(route.request)):
                            return self._continue_route(route)
                    except Blocked as error:
                        self.denied_write=True;self.denied_request={'host':host,'path':p.path,'method':route.request.method,'reason':error.reason}
                        return route.abort()
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
                                  isinstance(q.get('query'),str) and re.match(r'^\s*query\b',q['query'])
                                  and not re.search(r'\b(?:mutation|subscription)\b',q['query']) or
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
        return self._continue_route(route)

    def _continue_route(self,route,*,_reviewed_read=False,**overrides):
        """Last local authority check, after native payload validation."""
        request=route.request
        reading=_reviewed_read or request.method in ('GET','HEAD','OPTIONS')
        if not reading:
            from .workday import search_read
            reading=search_read(self.current_host,request.url,request.method,getattr(request,'post_data_buffer',None))
            parsed=urlsplit(request.url)
            if (not reading and self.current_host==parsed.hostname=='jobs.ashbyhq.com'
                    and parsed.scheme=='https' and parsed.path=='/api/non-user-graphql'
                    and request.method=='POST'):
                try:
                    data=json.loads(request.post_data or '')
                    queries=data if isinstance(data,list) else [data]
                    reading=bool(queries) and all(isinstance(q,dict) and isinstance(q.get('query'),str)
                        and re.match(r'^\s*query\b',q['query'])
                        and not re.search(r'\b(?:mutation|subscription)\b',q['query']) for q in queries)
                except (ValueError,TypeError):pass
        if not reading:
            try:
                if self.write_boundary:self.write_boundary.check(application_id=self.aid if self.attempted else None)
                if self.native_submission:self.native_submission.boundary.check()
                if self.auth_write and self.auth_write.get('boundary'):self.auth_write['boundary'].check()
                if (self.ashby_submission and self.ashby_submission.used
                        and self.ashby_submission.request_signature
                        and self.ashby_submission.request_signature[:2]==(request.url,request.method)
                        and self.ashby_submission.request_signature==(request.url,request.method,digest(json.loads(request.post_data or '{}')))
                        and time.monotonic()>=self.ashby_submission.write_deadline):
                    raise Blocked('cycle_timeout','The approved final request expired before transport')
            except Blocked:
                self.denied_write=True
                self.denied_request={'host':urlsplit(request.url).hostname,'path':urlsplit(request.url).path,
                                     'method':request.method,'reason':'approval_changed'}
                self._record_post_submit_denial()
                return route.abort()
        return route.continue_(**overrides)

    def _record_submission_request(self,request):
        if self.aid and self.attempted and request.method not in ('GET','HEAD','OPTIONS') and (urlsplit(request.url).hostname==self.current_host or self.native_submission):
            key=self._request_key(request)
            # Context routing and page response callbacks can wrap the same
            # request differently. Correlate immutable transport content; drop
            # ambiguous overlapping duplicates rather than misattribute them.
            self.submission_requests[key]=self.aid if key not in self.submission_requests else None

    def _native_submission_acknowledged(self):
        if self.current_host=='jobs.ashbyhq.com':return bool(self.ashby_submission and self.ashby_submission.acknowledged)
        if self.native_submission:return self.native_submission.receipt_matches(self.page.url)
        return bool(self.test_url)

    @staticmethod
    def _request_key(request):
        return (request.url,request.method,hashlib.sha256(getattr(request,'post_data_buffer',None) or b'').hexdigest())

    def _upload_response(self,response):
        request=response.request
        if self.current_host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}:
            from .greenhouse_submission import observe_upload
            with contextlib.suppress(Exception):observe_upload(self,response,self._request_key(request))
        if self.native_submission:
            with contextlib.suppress(Exception):self.native_submission.observe(response,self._request_key(request))
        if self.current_host=='jobs.ashbyhq.com':
            from .ashby_attachments import observe_attachment
            with contextlib.suppress(Exception):observe_attachment(self,response,self._request_key(request))
        if self.workday_grant and hasattr(self.workday_grant,'observe_response'):
            with contextlib.suppress(Exception):self.workday_grant.observe_response(response)
        if self.ashby_submission:
            with contextlib.suppress(Exception):self.ashby_submission.observe_response(response)
        if self.lever_resume:self.lever_resume.observe(response,self._request_key(request))
        if self.ashby_draft:
            with contextlib.suppress(Exception):self.ashby_draft.observe_posting(request,response)
            with contextlib.suppress(Exception):self.ashby_draft.observe_locations(request,response)
            with contextlib.suppress(Exception):self.ashby_draft.observe_schools(request,response)
            with contextlib.suppress(Exception):self.ashby_draft.observe_save(response,self._request_key(request))
        application_id=self.submission_requests.pop(self._request_key(request),None)
        if application_id:
            # Transport success is diagnostic evidence, never a receipt. Do not
            # persist query strings, headers, submitted answers or response bodies.
            with contextlib.suppress(Exception):
                self.store.event('submission_response',application_id,{'stage':'after_submit','host':urlsplit(request.url).hostname,
                    'path':urlsplit(request.url).path,'method':request.method,'status':response.status})

    def _wait_ready(self):
        self.page.wait_for_function("""() => document.body &&
          !document.querySelector('[aria-busy="true"]') &&
          (document.querySelector('form,input:not([type=hidden]),textarea,select') ||
           Array.from(document.querySelectorAll('a,button')).some(e=>/^apply/i.test(e.textContent.trim())) ||
           /no longer|expired|not found|sign in|log in|captcha/i.test(document.body.innerText))""",timeout=20000)
        # Network idle is a bounded hydration aid, not a requirement on analytics-heavy sites.
        with contextlib.suppress(Exception):self.page.wait_for_load_state('networkidle',timeout=4000)
        if self.ashby_draft:self._wait_ashby_ready()

    def _wait_ashby_ready(self):
        # Ashby's client can replace a renamed tenant after the old page has
        # already satisfied DOM readiness. Wait for a coherent current page,
        # not an old Apply link followed by a transient empty document.
        from playwright.sync_api import Error as RenderingError
        deadline=time.monotonic()+10;redirect=None
        navigation_names=re.compile(r'^(?:apply(?: now| for this job)?|application)$',re.I)
        while True:
            self.store.checkpoint()
            try:
                url=self.page.url
                self.ashby_draft.bind_page(url)
                redirect=None
                fields=self.page.evaluate(SNAPSHOT,CONTROLS)
                posting_read=self.ashby_draft.identity[0] in self.ashby_draft.postings
                application_page=urlsplit(url).path.rstrip('/').endswith('/application')
                entry=posting_read and not application_page and (self.page.get_by_role('link',name=navigation_names).count()
                                       or self.page.get_by_role('button',name=navigation_names).count()
                                       or self._ashby_application_entry(self.ashby_draft.job) is not None)
                body=self.page.evaluate("() => document.body?.innerText || ''")
                stopped=bool(re.search(r'no longer|expired|not found|sign in|log in|captcha',body,re.I))
                # DOM controls can precede the official form response. Native
                # field identities must be available before taking a snapshot.
                if self.page.url==url and (posting_read and (fields or entry) or stopped):return
            except Blocked as error:
                if error.reason!='unexpected_redirect':raise
                redirect=error
            except RenderingError as error:
                if 'Execution context was destroyed' not in str(error) and 'Cannot find context' not in str(error):raise
            if time.monotonic()>=deadline:
                if redirect:raise redirect
                raise Blocked('posting_fetch_failed','The Ashby posting did not finish rendering before timeout; no fields were filled')
            self.page.wait_for_timeout(100)

    def _record_post_submit_denial(self):
        final_grant=self.ashby_submission or self.native_submission
        if (self.denied_write and self.attempted and final_grant
                and not self.post_submit_denial_recorded):
            detail=dict(self.denied_request or {})
            if not final_grant.used:detail['final_transport_used']=False
            self.store.event('post_submit_write_blocked',self.aid,detail)
            self.post_submit_denial_recorded=True

    def _guard(self,job,allow_verification=False):
        if not self.attempted:self.store.checkpoint()
        final_grant=self.ashby_submission or self.native_submission
        if self.denied_write and not (self.attempted and final_grant and final_grant.used):
            self._record_post_submit_denial()
            raise Blocked("unapproved_draft_write","Unrecognized pre-submit request blocked: "+json.dumps(self.denied_request or {},sort_keys=True))
        # A blocked follow-up mutation never gains authority, but it must not
        # erase the native result of the one final request already transmitted.
        # Outcome classification still requires its matching ACK and receipt.
        self._record_post_submit_denial()
        url=self.page.url; host=urlsplit(url).hostname
        if self.test_url and url.startswith(self.test_url):pass
        elif host!=self.current_host:
            # Redirect URLs may carry one-use codes or signed query parameters.
            raise Blocked("unexpected_redirect",'Destination host changed to '+str(host or 'unknown'))
        if self.ashby_draft and not self.attempted:self.ashby_draft.bind_page(url)
        if self.page.locator('input[type=password]').count():raise Blocked("account_blocked")
        # Passive CAPTCHA scoring can preload a hidden challenge frame. Only
        # an exposed widget/challenge requires human intervention.
        captcha_frames=self.page.locator('iframe[src*="bframe"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]')
        # Playwright considers opacity:0 visible. Verification clients can mount
        # a transparent frame (or parent) while a passive check is pending.
        # Recheck each outcome poll so a subsequently exposed challenge stops us.
        # Read the whole selection in one DOM operation. Receipt rendering can
        # remove/reorder frames between separate locator visibility/style calls.
        if captcha_frames.evaluate_all('''frames=>frames.some(frame=>{
            const box=frame.getBoundingClientRect(),style=getComputedStyle(frame);
            if(!box.width||!box.height||style.visibility==='hidden'||style.visibility==='collapse')return false;
            for(let node=frame;node;node=node.parentElement){
                if(Number(getComputedStyle(node).opacity)===0)return false;
            }
            return true;
        })'''):
            raise Blocked("captcha_blocked")
        text=self.page.locator('body').inner_text(timeout=5000)
        if re.search(r"(?:job|position|posting).{0,40}(?:no longer available|no longer accepting|has expired|has been filled|(?:was )?not found)",text,re.I):raise Blocked("expired_posting")
        if LOGIN.search(text) and not (allow_verification and self._email_verification(text)):raise Blocked("account_or_verification_blocked")
        if page_requires_manual_work(text):raise Blocked("human_work_sample")
        return text

    def _wait_submission_outcome(self,job,accept_verification=True):
        from playwright.sync_api import Error as BrowserError
        # ATS processing routinely exceeds one second, especially on the Pi.
        deadline=time.monotonic()+45
        human_deadline=None;phase=None;result='interrupted'
        def announce(value,seconds):
            self.store.event('human_verification',self.aid,{'phase':value,'job_id':job['id'],
                'run_id':getattr(self.store,'active_run_id',None),'expires_at':time.time()+seconds})
        try:
            while True:
                final_grant=self.ashby_submission or self.native_submission
                if final_grant and not final_grant.used:
                    if self.ashby_submission:
                        if not self.ashby_submission.valid_intent(check_answers=False):
                            raise Blocked('application_changed','The approved application changed during verification')
                    else:final_grant.boundary.check()
                try:text=self._guard(job,allow_verification=True)
                except BrowserError as error:
                    # Confirmation navigation can replace the DOM while its
                    # read is in flight, especially on the Pi. Observe again
                    # within the same deadline; closed pages and other errors
                    # still preserve an uncertain final outcome.
                    if (not self.attempted or not final_grant or not final_grant.used
                            or 'Execution context was destroyed, most likely because of a navigation' not in str(error)
                            or time.monotonic()>=deadline):
                        raise
                    self.page.wait_for_timeout(100)
                    continue
                except Blocked as error:
                    if error.reason!='captcha_blocked' or not final_grant:raise
                    if final_grant.used:
                        # A closing verification frame can outlive the native
                        # submit response. Observe the existing request only;
                        # never offer or authorize a second submission.
                        if phase!='checking_receipt':
                            deadline=time.monotonic()+45;announce('checking_receipt',45);phase='checking_receipt'
                        text=self.page.locator('body').inner_text(timeout=5000)
                        received=(self._native_submission_acknowledged() and submission_receipt(text,self.receipt_baseline)
                                  and not self.page.locator('input[type=email]').count())
                        if received or final_grant.failed or time.monotonic()>=deadline:
                            result='outcome_check_complete';return text
                        self.page.wait_for_timeout(250)
                        continue
                    if not self.human_verification_seconds:raise
                    if human_deadline is None:
                        human_deadline=time.monotonic()+self.human_verification_seconds
                        if self.ashby_submission:self.ashby_submission.write_deadline=human_deadline
                        else:
                            final_grant.deadline=human_deadline
                            final_grant.boundary.deadline=human_deadline
                    remaining=human_deadline-time.monotonic()
                    if remaining<=0:
                        result='timed_out';raise Blocked('captcha_blocked') from None
                    if phase!='challenge':announce('challenge',remaining);phase='challenge'
                    # Only a person in the existing visible browser completes
                    # verification. The worker never interacts with the widget.
                    self.page.wait_for_timeout(250)
                    continue
                if phase=='challenge':
                    if not final_grant.used:self.store.checkpoint()
                    deadline=time.monotonic()+45
                    announce('checking_receipt',45);phase='checking_receipt'
                if (REJECTED.search(text)
                        or submission_receipt(text,self.receipt_baseline) and not self.page.locator('input[type=email]').count()
                           and self._native_submission_acknowledged()
                        or self.ashby_submission and self.ashby_submission.failed
                        or self.native_submission and self.native_submission.response_seen and self.native_submission.failed
                           and submission_receipt(text,self.receipt_baseline)
                        or accept_verification and self._email_verification(text)
                        or self.page.locator('[aria-invalid=true]').count() or time.monotonic()>=deadline):
                    result='outcome_check_complete';return text
                self.page.wait_for_timeout(500)
        finally:
            if phase is not None:
                self.store.event('human_verification',self.aid,{'phase':'finished','result':result,
                    'job_id':job['id'],'run_id':getattr(self.store,'active_run_id',None),'expires_at':time.time()})

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
        self.receipt_baseline=self.page.locator('body').inner_text(timeout=5000)
        if self.native_submission:
            prepare=getattr(self.native_submission,'prepare_verification',None)
            if not callable(prepare):raise Blocked('verification_form_changed')
            prepare(code)
        self.store.begin_verification(self.aid)
        verification_submit.click(timeout=15000)
        text=self._wait_submission_outcome(job,accept_verification=False)
        screenshot=self.store.root/'screenshots'/(self.aid+'-verified.jpg')
        # A rejected code may remain visible. Do not persist it in screenshots/text.
        screenshot_kwargs={'mask':[owner if owner.count()==1 else control.locator('xpath=..')]} if control.count() else {}
        screenshot_name=self._outcome_screenshot(screenshot,**screenshot_kwargs)
        text=re.sub(r'\s*'.join(re.escape(c) for c in code),'[verification code redacted]',text)
        confirmed=(submission_receipt(text,self.receipt_baseline) and not self.page.locator('input[type=email]').count()
                   and self._native_submission_acknowledged())
        outcome='confirmed' if confirmed else 'awaiting_verification' if self._email_verification(text) else 'unknown'
        self.store.finish(self.aid,outcome,text,screenshot_name)
        return outcome

    def _snapshot(self):
        fields=self.page.evaluate(SNAPSHOT,CONTROLS)
        if self.ashby_draft:
            from .ashby_education import snapshot
            fields=snapshot(self,fields)
        # Custom dropdown option enumeration is a read task, before any personal value is filled.
        for f in fields:
            self.store.checkpoint()
            if f['type']=='combobox':
                el=self._control(f)
                if f.get('lever_location'):
                    if self.current_host not in {'jobs.lever.co','jobs.eu.lever.co'} and not self.test_url:
                        raise Blocked('unsupported_widget',f['label'])
                    from .ats_widgets import lever_location_snapshot
                    f.update(lever_location_snapshot(self,el));continue
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
                    from .ats_widgets import committed_chips
                    chips=committed_chips(el)
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

    def _ashby_application_entry(self,job):
        """The official Application tab is a posting-bound navigation link."""
        if self.current_host!='jobs.ashbyhq.com':return None
        posting=urlsplit(self.ashby_draft.navigation_url if self.ashby_draft else job['url'])
        if not re.fullmatch(r'/[A-Za-z0-9_-]+/[0-9a-f-]{36}/?',posting.path,re.I):return None
        entry=self.page.get_by_role('tab',name='Application',exact=True)
        if entry.count()!=1:return None
        data=entry.evaluate('(e)=>({tag:e.tagName,href:e.href,form:!!e.closest("form")})')
        target=urlsplit(data.get('href') or '')
        if (data['tag']!='A' or data['form'] or target.scheme!='https'
                or target.hostname!=self.current_host or target.port not in (None,443)
                or target.username or target.password or target.query or target.fragment
                or target.path!=posting.path.rstrip('/')+'/application'):return None
        return entry

    def _fill(self,answer):
        f=answer['field']; value=answer['value']; controls=self.page.locator(CONTROLS)
        if f['type']=='ashby_education':
            from .ashby_education import fill
            return fill(self,answer)
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

    def _fill_ashby_answer(self,answer):
        """Commit each approved field before the next native autosave starts."""
        from .ashby_drafts import same
        draft=self.ashby_draft;field=answer['field']
        # The separate consent control shares Phone's official field identity,
        # but needs its own consent acknowledgement rather than Phone's save.
        keys=[] if field.get('ashby_texting_consent') else [key for key in draft.grants if field.get('ashby_entry_id')==key[0]+'_'+key[1]]
        key=keys[0] if len(keys)==1 else None
        if key and key in draft.saved and same(draft.forms[key[0]]['values'].get(key[1]),draft.grants[key]['value']):
            # The official response already acknowledged this exact approved
            # value. Re-entering it can race React's blur/debounce autosaves.
            return
        if key:draft.saved.discard(key)
        self._fill(answer)
        if not key and not field.get('ashby_texting_consent'):return
        if field['type']!='ashby_education':self._control(field).blur()
        deadline=time.monotonic()+20
        def acknowledged():
            if key:return key in draft.saved and not draft.pending
            return draft.texting is not None and draft.texting.complete and not draft.pending
        while not acknowledged() and not draft.failed and time.monotonic()<deadline:
            self.store.checkpoint();self.page.wait_for_timeout(100)
        if draft.failed or not acknowledged():
            raise Blocked('ashby_draft_save_failed','The employer has not acknowledged the approved field; no final submission was attempted')

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

    def _verify_with_prepare_recovery(self,answers,documents,fields):
        try:
            return self._verify(answers,documents,fields)
        except Blocked as error:
            if not self.preparing or error.reason!='field_verification_failed':raise
            matches=[f for f in fields if f['label']==error.detail]
            approved=[a for a in answers if a['field']['label']==error.detail]
            if (len(matches)!=1 or len(approved)!=1 or matches[0]['type']!='textarea'
                    or approved[0]['field']!=matches[0]):raise
            if self._control(matches[0]).input_value()!='':raise
            # Preparation suppresses remote autosaves; a controlled textarea
            # can be reset while other fields are filled. Retry only locally,
            # once, and require every original verification to pass again.
            self.store.checkpoint()
            self._fill(approved[0])
            return self._verify(answers,documents,fields)

    def _verify(self,answers,documents,fields):
        fresh=self._snapshot()
        body=self.page.locator('body').inner_text()
        def remaining(items):return self._pending_verification_fields(items,documents,body)
        compared=self._filled_number_fields(fields,fresh,answers)
        if digest(self._shape(remaining(compared)))!=digest(self._shape(remaining(fields))):
            self.store.event('form_changed',self.current_host,{'before':self._shape(remaining(fields)),'after':self._shape(remaining(fresh))})
            raise Blocked('form_changed')
        for a in answers:
            if a['field']['type']=='ashby_education':
                from .ashby_education import verify
                verify(self,a);continue
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
            if f['type']=='textarea':value=textarea_value(value)
            if a['provenance'].get('fact_key')=='phone':
                actual=re.sub(r'[^0-9]','',actual);value=re.sub(r'[^0-9]','',value)
            if actual!=value:raise Blocked('field_verification_failed',f['label'])
        for d in documents:
            if self.preparing:
                path=safe_document(self.store.root/'documents'/d['filename'],self.store.root/'documents')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=d['hash']:raise Blocked('document_tampered')
                continue
            if self.lever_resume and d['hash']==self.lever_resume.document['hash']:self.lever_resume.verify()
            if not self.preparing and self.current_host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'} and d['hash'] not in self.uploaded_files:
                raise Blocked('upload_verification_failed','Greenhouse has not acknowledged the approved PDF upload')
            if not self.preparing and self.current_host=='jobs.ashbyhq.com':
                from .ashby_attachments import attached
                if not attached(self,d):
                    raise Blocked('upload_verification_failed','The approved PDF is not acknowledged at its intended employer form field')
            if d['hash'] in self.uploaded_files and d['filename'] in body:continue
            el=self._control(d['field'])
            sizes=el.evaluate('(e)=>Array.from(e.files||[]).map(f=>f.size)')
            expected=safe_document(self.store.root/'documents'/d['filename'],self.store.root/'documents').stat().st_size
            if sizes!=[expected] and not (d['hash'] in self.uploaded_files and d['filename'] in self.page.locator('body').inner_text()):raise Blocked('upload_verification_failed')
        if self.page.locator('[aria-invalid=true]').count() or not self.page.evaluate('''preparing => Array.from(document.forms).every(f=>Array.from(f.elements).every(e=>{
            if(preparing && e.type==='file')return true;
            if(e.type==='checkbox' && e.name && e.getAttribute('description')){
                const group=Array.from(f.elements).filter(x=>x.type==='checkbox'&&x.name===e.name&&x.getAttribute('description')===e.getAttribute('description'));
                // Greenhouse marks each option required, but requires a group answer.
                if(group.length>1)return !group.some(x=>x.required)||group.some(x=>x.checked);
            }
            return !e.checkValidity||e.checkValidity();
        }))''',self.preparing):raise Blocked('invalid_fields')

    def apply(self,job,live=True):
        self.workday_grant=None
        self.workday_read_scope=None
        self.preparing=not live
        # A DNS outage is not a permanent private-address verdict. Re-resolve
        # unavailable destinations at the next attempt, retaining actual denials.
        self.host_cache={host:allowed for host,allowed in self.host_cache.items() if allowed is not None}
        self.aid=None; self.attempted=False; self.denied_write=False; self.denied_request=None; self.upload_payloads={}; self.uploaded_files=set();self.auth_write=None;self.ashby_file_handles={};self.ashby_attached_files=set()
        self.ashby_attachment_state=None;self.post_submit_denial_recorded=False
        self.ashby_submission=None;self.human_verification_seconds=0
        self.native_submission=None;self.greenhouse_uploads={};self.greenhouse_upload_fields={};self.greenhouse_native_options={};self.greenhouse_upload_pending={}
        self.greenhouse_native_locations={}
        self.write_boundary=None
        self.lever_resume=None
        self.store.check_job_decision(job['id'])
        if live:
            previous=self.store.db.execute('SELECT state FROM applications WHERE job_id=?',(job['id'],)).fetchone()
            if previous and previous[0]!='prepared':raise Blocked('duplicate_or_uncertain','This requisition already has a recorded attempt')
            self.store._check_budget(job,self.store.settings())
            from .prior_applications import preflight
            preflight(self.store,job)
        self.current_host=job['host']
        from .ashby_drafts import AshbyDraft
        self.ashby_draft=AshbyDraft(self.store,job) if self.current_host=='jobs.ashbyhq.com' and not self.test_url else None
        if self.test_url:self.current_host=urlsplit(self.test_url).hostname
        elif job['host'] not in ATS_HOSTS|PORTAL_HOSTS:raise Blocked('unapproved_destination')
        if live and not self.test_url:
            from .workday import TENANTS
            native={'jobs.ashbyhq.com','jobs.lever.co','jobs.eu.lever.co','boards.greenhouse.io',
                    'job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}
            if job['host'] not in native|set(TENANTS):
                raise Blocked('unsupported_form','Automatic applying for this portal needs reviewed native submission support; open the job link to finish manually')
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
            destination=urlsplit(navigation).hostname
            unresolved=destination in self.host_cache and self.host_cache[destination] is None
            if isinstance(error,NavigationError) and code and code[1]=='ERR_FAILED' and unresolved:
                self.store.event('navigation_destination_unavailable',job['id'],{'host':destination})
                raise Blocked('navigation_failed','Initial public posting destination could not be resolved; no fields were filled') from None
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
        if self.ashby_draft:self.ashby_draft.job=job
        eligible(job,self.store.settings(),self.store.facts())
        self.store.upsert_job(job)
        from .workday import TENANTS,WorkdayController
        if job['host'] in TENANTS:
            controller=WorkdayController(self,job)
            if live and getattr(self,'workday_commission_job',None)==job['id']:
                outcome=controller.commission()
                if outcome=='confirmed':
                    from .workday import verify_capability
                    record=self.store.db.execute('SELECT id FROM applications WHERE job_id=? AND state=?',(job['id'],'confirmed')).fetchone()
                    if not record:raise Blocked('submission_unknown')
                    try:verify_capability(self.store,job['host'],controller.profile.signature,record['id'])
                    except Exception as error:
                        # Operational promotion is separate from an employer's
                        # already persisted acceptance. Preserve its true outcome.
                        with contextlib.suppress(Exception):
                            self.store.event('workday_capability_held',record['id'],{'type':type(error).__name__})
                return outcome
            return controller.apply(live=live)
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
                entry=self._ashby_application_entry(job)
                if entry is not None:
                    entry.click();self._wait_ready();continue
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
                    kind=_document_kind(f)
                    doc=self.store.db.execute('SELECT * FROM documents WHERE kind=?',(kind,)).fetchone()
                    if kind=='resume' and self.store.settings()['tailored_resumes']:
                        from .tailored_resumes import generate as generate_resume
                        doc=generate_resume(self.store,job)
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
            if live:
                from .approval_boundary import ApprovalBoundary
                proof={'job_id':job['id'],'url':job['url'],'answers':all_answers+answers,'documents':all_docs+documents,
                       'facts_hash':digest(self.store.facts()),'steps':steps+[{'step':step,'fields':fields,'url':self.page.url}]}
                self.write_boundary=ApprovalBoundary(self.store,job,proof)
                from .answers import validate_package
                validate_package(self.store,job,proof)
                self.write_boundary.check()
            for d in documents:
                path=safe_document(self.store.root/'documents'/d['filename'],self.store.root/'documents')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=d['hash']:raise Blocked('document_tampered')
                # Selecting a local file can replace the widget or start a
                # parser even when network writes are blocked. Preparation
                # validates the chosen PDF without touching upload controls.
                if not live:continue
                self.upload_payloads[d['hash']]=path.read_bytes()
                if d in retained_docs:continue
                control=self._control(d['field'])
                if live and self.current_host in {'jobs.lever.co','jobs.eu.lever.co'} and d['kind']=='resume' and control.get_attribute('id')=='resume-upload-input':
                    from .lever_resume import LeverResume
                    self.lever_resume=LeverResume(self,d)
                control.set_input_files(str(path))
            if documents:
                with contextlib.suppress(Exception):self.page.wait_for_load_state('networkidle',timeout=8000)
                if live and (self.lever_resume or self.current_host in {'jobs.ashbyhq.com','boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}):
                    expected_uploads=[self.lever_resume.document['hash']] if self.lever_resume else [d['hash'] for d in documents]
                    deadline=time.monotonic()+20
                    while any(h not in self.uploaded_files for h in expected_uploads) and time.monotonic()<deadline:
                        self.store.checkpoint();self.page.wait_for_timeout(250)
                if live and self.ashby_draft:
                    from .ashby_attachments import attached
                    # S3 receiving bytes is earlier than the native form's
                    # attachment ACK. Do not race text autosaves against it.
                    deadline=time.monotonic()+20
                    while not all(attached(self,d) for d in documents) and time.monotonic()<deadline:
                        self.store.checkpoint();self.page.wait_for_timeout(100)
                    if not all(attached(self,d) for d in documents):
                        raise Blocked('upload_verification_failed','The approved PDF is not acknowledged at its intended employer form field')
            if live and self.ashby_draft:
                proof={'job_id':job['id'],'url':job['url'],'answers':all_answers+answers,'documents':all_docs+documents,
                       'facts_hash':digest(self.store.facts()),'steps':steps+[{'step':step,'fields':fields,'url':self.page.url}]}
                self.ashby_draft.enable(self,answers,proof)
            for a in answers:
                self.store.checkpoint()
                self.store.event('field_filling',job['id'],{'label':a['field']['label']})
                if live and self.ashby_draft:self._fill_ashby_answer(a)
                else:self._fill(a)
            if live and self.lever_resume:self.lever_resume.clear_unapproved_prefills(fields,answers,job['id'])
            try:self._verify_with_prepare_recovery(answers,documents,fields)
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
            if live and self.ashby_draft:
                deadline=time.monotonic()+20
                while not self.ashby_draft.complete and not self.ashby_draft.failed and time.monotonic()<deadline:
                    self.store.checkpoint();self.page.wait_for_timeout(100)
                if not self.ashby_draft.complete:
                    raise Blocked('ashby_draft_save_failed','The employer has not acknowledged every approved field; no final submission was attempted')
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
            if (self.current_host in {'jobs.lever.co','jobs.eu.lever.co'} or self.test_url) and self.page.locator('input#hcaptchaResponseInput[name="h-captcha-response"]').count():
                # A blocked or unavailable SDK leaves Lever's type=button inert.
                # This is a preflight failure, not an uncertain employer attempt.
                from playwright.sync_api import TimeoutError as VerificationTimeout
                try:self.page.wait_for_function("()=>typeof window.hcaptcha?.execute==='function'",timeout=LEVER_VERIFICATION_TIMEOUT)
                except VerificationTimeout:raise Blocked('posting_fetch_failed','Employer verification client did not load; no final submission was attempted') from None
            # The committed intent is immediately before the only final click.
            self._guard(job)
            self._verify(answers,documents,fields)
            self.store.checkpoint()
            if self.write_boundary:self.write_boundary.check()
            self.receipt_baseline=self.page.locator('body').inner_text(timeout=5000)
            if not self.test_url and self.current_host in {'jobs.ashbyhq.com','jobs.lever.co','jobs.eu.lever.co','boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}:
                wait_seconds=self.store.settings()['human_verification_wait_seconds']
                if wait_seconds and not self.visible:
                    raise Blocked('human_verification_unavailable','Human verification requires a visible application browser on the worker desktop')
                self.human_verification_seconds=wait_seconds
            if self.ashby_draft:
                from .ashby_submission import AshbySubmission
                attribution=self.page.evaluate("() => {try{return localStorage.getItem('ashby_utm_source')}catch{return null}}")
                self.ashby_submission=AshbySubmission(self.ashby_draft,self.aid,attribution)
            elif not self.test_url and self.current_host in {'jobs.lever.co','jobs.eu.lever.co'}:
                from .lever_submission import LeverSubmission
                self.native_submission=LeverSubmission(self,job,package,self.aid,submit)
            elif not self.test_url and self.current_host in {'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}:
                from .greenhouse_submission import GreenhouseSubmission
                self.native_submission=GreenhouseSubmission(self,job,package,self.aid,submit)
            if self.write_boundary:self.write_boundary.check()
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
                confirmed=(submission_receipt(text,self.receipt_baseline) and not self.page.locator('input[type=email]').count()
                           and self._native_submission_acknowledged())
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
                # Fixed blocker codes explain a post-click interruption without
                # recording exception text that may contain URLs or form values.
                reason=e.reason if isinstance(e,Blocked) else None
                self.store.event('submission_error',self.aid,{'stage':stage,'type':type(e).__name__,
                                                            **({'reason':reason} if reason else {})})
                outcome=self.store.db.execute('SELECT state FROM applications WHERE id=?',(self.aid,)).fetchone()
                if outcome and outcome[0]=='submitting':
                    # Persist uncertainty before attempting optional evidence:
                    # a dead page or failed capture must never lose the intent.
                    self.store.finish(self.aid,'unknown',f'{type(e).__name__}'+(f' ({reason})' if reason else '')+': outcome requires verification')
                    screenshot=self.store.root/'screenshots'/(self.aid+'-after.jpg')
                    screenshot_name=self._outcome_screenshot(screenshot)
                    if screenshot_name:
                        self.store.db.execute("UPDATE applications SET screenshot=? WHERE id=? AND state='unknown'",(screenshot_name,self.aid))
                if isinstance(e,Blocked) and e.reason=='paused':raise
                detail=('A CAPTCHA appeared after the final click. Verify the outcome with the employer before applying again.'
                        if reason=='captcha_blocked' else
                        'The final click was recorded, but its outcome could not be verified. Check with the employer before applying again.')
                raise Blocked('submission_unknown',detail)
        raise Blocked('unsupported_form','Step limit reached')
