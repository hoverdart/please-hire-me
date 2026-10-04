import ssl
from urllib.error import HTTPError,URLError

import pytest
from hireme.ats_adapters import WorkdayPostingAdapter,adapter_for
from hireme.portals import WORKDAY
from hireme.util import Blocked


def fixture(configured):
    _,tenant,_,site,host=configured
    path='/job/US-CA-Santa-Clara/Software-Engineering-Intern_JR12345'
    job={'host':host,'url':f'https://{host}/en-US/{site}'+path,'title':'Software Engineering Intern'}
    response={'jobPostingInfo':{'title':job['title'],'jobDescription':'<p>Currently pursuing a computer science degree.</p><p>Build Python software.</p>',
        'jobReqId':'JR12345','jobPostingId':path.rsplit('/',1)[1],'jobPostingSiteId':site,
        'externalUrl':f'https://{host}/{site}'+path,'canApply':True}}
    return job,response,f'https://{host}/wday/cxs/{tenant}/{site}'+path


@pytest.mark.parametrize('configured',WORKDAY)
def test_public_posting_read_is_scoped_and_checkpointed(monkeypatch,configured):
    job,response,endpoint=fixture(configured);calls=[];checks=[]
    class ReadOnlyNetwork:
        def __init__(self,checkpoint=None,deadline=None):
            assert deadline==123;checkpoint()
        def json(self,url,data=None):calls.append((url,data));return response
    monkeypatch.setattr('hireme.net.Network',ReadOnlyNetwork)
    adapter=adapter_for(job);assert isinstance(adapter,WorkdayPostingAdapter)
    url,text=adapter.navigation(job,checkpoint=lambda:checks.append(True),deadline=123)
    assert calls==[(endpoint,None)] and checks==[True,True] and url==job['url']
    assert 'Build Python software.' in text
    assert adapter.posting_text('Sign in. Will you graduate before September?',text)==text


@pytest.mark.parametrize('change,reason',[
    ({'jobReqId':'JR99999'},'posting_inspection_review'),
    ({'jobPostingId':'another-posting_JR12345'},'posting_inspection_review'),
    ({'jobPostingSiteId':'AnotherSite'},'posting_inspection_review'),
    ({'externalUrl':'https://attacker.invalid/register'},'posting_inspection_review'),
    ({'externalUrl':'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Other_JR12345'},'posting_changed_review'),
    ({'title':'Senior Software Engineer'},'posting_changed_review'),
    ({'jobDescription':''},'posting_inspection_review'),
    ({'canApply':False},'expired_posting'),
    ({'canApply':'true'},'posting_inspection_review'),
])
def test_posting_identity_and_availability_require_review(monkeypatch,change,reason):
    job,response,_=fixture(WORKDAY[0]);response['jobPostingInfo'].update(change)
    monkeypatch.setattr('hireme.net.Network.json',lambda *a,**kw:response)
    with pytest.raises(Blocked,match=reason):adapter_for(job).navigation(job)


@pytest.mark.parametrize('path',[
    '/en-US/AnotherSite/job/US-CA/Software_JR12345',
    '/en-US/NVIDIAExternalCareerSite/job/../Software_JR12345',
    '/en-US/NVIDIAExternalCareerSite/job/%2e%2e/Software_JR12345',
    '/en-US/NVIDIAExternalCareerSite/job/US-CA/Software_JR12345?redirect=register',
    '/en-US/NVIDIAExternalCareerSite/job/US-CA/Software_JR12345#register',
    '/en-US/NVIDIAExternalCareerSite/login',
])
def test_unrecognized_paths_do_not_make_employer_requests(monkeypatch,path):
    job,_,_=fixture(WORKDAY[0]);job['url']='https://'+job['host']+path
    def forbidden(*a,**kw):raise AssertionError('Unexpected network access')
    monkeypatch.setattr('hireme.net.Network.json',forbidden)
    with pytest.raises(Blocked,match='posting_inspection_review'):adapter_for(job).navigation(job)


@pytest.mark.parametrize('failure,reason',[
    (HTTPError('https://example.invalid',503,'unavailable',None,None),'posting_fetch_failed'),
    (HTTPError('https://example.invalid',404,'missing',None,None),'posting_inspection_review'),
    (URLError(TimeoutError()),'posting_fetch_failed'),
    (URLError(ssl.SSLCertVerificationError()),'posting_inspection_review'),
    (ValueError('Invalid JSON'),'posting_inspection_review'),
    (Blocked('paused'),'paused'),
])
def test_read_failures_preserve_cancellation_and_retry_policy(monkeypatch,failure,reason):
    job,_,_=fixture(WORKDAY[0])
    def fail(*a,**kw):raise failure
    monkeypatch.setattr('hireme.net.Network.json',fail)
    with pytest.raises(Blocked,match=reason):adapter_for(job).navigation(job)


def test_browser_routes_workday_inspection_through_worker_controls(store,monkeypatch):
    from hireme.browser import Browser
    from hireme.util import digest
    job,_,_=fixture(WORKDAY[0]);job.update(id=digest(job['url']),company='Nvidia',location='San Francisco, United States',source='fixture')
    store.upsert_job(job);store.run_deadline=123456789.0
    called=[]
    class Inspection:
        def navigation(self,posting,*,checkpoint=None,deadline=None):
            assert posting==job and checkpoint.__self__ is store and deadline==store.run_deadline
            called.append(True)
            raise Blocked('posting_unavailable_review')
    monkeypatch.setattr('hireme.ats_adapters.adapter_for',lambda posting:Inspection())
    with Browser(store) as browser:
        with pytest.raises(Blocked,match='posting_unavailable_review'):browser.apply(job)
    assert called==[True] and not store.db.execute('SELECT 1 FROM applications').fetchone()
