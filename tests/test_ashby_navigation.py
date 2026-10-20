import pytest

from hireme.browser import Browser

POSTING='https://jobs.ashbyhq.com/acme/11111111-1111-4111-8111-111111111111'


@pytest.mark.parametrize('target',[
    POSTING+'/application',
    'https://other.invalid/acme/11111111-1111-4111-8111-111111111111/application',
    POSTING.replace('/acme/','/other/')+'/application',
    POSTING+'/application?submit=true',
    POSTING+'/application#submit',
    POSTING.replace('https:','http:')+'/application',
])
def test_ashby_application_tab_must_be_exact_posting_navigation(store,target):
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com'
        b.page.set_content(f'<a role="tab" href="{target}">Application</a><button>Apply for this job</button><button>Apply for this job</button>')
        entry=b._ashby_application_entry({'url':POSTING})
        assert (entry is not None)==(target==POSTING+'/application')
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('html',[
    f'<form><a role="tab" href="{POSTING}/application">Application</a></form>',
    '<button role="tab">Application</button>',
    f'<a role="tab" href="{POSTING}/application">Application</a>'*2,
])
def test_ambiguous_or_form_bound_tabs_do_not_authorize_navigation(store,html):
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.page.set_content(html)
        assert b._ashby_application_entry({'url':POSTING}) is None


@pytest.mark.parametrize('delayed_redirect',[False,True])
def test_verified_tenant_redirect_enters_application_without_changing_durable_job(store,job,monkeypatch,delayed_redirect):
    import json
    from hireme.ashby_drafts import AshbyDraft
    from hireme.util import digest
    REQ="11111111-1111-4111-8111-111111111111"
    def form():return {"id":"22222222-2222-4222-8222-222222222222","sourceFormDefinitionId":"33333333-3333-4333-8333-333333333333","sections":[]}
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    original=f'https://jobs.ashbyhq.com/acme/{REQ}'
    renamed=f'https://jobs.ashbyhq.com/renamed/{REQ}'
    ashby={**job,'id':digest(original),'url':original,'host':'jobs.ashbyhq.com'}
    store.upsert_job(ashby)
    before=dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(ashby['id'],)).fetchone())
    reads=[];writes=[]
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=AshbyDraft(store,ashby)
        if delayed_redirect:
            # Analytics can exhaust the network-idle aid before a client-side
            # tenant rename. The old DOM's Apply link must not establish ready.
            monkeypatch.setattr(b.page,'wait_for_load_state',lambda *a,**kw:None)
        def serve(route):
            if route.request.method=='GET':
                reads.append(route.request.url)
                if route.request.url==original:
                    html='<a href="'+original+'/application">Apply for this job</a><script>setTimeout(()=>location.replace('+json.dumps(renamed)+'),'+('150' if delayed_redirect else '0')+')</script>'
                else:
                    query={'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                           'variables':{'organizationHostedJobsPageName':'renamed','jobPostingId':REQ}}
                    html='<h1>Synthetic posting</h1><a role="tab" href="'+renamed+'/application">Application</a><a href="'+renamed+'/application">Apply for this job</a><script>fetch("/api/non-user-graphql",{method:"POST",body:JSON.stringify('+json.dumps(query)+')})</script>'
                    if route.request.url.endswith('/application'):
                        html+='<script>setTimeout(()=>document.body.insertAdjacentHTML("beforeend",\'<form><label>Name<input name="name"></label></form>\'),150)</script>'
                route.fulfill(status=200,content_type='text/html',body=html)
            else:
                data=json.loads(route.request.post_data);writes.append(data['operationName'])
                assert data['operationName']=='ApiJobPosting'
                route.fulfill(status=200,content_type='application/json',body=json.dumps({'data':{'jobPosting':{'id':REQ,'applicationForm':form()}}}))
        b.page.route('**/*',serve)
        b.page.goto(original);b._wait_ready();b._guard(ashby)
        assert b.page.url==renamed
        entry=b._ashby_application_entry(ashby)
        assert entry is not None
        entry.click();b._wait_ready();b._guard(ashby)
        assert b.page.url==renamed+'/application'
    assert reads==[original,renamed,renamed+'/application']
    assert writes==['ApiJobPosting','ApiJobPosting']
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(ashby['id'],)).fetchone())==before
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
