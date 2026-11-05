"""Verify the exact public requisition in Workday's rendered client."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit,urlunsplit

from .browser_observation import observe
from .util import Blocked
from .workday_site import site_for_url


def inspect_posting(page,job,checkpoint,timeout=25):
    expected=site_for_url(job['url'])
    normalize=lambda text:' '.join(text.casefold().split())
    requisition=expected.path.rsplit('_',1)[-1]
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
