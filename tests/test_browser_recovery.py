"""Browser regressions reproduced without real employer requests or inference."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.browser import Browser
from hireme.browser_navigation import candidates, navigate
from hireme.platform_browser import PlatformBrowser
from hireme.platform_connections import configure, import_listing, status
from hireme.platform_routing import external_destination, save_route
from hireme.util import Blocked
from tests.test_browser import ats, local_job
from tests.test_platform_browser import native_site


@pytest.mark.parametrize('path,expected',[
    ('/acme/req-123/apply',True), ('/acme/other/apply',False),
    ('/acme/req-123/apply?submit=true',False), ('/acme/req-123/apply#submit',False),
])
def test_navigation_keeps_exact_requisition(job,path,expected):
    rows=candidates(job,[{'text':'Apply to this position','href':'https://jobs.lever.co'+path}]*2)
    assert len(rows)==int(expected)


def test_application_link_uses_navigation_without_running_its_handler(store,job,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    with Browser(store) as browser:
        browser.current_host=job['host'];requests=[]
        def serve(route):
            requests.append((route.request.method,route.request.url))
            route.fulfill(status=200,content_type='text/html',body='<form><label>Email<input type=email></label></form>')
        browser.context.route('**/*',serve)
        browser.page.set_content(f'<a href="{job["url"]}/apply" onclick="fetch(\'/danger\',{{method:\'POST\'}});return false">Apply to this position</a>')
        assert navigate(browser,job)
        assert requests==[('GET',job['url']+'/apply')]
        assert not browser.attempted


def test_posting_search_field_does_not_prevent_application_navigation(store,job,monkeypatch):
    from hireme.ats_adapters import SingleStepAdapter
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    monkeypatch.setattr('hireme.ats_adapters.adapter_for',lambda job:SingleStepAdapter())
    html=(Path(__file__).parent/'fixtures/application.html').read_text()
    with Browser(store) as browser:
        requests=[]
        def serve(route):
            requests.append((route.request.method,route.request.url))
            body=html if route.request.url.endswith('/apply') else (
                '<h1>'+job['title']+'</h1><p>Build Python and TypeScript software.</p>'
                '<form><label>Search<input name=search></label></form>'
                '<a href="'+job['url']+'/apply">Apply to this position</a>')
            route.fulfill(status=200,content_type='text/html',body=body)
        browser.context.route('**/*',serve)
        assert browser.apply(job,live=False)=='prepared'
        assert requests==[('GET',job['url']),('GET',job['url']+'/apply')]
        package=json.loads(store.db.execute('SELECT package FROM applications').fetchone()[0])
        assert not any(a['field']['label']=='Search' for a in package['answers'])


@pytest.mark.parametrize('attribute',['hidden','inert','aria-hidden="true"','style="display:none"','style="visibility:hidden"','style="opacity:0"'])
def test_hidden_conditional_upload_is_not_a_current_question(store,attribute):
    with Browser(store) as browser:
        browser.page.set_content('<form><label>Name<input name=name></label><div '+attribute+
            '><label>Work sample<input name=sample type=file required></label></div>'
            '<label>Resume<input name=resume type=file style="display:none"></label></form>')
        assert [f['label'] for f in browser._snapshot()]==['Name','Resume/CV']


def test_styled_native_choices_remain_observable_while_hidden_sections_stay_excluded(store):
    with Browser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<form>
          <label for=consent>Required consent</label><input id=consent type=checkbox required style="opacity:0">
          <fieldset><legend>Authorization</legend>
            <label><input type=radio name=authorization value=Yes required style="opacity:0">Yes</label>
            <label><input type=radio name=authorization value=No style="opacity:0">No</label></fieldset>
          <section style="opacity:0"><label><input type=checkbox required>Hidden consent</label></section>
        </form>''')
        fields=browser._snapshot()
        assert [(f['label'],f['type']) for f in fields]==[('Required consent','checkbox'),('Authorization','radio')]
        assert all(f['required'] for f in fields) and fields[1]['options']==['Yes','No']


def test_parent_change_is_inspected_before_filling_reused_control(store,ats):
    html=(Path(__file__).parent/'fixtures/application.html').read_text()
    # The name answer replaces the email question while keeping its DOM ID.
    html=html.replace('</body>', '''<script>
      document.getElementById('name').addEventListener('input',()=>{
        const email=document.getElementById('email');
        document.querySelector('label[for=email]').textContent='Unapproved new question';
        email.addEventListener('input',()=>window.staleWrite=true);
      });</script></body>''')
    with Browser(store,test_url=ats[0]) as browser:
        browser.page.route(ats[0]+'/**',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        with pytest.raises(Blocked,match='form_changed'):browser.apply(local_job(store,ats))
        assert not browser.page.evaluate('window.staleWrite||false')
    assert not ats[1]


@pytest.mark.parametrize('connection',['handshake','workatastartup'])
def test_saved_listing_routes_without_native_submission_capabilities(store,native_site,connection):
    native_site[2]['value']='external'
    base='https://app.joinhandshake.com/jobs/123' if connection=='handshake' else 'https://www.workatastartup.com/jobs/123'
    key=import_listing(store,base,'Acme','Software Engineer Intern Summer 2027','San Francisco, United States')
    job=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])
    configure(store,connection,{'enabled':True})
    with PlatformBrowser(store,connection,test_url=native_site[0]+'/jobs/123') as browser:
        assert browser.apply(job)=='routed'
    employer=store.db.execute('SELECT * FROM jobs WHERE host="jobs.lever.co"').fetchone()
    assert employer and employer['status']=='discovered'
    assert store.db.execute('SELECT job_id FROM job_origins').fetchone()[0]==employer['id']
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(key,)).fetchone()[0]=='routed'
    assert not native_site[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not any(cap['available'] for cap in status(store,connection)['capabilities'].values())
    assert import_listing(store,base,job['company'],job['title'])==employer['id']
    assert not store.db.execute('SELECT 1 FROM connection_conflicts').fetchone()


@pytest.mark.parametrize('connection',['handshake','workatastartup'])
def test_adaptive_platform_lane_preserves_external_employer_routing(store,native_site,connection):
    from hireme.browser_agent import AdaptiveBrowser
    native_site[2]['value']='external'
    base='https://app.joinhandshake.com/jobs/123' if connection=='handshake' else 'https://www.workatastartup.com/jobs/123'
    key=import_listing(store,base,'Acme','Software Engineer Intern Summer 2027','San Francisco, United States')
    job=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])
    configure(store,connection,{'enabled':True,'native_apply_enabled':True})
    with AdaptiveBrowser(store,connection_id=connection,test_url=native_site[0]+'/jobs/123') as browser:
        assert browser.apply(job)=='routed'
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(key,)).fetchone()[0]=='routed'
    assert not native_site[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('state',['prepared','unknown','confirmed'])
def test_routing_cannot_change_a_listing_with_application_history(store,job,package,state):
    # Give the synthetic platform listing an application record to ensure even
    # a prepared package cannot be silently moved to an employer identity.
    key=import_listing(store,'https://app.joinhandshake.com/jobs/123','Acme',job['title'],job['location'],job['description'])
    native=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])
    package={**package,'job_id':key,'url':native['url']}
    aid=store.prepare(native,package)
    if state!='prepared':
        store.begin_submit(aid);store.finish(aid,state,'Synthetic history')
    before=store.application_record(aid)
    with pytest.raises(Blocked,match='identity_review'):save_route(store,native,job)
    assert store.application_record(aid)==before
    assert store.db.execute('SELECT job_id FROM job_origins').fetchone()[0]==key


def test_listing_reads_never_authorize_graphql_mutations(store):
    browser=PlatformBrowser(store,'handshake');browser.inspecting_listing=True
    for query,expected in [('query Job { job(id:123) { title } }',True),
                           ('mutation Job { apply(jobId:123) { id } }',False),
                           ('query Job { job { title } } mutation Apply { apply { id } }',False)]:
        request=SimpleNamespace(url='https://app.joinhandshake.com/hs/graphql',method='POST',
            post_data_buffer=json.dumps({'query':query,'variables':{}}).encode())
        assert bool(browser._listing_read(request)) is expected


@pytest.mark.parametrize('change',['title','company','quick_apply','board','multiple','other_employer'])
def test_external_route_requires_exact_listing_and_unambiguous_requisition(store,job,change):
    native={**job,'connection_id':'handshake'}
    snapshot={'headings':[job['title']],'text':'Acme '+job['title'],
              'links':[{'text':'Apply externally','href':job['url']}]}
    if change=='title':snapshot['headings']=['Different role']
    if change=='company':snapshot['text']='Different company'
    if change=='quick_apply':snapshot['links'][0]['text']='Quick Apply'
    if change=='board':snapshot['links'][0]['href']='https://jobs.lever.co/acme'
    if change=='multiple':snapshot['links'].append({'text':'Apply externally','href':job['url']+'-other'})
    if change=='other_employer':snapshot['links'][0]['href']=job['url'].replace('/acme/','/other/')
    if change in {'quick_apply','board','other_employer'}:
        assert external_destination(store,native,snapshot) is None
    else:
        with pytest.raises(Blocked):external_destination(store,native,snapshot)


def test_explicit_listing_inspection_works_while_paused_without_enabling_native_writes(store,native_site,monkeypatch,job):
    from hireme.platform_connections import inspect_listing
    native_site[2]['value']='external'
    store.update_settings({'live_enabled':False})
    key=import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'],job['location'])
    monkeypatch.setattr('hireme.platform_browser.PlatformBrowser',lambda store,connection:
        PlatformBrowser(store,connection,test_url=native_site[0]+'/jobs/123'))
    result=inspect_listing(store,'handshake',key)
    assert result['routed'] and result['job_id']==job['id']
    assert not store.settings()['live_enabled'] and not native_site[1]
    assert not status(store,'handshake')['native_apply_enabled']


def test_unrelated_invalid_form_cannot_block_the_application(store,ats):
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('</body>',
        '<form id=newsletter><input aria-label="Newsletter signup" type=email required></form></body>')
    with Browser(store,test_url=ats[0]) as browser:
        browser.context.route(ats[0]+'/**',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        assert browser.apply(local_job(store,ats),live=False)=='prepared'


def test_navigation_provider_cannot_return_an_unobserved_action():
    from hireme.provider import ClaudeProvider
    provider=object.__new__(ClaudeProvider)
    provider.request=lambda *args:{'action_id':'invented-action'}
    with pytest.raises(Blocked,match='provider_invalid_output'):
        provider.choose_browser_action({'title':'Role'},[{'id':'observed','label':'Apply'}])


def test_routing_preserves_existing_official_posting_metadata(store,job):
    before=dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
    key=import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'])
    native=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])
    old_question=store.ask(key,native['host'],'Obsolete native platform question',[])
    other_question=store.ask(job['id'],job['host'],'Current employer question',[])
    assert save_route(store,native,{**job,'source':'handshake','description':'Lower fidelity platform text'})==job['id']
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())==before
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(old_question,)).fetchone()[0]==1
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(other_question,)).fetchone()[0]==0


@pytest.mark.parametrize('url',['https://jobs.lever.co/acme','https://jobs.lever.co/other/req-123'])
def test_routing_boundary_rejects_boards_and_other_employers(store,job,url):
    from hireme.discovery import posting
    key=import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'])
    native=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])
    employer=posting(url,job['company'],job['title'],job['location'],'handshake')
    with pytest.raises(Blocked):save_route(store,native,employer)
    assert store.db.execute('SELECT job_id FROM job_origins').fetchone()[0]==key


def test_radio_groups_remain_bound_to_their_own_form(store):
    with Browser(store) as browser:
        browser.page.set_content('''<form id=survey><fieldset><legend>Survey</legend>
          <input type=radio name=choice value=Unrelated><label>Unrelated</label></fieldset></form>
          <form id=application><label>Full name<input name=name></label><label>Email<input name=email></label>
          <fieldset><legend>Will you require sponsorship?</legend>
          <label><input type=radio name=choice value=Yes required>Yes</label>
          <label><input type=radio name=choice value=No required>No</label></fieldset></form>''')
        fields=browser._snapshot()
        radio=next(f for f in fields if f['type']=='radio')
        assert radio['options']==['Yes','No']
        assert not any(f['label']=='Survey' for f in fields)


def test_malformed_unrelated_link_does_not_break_navigation(job):
    assert candidates(job,[{'text':'Apply','href':'https://[malformed'}])==[]


def test_anonymous_email_control_is_reacquired_inside_the_application_form(store):
    from hireme.answers import resolve
    with Browser(store) as browser:
        browser.page.set_content('''<form id=newsletter><label>Email<input name=email type=email required></label></form>
          <form id=application><label>Full name<input name=name required></label>
          <label>Email<input name=email type=email required></label></form>''')
        field=next(f for f in browser._snapshot() if f['label']=='Email')
        browser._fill(resolve(store,'jobs.lever.co',field))
        assert browser.page.locator('#application input[name=email]').input_value()=='test@candidate.invalid'
        assert browser.page.locator('#newsletter input[name=email]').input_value()==''


def test_external_route_is_an_outcome_even_when_native_capabilities_are_available(store,job,monkeypatch):
    configure(store,'handshake',{'enabled':True,'native_apply_enabled':True})
    key=import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'],job['location'],job['description'])
    native=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])
    monkeypatch.setattr('hireme.platform_browser.flows',lambda *args:[{'fixture':True}])
    browser=PlatformBrowser(store,'handshake')
    monkeypatch.setattr(browser,'check_session',lambda:True)
    def prepare(candidate):
        save_route(store,candidate,job)
        return job,None,None
    monkeypatch.setattr(browser,'prepare',prepare)
    assert browser.apply(native)=='routed'
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(key,)).fetchone()[0]=='routed'
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
