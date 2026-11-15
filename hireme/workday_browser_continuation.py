"""Readback authority for continuing one exact, acknowledged Workday draft."""
from __future__ import annotations

import json
import re

from .util import Blocked,digest
from .workday_site import site_for_url


def checkpoint(agent,job):
    from .browser_agent_protocol import guard_pending
    from .answers import validate_package
    guard_pending(agent.store,job)
    row=agent.store.db.execute("SELECT package,hash FROM applications WHERE job_id=? AND state='prepared'",(job['id'],)).fetchone()
    if row:package=json.loads(row['package']);checksum=row['hash']
    else:
        event=agent.store.db.execute("SELECT detail FROM events WHERE kind='browser_agent_checkpoint' AND subject=? ORDER BY seq DESC LIMIT 1",(job['id'],)).fetchone()
        if not event:return None
        event=json.loads(event['detail']);package=event.get('package');checksum=event.get('package_hash')
    if not isinstance(package,dict) or digest(package)!=checksum:raise Blocked('package_tampered')
    metadata=package.get('browser_agent',{})
    remote=metadata.get('remote_application_hash')
    if (metadata.get('profile_binding')!=agent.binding or not metadata.get('completed_steps')
            or not isinstance(remote,str) or not re.fullmatch('[a-f0-9]{64}',remote)):return None
    package['facts_hash']=digest(agent.store.facts())
    validate_package(agent.store,job,package)
    def bound(operation_id,kinds):
        operation=agent.store.db.execute("SELECT * FROM draft_operations WHERE id=? AND job_id=? AND state='acknowledged'",(operation_id,job['id'])).fetchone()
        if not operation or operation['operation'] not in kinds:return False
        target=json.loads(operation['target'])
        return target.get('profile_binding')==agent.binding and target.get('remote_application_hash')==remote
    for step in metadata['completed_steps']:
        operation_id=step.get('operation_id')
        if not operation_id or not bound(operation_id,{'ui:next'}):return None
    if any(not bound(upload.get('operation_id'),{'ui:upload','ui:next'}) for upload in metadata.get('uploaded_documents',[])):return None
    return remote


def verify(agent,job,info):
    """A posting flag alone never authorizes adopting another employer record."""
    saved=info.get('jobApplication')
    if (not isinstance(saved,dict) or saved.get('draft') is not True
            or saved.get('dateCompleted') is not None):return None
    remote=saved.get('id')
    if not isinstance(remote,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._~-]{0,299}',remote):return None
    expected=checkpoint(agent,job)
    if not expected or expected!=digest(remote):return None
    from .browser_identity import verify_workday_account
    if not verify_workday_account(agent):return None
    site=site_for_url(job['url'])
    url=f'https://{site.host}/wday/calypso/cxs/candidatehome/{site.tenant}/{site.site}/application/{remote}/config'
    try:result=agent.page.evaluate('''async url=>{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      try{const response=await fetch(url,{method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',
        headers:{accept:'application/json','X-CALYPSO-CSRF-TOKEN':window.workday.token},signal:controller.signal});
        const text=await response.text();if(text.length>2000000)throw new Error('Oversized saved application');
        return {status:response.status,body:JSON.parse(text)};
      }finally{clearTimeout(timer);}
    }''',url)
    except Blocked:raise
    except Exception:return None
    if not isinstance(result,dict) or result.get('status')!=200:return None
    data=result.get('body')
    if not isinstance(data,dict) or data.get('error') or data.get('errors'):return None
    record=data.get('application');identity=job.get('_workday_public_identity',{})
    clean=lambda value:' '.join(value.casefold().split()) if isinstance(value,str) else None
    if (not isinstance(record,dict) or record.get('applicationId')!=remote
            or record.get('jobPostingAnchorId')!=site.path.rsplit('/',1)[-1]
            or record.get('jobRequisitionId')!=identity.get('requisition_id')
            or record.get('draft') is not True or record.get('active') is not True
            or record.get('applicationType')!='EXTERNAL' or record.get('status')=='Application Received'
            or clean(record.get('postingTitle'))!=clean(job.get('title'))):return None
    agent.workday_application_hash=expected;agent.owner_verified=True
    proof={'remote_application_hash':expected,'owner_verified':True,'evidence_hash':digest(result)}
    agent.store.event('workday_saved_application_verified',job['id'],proof)
    return proof
