"""Posting-bound browser navigation, with optional model selection of observed links.

The model selects an opaque candidate ID. Python constructs the candidates and
navigates directly; remote event handlers, model selectors and invented URLs do
not become browser actions. Submission and draft-write grants remain separate.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .util import Blocked, digest


LINKS = r"""() => Array.from(document.querySelectorAll('a[href]')).filter(e =>
  e.checkVisibility({visibilityProperty:true,opacityProperty:true}) &&
  !e.closest('form,[inert],[aria-hidden="true"]') &&
  !e.hasAttribute('download')).map(e => ({
    text:(e.getAttribute('aria-label') || e.innerText).trim().replace(/\s+/g,' ').slice(0,200),
    href:e.href, role:e.getAttribute('role') || 'link'
  })).slice(0,250)"""


def application_target(job, url):
    """An application link must keep the exact saved employer requisition."""
    try:original, target = urlsplit(job['url']), urlsplit(url)
    except ValueError:return False
    if (target.scheme != original.scheme or target.netloc != original.netloc
            or target.username or target.password or target.fragment
            or target.query or target.path == original.path):
        return False
    path = original.path.rstrip('/')
    # These are application-page routes, never guessed mutation endpoints.
    if job['host'] == 'jobs.ashbyhq.com':
        path = re.sub(r'/application$', '', path)
        return target.path.rstrip('/') == path + '/application'
    if job['host'] in {'jobs.lever.co', 'jobs.eu.lever.co'}:
        path = re.sub(r'/apply$', '', path)
        return target.path.rstrip('/') == path + '/apply'
    return False


def candidates(job, links, visited=()):
    found = {}
    for link in links:
        url, text = link.get('href'), link.get('text', '')
        if (not isinstance(url, str) or not isinstance(text, str)
                or url in visited or not application_target(job, url)
                or not re.search(r'\b(?:apply|application)\b', text, re.I)):
            continue
        found.setdefault(url, {'id': digest(url), 'text': text, 'url': url})
    return list(found.values())


def navigate(browser, job, provider=None):
    """Recover a posting-to-application navigation without running a click handler."""
    browser.store.checkpoint()
    visited = getattr(browser, 'navigation_visited', set())
    visited.add(browser.page.url)
    browser.navigation_visited = visited
    rows = candidates(job, browser.page.evaluate(LINKS), visited)
    if not rows:
        return False
    if len(rows) == 1:
        selected = rows[0]
    else:
        if provider is None:
            return False
        choice = provider.choose_browser_action(
            {'company': job['company'], 'title': job['title']},
            [{'id': row['id'], 'label': row['text']} for row in rows])
        selected = next((row for row in rows if row['id'] == choice), None)
        if selected is None:
            return False
    # Reinspect after inference: a stale candidate cannot authorize navigation.
    fresh = candidates(job, browser.page.evaluate(LINKS), visited)
    if selected not in fresh:
        raise Blocked('form_changed', 'The application navigation changed during inspection')
    browser.store.checkpoint()
    browser.store.event('browser_navigation', job['id'], {'candidate': selected['id']})
    browser.page.goto(selected['url'], wait_until='domcontentloaded', timeout=45000)
    visited.add(selected['url'])
    browser._wait_ready()
    return True
