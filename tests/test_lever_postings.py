"""Fresh official posting evidence stays separate from the Lever application form."""
from urllib.error import HTTPError,URLError
import pytest
from hireme.ats_adapters import adapter_for,LeverPostingAdapter
from hireme.util import Blocked


def posting(host='jobs.lever.co'):
    req='12345678-1234-1234-1234-123456789abc'
    url=f'https://{host}/acme/{req}'
    job={'host':host,'url':url+'/apply','title':'Software Engineer Intern Summer 2027','description':'Old cached description'}
    data={'id':req,'text':job['title'],'hostedUrl':url,'applyUrl':job['url'],
          'descriptionPlain':'Our mission is to build manufacturing software.',
          'lists':[{'text':'Requirements','content':'<ul><li>Currently pursuing a degree.</li></ul>'}],
          'additionalPlain':'Summer availability required.'}
    return job,data


@pytest.mark.parametrize('host',['jobs.lever.co','jobs.eu.lever.co'])
def test_lever_apply_page_preserves_verified_full_posting(monkeypatch,host):
    job,data=posting(host);calls=[];checks=[]
    class Read:
        def __init__(self,checkpoint=None,deadline=None):assert deadline==123;checkpoint()
        def json(self,url,payload=None):calls.append((url,payload));return data
    monkeypatch.setattr('hireme.net.Network',Read)
    adapter=adapter_for(job);assert isinstance(adapter,LeverPostingAdapter)
    url,text=adapter.navigation(job,checkpoint=lambda:checks.append(True),deadline=123)
    api='api.eu.lever.co' if host=='jobs.eu.lever.co' else 'api.lever.co'
    assert calls==[(f'https://{api}/v0/postings/acme/{data["id"]}?mode=json',None)] and len(checks)==2
    assert url==job['url'] and 'manufacturing software' in text and 'Requirements' in text and 'Currently pursuing' in text
    assert '<li>' not in text and adapter.posting_text('Title\nLocation\nApply for this job\nMilitary status?',text)==text


@pytest.mark.parametrize('changes,reason',[
    ({'id':'another-id'},'posting_inspection_review'),
    ({'text':'A different role'},'posting_changed_review'),
    ({'hostedUrl':'https://jobs.lever.co/another/12345678-1234-1234-1234-123456789abc'},'posting_changed_review'),
    ({'applyUrl':'https://evil.invalid/acme/12345678-1234-1234-1234-123456789abc/apply'},'posting_changed_review'),
    ({'descriptionPlain':''},'posting_inspection_review'),
    ({'descriptionPlain':None},'posting_inspection_review'),
    ({'lists':[{'text':'Requirements'}]},'posting_inspection_review'),
])
def test_unverified_lever_posting_never_falls_back_to_cached_text(monkeypatch,changes,reason):
    job,data=posting();data.update(changes)
    monkeypatch.setattr('hireme.net.Network.json',lambda *a:data)
    with pytest.raises(Blocked,match=reason):adapter_for(job).navigation(job)


@pytest.mark.parametrize('error,reason',[
    (HTTPError('https://api.lever.co',404,'missing',{},None),'expired_posting'),
    (HTTPError('https://api.lever.co',503,'unavailable',{},None),'posting_fetch_failed'),
    (URLError('network failure'),'posting_fetch_failed'),
])
def test_lever_read_failures_remain_non_submission_events(monkeypatch,error,reason):
    job,_=posting()
    def fail(*a):raise error
    monkeypatch.setattr('hireme.net.Network.json',fail)
    with pytest.raises(Blocked,match=reason):adapter_for(job).navigation(job)


def test_lever_read_requires_the_expected_public_host_and_requisition(monkeypatch):
    job,_=posting();calls=[]
    monkeypatch.setattr('hireme.net.Network.json',lambda *a:calls.append(a))
    for url in ['http://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc',
                'https://127.0.0.1/acme/12345678-1234-1234-1234-123456789abc',
                'https://jobs.lever.co/acme/not-a-posting']:
        with pytest.raises(Blocked,match='posting_inspection_review'):adapter_for(job).navigation({**job,'url':url})
    assert not calls
