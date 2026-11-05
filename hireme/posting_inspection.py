"""Anonymous, read-only employer detail checks before application selection."""
from __future__ import annotations

import re
import tempfile

from .util import Blocked,canonical_url,now


def needs_details(job):
    text=job.get('description','').strip()
    return not text or bool(re.fullmatch(r'Sponsorship:[^\n]*',text))


def inspect(store,job):
    from .ats_adapters import adapter_for
    adapter=adapter_for(job)
    workday=job['host'].endswith('.myworkdayjobs.com')
    navigation,text=((job['url'],None) if workday else
                     adapter.navigation(job,checkpoint=store.checkpoint,deadline=getattr(store,'run_deadline',None)))
    if not text:
        from .browser import Browser
        from .browser_agent_protocol import read_request
        from .browser_observation import observe
        class AnonymousStore:
            def settings(self):return {**store.settings(),'headless':True,'human_verification_wait_seconds':0,'human_verification_handoff':False}
            def __getattr__(self,name):return getattr(store,name)
        # A disposable profile cannot attach personal tabs or applicant sessions.
        with tempfile.TemporaryDirectory(prefix='.hireme-posting-read-',dir=store.root.parent) as directory:
            reader=Browser(AnonymousStore());reader.profile_directory=directory
            reader.current_host=job['host'];reader.preparing=True
            with reader:
                reader.context.unroute('**/*')
                def readonly(route):
                    if not read_request(route.request):return route.abort()
                    if workday:
                        from urllib.parse import urlsplit
                        from .net import public_host
                        parsed=urlsplit(route.request.url);shard=job['host'].split('.')[1]
                        if (parsed.scheme=='https' and parsed.hostname in {shard+'.myworkday.com',shard+'.myworkdaycdn.com'}
                                and parsed.port in (None,443) and not parsed.username and not parsed.password
                                and route.request.method in {'GET','HEAD','OPTIONS'}
                                and re.fullmatch(r'/wday/asset/[A-Za-z0-9._/-]+',parsed.path) and '..' not in parsed.path.split('/')):
                            return route.continue_() if public_host(parsed.hostname) else route.abort()
                    reader._route(route)
                reader.context.route('**/*',readonly)
                reader.page.goto(navigation,wait_until='domcontentloaded',timeout=45000)
                if job['host'].endswith('.myworkdayjobs.com'):
                    from .workday_browser_posting import inspect_posting
                    text=inspect_posting(reader.page,job,store.checkpoint)
                else:
                    reader.page.wait_for_function('()=>document.body&&document.body.innerText.trim().length>0',timeout=20000)
                    if canonical_url(reader.page.url)!=canonical_url(navigation):raise Blocked('posting_changed_review','The public posting moved to a different destination')
                    view=observe(reader.page);normalize=lambda value:' '.join(value.casefold().split())
                    if normalize(job['title']) not in {normalize(title) for title in view['headings']}:
                        raise Blocked('posting_changed_review','The public employer page did not verify the saved title')
                    text=adapter.posting_text(view['text'])
    if not isinstance(text,str) or not text.strip():raise Blocked('posting_fetch_failed','The public posting body is unavailable')
    store.checkpoint()
    return {**job,'description':text,'_employer_posting_read':now()}


def refresh_candidates(store,rows,settings,limit):
    """Details reads are bounded separately and never count as applications."""
    import json
    from .job_holds import ready,hold
    from .policy import listing_scope
    read=0
    for row in rows:
        job=json.loads(row['payload'])
        if not needs_details(job) or not ready(store,job):continue
        try:listing_scope(job,settings,metadata=True)
        except Blocked:continue
        if read>=limit:break
        store.checkpoint();read+=1
        try:
            verified=inspect(store,job)
            store.upsert_job(verified)
            store.event('posting_details_read',job['id'],{'run_id':store.active_run_id,'source':'anonymous_employer_read'})
        except Blocked as error:
            if error.reason in {'paused','cycle_timeout'}:raise
            store.block(job['id'],error.reason,error.detail);hold(store,job,error.reason,error.detail,'screening')
        except Exception as error:
            store.checkpoint()
            store.block(job['id'],'posting_fetch_failed',type(error).__name__)
            hold(store,job,'posting_fetch_failed',stage='screening')
    return read
