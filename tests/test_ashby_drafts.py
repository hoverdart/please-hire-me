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


def draft(store,job,package,definition=DEFINITION):
    ashby={**job,'url':f'https://jobs.ashbyhq.com/acme/{REQ}','host':'jobs.ashbyhq.com'}
    ashby['id']=digest(ashby['url']);store.upsert_job(ashby)
    proof={**package,'job_id':ashby['id'],'url':ashby['url'],'steps':[]}
    proof['answers'][0]['field'].update(ashby_path='_systemfield_email',ashby_entry_id=RENDER+'__systemfield_email')
    d=AshbyDraft(store,ashby)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql',
        post_data=json.dumps({'operationName':'ApiJobPosting','query':'query ApiJobPosting { jobPosting { id } }',
                            'variables':{'organizationHostedJobsPageName':'acme','jobPostingId':REQ}}))
    official=form();official['sourceFormDefinitionId']=definition
    response=SimpleNamespace(status=200,json=lambda:{'data':{'jobPosting':{'id':REQ,'applicationForm':official}}})
    d.observe_posting(read,response);d.enable(None,proof['answers'],proof)
    return d,ashby


def composite_definition(**changes):
    return json.dumps({'kind':'CompositeFormDefinitionId-JobPostingApplicationFormV2',
        'formDefinitionId':DEFINITION,'jobPostingId':REQ,'jobBoardSuperType':'External',**changes},separators=(',',':'))


def test_exact_composite_definition_from_official_posting_can_save_approved_field(store,job,package):
    definition=composite_definition();d,_=draft(store,job,package,definition)
    r,data=request(formDefinitionIdentifier=definition)
    assert d.allow(r,data,'request') and not d.complete
    acknowledgement=form({'_systemfield_email':'test@candidate.invalid'})
    acknowledgement['sourceFormDefinitionId']=definition
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':acknowledgement}}),'request')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('definition',[
    composite_definition(jobPostingId='44444444-4444-4444-8444-444444444444'),
    composite_definition(kind='UnreviewedCompositeDefinition'),
    composite_definition(jobBoardSuperType='Internal'),
    composite_definition(formDefinitionId='unverified'),
    composite_definition(extra='unreviewed'),
    json.dumps({'formDefinitionId':DEFINITION,'jobPostingId':REQ}),
    composite_definition()[:-1]+',"jobPostingId":"'+REQ+'"}',
    json.loads(composite_definition()),
    'not a definition',
    'a'*513,
])
def test_unverified_composite_definition_never_creates_field_save_grants(store,job,package,definition):
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):draft(store,job,package,definition)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind IN ('ashby_draft_write','submit_intent')").fetchone()


@pytest.mark.parametrize('change',['request','acknowledgement'])
def test_changed_composite_definition_cannot_borrow_a_grant_or_acknowledgement(store,job,package,change):
    definition=composite_definition();d,_=draft(store,job,package,definition)
    changed=composite_definition(formDefinitionId='44444444-4444-4444-8444-444444444444')
    r,data=request(formDefinitionIdentifier=changed if change=='request' else definition)
    if change=='request':
        assert not d.allow(r,data,'request') and not d.pending
    else:
        assert d.allow(r,data,'request')
        acknowledgement=form({'_systemfield_email':'test@candidate.invalid'});acknowledgement['sourceFormDefinitionId']=changed
        d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':acknowledgement}}),'request')
        assert d.failed and not d.complete


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

SCHOOL_QUERY=(Path(__file__).parent/'fixtures/ashby-search-school.graphql').read_text()
SCHOOL='University of California, Berkeley'
SCHOOL_PATH='_systemfield_education_history'


def choice_draft(store,job,package,kind):
    from hireme.answers import resolve
    store.put_facts({'degree':'BS'})
    store.update_settings({'contextual_preferences':True})
    d,_=draft(store,job,{**package,'facts_hash':digest(store.facts())})
    multiple=kind=='checkbox-group'
    field={'index':0,'indices':list(range(3 if multiple else 2)),
        'label':'Which programming languages are you experienced with?' if multiple else 'Degree Level Currently Pursuing',
        'type':kind,'required':True,'options':['Python','TypeScript','Rust'] if multiple else ["Bachelor's","Master's"],
        'maxlength':-1,'value':'','ashby_path':'fixture-choice','ashby_entry_id':RENDER+'_fixture-choice',
        'ref':{'id':'choice-0'},'refs':[{'id':'choice-'+str(i)} for i in range(3 if multiple else 2)]}
    values=['python-id','typescript-id','rust-id'] if multiple else ['bachelors-id','masters-id']
    official=form();official['sections'][0]['fieldEntries'].append({'id':field['ashby_entry_id'],
        'field':{'path':'fixture-choice','title':field['label'],'type':'MultiValueSelect' if multiple else 'ValueSelect',
                 'selectableValues':[{'label':label,'value':value,'isArchived':False} for label,value in zip(field['options'],values)]},
        'fieldValue':None})
    d._remember_form(official)
    answer=resolve(store,d.job['host'],field,context=d.job)
    proof={**d.package,'answers':[answer]}
    return d,proof,official,values[:2] if multiple else values[0]


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
def test_official_choices_encode_controls_without_native_value_attributes(store,job,package,kind):
    d,proof,official,value=choice_draft(store,job,package,kind)
    no_value=SimpleNamespace(_control=lambda *args:SimpleNamespace(get_attribute=lambda name:None))
    d.enable(no_value,proof['answers'],proof)
    r,data=request(value,path='fixture-choice')
    assert d.allow(r,data,'choice') and not d.complete
    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':value}
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'choice')
    assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
@pytest.mark.parametrize('change',['missing','malformed','label','duplicate_label','duplicate_value','archived','archive_status','nonstring_value'])
def test_unverified_official_choice_metadata_never_grants_a_write(store,job,package,kind,change):
    d,proof,_,value=choice_draft(store,job,package,kind)
    metadata=d.forms[RENDER]['entries']['fixture-choice'];rows=metadata['selectableValues']
    if change=='missing':metadata.pop('selectableValues')
    elif change=='malformed':rows.append(None)
    elif change=='label':rows[0]['label']='Different choice'
    elif change=='duplicate_label':rows[1]['label']=rows[0]['label']
    elif change=='duplicate_value':rows[1]['value']=rows[0]['value']
    elif change=='archived':rows[0]['isArchived']=True
    elif change=='archive_status':rows[0]['isArchived']='false'
    elif change=='nonstring_value':rows[0]['value']=False
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    r,data=request(value,path='fixture-choice');assert not d.allow(r,data,'choice')


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
@pytest.mark.parametrize('change',['labels','unapproved','wrong_shape'])
def test_official_choice_grant_rejects_other_wire_values_and_shapes(store,job,package,kind,change):
    d,proof,_,value=choice_draft(store,job,package,kind);d.enable(None,proof['answers'],proof)
    if change=='labels':value=proof['answers'][0]['value']
    elif change=='unapproved':value=['python-id','typescript-id','rust-id'] if kind=='checkbox-group' else 'masters-id'
    else:value=value[0] if kind=='checkbox-group' else [value]
    r,data=request(value,path='fixture-choice');assert not d.allow(r,data,'choice')


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
def test_official_choice_save_requires_the_exact_acknowledged_values(store,job,package,kind):
    d,proof,official,value=choice_draft(store,job,package,kind);d.enable(None,proof['answers'],proof)
    r,data=request(value,path='fixture-choice');assert d.allow(r,data,'choice')
    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':['python-id'] if kind=='checkbox-group' else 'masters-id'}
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'choice')
    assert d.failed and not d.complete


@pytest.mark.parametrize('kind',['radio','checkbox-group'])
def test_native_controls_with_default_on_save_only_approved_official_choice_values(store,job,package,monkeypatch,kind):
    import time
    from html import escape
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,proof,official,value=choice_draft(store,job,package,kind);field=proof['answers'][0]['field']
    wire=[row['value'] for row in official['sections'][0]['fieldEntries'][-1]['field']['selectableValues']]
    inputs=''.join('<label><input type="'+('radio' if kind=='radio' else 'checkbox')+'" name="choices" id="choice-'+str(i)+'">'+escape(label)+'</label>' for i,label in enumerate(field['options']))
    envelope=request(value,path='fixture-choice')[1];writes=[]
    html='<h1>Synthetic native choices</h1>'+inputs+'<script>const wire='+json.dumps(wire)+';const envelope='+json.dumps(envelope)+''';
const inputs=[...document.querySelectorAll('input')];
inputs.forEach((input,index)=>input.addEventListener('change',()=>{
const value=input.type==='radio'?wire[index]:inputs.flatMap((node,i)=>node.checked?[wire[i]]:[]);
fetch('/api/non-user-graphql',{method:'POST',body:JSON.stringify({...envelope,variables:{...envelope.variables,value}})}).catch(()=>null);
}));</script>'''
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=d
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data);writes.append(data)
                official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':data['variables']['value']}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps({'data':{'setFormValue':official}}))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve);b.page.goto(d.job['url'])
        assert b.page.locator('input').evaluate_all('(nodes)=>nodes.every(n=>n.getAttribute("value")===null&&n.value==="on")')
        d.enable(b,proof['answers'],proof);b._fill(proof['answers'][0])
        deadline=time.monotonic()+10
        while not (d.complete or d.failed) and time.monotonic()<deadline:b.page.wait_for_timeout(50)
        assert d.complete and not d.failed
        assert len(writes)==1 and writes[0]['variables']['value']==value
        assert b.page.locator('input:checked').count()==(2 if kind=='checkbox-group' else 1)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def school_draft(store,job,package,*,observe=True,restored=None):
    from hireme.answers import resolve
    store.put_facts({'school':SCHOOL})
    package={**package,'facts_hash':digest(store.facts())}
    d,_=draft(store,job,package)
    field={'index':0,'indices':[0],'label':'College/University','type':'combobox','required':True,
           'options':[SCHOOL],'maxlength':-1,'value':'','ashby_path':SCHOOL_PATH,
           'ashby_entry_id':RENDER+'_'+SCHOOL_PATH}
    proof={**d.package,'answers':[resolve(store,d.job['host'],field)]}
    official=form()
    official['sections'][0]['fieldEntries'].append({'id':field['ashby_entry_id'],
        'field':{'path':SCHOOL_PATH,'title':field['label'],'type':'EducationHistory',
                 'schoolName':'required','degree':'excluded','major':'excluded',
                 'startDate':'excluded','endDate':'excluded','isRepeatable':True,'minRepeat':1,'maxRepeat':3},
        'fieldValue':{'value':restored} if restored is not None else None})
    d._remember_form(official)
    read=SimpleNamespace(method='POST',url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSearchSchoolByCanonicalName',
        post_data=json.dumps({'operationName':'ApiSearchSchoolByCanonicalName','query':SCHOOL_QUERY,
                             'variables':{'name':'Berkeley','organizationHostedJobsPageName':'acme'}}))
    result={'data':{'result':[{'id':'fixture-school','name':SCHOOL,'__typename':'CanonicalSchool'}]}}
    if observe:d.observe_schools(read,SimpleNamespace(status=200,json=lambda:result))
    return d,proof,read,result,official


@pytest.mark.parametrize('restored',[False,True])
def test_school_only_history_requires_exact_canonical_choice_and_acknowledgement(store,job,package,restored):
    value=[{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL}]
    d,proof,_,_,official=school_draft(store,job,package,observe=not restored,restored=value if restored else None)
    d.enable(None,proof['answers'],proof)
    assert d.complete is restored
    if not restored:
        r,data=request(value,path=SCHOOL_PATH)
        assert d.allow(r,data,'school') and not d.complete
        official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':value}
        d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':official}}),'school')
        assert d.complete
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['missing','wrong_host','wrong_method','wrong_tenant','wrong_query','wrong_operation',
    'errors','ambiguous','malformed','partial_malformed','extra_variable','wrong_url_operation'])
def test_unverified_school_lookup_never_grants_a_history_write(store,job,package,mode):
    d,proof,read,result,_=school_draft(store,job,package,observe=False)
    data=json.loads(read.post_data)
    if mode=='wrong_host':read.url=read.url.replace('jobs.ashbyhq.com','attacker.invalid')
    if mode=='wrong_method':read.method='PUT'
    if mode=='wrong_tenant':data['variables']['organizationHostedJobsPageName']='other'
    if mode=='wrong_query':data['query']+=' mutation Other { submitApplication { id } }'
    if mode=='wrong_operation':data['operationName']='Other'
    if mode=='extra_variable':data['variables']['extra']='unreviewed'
    if mode=='wrong_url_operation':read.url=read.url.replace('ApiSearchSchoolByCanonicalName','Other')
    if mode=='errors':result['errors']=[{'message':'Synthetic error'}]
    if mode=='ambiguous':result['data']['result'].append({'id':'different-school','name':SCHOOL})
    if mode=='malformed':result['data']['result']={}
    if mode=='partial_malformed':result['data']['result'].append({'id':None,'name':SCHOOL})
    read.post_data=json.dumps(data)
    if mode!='missing':d.observe_schools(read,SimpleNamespace(status=200,json=lambda:result))
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    r,data=request([{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL}],path=SCHOOL_PATH)
    assert not d.allow(r,data,'school')


@pytest.mark.parametrize('value',[
    SCHOOL,[{'canonicalSchoolId':'unverified','schoolName':SCHOOL}],
    [{'canonicalSchoolId':'fixture-school','schoolName':'Other school'}],
    [{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL,'degree':'Unapproved'}],
    [{'canonicalSchoolId':'fixture-school','schoolName':SCHOOL}]*2,
])
def test_school_grant_cannot_write_additional_or_changed_history(store,job,package,value):
    d,proof,_,_,_=school_draft(store,job,package);d.enable(None,proof['answers'],proof)
    r,data=request(value,path=SCHOOL_PATH)
    assert not d.allow(r,data,'school')


def test_school_lookup_retains_ambiguity_across_responses_and_repeated_paths_stay_held(store,job,package):
    d,proof,read,result,_=school_draft(store,job,package)
    result['data']['result'][0]['id']='different-school'
    d.observe_schools(read,SimpleNamespace(status=200,json=lambda:result))
    with pytest.raises(Blocked,match='ashby_draft_unsupported'):d.enable(None,proof['answers'],proof)
    d.schools={SCHOOL:{'fixture-school'}}
    proof={**proof,'answers':proof['answers']*2}
    with pytest.raises(Blocked,match='Duplicate Ashby field path'):d.enable(None,proof['answers'],proof)


@pytest.mark.parametrize('change',['degree','major','startDate','endDate','minRepeat','provenance','dom_prefill'])
def test_complex_history_or_unsourced_school_stays_held(store,job,package,change):
    d,proof,_,_,_=school_draft(store,job,package,observe=change!='dom_prefill')
    metadata=d.forms[RENDER]['entries'][SCHOOL_PATH]
    if change=='provenance':proof['answers'][0]['provenance']={'fact_key':'location','revision':1}
    elif change=='dom_prefill':proof['answers'][0]['field']['value']=SCHOOL
    elif change=='minRepeat':metadata[change]=2
    else:metadata[change]='required'
    expected='unsupported_or_stale_answer' if change=='provenance' else 'ashby_draft_unsupported'
    with pytest.raises(Blocked,match=expected):d.enable(None,proof['answers'],proof)


@pytest.mark.parametrize('mode',['saved','unverified_id','wrong_ack'])
def test_browser_routes_canonical_school_lookup_and_exact_history_save(store,job,package,monkeypatch,mode):
    import time
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    d,proof,read,result,official=school_draft(store,job,package,observe=False)
    d.package=None;d.grants={}
    value=[{'canonicalSchoolId':'unverified' if mode=='unverified_id' else 'fixture-school','schoolName':SCHOOL}]
    data=request(value,path=SCHOOL_PATH)[1];writes=[]
    html='<h1>Synthetic school widget</h1><script>window.lookup='+json.dumps(json.loads(read.post_data))+';window.save='+json.dumps(data)+''';
window.post = data => fetch('/api/non-user-graphql', {method:'POST',body:JSON.stringify(data)}).then(r=>r.json()).catch(()=>null);
</script>'''
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.ashby_draft=d
        class Route:
            def __init__(self,route):self.route=route;self.request=route.request
            def abort(self):self.route.abort()
            def continue_(self):
                data=json.loads(self.request.post_data)
                if data['operationName']=='ApiSearchSchoolByCanonicalName':body=result
                else:
                    assert data['operationName']=='ApiSetFormValue'
                    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    writes.append(data)
                    ack=[{'canonicalSchoolId':'different','schoolName':SCHOOL}] if mode=='wrong_ack' else value
                    official['sections'][0]['fieldEntries'][-1]['fieldValue']={'value':ack}
                    body={'data':{'setFormValue':official}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(body))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(Route(route))
        b.page.route('**/*',serve)
        b.page.goto(d.job['url'])
        b.page.evaluate('() => post(lookup)')
        deadline=time.monotonic()+10
        while not d.schools and time.monotonic()<deadline:b.page.wait_for_timeout(50)
        assert d.schools=={SCHOOL:{'fixture-school'}}
        d.enable(b,proof['answers'],proof)
        b.page.evaluate('() => post(save)')
        deadline=time.monotonic()+10
        while mode!='unverified_id' and not (d.complete or d.failed) and time.monotonic()<deadline:b.page.wait_for_timeout(50)
        assert len(writes)==(mode!='unverified_id')
        assert d.complete is (mode=='saved')
        assert d.failed is (mode=='wrong_ack')


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
