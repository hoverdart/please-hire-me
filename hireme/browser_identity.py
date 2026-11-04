"""Verify employer account identity without exposing credentials to a model."""
from __future__ import annotations

import re

from .util import Blocked


def account_email(data):
    if not isinstance(data,dict) or data.get('errors') or data.get('error'):return None
    for key in ('emailAddress','email','primaryEmail'):
        value=data.get(key)
        if isinstance(value,str) and re.fullmatch(r'[^\s@<>(),;]+@[^\s@<>(),;]+\.[^\s@<>(),;]+',value.strip()):return value.strip().casefold()
    for key in ('data','currentUser','current_user','viewer','userProfile','userprofile','profile'):
        if isinstance(data.get(key),dict):
            found=account_email(data[key])
            if found:return found
    return None


def verify_email(store,value):
    value=value.strip().casefold()
    allowed={str(fact['value']).strip().casefold() for key,fact in store.facts().items()
             if key in {'email','alternate_email'} and fact.get('confirmed')}
    allowed.update(email.casefold() for email in store.settings()['gmail_confirmed_aliases'])
    if value not in allowed:raise Blocked('browser_identity_mismatch','The employer account is signed in with a different email. Sign in as the confirmed applicant in the dedicated application browser.')
    return True


def workday_profile_response(page,job):
    """Use the real browser's cookies, client token and network stack for self reads."""
    from urllib.parse import urlsplit
    from .workday_site import site_for_url
    site=site_for_url(job['url'])
    if urlsplit(page.url).hostname!=site.host:raise Blocked('browser_identity_unverified')
    url=f'https://{site.host}/wday/calypso/cxs/candidatehome/{site.tenant}/{site.site}/userprofile'
    page.wait_for_function('''site=>window.workday?.tenant===site.tenant&&window.workday?.siteId===site.site&&!!window.workday?.token''',
        arg={'tenant':site.tenant,'site':site.site},timeout=5000)
    with page.expect_response(lambda response:response.url==url and response.request.method=='GET',timeout=15000) as pending:
        page.evaluate('''async url=>{
          const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
          try{const response=await fetch(url,{method:'GET',credentials:'same-origin',redirect:'error',
            headers:{accept:'application/json','Accept-Language':document.documentElement.lang,'X-CALYPSO-CSRF-TOKEN':window.workday.token},signal:controller.signal});
            await response.text();}finally{clearTimeout(timer);}
        }''',url)
    return pending.value


def verify_workday_account(agent):
    from .workday_site import site_for_url
    try:site=site_for_url(agent.job['url'])
    except ValueError:return False
    # This is the public client's authenticated profile GET, bound to the
    # exact tenant/site of the already verified public posting. It grants no
    # account creation, draft save, upload or finalization endpoint.
    url=f'https://{site.host}/wday/calypso/cxs/candidatehome/{site.tenant}/{site.site}/userprofile'
    if getattr(agent,'page',None) is not None:
        try:response=workday_profile_response(agent.page,agent.job)
        except Blocked:raise
        except Exception:raise Blocked('browser_identity_unverified','The employer account check did not complete in the application browser') from None
    else:response=agent.context.request.get(url,max_redirects=0,timeout=10000)
    if response.status in {401,403,404} or 300<=response.status<400:return False
    if response.status!=200:raise Blocked('browser_identity_unverified','The employer account check did not complete')
    try:value=account_email(response.json())
    except (ValueError,TypeError):value=None
    if not value:return False
    return verify_email(agent.store,value)


def identity_read(request,connection_id):
    """Recognize an observed self read; never construct a platform endpoint."""
    from urllib.parse import urlsplit
    from .browser_agent_protocol import read_request
    if not connection_id or not read_request(request):return False
    path=urlsplit(request.url).path
    if request.method=='GET':return bool(re.fullmatch(r'/api/(?:v\d+/)?(?:current_user|current-user|me|user/profile|users/me)',path))
    if request.method=='POST' and path in {'/hs/graphql','/graphql'}:
        import json
        try:
            data=json.loads(request.post_data_buffer or b'')
            return isinstance(data,dict) and data.get('operationName') in {'GetCurrentUser','CurrentUser','Viewer','Me'}
        except (ValueError,TypeError,UnicodeError):return False
    return False


def verify_observed_account(agent):
    """Read only explicitly applicant-owned identity surfaces in the page."""
    data=agent.page.evaluate('''()=>{
      const user=window.__NEXT_DATA__?.props?.pageProps?.currentUser||window.gon?.current_user||window.__INITIAL_STATE__?.currentUser;
      if(user)return {currentUser:{email:user.email||user.emailAddress}};
      const text=document.body?.innerText||'';
      const match=text.match(/(?:signed in as|logged in as|account email)\\s*:?\\s*([^\\s@]+@[^\\s@]+\\.[^\\s@.,;]+)/i);
      return match?{email:match[1]}:null;
    }''')
    value=account_email(data)
    return verify_email(agent.store,value) if value else False
