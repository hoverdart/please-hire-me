import pytest
from hireme.browser import Browser
from hireme.answers import resolve
from hireme.util import Blocked


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
