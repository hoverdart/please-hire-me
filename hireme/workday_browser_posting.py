"""Verify the exact public requisition in Workday's rendered client."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit,urlunsplit

from .browser_observation import observe
from .util import Blocked,digest
from .workday_site import site_for_url

VERSION=2


def public_identity(job,data):
    """Join the official API identity to this exact supplied URL, including variants."""
    try:
        expected=site_for_url(job['url']);info=data['jobPostingInfo']
        current=site_for_url(info['externalUrl']);anchor=expected.path.rsplit('/',1)[-1]
        req=info['jobReqId'];title=info['title']
        if (current!=expected or info.get('jobPostingId')!=anchor or info.get('jobPostingSiteId')!=expected.site
                or info.get('posted') is not True or info.get('canApply') is not True
                or not isinstance(req,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}',req)
                or not re.fullmatch(re.escape(req)+r'(?:-[0-9]+)?',anchor.rsplit('_',1)[-1])
                or not isinstance(title,str) or not 1<=len(title.strip())<=500 or '<' in title or '>' in title):return None
        return {'host':expected.host,'site':expected.site,'posting_anchor':anchor,'requisition_id':req,
                'title':title.strip(),'evidence_hash':digest(info)}
    except (KeyError,ValueError,TypeError,AttributeError):return None


def verified_posting(page,job,checkpoint):
    site=site_for_url(job['url']);checkpoint()
    url='https://'+site.host+'/wday/cxs/'+site.tenant+'/'+site.site+site.path
    try:
        result=page.evaluate('''async url=>{
            const c=new AbortController(),timer=setTimeout(()=>c.abort(),10000);
            try{const r=await fetch(url,{method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',
                headers:{accept:'application/json'},signal:c.signal});
                const text=await r.text();if(text.length>2000000)throw new Error('Oversized posting');
                let data=null;try{data=JSON.parse(text)}catch{}
                return {status:r.status,data};
            }finally{clearTimeout(timer);}
        }''',url)
    except Exception:result=None
    identity=None
    if isinstance(result,dict):
        if result.get('status') in {404,410}:
            raise Blocked('expired_posting','Workday no longer publishes this requisition')
        data=result.get('data')
        if result.get('status')==200 and isinstance(data,dict) and 'jobPostingInfo' in data:
            info=data['jobPostingInfo']
            if isinstance(info,dict) and (info.get('posted') is False or info.get('canApply') is False):
                raise Blocked('expired_posting','Workday no longer accepts applications for this requisition')
            identity=public_identity(job,data)
            if not identity:raise Blocked('posting_changed_review','The official Workday record does not match the saved requisition')
    current={**job,'title':identity['title'],'_workday_public_identity':identity} if identity else dict(job)
    current['description']=inspect_posting(page,current,checkpoint,identity=identity)
    return current


def dismiss_cookie_notice(page):
    """Decline optional cookies through the vendor's exact public control."""
    notices=page.locator('[data-automation-id="legalNotice"]:visible')
    if notices.count()!=1:return
    notice=notices.first
    accept=notice.locator('[data-automation-id="legalNoticeAcceptButton"]:visible')
    decline=notice.locator('[data-automation-id="legalNoticeDeclineButton"]:visible')
    if (accept.count()==decline.count()==1 and re.fullmatch(r'accept cookies',accept.inner_text().strip(),re.I)
            and re.search(r'\bcookies\b',notice.inner_text(),re.I)):
        decline.click()


def inspect_posting(page,job,checkpoint,timeout=25,identity=None):
    expected=site_for_url(job['url'])
    normalize=lambda text:' '.join(text.casefold().split())
    requisition=identity['requisition_id'] if identity else expected.path.rsplit('_',1)[-1]
    if not re.fullmatch(r'[A-Za-z0-9-]+',requisition):raise Blocked('posting_inspection_review')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        checkpoint()
        parsed=urlsplit(page.url)
        try:current=site_for_url(urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'','')))
        except ValueError:raise Blocked('posting_changed_review','The browser left the saved Workday requisition') from None
        if (current.host,current.site,current.path)!=(expected.host,expected.site,expected.path):
            raise Blocked('posting_changed_review','The browser shows a different Workday requisition')
        view=observe(page)
        if re.search(r"(?:the page you are looking for doesn.t exist|the job you are (?:looking|applying) for (?:is )?no longer|this (?:job|position) is no longer available)",view['text'],re.I):
            raise Blocked('expired_posting','Workday no longer publishes this requisition')
        descriptions=page.locator('[data-automation-id="jobPostingDescription"]').filter(visible=True)
        if descriptions.count()==1:
            if normalize(job['title']) not in {normalize(title) for title in view['headings']}:
                raise Blocked('posting_changed_review','The employer posting title changed')
            if not re.search(r'(?<![A-Za-z0-9])'+re.escape(requisition)+r'(?![A-Za-z0-9])',view['text']):
                raise Blocked('posting_inspection_review','The rendered posting does not identify the saved requisition')
            description=descriptions.inner_text().strip()
            if description:return description
        page.wait_for_timeout(200)
    raise Blocked('posting_fetch_failed','The Workday browser did not finish loading the public posting')
