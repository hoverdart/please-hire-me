"""Real isolated browsers against a synthetic, stateful multi-step employer."""
import hashlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from hireme.browser_agent import AdaptiveBrowser
from hireme.browser_observation import observe
from hireme.util import Blocked,digest


TITLE='Software Engineer Intern Summer 2027'
NAME='Test Person'


@pytest.fixture
def employer(store):
    state={'mode':'normal','records':[],'saved':{},'file':None,'finals':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,body,status=200,ctype='text/html'):
            raw=body.encode() if isinstance(body,str) else body
            self.send_response(status);self.send_header('Content-Type',ctype);self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/thanks':
                return self.respond(f'<h1>Your application has been received.</h1><h2>{TITLE}</h2>')
            if path=='/posting':
                if state['mode'] in {'single','redirect'}:
                    return self.respond(f'''<h1>{TITLE}</h1><p>Acme · Build Python and TypeScript software in San Francisco, United States.</p>
                      <form action=/submit method=post enctype=multipart/form-data>
                      <label>Full name<input name=name required></label><label>Email<input name=email type=email required></label>
                      <label>Resume<input name=resume type=file required></label><button>Submit application</button></form>''')
                if state['mode']=='ambiguous':
                    return self.respond(f'''<h1>{TITLE}</h1><h2>Acme</h2><p>Build Python and TypeScript software in San Francisco, United States.</p>
                      <a href=/apply/info>Apply Manually</a><a href=/apply/info?resume=1>Continue Application</a>''')
                if state['mode']=='entry_session':
                    return self.respond(f'''<h1>{TITLE}</h1><h2>Acme</h2><p>Build Python and TypeScript software in San Francisco, United States.</p>
                      <button onclick="fetch('/initialize',{{method:'POST',body:'{{}}'}}).then(()=>location.href='/apply/info')">Apply Manually</button>''')
                return self.respond(f'''<h1>{TITLE}</h1><h2>Acme</h2><p>Build Python and TypeScript software in San Francisco, United States.</p>
                  <form><label>Email<input name=newsletter type=email></label></form>
                  <a href=/apply/info>Apply for this job</a>''')
            if path=='/lookup':return self.respond(json.dumps(['United States']),ctype='application/json')
            if path=='/apply/info':
                extra=''
                if state['mode']=='unknown':extra='<label>What is your private clearance code?<input name=clearance required></label>'
                menu_content=('<input type=search aria-label="Search countries">' if state['mode']=='search' else
                    '<button type=button role=option>United States</button><button type=button role=option>Canada</button>')
                search_script=('''menu.querySelector('input').oninput=async()=>{
                    const response=await fetch('/lookup?q='+encodeURIComponent(menu.querySelector('input').value));
                    const choices=await response.json();menu.querySelectorAll('[role=option]').forEach(n=>n.remove());
                    choices.forEach(value=>{const option=document.createElement('button');option.type='button';option.setAttribute('role','option');
                    option.textContent=value;option.onclick=()=>{country.textContent=value;menu.hidden=true;
                    document.getElementById('conditional').innerHTML='<label>City<input id=city name=city required></label>';};menu.append(option);});};''' if state['mode']=='search' else '')
                html=f'''<h1>My Information</h1><h2>{TITLE}</h2><p>Acme</p>
                  <form id=application><label>Full name<input id=name name=name required></label>
                  <label>Email<input id=email name=email type=email required></label>
                  <label id=country-label>Country*</label><button id=country type=button role=combobox
                    aria-labelledby=country-label aria-controls=country-options aria-required=true>Select one</button>
                  <div id=country-options role=listbox hidden>{menu_content}</div>
                  <div id=conditional></div>{extra}<button id=next type=button>Save and continue</button></form>
                  <script>
                  const country=document.getElementById('country'),menu=document.getElementById('country-options');
                  country.onclick=()=>menu.hidden=!menu.hidden;
                  {'country.textContent="United States";document.getElementById("conditional").innerHTML="<label>City<input id=city name=city required></label>";' if state['mode']=='prefilled_combo' else ''}
                  {search_script}
                  menu.querySelectorAll('[role=option]').forEach(e=>e.onclick=()=>{{country.textContent=e.textContent;menu.hidden=true;
                    document.getElementById('conditional').innerHTML='<label>City<input id=city name=city required></label>';}});
                  for(const input of document.querySelectorAll('input'))input.onchange=()=>setTimeout(()=>fetch('/autosave',{{method:'POST',body:JSON.stringify({{field:input.name,value:input.value}})}}),{400 if state['mode']=='debounce' else 0});
                  document.getElementById('next').onclick=async()=>{{const result=await fetch('/save/info',{{method:'POST',body:JSON.stringify({{
                    name:document.getElementById('name').value,email:document.getElementById('email').value,
                    country:country.textContent,city:document.getElementById('city')?.value}})}});
                    if(result.ok)location.href='/apply/experience';}};
                  </script>'''
                if state['mode']=='delayed_navigation':
                    html='<header><div role=combobox></div></header><script>setTimeout(()=>{document.body.innerHTML='+json.dumps(html.split('<script>')[0])+''';
                    const country=document.getElementById('country'),menu=document.getElementById('country-options');
                    country.onclick=()=>menu.hidden=!menu.hidden;
                    menu.querySelectorAll('[role=option]').forEach(e=>e.onclick=()=>{country.textContent=e.textContent;menu.hidden=true;document.getElementById('conditional').innerHTML='<label>City<input id=city name=city required></label>';});
                    document.getElementById('next').onclick=async()=>{await fetch('/save/info',{method:'POST',body:JSON.stringify({name:document.getElementById('name').value,email:document.getElementById('email').value,country:country.textContent,city:document.getElementById('city').value})});location.href='/apply/experience';};
                    },1200)</script>'''
                return self.respond(html)
            if path=='/apply/experience':
                history=''
                if state['mode']=='history':
                    history='''<section data-automation-id=educationSection><h3>Education</h3><div data-automation-id=education-0>
                    <label>School or University<input name=school required></label>
                    <label>Degree<select name=degree required><option value="">Select one</option><option value=bs>Bachelor of Science</option></select></label>
                    <label>From<input name=startmonth type=number data-automation-id=dateSectionMonth-input required min=1 max=12></label>
                    <label>From<input name=startyear data-automation-id=dateSectionYear-input required></label></div></section>'''
                return self.respond(f'''<h1>My Experience</h1><h2>{TITLE}</h2><p>Acme</p>
                  {history}
                  <label>Resume<input id=resume name=resume type=file required></label><div id=attached></div>
                  <button id=next type=button>Save and continue</button><script>
                  const file=document.getElementById('resume');file.onchange=async()=>{{const name=file.files[0].name;
                    const result=await fetch('/upload',{{method:'POST',body:file.files[0]}});const data=await result.json();
                    if(data.id){{localStorage.setItem('resume-id',data.id);document.getElementById('attached').textContent=name;file.remove();}}}};
                  document.getElementById('next').onclick=async()=>{{await fetch('/save/experience',{{method:'POST',body:JSON.stringify({{resume:localStorage.getItem('resume-id'),education:Object.fromEntries(Array.from(document.querySelectorAll('section input,section select')).map(e=>[e.name,e.value]))}})}});location.href='/apply/review';}};
                  </script>''')
            if path=='/apply/review':
                saved=state['saved'];email=saved.get('email','')
                return self.respond(f'''<h1>Review</h1><h2>{TITLE}</h2><p>Acme</p><p>{NAME} {email}</p>
                  <button id=submit type=button>Submit application</button><script>
                  document.getElementById('submit').onclick=async()=>{{const response=await fetch('/submit',{{method:'POST',body:JSON.stringify({{job:'synthetic-123',resume:localStorage.getItem('resume-id')}})}});
                    if(response.ok)document.body.innerHTML='<h1>Your application has been received.</h1><h2>{TITLE}</h2><p>Acme</p>';}};
                  </script>''')
            self.respond('Not found',404)
        def do_POST(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
            db=sqlite3.connect(store.root/'ledger.sqlite3')
            intents=db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0]
            drafts=db.execute("SELECT COUNT(*) FROM draft_operations WHERE state='intent'").fetchone()[0];db.close()
            state['records'].append({'path':self.path,'body':raw,'intents':intents,'drafts':drafts})
            if self.path=='/upload':
                state['file']=raw
                return self.respond(json.dumps({'id':'synthetic-resume'}),ctype='application/json')
            if self.path=='/save/info':
                state['saved']=json.loads(raw)
                if state['mode']=='save_error':return self.respond(json.dumps({'errors':['Could not save']}),ctype='application/json')
                if not all(state['saved'].get(k) for k in ('name','email','city','country')):
                    return self.respond(json.dumps({'errors':['Missing fields']}),400,'application/json')
            if self.path=='/submit':
                state['finals']+=1
                if state['mode']=='single':
                    return self.respond(f'<h1>Your application has been received.</h1><h2>{TITLE}</h2>')
                if state['mode']=='redirect':
                    self.send_response(303);self.send_header('Location','/thanks');self.end_headers();return
                payload={} if state['mode']=='missing_ack' else {'status':'submitted','application_id':'synthetic-application'}
                return self.respond(json.dumps(payload),ctype='application/json')
            self.respond(json.dumps({'saved':True}),ctype='application/json')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    job={'id':digest(base+'/posting'),'url':base+'/posting','host':'127.0.0.1','company':'Acme','title':TITLE,
        'location':'San Francisco, United States','description':'Build Python and TypeScript software.','source':'fixture'}
    store.upsert_job(job);store.put_facts({'country':'United States','city':'Berkeley'})
    store.update_settings({'remote_drafts':True})
    yield base,job,state
    server.shutdown();server.server_close();thread.join(2)


def browser_for(store,employer,**kwargs):
    browser=AdaptiveBrowser(store,test_url=employer[0],**kwargs)
    browser.receipt_timeout=1;browser.operation_timeout=3
    return browser


def test_multi_step_ui_applies_with_custom_choices_conditional_fields_and_exact_pdf(store,employer):
    base,job,state=employer
    with browser_for(store,employer) as browser:
        try:assert browser.apply(job)=='confirmed'
        except Exception as error:
            error.add_note(json.dumps({'url':browser.page.url,'memory':browser.memory,
                'requests':[r['path'] for r in state['records']],
                'operations':[tuple(r) for r in store.db.execute('SELECT operation,state FROM draft_operations')]}))
            raise
        with pytest.raises(Blocked):browser.apply(job)
    assert state['saved']=={'name':NAME,'email':'test@candidate.invalid','country':'United States','city':'Berkeley'}
    document=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    assert hashlib.sha256(state['file']).hexdigest()==document['hash']
    assert state['finals']==1
    assert all(record['drafts']>=1 for record in state['records'])
    assert next(r for r in state['records'] if r['path']=='/submit')['intents']==1
    assert not any(r['intents'] for r in state['records'] if r['path']!='/submit')
    assert not store.db.execute("SELECT 1 FROM draft_operations WHERE state IN ('intent','unknown')").fetchone()
    package=json.loads(store.db.execute('SELECT package FROM applications').fetchone()[0])
    assert package['browser_agent']['complete'] and len(package['steps'])>=2
    assert any(a['field']['label']=='City' for a in package['answers'])


def test_opaque_final_ack_uses_verified_readback_without_repeating_submission(store,employer,monkeypatch):
    employer[2]['mode']='missing_ack';readbacks=[]
    def verified(agent,operation):
        assert operation.final and operation.complete
        assert agent._receipt(agent.page.locator('body').inner_text(),agent.receipt_baseline)
        assert operation.requests and all(r['ok'] for r in operation.responses.values())
        readbacks.append(agent.job['id'])
        return True
    monkeypatch.setattr('hireme.workday_browser_receipt.verify_submission',verified)
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1])=='confirmed'
        with pytest.raises(Blocked):browser.apply(employer[1])
    assert readbacks==[employer[1]['id']] and employer[2]['finals']==1
    assert not store.db.execute("SELECT 1 FROM draft_operations WHERE state IN ('intent','unknown')").fetchone()


def test_unknown_required_question_is_held_before_final_action(store,employer):
    employer[2]['mode']='unknown'
    with browser_for(store,employer) as browser:
        with pytest.raises(Blocked,match='missing_answers'):browser.apply(employer[1])
    assert employer[2]['finals']==0
    assert store.db.execute("SELECT label FROM questions WHERE resolved=0").fetchone()[0]=='What is your private clearance code?'


def test_prepare_only_sends_no_values_documents_or_native_saves(store,employer):
    with browser_for(store,employer) as browser:
        with pytest.raises(Blocked,match='browser_preparation_incomplete'):browser.apply(employer[1],live=False)
    assert employer[2]['records']==[] and employer[2]['file'] is None
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


def test_uncertain_save_survives_recovery_and_cannot_repeat_or_move_to_another_job(store,employer):
    employer[2]['mode']='save_error'
    with browser_for(store,employer) as browser:
        with pytest.raises(Blocked,match='browser_draft_uncertain'):browser.apply(employer[1])
        before=list(employer[2]['records']);store.recover()
        with pytest.raises(Blocked,match='browser_draft_uncertain'):browser.apply(employer[1])
        alternate={**employer[1],'id':'different-job','url':employer[0]+'/another'}
        store.upsert_job(alternate)
        with pytest.raises(Blocked,match='browser_draft_uncertain'):browser.apply(alternate)
    assert employer[2]['records']==before and employer[2]['finals']==0


def test_receipt_without_employer_ack_is_unknown_and_not_retried(store,employer):
    employer[2]['mode']='missing_ack'
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1])=='unknown'
        before=list(employer[2]['records'])
        with pytest.raises(Blocked):browser.apply(employer[1])
    assert employer[2]['records']==before and employer[2]['finals']==1


@pytest.mark.parametrize('mode',['single','redirect'])
def test_native_multipart_form_keeps_file_local_until_durable_final_intent(store,employer,mode):
    employer[2]['mode']=mode
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1])=='confirmed'
    record,=employer[2]['records']
    assert record['path']=='/submit' and record['intents']==1 and record['drafts']==1
    row=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    assert (store.root/'documents'/row['filename']).read_bytes() in record['body']


def test_single_page_preparation_never_selects_or_transmits_a_file(store,employer):
    employer[2]['mode']='single'
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1],live=False)=='prepared'
        assert browser.page.locator('input[type=file]').evaluate('e=>e.files.length')==0
    assert employer[2]['records']==[]
    package=json.loads(store.db.execute('SELECT package FROM applications').fetchone()[0])
    assert package['browser_agent']['complete'] and not package['browser_agent']['uploaded_documents']


def test_clean_step_checkpoint_resumes_without_repeating_earlier_remote_saves(store,employer,monkeypatch):
    with browser_for(store,employer) as browser:
        save=browser._save_partial
        def stop(*args):
            result=save(*args)
            if browser.completed_steps:raise RuntimeError('Synthetic stop after committed step')
            return result
        monkeypatch.setattr(browser,'_save_partial',stop)
        with pytest.raises(RuntimeError):browser.apply(employer[1])
    assert not store.db.execute("SELECT 1 FROM draft_operations WHERE state IN ('intent','unknown')").fetchone()
    store.put_facts({'github':'https://github.com/synthetic-approved-applicant'})
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1])=='confirmed'
    assert len([r for r in employer[2]['records'] if r['path']=='/save/info'])==1
    assert employer[2]['finals']==1


def test_read_only_entry_checkpoint_reenters_via_public_controls_before_first_save(store,employer,monkeypatch):
    with browser_for(store,employer) as browser:
        with pytest.raises(Blocked,match='browser_preparation_incomplete'):browser.apply(employer[1],live=False)
    assert not employer[2]['records']
    with browser_for(store,employer) as browser:
        restore=browser._restore_package;targets=[]
        def restored():
            target=restore();targets.append(target);return target
        monkeypatch.setattr(browser,'_restore_package',restored)
        assert browser.apply(employer[1])=='confirmed'
    assert targets==[None]
    assert len([r for r in employer[2]['records'] if r['path']=='/save/info'])==1


def test_changed_fact_rebuilds_unsaved_preview_without_reusing_stale_answers(store,employer):
    with browser_for(store,employer) as browser:
        with pytest.raises(Blocked,match='browser_preparation_incomplete'):browser.apply(employer[1],live=False)
    assert not employer[2]['records']
    store.put_facts({'first_name':'Updated','full_name':'Updated Person'})
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1])=='confirmed'
    assert employer[2]['saved']['name']=='Updated Person'
    assert employer[2]['finals']==1


def test_acknowledged_unsaved_entry_reenters_without_repeating_saved_steps(store,employer,monkeypatch):
    employer[2]['mode']='entry_session'
    # The synthetic server models Workday's unsaved client application entry.
    employer[1]['host']='synthetic.wd12.myworkdayjobs.com'
    store.upsert_job(employer[1])
    with browser_for(store,employer) as browser:
        save=browser._save_partial
        def stop(*args):
            save(*args)
            raise RuntimeError('Synthetic stop after transient application entry')
        monkeypatch.setattr(browser,'_save_partial',stop)
        with pytest.raises(RuntimeError):browser.apply(employer[1])
    assert [r['path'] for r in employer[2]['records']]==['/initialize']
    assert store.db.execute("SELECT operation,state FROM draft_operations").fetchone()[:]==('ui:entry','acknowledged')
    with browser_for(store,employer) as browser:
        restore=browser._restore_package;targets=[]
        from hireme import icims
        from playwright.sync_api import Error
        enter_content=icims.enter_content;navigation_races=[]
        def frame_read(page,**kwargs):
            if browser.operation is not None and not navigation_races:
                navigation_races.append(True)
                raise Error('Locator.evaluate_all: Execution context was destroyed, most likely because of a navigation')
            return enter_content(page,**kwargs)
        monkeypatch.setattr(icims,'enter_content',frame_read)
        def restored():
            result=restore();targets.append(result);return result
        monkeypatch.setattr(browser,'_restore_package',restored)
        assert browser.apply(employer[1])=='confirmed'
    assert targets==[None]
    assert navigation_races==[True]
    assert len([r for r in employer[2]['records'] if r['path']=='/save/info'])==1
    assert employer[2]['finals']==1


def test_other_employers_keep_acknowledged_entry_checkpoints(store,employer,monkeypatch):
    employer[2]['mode']='entry_session'
    with browser_for(store,employer) as browser:
        save=browser._save_partial
        def stop(*args):
            save(*args)
            raise RuntimeError('Synthetic interruption')
        monkeypatch.setattr(browser,'_save_partial',stop)
        with pytest.raises(RuntimeError):browser.apply(employer[1])
    with browser_for(store,employer) as browser:
        restore=browser._restore_package;targets=[]
        def restored():
            result=restore();targets.append(result);return result
        monkeypatch.setattr(browser,'_restore_package',restored)
        assert browser.apply(employer[1])=='confirmed'
    assert targets==[employer[0]+'/apply/info']
    assert len([r for r in employer[2]['records'] if r['path']=='/initialize'])==1


def test_planner_uses_only_observed_navigation_actions(store,employer):
    employer[2]['mode']='ambiguous';calls=[]
    def planner(view,actions,history):
        calls.append((view['headings'],actions,history))
        return next(a['id'] for a in actions if a['label']=='Continue Application')
    with browser_for(store,employer,planner=planner) as browser:
        assert browser.apply(employer[1])=='confirmed'
    assert calls and calls[0][0]==[TITLE,'Acme']
    assert all(set(a)=={'id','kind','label'} for a in calls[0][1])


def test_invented_planner_action_cannot_click_or_send_anything(store,employer):
    employer[2]['mode']='ambiguous'
    with browser_for(store,employer,planner=lambda *args:'invented-selector-or-action') as browser:
        with pytest.raises(Blocked,match='provider_invalid_output'):browser.apply(employer[1])
    assert not employer[2]['records']


def test_debounced_field_saves_remain_inside_the_original_source_bound_operation(store,employer):
    employer[2]['mode']='debounce'
    with browser_for(store,employer) as browser:
        assert browser.apply(employer[1])=='confirmed'
    saves=[r for r in employer[2]['records'] if r['path']=='/autosave']
    assert len(saves)==2 and all(r['drafts']==1 and r['intents']==0 for r in saves)


def test_changed_source_revokes_a_debounced_save_before_it_reaches_the_employer(store,employer,monkeypatch):
    employer[2]['mode']='debounce'
    with browser_for(store,employer) as browser:
        fill=browser._fill_ui
        def change_source(answer):
            fill(answer)
            store.put_facts({'full_name':'Different Confirmed Name'})
        monkeypatch.setattr(browser,'_fill_ui',change_source)
        with pytest.raises(Blocked):browser.apply(employer[1])
    assert employer[2]['records']==[] and employer[2]['finals']==0


def test_remote_draft_permission_is_checked_before_any_save(store,employer):
    store.update_settings({'remote_drafts':False})
    with browser_for(store,employer) as browser:
        with pytest.raises(Blocked,match='remote_draft_permission_required'):browser.apply(employer[1])
    assert employer[2]['records']==[]


def test_clean_upload_checkpoint_reuses_exact_acknowledged_document_after_restart(store,employer,monkeypatch):
    with browser_for(store,employer) as browser:
        save=browser._save_partial
        def stop(*args):
            result=save(*args)
            if browser.uploads:raise RuntimeError('Synthetic stop after acknowledged upload')
            return result
        monkeypatch.setattr(browser,'_save_partial',stop)
        with pytest.raises(RuntimeError):browser.apply(employer[1])
    with browser_for(store,employer) as browser:assert browser.apply(employer[1])=='confirmed'
    assert len([r for r in employer[2]['records'] if r['path']=='/upload'])==1


def test_dedicated_profile_cannot_be_reassigned_to_another_applicant(store,employer):
    browser=browser_for(store,employer);browser._binding()
    store.put_facts({'email':'another@candidate.invalid'})
    with pytest.raises(Blocked,match='browser_profile_changed'):browser._binding()


def test_search_only_combobox_uses_a_confirmed_lookup_then_exact_observed_option(store,employer):
    employer[2]['mode']='search'
    with browser_for(store,employer) as browser:assert browser.apply(employer[1])=='confirmed'
    assert employer[2]['saved']['country']=='United States'
    assert not any(r['path'].startswith('/lookup') for r in employer[2]['records'])


def test_prefilled_custom_choice_is_verified_then_closed_before_continuing(store,employer):
    employer[2]['mode']='prefilled_combo'
    with browser_for(store,employer) as browser:assert browser.apply(employer[1])=='confirmed'
    assert employer[2]['saved']['country']=='United States'


def test_navigation_waits_for_delayed_client_render_instead_of_holding_an_empty_screen(store,employer):
    employer[2]['mode']='delayed_navigation'
    with browser_for(store,employer) as browser:assert browser.apply(employer[1])=='confirmed'
    assert employer[2]['finals']==1


def test_workday_shaped_history_and_split_numeric_dates_use_confirmed_entries(store,employer):
    employer[2]['mode']='history'
    entry=store.put_history_entry('education',{'institution':'Synthetic University','degree':'Bachelor of Science','start':'2024-09'},
        source='Applicant-confirmed synthetic history',confirmed=True)
    with browser_for(store,employer) as browser:assert browser.apply(employer[1])=='confirmed'
    record=next(r for r in employer[2]['records'] if r['path']=='/save/experience')
    assert json.loads(record['body'])['education']=={'school':'Synthetic University','degree':'bs','startmonth':'9','startyear':'2024'}
    package=json.loads(store.db.execute('SELECT package FROM applications').fetchone()[0])
    history=[a for a in package['answers'] if 'history_entry_id' in a.get('provenance',{})]
    assert len(history)==4 and all(a['provenance']['history_entry_id']==entry['id'] for a in history)


def test_reinspection_detects_changed_choices_and_declaration_wording(store,employer):
    with browser_for(store,employer) as browser:
        browser.page.set_content('''<label>Country<select name=country aria-describedby=description>
            <option value=us>United States</option></select></label><p id=description>Current country</p>''')
        before=observe(browser.page)
        browser.page.locator('option').evaluate('e=>e.textContent="Canada"')
        changed=observe(browser.page)
        assert before['signature']!=changed['signature']
        assert before['fields'][0]['ui_id']==changed['fields'][0]['ui_id']
        browser.page.locator('#description').evaluate('e=>e.textContent="New declaration requiring review"')
        declaration=observe(browser.page)
        assert changed['fields'][0]['ui_id']!=declaration['fields'][0]['ui_id']


def test_adaptive_handshake_upload_requires_observed_private_visibility(store,employer):
    with browser_for(store,employer) as browser:
        browser.connection_id='handshake'
        browser.page.set_content('<form><p>Visibility: Public</p><label>Resume<input type=file></label></form>')
        view=observe(browser.page)
        with pytest.raises(Blocked,match='connection_document_visibility_review'):
            browser._check_upload_policy(view['fields'][0],view)
        assert browser.page.locator('input').evaluate('e=>e.files.length')==0
        browser.page.locator('p').evaluate('e=>e.textContent="Visibility: Private"')
        view=observe(browser.page)
        browser._check_upload_policy(view['fields'][0],view)


def test_personal_browser_context_and_tabs_are_untouched(store,employer):
    from playwright.sync_api import sync_playwright
    ready=threading.Event();finished=threading.Event();errors=[]
    def personal_session():
        try:
            with sync_playwright() as playwright:
                personal=playwright.chromium.launch(headless=True)
                context=personal.new_context();tab=context.new_page();tab.set_content('<h1>Unrelated personal tab</h1>')
                second=context.new_page();second.set_content('<h1>Another personal tab</h1>')
                ready.set()
                assert finished.wait(90)
                assert not tab.is_closed() and not second.is_closed() and len(context.pages)==2
                assert tab.locator('h1').inner_text()=='Unrelated personal tab'
                assert second.locator('h1').inner_text()=='Another personal tab'
                personal.close()
        except BaseException as error:errors.append(error);ready.set()
    thread=threading.Thread(target=personal_session,daemon=True);thread.start()
    try:
        assert ready.wait(15) and not errors
        with browser_for(store,employer) as browser:
            assert browser.apply(employer[1])=='confirmed'
    finally:
        finished.set();thread.join(15)
    assert not thread.is_alive() and not errors
