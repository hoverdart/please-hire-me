import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from hireme.answers import resolve
from hireme.lever_submission import CAPTCHA, LeverSubmission
from hireme.util import Blocked,digest


@pytest.fixture
def lever(store,job,package):
    job={**job,'url':'https://jobs.lever.co/acme/11111111-1111-4111-8111-111111111111','host':'jobs.lever.co'}
    job['id']=digest(job['url']);store.upsert_job(job)
    fields=[{'index':i,'indices':[i],'label':label,'type':kind,'required':True,'options':[],
        'maxlength':-1,'ref':{'name':name,'id':name},'refs':[{'name':name,'id':name}]}
        for i,(name,label,kind) in enumerate([('name','Full name','text'),('email','Email','email')])]
    doc=copy.deepcopy(package['documents'][0]);doc['field']={**doc['field'],'ref':{'name':'resume','id':'resume-upload-input'}}
    blob=(store.root/'documents'/doc['filename']).read_bytes()
    approved={'job_id':job['id'],'url':job['url'],'answers':[resolve(store,job['host'],field) for field in fields],
        'documents':[doc],'facts_hash':digest(store.facts()),'steps':[{'fields':[*fields,doc['field']]}]}
    aid=store.prepare(job,approved)
    account='22222222-2222-4222-8222-222222222222';storage='synthetic-storage'
    pairs=[('name','Test Person'),('email','test@candidate.invalid'),('accountId',account),('resumeStorageId',storage),('timezone','America/Los_Angeles')]
    form={'method':'post','action':job['url']+'/apply','enctype':'multipart/form-data',
        'controls':[{'name':name,'id':name,'type':'text' if name in {'name','email'} else 'hidden','hidden':name not in {'name','email'},'value':value} for name,value in pairs]
            +[{'name':CAPTCHA,'type':'hidden','hidden':True,'value':''}],
        'entries':[{'name':name,'value':value} for name,value in pairs]
            +[{'name':CAPTCHA,'value':''},{'name':'resume','filename':doc['filename'],'size':len(blob),'type':'application/pdf'}]}
    browser=SimpleNamespace(store=store,page=SimpleNamespace(url=job['url']+'/apply',evaluate=lambda script:copy.deepcopy(form)),
        upload_payloads={doc['hash']:blob},lever_resume=SimpleNamespace(account=account,storage_id=storage,verify=lambda:None),receipt_baseline='Software engineering internship')
    return SimpleNamespace(browser=browser,job=job,package=approved,aid=aid,form=form,pairs=pairs,document=doc,blob=blob)


def request(fixture,changes=None,file_bytes=None):
    pairs=copy.deepcopy(fixture.pairs)+[(CAPTCHA,'synthetic-passive-captcha')]
    if changes:pairs=changes(pairs)
    boundary='FixtureBoundary'
    body=b''
    for name,value in pairs:
        body+=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="resume"; filename="{fixture.document["filename"]}"\r\nContent-Type: application/pdf\r\n\r\n'.encode()
        +(fixture.blob if file_bytes is None else file_bytes)+f'\r\n--{boundary}--\r\n'.encode())
    return SimpleNamespace(url=fixture.browser.page.url,method='POST',post_data_buffer=body,
        headers={'content-type':'multipart/form-data; boundary='+boundary})


def key(req):return req.url,req.method,hashlib.sha256(req.post_data_buffer).hexdigest()


def response(req,content='<html><body><h2>Thank you for applying.</h2><p>Your application has been received.</p></body></html>',status=200,ctype='text/html'):
    return SimpleNamespace(request=req,status=status,headers={'content-type':ctype},text=lambda:content)


def test_one_native_post_rebuilds_approved_pdf_and_requires_correlated_native_html_receipt(store,lever):
    grant=LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
    store.begin_submit(lever.aid)
    req=request(lever,file_bytes=b'')
    result=grant.grant(req,key(req))
    assert lever.blob in result['post_data'] and grant.used
    assert not grant.grant(req,key(req))
    grant.observe(response(req),(req.url,req.method,'wrong-request'))
    assert not grant.response_seen and not grant.acknowledged
    # Pausing after transmission must not discard a native accepted result.
    store.update_settings({'live_enabled':False})
    grant.observe(response(req),key(req))
    assert grant.acknowledged and grant.receipt_matches(req.url)
    assert not grant.receipt_matches(req.url+'?another=posting')
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='native_submission_write'").fetchone()[0]==1


@pytest.mark.parametrize('change',['answer','account','storage','extra','captcha','duplicate','file','url','method'])
def test_native_body_identity_and_approved_content_cannot_change(store,lever,change):
    grant=LeverSubmission(lever.browser,lever.job,lever.package,lever.aid);store.begin_submit(lever.aid)
    def modify(pairs):
        target={'answer':'email','account':'accountId','storage':'resumeStorageId','captcha':CAPTCHA}.get(change)
        if target:return [(name,'unapproved' if name==target else value) for name,value in pairs if change!='captcha' or name!=CAPTCHA]
        if change=='extra':return pairs+[('unapproved_claim','fabricated')]
        if change=='duplicate':return pairs+[('email','unapproved@candidate.invalid')]
        return pairs
    req=request(lever,modify,file_bytes=b'not-approved' if change=='file' else None)
    if change=='url':req.url=req.url.replace('/acme/','/another/')
    if change=='method':req.method='PUT'
    assert not grant.grant(req,key(req)) and not grant.used


@pytest.mark.parametrize('content,status,ctype',[
    ('<p>Thank you for applying.</p>',503,'text/html'),
    ('{"ok":true,"applicationId":"not-the-hosted-form"}',200,'application/json'),
    ('<p>Your application has not been submitted.</p>',200,'text/html'),
    ('<p>Thank you for applying.</p><form><input type=email></form>',200,'text/html'),
    ('<p>Software engineering internship</p>',200,'text/html'),
])
def test_native_transport_success_without_native_completed_receipt_stays_unknown(store,lever,content,status,ctype):
    grant=LeverSubmission(lever.browser,lever.job,lever.package,lever.aid);store.begin_submit(lever.aid)
    req=request(lever);assert grant.grant(req,key(req))
    grant.observe(response(req,content,status,ctype),key(req))
    assert grant.response_seen and grant.failed and not grant.acknowledged


@pytest.mark.parametrize('change',['pause','facts','package','form_destination','unapproved_prefill','hidden_duplicate'])
def test_live_authority_and_verified_form_are_required_before_send(store,lever,change):
    if change=='form_destination':lever.form['action']=lever.form['action'].replace('/acme/','/other/')
    if change=='unapproved_prefill':
        lever.form['controls'].append({'name':'linkedInData','type':'hidden','hidden':True,'value':'unapproved personal profile'})
        lever.form['entries'].append({'name':'linkedInData','value':'unapproved personal profile'})
    if change=='hidden_duplicate':
        lever.form['controls'].append({'name':'email','type':'hidden','hidden':True,'value':'unapproved@candidate.invalid'})
        lever.form['entries'].append({'name':'email','value':'unapproved@candidate.invalid'})
    if change in {'form_destination','unapproved_prefill','hidden_duplicate'}:
        with pytest.raises(Blocked):LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
        return
    grant=LeverSubmission(lever.browser,lever.job,lever.package,lever.aid);store.begin_submit(lever.aid)
    if change=='pause':store.update_settings({'live_enabled':False})
    elif change=='facts':store.put_facts({'skills':'Python, Rust'})
    else:grant.package['url']='https://jobs.lever.co/other'
    req=request(lever)
    with pytest.raises(Blocked):grant.grant(req,key(req))
    assert not grant.used


def test_changed_dom_value_before_grant_construction_cannot_replace_the_approved_answer(store,lever):
    original=copy.deepcopy(lever.package)
    for control in lever.form['controls']:
        if control['name']=='email':control['value']='unapproved@candidate.invalid'
    for entry in lever.form['entries']:
        if entry['name']=='email':entry['value']='unapproved@candidate.invalid'
    with pytest.raises(Blocked,match='field_verification_failed'):
        LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
    assert lever.package==original and store.application_record(lever.aid)['state']=='prepared'
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('name',['accountId','resumeStorageId','timezone'])
def test_duplicate_native_metadata_is_ambiguous_even_when_last_value_matches(store,lever,name):
    lever.form['controls'].insert(0,{'name':name,'type':'hidden','hidden':True,'value':'unapproved-metadata'})
    lever.form['entries'].insert(0,{'name':name,'value':'unapproved-metadata'})
    with pytest.raises(Blocked,match='unsupported_form'):
        LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)


@pytest.mark.parametrize('approved,checked',[('Yes',True),('No',False)])
def test_native_marketing_consent_preserves_default_zero_and_only_approved_checked_one(approved,checked):
    field={'label':'Marketing consent','type':'checkbox','ref':{'name':'consent[marketing]','id':'marketing'},'option_values':['1']}
    answer={'field':field,'value':approved}
    form={'controls':[{'name':'consent[marketing]','id':'marketing','type':'checkbox','hidden':False,'value':'1','checked':checked},
                      {'name':'consent[marketing]','type':'hidden','hidden':True,'value':'0'}],
          'entries':[{'name':'consent[marketing]','value':'0'}]+([{'name':'consent[marketing]','value':'1'}] if checked else [])}
    assert LeverSubmission._approved_pairs({'answers':[answer]},form)[0]=={'consent[marketing]'}
    form['controls'][0]['checked']=not checked
    with pytest.raises(Blocked,match='field_verification_failed'):LeverSubmission._approved_pairs({'answers':[answer]},form)


@pytest.mark.parametrize('kind',['radio','checkbox-group','select'])
def test_native_choice_values_are_bound_to_the_package_option_mapping(kind):
    names=['native-choice'] if kind=='select' else ['native-choice','native-choice']
    field={'label':'Approved choice','type':kind,'options':['First choice','Second choice'],
           'option_values':['native-first','native-second'],
           'refs':[{'name':name,'id':'choice-'+str(i)} for i,name in enumerate(names)]}
    field['ref']=field['refs'][0]
    controls=([{'name':'native-choice','id':'choice-0','type':'select-one','hidden':False,'value':'native-second'}] if kind=='select' else
        [{'name':'native-choice','id':'choice-'+str(i),'type':'radio' if kind=='radio' else 'checkbox','hidden':False,
          'value':value,'checked':i==1} for i,value in enumerate(field['option_values'])])
    form={'controls':controls,'entries':[{'name':'native-choice','value':'native-second'}]}
    package={'answers':[{'field':field,'value':'Second choice'}]}
    LeverSubmission._approved_pairs(package,form)
    controls[-1]['value']='unapproved-option'
    with pytest.raises(Blocked):LeverSubmission._approved_pairs(package,form)


def test_location_requires_same_approved_selected_name_and_holds_unreviewed_residential_fields(store,lever):
    field={'index':3,'indices':[3],'label':'Current location','type':'combobox','lever_location':True,'required':True,
           'options':['Berkeley, CA'],'maxlength':-1,'ref':{'name':'location','id':'location'},'refs':[{'name':'location','id':'location'}]}
    lever.package['answers'].append(resolve(store,lever.job['host'],field,context=lever.job))
    lever.package['steps'][0]['fields'].append(field)
    lever.aid=store.prepare(lever.job,lever.package)
    for name,value,hidden in [('location','Berkeley, CA',False),('selectedLocation',json.dumps({'id':'native-city','name':'Berkeley, CA'}),True)]:
        lever.form['controls'].append({'name':name,'id':name,'type':'hidden' if hidden else 'text','hidden':hidden,'value':value})
        lever.form['entries'].append({'name':name,'value':value})
    LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
    lever.form['controls'].append({'name':'residentialLocation[street]','type':'hidden','hidden':True,'value':'unapproved street'})
    lever.form['entries'].append({'name':'residentialLocation[street]','value':'unapproved street'})
    with pytest.raises(Blocked,match='unsupported_form'):LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
    lever.form['controls'].pop();lever.form['entries'].pop()
    lever.form['controls'][-1]['value']=json.dumps({'id':'native-city','name':'Berkeley, CA','street':'unapproved street'})
    lever.form['entries'][-1]['value']=lever.form['controls'][-1]['value']
    with pytest.raises(Blocked,match='field_verification_failed'):LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
    lever.form['controls'][-1]['value']=json.dumps({'id':'another-city','name':'Unapproved city'})
    lever.form['entries'][-1]['value']=lever.form['controls'][-1]['value']
    with pytest.raises(Blocked,match='field_verification_failed'):LeverSubmission(lever.browser,lever.job,lever.package,lever.aid)
