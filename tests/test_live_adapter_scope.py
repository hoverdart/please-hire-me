from pathlib import Path

import pytest

from hireme.browser import Browser
from hireme.util import Blocked,digest


@pytest.mark.parametrize('host,path',[('apply.workable.com','/acme/j/synthetic'),('jobs.smartrecruiters.com','/Acme/synthetic')])
def test_unreviewed_live_adapter_holds_before_navigation_applicant_values_or_intent(store,monkeypatch,job,host,path):
    url='https://'+host+path
    candidate={**job,'url':url,'host':host,'id':digest(url)}
    store.upsert_job(candidate)
    browser=Browser(store)
    with pytest.raises(Blocked,match='unsupported_form'):browser.apply(candidate)
    assert not browser.aid and not browser.attempted and browser.page is None
    assert not browser._native_submission_acknowledged()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_unreviewed_adapter_keeps_local_preparation_without_final_authority(store,monkeypatch,job):
    url='https://apply.workable.com/acme/j/synthetic'
    candidate={**job,'url':url,'host':'apply.workable.com','id':digest(url)}
    store.upsert_job(candidate)
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    html=(Path(__file__).parent/'fixtures/application.html').read_text()
    requests=[]
    with Browser(store) as browser:
        def respond(route):
            requests.append((route.request.method,route.request.url))
            assert route.request.method=='GET' and route.request.url==url
            route.fulfill(status=200,content_type='text/html',body=html)
        browser.page.route('**/*',respond)
        assert browser.apply(candidate,live=False)=='prepared'
        assert not browser._native_submission_acknowledged()
    assert requests==[('GET',url)]
    row=store.db.execute('SELECT state,attempted FROM applications').fetchone()
    assert row['state']=='prepared' and not row['attempted']
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
