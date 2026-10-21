"""A synchronous Playwright body read can reenter the outcome poller.

The response is pending until its decoder has finished. A provisional failure
must not make the worker classify a transmitted request before that point.
"""
import json
from types import SimpleNamespace

import pytest

from hireme.ashby_submission import AshbySubmission
from hireme.browser import Browser
from hireme.lever_submission import LeverSubmission
from hireme.util import digest


RECEIPT='Thank you for applying. Your application has been received.'


@pytest.mark.parametrize('platform',['ashby','lever'])
@pytest.mark.parametrize('accepted',[False,True])
def test_outcome_poll_waits_for_reentrant_native_response_body_decode(store,platform,accepted):
    browser=Browser(store)
    browser.attempted=True
    browser.current_host='jobs.ashbyhq.com' if platform=='ashby' else 'jobs.lever.co'
    browser._guard=lambda *args,**kwargs:RECEIPT
    class StillDecoding(Exception):pass
    def wait(milliseconds):raise StillDecoding()
    browser.page=SimpleNamespace(url='https://jobs.lever.co/acme/posting/apply',
        locator=lambda selector:SimpleNamespace(count=lambda:0),wait_for_timeout=wait)
    request=SimpleNamespace(url='https://jobs.ashbyhq.com/api/non-user-graphql',method='POST',post_data=json.dumps({'native':'request'}))
    key=(request.url,request.method,'request-key')
    grant=object.__new__(AshbySubmission if platform=='ashby' else LeverSubmission)
    grant.used=True;grant.response_seen=False;grant.acknowledged=False;grant.failed=False
    grant.store=store;grant.aid='synthetic-decoder';grant.surveys=[]
    grant.request_signature=(request.url,request.method,digest({'native':'request'}))
    grant.request_keys={key};grant.baseline='Software engineer internship';grant.url=browser.page.url
    if platform=='ashby':browser.ashby_submission=grant
    else:browser.native_submission=grant
    def decode():
        # This is the same reentrance exposed by response.json()/text() on a
        # slow worker: body decoding yields and an outcome poll runs first.
        with pytest.raises(StillDecoding):browser._wait_submission_outcome({'id':'synthetic-job'})
        if platform=='lever':return '<html><body>'+RECEIPT+'</body></html>' if accepted else '<p>Not accepted.</p>'
        return {'data':{'submitApplicationFormAction':{'applicationFormResult':
            {'__typename':'FormSubmitSuccess' if accepted else 'FormSubmitError'},'messages':{}}}}
    response=SimpleNamespace(request=request,status=200,headers={'content-type':'text/html'},json=decode,text=decode)
    if platform=='ashby':grant.observe_response(response)
    else:grant.observe(response,key)
    assert grant.response_seen and grant.acknowledged is accepted and grant.failed is not accepted
    assert browser._wait_submission_outcome({'id':'synthetic-job'})==RECEIPT
