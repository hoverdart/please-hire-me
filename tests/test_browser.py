import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from hireme.browser import Browser,submission_receipt
from hireme.util import Blocked,digest

@pytest.fixture
def ats(request):
    records=[];html=(Path(__file__).parent/'fixtures/application.html').read_bytes()
    if getattr(request,'param',False) is True:
        html=html.replace(b'<button type="submit">',b'<label for="cover_letter">Cover Letter*</label><input id="cover_letter" name="cover_letter" type="file" required><button type="submit">')
    if getattr(request,'param',None)=='otp':
        html=html.replace(b'e.preventDefault();let f=',b'e.preventDefault();if(!document.getElementById("security_code")){let p=document.createElement("p");p.textContent="A verification code was sent to test@candidate.invalid. To submit your application, enter the 8-character code.";let l=document.createElement("label");l.htmlFor="security_code";l.textContent="Security code";let c=document.createElement("input");c.id="security_code";c.required=true;e.target.append(p,l,c);return;}let f=')
    if getattr(request,'param',None) in ('otp-segmented','otp-custom-receipt'):
        html=html.replace(b'e.preventDefault();let f=',b'''e.preventDefault();if(!document.getElementById("security_code")){
          let original=e.target;original.hidden=true;
          let form=document.createElement('form');form.id='verification';
          let p=document.createElement('p');p.textContent='A verification code was sent to test@candidate.invalid. To submit your application, enter the 8-character code.';
          let label=document.createElement('label');label.htmlFor='security_code';label.textContent='Security code';form.append(p,label);
          let controls=[];let button=document.createElement('button');button.type='submit';button.textContent='Submit application';button.disabled=true;
          for(let i=0;i<8;i++){let c=document.createElement('input');c.id=i===0?'security_code':'slot-'+i;c.maxLength=1;c.required=true;controls.push(c);form.append(c);
            c.addEventListener('input',()=>{if(c.value.length===1&&i<7)controls[i+1].focus();button.disabled=!controls.every(x=>x.value.length===1);});}
          form.append(button);document.body.append(form);
          form.addEventListener('submit',async event=>{event.preventDefault();if(controls.map(x=>x.value).join('')!=='ABC12345')return;
            await fetch('/submit',{method:'POST',body:new FormData(original)});document.body.innerHTML='<h1>Thank you for applying. Your application has been received.</h1>';});return;}let f=''' )
    if getattr(request,'param',None)=='otp-custom-receipt':
        html=html.replace(b'Thank you for applying. Your application has been received.',
            b'Our Talent team will carefully review your qualifications and experience. Thanks again for applying!')
    if getattr(request,'param',None)=='spam':
        html=html.replace(b"h.textContent='Thank you for applying. Your application has been received.';",
            b'h.textContent="We couldn\'t submit your application. Your application submission was flagged as possible spam.";')
    if getattr(request,'param',None)=='processing-error':
        html=html.replace(b"h.textContent='Thank you for applying. Your application has been received.';",
            b"h.textContent='Posting details. '.repeat(400)+'There was an error processing your application. Please try again.';")
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(html)
        def do_POST(self):
            records.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200);self.end_headers();self.wfile.write(b'OK')
    server=ThreadingHTTPServer(('127.0.0.1',0),H)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    yield 'http://127.0.0.1:'+str(server.server_address[1]),records
    server.shutdown();server.server_close()


def local_job(store,ats):
    url=ats[0]+'/application';j={'id':digest(url),'url':url,'host':'127.0.0.1','company':'Synthetic ATS','title':'Software Engineer Intern Summer 2027','location':'San Francisco, United States','description':'Build Python and TypeScript software','source':'fixture'}
    store.upsert_job(j);return j


@pytest.mark.parametrize('text',[
    'Thanks again for applying!',
    'Our Talent team will carefully review your qualifications and experience.',
    'Thank you for taking the time to apply. Please complete the application.',
    'There was an error processing your application. Please try again.',
])
def test_custom_receipt_requires_both_completed_acknowledgement_and_review(text):
    assert not submission_receipt(text)


def test_local_exact_answers_upload_confirm_and_no_retry(store,ats):
    job=local_job(store,ats)
    with Browser(store,test_url=ats[0]) as b:
        assert b.apply(job)=='confirmed'
        with pytest.raises(Blocked):b.apply(job)
    assert len(ats[1])==1
    body=ats[1][0].decode(errors='replace');assert 'Test Person' in body and 'test@candidate.invalid' in body and 'No' in body
    app=store.db.execute('SELECT * FROM applications').fetchone()
    assert app['state']=='confirmed'
    assert len(json.loads(app['package'])['answers'])==3
    assert (store.root/'screenshots'/app['screenshot']).is_file()


def test_prepare_only_never_submits(store,ats):
    with Browser(store,test_url=ats[0]) as b:assert b.apply(local_job(store,ats),live=False)=='prepared'
    assert not ats[1]


@pytest.mark.parametrize('missing_fact',[False,True])
def test_required_field_timeouts_retry_only_without_genuinely_missing_answers(store,ats,monkeypatch,missing_fact):
    from hireme.answers import resolve as real_resolve
    def resolve_field(store,host,field,*args,**kwargs):
        if field['label']=='Describe your project':raise Blocked('provider_timeout')
        if field['label']=='Unconfirmed fact':raise Blocked('missing_fact')
        return real_resolve(store,host,field,*args,**kwargs)
    monkeypatch.setattr('hireme.browser.resolve',resolve_field)
    html=(Path(__file__).parent/'fixtures/application.html').read_text()
    extra='<label>Describe your project<textarea name="project" required></textarea></label>'
    if missing_fact:extra+='<label>Unconfirmed fact<input name="unknown" required></label>'
    html=html.replace('</form>',extra+'</form>')
    job=local_job(store,ats)
    with Browser(store,test_url=ats[0]) as b:
        b.page.route(ats[0]+'/**',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
        with pytest.raises(Blocked,match='missing_answers' if missing_fact else 'provider_timeout'):b.apply(job,live=False)
    assert not ats[1]
    assert not store.db.execute('SELECT 1 FROM applications WHERE attempted IS NOT NULL').fetchone()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


def test_report_question_variants_fill_and_submit_from_confirmed_sources(store, ats):
    """Exercise snapshot -> resolution -> filling -> validation -> local receipt."""
    job=local_job(store,ats)
    store.put_facts({'phone':'+12025550123','school':'Confirmed University','country':'United States'})
    store.update_settings({'contextual_preferences':True})
    label='I understand that all employees for this position will be expected to be available during coordination hours (Mon-Fri, 9am-3pm Pacific Time).'
    additions=f'''<label>Phone<input name="phone" type="tel" maxlength="10" required></label>
    <label>School Name<input name="school" required></label>
    <label>In which of the following employment eligible countries are you seeking to work, if hired?<select name="country" required><option value="">Choose</option><option>United States</option><option>Canada</option></select></label>
    <label>Are you legally authorized to work in that country?<select name="auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
    <label>Are you legally authorized to work in the country for which you are applying?*<select name="generic_auth" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>
    <label>What is your preferred programming language for your interviews (you can change this later)?<select name="language" required><option value="">Choose</option><option>Java</option><option>Python</option></select></label>
    <label>{label}*<input type="checkbox" name="coordination" required></label>'''
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('</form>',additions+'</form>')
    with Browser(store,test_url=ats[0]) as b:
        b.context.route(ats[0]+'/**',lambda route:route.fulfill(body=html,content_type='text/html') if route.request.method=='GET' else route.continue_())
        b.page.goto(job['url'])
        reviewed=next(f for f in b._snapshot() if 'coordination hours' in f['label'])
        reviewed={**reviewed,'label':label}
        qid=store.ask(job['id'],job['host']+'|'+store.company(job['company']),label,reviewed['options'],field=reviewed,context=job)
        store.answer_question(qid,'Yes')
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1
    app=store.db.execute('SELECT * FROM applications WHERE job_id=?',(job['id'],)).fetchone()
    values={a['field']['label']:a['value'] for a in json.loads(app['package'])['answers']}
    assert values['Phone']=='2025550123' and values['School Name']=='Confirmed University'
    assert values['Are you legally authorized to work in that country?']=='Yes'
    assert values['What is your preferred programming language for your interviews (you can change this later)?']=='Python'
    assert store.db.execute('SELECT count(*) FROM model_requests').fetchone()[0]==0


def test_false_required_graduation_confirmation_stops_as_eligibility(store, ats):
    job=local_job(store,ats)
    additions='<label>I confirm that my graduation date will be either Fall 2026 or Spring 2027*<select name="graduation" required><option value="">Choose</option><option>Yes</option><option>No</option></select></label>'
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('</form>',additions+'</form>')
    with Browser(store,test_url=ats[0]) as b:
        b.context.route(ats[0]+'/**',lambda route:route.fulfill(body=html,content_type='text/html'))
        with pytest.raises(Blocked,match='graduation_mismatch'):b.apply(job)
    assert not ats[1]
    assert store.db.execute('SELECT count(*) FROM questions').fetchone()[0]==0
    assert store.db.execute('SELECT count(*) FROM applications').fetchone()[0]==0


@pytest.mark.parametrize('ats',['processing-error'],indirect=True)
def test_post_submit_generic_error_is_uncertain_with_tail_evidence_and_no_replay(store,ats):
    job=local_job(store,ats)
    with Browser(store,test_url=ats[0]) as b:
        assert b.apply(job)=='unknown'
        with pytest.raises(Blocked):b.apply(job)
    app=store.db.execute('SELECT * FROM applications').fetchone()
    assert app['state']=='unknown' and 'There was an error processing your application' in app['confirmation']
    assert len(app['confirmation'])<4100 and len(ats[1])==1
    response=json.loads(store.db.execute("SELECT detail FROM events WHERE kind='submission_response' AND subject=?",(app['id'],)).fetchone()[0])
    assert response=={'stage':'after_submit','host':'127.0.0.1','path':'/submit','method':'POST','status':200}


def test_late_response_metadata_stays_bound_to_original_intent_and_excludes_query(store):
    class Request:
        method='POST';url='https://job-boards.greenhouse.io/submit?private=secret'
    class Response:
        request=Request();status=503
    b=Browser(store);b.aid='next-application';b.current_host='jobs.lever.co'
    b.submission_requests[b._request_key(Response.request)]='original-application'
    b._upload_response(Response())
    row=store.db.execute("SELECT subject,detail FROM events WHERE kind='submission_response'").fetchone()
    assert row['subject']=='original-application'
    assert json.loads(row['detail'])=={'stage':'after_submit','host':'job-boards.greenhouse.io','path':'/submit','method':'POST','status':503}
    assert not b.submission_requests


def test_unknown_field_blocks_before_click(store,ats):
    job=local_job(store,ats)
    with Browser(store,test_url=ats[0]) as b:
        original=b._snapshot
        def injected():
            fields=original();fields.append({'index':99,'indices':[99],'label':'Invent a SAT score','type':'text','required':True,'options':[],'maxlength':-1,'value':''});return fields
        b._snapshot=injected
        with pytest.raises(Blocked,match='SAT'):b.apply(job)
    assert not ats[1]
    assert store.db.execute('SELECT count(*) FROM questions').fetchone()[0]==1


def test_route_denies_private_or_unapproved_write(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda h:h!='127.0.0.1')
    b=Browser(store);b.current_host='jobs.lever.co'
    class Request:
        def __init__(self,url,method='GET',post_data=None):self.url=url;self.method=method;self.post_data=post_data
    class Route:
        def __init__(self,request):self.request=request;self.action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    for url,method in [('https://127.0.0.1/secret','GET'),('https://attacker.invalid/pixel?personal=data','GET'),('https://jobs.lever.co/acme/submit','POST')]:
        r=Route(Request(url,method));b._route(r);assert r.action=='abort'
    b.attempted=True
    r=Route(Request('https://jobs.lever.co/acme/submit','POST'));b._route(r);assert r.action=='continue'


def test_login_captcha_and_personal_tab_isolation(store,ats):
    job=local_job(store,ats)
    with Browser(store,test_url=ats[0]) as b:
        b.page.goto(ats[0])
        b.page.set_content('<label>Password<input type=password></label>')
        with pytest.raises(Blocked,match='account'):b._guard(job)
        b.context.route('https://captcha.invalid/**',lambda route:route.abort())
        b.page.set_content('<iframe src="https://captcha.invalid/bframe"></iframe>',wait_until='domcontentloaded')
        with pytest.raises(Blocked,match='captcha'):b._guard(job)
        # Only a private persistent context is opened; no CDP attachment to everyday Chrome.
        assert len(b.context.pages)==1
    assert not ats[1]


def test_required_transcript_blocks_until_imported_then_attaches(store,ats,tmp_path):
    import hashlib
    from pypdf import PdfWriter
    from hireme.onboarding import import_resume
    job=local_job(store,ats)
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('</form>','<label>Transcript<input type="file" name="transcript" required></label></form>')
    def serve_form(route):
        if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
        else:route.fallback()
    with Browser(store,test_url=ats[0]) as b:
        b.page.route(ats[0]+'/**',serve_form)
        with pytest.raises(Blocked,match='Transcript'):b.apply(job)
    assert not ats[1]
    transcript=tmp_path/'transcript.pdf';writer=PdfWriter();writer.add_blank_page(width=612,height=792)
    with transcript.open('wb') as f:writer.write(f)
    imported=import_resume(store,transcript,'transcript')
    with Browser(store,test_url=ats[0]) as b:
        b.page.route(ats[0]+'/**',serve_form)
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1
    package=json.loads(store.db.execute('SELECT package FROM applications').fetchone()[0])
    doc=next(d for d in package['documents'] if d['kind']=='transcript')
    assert doc['hash']==imported['hash']==hashlib.sha256(transcript.read_bytes()).hexdigest()


def test_cdn_assets_hydrate_form_before_answering_and_submit(store,ats,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    job=local_job(store,ats)
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    script='setTimeout(()=>{document.body.innerHTML='+json.dumps(original.split('<body>')[1].split('</body>')[0].split('<script>')[0])+';document.body.removeAttribute("aria-busy");document.querySelector("form").onsubmit=async e=>{e.preventDefault();await fetch("/submit",{method:"POST",body:new FormData(e.target)});document.body.textContent="Thank you for applying. Your application has been received."};},350);'
    shell='<html><head><link rel="stylesheet" href="https://job-boards.cdn.greenhouse.io/assets/test.css"><script src="https://cdn.ashbyprd.com/frontend_non_user/test.js"></script></head><body aria-busy="true">Loading application…</body></html>'
    with Browser(store,test_url=ats[0]) as b:
        def resources(route):
            if route.request.url==ats[0]+'/application':return route.fulfill(status=200,content_type='text/html',body=shell)
            if route.request.url.endswith('test.css'):body='h1 {color:rgb(12,34,56)}';ctype='text/css'
            elif route.request.url.endswith('test.js'):body=script;ctype='text/javascript'
            else:return route.fallback()
            class Gate:
                request=route.request
                def abort(self):route.abort()
                def continue_(self):route.fulfill(status=200,content_type=ctype,body=body)
            b._route(Gate())
        b.page.route('**/*',resources)
        original_fill=b._fill
        def check_style(answer):
            assert b.page.locator('h1').evaluate('(e)=>getComputedStyle(e).color')=='rgb(12, 34, 56)'
            original_fill(answer)
        b._fill=check_style
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1
    assert not store.db.execute('SELECT 1 FROM questions WHERE resolved=0').fetchone()


def test_required_cdn_and_upload_metadata_reads_allowed_but_writes_denied(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='job-boards.greenhouse.io'
    class Request:
        def __init__(self,url,method='GET'):self.url=url;self.method=method;self.post_data='{}'
    class Route:
        def __init__(self,url,method='GET'):self.request=Request(url,method);self.action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    for url in ['https://job-boards.cdn.greenhouse.io/assets/entry.css','https://cdn.ashbyprd.com/frontend_non_user/.vite/manifest.json','https://cdn.lever.co/fonts/font.woff','https://s9-recruiting.cdn.greenhouse.io/logo.png','https://boards.greenhouse.io/uncacheable_attributes/presigned_fields']:
        r=Route(url);b._route(r);assert r.action=='continue'
    for url in ['https://job-boards.cdn.greenhouse.io.attacker.invalid/entry.css','https://attacker.invalid/pixel','https://cdn.ashbyprd.com/submit']:
        r=Route(url,'POST');b._route(r);assert r.action=='abort'


def test_realistic_profile_wording_and_writing_sample_reuse_submit_without_questions(store,ats,monkeypatch):
    store.put_facts({'school':'University of California, Berkeley','degree':'B.S.','graduation':'2028-05'})
    tid=store.put_template('project','I built a Python service and measured the effect of each change.')
    class Model:
        def __init__(self,*args):pass
        def match_field(self,field,*args):return {'fact_key':None,'template_id':tid}
    monkeypatch.setattr('hireme.provider.ManagedProvider',Model)
    job={**local_job(store,ats),'source':'gh:synthetic'}
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    additions='''<label>Which college or university do you currently attend?*<select name="school" required><option value="">Select…</option><option>Harvard University</option><option>Other</option></select></label>
    <label>Degree*<select name="degree" required><option value="">Select…</option><option>Bachelor’s degree</option></select></label>
    <label>When do you expect to graduate?*<select name="graduation" required><option value="">Select…</option><option>Spring 2028</option><option>Fall 2028</option></select></label>
    <label>How did you hear about this role?*<select name="source" required><option value="">Select…</option><option>Employee Referral</option><option>University Career Center / Job Board</option></select></label>
    <label>Please give a concrete example of a successful project<textarea name="sample" required></textarea></label>'''
    html=original.replace('</form>',additions+'</form>')
    with Browser(store,test_url=ats[0]) as b:
        def serve_form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',serve_form)
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1
    body=ats[1][0].decode(errors='replace')
    for expected in ['Other','Bachelor’s degree','Spring 2028','University Career Center / Job Board','I built a Python service']:assert expected in body
    assert not store.db.execute('SELECT 1 FROM questions WHERE resolved=0').fetchone()


def test_upload_dom_changes_do_not_redirect_answers_to_stale_indices(store,ats):
    job=local_job(store,ats)
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('</body>','''<script>document.querySelector('input[type=file]').addEventListener('change',()=>{
      const sentinel=document.createElement('input');sentinel.disabled=true;sentinel.value='Do not fill this';
      document.querySelector('form').prepend(sentinel);
      const hidden=document.createElement('input');hidden.required=true;hidden.value='internal-value';hidden.setAttribute('aria-hidden','true');hidden.tabIndex=-1;
      document.querySelector('form').prepend(hidden);
    });</script></body>''')
    with Browser(store,test_url=ats[0]) as b:
        def form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',form)
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1 and b'Test Person' in ats[1][0]


def test_upload_bucket_requires_approved_file_and_response_ack(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='job-boards.greenhouse.io';b.upload_payloads={'approved-hash':b'%PDF-approved-content'}
    class Request:
        url='https://grnhse-prod-jben-us-west-2.s3.us-west-2.amazonaws.com/'
        method='POST'
        post_data_buffer=b'multipart-prefix%PDF-approved-contentmultipart-suffix'
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    r=Route();b._route(r);assert r.action=='continue'
    class Response:
        request=r.request;status=204
    b._upload_response(Response());assert 'approved-hash' in b.uploaded_files
    r.request.post_data_buffer=b'arbitrary-unapproved-file';b._route(r);assert r.action=='abort'
    r.request.url='https://attacker-bucket.s3.us-west-2.amazonaws.com/';r.request.post_data_buffer=b'%PDF-approved-content';b._route(r);assert r.action=='abort'


def test_custom_combobox_verifies_selected_option_not_empty_search_input(store,ats):
    store.put_facts({'country':'United States'})
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    html=original.replace('</form>','''<label for="country-picker">Country</label><div class="select__control">
    <span class="select__single-value">+1</span><input id="country-picker" role="combobox">
    <input type="hidden" name="country" value="Canada +1"></div>
    <div id="menu" role="listbox" hidden><div role="option" aria-selected="false">United States +1</div><div role="option" aria-selected="true">Canada +1</div></div></form>''').replace('</body>','''<script>
    const picker=document.querySelector('#country-picker'),menu=document.querySelector('#menu');
    picker.onclick=()=>{menu.hidden=false};picker.onkeydown=e=>{if(e.key==='Escape')menu.hidden=true};
    menu.querySelectorAll('[role=option]').forEach(option=>option.onclick=()=>{
      menu.querySelectorAll('[role=option]').forEach(x=>x.setAttribute('aria-selected',x===option?'true':'false'));
      document.querySelector('[name=country]').value=option.textContent;picker.value='';menu.hidden=true;
    });</script></body>''')
    with Browser(store,test_url=ats[0]) as b:
        def form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',form)
        assert b.apply(local_job(store,ats))=='confirmed'
    assert b'United States +1' in ats[1][0]


def test_acknowledged_upload_can_remove_original_file_input(store,ats):
    job=local_job(store,ats)
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('</body>','''<script>
    document.querySelector('input[type=file]').addEventListener('change',e=>{
      const name=e.target.files[0].name;const label=document.createElement('span');label.textContent=name;
      e.target.replaceWith(label);
    });</script></body>''')
    with Browser(store,test_url=ats[0]) as b:
        def form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',form)
        original=b._verify
        def ack(answers,documents,fields):
            # Equivalent to the independently tested successful upload response callback.
            b.uploaded_files.update(d['hash'] for d in documents)
            b.ashby_attached_files.update(d['hash'] for d in documents)
            return original(answers,documents,fields)
        b._verify=ack
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1


def test_phone_country_flag_verification_ignores_other_dropdown_options(store,ats):
    store.put_facts({'country':'United States'})
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    html=original.replace('</form>','''<label for="country-picker">Country</label><div class="select__control">
    <span class="select__single-value"><i class="iti__flag iti__ca"></i>+1</span>
    <input id="country-picker" role="combobox" aria-controls="country-menu"><input type="hidden" name="country" value="Canada +1"></div>
    <div id="country-menu" role="listbox" hidden><div role="option"><i class="iti__flag iti__us"></i>United States +1</div><div role="option"><i class="iti__flag iti__ca"></i>Canada +1</div></div>
    <div role="option" hidden><i class="iti__flag iti__us"></i>United States+1</div></form>''').replace('</body>','''<script>
    const picker=document.querySelector('#country-picker'),menu=document.querySelector('#country-menu');
    picker.onclick=()=>{menu.hidden=false};picker.onkeydown=e=>{if(e.key==='Escape')menu.hidden=true};
    menu.querySelectorAll('[role=option]').forEach(option=>option.onclick=()=>{
      const code=option.querySelector('i').className;document.querySelector('.select__single-value i').className=code;
      document.querySelector('[name=country]').value=option.textContent;picker.value='';menu.hidden=true;
    });</script></body>''')
    with Browser(store,test_url=ats[0]) as b:
        def form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',form)
        assert b.apply(local_job(store,ats))=='confirmed'
    assert b'United States +1' in ats[1][0]


def test_header_apply_button_does_not_compete_with_form_submit(store,ats):
    job=local_job(store,ats)
    html=(Path(__file__).parent/'fixtures/application.html').read_text().replace('<body>','<body><button type="button">Apply</button>')
    with Browser(store,test_url=ats[0]) as b:
        def form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',form)
        assert b.apply(job)=='confirmed'
    assert len(ats[1])==1


def test_dynamic_dropdown_options_do_not_change_form_contract():
    original={'label':'School','type':'combobox','required':False,'options':['A College'],'multiple':False,'maxlength':-1}
    searched={**original,'options':['University of California, Berkeley']}
    assert Browser._shape([original])==Browser._shape([searched])
    assert Browser._shape([original])!=Browser._shape([{**searched,'required':True}])
    assert Browser._shape([{**original,'type':'select'}])!=Browser._shape([{**searched,'type':'select'}])


def test_custom_multiselect_reads_selected_chip(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        b.page.goto(ats[0])
        b.page.set_content('<label for="season">Internship season</label><div><div class="select__multi-value__label">Summer 2027</div><input id="season" role="combobox"></div>')
        assert b._snapshot()[0]['value']=='Summer 2027'


def test_geocoding_endpoint_is_read_only(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='job-boards.greenhouse.io'
    class Request:
        url='https://api-geocode-earth-proxy.greenhouse.io/v1/autocomplete?text=Berkeley'
        method='GET'
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    route=Route();b._route(route);assert route.action=='continue'
    route.request.method='POST';b._route(route);assert route.action=='abort'


def test_upload_label_normalization_preserves_required_marker(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        b.page.goto(ats[0])
        b.page.set_content('<label for="cover_letter">Cover Letter*</label><input id="cover_letter" type="file">')
        field=b._snapshot()[0]
        assert field['label']=='Cover letter' and field['required'] is True


def test_upload_wrapper_required_marker_is_not_hidden_attach_label(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        b.page.goto(ats[0])
        b.page.set_content('<div class="file-upload"><div>Cover Letter*</div><div><label for="cover_letter">Attach</label><input id="cover_letter" type="file"></div></div>')
        assert b._snapshot()[0]['required'] is True


@pytest.mark.parametrize('ats',[True],indirect=True)
def test_required_cover_letter_is_generated_uploaded_and_confirmed(store,ats,monkeypatch):
    store.update_settings({'tailored_writing':True,'cover_letters':True})
    store.put_template('experience','I built Python services and tested their production behavior.')
    class Model:
        def __init__(self,*args,**kwargs):pass
        def draft_answer(self,label,choices,context,maxlength):
            return {'answer':'I built Python services and tested their behavior.\n\nI enjoy working on reliable tools.\n\nI would like to apply that experience in this role.','sentence_ids':[choices[0]['id']]}
    monkeypatch.setattr('hireme.provider.ManagedProvider',Model)
    with Browser(store,test_url=ats[0]) as b:assert b.apply(local_job(store,ats))=='confirmed'
    assert len(ats[1])==1
    app=store.db.execute('SELECT * FROM applications').fetchone()
    docs=json.loads(app['package'])['documents']
    letter=next(d for d in docs if d['kind']=='cover_letter')
    assert letter['generated'] and letter['hash'].encode() in ats[1][0]


@pytest.mark.parametrize('ats',['otp','otp-segmented','otp-custom-receipt'],indirect=True)
@pytest.mark.parametrize('screenshot_failure',[False,True])
def test_gmail_code_continues_the_same_application_without_model_access(store,ats,monkeypatch,screenshot_failure):
    store.update_settings({'gmail_verification':True})
    class Mailbox:
        def __init__(self,s):self.store=s
        def find_code(self,company,since,aid,length):
            assert company=='Synthetic ATS' and length==8
            return 'ABC12345'
    monkeypatch.setattr('hireme.gmail.GmailClient',Mailbox)
    with Browser(store,test_url=ats[0]) as b:
        original=b.page.screenshot
        def screenshot(**kwargs):
            if screenshot_failure and not str(kwargs['path']).endswith('-before.jpg'):raise TimeoutError('slow screenshot')
            return original(**kwargs)
        monkeypatch.setattr(b.page,'screenshot',screenshot)
        assert b.apply(local_job(store,ats))=='confirmed'
    assert len(ats[1])==1
    app=store.db.execute('SELECT * FROM applications').fetchone()
    assert app['state']=='confirmed'
    assert store.db.execute('SELECT attempts,state FROM verification_challenges').fetchone()[0]==1
    assert 'ABC12345' not in app['package'] and 'ABC12345' not in app['confirmation']
    assert 'ABC12345' not in str([tuple(r) for r in store.db.execute('SELECT * FROM events')])


@pytest.mark.parametrize('ats',['otp'],indirect=True)
def test_without_gmail_connection_verification_is_held_and_not_retried(store,ats):
    store.update_settings({'gmail_verification':True})
    with Browser(store,test_url=ats[0]) as b:
        assert b.apply(local_job(store,ats))=='awaiting_verification'
        with pytest.raises(Blocked):b.apply(local_job(store,ats))
    assert not ats[1]
    assert store.db.execute('SELECT state FROM applications').fetchone()[0]=='awaiting_verification'


@pytest.mark.parametrize('reason',['model_budget_exhausted','provider_rate_limited','cycle_timeout'])
def test_model_budget_and_rate_limit_stop_before_employer_write(store,ats,monkeypatch,reason):
    class Model:
        calls=0
        def __init__(self,*args):pass
        def match_field(self,*args,**kwargs):Model.calls+=1;raise Blocked(reason)
    monkeypatch.setattr('hireme.provider.ManagedProvider',Model)
    with Browser(store,test_url=ats[0]) as browser:
        original=browser._snapshot
        def injected():
            fields=original();fields.append({'index':99,'indices':[99],'label':'Unknown personal fact','type':'text','required':True,'options':[],'maxlength':-1,'value':''})
            if reason=='model_budget_exhausted':fields.append({**fields[-1],'label':'Another unanswered fact','index':100,'indices':[100]})
            return fields
        browser._snapshot=injected
        with pytest.raises(Blocked,match=reason):browser.apply(local_job(store,ats))
    assert not ats[1]
    if reason=='model_budget_exhausted':
        assert store.db.execute('SELECT count(*) FROM questions WHERE resolved=0').fetchone()[0]==2
        assert Model.calls==1
    else:assert not store.db.execute('SELECT 1 FROM questions').fetchone()


def test_ashby_autosave_is_suppressed_without_blocking_local_form(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com'
    class Request:
        url='https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiSetFormValue'
        method='POST'
        post_data=json.dumps({'operationName':'ApiSetFormValue','query':'mutation ApiSetFormValue { setFormValue { id } }'})
    class Route:
        request=Request()
        action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    r=Route();b._route(r)
    assert r.action=='abort' and not b.denied_write
    r.request.post_data=json.dumps({'operationName':'SubmitApplication','query':'mutation SubmitApplication { submitApplication { id } }'})
    b._route(r)
    assert r.action=='abort' and b.denied_write
    assert b.denied_request=={'host':'jobs.ashbyhq.com','path':'/api/non-user-graphql','method':'POST','operation':'SubmitApplication'}


def test_ashby_hydration_autosave_does_not_prevent_preparing_form(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    url='https://jobs.ashbyhq.com/acme/synthetic-req/application'
    job={'id':__import__('hireme.util',fromlist=['digest']).digest(url),'url':url,
         'host':'jobs.ashbyhq.com','company':'Acme','title':'Software Engineer Intern Summer 2027',
         'location':'San Francisco, United States','description':'Build Python software.','source':'ash:synthetic'}
    store.upsert_job(job)
    html=(Path(__file__).parent/'fixtures/application.html').read_text()
    html=html.replace('</body>', '''<script>
      fetch('/api/non-user-graphql?op=ApiSetFormValue', {method:'POST',
        body:JSON.stringify({operationName:'ApiSetFormValue',
          query:'mutation ApiSetFormValue { setFormValue { id } }',variables:{value:null}})
      }).catch(()=>{});
      </script></body>''')
    with Browser(store) as b:
        def serve(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:b._route(route)
        b.page.route('**/*',serve)
        original_verify=b._verify
        def verify_with_synthetic_upload(answers,documents,fields):
            # This fixture uses native local file selection; S3 routing is tested separately.
            b.uploaded_files.update(d['hash'] for d in documents)
            b.ashby_attached_files.update(d['hash'] for d in documents)
            return original_verify(answers,documents,fields)
        b._verify=verify_with_synthetic_upload
        assert b.apply(job,live=False)=='prepared'
        assert not b.denied_write
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='draft_autosave_suppressed'").fetchone()[0]>=1


def test_greenhouse_checkbox_choices_keep_question_and_choose_only_one(store,ats):
    job=local_job(store,ats)
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    html=original.replace('</form>', '''<div>
    <label><input type="checkbox" name="authorization[]" required id="auth-yes" description="Are you currently authorized to work in the United States?" value="1">Yes</label>
    <label><input type="checkbox" name="authorization[]" required id="auth-no" description="Are you currently authorized to work in the United States?" value="2">No</label>
    </div></form>''')
    with Browser(store,test_url=ats[0]) as b:
        b.page.route(ats[0]+'/**',lambda route:route.fulfill(status=200,content_type='text/html',body=html) if route.request.method=='GET' else route.fallback())
        assert b.apply(job,live=False)=='prepared'
        assert b.page.locator('#auth-yes').is_checked()
        assert not b.page.locator('#auth-no').is_checked()
        groups=[f for f in b._snapshot() if f['type']=='checkbox-group']
        assert len(groups)==1 and groups[0]['value']=='Yes'


def test_ashby_checkbox_fieldset_retains_question_and_independent_option_names(store,ats):
    store.put_facts({'onsite':'Yes'})
    store.update_settings({'contextual_preferences':True})
    job={**local_job(store,ats),'source':'ash:synthetic'}
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    fields=''
    for title,options in [('Which office are you applying to? (Select both if appropriate)',['San Francisco HQ - 181 Fremont Street','New York City - 1 World Trade']),('How did you hear about Koah?',['LinkedIn','Indeed','Search engine','Other'])]:
        fields+='<fieldset class="ashby-application-form-input-checkbox-group"><label class="ashby-application-form-question-title _required_test">'+title+'</label>'
        for i,option in enumerate(options):
            ident=str(len(fields))+str(i)
            fields+=f'<label for="{ident}">{option}</label><input type="checkbox" id="{ident}" name="{option}">'
        fields+='</fieldset>'
    html=original.replace('</form>',fields+'</form>')
    with Browser(store,test_url=ats[0]) as b:
        b.page.route(ats[0]+'/**',lambda r:r.fulfill(status=200,content_type='text/html',body=html) if r.request.method=='GET' else r.fallback())
        assert b.apply(job,live=False)=='prepared'
        groups=[f for f in b._snapshot() if f['type']=='checkbox-group']
        assert [f['value'] for f in groups]==['San Francisco HQ - 181 Fremont Street','Other']
        assert all(f['required'] for f in groups)


def test_batched_ashby_autosave_is_aborted_but_submission_batch_stays_blocked(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com'
    class Request:
        url='https://jobs.ashbyhq.com/api/non-user-graphql';method='POST'
        post_data=json.dumps([{'operationName':'ApiSetFormValue','query':'mutation ApiSetFormValue { setFormValue { id } }'}, {'operationName':'Read','query':'query Read { id }'}])
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    route=Route();b._route(route)
    assert route.action=='abort' and not b.denied_write
    route.request.post_data=json.dumps([{'operationName':'Submit','query':'mutation Submit { submit { id } }'}])
    b._route(route)
    assert b.denied_write and b.denied_request['operations']==['Submit']


def test_ashby_upload_handle_only_for_approved_document_bytes(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com';b.upload_payloads={'approved':b'pdf-bytes'}
    class Request:
        url='https://jobs.ashbyhq.com/api/non-user-graphql';method='POST'
        post_data=json.dumps({'operationName':'ApiCreateFileUploadHandle','query':'mutation ApiCreateFileUploadHandle { createFileUploadHandle { handle } }','variables':{'filename':'approved.pdf','contentType':'application/pdf','contentLength':9}})
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    route=Route();b._route(route);assert route.action=='continue'
    data=json.loads(route.request.post_data);data['variables']['filename']='unknown.pdf';route.request.post_data=json.dumps(data)
    b._route(route);assert route.action=='abort' and b.denied_write


def test_ashby_s3_upload_requires_approved_bytes_and_exact_destination(store,monkeypatch):
    from hireme.browser import ASHBY_UPLOAD_HOST
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com';b.upload_payloads={'approved':b'approved-pdf-content'}
    class Request:
        url='https://'+ASHBY_UPLOAD_HOST+'/';method='POST';post_data_buffer=b'form approved-pdf-content ending'
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    route=Route();b._route(route);assert route.action=='continue'
    # Chromium omits multipart file bytes from the intercepted request body.
    route.request.post_data_buffer=b'filename=approved.pdf; signed form fields'
    b._route(route);assert route.action=='continue'
    route.request.post_data_buffer=b'unapproved';b._route(route);assert route.action=='abort'
    route.request.url='https://'+ASHBY_UPLOAD_HOST+'.attacker.invalid/'
    route.request.post_data_buffer=b'approved-pdf-content';b._route(route);assert route.action=='abort'


def test_ashby_attachment_requires_handle_from_approved_successful_upload(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com'
    b.ashby_file_handles={'known':'approved'}
    class Request:
        url='https://jobs.ashbyhq.com/api/non-user-graphql';method='POST'
        post_data=json.dumps({'operationName':'ApiSetFormValueToFile','query':'mutation ApiSetFormValueToFile { setFormValueToFile { id } }','variables':{'fileHandle':'known'}})
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    r=Route();b._route(r);assert r.action=='abort'
    b.uploaded_files.add('approved');b.denied_write=False;b._route(r);assert r.action=='continue'
    class Response:
        request=Request();status=200
        def json(self):return {'data':{'setFormValueToFile':{'id':'form'}}}
    b._upload_response(Response());assert 'approved' in b.ashby_attached_files


def test_ashby_yesno_buttons_preserve_required_question_and_confirmed_answer(store,ats):
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    html=original.replace('</form>', '''<div class="ashby-application-form-field-entry">
    <label class="ashby-application-form-question-title _required_test">Are you legally authorized to work in the United States?</label>
    <div class="ashby-application-form-input-yesno">
    <input type="checkbox" style="display:none" tabindex="-1">
    <button type="button" class="ashby-application-form-input-yesno-option" aria-pressed="false">Yes</button>
    <button type="button" class="ashby-application-form-input-yesno-option" aria-pressed="false">No</button>
    </div></div><script>document.querySelectorAll('.ashby-application-form-input-yesno-option').forEach(e=>e.onclick=()=>{e.parentElement.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===e)))})</script></form>''')
    with Browser(store,test_url=ats[0]) as b:
        b.page.route(ats[0]+'/**',lambda r:r.fulfill(status=200,content_type='text/html',body=html) if r.request.method=='GET' else r.fallback())
        assert b.apply(local_job(store,ats),live=False)=='prepared'
        f=next(f for f in b._snapshot() if f['type']=='yesno')
        assert f['required'] and f['value']=='Yes' and f['options']==['Yes','No']


def test_submission_waits_for_delayed_ats_confirmation(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        b.page.goto(ats[0]);b.current_host='127.0.0.1';b.attempted=True
        b.page.evaluate("setTimeout(()=>{document.body.innerHTML='Thank you for applying. Your application has been received.'},2500)")
        text=b._wait_submission_outcome(local_job(store,ats))
        assert 'received' in text


@pytest.mark.parametrize('ats',['spam'],indirect=True)
def test_explicit_ats_spam_rejection_is_not_uncertain_or_retried(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        job=local_job(store,ats)
        assert b.apply(job)=='not_submitted'
        with pytest.raises(Blocked):b.apply(job)
    app=store.db.execute('SELECT state,confirmation FROM applications').fetchone()
    assert app['state']=='not_submitted' and 'possible spam' in app['confirmation']


def test_greenhouse_filename_alone_does_not_prove_upload(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        b.current_host='job-boards.greenhouse.io'
        b.page.set_content('<label for="resume">Resume/CV*</label><input id="resume" type="file"><p>approved.pdf</p>')
        fields=b._snapshot();documents=[{'field':fields[0],'hash':'approved','filename':'approved.pdf'}]
        with pytest.raises(Blocked,match='Greenhouse has not acknowledged'):b._verify([],documents,fields)
        b.uploaded_files.add('approved')
        b._verify([],documents,fields)


def test_ashby_radio_question_inherits_required_heading(store,ats):
    with Browser(store,test_url=ats[0]) as b:
        b.page.set_content('<fieldset class="_fieldEntry_x ashby-application-form-input-radio-group"><label class="ashby-application-form-question-title _required_x">Can you work in our office?</label><label><input type="radio" name="office" value="yes">Yes</label><label><input type="radio" name="office" value="no">No</label></fieldset>')
        f=b._snapshot()[0]
        assert f['required'] and f['type']=='radio' and f['label']=='Can you work in our office?'


def test_outcome_screenshot_failure_does_not_lose_confirmation(store,ats,monkeypatch):
    with Browser(store,test_url=ats[0]) as b:
        screenshot=b.page.screenshot
        def fail_after(**kwargs):
            if str(kwargs['path']).endswith('-after.jpg'):raise TimeoutError('slow screenshot')
            return screenshot(**kwargs)
        monkeypatch.setattr(b.page,'screenshot',fail_after)
        assert b.apply(local_job(store,ats))=='confirmed'
    app=store.db.execute('SELECT state,screenshot FROM applications').fetchone()
    assert app['state']=='confirmed' and app['screenshot']==''

@pytest.mark.parametrize('error,reason', [('net::ERR_CONNECTION_RESET','navigation_failed'),('net::ERR_HTTP_RESPONSE_CODE_FAILURE','posting_navigation_review'),('net::ERR_CERT_DATE_INVALID','posting_navigation_review')])
def test_initial_navigation_retries_only_known_transient_failures(store,ats,monkeypatch,error,reason):
    from playwright.sync_api import Error
    with Browser(store,test_url=ats[0]) as browser:
        def failed(*args,**kwargs):raise Error(error)
        monkeypatch.setattr(browser.page,'goto',failed)
        with pytest.raises(Blocked,match=reason):browser.apply(local_job(store,ats))
    assert not ats[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_delayed_degree_choices_are_validated_and_selected_without_raw_fact_search(store,ats):
    store.put_facts({'degree':'B.S.'})
    original=(Path(__file__).parent/'fixtures/application.html').read_text()
    html=original.replace('</form>', '''<label for="degree-picker">Degree*</label><div class="select__control">
    <span class="select__single-value"></span><input id="degree-picker" role="combobox" aria-controls="degree-menu" required>
    <input type="hidden" name="education_degree"></div><div id="degree-menu" role="listbox" hidden></div></form>''').replace('</body>', '''<script>
    const picker=document.querySelector('#degree-picker'),menu=document.querySelector('#degree-menu');
    const labels=["Bachelor's Degree","Master's Degree"];
    function options(){menu.innerHTML='';for(const label of labels){const o=document.createElement('div');o.role='option';o.textContent=label;o.setAttribute('aria-selected',document.querySelector('[name=education_degree]').value===label?'true':'false');
      o.onclick=()=>{document.querySelector('[name=education_degree]').value=label;picker.required=false;picker.value='';document.querySelector('.select__single-value').textContent=label;menu.hidden=true};menu.append(o)}menu.hidden=false}
    picker.onclick=()=>setTimeout(options,800);
    picker.oninput=()=>{menu.querySelectorAll('[role=option]').forEach(o=>o.hidden=!o.textContent.toLowerCase().includes(picker.value.toLowerCase()))};
    picker.onkeydown=e=>{if(e.key==='Escape')menu.hidden=true};
    </script></body>''')
    with Browser(store,test_url=ats[0]) as b:
        def form(route):
            if route.request.method=='GET':route.fulfill(status=200,content_type='text/html',body=html)
            else:route.fallback()
        b.page.route(ats[0]+'/**',form)
        assert b.apply(local_job(store,ats))=='confirmed'
    assert b"Bachelor's Degree" in ats[1][0] and b'B.S.' not in ats[1][0]


@pytest.mark.parametrize('initial,entered,step,minimum,valid', [
    ('','2028','', '', True), ('2026','2028','2','',True),
    ('2026','2027','2','',False), ('0.5','2.5','1','',True),
    ('0.5','2','1','',False), ('2026','2027','2','2025',True),
    ('0.5','2','any','',True),
])
def test_controlled_number_value_updates_preserve_only_equivalent_constraints(store,ats,initial,entered,step,minimum,valid):
    with Browser(store,test_url=ats[0]) as b:
        b.page.set_content(f'<form><label for="year">End date year</label><input id="year" type="number" value="{initial}"'+
            (f' step="{step}"' if step else '')+(f' min="{minimum}"' if minimum else '')+
            ' oninput="this.setAttribute(\'value\',this.value)"></form>')
        before=b._snapshot()
        b.page.locator('#year').fill(entered)
        if valid:b._verify([],[],before)
        else:
            with pytest.raises(Blocked,match='form_changed'):b._verify([],[],before)
        # Actual employer constraints still stop advancement after hydration.
        b.page.locator('#year').evaluate('(e)=>e.max="1"')
        with pytest.raises(Blocked,match='form_changed'):b._verify([],[],before)
    assert not ats[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_ashby_nested_sms_consent_is_distinct_from_phone(store,ats):
    from hireme.answers import resolve
    store.put_facts({'sms':'No'})
    with Browser(store,test_url=ats[0]) as b:
        b.page.set_content('''<form><div class="ashby-application-form-field-entry">
          <label class="ashby-application-form-question-title _required_">Phone</label>
          <input type="tel" id="phone" aria-label="Phone">
          <div class="ashby-application-form-texting-consent-description"><div class="_consentRadioGroup_">
            <label><input type="radio" name="communicationConsent" value="yes">Yes - I consent to receiving text messages</label>
            <label><input type="radio" name="communicationConsent" value="no">No - I do not consent to receiving text messages</label>
          </div></div></div></form>''')
        fields=b._snapshot()
        assert [f['label'] for f in fields]==['Phone','Consent to receiving text messages']
        answers=[resolve(store,'jobs.ashbyhq.com',f) for f in fields]
        for a in answers:b._fill(a)
        b._verify(answers,[],fields)
        assert b.page.locator('#phone').input_value()==store.facts()['phone']['value']
        assert b.page.locator('input[value=no]').is_checked()
    assert not ats[1]


@pytest.mark.parametrize('duplicate',[False,True])
def test_ashby_rich_school_option_uses_exact_canonical_name(store,ats,duplicate):
    from hireme.answers import resolve
    store.put_facts({'school':'University of California, Berkeley'})
    option='''<div role="option" onclick="document.querySelector('#school').value=this.querySelector('span').textContent">
      <div><span class="_canonicalSchoolResultName_205">University of California, Berkeley</span><span>United States</span></div><span>berkeley.edu</span></div>'''
    with Browser(store,test_url=ats[0]) as b:
        b.page.set_content('<label for="school">School Name</label><input id="school" role="combobox" aria-controls="schools"><div id="schools">'+option*(2 if duplicate else 1)+'</div>')
        fields=b._snapshot();f=fields[0]
        assert f['options']==['University of California, Berkeley']
        a=resolve(store,'jobs.ashbyhq.com',f)
        if duplicate:
            with pytest.raises(Blocked,match='option_mismatch'):b._fill(a)
        else:
            b._fill(a);b._verify([a],[],fields)
            assert b.page.locator('#school').input_value()==a['value']
    assert not ats[1]


def test_ashby_autocomplete_uses_fieldset_question_instead_of_placeholder(store,ats):
    from hireme.answers import resolve
    with Browser(store,test_url=ats[0]) as b:
        b.page.set_content('''<fieldset class="_fieldEntry_test"><div class="ashby-application-form-question-title _required_test">How did you hear about Gecko?</div>
          <div><input role="combobox" placeholder="Start typing..." aria-controls="source-menu"></div></fieldset>
          <div id="source-menu"><div role="option" onclick="document.querySelector('input').value=this.textContent">Company Website</div></div>''')
        fields=b._snapshot();f=fields[0]
        assert f['label']=='How did you hear about Gecko?' and f['required']
        answer=resolve(store,'jobs.ashbyhq.com',f,context={'source':'ash:gecko-robotics'})
        assert answer['value']=='Company Website'
        b._fill(answer);b._verify([answer],[],fields)
    assert not ats[1]


def test_ashby_static_autocomplete_opens_adjacent_toggle_before_enumerating(store,ats):
    from hireme.answers import resolve
    with Browser(store,test_url=ats[0]) as b:
        b.page.set_content('''<fieldset><label class="ashby-application-form-question-title _required_test">How did you hear about Gecko?</label>
          <div><input class="ashby-application-form-input-autocomplete" role="combobox" placeholder="Start typing..." aria-controls="sources" aria-expanded="false">
          <button onclick="document.querySelector('#sources').hidden=false;document.querySelector('input').setAttribute('aria-expanded','true')">Open</button></div></fieldset>
          <div id="sources" hidden><div role="option" aria-selected="true" onclick="document.querySelector('input').value=this.textContent;this.parentElement.hidden=true;document.querySelector('input').setAttribute('aria-expanded','false')">Other</div></div>
          <script>document.querySelector('input').onkeydown=e=>{if(e.key==='Escape'){document.querySelector('#sources').hidden=true;e.target.setAttribute('aria-expanded','false')}}</script>''')
        fields=b._snapshot();f=fields[0]
        assert f['options']==['Other'] and f['value']==''
        answer=resolve(store,'jobs.ashbyhq.com',f,context={'source':'ash:gecko-robotics'})
        b._fill(answer);b._verify([answer],[],fields)
        assert b.page.locator('input').input_value()=='Other'
    assert not ats[1]


def test_ashby_texting_draft_save_is_suppressed_without_granting_submission(store,monkeypatch):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com'
    class Request:
        url='https://jobs.ashbyhq.com/api/non-user-graphql';method='POST'
        post_data=json.dumps({'operationName':'ApiSubmitCandidateTextingConsent','query':'mutation ApiSubmitCandidateTextingConsent { submitCandidateTextingConsent { id } }'})
    class Route:
        request=Request();action=None
        def abort(self):self.action='abort'
        def continue_(self):self.action='continue'
    r=Route();b._route(r)
    assert r.action=='abort' and not b.denied_write
    assert not b.attempted and not store.db.execute('SELECT 1 FROM applications').fetchone()
    r.request.post_data=json.dumps({'operationName':'ApiSubmitCandidateTextingConsent','query':'mutation SubmitApplication { submitApplication { id } }'})
    b._route(r)
    assert r.action=='abort' and b.denied_write


def test_initial_render_timeout_gets_bounded_read_retry_without_any_write(store,ats,monkeypatch):
    from playwright.sync_api import TimeoutError
    job=local_job(store,ats)
    def fail_ready(self):raise TimeoutError('Synthetic render timeout')
    monkeypatch.setattr(Browser,'_wait_ready',fail_ready)
    with Browser(store,test_url=ats[0]) as browser:
        with pytest.raises(Blocked,match='posting_fetch_failed'):browser.apply(job)
        assert not browser.attempted and not browser.aid
    assert not ats[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def conditional_html(label='City',unstable=False,change_existing=False):
    html=(Path(__file__).parent/'fixtures/application.html').read_text()
    script='''<script>let revealed=0;document.getElementById('name').addEventListener('input',()=>{
      if(!UNSTABLE && revealed)return;
      revealed++;let l=document.createElement('label');let c=document.createElement('input');
      c.id='revealed-'+revealed;c.name='revealed-'+revealed;c.required=true;l.htmlFor=c.id;l.textContent=LABEL;
      document.getElementById('application').insertBefore(l,document.querySelector('button[type=submit]'));
      l.after(c);
      CHANGE
    });</script>'''.replace('UNSTABLE',str(unstable).lower()).replace('LABEL',json.dumps(label)).replace('CHANGE',
        "document.querySelector('label[for=email]').textContent='Changed email question';" if change_existing else '')
    return html.replace('</body>',script+'</body>')


@pytest.mark.parametrize('live',[False,True])
def test_conditional_required_fields_resolve_before_preparation_or_submission(store,ats,live):
    store.put_facts({'city':'Berkeley'})
    job=local_job(store,ats);html=conditional_html()
    with Browser(store,test_url=ats[0]) as b:
        b.context.route(ats[0]+'/**',lambda route:route.fulfill(body=html,content_type='text/html') if route.request.method=='GET' else route.continue_())
        assert b.apply(job,live=live)==('confirmed' if live else 'prepared')
    app=store.db.execute('SELECT * FROM applications WHERE job_id=?',(job['id'],)).fetchone()
    package=json.loads(app['package'])
    assert len(package['answers'])==4 and len(package['documents'])==1
    assert next(a['value'] for a in package['answers'] if a['field']['label']=='City')=='Berkeley'
    assert len(ats[1])==int(live)
    if live:assert b'Berkeley' in ats[1][0]
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='conditional_fields_revealed'").fetchone()[0]==1


def test_conditional_unconfirmed_high_school_gpa_remains_held(store,ats):
    store.put_facts({'gpa':'3.76/4.0'})
    job=local_job(store,ats);html=conditional_html('High school GPA')
    with Browser(store,test_url=ats[0]) as b:
        b.context.route(ats[0]+'/**',lambda route:route.fulfill(body=html,content_type='text/html'))
        with pytest.raises(Blocked,match='missing_answers'):b.apply(job)
    assert not ats[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert store.db.execute('SELECT label FROM questions WHERE resolved=0').fetchone()[0]=='High school GPA'


@pytest.mark.parametrize('unstable,change_existing',[(True,False),(False,True)])
def test_unstable_or_replaced_controls_never_reach_submit(store,ats,unstable,change_existing):
    store.put_facts({'city':'Berkeley'})
    job=local_job(store,ats);html=conditional_html(unstable=unstable,change_existing=change_existing)
    with Browser(store,test_url=ats[0]) as b:
        b.context.route(ats[0]+'/**',lambda route:route.fulfill(body=html,content_type='text/html'))
        with pytest.raises(Blocked,match='form_changed'):b.apply(job)
    assert not ats[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='conditional_fields_revealed'").fetchone()[0]==(3 if unstable else 0)
