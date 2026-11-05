"""Bind a public Workday career site to its exact supplied posting URL."""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit


HOST=re.compile(r'(?P<tenant>[a-z0-9][a-z0-9-]{0,80})\.(?P<shard>wd\d{1,3})\.myworkdayjobs\.com')


@dataclass(frozen=True)
class WorkdaySite:
    host: str
    tenant: str
    shard: str
    site: str
    path: str


def site_for_url(url):
    parsed=urlsplit(url)
    match=HOST.fullmatch(parsed.hostname or '')
    if (not match or parsed.scheme!='https' or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Use an exact public Workday requisition URL')
    path=re.fullmatch(r'/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9_-]{1,100})'
        r'(?P<posting>/job/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+)',parsed.path)
    if not path or any(segment in {'.','..'} for segment in parsed.path.split('/')):
        raise ValueError('Use an exact public Workday requisition URL')
    from .portals import WORKDAY
    configured=next((row for row in WORKDAY if row[4]==parsed.hostname),None)
    if configured and path['site']!=configured[3]:raise ValueError('The Workday career site does not match its configured employer')
    return WorkdaySite(parsed.hostname,match['tenant'],match['shard'],path['site'],path['posting'])


def is_workday_url(url):
    try:site_for_url(url);return True
    except (ValueError,TypeError):return False


def asset_read(host,request):
    """The Workday client fetches translations as JSON, not script resources."""
    match=HOST.fullmatch(host or '')
    if not match or request.method not in {'GET','HEAD','OPTIONS'}:return False
    parsed=urlsplit(request.url)
    return bool(parsed.scheme=='https' and parsed.port in (None,443) and not parsed.username and not parsed.password
        and parsed.hostname in {match['shard']+'.myworkday.com',match['shard']+'.myworkdaycdn.com'}
        and request.resource_type!='document' and not parsed.query
        and re.fullmatch(r'/wday/asset/[A-Za-z0-9._/-]+',parsed.path)
        and '..' not in parsed.path.split('/'))
