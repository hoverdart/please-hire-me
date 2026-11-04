"""Fresh, owner-bound readback when a Workday final acknowledgement is opaque."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .util import Blocked,digest
from .workday_site import site_for_url


def received_record(job,remote,data):
    try:site=site_for_url(job['url'])
    except (ValueError,TypeError):return False
    if not isinstance(data,dict) or data.get('error') or data.get('errors'):return False
    record=data.get('application')
    if not isinstance(record,dict):return False
    slug=site.path.rsplit('/',1)[-1]
    clean=lambda value:' '.join(value.casefold().split()) if isinstance(value,str) else None
    return (record.get('applicationId')==remote and record.get('jobPostingAnchorId')==slug
        and record.get('draft') is False and record.get('active') is True
        and record.get('applicationType')=='EXTERNAL' and record.get('status')=='Application Received'
        and bool(clean(job.get('title'))) and clean(record.get('postingTitle'))==clean(job.get('title'))
        and isinstance(record.get('dateApplied'),str) and bool(record['dateApplied'].strip())
        and ('_' not in slug or record.get('jobRequisitionId')==slug.rsplit('_',1)[-1]))


def verify_submission(agent,operation):
    """Read only after an acknowledged finalizer and a fresh visible receipt.

    The application identity comes from the client's actual final request, not
    a model-authored destination or a search of other candidate applications.
    """
    if not operation.final or operation.denied or not operation.complete:return False
    if not agent._receipt(agent.page.locator('body').inner_text(),agent.receipt_baseline):return False
    try:site=site_for_url(agent.job['url'])
    except (ValueError,TypeError):return False
    matches=[]
    for key,item in operation.requests.items():
        request=item['request'];parsed=urlsplit(request.url)
        match=re.fullmatch(r'/wday/(?:calypso/)?cxs/(?:jobapplication/)?'+re.escape(site.tenant)+
            r'/jobapplication/([A-Za-z0-9][A-Za-z0-9._~-]{0,299})/finalize',parsed.path)
        if (match and request.method=='POST' and not parsed.query and not parsed.fragment
                and parsed.scheme=='https' and parsed.netloc==site.host
                and operation.responses.get(key,{}).get('ok') is True):matches.append(match[1])
    if len(matches)!=1 or getattr(operation,'readback_attempted',False):return False
    operation.readback_attempted=True
    from .browser_identity import verify_workday_account
    if not verify_workday_account(agent):return False
    remote=matches[0]
    url=f'https://{site.host}/wday/calypso/cxs/candidatehome/{site.tenant}/{site.site}/application/{remote}/config'
    try:
        result=agent.page.evaluate('''async url=>{
          const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
          try{const response=await fetch(url,{method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',
            headers:{accept:'application/json','X-CALYPSO-CSRF-TOKEN':window.workday.token},signal:controller.signal});
            const text=await response.text();if(text.length>2000000)throw new Error('Oversized receipt');
            return {status:response.status,body:JSON.parse(text)};
          }finally{clearTimeout(timer);}
        }''',url)
    except Blocked:raise
    except Exception:return False
    if not isinstance(result,dict) or result.get('status')!=200 or not received_record(agent.job,remote,result.get('body')):return False
    agent.store.event('browser_submission_readback_verified',agent.job['id'],{
        'owner_verified':True,'source':'exact_workday_application_config','evidence_hash':digest(result),
        'status':'Application Received','remote_application_hash':digest(remote)})
    return True
