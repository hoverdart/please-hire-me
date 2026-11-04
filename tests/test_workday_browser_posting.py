from types import SimpleNamespace

import pytest

from hireme.browser import Browser
from hireme.browser_agent import AdaptiveBrowser
from hireme.util import Blocked,digest
from hireme.workday_browser_posting import inspect_posting
from hireme.workday_site import asset_read

URL='https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Synthetic_JR12345'
TITLE='Software Engineer Intern Summer 2027'
DESCRIPTION='Build Python and TypeScript software in San Francisco, United States.'
HTML=f'''<h1>{TITLE}</h1><div data-automation-id=jobPostingDescription>{DESCRIPTION}</div><p>JR12345</p>
    <form><label>Full name<input name=name required></label><label>Email<input name=email type=email required></label>
    <label>Resume<input type=file required></label><button>Submit application</button></form>'''


@pytest.fixture
def posting_page(store):
    with Browser(store) as browser:
        browser.page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=HTML))
        browser.page.goto(URL)
        yield browser.page


def test_rendered_workday_identity_verifies_without_a_separate_http_api(posting_page):
    assert inspect_posting(posting_page,{'url':URL,'title':TITLE},lambda:None)==DESCRIPTION


@pytest.mark.parametrize('change,reason',[
    ('title','posting_changed_review'),('req','posting_inspection_review'),('expired','expired_posting')])
def test_changed_or_expired_rendered_posting_does_not_authorize_application(posting_page,change,reason):
    if change=='title':posting_page.locator('h1').evaluate('e=>e.textContent="Different job"')
    elif change=='req':posting_page.locator('p').evaluate('e=>e.textContent="JR99999"')
    else:posting_page.set_content("<h1>Careers</h1><p>The page you are looking for doesn't exist.</p>")
    with pytest.raises(Blocked,match=reason):inspect_posting(posting_page,{'url':URL,'title':TITLE},lambda:None)


@pytest.mark.parametrize('tailored',[False,True])
def test_adaptive_workday_prepares_when_native_http_read_is_unavailable(store,monkeypatch,tailored):
    job={'id':digest(URL),'url':URL,'title':TITLE,'host':'nvidia.wd5.myworkdayjobs.com',
        'company':'Nvidia','location':'San Francisco, United States','source':'fixture','description':'Previous discovery summary: Python and TypeScript software.'}
    store.upsert_job(job)
    if tailored:
        store.put_history_entry('employment',{'organization':'Synthetic Employer','title':'Software Intern',
            'description':'Built Python software and tested TypeScript interfaces.'},source='Confirmed synthetic history',confirmed=True)
        store.update_settings({'tailored_resumes':True})
    def forbidden(*args,**kwargs):raise AssertionError('Adaptive Workday must use its rendered posting, not urllib')
    monkeypatch.setattr('hireme.net.Network.json',forbidden)
    monkeypatch.setattr('hireme.browser_identity.verify_workday_account',lambda agent:False)
    with AdaptiveBrowser(store) as browser:
        browser.page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=HTML))
        assert browser.apply(job,live=False)=='prepared'
        assert browser.page.locator('input[type=file]').evaluate('e=>e.files.length')==0
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


@pytest.mark.parametrize('resource_type',['script','fetch','xhr'])
def test_same_shard_workday_translation_assets_can_load(resource_type):
    request=SimpleNamespace(method='GET',url='https://wd5.myworkdaycdn.com/wday/asset/candidate-experience-jobs/compiled-lang/generic/en-US.json',resource_type=resource_type)
    assert asset_read('nvidia.wd5.myworkdayjobs.com',request)


@pytest.mark.parametrize('url,method,resource_type',[
    ('https://wd12.myworkdaycdn.com/wday/asset/generic/en-US.json','GET','fetch'),
    ('https://wd5.myworkdaycdn.com/wday/cxs/account','GET','fetch'),
    ('https://wd5.myworkdaycdn.com/wday/asset/../account','GET','fetch'),
    ('https://wd5.myworkdaycdn.com/wday/asset/generic/en-US.json','POST','fetch'),
    ('https://wd5.myworkdaycdn.com/wday/asset/generic/en-US.json','GET','document'),
    ('https://wd5.myworkdaycdn.com.attacker.invalid/wday/asset/en-US.json','GET','fetch'),
])
def test_asset_reads_do_not_grant_other_shards_or_writes(url,method,resource_type):
    assert not asset_read('nvidia.wd5.myworkdayjobs.com',SimpleNamespace(url=url,method=method,resource_type=resource_type))


def test_real_browser_self_read_carries_the_sites_csrf_token(posting_page,store):
    from hireme.browser_identity import verify_workday_account
    from hireme.workday_profiles import PROFILE_READ
    posting_page.evaluate('''()=>window.workday={tenant:'nvidia',siteId:'NVIDIAExternalCareerSite',token:'synthetic-csrf'}''')
    reads=[]
    def profile(route):
        reads.append((route.request.method,route.request.headers.get('x-calypso-csrf-token')))
        route.fulfill(status=200,json={'emailAddress':store.facts()['email']['value']})
    posting_page.route(PROFILE_READ,profile)
    assert verify_workday_account(SimpleNamespace(page=posting_page,job={'url':URL},store=store))
    assert reads==[('GET','synthetic-csrf')]


def test_sign_in_provider_choice_is_an_account_hold_before_password_fields(store):
    from hireme.browser_agent_protocol import origin
    with AdaptiveBrowser(store) as browser:
        browser.page.set_content('<h1>Sign In</h1><button>Sign in with email</button>')
        browser.allowed_origin=origin(browser.page.url);browser.denied_write=False;browser.owner_verified=False;browser.preparing=True
        with pytest.raises(Blocked,match='account_blocked'):browser._check()
