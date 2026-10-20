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
