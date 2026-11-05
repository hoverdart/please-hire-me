"""Authority, identity, routing and read classification for adaptive UI actions."""
import json
from types import SimpleNamespace

import pytest

from hireme.browser_agent_protocol import read_request,response_ok,explicit_acceptance,looks_final
from hireme.browser_dispatch import use_adaptive,can_fallback
from hireme.browser_identity import account_email,verify_email,verify_workday_account
from hireme.util import Blocked,digest
from hireme.workday_site import site_for_url,is_workday_url


HOST='synthetic.wd12.myworkdayjobs.com'
URL=f'https://{HOST}/en-US/External/job/California/Software-Engineer_JR123'


def request(path,body=None,method='POST',headers=None):
    return SimpleNamespace(url=f'https://{HOST}'+path,method=method,headers=headers or {},
        post_data_buffer=json.dumps(body).encode() if body is not None else b'')


@pytest.mark.parametrize('resource',['name','address','workexperiences','educations','resumeattachments','selfidentify'])
@pytest.mark.parametrize('prefix',['/wday/cxs','/wday/calypso/cxs/jobapplication'])
def test_workday_definition_reads_are_distinct_from_saves(resource,prefix):
    path=f'{prefix}/synthetic/jobapplication/abc-123/{resource}'
    assert read_request(request(path,{},headers={'form-definition':'true'}))
    assert not read_request(request(path,{}))
    assert not read_request(request(path,{'answer':'private'},headers={'form-definition':'true'}))
    assert not read_request(request(path,{},method='DELETE',headers={'form-definition':'true'}))


@pytest.mark.parametrize('resource,data',[
    ('source',{'source':{'id':'source-id','descriptor':'Company Website','ariaLabel':'Company Website not checked',
        'ariaLabelSelected':'selected','ariaLabelUnselected':'not selected','index':0,'isRTL':False,'isSelected':False}}),
    ('emailaddress',{'emailAddress':'test@candidate.invalid'}),
    ('phonenumber',{'descriptor':'','countryPhoneCode':{'id':'code-id','descriptor':'United States (+1)'},'extension':'','phoneNumber':''}),
    ('previousworker',{'currentlyWorkHere':False}),
])
def test_contextual_workday_definition_reads_require_native_header_and_exact_schema(resource,data):
    path=f'/wday/calypso/cxs/jobapplication/synthetic/jobapplication/abc-123/{resource}'
    assert read_request(request(path,data,headers={'form-definition':'true'}))
    assert not read_request(request(path,data))
    assert not read_request(request(path,{**data,'submit':True},headers={'form-definition':'true'}))
    assert not read_request(request(path+'?submit=true',data,headers={'form-definition':'true'}))
    assert not read_request(request(path.replace('/jobapplication/abc-123/','/profile/abc-123/'),data,headers={'form-definition':'true'}))


@pytest.mark.parametrize('source',[
    {'id':'source-id','descriptor':'Website','isSelected':'yes'},
    {'id':'source-id','descriptor':'Website','index':True},
    {'id':'source-id','descriptor':'Website','unexpected':'value'},
    {'id':'source-id','descriptor':'x'*2001},
    {'id':'source-id'},
    ['source-id'],
])
def test_unrecognized_workday_prompt_context_is_not_a_read(source):
    path='/wday/calypso/cxs/jobapplication/synthetic/jobapplication/abc-123/source'
    assert not read_request(request(path,{'source':source},headers={'form-definition':'true'}))


@pytest.mark.parametrize('resource,data',[
    ('source',{'source':{'id':'a','descriptor':'Website'},'sourcePopulatedFromUrl':False}),
    ('phonenumber',{'id':'b','countryPhoneCode':{'id':'a','descriptor':'United States (+1)'},'phoneNumber':'5551234567','phoneType':{'id':'c','descriptor':'Mobile'}}),
    ('previousworker',{'id':'a','candidateIsPreviousWorker':False,'manager':None,'email':None,'employeeID':None,'location':None}),
    ('workexperiences',{'currentlyWorkHere':True}),
    ('socials',{'facebookAccount':'','linkedInAccount':'','twitterAccount':''}),
    ('skills',{'skills':None}),
    ('previousworker',{'id':'record','candidateIsPreviousWorker':False}),
    ('uspersonalinfo',{'ethnicity':{'id':'e','descriptor':'Fixture ethnicity'},'ethnicityMulti':[{'id':'e','descriptor':'Fixture ethnicity'}],
        'gender':{'id':'g','descriptor':'Fixture gender'},'veteranStatus':{'id':'v','descriptor':'Fixture veteran'}}),
    ('selfidentify',{'id':'record','disabilityForm':{'id':'form'},'name':'','employeeId':'','dateSignedOn':'','disabilityStatus':{'id':''}}),
])
def test_saved_workday_context_definitions_remain_exact_reads(resource,data):
    path='/wday/calypso/cxs/jobapplication/synthetic/jobapplication/abc-123/'+resource
    assert read_request(request(path,data,headers={'form-definition':'true'}))
    assert not read_request(request(path,data))
    assert not read_request(request(path,{**data,'save':True},headers={'form-definition':'true'}))


@pytest.mark.parametrize('method',['POST','PUT'])
@pytest.mark.parametrize('resource,data',[
    ('termsandconditions',{'acceptTermsAndAgreements':True}),
    ('uspersonalinfo',{'id':'record','veteranStatus':{'id':'v','descriptor':'Fixture veteran response'},
        'gender':{'id':'g','descriptor':'Fixture gender response'},'ethnicity':{'id':'e','descriptor':'Fixture ethnicity response'}}),
    ('resumeattachments',{'id':'resume-group','attachments':[{'id':'attachment','descriptor':'approved.pdf',
        'fileName':'approved.pdf','fileLength':1024,'contentType':{'id':'pdf','descriptor':'application/pdf'},'file':'opaque-native-handle'}]}),
])
def test_workday_saved_disclosure_definitions_do_not_replay_identical_saves(resource,data,method):
    path='/wday/cxs/synthetic/jobapplication/abc-123/'+resource
    assert read_request(request(path,data,method,{'form-definition':'true'}))
    assert not read_request(request(path,data,method))
    assert not read_request(request(path,{**data,'finalize':True},method,{'form-definition':'true'}))


@pytest.mark.parametrize('resource,data',[
    ('termsandconditions',{'acceptTermsAndAgreements':1}),
    ('uspersonalinfo',{'id':'record','veteranStatus':{},'gender':{},'ethnicity':{}}),
    ('resumeattachments',{'attachments':'not-a-list'}),
    ('resumeattachments',{'attachments':[{'file':'unreviewed-bytes'}]}),
])
def test_unknown_saved_disclosure_definition_shapes_are_not_reads(resource,data):
    path='/wday/cxs/synthetic/jobapplication/abc-123/'+resource
    assert not read_request(request(path,data,headers={'form-definition':'true'}))


def test_ui_operations_cannot_delete_saved_records(store,job,package):
    from hireme.browser_agent_protocol import UIOperation
    store.update_settings({'remote_drafts':True})
    agent=SimpleNamespace(store=store,job=job,aid=None,preparing=False)
    operation=UIOperation(agent,{'kind':'next','id':'next-control'},package)
    assert not operation.allow(request('/jobapplication/owned/workexperiences/record',method='DELETE'))
    assert operation.record is None and not operation.requests


@pytest.mark.parametrize('data,allowed',[
    ({'query':'query CurrentApplication { me { id } }'},True),
    ({'query':'mutation SubmitApplication { submit { id } }'},False),
    ({'query':'query Read { me { id } } mutation Submit { submit { id } }'},False),
    ({'operationName':'query','answer':'private'},False),
    ([],False),
])
def test_graphql_reads_require_an_actual_query(data,allowed):
    assert read_request(request('/graphql',data)) is allowed


@pytest.mark.parametrize('payload',[
    {'errors':['invalid']},{'data':{'success':False}},{'validationErrors':{'email':'invalid'}},
    {'result':[{'status':'failed'}]},{'ok':False},
])
def test_error_envelopes_cannot_acknowledge_a_save_or_submission(payload):
    assert not response_ok(payload) and not explicit_acceptance(payload)


def test_successful_draft_save_is_not_a_final_submission_receipt():
    assert response_ok({'saved':True}) and not explicit_acceptance({'saved':True})
    assert explicit_acceptance({'data':{'applicationStatus':'submitted'}})
    assert looks_final(request('/jobapplication/abc/finalize',{}))
    assert looks_final(request('/graphql',{'operationName':'SubmitApplication'}))


def test_dynamic_workday_sites_are_bound_to_exact_public_postings():
    site=site_for_url(URL)
    assert (site.host,site.tenant,site.shard,site.site,site.path)==(
        HOST,'synthetic','wd12','External','/job/California/Software-Engineer_JR123')
    assert is_workday_url(URL.replace('en-US','fr-FR'))


@pytest.mark.parametrize('url',[
    URL.replace('https:','http:'),URL+'?redirect=somewhere',URL+'#apply',
    URL.replace(HOST,'synthetic.myworkdayjobs.com'),URL.replace(HOST,HOST+'.attacker.invalid'),
    URL.replace(HOST,'user@'+HOST),URL.replace('/California/','/../'),
    URL.replace('/California/','/%2e%2e/'),URL.replace('/job/California/Software-Engineer_JR123','/login'),
    URL.replace(HOST,HOST+':8443'),
])
def test_malformed_or_unbound_workday_links_are_not_admitted(url):
    assert not is_workday_url(url)


def test_email_identity_is_confirmed_and_not_inferred_from_a_signed_in_flag(store):
    assert account_email({'data':{'userProfile':{'emailAddress':'TEST@candidate.invalid'}}})=='test@candidate.invalid'
    assert account_email({'errors':['denied'],'email':'test@candidate.invalid'}) is None
    assert verify_email(store,'test@candidate.invalid')
    with pytest.raises(Blocked,match='browser_identity_mismatch'):verify_email(store,'other@candidate.invalid')


@pytest.mark.parametrize('status,payload,result',[
    (401,{},False),(302,{},False),(200,{},False),
    (200,{'emailAddress':'test@candidate.invalid'},True),
])
def test_workday_identity_uses_only_the_exact_authenticated_profile_get(store,status,payload,result):
    calls=[]
    def get(url,**kwargs):
        calls.append((url,kwargs));return SimpleNamespace(status=status,json=lambda:payload)
    agent=SimpleNamespace(store=store,job={'url':URL},context=SimpleNamespace(request=SimpleNamespace(get=get)))
    assert verify_workday_account(agent) is result
    assert calls==[(f'https://{HOST}/wday/calypso/cxs/candidatehome/synthetic/External/userprofile',{'max_redirects':0,'timeout':10000})]


def test_adaptive_browser_routes_fresh_workday_and_respects_disable(store):
    job={'id':'synthetic','host':HOST,'url':URL}
    assert use_adaptive(store,job)
    assert not use_adaptive(store,{'host':'jobs.lever.co'})
    store.update_settings({'adaptive_browser':False})
    assert not use_adaptive(store,job)


def test_native_fallback_is_only_available_before_a_side_effect_or_attempt(store):
    job={'id':'synthetic','host':'jobs.lever.co'}
    assert can_fallback(store,job,SimpleNamespace(attempted=False),Blocked('unsupported_widget'))
    assert not can_fallback(store,job,SimpleNamespace(attempted=True),Blocked('unsupported_widget'))
    assert not can_fallback(store,job,SimpleNamespace(attempted=False),Blocked('upload_verification_failed'))


def test_login_completion_releases_an_account_hold_without_claiming_account_verification(store):
    from hireme.browser_login import login_completed
    from hireme.job_holds import dependency
    job={'id':'synthetic','url':URL,'host':HOST,'company':'Synthetic','title':'Engineer','source':'fixture'}
    store.upsert_job(job)
    before=dependency(store,job,'account','browser_identity_unverified')
    login_completed(store,HOST)
    assert before!=dependency(store,job,'account','browser_identity_unverified')
    assert HOST in store.settings()['signed_in_portals']
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


def test_login_only_accepts_a_saved_trusted_posting(store,monkeypatch):
    from hireme.browser_login import saved_destination
    monkeypatch.setattr('hireme.browser_login.public_host',lambda host:True)
    job={'id':digest(URL),'url':URL,'host':HOST,'company':'Synthetic','title':'Engineer','source':'fixture'}
    store.upsert_job(job)
    assert saved_destination(store,job['id'])['url']==URL
    with pytest.raises(ValueError):saved_destination(store,'unknown-job')
    job['url']='https://attacker.invalid/sign-in';job['id']='untrusted';store.upsert_job(job)
    with pytest.raises(ValueError):saved_destination(store,job['id'])


def test_observed_platform_identity_reads_do_not_admit_profile_writes():
    from hireme.browser_identity import identity_read
    assert identity_read(request('/hs/graphql',{'operationName':'GetCurrentUser','query':'query GetCurrentUser { currentUser { email } }'}),'handshake')
    assert not identity_read(request('/hs/graphql',{'operationName':'GetCurrentUser','query':'mutation GetCurrentUser { updateProfile { email } }'}),'handshake')
    assert identity_read(request('/api/me',method='GET'),'workatastartup')
    assert not identity_read(request('/api/me',{'email':'other@candidate.invalid'},method='PATCH'),'workatastartup')


def test_application_actions_cannot_authorize_global_profile_or_account_changes():
    from hireme.browser_agent_protocol import unrelated_write
    assert unrelated_write(request('/api/profile',{'email':'other@candidate.invalid'}))
    assert unrelated_write(request('/hs/graphql',{'operationName':'UpdateProfile','query':'mutation UpdateProfile { updateProfile { id } }'}))
    assert unrelated_write(request('/api/register',{}))
    assert not unrelated_write(request('/wday/calypso/cxs/jobapplication/synthetic/jobapplication/abc/name',{'name':'Approved Name'}))


def test_checkpoint_url_keeps_draft_identity_and_drops_tracking_and_tokens():
    from hireme.browser_agent import _clean_url
    assert _clean_url('https://employer.invalid/apply?source=email&applicationId=abc-123&token=private#review')==(
        'https://employer.invalid/apply?applicationId=abc-123')


def test_connected_dispatch_closes_native_context_before_safe_ui_fallback(store,job,monkeypatch):
    from hireme.platform_browser import ConnectedBrowser
    calls=[]
    class Native:
        attempted=False
        def __init__(self,store):pass
        def __enter__(self):calls.append('native-open');return self
        def __exit__(self,*args):calls.append('native-close')
        def apply(self,job,live=True):raise Blocked('unsupported_widget')
    class Adaptive(Native):
        def __init__(self,store,**kwargs):pass
        def __enter__(self):calls.append('ui-open');return self
        def __exit__(self,*args):calls.append('ui-close')
        def apply(self,job,live=True):assert not live;return 'prepared'
    monkeypatch.setattr('hireme.platform_browser.Browser',Native)
    monkeypatch.setattr('hireme.browser_agent.AdaptiveBrowser',Adaptive)
    with ConnectedBrowser(store) as browser:assert browser.apply(job,live=False)=='prepared'
    assert calls==['native-open','native-close','ui-open','ui-close']


@pytest.mark.parametrize('connection,receipt',[
    ('workatastartup','Your introduction has been sent.'),('workatastartup','Your message has been sent.'),
    ('handshake','Your application has been received.'),
])
def test_receipts_are_fresh_and_specific_to_the_platform(store,connection,receipt):
    from hireme.browser_agent import AdaptiveBrowser
    agent=AdaptiveBrowser(store,connection_id=connection)
    assert agent._receipt(receipt) and not agent._receipt(receipt,receipt)
    assert not agent._receipt('Thank you for visiting. Your application has not been submitted.')
