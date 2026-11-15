"""Verify the exact public requisition in Workday's rendered client."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit,urlunsplit

from .browser_observation import observe
from .util import Blocked,digest
from .workday_site import site_for_url

VERSION=4


def public_identity(job,data,*,allow_saved=False):
    """Join the official API identity to this exact supplied URL, including variants."""
    try:
        expected=site_for_url(job['url']);info=data['jobPostingInfo']
        current=site_for_url(info['externalUrl']);anchor=expected.path.rsplit('/',1)[-1]
        req=info['jobReqId'];title=info['title']
        saved=info.get('jobApplication')
        continuation=(allow_saved and isinstance(saved,dict) and saved.get('draft') is True
            and saved.get('dateCompleted') is None and isinstance(saved.get('id'),str)
            and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._~-]{0,299}',saved['id']))
        if (current!=expected or info.get('jobPostingId')!=anchor or info.get('jobPostingSiteId')!=expected.site
                or info.get('posted') is not True or not (info.get('canApply') is True or continuation)
                or not isinstance(req,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',req)
                or not re.fullmatch(r'.+_'+re.escape(req)+r'(?:-[0-9]+)?',anchor)
                or not isinstance(title,str) or not 1<=len(title.strip())<=500 or '<' in title or '>' in title):return None
        return {'host':expected.host,'site':expected.site,'posting_anchor':anchor,'requisition_id':req,
                'title':title.strip(),'evidence_hash':digest(info)}
    except (KeyError,ValueError,TypeError,AttributeError):return None


def verified_posting(page,job,checkpoint,*,saved_application=None):
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
    identity=None;saved_proof=None
    if isinstance(result,dict):
        if result.get('status') in {404,410}:
            raise Blocked('expired_posting','Workday no longer publishes this requisition')
        data=result.get('data')
        if result.get('status')==200 and isinstance(data,dict) and 'jobPostingInfo' in data:
            info=data['jobPostingInfo']
            if isinstance(info,dict) and info.get('posted') is False:
                raise Blocked('expired_posting','Workday no longer accepts applications for this requisition')
            is_saved=isinstance(info,dict) and info.get('canApply') is False and isinstance(info.get('jobApplication'),dict) and info['jobApplication'].get('draft') is True
            if isinstance(info,dict) and info.get('canApply') is False and not is_saved:
                raise Blocked('expired_posting','Workday no longer accepts applications for this requisition')
            identity=public_identity(job,data,allow_saved=is_saved)
            if not identity:raise Blocked('posting_changed_review','The official Workday record does not match the saved requisition')
            if is_saved:
                current={**job,'title':identity['title'],'_workday_public_identity':identity}
                saved_proof=saved_application(current,info) if saved_application else None
                if not saved_proof:raise Blocked('browser_draft_review','Verify the exact owned saved application and its acknowledged local checkpoint before continuing')
    current={**job,'title':identity['title'],'_workday_public_identity':identity} if identity else dict(job)
    if saved_proof:current['_workday_saved_application']=saved_proof
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
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',requisition):raise Blocked('posting_inspection_review')
    deadline=time.monotonic()+timeout
    identity_deadline=None;mismatch=None
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
            title_matches=normalize(job['title']) in {normalize(title) for title in view['headings']}
            req_matches=re.search(r'(?<![A-Za-z0-9_])'+re.escape(requisition)+r'(?![A-Za-z0-9_])',view['text'])
            if title_matches and req_matches:
                description=descriptions.inner_text().strip()
                if description:return description
            else:
                mismatch=('posting_changed_review','The employer posting title changed') if not title_matches else (
                    'posting_inspection_review','The rendered posting does not identify the saved requisition')
                if identity_deadline is None:identity_deadline=min(deadline,time.monotonic()+3)
                if time.monotonic()>=identity_deadline:raise Blocked(*mismatch)
        page.wait_for_timeout(200)
    if mismatch:raise Blocked(*mismatch)
    raise Blocked('posting_fetch_failed','The Workday browser did not finish loading the public posting')
