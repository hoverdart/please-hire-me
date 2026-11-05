"""Read one saved platform listing and route an explicit employer ATS link.

This path does not search a platform, click Quick Apply, upload a document or
grant a platform mutation. Native platform applications retain their own gates.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from .util import Blocked, now


LISTING = r"""() => {
 const root=document.querySelector('main,[role=main]') || document.body;
 const visible=e=>e.checkVisibility({visibilityProperty:true,opacityProperty:true});
 return {text:root.innerText.slice(0,100000),
   headings:Array.from(root.querySelectorAll('h1,h2,h3,[role=heading]')).filter(visible).map(e=>e.innerText.trim()),
   links:Array.from(root.querySelectorAll('a[href]')).filter(e=>visible(e)&&!e.closest('form')&&!e.hasAttribute('download')).map(e=>({
     text:(e.getAttribute('aria-label')||e.innerText).trim(),href:e.href
   })).slice(0,250)};
}"""


def external_destination(store, job, snapshot):
    from .platform_connections import _external_requisition
    from .discovery import posting
    normalize = lambda value: ' '.join(value.casefold().split())
    title, company = normalize(job['title']), normalize(job['company'])
    headings = {normalize(value) for value in snapshot['headings']}
    if not headings.intersection({title, title+' at '+company, title+' @ '+company}):
        raise Blocked('posting_changed_review', 'The saved role could not be matched to the visible listing heading')
    text = normalize(snapshot['text'])
    if not re.search(r'(?<!\w)'+re.escape(company)+r'(?!\w)', text):
        raise Blocked('posting_changed_review', 'The saved employer could not be matched to the visible listing')
    targets = {}
    for link in snapshot['links']:
        if not re.fullmatch(r'apply(?: externally| (?:on|at|via) (?:the )?(?:company|employer)(?:[’\']s)?(?: (?:website|site))?| for this job| now)?',
                            link['text'].strip(), re.I):
            continue
        try:
            target = posting(link['href'], job['company'], job['title'], job.get('location',''),
                             job['connection_id'], snapshot['text'])
            _external_requisition(target['url'])
            if not _same_employer(store,job,target):continue
        except (ValueError, Blocked):
            continue
        targets[target['url']] = target
    if len(targets) > 1:
        raise Blocked('connection_identity_review', 'The listing exposes multiple employer application destinations')
    return next(iter(targets.values()), None)


def _same_employer(store,job,target):
    # A platform's outbound link must keep the saved employer too. The existing
    # ATS adapter subsequently verifies the exact role before any field writes.
    parts=urlsplit(target['url']).path.strip('/').split('/')
    if target['host'].endswith('.myworkdayjobs.com'):
        from .portals import WORKDAY
        issuer=next((row[0] for row in WORKDAY if row[4]==target['host']),None)
        if issuer is None:
            from .workday_site import site_for_url
            try:issuer=site_for_url(target['url']).tenant
            except ValueError:return False
    elif target['host'] in {'jobs.lever.co','jobs.eu.lever.co','jobs.ashbyhq.com',
            'boards.greenhouse.io','job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io',
            'apply.workable.com','jobs.smartrecruiters.com'}:
        issuer=parts[0]
    else:
        return False
    company_key=lambda value:re.sub(r'[^a-z0-9]','',store.company(value))
    return bool(issuer) and company_key(issuer)==company_key(job['company'])


def save_route(store, job, employer):
    """Move only an untouched listing origin; retain every historical identity."""
    from .job_holds import safe_state
    from .platform_connections import platform_listing, _external_requisition
    _external_requisition(employer['url'])
    if not _same_employer(store,job,employer):
        raise Blocked('connection_destination_review','The destination does not match the saved employer ATS account')
    connection, native, listing = platform_listing(job['url'])
    with store.transaction():
        saved=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
        identity=('id','url','host','company','title','connection_id','platform_job_id')
        saved=json.loads(saved['payload']) if saved else None
        if not saved or any(saved.get(key)!=job.get(key) for key in identity):
            raise Blocked('posting_changed_review','The saved listing changed during inspection')
        if (not safe_state(store,job['id'])
                or store.db.execute('SELECT 1 FROM application_records WHERE job_id=?',(job['id'],)).fetchone()
                or store.db.execute('SELECT 1 FROM job_decisions WHERE job_id=?',(job['id'],)).fetchone()):
            raise Blocked('connection_identity_review', 'This listing has application history. Review the employer destination before routing it.')
        existing = store.db.execute('SELECT id,company,title FROM jobs WHERE url=?',(employer['url'],)).fetchone()
        if existing and (store.company(existing['company']) != store.company(job['company'])
                or ' '.join(existing['title'].casefold().split()) != ' '.join(job['title'].casefold().split())):
            raise Blocked('connection_identity_review', 'The employer requisition conflicts with an existing company or role')
        origin = store.db.execute('SELECT job_id FROM job_origins WHERE connection_id=? AND platform_job_id=?',
                                 (connection,native)).fetchone()
        destination_key=existing['id'] if existing else employer['id']
        if origin and origin['job_id'] not in {job['id'],destination_key}:
            raise Blocked('connection_identity_review', 'This listing already identifies a different saved requisition')
        # A platform observation must not replace an existing official ATS
        # posting, its source or its historical canonical identifier.
        key = existing['id'] if existing else store.upsert_job(employer)
        store.db.execute('INSERT INTO job_origins VALUES(?,?,?,?,?) ON CONFLICT(connection_id,platform_job_id) '
                         'DO UPDATE SET job_id=excluded.job_id,listing_url=excluded.listing_url,observed=excluded.observed',
                         (connection,native,key,listing,now()))
        store.db.execute("UPDATE jobs SET status='routed',reason='' WHERE id=?",(job['id'],))
        store.db.execute('DELETE FROM job_holds WHERE job_id=?',(job['id'],))
        store.retire_questions(job['id'],set())
        store.db.execute('DELETE FROM connection_conflicts WHERE connection_id=? AND platform_job_id=?',(connection,native))
        store.event('connection_external_routed',job['id'],{'job_id':key,'destination':employer['host']})
    return key


def inspect_external(browser, job):
    """Inspect a specific saved link using the dedicated authenticated profile."""
    from .platform_connections import platform_listing
    connection, _, listing = platform_listing(job['url'])
    if connection != browser.connection_id or job.get('host') != browser.spec['host']:
        raise Blocked('connection_identity_review')
    browser.inspecting_listing = True
    try:
        browser.page.goto(browser.test_url or listing,wait_until='domcontentloaded',timeout=45000)
        if not browser.test_url:
            try:
                current_connection, _, current = platform_listing(browser.page.url)
            except ValueError:
                raise Blocked('connection_sign_in_required','Sign in to inspect this saved platform job') from None
            if current_connection != connection or current != listing:
                raise Blocked('posting_changed_review','The platform redirected to a different listing')
        # Wait for the listing heading rather than declaring a client shell an
        # unsupported form immediately after DOMContentLoaded.
        deadline = time.monotonic()+15
        while True:
            browser.store.checkpoint()
            browser._guard_connection()
            snapshot = browser.page.evaluate(LISTING)
            if browser.page.locator('input[type=password]:visible').count():
                raise Blocked('connection_sign_in_required')
            try:
                employer = external_destination(browser.store,job,snapshot)
                break
            except Blocked as error:
                if error.reason != 'posting_changed_review' or time.monotonic()>=deadline:
                    raise
                browser.page.wait_for_timeout(200)
        if employer:
            save_route(browser.store,job,employer)
        return employer
    finally:
        browser.inspecting_listing = False
