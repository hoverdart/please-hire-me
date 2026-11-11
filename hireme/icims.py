"""iCIMS posting identity and the hosted application frame boundary.

An iCIMS career site shows a requisition inside a same-origin content frame.
The adaptive executor works on one top-level document, so it opens that
frame's own address directly. Only a frame on the posting's exact origin for
the same requisition number qualifies; every other frame stays outside the
executor and becomes a specific task. No iCIMS request is authored here.

Live iCIMS acceptance is not established by this module or its fixtures.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from .util import Blocked

HOST = re.compile(r'[a-z0-9-]{1,80}\.icims\.com')
JOB_PATH = re.compile(r'/jobs/(\d{1,12})(?:/[A-Za-z0-9%._~-]{0,300})?/(?:job|login|candidate|questions|profile|apply|confirmation)/?')
WRAPPER = 'iframe#icims_content_iframe'


def is_icims_host(host):
    return bool(HOST.fullmatch((host or '').split('|', 1)[0].casefold()))


def requisition(url):
    """(host, numeric requisition ID) for a recognized hosted posting or application step, else None."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    match = JOB_PATH.fullmatch(parsed.path)
    if not match or parsed.username or parsed.password or not parsed.hostname:
        return None
    return parsed.hostname.casefold() + (f':{port}' if port else ''), match[1]


def identity(url):
    """Duplicate-comparison identity: one requisition regardless of its title slug or frame query."""
    parsed = urlsplit(url)
    found = requisition(url)
    if parsed.scheme != 'https' or not found or not is_icims_host(parsed.hostname):
        return None
    return ('icims', found[0], found[1])


def frame_url(url):
    """The posting's own content address, keeping its origin, path and requisition."""
    parsed = urlsplit(url)
    if not requisition(url):
        raise Blocked('icims_posting_unrecognized', 'This iCIMS link is not a recognized job posting address. Open it and apply manually.')
    # Tracking and session parameters are not carried into the application.
    return f'{parsed.scheme}://{parsed.netloc}{parsed.path}?in_iframe=1'


def approved_frame(page_url, source):
    """Validate an observed content frame against the page that embeds it."""
    try:
        page, frame = urlsplit(page_url), urlsplit(source)
    except ValueError:
        frame = None
    expected, actual = requisition(page_url), requisition(source) if frame else None
    if (not frame or frame.scheme != page.scheme or frame.netloc != page.netloc or frame.username or frame.password
            or not expected or actual != expected or dict(parse_qsl(frame.query)).get('in_iframe') != '1'):
        raise Blocked('icims_frame_boundary', 'This application is embedded from a different site or for a different requisition, '
                      'so it is outside the automatic browser. Open the posting and apply manually, then record it as Applied manually.')
    return f'{frame.scheme}://{frame.netloc}{frame.path}?in_iframe=1'


def enter_content(page, *, limit_reached=False):
    """If the page is the iCIMS wrapper, return the approved content address to open; otherwise None."""
    sources = page.locator(WRAPPER).evaluate_all('nodes=>nodes.map(node=>node.src)')
    if not sources:
        return None
    if len(sources) != 1 or limit_reached:
        raise Blocked('icims_frame_boundary', 'The application page kept returning to an embedded frame. Open the posting and apply manually.')
    return approved_frame(page.url, sources[0])
