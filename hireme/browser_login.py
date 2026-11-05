"""Human sign-in in the worker's own browser profile, from a saved posting."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from urllib.parse import urlsplit

from .util import Blocked, private_dir, public_host


def saved_destination(store,job_id):
    from .discovery import ATS_HOSTS,PORTAL_HOSTS
    from .workday_site import is_workday_url
    row=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise ValueError('Choose a saved job')
    job=json.loads(row['payload']);parsed=urlsplit(job['url'])
    if job.get('connection_id'):raise ValueError('Use Sign in in this platform’s connection settings')
    if (parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password
            or parsed.hostname!=job['host'] or not public_host(parsed.hostname)
            or job['host'] not in ATS_HOSTS|PORTAL_HOSTS and not is_workday_url(job['url'])):
        raise ValueError('Choose an approved employer posting')
    return job


def login_completed(store,host):
    store.update_settings({'signed_in_portals':sorted(set(store.settings()['signed_in_portals'])|{host})})
    # This only releases a sign-in hold for another independent identity check.
    # It is not proof of the account owner, a saved draft, or a submission.
    store.event('browser_login_completed',host,{})


def retain_nvidia_sign_in(store,context,job,timeout=600):
    """Verify outside response callbacks, including an OAuth-created tab."""
    from .browser_identity import workday_profile_response,account_email,verify_email
    from .workday_sessions import capture
    deadline=time.monotonic()+timeout;next_check=0;last_progress=None
    def progress(state):
        nonlocal last_progress
        if state!=last_progress:
            store.event('browser_login_progress',job['host'],{'state':state})
            last_progress=state
    while time.monotonic()<deadline:
        pages=[page for page in context.pages if not page.is_closed()]
        if not pages:return False
        if time.monotonic()>=next_check:
            for page in reversed(pages):
                if urlsplit(page.url).hostname!=job['host']:continue
                try:
                    if not page.evaluate('()=>!!window.workday?.token'):continue
                    response=workday_profile_response(page,job)
                    if response.status!=200:
                        progress('waiting_for_sign_in' if response.status in {401,403} else 'profile_read_failed')
                        continue
                    value=account_email(response.json())
                    if not value:continue
                    verify_email(store,value)
                    capture(store,context,response)
                    store.event('browser_login_verified',job['host'],{})
                    return True
                except Blocked as error:
                    progress(error.reason)
                    continue
                except Exception:
                    # A navigation or incomplete OAuth step is not owner proof.
                    # Keep the user's window available for another bounded read.
                    continue
            next_check=time.monotonic()+5
        try:pages[0].wait_for_timeout(250)
        except Exception:pass
    return False


def start(store,job_id):
    from .store import Store,worker_lock
    job=saved_destination(store,job_id)
    with worker_lock(store.root),worker_lock(store.root,'browser'):
        from .browser_agent import AdaptiveBrowser
        AdaptiveBrowser(store)._binding()
        store.event('browser_login_started',job['host'],{'job_id':job_id})
    root=store.root
    def run():
        from playwright.sync_api import sync_playwright
        worker=Store(root)
        try:
            with worker_lock(root),worker_lock(root,'browser'),sync_playwright() as playwright:
                channel=worker.settings()['browser_channel']
                options={'headless':False,'accept_downloads':False}
                if channel=='chrome':options['channel']='chrome'
                elif channel=='system-chromium':
                    options['executable_path']=shutil.which('chromium') or shutil.which('chromium-browser')
                    if not options['executable_path']:raise ValueError('Install system Chromium first')
                context=playwright.chromium.launch_persistent_context(str(private_dir(root/'browser')),**options)
                closed=False;verified=False
                try:
                    from .workday_profiles import NVIDIA
                    page=context.pages[0] if context.pages else context.new_page()
                    page.goto(job['url'],wait_until='domcontentloaded',timeout=45000)
                    if job['host']==NVIDIA:
                        # Open the employer's provider chooser, rather than
                        # leaving the user on a posting beside Chrome's own
                        # unrelated browser-profile sign-in controls.
                        try:page.locator('button,a').filter(has_text=re.compile(r'^sign in$',re.I)).filter(visible=True).first.click(timeout=10000)
                        except Exception:pass
                        verified=retain_nvidia_sign_in(worker,context,job)
                    else:
                        deadline=time.monotonic()+600
                        while time.monotonic()<deadline:
                            if page.is_closed():closed=True;break
                            try:page.wait_for_timeout(250)
                            except Exception:
                                if page.is_closed():closed=True;break
                                raise
                finally:
                    try:context.close()
                    except Exception:pass
                if verified or closed:login_completed(worker,job['host'])
                else:worker.event('browser_login_failed',job['host'],{'reason':'sign_in_not_verified'})
        except Exception:
            worker.event('browser_login_failed',job['host'],{'reason':'could_not_open_or_complete'})
        finally:worker.close()
    threading.Thread(target=run,name='hireme-employer-sign-in',daemon=True).start()
    from .workday_profiles import NVIDIA
    message=('Sign in to NVIDIA in the dedicated browser and leave the window open. It will close automatically after the app verifies and retains your session.'
        if job['host']==NVIDIA else 'Sign in in the dedicated employer browser on the worker desktop, then close that window. The next run will verify the account and resume the saved application.')
    return {'started':True,'message':message}
