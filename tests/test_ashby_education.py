"""Compound saves must carry exactly the confirmed history, never an edit prefix."""
import json
import calendar
import re
from pathlib import Path

import pytest

from hireme.answers import resolve,validate_package
from hireme.ashby_education import schema,wire,school_choice
from hireme.browser import Browser
from hireme.util import Blocked,digest
from tests.test_ashby_drafts import REQ,RENDER,DEFINITION,QUERY,draft,form
from tests.ashby_document_fixture import AshbyDocuments

PATH='_systemfield_education_history'
META={'path':PATH,'title':'Education History','type':'EducationHistory','isNullable':False,'isMany':False,
      'schoolName':'required','degree':'optional','major':'optional','startDate':'optional',
      'endDate':'optional','isRepeatable':True,'minRepeat':1,'maxRepeat':None}
SCHOOL='University of California, Berkeley'
SCHOOL_QUERY=(Path(__file__).parent/'fixtures/ashby-search-school.graphql').read_text()


@pytest.mark.parametrize('choices',[[],['Other'],['Unrelated School'],['Other','Unrelated School']])
def test_missing_canonical_school_is_a_mapping_hold(choices):
    with pytest.raises(Blocked,match='mapping_review'):school_choice(SCHOOL,choices)


def history(store,**changes):
    values={'institution':SCHOOL,'degree':'B.S.',
        'field_of_study':'Electrical Engineering and Computer Sciences','start':'2025-08',
        'expected_end':'2028-05','current':True,**changes}
    return store.put_history_entry('education',{k:v for k,v in values.items() if v is not None},
                                   source='Applicant confirmation',confirmed=True)


def field():
    return {'label':'Education History','type':'ashby_education','required':True,'options':[],
            'education_schema':schema(META),'school_options':[SCHOOL],
            'ashby_path':PATH,'ashby_entry_id':RENDER+'_'+PATH,'maxlength':-1}


def test_history_month_precision_and_current_status_never_become_completed_degree_dates(store):
    row=history(store);answer=resolve(store,'jobs.ashbyhq.com',field())
    value=json.loads(answer['value'])[0]
    assert value=={'schoolName':SCHOOL,'degree':'B.S.','major':'Electrical Engineering and Computer Sciences',
                   'startDate':'2025-08-01','isCurrent':True}
    assert answer['provenance']['ashby_education_history'][0][:2]==[row['id'],1]
    assert resolve(store,'jobs.ashbyhq.com',field())==json.loads(json.dumps(answer))


def test_optional_empty_history_can_be_omitted_but_required_history_cannot(store):
    assert resolve(store,'jobs.ashbyhq.com',{**field(),'required':False}) is None
    with pytest.raises(Blocked,match='history_confirmation_required'):resolve(store,'jobs.ashbyhq.com',field())


@pytest.mark.parametrize('change',['status','degree','start','end','too_many','not_repeatable'])
def test_required_or_unavailable_history_is_not_invented(store,change):
    values={'current':None} if change=='status' else {'degree':None} if change=='degree' else {'start':None} if change=='start' else {}
    history(store,**values);f=field()
    if change=='degree':f['education_schema']['degree']='required'
    elif change=='start':f['education_schema']['startDate']='required'
    elif change=='end':
        store.withdraw_history_entry(store.history_entries('education')[0]['id'])
        history(store,current=False,expected_end=None)
        f['education_schema']['endDate']='required'
    elif change in {'too_many','not_repeatable'}:
        history(store)
        if change=='too_many':f['education_schema']['maxRepeat']=1
        else:f['education_schema']['isRepeatable']=False
    with pytest.raises(Blocked):resolve(store,'jobs.ashbyhq.com',f)


@pytest.mark.parametrize('change',['withdrawn','revised','added','value','schema','ambiguous_school'])
def test_compound_answer_and_native_grant_revalidate_history(store,job,package,change):
    row=history(store);d,_=draft(store,job,package)
    f=field();answer=resolve(store,'jobs.ashbyhq.com',f)
    d.forms[RENDER]['entries'][PATH]=dict(META);d.schools={SCHOOL:{'canonical-id'}}
    proof={**d.package,'answers':[answer,*d.package['answers']]}
    d.enable(None,proof['answers'],proof)
    assert d.grants[(RENDER,PATH)]['value'][0]['canonicalSchoolId']=='canonical-id'
    if change=='withdrawn':store.withdraw_history_entry(row['id'])
    elif change=='revised':store.put_history_entry('education',{**row['values'],'degree':'Different'},source='Correction',confirmed=True,entry_id=row['id'])
    elif change=='added':history(store,institution='Other University')
    elif change=='value':answer['value']=answer['value'].replace('B.S.','Ph.D.')
    elif change=='schema':d.forms[RENDER]['entries'][PATH]['major']='excluded'
    elif change=='ambiguous_school':d.schools[SCHOOL].add('other-id')
    with pytest.raises(Blocked):d.enable(None,proof['answers'],proof)


@pytest.mark.parametrize('key,value',[('schoolName','optional'),('degree','unknown'),('minRepeat',True),
                                     ('maxRepeat',0),('isRepeatable',None)])
def test_changed_native_schema_is_rejected(key,value):
    with pytest.raises(Blocked):schema({**META,key:value})


@pytest.mark.parametrize('mode,record,renderer',
    [(mode,record,'repeat') for mode in ['live','prepare','rejected_ack','unknown_control','changed_title']
     for record in ['current','completed','optional_unknown']]+
    [('live','current',renderer) for renderer in ['single','dates_only','two_rows','delete_extra','restored','delayed_attachment']]+
    [(mode,'current','repeat') for mode in ['changed_month','outside_control','attachment_rejected']])
def test_browser_compound_history_suppresses_partial_edits_and_requires_exact_ack(store,monkeypatch,mode,record,renderer):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    changes=({'current':False,'expected_end':None,'end':'2026-05'} if record=='completed' else
             {'degree':None,'field_of_study':None} if record=='optional_unknown' else {})
    history(store,**changes);remote={};saves=[];final=[];searches=[];violations=[]
    if renderer=='two_rows':history(store,degree='M.S.',start='2026-01')
    metadata={**META}
    if renderer=='single':metadata['isRepeatable']=False
    if renderer=='dates_only':metadata.update(degree='excluded',major='excluded')
    job={'id':digest(REQ),'url':f'https://jobs.ashbyhq.com/acme/{REQ}','host':'jobs.ashbyhq.com',
         'company':'Acme','title':'Software Engineer Intern Summer 2027','description':'Build Python software.',
         'location':'San Francisco, United States','source':'fixture'}
    store.upsert_job(job);documents=AshbyDocuments(store,'acme',REQ,RENDER,DEFINITION)
    action='44444444-4444-4444-8444-444444444444'
    expected=json.loads(resolve(store,'jobs.ashbyhq.com',{**field(),'education_schema':schema(metadata)})['value'])
    for item in expected:item['canonicalSchoolId']='canonical-id'
    if renderer=='restored':remote[PATH]=expected
    def official():
        f=form(remote);f['sections'][0]['fieldEntries'].append({'id':RENDER+'_'+PATH,'field':metadata,
            'fieldValue':{'value':remote[PATH]} if PATH in remote else None})
        f['formControls']=[{'identifier':action,'title':'Submit','__typename':'FormControl'}]
        return documents.form(f)
    native={'operationName':'ApiSubmitSingleApplicationFormAction',
            'query':(Path(__file__).parent/'fixtures/ashby-submit-single-application-form.graphql').read_text(),
            'variables':{'organizationHostedJobsPageName':'acme','jobPostingId':REQ,'recaptchaToken':'synthetic-token',
                         'formRenderIdentifier':RENDER,'formDefinitionIdentifier':DEFINITION,'actionIdentifier':action}}
    variables={'organizationHostedJobsPageName':'acme','formRenderIdentifier':RENDER,'formDefinitionIdentifier':DEFINITION}
    def date(key):
        return '<div id="'+PATH+'-'+key+'"><select><option value="">Month...</option>'+''.join(f'<option value="{m}">{calendar.month_name[m]}</option>' for m in range(1,13))+'</select><select><option value="">Year...</option>'+''.join(f'<option value="{y}">{y}</option>' for y in [2025,2026,2028])+'</select></div>'
    education='<div data-field-path="'+PATH+'" data-field-entry-id="'+RENDER+'_'+PATH+'"><label class="ashby-application-form-question-title">Education History</label><div class="ashby-application-form-input-education-entry"><input role="combobox" class="ashby-application-form-input-autocomplete" aria-controls="schools"><div role="listbox" id="schools" hidden><div role="option">'+SCHOOL+'</div></div><input id="'+PATH+'-degree"><input id="'+PATH+'-major">'+date('startDate')+date('endDate')+'<input type="checkbox" id="'+PATH+'-isCurrent"></div></div>'
    education=education.replace('class="ashby-application-form-input-education-entry"','data-education-row class="ashby-application-form-input-education-entry"')
    if renderer=='single':education=education.replace('class="ashby-application-form-input-education-entry"','class="single-education"')
    if renderer=='dates_only':
        for key in ('degree','major'):education=education.replace('<input id="'+PATH+'-'+key+'">','')
    education=education.replace('<input type="checkbox"','<button type="button" class="ashby-application-form-input-education-entry-delete">Delete</button><input type="checkbox"')
    education=education[:-6]+'<button type="button" class="ashby-application-form-input-education-add">Add Education</button></div>'
    if mode=='unknown_control':education=education.replace('</div></div>','<input name="unreviewed"></div></div>')
    if mode=='changed_title':education=education.replace('>Education History<','>Other history<')
    if mode=='changed_month':education=education.replace('<option value="1">January','<option value="0">January')
    if mode=='outside_control':education=education[:-6]+'<input name="unreviewed-outside-row"></div>'
    inputs=''.join(f'<div data-field-path="{p}" data-field-entry-id="{RENDER}_{p}"><label>{title}<input id="{p}" type="{kind}" required></label></div>' for p,title,kind in [('_systemfield_name','Name','text'),('_systemfield_email','Email','email')])
    html='<html><body><h1>Software Engineer Intern Summer 2027</h1><p>Build Python software in San Francisco, United States.</p><form>'+inputs+education+'<label>Resume<input type="file" name="resume" required></label><button type="submit">Submit Application</button></form><script>'+'''
const endpoint='/api/non-user-graphql', query=QUERY, vars=VARS, path=PATH;
const send=(operationName,query,variables)=>fetch(endpoint,{method:'POST',body:JSON.stringify({operationName,query,variables})}).catch(()=>{});
send('ApiJobPosting','query ApiJobPosting { jobPosting { id } }',{organizationHostedJobsPageName:'acme',jobPostingId:REQ});
for(const id of ['_systemfield_name','_systemfield_email']) { const el=document.getElementById(id); el.onblur=()=>send('ApiSetFormValue',query,{...vars,path:id,value:el.value}); }
const root=document.querySelector('[data-field-path="'+path+'"]'), template=root.querySelector('[data-education-row]').cloneNode(true);
let items=INITIAL,timer;const save=()=>send('ApiSetFormValue',query,{...vars,path,value:items});
const changed=()=>{clearTimeout(timer);timer=setTimeout(save,200)};
function bind(row,index) {
 const school=row.querySelector('[role=combobox]'), menu=row.querySelector('[role=listbox]');menu.id='schools'+index;school.setAttribute('aria-controls',menu.id);school.value=items[index].schoolName||'';
 school.oninput=()=>{delete items[index].schoolName;delete items[index].canonicalSchoolId;changed();menu.hidden=false;send('ApiSearchSchoolByCanonicalName',SCHOOLQUERY,{name:school.value,organizationHostedJobsPageName:'acme'})};
school.onkeydown=e=>{if(e.key==='Escape')menu.hidden=true};
menu.querySelector('[role=option]').onclick=()=>{school.value=SCHOOL;menu.hidden=true;items[index]={...items[index],schoolName:SCHOOL,canonicalSchoolId:'canonical-id'};changed()};
for(const key of ['degree','major']) {const el=row.querySelector('[id="'+path+'-'+key+'"]');if(!el)continue;el.value=items[index][key]||'';el.oninput=()=>{items[index]={...items[index],[key]:el.value};changed()};el.onblur=()=>{clearTimeout(timer);save()};}
for(const key of ['startDate','endDate']) {const selects=row.querySelector('[id="'+path+'-'+key+'"]').querySelectorAll('select');if(items[index][key]){selects[0].value=Number(items[index][key].slice(5,7));selects[1].value=items[index][key].slice(0,4)}for(const el of selects)el.onchange=()=>{if(selects[0].value&&selects[1].value){items[index]={...items[index],[key]:selects[1].value+'-'+selects[0].value.padStart(2,'0')+'-01'};changed()}};}
const current=row.querySelector('[type=checkbox]');current.checked=items[index].isCurrent||false;current.onchange=e=>{items[index]={...items[index],isCurrent:e.target.checked};if(items[index].isCurrent)delete items[index].endDate;changed()};
row.querySelector('button').onclick=()=>{items.splice(index,1);row.remove();changed()};
}
bind(root.querySelector('[data-education-row]'),0);
for(let i=1;i<items.length;i++){const row=template.cloneNode(true);root.insertBefore(row,root.lastElementChild);bind(row,i)}
root.lastElementChild.onclick=()=>{items.push({});const row=template.cloneNode(true);root.insertBefore(row,root.lastElementChild);bind(row,items.length-1);changed()};
document.querySelector('form').onsubmit=async e=>{e.preventDefault();await fetch(endpoint,{method:'POST',body:JSON.stringify(NATIVE)});document.body.innerHTML='Thank you for applying. Your application was received.'};
''' .replace('INITIAL',json.dumps(expected if renderer=='restored' else [{},{}] if renderer=='delete_extra' else [{}])).replace('SCHOOLQUERY',json.dumps(SCHOOL_QUERY)).replace('QUERY',json.dumps(QUERY)).replace('VARS',json.dumps(variables)).replace('PATH',json.dumps(PATH)).replace('REQ',json.dumps(REQ)).replace('SCHOOL',json.dumps(SCHOOL)).replace('NATIVE',json.dumps(native))+'</script></body></html>'
    html=documents.html(html)
    if renderer=='delayed_attachment':
        html=html.replace("await send({operationName:'ApiSetFormValueToFile'", "await new Promise(r=>setTimeout(r,500));await send({operationName:'ApiSetFormValueToFile'")
        html=re.sub(r"(?m)^send\('ApiJobPosting'.*$",lambda match:'setTimeout(()=>{'+match[0]+'},250);',html)
    with Browser(store) as browser:
        if renderer=='delayed_attachment':
            wait=browser.page.wait_for_load_state
            monkeypatch.setattr(browser.page,'wait_for_load_state',lambda state,*a,**kw:None if state=='networkidle' else wait(state,*a,**kw))
        class Route:
            def __init__(self,r):self.route=r;self.request=r.request
            def abort(self):self.route.abort()
            def continue_(self,**changes):
                if (mode=='attachment_rejected' and '/api/non-user-graphql' in self.request.url
                        and json.loads(self.request.post_data or '{}').get('operationName')=='ApiSetFormValueToFile'):
                    self.route.fulfill(status=500,content_type='application/json',body='{"errors":[{"message":"Synthetic attachment failure"}]}');return
                if documents.respond(browser,self.route,changes):return
                data=json.loads(self.request.post_data);op=data['operationName']
                if op=='ApiJobPosting':result={'data':{'jobPosting':{'id':REQ,'applicationForm':official()}}}
                elif op=='ApiSearchSchoolByCanonicalName':
                    searches.append(data['variables']['name'])
                    if any(v['path']==PATH for v in saves):violations.append('Post-ACK school lookup')
                    result={'data':{'result':[{'id':'canonical-id','name':SCHOOL}]}}
                elif op=='ApiSetFormValue':
                    if renderer=='delayed_attachment' and documents.file is None:violations.append('Text save before attachment acknowledgement')
                    if store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone():violations.append('Save after final intent')
                    v=data['variables'];saves.append(v)
                    if v['path']==PATH:assert v['value']==expected
                    remote[v['path']]=v['value'];result={'data':{'setFormValue':official()}}
                    if mode=='rejected_ack' and v['path']==PATH:result['errors']=[{'message':'Synthetic reject'}]
                else:
                    assert data==native and remote[PATH]==expected
                    assert store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
                    final.append(data);result={'data':{'submitApplicationFormAction':{'applicationFormResult':{'__typename':'FormSubmitSuccess'},'messages':{}}}}
                self.route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:browser._route(Route(route))
        browser.page.route('**/*',serve)
        if mode in {'unknown_control','changed_title','rejected_ack','changed_month','outside_control','attachment_rejected'}:
            reason={'unknown_control':'unsupported_widget','changed_title':'form_changed','rejected_ack':'ashby_draft_save_failed','changed_month':'unsupported_widget','outside_control':'unsupported_widget','attachment_rejected':'upload_verification_failed'}[mode]
            with pytest.raises(Blocked,match=reason):browser.apply(job,live=True)
            assert not final
            assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
        else:assert browser.apply(job,live=mode=='live')==('confirmed' if mode=='live' else 'prepared')
    assert bool(final)==(mode=='live')
    assert not violations
    if mode not in {'unknown_control','changed_title','changed_month','outside_control'}:
        # One discovery search, restoration to blank, then native selection.
        if renderer=='repeat':assert searches==([SCHOOL,''] if mode=='attachment_rejected' else [SCHOOL,'',SCHOOL])
        assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='ashby_school_lookup'").fetchone()[0]==1
    assert all(v['value']==expected for v in saves if v['path']==PATH)
    if mode=='live':assert len([v for v in saves if v['path']==PATH])==1
    if mode=='prepare':assert not saves and not documents.operations
    if mode=='attachment_rejected':assert not saves
