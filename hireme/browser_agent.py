"""Observe, resolve, act and verify applications through the employer's UI.

This executor keeps one app-owned Playwright session alive across steps. Its
planner can choose observed actions; approved facts and document bytes are
resolved and checked by Python. It never attaches to a personal browser.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlsplit, urlunsplit,parse_qsl,urlencode

from .browser import Browser, _document_kind, _prefilled, submission_receipt
from .browser_agent_protocol import UIOperation, guard_pending, origin, read_request
from .browser_observation import ACTIONS, CONTROLS, identity, observe
from .draft_journal import DraftJournal
from .provider import LazyProvider
from .util import Blocked, atomic_json, digest, now, private_dir, public_host, safe_document, textarea_value


VERSION=1
FINAL=re.compile(r'^(?:submit(?: (?:your |my |the )?application)?|send (?:application|introduction)|send|'
                 r'finish(?: application)?|complete application|quick apply|apply(?: now| for this job)?)\s*[.!→]*$',re.I)
NEXT=re.compile(r'^(?:next(?: step)?|continue(?: application)?|save\s*(?:and|&)\s*continue|'
                r'review(?: application| and submit)?|proceed|continue to .+)\s*[.!→]*$',re.I)
ENTRY=re.compile(r'^(?:apply(?: now| online| for this job(?: online)?)?|apply manually|continue (?:my |your )?application|'
                 r'start(?: application)?|get started|begin application|application)\s*[.!→]*$',re.I)
UNSAFE=re.compile(r'\b(?:delete|withdraw|unsubscribe|logout|log out|sign out|remove|discard|'
                  r'share|follow|save job|edit profile|update profile|create account|sign up|register)\b',re.I)
HISTORY_KEYS={
    'employment':[(r'company|employer|organization','organization'),(r'job title|position|^title','title'),
                  (r'location','location'),(r'currently|current|still work','current'),(r'description|responsibilit','description'),
                  (r'start|from','start'),(r'end|to(?:\s|$)','end')],
    'education':[(r'school|university|institution','institution'),(r'degree','degree'),(r'major|field of study','field_of_study'),
                 (r'location','location'),(r'currently|current|still attend','current'),(r'gpa','gpa'),
                 (r'start|from','start'),(r'graduat|end|to(?:\s|$)','end')],
}


def _clean_url(url):
    parts=urlsplit(url)
    # Preserve an explicit draft identity when the site's router keeps it in
    # the query. Tracking parameters and credentials are not recovery targets.
    identity_keys={'applicationId','jobApplicationId','application_id','draftId','draft_id'}
    query=[(key,value) for key,value in parse_qsl(parts.query) if key in identity_keys and re.fullmatch(r'[A-Za-z0-9._~-]{1,300}',value)]
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),''))


def _step_key(step):
    claims=[]
    for answer in step['answers']:
        field=answer['field'];ref=field.get('ref',{}).get('id','')
        questionnaire=ref if re.match(r'(?:primary|secondary)Questionnaire--',ref) else ''
        claims.append([field['label'],questionnaire,field.get('application_agreement_job'),
            answer['value'],answer.get('provenance',{})])
    return digest({'claims':sorted(claims,key=lambda row:json.dumps(row,sort_keys=True)),
        'documents':sorted((d['kind'],d['hash'],d['field']['label']) for d in step['documents'])})


class AdaptiveBrowser(Browser):
    def __init__(self,store,test_url=None,*,connection_id=None,planner=None):
        super().__init__(store,test_url)
        self.connection_id=connection_id
        if connection_id:
            from .platform_connections import profile_path
            self.profile_directory=profile_path(store,connection_id)
        self.planner=planner;self.operation=None;self.job=None;self.allowed_origin=None
        self.stopped_reason=None;self.journal=None;self.completed_steps=[];self.uploads={}

    def __enter__(self):
        super().__enter__()
        self.page.remove_listener('response',self._upload_response)
        return self

    def _route(self,route):
        request=route.request
        try:
            self.store.checkpoint()
            parsed=urlsplit(request.url)
            same=origin(request.url)==self.allowed_origin
            if self.test_url and same:
                safe=True
            else:
                if parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password:return route.abort()
                if parsed.hostname not in self.host_cache:self.host_cache[parsed.hostname]=public_host(parsed.hostname)
                safe=bool(self.host_cache[parsed.hostname])
            if not safe:return route.abort()
            from .workday_site import asset_read
            if asset_read(self.current_host,request):return route.continue_()
            if same and re.fullmatch(r'/(?:vslog|telemetry|analytics|metrics)(?:/.*)?',parsed.path,re.I):
                return route.abort()
            if same and read_request(request):
                if self.operation and self.operation.action['kind']=='search':self.operation.boundary.check()
                from .browser_identity import identity_read,account_email,verify_email
                if identity_read(request,self.connection_id):
                    response=route.fetch(max_redirects=0,max_retries=0,timeout=10000)
                    if response.status==200:
                        try:value=account_email(response.json())
                        except (ValueError,TypeError):value=None
                        if value:self.owner_verified=verify_email(self.store,value)
                    return route.fulfill(response=response)
                return route.continue_()
            if same and self.operation and self.operation.allow(request):
                operation=self.operation
                try:
                    # Observe the exact upstream reply before releasing it to
                    # the client. A client may immediately navigate without
                    # consuming a fetch body, losing a page-level response event.
                    response=route.fetch(max_redirects=0,max_retries=0,timeout=20000)
                    raw=response.body()
                    if len(raw)>2*1024*1024:raise ValueError('Employer reply too large')
                    try:data=json.loads(raw)
                    except (ValueError,UnicodeError):data=None
                    operation.capture(request,response.status,data,headers=response.headers,body=raw)
                    return route.fulfill(response=response)
                except Exception:
                    operation.failure=True
                    return route.abort()
            # Independent static resources may render the app but cannot receive
            # candidate writes or top-level navigation through this exception.
            if request.method=='GET' and request.resource_type in {'script','stylesheet','font','image','media'}:
                return route.continue_()
            # Let the site's verification client run; no challenge controls are
            # exposed as agent actions. A visible challenge stops the loop.
            verification=parsed.hostname in {'www.google.com','www.gstatic.com','www.recaptcha.net','recaptcha.google.com','hcaptcha.com'} or (parsed.hostname or '').endswith('.hcaptcha.com')
            if verification and request.resource_type!='document' and request.method in {'GET','POST','OPTIONS'}:
                return route.continue_()
            if same and request.method not in {'GET','HEAD','OPTIONS'}:
                if self.operation:self.operation.denied=True
                else:self.denied_write=True
            return route.abort()
        except Blocked as error:
            self.stopped_reason=error
            return route.abort()
        except (ValueError,TypeError):
            return route.abort()

    def _check(self):
        from .browser_observation import retry_dom_read
        return retry_dom_read(self.page,self._check_page,checkpoint=self.store.checkpoint)

    def _check_page(self):
        self.store.checkpoint()
        if not self.preparing and not self.store.settings()['live_enabled']:raise Blocked('paused')
        if self.stopped_reason:raise self.stopped_reason
        if self.denied_write and not self.preparing:raise Blocked('browser_operation_blocked','The employer attempted a save outside the active approved browser action')
        if origin(self.page.url)!=self.allowed_origin:raise Blocked('unexpected_redirect')
        if self.connection_id and not getattr(self,'owner_verified',False):
            from .browser_identity import verify_observed_account
            self.owner_verified=verify_observed_account(self)
        if self.page.locator('input[type=password]:visible').count():
            raise Blocked('account_blocked','Sign in using the app’s dedicated employer browser, then resume this saved application')
        if not getattr(self,'owner_verified',True) and self.page.get_by_role('heading',name=re.compile(r'^sign in$',re.I)).count():
            if self.page.locator('button,a').filter(has_text=re.compile(r'^sign in with (?:email|google|linkedin)$',re.I)).filter(visible=True).count():
                raise Blocked('account_blocked','Sign in using the app’s dedicated employer browser, then resume this saved application')
        frames=self.page.locator('iframe[src*="bframe"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]')
        if frames.evaluate_all('''nodes=>nodes.some(e=>e.checkVisibility({visibilityProperty:true,opacityProperty:true})&&
            e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0)'''):
            raise Blocked('captcha_blocked')

    def _receipt(self,text,before=''):
        if submission_receipt(text,before):return True
        if self.connection_id=='workatastartup':
            marker=re.compile(r'(?:^|[.!?\n])\s*(?:(?:your |the )?introduction (?:has been |was )?(?:successfully )?sent\b|'
                r'(?:your |the )?message (?:has been |was )?(?:successfully )?sent\b)',re.I)
            markers=lambda value:{' '.join(m.group().strip('.!?\n ').casefold().split()) for m in marker.finditer(value)}
            return bool(markers(text)-markers(before))
        return False

    @staticmethod
    def _view_ready(view):
        return bool(any(field['label'] or field['required'] for field in view['fields']) or view['errors'] or any(
            ENTRY.fullmatch(a['label']) or NEXT.fullmatch(a['label']) or FINAL.fullmatch(a['label']) for a in view['actions']))

    def _enter_frame(self):
        from .icims import enter_content
        content=enter_content(self.page,limit_reached=getattr(self,'frame_entries',0)>=8)
        if not content:return False
        self.frame_entries=getattr(self,'frame_entries',0)+1
        self.page.goto(content,wait_until='domcontentloaded',timeout=45000)
        self.page.wait_for_function('()=>document.body&&document.body.innerText.trim().length>0',timeout=20000)
        return True

    def _outcome_text(self):
        """Visible page text, including an approved same-requisition content frame. Reads only."""
        text=self.page.locator('body').inner_text()
        from .icims import WRAPPER,enter_content
        try:
            if enter_content(self.page):
                text+='\n'+self.page.frame_locator(WRAPPER).locator('body').inner_text(timeout=2000)
        except Exception:
            # An unapproved or unreadable frame contributes no receipt evidence.
            pass
        return text

    def _wait_ready(self):
        deadline=time.monotonic()+getattr(self,'operation_timeout',20)
        while time.monotonic()<deadline:
            self._check()
            # An iCIMS step can land on its frame wrapper. Open the same
            # requisition's content document; any other frame is a boundary.
            if self._enter_frame():continue
            view=observe(self.page)
            if self._view_ready(view):return view
            if re.search(r'the page you are looking for doesn.t exist',view['text'],re.I):raise Blocked('expired_posting')
            self.page.wait_for_timeout(200)
        raise Blocked('browser_no_progress','The application screen did not finish loading usable controls')

    def _binding(self):
        directory=getattr(self,'profile_directory',self.store.root/'browser')
        directory=private_dir(directory)
        marker=directory/'application-agent-binding.json'
        if marker.is_symlink():raise Blocked('browser_profile_invalid')
        owner=digest(self.store.facts().get('email',{}).get('value','').strip().casefold())
        if not marker.exists():atomic_json(marker,{'binding':secrets.token_hex(32),'owner':owner})
        try:
            data=json.loads(marker.read_text());binding=data['binding']
            if not re.fullmatch(r'[a-f0-9]{64}',binding):raise ValueError()
            if data.get('owner')!=owner:raise Blocked('browser_profile_changed','This application browser belongs to a different confirmed applicant')
        except (OSError,ValueError,KeyError,TypeError):raise Blocked('browser_profile_invalid') from None
        return digest([binding,owner])

    def document_bytes(self,document):
        path=safe_document(self.store.root/'documents'/document['filename'],self.store.root/'documents')
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=document['hash']:raise Blocked('document_tampered')
        return data

    def _base_document(self):
        doc=self.store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
        if not doc:raise Blocked('missing_resume')
        doc=dict(doc)
        if self.store.settings()['tailored_resumes']:
            from .tailored_resumes import generate
            doc=generate(self.store,self.job)
        doc['field']={'label':'Selected resume','type':'file','required':False,'options':[]}
        self.document_bytes(doc)
        return doc

    def _package(self,answers=(),documents=(),fields=(),*,complete=False):
        old_answers=[a for step in self.completed_steps for a in step['answers']]
        old_documents=[d for step in self.completed_steps for d in step['documents']]
        docs={d['kind']:d for d in [self.resume,*old_documents,*documents]}
        steps=[{'fields':step['fields'],'url':step['url']} for step in self.completed_steps]
        if fields:steps.append({'fields':list(fields),'url':_clean_url(self.page.url)})
        return {'job_id':self.job['id'],'url':self.job['url'],'facts_hash':digest(self.store.facts()),
            'answers':old_answers+list(answers),'documents':list(docs.values()),'steps':steps,
            'browser_agent':{'version':VERSION,'profile_binding':self.binding,'last_url':_clean_url(self.page.url),
                'complete':complete,'uploaded_documents':list(self.uploads.values()),
                'completed_steps':self.completed_steps}}

    def _resolve(self,field,previous):
        section=field.get('section')
        if section in HISTORY_KEYS:
            from .workday_history import resolve_field
            histories=self.store.history_entries(section)
            index=field.get('section_entry',0)
            entry=next((entry for entry in histories if entry['id']==field.get('history_entry_id')),None)
            if entry is None:
                if field.get('history_entry_id'):raise Blocked('history_confirmation_required',field['label'])
                if index>=len(histories):raise Blocked('history_confirmation_required',field['label'])
                entry=histories[index]
            history_label=re.sub(r'[✱*]','',field['label'])
            key=next((key for pattern,key in HISTORY_KEYS[section] if re.search(pattern,history_label,re.I)),None)
            if section=='education' and key=='end' and 'end' not in entry['values']:
                key='expected_end'
            if not key:raise Blocked('history_confirmation_required',field['label'])
            field.update(history_entry_id=entry['id'],history_key=key)
            return resolve_field(self.store,field,provider=getattr(self,'provider',None))
        from .answers import resolve
        return resolve(self.store,self.job['answer_scope'],field,self.provider,
            context={**self.job,'previous_answers':previous,'form_questions':self.questions})

    def _read_value(self,field):
        control=self._control_ui(field)
        if field.get('workday_prompt'):
            return next(f['value'] for f in observe(self.page)['fields'] if f['ui_id']==identity(field))
        if field['type']=='checkbox':
            return 'Yes' if control.evaluate('(e)=>!!e.checked||e.getAttribute("aria-checked")==="true"') else 'No'
        if field['type']=='radio':
            for i,value in enumerate(field['options']):
                if self._control_ui(field,i).evaluate('(e)=>!!e.checked||e.getAttribute("aria-checked")==="true"'):return value
            return ''
        if field['type']=='select':return control.locator('option:checked').inner_text().strip() if control.input_value() else ''
        if field.get('contenteditable'):return control.inner_text()
        if field['type']=='combobox' and control.evaluate('(e)=>e.tagName==="BUTTON"'):return control.inner_text().strip()
        return control.input_value()

    def _control_ui(self,field,option=None):
        fresh=observe(self.page)
        matches=[f for f in fresh['fields'] if f['ui_id']==identity(field)]
        if len(matches)!=1:raise Blocked('form_changed',field['label'])
        actual=matches[0]
        return self.page.locator(CONTROLS).nth(actual['indices'][option] if option is not None else actual['index'])

    def _matches(self,answer):
        actual=self._read_value(answer['field']);expected=answer['value']
        if answer['field'].get('history_date_part')=='month_number' and re.fullmatch(r'\d{1,2}',actual) and re.fullmatch(r'\d{1,2}',expected):
            return 1<=int(actual)<=12 and int(actual)==int(expected)
        if answer['field']['type']=='textarea':
            return textarea_value(actual)==textarea_value(expected)
        if answer.get('provenance',{}).get('fact_key')=='phone':
            return re.sub(r'\D','',actual)==re.sub(r'\D','',expected)
        return actual==expected

    def _document(self,field):
        kind=_document_kind(field)
        if kind=='resume':return {**self.resume,'field':field}
        if kind=='cover_letter' and field['required']:
            from .letters import generate_cover_letter
            return {**generate_cover_letter(self.store,self.job,self.provider),'field':field}
        if kind:
            row=self.store.db.execute('SELECT * FROM documents WHERE kind=?',(kind,)).fetchone()
            if row:return {**dict(row),'field':field}
        return None

    def _check_upload_policy(self,field,view):
        if self.connection_id!='handshake':return
        private=any(re.search(r'visibility|who can (?:see|view)|privacy',f['label'],re.I)
            and re.fullmatch(r'private(?: document)?',f.get('value','').strip(),re.I) for f in view['fields'])
        status=self._control_ui(field).evaluate('e=>e.closest("[role=dialog],form,section")?.innerText||""')
        if not private and not re.search(r'\b(?:document )?visibility\s*:\s*private\b',status,re.I):
            raise Blocked('connection_document_visibility_review','Verify Private document visibility in this platform’s dedicated application browser before uploading. The worker does not replace existing profile documents.')

    def _lookup_choice(self,field,answers):
        if field['ui_id'] in self.searched and not field.get('workday_prompt'):return None
        probe={**field,'type':'text','options':[]}
        probe.pop('option_values',None)
        try:query=self._resolve(probe,answers)
        except Blocked as error:
            if error.reason in {'paused','cycle_timeout','model_budget_exhausted','provider_rate_limited'}:raise
            return None
        provenance=query.get('provenance',{}) if query else {}
        if provenance.get('fact_key') not in {'school','degree','major','city','location','country','state'} and 'history_entry_id' not in provenance:return None
        candidates=[query['value']]
        if field.get('workday_prompt') and (provenance.get('fact_key')=='major' or provenance.get('history_key')=='field_of_study'):
            # Search terms narrow a menu; only the original confirmed value
            # can authorize a selected answer. Native prompts often cannot
            # match a full compound program name as one search phrase.
            parts=re.split(r'\s+(?:and|&)\s+',query['value'],flags=re.I)
            candidates.extend(part.strip() for part in parts)
            candidates.extend(part.split()[0] for part in parts if part.split() and len(part.split()[0])>=4)
        tried=getattr(self,'search_queries',{}).get(field['ui_id'],set())
        term=next((value for value in candidates if value and value not in tried),None)
        if term is None:return None
        return {'id':'search:'+field['ui_id']+':'+digest(term),'kind':'search','label':'Look up approved value for '+field['label'],
            'field':field,'answer':query,'query_text':term}

    def _bind_history(self,view):
        """Bind saved row identities before filling, independent of server order."""
        if not hasattr(self,'history_bindings'):self.history_bindings={}
        for section in HISTORY_KEYS:
            rows={}
            for field in view['fields']:
                if field.get('section')==section:
                    key=(section,field.get('history_ref',str(field.get('section_entry',0))))
                    rows.setdefault(key,[]).append(field)
            entries=self.store.history_entries(section)
            assigned={self.history_bindings[key] for key in rows if key in self.history_bindings};remaining=[]
            for key,fields in rows.items():
                bound=self.history_bindings.get(key)
                if bound:
                    assigned.add(bound)
                    for field in fields:field['history_entry_id']=bound
                    continue
                identity={}
                for field in fields:
                    value=field.get('value','').strip()
                    if not value:continue
                    label=field['label']
                    part=('institution' if re.search(r'school|university|institution',label,re.I) else None) if section=='education' else (
                        'organization' if re.search(r'company|employer|organization',label,re.I) else
                        'title' if re.search(r'job title|position|^title',label,re.I) else None)
                    if part:identity[part]=value
                matches=[entry for entry in entries if identity and all(
                    entry['values'].get(part,'').strip().casefold()==value.casefold() for part,value in identity.items())]
                available=[entry for entry in matches if entry['id'] not in assigned]
                if len(available)==1:
                    entry=available[0];assigned.add(entry['id']);self.history_bindings[key]=entry['id']
                    for field in fields:field['history_entry_id']=entry['id']
                elif identity and (not matches or len(available)>1):
                    raise Blocked('history_confirmation_required','The saved '+section+' row does not uniquely match confirmed history')
                elif identity:
                    raise Blocked('history_confirmation_required','A duplicate saved history row needs outcome review')
                else:remaining.append((key,fields))
            unused=[entry for entry in entries if entry['id'] not in assigned]
            for (key,fields),entry in zip(remaining,unused):
                self.history_bindings[key]=entry['id']
                for field in fields:field['history_entry_id']=entry['id']

    def _choices(self,view):
        choices=[];answers=[];documents=[];pending=[]
        self._bind_history(view)
        self.questions=[f['label'] for f in view['fields']]
        # Posting pages may contain contact/newsletter controls. Prefer their
        # application entry over entering applicant data into another form.
        step_heading=any(re.fullmatch(r'my information|my experience|application questions|review(?: application)?|'
            r'voluntary disclosures|self identification',h,re.I) for h in view['headings'])
        application_upload=any(f['type']=='file' and _document_kind(f)=='resume' for f in view['fields'])
        if not step_heading and not application_upload and not self.completed_steps:
            entries=[a for a in view['actions'] if not a['in_form'] and ENTRY.fullmatch(a['label'])
                and (not a['href'] or origin(a['href'])==self.allowed_origin)]
            if entries:return [{'id':'entry:'+a['ui_id'],'kind':'entry','label':a['label'],'action':a} for a in entries],[],[],[]
        for field in view['fields']:
            self._check()
            if field['type']=='checkbox' and re.search(r'agree to our Terms and Conditions and Applicant Privacy Policy',field['label'],re.I):
                field['application_agreement_job']=self.job['id']
            if not field['label']:
                if field['required']:pending.append((field,'unlabeled_required_field'))
                continue
            if field['type']=='file':
                document=self._document(field)
                if not document:
                    if field['required']:pending.append((field,'missing_document'))
                    continue
                documents.append(document)
                if not self.preparing and document['hash'] not in self.uploads and document['hash'] not in self.staged_documents:
                    self._check_upload_policy(field,view)
                    choices.append({'id':'upload:'+field['ui_id'],'kind':'upload','label':'Attach approved '+document['kind'],
                        'field':field,'document':document})
                continue
            if field['type']=='combobox' and not field['options'] and field.get('selected_options'):
                field.update(options=field['selected_options'],option_values=field['selected_options'])
            if field['type']=='combobox' and not field['options']:
                if field.get('option_groups'):
                    probe={**field,'options':field['option_groups'],'option_values':field['option_groups']}
                    try:group=self._resolve(probe,answers)
                    except Blocked as error:
                        if error.reason in {'paused','cycle_timeout','model_budget_exhausted','provider_rate_limited'}:raise
                        group=None
                    if group:
                        choices.append({'id':'expand:'+field['ui_id']+':'+digest(group['value']),
                            'kind':'expand','label':'Inspect '+group['value']+' choices for '+field['label'],
                            'field':field,'value':group['value']})
                    elif field['required']:
                        pending.append((field,'required_answer_missing'))
                        choices.append({'id':'close_options:'+field['ui_id'],'kind':'close_options',
                            'label':'Close inspected choices for '+field['label'],'field':field})
                    continue
                cached=self.option_cache.get(field['ui_id'])
                if cached and (not field.get('workday_prompt') or field['value'] in cached['options']):
                    field.update(cached)
                if field['ui_id'] not in self.opened:
                    choices.append({'id':'options:'+field['ui_id'],'kind':'options','label':'Inspect choices for '+field['label'],'field':field})
                    continue
                lookup=self._lookup_choice(field,answers) if not field['options'] else None
                if lookup:
                    choices.append(lookup)
                    continue
            if field['type']=='combobox' and field['options']:
                self.option_cache[field['ui_id']]={k:field[k] for k in ('options','option_values') if k in field}
            try:
                answer=self._resolve(field,answers)
                if answer:
                    # Virtualized prompts initially expose only a small slice
                    # of their choices. Search the confirmed name before using
                    # a catch-all that would discard a known institution.
                    lookup=self._lookup_choice(field,answers) if field.get('workday_prompt') and field.get('options_visible') and answer['value'].casefold()=='other' else None
                    if lookup and lookup['answer']['value'].casefold()!='other':
                        choices.append(lookup)
                        continue
                    answers.append(answer)
                    if not self._matches(answer):choices.append({'id':'fill:'+field['ui_id'],'kind':'fill',
                        'label':'Fill approved answer for '+field['label'],'field':field,'answer':answer})
                    elif field['type']=='combobox' and field.get('options_visible'):
                        choices.append({'id':'close_options:'+field['ui_id'],'kind':'close_options',
                            'label':'Close verified choices for '+field['label'],'field':field})
                elif field['required'] or _prefilled(field):
                    pending.append((field,'required_answer_missing'))
                    if field.get('options_visible'):
                        choices.append({'id':'close_options:'+field['ui_id'],'kind':'close_options',
                            'label':'Close inspected choices for '+field['label'],'field':field})
                elif field.get('options_visible'):
                    choices.append({'id':'close_options:'+field['ui_id'],'kind':'close_options',
                        'label':'Close optional choices for '+field['label'],'field':field})
            except Blocked as error:
                if error.reason in {'paused','cycle_timeout','model_budget_exhausted','provider_rate_limited'}:raise
                lookup=self._lookup_choice(field,answers) if field.get('workday_prompt') and field.get('options_visible') else None
                if lookup:
                    choices.append(lookup)
                    continue
                if field['required'] or _prefilled(field):pending.append((field,error.reason))
                if field.get('options_visible'):
                    choices.append({'id':'close_options:'+field['ui_id'],'kind':'close_options',
                        'label':'Close inspected choices for '+field['label'],'field':field})
        # Add only enough visible history rows to represent confirmed entries.
        for action in view['actions']:
            section=action.get('section')
            if section in HISTORY_KEYS and re.fullmatch(r'add(?: another)?(?: (?:work )?(?:experience|education))?',action['label'],re.I):
                rows={f.get('section_entry',0) for f in view['fields'] if f.get('section')==section}
                if len(rows)<len(self.store.history_entries(section)):
                    choices.append({'id':'add:'+action['ui_id'],'kind':'add','label':'Add confirmed '+section+' entry','action':action})
        if pending:
            # Do not upload into a form whose disclosure/consent or prefilled
            # controls still need review. Known factual fields can be resolved
            # incrementally without disclosing a document under unknown terms.
            choices=[choice for choice in choices if choice['kind']!='upload']
        ready=not choices and not pending
        for action in view['actions']:
            label=action['label'].strip()
            if UNSAFE.search(label):continue
            target=action.get('href')
            if target and (origin(target)!=self.allowed_origin or re.search(r'/(?:login|access|sign_in|register)(?:/|$)',urlsplit(target).path)):
                continue
            if not view['fields'] and ENTRY.fullmatch(label) and not re.search('quick',label,re.I):
                choices.append({'id':'entry:'+action['ui_id'],'kind':'entry','label':label,'action':action})
            elif ready and NEXT.fullmatch(label):
                choices.append({'id':'next:'+action['ui_id'],'kind':'next','label':label,'action':action})
            elif ready and FINAL.fullmatch(label) and (view['fields'] or self.completed_steps):
                choices.append({'id':'submit:'+action['ui_id'],'kind':'submit','label':label,'action':action})
        return choices,answers,documents,pending

    def _choose(self,view,choices):
        # Filling resolved facts is a deterministic tool operation. The planner
        # is used for navigation/competing actions, not to invent field values.
        immediate=[a for a in choices if a['kind'] in {'options','expand','close_options','search','fill','upload','add'}]
        if immediate:
            priority={'options':0,'expand':0,'close_options':0,'search':1,'fill':2,'add':3,'upload':4}
            return min(immediate,key=lambda action:-1 if action['kind']=='expand' or
                action['kind'] in {'fill','search'} and action['field'].get('options_visible') else priority[action['kind']])
        if len(choices)==1:return choices[0]
        if not choices:return None
        if self.planner:
            selected=self.planner(view,[{k:a[k] for k in ('id','kind','label')} for a in choices],self.memory[-8:])
        else:
            selected=self.provider.plan_browser_step(
                {'company':self.job['company'],'title':self.job['title'],'location':self.job.get('location','')},
                {'headings':view['headings'],'errors':view['errors'],'text':view['text'][:12000]},
                [{k:a[k] for k in ('id','kind','label')} for a in choices],self.memory[-8:])
        if selected is None:return None
        matches=[action for action in choices if action['id']==selected]
        if len(matches)!=1:raise Blocked('provider_invalid_output','The browser planner selected an unobserved action')
        return matches[0]

    def _click(self,action):
        fresh=observe(self.page)
        matches=[a for a in fresh['actions'] if a['ui_id']==action['ui_id']]
        if len(matches)!=1:raise Blocked('form_changed',action['label'])
        control=self.page.locator(ACTIONS).nth(matches[0]['index'])
        control.click(timeout=15000,no_wait_after=True)

    def _fill_ui(self,answer):
        field=answer['field'];value=answer['value'];control=self._control_ui(field)
        if field['type']=='checkbox':
            if self._read_value(field)!=value:control.click()
        elif field['type']=='radio':self._control_ui(field,field['options'].index(value)).click()
        elif field['type']=='select':control.select_option(value=field['option_values'][field['options'].index(value)])
        elif field['type']=='combobox':
            if field.get('workday_prompt'):
                self._prompt_option(field,value).click()
                return
            options=self.page.get_by_role('option',name=value,exact=True).filter(visible=True)
            if options.count()!=1:
                control.click()
                options.wait_for(state='visible',timeout=5000)
            if options.count()!=1:raise Blocked('option_mismatch',field['label'])
            options.click()
        else:
            control.fill(textarea_value(value) if field['type']=='textarea' else value)
            control.blur()

    def _prompt_option(self,field,value,*,group=False):
        fresh=observe(self.page)
        actual=next((f for f in fresh['fields'] if f['ui_id']==identity(field)),None)
        key='option_groups' if group else 'options'
        if not actual or actual.get(key,[]).count(value)!=1:
            raise Blocked('option_mismatch',field['label'])
        index=actual['group_indices' if group else 'option_indices'][actual[key].index(value)]
        return self.page.locator('[role=option]').nth(index)

    def _open_options(self,field):
        control=self._control_ui(field)
        icon=control.locator('xpath=ancestor::*[@data-automation-id="multiSelectContainer"][1]').locator('[data-automation-id=promptIcon]') if field.get('workday_prompt') else None
        if icon is not None and icon.count()==1:icon.click()
        else:control.click()

    def _search_input(self,field):
        control=self._control_ui(field)
        if not field.get('workday_prompt'):return control
        widget=control.get_attribute('data-uxi-multiselect-id')
        inputs=self.page.locator('input[data-automation-id=searchBox][data-uxi-widget-type=selectinput]').filter(visible=True)
        indices=inputs.evaluate_all('(nodes,widget)=>nodes.map((e,i)=>e.getAttribute("data-uxi-multiselect-id")===widget?i:-1).filter(i=>i>=0)',widget)
        if len(indices)>1:raise Blocked('unsupported_widget','The prompt has more than one search input')
        # The popup swaps its input as it opens. Its semantic selector remains
        # stable when typing re-renders controls ahead of the original DOM index.
        return inputs.nth(indices[0]) if indices else control

    def _perform(self,choice,view,answers,documents):
        kind=choice['kind']
        if kind=='expand':
            self._prompt_option(choice['field'],choice['value'],group=True).click()
            self.option_cache.pop(choice['field']['ui_id'],None)
            self.page.wait_for_timeout(200)
            self.memory.append({'action':choice['label'],'result':'choices expanded'})
            return
        if kind=='close_options':
            control=self._control_ui(choice['field'])
            self.page.keyboard.press('Escape')
            self.page.wait_for_timeout(350)
            fresh=observe(self.page)
            still_open=any(f['ui_id']==choice['field']['ui_id'] and f.get('options_visible') for f in fresh['fields'])
            if still_open and control.evaluate('(e)=>e.tagName==="BUTTON"'):control.click()
            control.blur()
            self.page.wait_for_timeout(350)
            fresh=observe(self.page)
            if any(f['ui_id']==choice['field']['ui_id'] and f.get('options_visible') for f in fresh['fields']):
                headings=self.page.get_by_role('heading').filter(visible=True)
                if headings.count():headings.last.click()
                self.page.wait_for_timeout(350)
            self.memory.append({'action':choice['label'],'result':'choices closed'})
            return
        if kind=='options':
            self._open_options(choice['field'])
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                self._check();fresh=observe(self.page)
                match=next((f for f in fresh['fields'] if f['ui_id']==choice['field']['ui_id']),None)
                if match and (match['options'] or match.get('option_groups')):break
                self.page.wait_for_timeout(100)
            self.opened.add(choice['field']['ui_id'])
            self.memory.append({'action':choice['label'],'result':'choices inspected'})
            return
        if self.preparing:
            if kind=='fill':self._fill_ui(choice['answer']);return
            if kind in {'search','upload','next','add'}:
                self._save_partial(answers,documents)
                raise Blocked('browser_preparation_incomplete','This multi-step draft needs live employer saves to advance; local preparation sent no values or documents')
        # Partial operation packages contain only source-approved fields. The
        # complete visible step is captured only when every required field is resolved.
        approved_fields=[a['field'] for a in answers]+[d['field'] for d in documents]
        if kind=='search':
            answers=[*answers,choice['answer']]
            approved_fields.append(choice['answer']['field'])
        package=self._package(answers,documents,approved_fields)
        if kind=='submit':return self._submit(choice,view,answers,documents)
        if kind=='next':self._verify_current(view,answers)
        operation=UIOperation(self,choice,package,document=choice.get('document'))
        self.operation=operation
        try:
            self._check()
            if kind=='fill':self._fill_ui(choice['answer'])
            elif kind=='search':
                if choice['field'].get('workday_prompt'):
                    current=next((f for f in observe(self.page)['fields'] if f['ui_id']==choice['field']['ui_id']),None)
                    if current and not current.get('options_visible'):self._open_options(choice['field'])
                    self.page.wait_for_timeout(200)
                control=self._search_input(choice['field'])
                if control.evaluate('(e)=>e.tagName!=="INPUT"'):
                    menus=self.page.get_by_role('listbox').filter(visible=True)
                    inputs=menus.locator('input:visible')
                    if inputs.count()!=1:raise Blocked('unsupported_widget',choice['field']['label']+' — no unique search input')
                    control=inputs
                query=choice.get('query_text',choice['answer']['value'])
                if choice['field'].get('workday_prompt'):
                    control.fill('')
                    control.press_sequentially(query,delay=20)
                else:control.fill(query)
                if choice['field'].get('search_on_enter'):control.press('Enter')
                self.searched.add(choice['field']['ui_id'])
                if not hasattr(self,'search_queries'):self.search_queries={}
                self.search_queries.setdefault(choice['field']['ui_id'],set()).add(query)
                search_started=time.monotonic()
            elif kind=='upload':
                document=choice['document'];data=self.document_bytes(document)
                if self.connection_id=='handshake' and len(data)>1024*1024:raise Blocked('document_too_large')
                self._control_ui(document['field']).set_input_files({'name':document['filename'],'mimeType':'application/pdf','buffer':data})
            else:self._click(choice['action'])
            deadline=time.monotonic()+getattr(self,'operation_timeout',20)
            changed=False
            while time.monotonic()<deadline:
                self._check()
                # A completed step can return to the hosted frame wrapper.
                if kind in {'entry','next'} and self._enter_frame():continue
                fresh=observe(self.page)
                changed=fresh['signature']!=view['signature']
                if operation.denied:
                    reason='remote_draft_permission_required' if not self.store.settings()['remote_drafts'] else 'browser_operation_blocked'
                    raise Blocked(reason,'The employer requested a write outside this approved UI action')
                if operation.failure:raise Blocked('browser_draft_uncertain','The employer rejected or failed a UI save'+
                    (': '+operation.failure_detail if getattr(operation,'failure_detail','') else ''))
                if kind=='fill':
                    visible=any(f['ui_id']==identity(choice['field']) for f in fresh['fields'])
                    condition=(not visible and changed) or (visible and self._matches(choice['answer']))
                elif kind=='upload':
                    condition=operation.upload_sent and bool(operation.requests)
                    if not operation.requests and not condition:
                        checksum=self._control_ui(choice['field']).evaluate('''async e=>{
                          if(e.files?.length!==1)return null;
                          const buffer=await e.files[0].arrayBuffer();
                          return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer))).map(n=>n.toString(16).padStart(2,'0')).join('');}''')
                        condition=checksum==choice['document']['hash']
                elif kind=='search':
                    condition=any(f['ui_id']==choice['field']['ui_id'] and f['options'] for f in fresh['fields'])
                    if choice['field'].get('workday_prompt'):
                        condition=(time.monotonic()-search_started>=2 and
                            self._search_input(choice['field']).input_value()==query)
                elif kind in {'entry','next'}:condition=changed and self._view_ready(fresh)
                else:condition=changed
                if condition and operation.complete and time.monotonic()-operation.last_activity>=.75:break
                self.page.wait_for_timeout(100)
            else:
                reason='browser_draft_uncertain' if operation.record else 'browser_no_progress'
                raise Blocked(reason,'The browser action did not reach a verified result: '+choice['label']+
                    f' (writes={len(operation.requests)}, acknowledgements={len(operation.responses)}, changed={changed})')
            if self._receipt(fresh['text'],view['text']):
                raise Blocked('browser_draft_uncertain','An unexpected completion appeared before final submission authorization')
            operation.acknowledge({'signature':fresh['signature'],'action':choice['id']})
            if kind=='upload':
                if operation.record:
                    self.uploads[choice['document']['hash']]={'hash':choice['document']['hash'],'kind':choice['document']['kind'],
                        'operation_id':operation.record,'profile_binding':self.binding}
                else:self.staged_documents[choice['document']['hash']]=choice['document']
            for checksum in operation.transmitted_documents:
                document=self.staged_documents[checksum]
                self.uploads[checksum]={'hash':checksum,'kind':document['kind'],
                    'operation_id':operation.record,'profile_binding':self.binding}
            if kind=='next':
                step={'fields':view['fields'],'answers':answers,'documents':documents,'url':_clean_url(view['url'])}
                from .workday_site import HOST
                if not HOST.fullmatch(self.job['host']) or _step_key(step) not in {_step_key(old) for old in self.completed_steps}:
                    self.completed_steps.append(step)
                self.opened.clear()
                self.history_bindings={}
                self._save_partial([],[])
            elif kind=='entry' and changed:
                self._save_partial([],[])
            elif kind=='upload' and operation.record:
                self._save_partial(answers,documents)
            self.memory.append({'action':choice['label'],'result':'changed' if changed else 'verified'})
            self.store.event('browser_agent_action',self.job['id'],{'kind':kind,'control':choice['id'],'result':'verified'})
        except BaseException:
            operation.uncertain()
            raise
        finally:self.operation=None

    def _save_partial(self,answers,documents):
        fields=[a['field'] for a in answers]+[d['field'] for d in documents]
        package=self._package(answers,documents,fields)
        self.aid=self.store.prepare(self.job,package)
        return package

    def _verify_current(self,view,answers):
        fresh=observe(self.page)
        if fresh['signature']!=view['signature']:raise Blocked('form_changed')
        for answer in answers:
            if not self._matches(answer):raise Blocked('field_verification_failed',answer['field']['label'])
        if fresh['errors']:raise Blocked('invalid_fields','; '.join(fresh['errors'])[:500])
        return fresh

    def _submit(self,choice,view,answers,documents):
        self._verify_current(view,answers)
        if not self.preparing and self.resume['hash'] not in self.uploads and self.resume['hash'] not in self.staged_documents:
            raise Blocked('resume_not_attached','The selected resume has not been transmitted and acknowledged in this employer application')
        package=self._package(answers,documents,view['fields'],complete=True)
        self.aid=self.store.prepare(self.job,package)
        before=self.page.locator('body').inner_text()
        self.receipt_baseline=before
        self.page.screenshot(path=str(private_dir(self.store.root/'screenshots')/(self.aid+'-before.jpg')),type='jpeg',full_page=True)
        if self.preparing:return 'prepared'
        operation=UIOperation(self,choice,package,final=True)
        self._check();operation.boundary.check()
        self.store.begin_submit(self.aid)
        self.attempted=True;self.operation=operation
        try:
            self._click(choice['action'])
            deadline=time.monotonic()+getattr(self,'receipt_timeout',45)
            while time.monotonic()<deadline:
                self._check();text=self._outcome_text()
                receipt=self._receipt(text,before)
                if operation.requests and operation.complete and receipt and not operation.accepted:
                    from .workday_browser_receipt import verify_submission
                    operation.accepted=verify_submission(self,operation)
                if operation.requests and operation.complete and operation.accepted and receipt:
                    operation.acknowledge({'receipt':digest(text),'signature':observe(self.page)['signature']})
                    screenshot=self._outcome_screenshot(self.store.root/'screenshots'/(self.aid+'-after.jpg'))
                    self.store.finish(self.aid,'confirmed','Correlated employer UI acknowledgement and fresh application receipt',screenshot)
                    return 'confirmed'
                if operation.denied or operation.failure:break
                self.page.wait_for_timeout(100)
            self.store.event('browser_submission_unverified',self.job['id'],{
                'requests':len(operation.requests),'acknowledgements':len(operation.responses),
                'complete':operation.complete,'accepted':operation.accepted,'denied':operation.denied,
                'failure':operation.failure,'visible_receipt':self._receipt(self.page.locator('body').inner_text(),before)})
            operation.uncertain()
            self.store.finish(self.aid,'unknown','The final browser action was recorded; employer acceptance could not be verified')
            return 'unknown'
        except BaseException:
            operation.uncertain()
            row=self.store.db.execute('SELECT state FROM applications WHERE id=?',(self.aid,)).fetchone()
            if row and row['state']=='submitting':self.store.finish(self.aid,'unknown','The final browser action was interrupted; verify its employer outcome')
            raise
        finally:self.operation=None

    def _restore_package(self):
        row=self.store.db.execute("SELECT package,hash FROM applications WHERE job_id=? AND state='prepared'",(self.job['id'],)).fetchone()
        package=json.loads(row['package']) if row else None
        checksum=row['hash'] if row else None
        if not package or not package.get('browser_agent'):
            checkpoint=self.store.db.execute("SELECT detail FROM events WHERE kind='browser_agent_checkpoint' AND subject=? ORDER BY seq DESC LIMIT 1",(self.job['id'],)).fetchone()
            if not checkpoint:return None
            checkpoint=json.loads(checkpoint['detail']);package=checkpoint.get('package');checksum=checkpoint.get('package_hash')
        if not isinstance(package,dict):raise Blocked('browser_package_invalid')
        metadata=package.get('browser_agent')
        if not isinstance(metadata,dict):raise Blocked('browser_package_invalid')
        from .answers import validate_package
        if digest(package)!=checksum:raise Blocked('package_tampered')
        if metadata.get('profile_binding')!=self.binding:raise Blocked('browser_profile_changed')
        target=metadata.get('last_url')
        if not isinstance(target,str) or origin(target)!=self.allowed_origin:raise Blocked('package_destination_mismatch')
        from .workday_site import HOST
        acknowledged=list(self.store.db.execute(
            "SELECT operation FROM draft_operations WHERE job_id=? AND operation LIKE 'ui:%' AND state='acknowledged'",(self.job['id'],)))
        transient=HOST.fullmatch(self.job['host']) and all(row['operation']=='ui:entry' for row in acknowledged)
        if metadata.get('completed_steps')==[] and metadata.get('uploaded_documents')==[] and (not acknowledged or transient):
            # No answer, upload or completed step will be reused. A newly
            # confirmed value can invalidate the old local preview; resolve a
            # fresh package instead of requiring its stale answers to validate.
            return None
        # Adding a newly confirmed fact must not strand otherwise unchanged
        # saved steps. Each recorded answer/document is revalidated against
        # current sources before adopting the new aggregate facts fingerprint.
        package['facts_hash']=digest(self.store.facts())
        validate_package(self.store,self.job,package)
        self.completed_steps=metadata.get('completed_steps',[])
        if HOST.fullmatch(self.job['host']):
            # Workday revisits populated pages when a saved application opens.
            # Keep one copy of each identical approved step; the separate save
            # journal still retains every acknowledgement and reconciliation.
            unique={}
            for step in self.completed_steps:unique.setdefault(_step_key(step),step)
            self.completed_steps=list(unique.values())
        for upload in metadata.get('uploaded_documents',[]):
            operation=self.store.db.execute("SELECT state,job_id,operation FROM draft_operations WHERE id=?",(upload.get('operation_id'),)).fetchone()
            if (not operation or operation['state']!='acknowledged' or operation['job_id']!=self.job['id']
                    or operation['operation'] not in {'ui:upload','ui:next'} or upload.get('profile_binding')!=self.binding):
                raise Blocked('upload_verification_failed')
            self.uploads[upload['hash']]=upload
        if HOST.fullmatch(self.job['host']):
            # Workday's apply router needs its own authenticated continuation;
            # a cold saved URL can initialize a new, empty client session.
            self.resume_workday=True
            return None
        return target

    def _resume_workday(self):
        """Continue the exact visible saved application in Candidate Home."""
        if getattr(self,'owner_verified',None) is False:
            raise Blocked('account_blocked','The employer session expired. Sign in using the app’s dedicated employer browser, then resume this saved application')
        def find(label):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                self._check();view=observe(self.page)
                matches=[a for a in view['actions'] if a['label']==label and
                    (not a['href'] or origin(a['href'])==self.allowed_origin)]
                if len(matches)==1:return view,matches[0]
                if len(matches)>1:break
                self.page.wait_for_timeout(100)
            raise Blocked('browser_draft_review','The saved Workday application has no unique '+label+' control')
        _,home=find('Candidate Home');self._click(home)
        _,related=find('Related Actions '+self.job['title']);self._click(related)
        view,continuation=find('Continue Application')
        # This control came from the menu for this exact posting, never a
        # similarly titled suggested role or another existing application.
        href=continuation.get('href')
        if href:
            from .workday_site import site_for_url
            site=site_for_url(self.job['url'])
            slug=site.path.rsplit('/',1)[-1]
            if not urlsplit(href).path.endswith('/'+slug+'/apply'):
                raise Blocked('browser_draft_review','The continuation belongs to a different requisition')
        choice={'id':'entry:'+continuation['ui_id'],'kind':'entry','label':continuation['label'],'action':continuation}
        self._perform(choice,view,[],[])

    def apply(self,job,live=True):
        self.job=dict(job);self.preparing=not live;self.attempted=False;self.aid=None
        if not self.test_url:
            from .discovery import ATS_HOSTS,PORTAL_HOSTS
            from .platform_connections import PLATFORMS
            from .workday_site import is_workday_url
            parsed=urlsplit(job['url'])
            platform_host=PLATFORMS.get(self.connection_id,{}).get('host')
            if (parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password
                    or parsed.hostname!=job['host']
                    or job['host'] not in ATS_HOSTS|PORTAL_HOSTS|({platform_host} if platform_host else set()) and not is_workday_url(job['url'])):
                raise Blocked('unapproved_destination')
        self.allowed_origin=origin(self.test_url or job['url']);self.current_host=urlsplit(self.allowed_origin).hostname
        self.stopped_reason=None;self.completed_steps=[];self.uploads={};self.staged_documents={};self.memory=[];self.opened=set();self.searched=set();self.search_queries={};self.option_cache={}
        self.operation=None;self.denied_write=False;self.resume_workday=False;self.history_bindings={};self.frame_entries=0
        self.job['answer_scope']=job.get('answer_scope') or job['host']+'|'+self.store.company(job['company'])
        self.store.check_job_decision(job['id']);guard_pending(self.store,job)
        if (self.store.db.execute("SELECT 1 FROM draft_operations WHERE job_id=? AND operation NOT LIKE 'ui:%'",(job['id'],)).fetchone()
                or self.store.db.execute('SELECT 1 FROM remote_draft_steps WHERE job_id=?',(job['id'],)).fetchone()):
            raise Blocked('browser_legacy_draft_review','Continue or reconcile the existing native employer draft before changing its executor')
        if job['host']=='jobs.ashbyhq.com' and not self.test_url:
            from .ashby_legacy_guard import protect_legacy_ashby
            from .ashby_guard import check
            protect_legacy_ashby(self.store);check(self.store,job['id'])
        if self.connection_id:
            from .platform_connections import configuration
            config=configuration(self.store,self.connection_id)
            if job.get('connection_id')!=self.connection_id or not config['enabled'] or not config['native_apply_enabled']:
                raise Blocked('connection_disabled')
        self.store._check_budget(job,self.store.settings())
        from .retry_authorization import safe_state
        if not safe_state(self.store,job['id']):raise Blocked('duplicate_or_uncertain')
        if live:
            from .prior_applications import preflight
            preflight(self.store,job)
        self.binding=self._binding();self.journal=DraftJournal(self.store,self.job)
        self.store.browser_agent_session=self.journal.session
        self.provider=LazyProvider(self.store.settings()['model_timeout_seconds'],store=self.store,
            checkpoint=self.store.checkpoint,observer=lambda stage,detail:self.store.event(stage,job['id'],detail))
        try:
            workday=job['host'].endswith('.myworkdayjobs.com')
            if self.test_url:
                navigation=job['url'] if origin(job['url'])==self.allowed_origin else self.test_url
                posting_text=None
            elif workday:
                # Workday can reject urllib's JSON requests while serving its
                # ordinary browser client. Verify that exact rendered posting;
                # the UI executor must not depend on a separate native API.
                from .workday_site import site_for_url
                site_for_url(job['url'])
                navigation=job['url'];posting_text=None
            else:
                from .ats_adapters import adapter_for
                navigation,posting_text=adapter_for(job).navigation(job,checkpoint=self.store.checkpoint,
                    deadline=getattr(self.store,'run_deadline',None))
            self.owner_verified=bool(self.test_url) or not (workday or self.connection_id)
            self.page.goto(navigation,wait_until='domcontentloaded',timeout=45000)
            self.page.wait_for_function('()=>document.body&&document.body.innerText.trim().length>0',timeout=20000)
            self._check()
            if self._enter_frame():self._check()
            initial=observe(self.page)
            if workday and not self.test_url:
                from .workday_browser_posting import inspect_posting
                posting_text=inspect_posting(self.page,self.job,self.store.checkpoint)
                from .browser_identity import verify_workday_account
                self.owner_verified=verify_workday_account(self)
                initial=observe(self.page)
            has_checkpoint=(self.store.db.execute('SELECT 1 FROM applications WHERE job_id=?',(job['id'],)).fetchone()
                or self.store.db.execute("SELECT 1 FROM events WHERE kind='browser_agent_checkpoint' AND subject=? LIMIT 1",(job['id'],)).fetchone())
            if self.connection_id and not has_checkpoint:
                from .platform_routing import LISTING,external_destination,save_route
                employer=external_destination(self.store,self.job,self.page.evaluate(LISTING))
                if employer:
                    save_route(self.store,self.job,employer)
                    return 'routed'
            from .policy import eligible
            from .answers import page_requires_manual_work
            if not posting_text:
                normalize=lambda text:' '.join(text.casefold().split())
                title=normalize(job['title'])
                if not any(title==normalize(heading) or normalize(heading).startswith(title+' at ') for heading in initial['headings']):
                    raise Blocked('posting_changed_review','The browser could not verify this saved role in the current employer page')
            if page_requires_manual_work(posting_text or initial['text']):raise Blocked('human_work_sample')
            current_posting=posting_text or initial['text']
            eligible({**self.job,'description':current_posting},self.store.settings(),self.store.facts())
            self.job['description']=current_posting
            self.store.upsert_job(self.job)
            # A tailored resume binds the verified posting. Generating before
            # this refresh made an otherwise valid resume stale before its
            # first browser action (API HTML vs rendered text also differs).
            self.resume=self._base_document()
            restored=self._restore_package()
            if restored and restored!=self.page.url:
                self.page.goto(restored,wait_until='domcontentloaded',timeout=45000)
                self._check()
            elif self.resume_workday:self._resume_workday()
            seen={}
            for _ in range(120):
                view=self._wait_ready()
                choices,answers,documents,pending=self._choices(view)
                # Optional blank controls need not become questions. Every
                # unknown required or prefilled value stays in the question queue.
                if not choices:
                    asked=set()
                    for field,reason in pending:
                        asked.add(self.store.ask(self.job['id'],self.job['answer_scope'],field['label'],field.get('options',[]),
                            reason,field=field,context=self.job))
                    if pending:
                        self.store.retire_questions(self.job['id'],asked)
                        self._save_partial(answers,documents)
                        raise Blocked('missing_answers','; '.join(f['label'] for f,_ in pending))
                    raise Blocked('browser_no_progress','No application action is available in the current page')
                choice=self._choose(view,choices)
                if choice is None:raise Blocked('browser_no_progress','The browser planner needs a clearer application action')
                fingerprint=digest([view['signature'],choice['id'],[a['value'] for a in answers],list(self.uploads)])
                seen[fingerprint]=seen.get(fingerprint,0)+1
                if seen[fingerprint]>2:raise Blocked('browser_no_progress','The same browser action made no durable progress')
                # Reinspect immediately before dispatch. IDs cannot survive a
                # changed question/control just because a CSS index still exists.
                if observe(self.page)['signature']!=view['signature']:continue
                outcome=self._perform(choice,view,answers,documents)
                if outcome:return outcome
            raise Blocked('browser_step_limit','The application exceeded its bounded action allowance')
        finally:
            if getattr(self.store,'browser_agent_session',None)==self.journal.session:
                del self.store.browser_agent_session
