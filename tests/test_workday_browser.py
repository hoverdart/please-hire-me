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
