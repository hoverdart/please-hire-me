"""Metadata-only listings need employer evidence, not optimistic fit points."""
from pathlib import Path

import pytest

from hireme.browser import Browser
from hireme.job_holds import ready
from hireme.policy import eligible,listing_scope
from hireme.posting_inspection import inspect,needs_details
from hireme.util import Blocked,digest
from hireme.worker import cycle


@pytest.mark.parametrize('description',['','Sponsorship: Yes','Sponsorship: No'])
def test_metadata_is_not_a_low_fit_verdict_or_application_authority(store,job,description):
    item={**job,'description':description}
    assert needs_details(item)
    with pytest.raises(Blocked,match='posting_details_required'):eligible(item,store.settings(),store.facts())


def test_aggregator_terms_only_authorize_reading_details_not_submitting(store,job):
    store.update_settings({'seniority':['summer-internship']})
    item={**job,'title':'Software Engineering Intern','description':'','terms':['Summer 2027']}
    listing_scope(item,store.settings(),metadata=True)
    with pytest.raises(Blocked,match='internship_out_of_scope'):eligible(item,store.settings(),store.facts())


def test_verified_api_details_need_no_browser_or_model(store,job,monkeypatch):
    class Adapter:
        def navigation(self,*args,**kwargs):return job['url'],job['description']
    monkeypatch.setattr('hireme.ats_adapters.adapter_for',lambda item:Adapter())
    def forbidden(*args,**kwargs):pytest.fail('Verified API text should not launch a browser')
    monkeypatch.setattr('hireme.browser.Browser',forbidden)
    result=inspect(store,{**job,'description':''})
    assert result['description']==job['description'] and result['_employer_posting_read']
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_anonymous_public_browser_never_uses_applicant_profile_or_sends_mutations(store,job,monkeypatch):
    class Adapter:
        def navigation(self,*args,**kwargs):return job['url'],None
        def posting_text(self,text):return text
    monkeypatch.setattr('hireme.ats_adapters.adapter_for',lambda item:Adapter())
    profile=store.root/'browser';profile.mkdir();sentinel=profile/'private-session-sentinel';sentinel.write_text('untouched')
    blocked=[];writes=[]
    class FixtureBrowser(Browser):
        def __enter__(self):
            assert self.store.settings()['headless'] is True
            assert Path(self.profile_directory)!=profile
            super().__enter__()
            self.context.on('requestfailed',lambda request:blocked.append(request.method) if request.method=='POST' else None)
            return self
        def _route(self,route):
            if route.request.method!='GET':writes.append(route.request.method);return route.abort()
            route.fulfill(status=200,content_type='text/html',body=f'''<h1>{job['title']}</h1><p>Build Python and TypeScript software.</p>
                <script>fetch('/save',{{method:'POST',body:'{{}}'}}).catch(()=>{{}})</script>''')
        def __exit__(self,*args):
            self.page.wait_for_timeout(150)
            return super().__exit__(*args)
    monkeypatch.setattr('hireme.browser.Browser',FixtureBrowser)
    result=inspect(store,{**job,'description':''})
    assert 'Python' in result['description']
    assert blocked==['POST'] and not writes
    assert sentinel.read_text()=='untouched' and list(profile.iterdir())==[sentinel]
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_sparse_aggregator_refresh_cannot_erase_employer_details(store,job):
    verified={**job,'source':'simplify:internships','_employer_posting_read':'2026-01-01'}
    store.upsert_job(verified)
    store.upsert_job({**job,'source':'simplify:internships','description':'Sponsorship: Unknown'})
    import json
    current=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
    assert current['description']==job['description'] and not needs_details(current)


def test_bounded_detail_reads_finish_before_application_screening_and_do_not_count_as_attempts(store,job,monkeypatch):
    reads=[]
    for i in range(3):
        item={**job,'id':digest(i),'url':job['url']+str(i),'description':''}
        store.upsert_job(item)
    def read(s,item):reads.append(item['id']);return {**item,'description':job['description'],'_employer_posting_read':'test'}
    monkeypatch.setattr('hireme.posting_inspection.inspect',read)
    class Application:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,item,live=True):assert not needs_details(item);return 'prepared'
    result=cycle(store,Path.cwd(),discover=False,live=False,browser_factory=Application,max_attempts=1)
    assert len(reads)==1 and result['attempts']==1 and result['prepared']==1
    assert store.db.execute('SELECT count(*) FROM jobs WHERE reason LIKE ?',('low_fit%',)).fetchone()[0]==0


def test_failed_details_read_is_not_an_application_or_model_attempt(store,job,monkeypatch):
    store.upsert_job({**job,'description':''})
    def failure(*args):raise Blocked('posting_fetch_failed')
    monkeypatch.setattr('hireme.posting_inspection.inspect',failure)
    class Forbidden:
        def __init__(self,*args):pytest.fail('No complete posting was eligible')
    result=cycle(store,Path.cwd(),discover=False,browser_factory=Forbidden)
    assert result['attempts']==0 and result['confirmed']==0
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    assert not ready(store,job)


def test_zero_attempt_screening_does_not_open_a_detail_browser(store,job,monkeypatch):
    store.upsert_job({**job,'description':''})
    def forbidden(*args):pytest.fail('A zero-attempt local screening requested an employer read')
    monkeypatch.setattr('hireme.posting_inspection.inspect',forbidden)
    result=cycle(store,Path.cwd(),discover=False,live=False,limit=0)
    assert result['attempts']==0
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
