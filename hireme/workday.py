"""Workday write protocol. Unknown operations cannot inherit submit authority."""
from dataclasses import dataclass
import re
import json
import hashlib
import uuid
from urllib.parse import urlsplit
from .util import Blocked,digest,now
from .portals import WORKDAY

VERSION=2
TENANTS={host:tenant for _,tenant,_,_,host in WORKDAY}
SECTIONS={'My Information','My Experience','Application Questions','Voluntary Disclosures','Review','Availability','Self Identification'}


def search_read(host,url,method,payload):
    """Recognize the configured public jobs search, not account/draft traffic.

    The same empty-facet schema is used by discovery. Unknown search extensions
    stay blocked until inspected; no generic Workday POST permission is added.
    """
    configured=next((row for row in WORKDAY if row[4]==host),None)
    if not configured or method!='POST' or not isinstance(payload,bytes) or len(payload)>4096:return False
    try:
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname!=host or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path!=f'/wday/cxs/{configured[1]}/{configured[3]}/jobs'):return False
        data=json.loads(payload)
    except (ValueError,UnicodeError):return False
    return (isinstance(data,dict) and set(data)=={'appliedFacets','limit','offset','searchText'}
            and data['appliedFacets']=={} and type(data['limit']) is int and 1<=data['limit']<=100
            and type(data['offset']) is int and 0<=data['offset']<=10000
            and isinstance(data['searchText'],str) and len(data['searchText'])<=500)

def source_hash(store):
    from .workday_history import fingerprint
    return digest({'facts':{k:v['revision'] for k,v in store.facts().items()},
        'history':fingerprint(store),
        'answers':[tuple(r) for r in store.db.execute('SELECT id,revision FROM answers ORDER BY id')],
        'documents':[tuple(r) for r in store.db.execute('SELECT kind,hash FROM documents ORDER BY kind')],
        'materials':[tuple(r) for r in store.db.execute('SELECT id,revision,confirmed FROM materials ORDER BY id')],
        'templates':[tuple(r) for r in store.db.execute('SELECT id,revision FROM templates ORDER BY id')]})

@dataclass
class WriteGrant:
    store: object
    job: dict
    step: str
    url: str
    method: str
    payload: bytes
    final: bool=False
    application_id: str|None=None
    used: bool=False
    record_id: str|None=None
    operation_key: str|None=None

    def __post_init__(self):
        self.store.checkpoint()
        from .approval_boundary import ApprovalBoundary
        self.boundary=ApprovalBoundary(self.store,self.job,require_live=self.final,
            application_id=self.application_id if self.final else None)
        parsed=urlsplit(self.url)
        if (self.job['host'] not in TENANTS or parsed.scheme!='https' or parsed.hostname!=self.job['host']
            or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment
            or self.method not in {'POST','PUT'} or not isinstance(self.payload,bytes) or len(self.payload)>32*1024*1024):
            raise Blocked('workday_unapproved_operation')
        if self.step not in SECTIONS:raise Blocked('multi_step_requires_adapter','Unrecognized Workday section')
        self.store.check_job_decision(self.job['id'])
        if not self.final:self.store._check_budget(self.job,self.store.settings())
        if self.final:
            if self.step!='Review' or not self.application_id:raise Blocked('workday_submit_not_reserved')
            app=self.store.application_record(self.application_id)
            if not app or app['job_id']!=self.job['id'] or app['state']!='submitting' or not self.store.settings()['live_enabled']:
                raise Blocked('workday_submit_not_reserved')
        elif not self.store.settings()['remote_drafts']:
            raise Blocked('remote_draft_permission_required','Enable remote draft saves before advancing Workday steps')
        self.sources=source_hash(self.store)
        self.generation=self.store.control_generation()
        if self.operation_key is not None and not re.fullmatch('[a-f0-9]{64}',self.operation_key):raise Blocked('workday_unapproved_operation')
        self.ledger_step=self.step+(':'+self.operation_key if self.operation_key else '')
        previous=self.store.db.execute("SELECT state FROM remote_draft_steps WHERE job_id=? AND step=? AND state IN ('intent','unknown')",(self.job['id'],self.ledger_step)).fetchone()
        if previous:raise Blocked('remote_draft_uncertain','Inspect the employer draft before retrying this step')

    def consume(self,url,method,payload):
        self.store.checkpoint()
        if self.used or (url,method,payload)!=(self.url,self.method,self.payload):return False
        if self.sources!=source_hash(self.store):raise Blocked('stale_answer','Approved sources changed before remote save')
        if self.final:
            app=self.store.application_record(self.application_id)
            if not self.store.settings()['live_enabled'] or not app or app['state']!='submitting':raise Blocked('workday_submit_not_reserved')
        elif not self.store.settings()['remote_drafts']:raise Blocked('remote_draft_permission_required')
        # A pause/resume cycle must not revive a previously approved step.
        if self.store.control_generation()!=self.generation:raise Blocked('paused')
        # Persist intent only when the exact write is about to leave the browser.
        # Payload bytes remain in memory; private ledger records their hash only.
        self.record_id=digest(self.job['id']+self.step+uuid.uuid4().hex+self.url+hashlib.sha256(self.payload).hexdigest())
        with self.store.transaction():
            self.boundary.check()
            if self.store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step=? AND state IN ('intent','unknown')",(self.job['id'],self.ledger_step)).fetchone():raise Blocked('remote_draft_uncertain')
            self.store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(self.record_id,self.job['id'],self.job['host'],self.ledger_step,hashlib.sha256(self.payload).hexdigest(),self.sources,'intent',now(),now(),''))
            self.store.event('remote_step_intent',self.job['id'],{'step':self.step,'tenant':self.job['host'],'final':self.final,'record_id':self.record_id})
        self.used=True
        return True

    def consume_request(self,request):
        expected=getattr(self,'request_headers',None)
        if expected is not None:
            actual={key.lower():value for key,value in request.headers.items()}
            if (any(actual.get(key.lower())!=value for key,value in expected.items())
                    or ('form-definition' not in {key.lower() for key in expected} and 'form-definition' in actual)):
                return False
        return self.consume(request.url,request.method,request.post_data_buffer or b'')

    def acknowledge(self,receipt):
        if not self.used or not isinstance(receipt,str) or not receipt.strip():raise Blocked('remote_draft_uncertain')
        self.store.db.execute("UPDATE remote_draft_steps SET state='acknowledged',acknowledgement=?,updated=? WHERE id=? AND state='intent'",(receipt[:300],now(),self.record_id))

    def uncertain(self):
        if self.record_id:self.store.db.execute("UPDATE remote_draft_steps SET state='unknown',updated=? WHERE id=? AND state='intent'",(now(),self.record_id))

def verify_capability(store,tenant,signature,application_id):
    from .workday_profiles import owner_hash,profile_binding,transport
    profile=transport(tenant)
    binding=profile_binding(store,tenant)
    if not profile or signature!=profile.signature or not binding:
        raise ValueError('A reviewed native flow and this machine browser binding are required')
    app=store.application_record(application_id)
    if tenant not in TENANTS or not app or app['state']!='confirmed':raise ValueError('A confirmed Workday application is required')
    job=store.db.execute('SELECT host FROM jobs WHERE id=?',(app['job_id'],)).fetchone()
    if not job or job['host']!=tenant or not isinstance(signature,str) or not re.fullmatch('[0-9a-f]{64}',signature):raise ValueError('Verification must match its tenant and flow')
    if not store.db.execute("SELECT 1 FROM events WHERE kind='confirmed' AND subject=?",(application_id,)).fetchone():raise ValueError('Automatic receipt evidence is required for adapter verification')
    receipt=store.db.execute("SELECT detail FROM events WHERE kind='workday_receipt' AND subject=? ORDER BY seq DESC LIMIT 1",(application_id,)).fetchone()
    try:evidence=json.loads(receipt['detail']) if receipt else {}
    except (ValueError,TypeError):evidence={}
    if (not isinstance(evidence,dict) or evidence.get('flow_signature')!=signature or evidence.get('owner_hash')!=owner_hash(store)
            or evidence.get('profile_binding')!=binding or evidence.get('proof_scope')!='live'
            or not re.fullmatch('[a-f0-9]{64}',str(evidence.get('receipt_hash','')))):
        raise ValueError('A correlated native receipt bound to this owner and browser is required')
    from .workday_state import check_receipt_chain
    with store.transaction():
        check_receipt_chain(store,application_id,evidence)
        store.db.execute('INSERT OR REPLACE INTO adapter_verifications VALUES(?,?,?,?,?)',(tenant,signature,VERSION,application_id,now()))
        store.event('workday_operational_verified',application_id,{**evidence,'version':VERSION})


class WorkdayAdapter:
    """Read/validate Workday steps before granting any remote operation.

    Authenticated transport profiles must be observed and verified per tenant;
    this controller never infers a write URL or copies an unknown operation.
    """
    version=VERSION

    def __init__(self,browser,job):
        self.browser=browser;self.store=browser.store;self.job=job
        if job['host'] not in TENANTS:raise Blocked('multi_step_requires_adapter','Workday tenant is not configured')
        from .workday_profiles import transport
        self.profile=transport(job['host'])

    def snapshot(self,section):
        canonical='Application Questions' if section.startswith('Application Questions') else section
        if canonical not in SECTIONS:raise Blocked('multi_step_requires_adapter','Unrecognized Workday section: '+section)
        fields=self.browser._snapshot()
        signature=digest({'tenant':self.job['host'],'section':canonical,'fields':[{'label':f['label'],'type':f['type'],'section':f.get('section',''),'options':sorted(f.get('options',[]))} for f in fields]})
        return {'section':canonical,'fields':fields,'signature':signature,'version':VERSION}

    def resolve_step(self,snapshot,provider=None):
        from .answers import resolve
        answers=[];missing=[]
        for field in snapshot['fields']:
            self.store.checkpoint()
            if field['type']=='file':continue # Hash-verified upload path remains separate.
            try:
                if field.get('section') in {'employment','education'}:
                    from .workday_history import resolve_field
                    answer=resolve_field(self.store,field)
                else:
                    answer=resolve(self.store,self.job['host']+'|'+self.store.company(self.job['company']),field,provider,context=self.job)
                if answer:answers.append(answer)
                elif field.get('required') or field.get('value'):
                    raise Blocked('required_answer_missing' if field.get('required') else 'unknown_prefilled_value',field['label'])
            except Blocked as error:
                if error.reason in {'paused','cycle_timeout','provider_rate_limited','model_budget_exhausted'}:raise
                if field.get('required') or field.get('value'):
                    self.store.ask(self.job['id'],self.job['host']+'|'+self.store.company(self.job['company']),field['label'],field.get('options',[]),error.reason,field=field,context=self.job)
                    missing.append(field['label'])
        if missing:raise Blocked('missing_answers','; '.join(missing))
        return answers

    def authorize_step(self,snapshot,url,method,payload,answers,documents,*,package=None,final=False,application_id=None):
        # Available only to a recognized transport profile; DOM action URLs and
        # arbitrary captured traffic are never transport profiles.
        from .workday_profiles import transport
        if not self.profile or self.profile is not transport(self.job['host']):
            raise Blocked('multi_step_requires_adapter','Authenticated Workday transport profile requires verification')
        from .answers import validate_package
        if package is None:
            raise Blocked('workday_package_incomplete','The accumulated approved application is required')
        from .approval_boundary import ApprovalBoundary
        from .workday_profiles import profile_binding
        session=getattr(self,'session',None)
        if not session or not session.current(self.store) or session.host!=self.job['host']:
            raise Blocked('workday_session_unverified')
        boundary=ApprovalBoundary(self.store,self.job,package,require_live=final,
            application_id=application_id if final else None,
            profile_binding=lambda:profile_binding(self.store,self.job['host']))
        validate_package(self.store,self.job,package)
        # Earlier saved steps are part of approval, but their controls no longer
        # exist on this page. Verify only this step against the current DOM.
        fields=snapshot['fields']
        if (any(answer not in package['answers'] for answer in answers)
                or any(document not in package['documents'] for document in documents)
                or any(answer['field'] not in fields for answer in answers)
                or any(document['field'] not in fields for document in documents)):
            raise Blocked('workday_package_incomplete')
        self.browser._verify(answers,documents,fields)
        validator=getattr(self.profile,'validate_operation',None)
        if not callable(validator) or validator(snapshot,url,method,payload,answers,documents,final=final) is not True:
            raise Blocked('workday_unapproved_operation')
        boundary.check()
        grant=WriteGrant(self.store,self.job,snapshot['section'],url,method,payload,final,application_id)
        grant.boundary=boundary
        self.browser.workday_grant=grant
        return grant



def _persisted_drafts(store,host):
    """Read aggregate evidence on old/new ledgers without repairing either."""
    tables={row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    drafts=({row[0]:row[1] for row in store.db.execute(
        'SELECT state,COUNT(*) FROM workday_drafts WHERE tenant=? GROUP BY state',(host,))}
        if 'workday_drafts' in tables else {})
    steps=({row[0]:row[1] for row in store.db.execute(
        'SELECT state,COUNT(*) FROM remote_draft_steps WHERE tenant=? GROUP BY state',(host,))}
        if 'remote_draft_steps' in tables else {})
    orphaned=0
    if 'remote_draft_steps' in tables:
        projected=(' AND NOT EXISTS(SELECT 1 FROM workday_drafts d WHERE d.job_id=r.job_id)'
                   if 'workday_drafts' in tables else '')
        orphaned=store.db.execute("SELECT COUNT(DISTINCT job_id) FROM remote_draft_steps r WHERE tenant=? "
            "AND state='acknowledged' AND (step='My Information' OR step LIKE 'My Information:%')"+projected,(host,)).fetchone()[0]
    return {'draft_states':drafts,'step_states':steps,
        'pending_draft_count':sum(count for state,count in drafts.items() if state!='submitted'),
        'uncertain_step_count':steps.get('intent',0)+steps.get('unknown',0),
        'unprojected_creation_count':orphaned}


def status(store):
    """Read-only release/status surface; never establishes session or authority."""
    from .workday_profiles import NVIDIA,operational_evidence,profile_binding,transport
    from .workday_sessions import cached_evidence
    tenants=[]
    for label,_,_,site,host in WORKDAY:
        reviewed=transport(host)
        bound=bool(profile_binding(store,host))
        verified=bool(operational_evidence(store,host,VERSION)) if reviewed and bound else False
        persisted=_persisted_drafts(store,host)
        uncertain=bool(persisted['uncertain_step_count'] or persisted['draft_states'].get('unknown',0))
        adoption=bool(persisted['unprojected_creation_count'])
        enabled=bool(reviewed and bound and verified and store.settings()['remote_drafts'] and not uncertain and not adoption)
        reason=('authenticated_transport_unverified' if host==NVIDIA and not reviewed else
                'tenant_not_commissioned' if not reviewed else
                'sign_in_required' if not bound else
                'live_acceptance_unverified' if not verified else
                'remote_drafts_disabled' if not store.settings()['remote_drafts'] else '')
        if uncertain or adoption:reason='remote_draft_uncertain'
        commissioning=('reconciliation_required' if uncertain else 'draft_adoption_required' if adoption else
            'transport_required' if not reviewed else 'sign_in_required' if not bound else
            'receipt_required' if not verified else 'draft_permission_required' if not store.settings()['remote_drafts'] else 'ready')
        action=('Read and reconcile the existing employer draft. Do not create another draft.' if uncertain else
            'Read and adopt the recorded employer draft. Do not create another draft.' if adoption else
            'Complete native flow verification before commissioning one eligible job.' if not reviewed else
            'Sign in as the confirmed applicant in the dedicated browser.' if not bound else
            'Commission one eligible job and verify its fresh employer receipt.' if not verified else
            'Enable remote draft saves before continuing.' if not store.settings()['remote_drafts'] else '')
        cached_owner=cached_evidence(store) if host==NVIDIA else None
        tenants.append({'name':label,'host':host,'site':site,'configured':True,'protocol_reviewed':bool(reviewed),
            'implementation_evidence':'reviewed_client_and_owned_read_schemas' if reviewed else None,
            'live_write_acceptance_verified':verified,
            'session_bound':bound,'operationally_verified':verified,'enabled':enabled,'reason':reason,
            'cached_owner_evidence':cached_owner,'commissioning_state':commissioning,**persisted,
            'hold':{'reason':reason,'action':action} if reason else None})
    return {'version':VERSION,'tenants':tenants,
        'note':'Source-reviewed implementation and owned read schemas do not establish live save, upload or submission acceptance. Commissioning requires a genuine owner/profile-bound receipt and acknowledged operation chain.'}


class WorkdayController(WorkdayAdapter):
    """Native controller entry point; absent reviewed transport stops before writes.

    NVIDIA has a bounded source-reviewed US native implementation. Scheduled
    writes remain unavailable until this owner's browser has a genuine native
    receipt bound to its approved package and acknowledged operation chain.
    """
    def __init__(self,browser,job,profile=None):
        super().__init__(browser,job)
        if profile is not None and profile is not self.profile:
            raise Blocked('workday_transport_unverified','A caller cannot install an unreviewed transport profile')

    def status(self):
        return next(item for item in status(self.store)['tenants'] if item['host']==self.job['host'])

    def _ready(self):
        from .workday_profiles import transport
        if not self.profile or self.profile is not transport(self.job['host']):
            raise Blocked('workday_transport_unverified',
                'NVIDIA authenticated save, upload and final receipt transport needs review; no application fields were written')
        self.store.checkpoint()
        read_session=getattr(self.profile,'read_session',None)
        if not callable(read_session):raise Blocked('workday_session_unverified')
        from .workday_profiles import NVIDIA,bind_session
        response=read_session(self)
        if self.job['host']==NVIDIA:
            from .workday_sessions import capture
            self.session=capture(self.store,self.browser.context,response)
        else:self.session=bind_session(self.store,response)

    def apply(self,live=True):
        self._ready()
        from .workday_profiles import operational_evidence
        if live and not operational_evidence(self.store,self.job['host'],VERSION):
            raise Blocked('workday_acceptance_unverified','Commission one reviewed application before enabling scheduled Workday submissions')
        self.store._check_budget(self.job,self.store.settings())
        return self.profile.apply(self,live=live)

    def commission(self):
        """Explicit one-job validation, unavailable without a reviewed native profile."""
        self._ready()
        if not self.store.settings()['live_enabled'] or not self.store.settings()['remote_drafts']:
            raise Blocked('remote_draft_permission_required')
        self.store._check_budget(self.job,self.store.settings())
        return self.profile.apply(self,live=True)

    def reconcile(self):
        """A reviewed profile may read the same draft; never retry a final intent."""
        if not self.profile:
            raise Blocked('workday_transport_unverified','Authenticated draft readback has not been reviewed')
        if (not self.store.db.execute('SELECT 1 FROM workday_drafts WHERE job_id=?',(self.job['id'],)).fetchone()
                and not self.store.db.execute('SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step LIKE ?',
                    (self.job['id'],'My Information:%')).fetchone()):
            raise Blocked('remote_draft_uncertain','There is no saved draft to reconcile')
        app=self.store.db.execute('SELECT state FROM applications WHERE job_id=?',(self.job['id'],)).fetchone()
        if app and app['state']!='prepared':
            raise Blocked('duplicate_or_uncertain','Verify the existing final submission outcome')
        self._ready()
        return self.profile.reconcile(self)


def _selected_job(store,job_id):
    from .workday_profiles import transport
    row=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise ValueError('Opportunity not found')
    job=json.loads(row['payload'])
    if job['host'] not in TENANTS or not transport(job['host']):
        raise Blocked('workday_transport_unverified','This tenant has no reviewed authenticated transport profile')
    store.check_job_decision(job_id)
    previous=store.db.execute('SELECT state FROM applications WHERE job_id=?',(job_id,)).fetchone()
    if previous and previous['state']!='prepared':
        raise Blocked('duplicate_or_uncertain','Verify the existing submission outcome; another attempt is not authorized')
    return job


def commission(store,repo,job_id):
    """One explicit selected job through normal cycle, lock, limits and reporting."""
    job=_selected_job(store,job_id)
    settings=store.settings()
    if not settings['live_enabled'] or not settings['remote_drafts']:
        raise Blocked('remote_draft_permission_required','Enable approved draft writes and submissions before commissioning')
    from .browser import Browser
    from .store import worker_lock
    from .worker import cycle
    from .workday_commission_state import OWNER,begin,finish
    class CommissionBrowser(Browser):
        def apply(self,selected,live=True):
            if not live or selected['id']!=job_id:raise Blocked('workday_unapproved_operation')
            self.workday_commission_job=job_id
            return super().apply(selected,live=True)
    # No active ordinary worker may overlap creation of the durable fence. An
    # intervening process after this lock is released will recover and pause it;
    # only this Store's ephemeral owner token can bind the selected cycle.
    started=False
    state='failed'
    try:
        with worker_lock(store.root):
            store.recover()
            _selected_job(store,job_id)
            begin(store,job_id,browser_factory=CommissionBrowser)
            started=True
        result=cycle(store,repo,discover=False,live=True,limit=1,max_attempts=1,job_ids={job_id},browser_factory=CommissionBrowser)
        state='finished'
        return result
    finally:
        owner=getattr(store,OWNER,None)
        if started or isinstance(owner,dict) and owner.get('browser_factory') is CommissionBrowser:
            finish(store,state=state)


def reconcile(store,repo,job_id):
    """Only read/acknowledge an existing remote draft, with no final attempt."""
    from .store import worker_lock
    from .browser import Browser
    with worker_lock(store.root),worker_lock(store.root,'browser'):
        job=_selected_job(store,job_id)
        with Browser(store) as browser:
            return WorkdayController(browser,job).reconcile()
