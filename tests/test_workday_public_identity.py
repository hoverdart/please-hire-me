import copy

import pytest

from hireme.util import Blocked
from hireme.workday_browser_posting import public_identity,verified_posting
from hireme.workday_browser_receipt import received_record

URL='https://synthetic.wd5.myworkdayjobs.com/External/job/California/Compiler-Intern_R123-2'
JOB={'url':URL,'title':'Old listing title'}
INFO={'jobPostingInfo':{'externalUrl':URL,'jobPostingId':'Compiler-Intern_R123-2',
    'jobPostingSiteId':'External','jobReqId':'R123','title':'Compiler Intern',
    'posted':True,'canApply':True}}


def test_official_identity_resolves_title_and_requisition_variant():
    identity=public_identity(JOB,INFO)
    assert identity['requisition_id']=='R123' and identity['title']=='Compiler Intern'
    job={**JOB,'title':identity['title'],'_workday_public_identity':identity}
    application={'applicationId':'remote','jobPostingAnchorId':'Compiler-Intern_R123-2',
        'jobRequisitionId':'R123','postingTitle':'Compiler Intern','draft':False,'active':True,
        'applicationType':'EXTERNAL','status':'Application Received','dateApplied':'November 15, 2026'}
    assert received_record(job,'remote',{'application':application})
    application['jobPostingAnchorId']='Compiler-Intern_R123'
    assert not received_record(job,'remote',{'application':application})


@pytest.mark.parametrize('field,value',[
    ('externalUrl',URL.replace('R123-2','R123-3')),('jobReqId','R12'),
    ('jobPostingSiteId','Other'),('jobPostingId','Compiler-Intern_R123'),
    ('posted',False),('canApply',False),('title','<script>')])
def test_official_identity_does_not_allow_unrelated_or_unavailable_posting(field,value):
    data=copy.deepcopy(INFO);data['jobPostingInfo'][field]=value
    assert public_identity(JOB,data) is None


@pytest.mark.parametrize('result,reason',[
    ({'status':404,'data':None},'expired_posting'),
    ({'status':410,'data':None},'expired_posting'),
    ({'status':200,'data':{'jobPostingInfo':{'posted':False}}},'expired_posting'),
    ({'status':200,'data':{'jobPostingInfo':{'canApply':False}}},'expired_posting'),
    ({'status':200,'data':{'jobPostingInfo':{'title':'Other'}}},'posting_changed_review'),
])
def test_explicit_api_failure_never_falls_back_to_stale_dom(monkeypatch,result,reason):
    from types import SimpleNamespace
    page=SimpleNamespace(evaluate=lambda *args:result)
    monkeypatch.setattr('hireme.workday_browser_posting.inspect_posting',lambda *a,**k:pytest.fail('Stale DOM fallback'))
    with pytest.raises(Blocked,match=reason):verified_posting(page,JOB,lambda:None)


def test_verified_official_record_refreshes_canonical_title_before_dom_check(monkeypatch):
    from types import SimpleNamespace
    page=SimpleNamespace(evaluate=lambda *args:{'status':200,'data':INFO})
    def inspect(page,job,checkpoint,identity):
        assert job['title']=='Compiler Intern' and identity==public_identity(JOB,INFO)
        return 'Official description'
    monkeypatch.setattr('hireme.workday_browser_posting.inspect_posting',inspect)
    current=verified_posting(page,JOB,lambda:None)
    assert current['description']=='Official description' and JOB['title']=='Old listing title'
