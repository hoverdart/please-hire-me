"""iCIMS posting identity and the hosted application frame boundary.

An iCIMS career site shows a requisition inside a same-origin content frame.
The adaptive executor uses that document while retaining the employer's real
wrapper. Only frames on the posting's exact origin for the same requisition
number qualify, including nested wrappers. Other frames stay outside the
executor and become a specific task. No iCIMS request is authored here.

Live iCIMS acceptance is not established by this module or its fixtures.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from .util import Blocked

HOST = re.compile(r'[a-z0-9-]{1,80}\.icims\.com')
JOB_PATH = re.compile(r'/jobs/(\d{1,12})(?:/[A-Za-z0-9%._~-]{0,300})?/(?:job|login|candidate|questions|profile|apply|confirmation)/?')
WRAPPER = 'iframe#icims_content_iframe'


def posting_url(url):
    """Resolve the exact public AMD careers bridge; never infer another tenant.

    The public posting's Apply link uses the same numeric requisition on AMD's
    iCIMS host. Keep the saved source URL and job ID; this is navigation only.
    """
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == 'https' and parsed.hostname == 'careers.amd.com'
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None):
            match = re.fullmatch(r'/careers-home/jobs/(\d{1,12})/?', parsed.path)
            if match:
                return f'https://careers-amd.icims.com/jobs/{match[1]}/job'
    except ValueError:
        pass
    return url


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
    url = posting_url(url)
    parsed = urlsplit(url)
    found = requisition(url)
    if parsed.scheme != 'https' or not found or not is_icims_host(parsed.hostname):
        return None
    return ('icims', found[0], found[1])


def frame_url(url):
    """The posting's own content address, keeping its origin, path and requisition."""
    url = posting_url(url)
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


class ContentPage:
    """Use only the wrapper's approved document, retaining its real parent.

    iCIMS can restore its wrapper when content is opened as a top-level page.
    Resolve the current frame for each DOM operation instead. A replaced frame
    never authorizes another requisition or origin. Page-level events, keyboard
    focus and screenshots still belong to the actual browser page.
    """
    _PARENT = {'context', 'keyboard', 'on', 'remove_listener', 'is_closed', 'close',
               'screenshot', 'bring_to_front', 'wait_for_timeout', 'expect_response'}
    _retry_detached_reads = True

    def __init__(self, page):
        self.parent = page
        self.expected = requisition(page.url)
        parsed = urlsplit(page.url)
        self.origin = (parsed.scheme, parsed.netloc)
        if not self.expected:
            raise Blocked('icims_frame_boundary')

    def _document(self):
        from .browser_observation import retry_dom_read
        return retry_dom_read(self, self._read_document)

    def _read_document(self):
        document = self.parent
        for depth in range(9):
            parsed = urlsplit(document.url)
            if (requisition(document.url) != self.expected
                    or (parsed.scheme, parsed.netloc) != self.origin):
                raise Blocked('icims_frame_boundary', 'The application moved to another requisition or site')
            sources = document.locator(WRAPPER).evaluate_all('nodes=>nodes.map(node=>node.src)')
            if not sources:
                return document
            if len(sources) != 1 or depth == 8:
                raise Blocked('icims_frame_boundary')
            approved_frame(document.url, sources[0])
            handle = document.locator(WRAPPER).element_handle()
            try:
                frame = handle.content_frame() if handle else None
            finally:
                if handle:handle.dispose()
            if frame is None:
                raise Blocked('icims_frame_boundary', 'The application content frame is unavailable')
            if frame.url in ('', 'about:blank'):
                frame.wait_for_url(re.compile(r'^https?://'), timeout=5000)
            document = frame

    @property
    def url(self):
        return self._document().url

    def __getattr__(self, name):
        return getattr(self.parent if name in self._PARENT else self._document(), name)
