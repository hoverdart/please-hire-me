import json

import pytest

from hireme.browser import Browser
from hireme.util import Blocked,digest
from tests.ashby_document_fixture import AshbyDocuments
from tests.test_ashby_drafts import form,REQ,RENDER,DEFINITION


@pytest.mark.parametrize('change',['none','required','name','native','form','populated'])
def test_only_empty_native_autofill_picker_is_excluded_from_application_fields(store,change):
    from hireme.browser import SNAPSHOT,CONTROLS
    from playwright.sync_api import sync_playwright
    attrs='required' if change=='required' else 'name="extra"' if change=='name' else ''
    wrapper='data-field-path="extra"' if change=='native' else ''
    html='<div class="ashby-application-form-autofill-input-root" '+wrapper+'><input type="file" '+attrs+'></div>'
    if change=='form':html='<form>'+html+'</form>'
    html+='<label>Resume<input type="file" id="_systemfield_resume" required></label>'
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();page.set_content(html)
        if change=='populated':page.locator('input[type=file]').first.set_input_files({'name':'unapproved.pdf','mimeType':'application/pdf','buffer':b'not-approved'})
        fields=page.evaluate(SNAPSHOT,CONTROLS);browser.close()
    assert len(fields)==(1 if change=='none' else 2)
    assert fields[-1]['label']=='Resume/CV' and fields[-1]['required']


@pytest.mark.parametrize('change',['unsupported_native_answer','optional_unknown','malformed_document','changed_source','bad_final_control'])
def test_visible_application_is_compiled_before_any_remote_write(store,job,monkeypatch,change):
    url=f'https://jobs.ashbyhq.com/acme/{REQ}'
    job={**job,'url':url,'host':'jobs.ashbyhq.com','id':digest(url)};store.upsert_job(job)
    documents=AshbyDocuments(store,'acme',REQ,RENDER,DEFINITION)
    official=documents.form(form())
    official['formControls']=[{'identifier':'44444444-4444-4444-8444-444444444444','title':'Submit','__typename':'FormControl'}]
    if change=='unsupported_native_answer':official['sections'][0]['fieldEntries'][1]['field']['type']='FutureWidget'
    if change=='optional_unknown':
        official['sections'][0]['fieldEntries'].append({'id':RENDER+'_optional','field':{'path':'optional','title':'Unrecognized optional detail','type':'FutureWidget'}})
    if change=='malformed_document':official['sections'][-1]['fieldEntries'][-1]['field']['isMany']=True
    if change=='bad_final_control':official['formControls'][0]['identifier']='unverified'
    html=documents.html('''<h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p><form>
    <div data-field-path="_systemfield_name" data-field-entry-id="'''+RENDER+'''__systemfield_name"><label>Name<input id="name" required></label></div>
    <div data-field-path="_systemfield_email" data-field-entry-id="'''+RENDER+'''__systemfield_email"><label>Email<input type="email" required></label></div>
    <label>Resume<input type="file" name="resume" required></label><button>Submit Application</button></form>
    <script>fetch('/api/non-user-graphql',{method:'POST',body:JSON.stringify({operationName:'ApiJobPosting',query:'query ApiJobPosting { jobPosting { id } }',variables:{organizationHostedJobsPageName:'acme',jobPostingId:"'''+REQ+'''"}})});</script>''')
    if change=='optional_unknown':html=html.replace('</form>','<div data-field-path="optional" data-field-entry-id="'+RENDER+'_optional"><label>Unrecognized optional detail<input name="optional"></label></div></form>')
    if change=='changed_source':
        from hireme.application_spec import plan
        def changing(*args):
            approved=plan(*args);store.put_facts({'phone':'5550000000'});return approved
        monkeypatch.setattr('hireme.application_spec.plan',changing)
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    writes=[]
    with Browser(store) as browser:
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self,**overrides):
                data=json.loads(self.request.post_data)
                if data.get('operationName')=='ApiJobPosting':
                    self.route.fulfill(status=200,content_type='application/json',body=json.dumps({'data':{'jobPosting':{'id':REQ,'applicationForm':official}}}))
                else:
                    writes.append(data.get('operationName'));self.route.fulfill(status=500,body='Unapproved fixture mutation')
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:browser._route(Route(route))
        browser.context.unroute('**/*');browser.context.route('**/*',serve)
        with pytest.raises(Blocked):browser.apply(job,live=True)
    assert not writes and not documents.operations
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
