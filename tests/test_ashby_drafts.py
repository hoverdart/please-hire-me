"""Public-protocol fixtures: field saves and final submissions are distinct."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.ashby_drafts import AshbyDraft,VARIABLES
from hireme.browser import Browser
from hireme.util import Blocked,digest

QUERY=(Path(__file__).parent/'fixtures/ashby-set-form-value.graphql').read_text()
REQ='11111111-1111-4111-8111-111111111111'
RENDER='22222222-2222-4222-8222-222222222222'
DEFINITION='33333333-3333-4333-8333-333333333333'


def form(values=None):
    values=values or {}
    return {'id':RENDER,'sourceFormDefinitionId':DEFINITION,'errorMessages':[],'formErrors':[],
            'sections':[{'fieldEntries':[{'id':RENDER+'_'+path,'field':{'path':path,'title':title},
                'fieldValue':{'value':values[path]} if path in values else None}
                for path,title in [('_systemfield_name','Name'),('_systemfield_email','Email')]+([('_systemfield_phone','Phone')] if '_systemfield_phone' in values else [])]}]}


def request(value='test@candidate.invalid',**changes):
    variables={'organizationHostedJobsPageName':'acme','formRenderIdentifier':RENDER,
               'formDefinitionIdentifier':DEFINITION,'path':'_systemfield_email','value':value}
    variables.update(changes)
    data={'operationName':'ApiSetFormValue','query':QUERY,'variables':variables}
    return SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',post_data=json.dumps(data)),data


def draft(store,job,package):
    ashby={**job,'url':f'https://jobs.ashbyhq.com/acme/{REQ}','host':'jobs.ashbyhq.com'}
    ashby['id']=digest(ashby['url']);store.upsert_job(ashby)
    proof={**package,'job_id':ashby['id'],'url':ashby['url'],'steps':[]}
    proof['answers'][0]['field'].update(ashby_path='_systemfield_email',ashby_entry_id=RENDER+'__systemfield_email')
    d=AshbyDraft(store,ashby)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                            'variables':{'organizationHostedJobsPageName':'acme','jobPostingId':REQ}}))
    response=SimpleNamespace(status=200,json=lambda:{'data':{'jobPosting':{'id':REQ,'applicationForm':form()}}})
    d.observe_posting(read,response);d.enable(None,proof['answers'],proof)
    return d,ashby


def test_exact_field_save_requires_verified_form_sources_and_acknowledgement(store,job,package):
    d,_=draft(store,job,package);r,data=request()
    assert d.allow(r,data,'request') and not d.complete
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':form({'_systemfield_email':'test@candidate.invalid'})}}),'request')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['exact','different','missing','error','wrong_entry'])
def test_only_exact_error_free_official_prefills_count_as_saved(store,job,package,mode):
    d,_=draft(store,job,package)
    saved=form({'_systemfield_email':'test@candidate.invalid'})
    entry=saved['sections'][0]['fieldEntries'][1]
    if mode=='different':entry['fieldValue']['value']='other@candidate.invalid'
    if mode=='missing':entry['fieldValue']=None
    if mode=='error':saved['formErrors']=['Synthetic form error']
    if mode=='wrong_entry':entry['id']='another-entry'
    d._remember_form(saved)
    if mode=='wrong_entry':
        with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,d.package['answers'],d.package)
        return
    d.enable(None,d.package['answers'],d.package)
    assert d.complete is (mode=='exact')
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='ashby_draft_write'").fetchone()


@pytest.mark.parametrize('changes',[
    {'organizationHostedJobsPageName':'other'}, {'formRenderIdentifier':'other'},
    {'formDefinitionIdentifier':'other'}, {'path':'unapproved'}, {'value':'unapproved@candidate.invalid'},
    {'extra':'unreviewed'}, {'value':True},
])
def test_changed_destination_path_or_value_has_no_write_grant(store,job,package,changes):
    d,_=draft(store,job,package);r,data=request(**changes)
    assert not d.allow(r,data,'request') and not d.pending


@pytest.mark.parametrize('change',['query','operation','batch'])
def test_no_additional_mutation_or_batch_can_borrow_a_field_grant(store,job,package,change):
    d,_=draft(store,job,package);r,data=request()
    if change=='query':data['query']+=' mutation Other { submitApplication { id } }'
    elif change=='operation':data['operationName']='ApiSubmitApplication'
    else:data=[data]
    assert not d.allow(r,data,'request')


def test_changed_sources_and_pause_revoke_an_existing_field_grant(store,job,package):
    d,_=draft(store,job,package);r,data=request()
    store.put_facts({'email':'changed@candidate.invalid'})
    with pytest.raises(Blocked):d.allow(r,data,'request')
    assert not d.pending
    store.update_settings({'live_enabled':False})
    with pytest.raises(Blocked,match='paused'):d.allow(r,data,'request')


@pytest.mark.parametrize('mode',['http_error','graphql_error','wrong_render','wrong_value','missing_value'])
def test_transport_success_alone_is_not_a_saved_field(store,job,package,mode):
    d,_=draft(store,job,package);r,data=request();assert d.allow(r,data,'request')
    saved=form({'_systemfield_email':'test@candidate.invalid'})
    if mode=='wrong_render':saved['id']='other'
    if mode=='wrong_value':saved['sections'][0]['fieldEntries'][1]['fieldValue']['value']='different'
    if mode=='missing_value':saved['sections'][0]['fieldEntries'][1]['fieldValue']=None
    result={'data':{'setFormValue':saved}}
    if mode=='graphql_error':result['errors']=[{'message':'Synthetic failure'}]
    d.observe_save(SimpleNamespace(status=500 if mode=='http_error' else 200,json=lambda:result),'request')
    assert d.failed and not d.complete


@pytest.mark.parametrize('restored_phone',[False,True])
@pytest.mark.parametrize('live,ack',[(True,True),(False,True),(True,False)])
def test_browser_saves_only_approved_fields_before_final_intent_and_keeps_preparation_local(store,monkeypatch,live,ack,restored_phone):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    job={'id':digest(REQ),'url':f'https://jobs.ashbyhq.com/acme/{REQ}', 'host':'jobs.ashbyhq.com',
         'company':'Acme','title':'Software Engineer Intern Summer 2027','description':'Build Python software.',
         'location':'San Francisco, United States','source':'fixture'}
    store.upsert_job(job);remote={};final=[]
    metadata=[('_systemfield_name','Name','text'),('_systemfield_email','Email','email')]
    if restored_phone:
        remote['_systemfield_phone']=store.facts()['phone']['value']
        metadata.append(('_systemfield_phone','Phone','tel'))
    initial=dict(remote)
    inputs=''.join(f'<div data-field-path="{path}" data-field-entry-id="{RENDER}_{path}"><label>{title}<input id="{path}" name="{path}" type="{kind}" value="{remote.get(path, "")}" required></label></div>' for path,title,kind in metadata)
    html='''<html><body><h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p><form>'''+inputs+'''<label>Resume<input type="file" name="resume" required></label><button type="submit">Submit Application</button></form><script>
const query='''+json.dumps(QUERY)+''';
const endpoint='/api/non-user-graphql';
const initial='''+json.dumps(initial)+''';
fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiJobPosting',query:'query ApiJobPosting { jobPosting { id } }',variables:{organizationHostedJobsPageName:'acme',jobPostingId:'''+json.dumps(REQ)+'''}})});
for(const el of document.querySelectorAll('input:not([type=file])'))el.addEventListener('input',()=>{if(el.value===initial[el.id])return;fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiSetFormValue',query,variables:{organizationHostedJobsPageName:'acme',formRenderIdentifier:'''+json.dumps(RENDER)+''',formDefinitionIdentifier:'''+json.dumps(DEFINITION)+''',path:el.id,value:el.value}})}).catch(()=>{})});
document.querySelector('form').onsubmit=async e=>{e.preventDefault();const r=await fetch(endpoint,{method:'POST',body:JSON.stringify({operationName:'ApiSubmitApplication'})});if(r.ok)document.body.innerHTML='Thank you for applying. Your application was received.'};
</script></body></html>'''
    with Browser(store) as b:
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiJobPosting':result={'data':{'jobPosting':{'id':REQ,'applicationForm':form(remote)}}}
                elif data['operationName']=='ApiSetFormValue':
                    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    v=data['variables'];remote[v['path']]=v['value'];result={'data':{'setFormValue':form(remote)}}
                    if not ack:result['errors']=[{'message':'Synthetic save rejected'}]
                else:
                    assert data['operationName']=='ApiSubmitApplication'
                    assert store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    assert remote=={**initial,'_systemfield_name':'Test Person','_systemfield_email':'test@candidate.invalid'}
                    final.append(data);result={'data':{'submitted':True}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        original=b._verify
        def verify(answers,documents,fields):
            b.uploaded_files.update(d['hash'] for d in documents);b.ashby_attached_files.update(d['hash'] for d in documents)
            return original(answers,documents,fields)
        b._verify=verify
        if live and not ack:
            with pytest.raises(Blocked,match='ashby_draft_save_failed'):b.apply(job,live=True)
            assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
        else:assert b.apply(job,live=live)==('confirmed' if live else 'prepared')
        if live and ack:
            with pytest.raises(Blocked):b.apply(job)
    assert len(final)==int(live and ack)
    if not live:assert remote==initial


def test_saved_form_can_reveal_a_conditional_field_without_authorizing_its_value(store,job,package):
    d,_=draft(store,job,package);r,data=request();assert d.allow(r,data,'request')
    saved=form({'_systemfield_email':'test@candidate.invalid'})
    saved['sections'][0]['fieldEntries'].append({'id':RENDER+'_conditional','field':{'path':'conditional','title':'New employer question'},'fieldValue':None})
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':saved}}),'request')
    assert 'conditional' in d.forms[RENDER]['entries']
    r,data=request(path='conditional',value='Invented answer')
    assert not d.allow(r,data,'new-request')


@pytest.mark.parametrize('url,method',[
    ('https://attacker.invalid/api/non-user-graphql','POST'),
    ('https://jobs.ashbyhq.com/another-path','POST'),
    ('http://jobs.ashbyhq.com/api/non-user-graphql','POST'),
    ('https://jobs.ashbyhq.com/api/non-user-graphql','PUT'),
])
def test_exact_field_grant_has_no_other_transport_authority(store,job,package,url,method):
    d,_=draft(store,job,package);r,data=request();r.url=url;r.method=method
    assert not d.allow(r,data,'request')


def test_field_grant_does_not_send_extra_envelope_data_or_url_actions(store,job,package):
    d,_=draft(store,job,package);r,data=request();data['extensions']={'unreviewed':'extra-data'}
    assert not d.allow(r,data,'extra')
    r,data=request();r.url+='?op=ApiSubmitApplication'
    assert not d.allow(r,data,'other-operation')
    r.url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSetFormValue'
    assert d.allow(r,data,'approved')

LOCATION_QUERY=(Path(__file__).parent/'fixtures/ashby-autocomplete-location.graphql').read_text()
LOCATION='Berkeley, California, United States'


def location_draft(store,job,package,*,observe=True,restored=None):
    from hireme.answers import resolve
    d,ashby=draft(store,job,package)
    field={'index':0,'indices':[0],'label':'Location','type':'combobox','required':True,
           'options':[LOCATION],'maxlength':-1,'value':'','ashby_path':'_systemfield_location',
           'ashby_entry_id':RENDER+'__systemfield_location'}
    answer=resolve(store,ashby['host'],field)
    proof={**d.package,'answers':[answer]}
    saved=form()
    saved['sections'][0]['fieldEntries'].append({'id':field['ashby_entry_id'],
        'field':{'path':'_systemfield_location','title':'Location','type':'Location','locationTypes':['City']},
        'fieldValue':{'value':restored} if restored else None})
    d._remember_form(saved)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiAutocompleteGeoLocation',
        post_data=json.dumps({'operationName':'ApiAutocompleteGeoLocation','query':LOCATION_QUERY,
                             'variables':{'text':'Berkeley','locationTypes':['City']}}))
    result={'data':{'result':{'suggestions':[{'name':LOCATION,'geoLocationPath':[
        {'providerLocationId':'fixture-country'},{'providerLocationId':'fixture-city'}]}]}}}
    if observe:d.observe_locations(read,SimpleNamespace(status=200,json=lambda:result))
    return d,proof,read,result


def test_location_object_requires_official_choice_and_exact_save_ack(store,job,package):
    d,proof,_,_=location_draft(store,job,package)
    d.enable(None,proof['answers'],proof)
    value={'text':LOCATION,'providerLocationId':'fixture-city'}
    r,data=request(value,path='_systemfield_location')
    assert d.allow(r,data,'location') and not d.complete
    saved=form();saved['sections'][0]['fieldEntries'].append({'id':RENDER+'__systemfield_location',
        'field':{'path':'_systemfield_location','title':'Location','type':'Location','locationTypes':['City']},
        'fieldValue':{'value':value}})
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':saved}}),'location')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['missing','wrong_host','wrong_method','wrong_query','wrong_operation','errors','wrong_types','ambiguous'])
def test_unverified_or_ambiguous_location_lookup_grants_no_write(store,job,package,mode):
    d,proof,read,result=location_draft(store,job,package,observe=False)
    if mode=='wrong_host':read.url=read.url.replace('jobs.ashbyhq.com','attacker.invalid')
    if mode=='wrong_method':read.method='PUT'
    data=json.loads(read.post_data)
    if mode=='wrong_query':data['query']+=' mutation Other { submitApplication { id } }'
    if mode=='wrong_operation':data['operationName']='Other'
    if mode=='wrong_types':data['variables']['locationTypes']=['Country']
    if mode=='errors':result['errors']=[{'message':'Synthetic failure'}]
    if mode=='ambiguous':result['data']['result']['suggestions'].append({'name':LOCATION,'geoLocationPath':[{'providerLocationId':'different-city'}]})
    read.post_data=json.dumps(data)
    if mode!='missing':d.observe_locations(read,SimpleNamespace(status=200,json=lambda:result))
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    r,data=request({'text':LOCATION,'providerLocationId':'fixture-city'},path='_systemfield_location')
    assert not d.allow(r,data,'location')


@pytest.mark.parametrize('value',[
    LOCATION,{'text':LOCATION,'providerLocationId':'unverified'},
    {'text':'Different city','providerLocationId':'fixture-city'},
    {'text':LOCATION,'providerLocationId':'fixture-city','extra':'unreviewed'},
])
def test_location_grant_cannot_save_arbitrary_client_object(store,job,package,value):
    d,proof,_,_=location_draft(store,job,package);d.enable(None,proof['answers'],proof)
    r,data=request(value,path='_systemfield_location')
    assert not d.allow(r,data,'location')


def test_exact_official_location_prefill_needs_no_new_autocomplete(store,job,package):
    d,proof,_,_=location_draft(store,job,package,observe=False,
        restored={'text':LOCATION,'providerLocationId':'fixture-city'})
    d.enable(None,proof['answers'],proof)
    assert d.complete


@pytest.mark.parametrize('target,verified',[
    (f'https://jobs.ashbyhq.com/renamed/{REQ}',True),
    (f'https://jobs.ashbyhq.com/renamed/{REQ}',False),
    (f'https://jobs.ashbyhq.com/renamed/{DEFINITION}',True),
    (f'https://attacker.invalid/renamed/{REQ}',True),
    (f'http://jobs.ashbyhq.com/renamed/{REQ}',True),
    (f'https://jobs.ashbyhq.com/renamed/{REQ}?submit=true',True),
])
def test_tenant_redirect_requires_same_posting_response_and_safe_page(store,job,package,target,verified):
    d,ashby=draft(store,job,package)
    proof=d.package
    d.package=None;d.grants={}
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                            'variables':{'organizationHostedJobsPageName':'renamed','jobPostingId':REQ}}))
    if verified:d.observe_posting(read,SimpleNamespace(status=200,json=lambda:{'data':{'jobPosting':{'id':REQ,'applicationForm':form()}}}))
    original=dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(ashby['id'],)).fetchone())
    if verified and target==f'https://jobs.ashbyhq.com/renamed/{REQ}':
        d.bind_page(target)
        assert d.identity==('renamed',REQ) and d.navigation_url==target
        d.enable(None,proof['answers'],proof)
        r,data=request();assert not d.allow(r,data,'old-tenant')
        r,data=request(organizationHostedJobsPageName='renamed');assert d.allow(r,data,'new-tenant')
    else:
        with pytest.raises(Blocked,match='unexpected_redirect'):d.bind_page(target)
    assert dict(store.db.execute('SELECT * FROM jobs WHERE id=?',(ashby['id'],)).fetchone())==original


@pytest.mark.parametrize('mode',['saved','unverified_id','wrong_ack'])
def test_browser_routes_location_lookup_and_only_the_selected_object(store,job,package,monkeypatch,mode):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,proof,read,result=location_draft(store,job,package,observe=False)
    d.package=None;d.grants={}
    value={'text':LOCATION,'providerLocationId':'fixture-city' if mode!='unverified_id' else 'unverified'}
    data=request(value,path='_systemfield_location')[1];writes=[]
    html='<h1>Synthetic location widget</h1><script>window.lookup='+json.dumps(json.loads(read.post_data))+';window.save='+json.dumps(data)+''';
window.post = data => fetch('/api/non-user-graphql', {method:'POST',body:JSON.stringify(data)}).then(r=>r.json()).catch(()=>null);
</script>'''
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=d
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiAutocompleteGeoLocation':body=result
                else:
                    assert data['operationName']=='ApiSetFormValue'
                    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    writes.append(data)
                    ack={**value,'providerLocationId':'different'} if mode=='wrong_ack' else value
                    saved=form();saved['sections'][0]['fieldEntries'].append({'id':RENDER+'__systemfield_location',
                        'field':{'path':'_systemfield_location','title':'Location'},'fieldValue':{'value':ack}})
                    body={'data':{'setFormValue':saved}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(body))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        b.page.goto(d.job['url'])
        b.page.evaluate('() => post(lookup)')
        b.page.wait_for_timeout(100)
        assert d.locations
        d.enable(b,proof['answers'],proof)
        b.page.evaluate('() => post(save)')
        b.page.wait_for_timeout(100)
        assert len(writes)==(mode!='unverified_id')
        assert d.complete is (mode=='saved')
        assert d.failed is (mode=='wrong_ack')
