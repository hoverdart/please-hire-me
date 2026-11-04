"""Workday prompt markup, including portal menus without combobox roles."""
import pytest

from hireme.browser_agent import AdaptiveBrowser
from hireme.browser_observation import observe
from hireme.util import Blocked


PROMPT='''<h1>My Information</h1>
<div data-automation-id=formField-source>
 <label for=source>How Did You Hear About Us?*</label>
 <div data-automation-id=multiSelectContainer>
  <input id=source placeholder=Search aria-required=true aria-describedby=instruction
    data-uxi-widget-type=selectinput data-uxi-multiselect-id=source-widget>
  <div data-automation-id=promptSelectionLabel></div>
  <div id=instruction data-automation-id=promptAriaInstruction aria-hidden=true>0 items selected</div>
 </div>
</div>
<div data-automation-id=formField-other><label for=other>Other prompt</label>
 <input id=other data-uxi-widget-type=selectinput data-uxi-multiselect-id=other-widget></div>
<div id=menu hidden></div>
<script>
const source=document.getElementById('source'),menu=document.getElementById('menu');
function option(label,group,widget='source-widget') {
 const item=document.createElement('div');item.setAttribute('role','option');
 item.setAttribute('aria-label',label+' not checked');
 item.innerHTML='<div data-uxi-multiselect-id="'+widget+'" data-uxi-multiselectlistitem-hassidecharm="'+group+'"><div data-automation-id=promptOption>'+label+'</div></div>';
 item.onclick=()=>{if(group){menu.replaceChildren(option('Company Website',false));}
 else {const chip=document.createElement('div');chip.setAttribute('data-automation-id','selectedItem');
 chip.setAttribute('role','option');chip.setAttribute('aria-label',label+', press delete to clear value.');
 chip.innerHTML='<p data-automation-id=promptOption>'+label+'</p>';
 document.querySelector('[data-automation-id=multiSelectContainer]').append(chip);
 source.id='source-after-selection';
 document.getElementById('instruction').textContent='1 item selected';source.value='';menu.hidden=true;}};
 return item;
}
source.onclick=()=>{menu.hidden=false;menu.replaceChildren(option('Website',true),option('Employee Referral',true),option('Unrelated value',false,'other-widget'));};
</script>'''


def test_workday_date_navigation_help_does_not_change_field_identity(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<div data-automation-id=formField-startDate><label for=month>From*</label>
            <input id=month type=number data-automation-id=dateSectionMonth-input aria-describedby=helpText-month value=7>
            <span id=helpText-month>The current value is 07/2024. Use arrow keys.</span></div>''')
        before=observe(browser.page)['fields'][0]
        browser.page.locator('#month').fill('8')
        browser.page.locator('#helpText-month').evaluate('(node)=>node.textContent="The current value is 08/2024. Use arrow keys."')
        after=observe(browser.page)['fields'][0]
        assert before['ui_id']==after['ui_id']
        assert not after.get('help_text') and after['value']=='8'


def test_workday_agreement_retains_surrounding_terms_and_links(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<section data-fkit-id=termsAndConditions--null>
            <p>Each candidate may maintain one authentic profile.</p>
            <p>I accept <a href="https://employer.invalid/privacy">Applicant Privacy Policy</a> and
            <a href="https://employer.invalid/terms">Terms of Service</a>.</p>
            <div data-automation-id=formField-acceptTermsAndAgreements>
              <label for=agree>By selecting the checkbox, you agree to our Terms and Conditions and Applicant Privacy Policy.*</label>
              <input id=agree type=checkbox required></div></section>
            <footer><a href="https://unrelated.invalid">Unrelated footer</a></footer>''')
        before=observe(browser.page)['fields'][0]
        assert 'one authentic profile' in before['help_text']
        assert [link['url'] for link in before['help_links']]==['https://employer.invalid/privacy','https://employer.invalid/terms']
        browser.page.locator('a[href="https://employer.invalid/terms"]').evaluate('(node)=>node.href="https://employer.invalid/new-terms"')
        after=observe(browser.page)['fields'][0]
        from hireme.field_context import field_context
        assert field_context('synthetic.wd12.myworkdayjobs.com',before)!=field_context('synthetic.wd12.myworkdayjobs.com',after)


@pytest.mark.parametrize('message,automation,live,is_error',[
    ('a'*64+'.pdf successfully uploaded','ariaLiveMessage','polite',False),
    ('a'*64+'.pdf could not be uploaded','ariaLiveMessage','polite',True),
    ('a'*64+'.pdf not successfully uploaded','ariaLiveMessage','polite',True),
    ('a'*64+'.pdf successfully uploaded','validationError','polite',True),
    ('a'*64+'.pdf successfully uploaded','ariaLiveMessage','assertive',True),
])
def test_workday_upload_success_announcement_does_not_block_review(store,message,automation,live,is_error):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('<h1>Review</h1><div role=alert data-automation-id="'+automation+'" aria-live="'+live+'">'+message+'</div>')
        assert observe(browser.page)['errors']==([message] if is_error else [])


def test_nested_workday_prompt_uses_its_own_options_and_verifies_selected_label(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content(PROMPT)
        initial=observe(browser.page)['fields'][0]
        assert initial['type']=='combobox' and initial['workday_prompt']
        assert not initial.get('help_text')
        browser._control_ui(initial).click()
        opened=observe(browser.page)['fields']
        field=opened[0]
        assert field['option_groups']==['Website','Employee Referral']
        assert field['options']==[]
        assert opened[1]['options']==['Unrelated value']
        with pytest.raises(Blocked,match='option_mismatch'):
            browser._prompt_option(field,'Employee Referral')
        browser._prompt_option(field,'Website',group=True).click()
        field=observe(browser.page)['fields'][0]
        assert field['option_groups']==[] and field['options']==['Company Website']
        browser._fill_ui({'field':field,'value':'Company Website'})
        assert browser._read_value(field)=='Company Website'
        final=observe(browser.page)['fields'][0]
        assert final['ui_id']==initial['ui_id']
        assert final['options']==[] and final['value']=='Company Website'


@pytest.mark.parametrize('source,expected',[('portal:Acme','Website'),('gh:Acme','Website'),('user',None),('simplify:internships',None)])
def test_website_source_category_requires_known_employer_board_provenance(source,expected):
    from hireme.answers import _discovery_answer
    answer=_discovery_answer('How Did You Hear About Us?*',['Website','Employee Referral'],{'source':source,'company':'Acme'})
    assert (answer['value'] if answer else None)==expected


def test_visible_menu_is_not_shared_with_unrelated_comboboxes(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<div data-automation-id=formField-country>
          <label>Country*</label><button role=combobox aria-label="Country Canada Required">Canada</button></div>
          <label>State<button role=combobox>Select one</button></label>
          <div role=listbox><div role=option>United States</div></div>''')
        browser.page.get_by_role('combobox').first.focus()
        fields=observe(browser.page)['fields']
        assert fields[0]['label']=='Country*'
        assert fields[0]['options']==['United States']
        assert fields[1]['options']==[]


def test_question_label_outside_wrapper_wins_over_current_choice_aria_label(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<label for=question>Are you legally authorized to work in this country?*</label>
          <div data-automation-id=formField-question><button id=question type=button aria-haspopup=listbox
          aria-label=" Select One Required">Select One</button></div>''')
        field=observe(browser.page)['fields'][0]
        assert field['label']=='Are you legally authorized to work in this country?*'
        assert field['required']


def test_rich_questionnaire_legend_wins_over_select_one_state(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<div data-automation-id=formField-question><fieldset><legend>
          <div data-automation-id=richText><p>Do you require sponsorship?<abbr title=required>*</abbr></p></div>
          </legend><button type=button id=primaryQuestionnaire--question aria-haspopup=listbox
          aria-label=" Select One Required">Select One</button></fieldset></div>''')
        field=observe(browser.page)['fields'][0]
        assert field['label']=='Do you require sponsorship?*' and field['required']


def test_custom_native_radio_and_checkbox_stay_observable_with_transparent_input(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<fieldset><legend>Previously employed?*</legend>
          <label>Yes<input type=radio name=prior value=yes style="opacity:0"></label>
          <label>No<input type=radio name=prior value=no style="opacity:0"></label></fieldset>
          <label>I have a preferred name<input type=checkbox style="opacity:0"></label>
          <div hidden><label>Hidden<input type=checkbox></label></div>
          <div style="opacity:0"><label>Hidden conditional consent<input type=checkbox required></label></div>''')
        fields=observe(browser.page)['fields']
        assert [f['type'] for f in fields]==['radio','checkbox']
        assert fields[0]['options']==['Yes','No']
        browser._fill_ui({'field':fields[0],'value':'No'})
        assert browser._read_value(fields[0])=='No'


def test_approved_visible_option_is_filled_before_opening_another_menu(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        fill={'id':'fill:one','kind':'fill','label':'Fill one','field':{'options_visible':True}}
        open_other={'id':'options:two','kind':'options','label':'Inspect two'}
        assert browser._choose({},[open_other,fill])==fill


@pytest.mark.parametrize('fact,options,expected',[
    ('Mobile',['Home','Home Cellular'],'Home Cellular'),
    ('Mobile',['Landline','Mobile'],'Mobile'),
    ('Landline',['Home','Home Cellular'],'Home'),
])
def test_phone_device_type_uses_an_explicit_confirmed_fact(store,job,fact,options,expected):
    from hireme.answers import resolve
    store.put_facts({'phone_type':fact})
    field={'label':'Phone Device Type*','type':'combobox','required':True,'options':options,'value':''}
    answer=resolve(store,job['host'],field,context=job)
    assert answer['value']==expected and answer['provenance']['fact_key']=='phone_type'


def test_calling_code_requires_both_confirmed_number_and_country(store,job):
    from hireme.answers import resolve,validate_package
    store.put_facts({'phone':'+1 555 123 4567','country':'United States'})
    field={'label':'Country Phone Code*','type':'combobox','required':True,'options':[
        'Canada (+1)','United States of America (+1)','United Kingdom (+44)'],'value':''}
    answer=resolve(store,job['host'],field,context=job)
    assert answer['value']=='United States of America (+1)'
    evidence=answer['provenance']['phone_country']
    assert evidence=={'phone_revision':store.facts()['phone']['revision'],'country_revision':store.facts()['country']['revision']}
    # The number alone does not establish a country for this shared code.
    store.put_facts({'country':'Canada'})
    with pytest.raises(Blocked):resolve(store,job['host'],field,context=job)


def test_split_workday_phone_number_keeps_its_confirmed_national_component(store,job):
    from hireme.answers import resolve
    store.put_facts({'phone':'+1 555 123 4567'})
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<label>Country Phone Code<input id=phoneNumber--countryPhoneCode></label>
          <label>Phone Number*<input id=phoneNumber--phoneNumber required></label>''')
        field=observe(browser.page)['fields'][1]
        assert field['phone_component']=='national'
        assert resolve(store,job['host'],field,context=job)['value']=='5551234567'


def test_virtualized_school_prompt_searches_confirmed_institution_before_other(store,job,monkeypatch):
    store.put_facts({'school':'University of Testville'})
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.job={**job,'answer_scope':job['host']}
        browser.preparing=False;browser.provider=None;browser.opened=set();browser.searched=set();browser.option_cache={}
        monkeypatch.setattr(browser,'_check',lambda:None)
        browser.page.set_content('''<h1>My Experience</h1>
          <div data-automation-id=formField-school><label for=school>School or University*</label>
          <div data-automation-id=multiSelectContainer><input id=school data-uxi-widget-type=selectinput
            data-uxi-multiselect-id=school-widget aria-required=true></div></div>
          <div role=option><div data-uxi-multiselect-id=school-widget><p data-automation-id=promptOption>Alpha University</p></div></div>
          <div role=option><div data-uxi-multiselect-id=school-widget><p data-automation-id=promptOption>Other</p></div></div>''')
        choices,answers,documents,pending=browser._choices(observe(browser.page))
        assert len(choices)==1 and choices[0]['kind']=='search'
        assert choices[0]['answer']['value']=='University of Testville'
        assert choices[0]['answer']['provenance']['fact_key']=='school'
        assert not answers and not pending


def test_prefilled_workday_chip_is_validated_without_opening_virtualized_choices(store,job,monkeypatch):
    store.put_facts({'phone':'+1 555 123 4567','country':'United States'})
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.job={**job,'answer_scope':job['host']}
        browser.preparing=False;browser.provider=None;browser.opened=set();browser.searched=set();browser.option_cache={}
        monkeypatch.setattr(browser,'_check',lambda:None)
        browser.page.set_content('''<h1>My Information</h1>
          <div data-automation-id=formField-phone><label for=code>Country Phone Code*</label>
          <div data-automation-id=multiSelectContainer><input id=code data-uxi-widget-type=selectinput
            data-uxi-multiselect-id=code-widget aria-required=true>
          <div data-automation-id=selectedItem role=option><p data-automation-id=promptOption>United States of America (+1)</p></div></div></div>''')
        choices,answers,documents,pending=browser._choices(observe(browser.page))
        assert not choices and not pending
        assert len(answers)==1 and answers[0]['value']=='United States of America (+1)'


def test_workday_fkit_rows_and_empty_history_actions_have_scoped_identity(store):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('''<div data-automation-id=applyFlowMyExpPage>
          <section><h2>Work Experience</h2><button>Add</button>
          <div data-fkit-id=workExperience-8--null><label>Job Title<input id=workExperience-8--jobTitle></label></div>
          <div data-fkit-id=workExperience-13--null><label>Job Title<input id=workExperience-13--jobTitle></label></div></section>
          <section><h2>Education</h2><div data-fkit-id=education-6--null>
          <label>School or University*<input id=education-6--schoolName></label></div></section>
          <section><h2>Resume/CV</h2><div data-automation-id=formField- data-fkit-id=resumeAttachments--attachments>
          <label>Upload a file (5MB max)<input type=file></label></div></section>
          <section><h2>Social Network URLs</h2><label>LinkedIn<input></label></section></div>''')
        view=observe(browser.page);fields=view['fields']
        assert [(f.get('section'),f.get('section_entry')) for f in fields[:3]]==[('employment',0),('employment',1),('education',0)]
        assert view['actions'][0]['section']=='employment'
        assert fields[3]['label']=='Resume/CV' and not fields[3].get('section')
        from hireme.browser import _document_kind
        assert _document_kind(fields[3])=='resume'
        assert not fields[4].get('section')


def test_workday_prompt_search_executes_enter_without_saving_an_answer(store,job,monkeypatch):
    from hireme.draft_journal import DraftJournal
    from hireme.browser_agent_protocol import origin
    store.put_facts({'school':'Testville University'})
    html='''<h1>My Experience</h1><div data-automation-id=formField-school>
      <label for=school>School*</label><div data-automation-id=multiSelectContainer>
      <input id=school data-uxi-widget-type=selectinput data-uxi-multiselect-id=school-widget enterkeyhint=search>
      </div></div><div id=menu></div><script>
      school.onkeydown=e=>{if(e.key==='Enter'){window.searchExecuted=true;menu.innerHTML=
      '<div role=option><div data-uxi-multiselect-id=school-widget><p data-automation-id=promptOption>Testville University</p></div></div>';}};
      </script>'''
    with AdaptiveBrowser(store,test_url=job['url']) as browser:
        browser.page.route(job['url'],lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        browser.page.goto(job['url'])
        browser.job={**job,'answer_scope':job['host']};browser.preparing=False;browser.provider=None;browser.questions=[]
        browser.binding='a'*64;browser.allowed_origin=origin(job['url']);browser.searched=set();browser.operation_timeout=4
        browser.staged_documents={};browser.journal=DraftJournal(store,job);browser.memory=[]
        browser.resume={**dict(store.db.execute('SELECT * FROM documents').fetchone()),
            'field':{'label':'Selected resume','type':'file','required':False,'options':[]}}
        monkeypatch.setattr(browser,'_check',lambda:None)
        view=observe(browser.page);field=view['fields'][0]
        choice=browser._lookup_choice(field,[])
        assert field['search_on_enter'] and choice['answer']['value']=='Testville University'
        browser._perform(choice,view,[],[])
        assert browser.page.evaluate('!!window.searchExecuted')
        assert observe(browser.page)['fields'][0]['options']==['Testville University']
        assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


def test_workday_month_padding_is_verified_as_the_same_confirmed_month(store,job):
    from hireme.workday_history import put
    put(store,'employment',{'organization':'Fixture Co','title':'Engineer','start':'2025-07'},source='fixture',confirmed=True)
    with AdaptiveBrowser(store,test_url=job['url']) as browser:
        browser.page.set_content('''<div data-fkit-id=workExperience-8--null>
          <label>From*<input id=month data-automation-id=dateSectionMonth-input
            oninput="this.value=String(Number(this.value))"></label></div>''')
        field=observe(browser.page)['fields'][0]
        answer=browser._resolve(field,[])
        assert answer['value']=='07'
        browser._fill_ui(answer)
        assert browser.page.locator('#month').input_value()=='7'
        assert browser._matches(answer)
        browser.page.locator('#month').fill('8')
        assert not browser._matches(answer)


@pytest.mark.parametrize('count,needs_add',[(2,True),(4,False)])
def test_add_another_inserts_only_missing_confirmed_history_rows(store,job,monkeypatch,count,needs_add):
    from hireme.workday_history import put
    for i in range(4):put(store,'employment',{'organization':'Fixture Co','title':'Engineer '+str(i)},source='fixture',confirmed=True)
    with AdaptiveBrowser(store,test_url=job['url']) as browser:
        browser.job={**job,'answer_scope':job['host']};browser.provider=None;browser.preparing=False
        browser.opened=set();browser.searched=set();browser.option_cache={}
        monkeypatch.setattr(browser,'_check',lambda:None)
        rows=''.join('<div data-fkit-id=workExperience-'+str(i+8)+'--null><label>Job Title*<input value="Engineer '+str(i)+'"></label></div>' for i in range(count))
        browser.page.set_content('<h1>My Experience</h1><div data-automation-id=applyFlowMyExpPage><section><h2>Work Experience</h2>'+rows+'<button>Add Another</button></section></div>')
        choices,*_=browser._choices(observe(browser.page))
        assert bool([c for c in choices if c['kind']=='add']) is needs_add
