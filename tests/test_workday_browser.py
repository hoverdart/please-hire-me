import pytest
from hireme.browser import Browser
from hireme.answers import resolve
from hireme.util import Blocked
from hireme.portals import WORKDAY


def test_workday_repeated_sections_keep_date_identity(store,job):
    html="""<main>
    <div data-automation-id="education"><label for="ed1">End date year</label><input id="ed1" type="number" required></div>
    <div data-automation-id="education"><label for="ed2">End date year</label><input id="ed2" type="number" required></div>
    <div data-automation-id="workExperience"><label for="employment">Start date month</label><input id="employment" required></div>
    </main>"""
    with Browser(store,test_url='http://fixture.invalid') as b:
        b.page.set_content(html)
        fields=b._snapshot()
        assert [(f['section'],f.get('section_entry')) for f in fields]==[('education',0),('education',1),('employment',None)]
        assert resolve(store,job['host'],fields[0],context=job)['value']=='2028'
        with pytest.raises(Blocked,match='repeated_entry_review'):resolve(store,job['host'],fields[1],context=job)
        with pytest.raises(Blocked):resolve(store,job['host'],fields[2],context=job)
        assert b.page.locator('#employment').input_value()==''
        assert store.db.execute('SELECT count(*) FROM remote_draft_steps').fetchone()[0]==0


@pytest.mark.parametrize('tenant,shard',[
    ('nvidia.wd5.myworkdayjobs.com','wd5'),('salesforce.wd12.myworkdayjobs.com','wd12'),
    ('intel.wd1.myworkdayjobs.com','wd1'),('arrowstreetcapital.wd5.myworkdayjobs.com','wd5'),
    ('gresearch.wd103.myworkdayjobs.com','wd103'),
])
def test_workday_static_assets_are_scoped_reads_without_account_or_draft_authority(store,monkeypatch,tenant,shard):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    browser=Browser(store);browser.current_host=tenant
    class Request:
        def __init__(self,url,method):self.url=url;self.method=method
    class Route:
        def __init__(self,url,method='GET'):self.request=Request(url,method);self.result=None
        def continue_(self):self.result='read'
        def abort(self):self.result='blocked'
    for host in (shard+'.myworkday.com',shard+'.myworkdaycdn.com'):
        asset='https://'+host+'/wday/asset/candidate-experience-jobs/cx-jobs.min.js'
        for method in ('GET','HEAD','OPTIONS'):
            route=Route(asset,method);browser._route(route);assert route.result=='read'
        for url,method in [(asset,'POST'),('https://'+host+'/register','GET'),
                           ('https://'+host+'/wday/asset/../../register','GET'),
                           ('https://'+host+'/wday/asset/%2e%2e/register','GET'),
                           ('https://other.myworkdaycdn.com/wday/asset/client.js','GET')]:
            route=Route(url,method);browser._route(route);assert route.result=='blocked'
    assert not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('company,tenant,shard,site,host',WORKDAY)
def test_workday_public_search_does_not_consume_remote_write_authority(store,monkeypatch,company,tenant,shard,site,host):
    import json
    monkeypatch.setattr('hireme.browser.public_host',lambda value:True)
    browser=Browser(store);browser.current_host=host
    class Grant:
        used=False
        def consume(self,*args):raise AssertionError('A public search cannot consume a draft grant')
    browser.workday_grant=Grant()
    class Request:
        method='POST'
        url=f'https://{host}/wday/cxs/{tenant}/{site}/jobs'
        post_data_buffer=json.dumps({'appliedFacets':{},'limit':20,'offset':0,'searchText':''}).encode()
    class Route:
        request=Request()
        result=None
        def continue_(self):self.result='read'
        def abort(self):self.result='blocked'
    for attempted in (False,True):
        browser.attempted=attempted
        route=Route();browser._route(route)
        assert route.result=='read' and not browser.workday_grant.used and not browser.denied_write
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_workday_search_schema_and_all_ungranted_writes_remain_blocked(store,monkeypatch):
    import json
    from hireme.workday import search_read
    host='nvidia.wd5.myworkdayjobs.com';url=f'https://{host}/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs'
    payload={'appliedFacets':{},'limit':20,'offset':0,'searchText':''}
    for replacement in ({'limit':True},{'limit':0},{'offset':-1},{'appliedFacets':{'unknown':['value']}},
                        {'searchText':'x'*501},{'submit':True}):
        assert not search_read(host,url,'POST',json.dumps({**payload,**replacement}).encode())
    for candidate in (url+'?submit=true',url.replace('/jobs','/register'),url.replace('/nvidia/','/intel/'),
                      url.replace('https://','http://'),url.replace(host,'intel.wd1.myworkdayjobs.com')):
        assert not search_read(host,candidate,'POST',json.dumps(payload).encode())
    assert not search_read(host,url,'PUT',json.dumps(payload).encode())
    assert not search_read(host,url,'POST',b'{invalid')
    monkeypatch.setattr('hireme.browser.public_host',lambda value:True)
    browser=Browser(store);browser.current_host=host
    class Request:
        method='POST';post_data_buffer=b'{}'
        def __init__(self,path):self.url=f'https://{host}{path}'
    class Route:
        result=None
        def __init__(self,path):self.request=Request(path)
        def continue_(self):self.result='read'
        def abort(self):self.result='blocked'
    for attempted in (False,True):
        browser.attempted=attempted
        for path in ('/register','/signin','/draft/save','/applications/submit','/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs'):
            route=Route(path);browser._route(route);assert route.result=='blocked'
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
