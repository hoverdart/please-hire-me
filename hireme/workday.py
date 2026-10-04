"""Workday write protocol. Unknown operations cannot inherit submit authority."""
from dataclasses import dataclass
import re
import json
import hashlib
import uuid
from urllib.parse import urlsplit
from .util import Blocked,digest,now
from .portals import WORKDAY

VERSION=1
TENANTS={host:tenant for _,tenant,_,_,host in WORKDAY}
SECTIONS={'My Information','My Experience','Application Questions','Voluntary Disclosures','Review'}


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
    return digest({'facts':{k:v['revision'] for k,v in store.facts().items()},
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

    def __post_init__(self):
        self.store.checkpoint()
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
        previous=self.store.db.execute("SELECT state FROM remote_draft_steps WHERE job_id=? AND step=? AND state IN ('intent','unknown')",(self.job['id'],self.step)).fetchone()
        if previous:raise Blocked('remote_draft_uncertain','Inspect the employer draft before retrying this step')

    def consume(self,url,method,payload):
        self.store.checkpoint()
        if self.used or (url,method,payload)!=(self.url,self.method,self.payload):return False
        if self.sources!=source_hash(self.store):raise Blocked('stale_answer','Approved sources changed before remote save')
        if self.final:
            app=self.store.application_record(self.application_id)
            if not self.store.settings()['live_enabled'] or not app or app['state']!='submitting':raise Blocked('workday_submit_not_reserved')
        elif not self.store.settings()['remote_drafts']:raise Blocked('remote_draft_permission_required')
        # Persist intent only when the exact write is about to leave the browser.
        # Payload bytes remain in memory; private ledger records their hash only.
        self.record_id=digest(self.job['id']+self.step+uuid.uuid4().hex+self.url+hashlib.sha256(self.payload).hexdigest())
        with self.store.transaction():
            if self.store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step=? AND state IN ('intent','unknown')",(self.job['id'],self.step)).fetchone():raise Blocked('remote_draft_uncertain')
            self.store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(self.record_id,self.job['id'],self.job['host'],self.step,hashlib.sha256(self.payload).hexdigest(),self.sources,'intent',now(),now(),''))
            self.store.event('remote_step_intent',self.job['id'],{'step':self.step,'tenant':self.job['host'],'final':self.final,'record_id':self.record_id})
        self.used=True
        return True

    def acknowledge(self,receipt):
        if not self.used or not isinstance(receipt,str) or not receipt.strip():raise Blocked('remote_draft_uncertain')
        self.store.db.execute("UPDATE remote_draft_steps SET state='acknowledged',acknowledgement=?,updated=? WHERE id=? AND state='intent'",(receipt[:300],now(),self.record_id))

    def uncertain(self):
        if self.record_id:self.store.db.execute("UPDATE remote_draft_steps SET state='unknown',updated=? WHERE id=? AND state='intent'",(now(),self.record_id))

def verify_capability(store,tenant,signature,application_id):
    app=store.application_record(application_id)
    if tenant not in TENANTS or not app or app['state']!='confirmed':raise ValueError('A confirmed Workday application is required')
    job=store.db.execute('SELECT host FROM jobs WHERE id=?',(app['job_id'],)).fetchone()
    if not job or job['host']!=tenant or not isinstance(signature,str) or not re.fullmatch('[0-9a-f]{64}',signature):raise ValueError('Verification must match its tenant and flow')
    if not store.db.execute("SELECT 1 FROM events WHERE kind='confirmed' AND subject=?",(application_id,)).fetchone():raise ValueError('Automatic receipt evidence is required for adapter verification')
    store.db.execute('INSERT OR REPLACE INTO adapter_verifications VALUES(?,?,?,?,?)',(tenant,signature,VERSION,application_id,now()))


class WorkdayAdapter:
    """Read/validate Workday steps before granting any remote operation.

    Authenticated transport profiles must be observed and verified per tenant;
    this controller never infers a write URL or copies an unknown operation.
    """
    version=VERSION

    def __init__(self,browser,job):
        self.browser=browser;self.store=browser.store;self.job=job
        if job['host'] not in TENANTS:raise Blocked('multi_step_requires_adapter','Workday tenant is not configured')

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
                answer=resolve(self.store,self.job['host']+'|'+self.store.company(self.job['company']),field,provider,context=self.job)
                if answer:answers.append(answer)
            except Blocked as error:
                if error.reason in {'paused','cycle_timeout','provider_rate_limited','model_budget_exhausted'}:raise
                if field.get('required') or field.get('value'):
                    self.store.ask(self.job['id'],self.job['host']+'|'+self.store.company(self.job['company']),field['label'],field.get('options',[]),error.reason,field=field,context=self.job)
                    missing.append(field['label'])
        if missing:raise Blocked('missing_answers','; '.join(missing))
        return answers

    def authorize_step(self,snapshot,url,method,payload,answers,documents,*,final=False,application_id=None):
        # Available only to a recognized transport profile; DOM action URLs and
        # arbitrary captured traffic are never transport profiles.
        if not getattr(self,'transport_verified',False):
            raise Blocked('multi_step_requires_adapter','Authenticated Workday transport profile requires verification')
        from .answers import validate_package
        validate_package(self.store,self.job,{'job_id':self.job['id'],'url':self.job['url'],'answers':answers,'documents':documents,'steps':[{'fields':snapshot['fields']} ]})
        self.browser._verify(answers,documents,snapshot['fields'])
        grant=WriteGrant(self.store,self.job,snapshot['section'],url,method,payload,final,application_id)
        self.browser.workday_grant=grant
        return grant
