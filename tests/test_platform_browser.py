"""Local protocol fixtures are intentionally incapable of unlocking live adapters."""
import base64
import hashlib
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import pytest
from hireme import platform_connections as pc
from hireme.platform_browser import PlatformBrowser,OperationGrant,flow_signature
from hireme.store import Store
from hireme.util import Blocked,digest,now
from tests.test_letters import setup_letter

TITLE='Software Engineer Intern Summer 2027'

@pytest.fixture
def native_site(store):
    records=[];mode={'value':'normal'};reads=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,body,ctype='text/html'):
            self.send_response(200);self.send_header('Content-Type',ctype+'; charset=utf-8');self.end_headers();self.wfile.write(body.encode())
        def do_GET(self):
            path=urlsplit(self.path).path;reads.append(self.path)
            if path=='/profile':return self.send('<div id="profile">Test Person · Approved resume</div>')
            if mode['value']=='signed_out':return self.send('<label>Password<input type="password"></label>')
            common=f'<a href="/application">Profile</a><button aria-label="Open profile options">Profile</button><h1>{TITLE}</h1><h2>Acme</h2><div id="posting">Build Python and TypeScript software.</div><div id="requirements">Resume and narrative response</div>'
            if path=='/search':
                page=int(parse_qs(urlsplit(self.path).query).get('page',['1'])[0])
                count=30 if mode['value']=='hundred' else 2
                cards=''.join(f'<article><a href="https://app.joinhandshake.com/jobs/{page*100+i}">Job</a><b>{TITLE}</b><i>Acme</i><span>San Francisco, United States</span></article>' for i in range(count))
                return self.send(common+cards)
            if path.startswith('/jobs/') and mode['value']=='external':return self.send(common+'<a id="external" href="https://jobs.lever.co/acme/req-123?utm_source=handshake">Apply externally</a>')
            controls='''<label>Message<textarea id="message" maxlength="4000"></textarea></label>
            <label>Relocate<input id="relocate" type="checkbox"></label>
            <label for="resume">Resume</label><select id="resume"><option value="stale">Stale resume</option><option value="approved">Approved resume</option></select>
            <label for="response">Response</label><select id="response"><option value="stale-response">Stale response</option></select>
            <select id="kind"><option value="resume">Resume upload</option><option value="supplemental_response">Response upload</option></select><input id="file" type="file"><label>Public<input id="public" type="checkbox" checked></label>
            <button id="save" type="button">Upload new document</button><button id="send" type="button">Send application</button>'''
            if mode['value']=='relocate':controls=controls.replace('id="relocate" type="checkbox"','id="relocate" type="checkbox" checked')
            script='''<script>
            save.onclick=async()=>{const f=file.files[0];const data=btoa(String.fromCharCode(...new Uint8Array(await f.arrayBuffer())));
              const r=await fetch('/upload',{method:'POST',body:JSON.stringify({document:data,filename:f.name,kind:document.getElementById('kind').value,visibility:'private',public:document.getElementById('public').checked})});
              const ack=await r.json();if(ack.id){document.getElementById(document.getElementById('kind').value==='resume'?'resume':'response').add(new Option('Uploaded',ack.id));}};
            send.onclick=async()=>{const message=document.getElementById('message').value;
              const document_ids=message?[]:[resume.value,response.value].filter(x=>x!=='stale-response');
              await fetch('/submit',{method:'POST',body:JSON.stringify({job_id:'123',message,document_ids,relocate:document.getElementById('relocate').checked})});};
            </script>'''
            if mode['value']=='extra_write':script=script.replace("relocate:document.getElementById('relocate').checked","relocate:false,unexpected:true")
            self.send(common+controls+script)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            db=sqlite3.connect(store.root/'ledger.sqlite3')
            intents=db.execute("SELECT COUNT(*) FROM events WHERE kind='submit_intent'").fetchone()[0];db.close()
            records.append((self.path,body,intents))
            if self.path=='/upload':
                self.send(json.dumps({} if mode['value']=='missing_ack' else {'id':'new-'+str(len(records))}),'application/json')
            else:self.send(json.dumps({'accepted':mode['value']!='ambiguous','job_id':'123'}),'application/json')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}',records,mode,reads
    server.shutdown();server.server_close()


def fixture_flows(store,browser,monkeypatch,connection='handshake',supplement=False,quick=False):
    origin=browser.origin
    profile={'url':origin+'/profile','selector':'#profile','fingerprint':digest('Test Person · Approved resume'),'facts_hash':digest(store.facts()),'resume_hash':store.db.execute("SELECT hash FROM documents WHERE kind='resume'").fetchone()[0]}
    upload={'path':'/upload','method':'POST','encoding':'json','body':{'document':'$document','filename':'$filename','kind':'$kind','visibility':'$visibility','public':False},'visibility':'private','immutable':True,'file_selector':'#file','save_selector':'#save','public_selector':'#public','kind_selector':'#kind','response_id_key':'id'}
    docs=[{'kind':'resume','selector':'#resume','upload':upload}]
    if supplement:docs.append({'kind':'supplemental_response','selector':'#response','upload':{**upload}})
    browser.page.goto(origin+'/native')
    signature=flow_signature(browser.page,[d['selector'] for d in docs] if connection=='handshake' else [])
    submission={'profile':profile,'documents':docs if connection=='handshake' else [],'profile_change_selectors':['#relocate'],
        'message_selector':'#message','message_prompt':'Tell us about your experience and why this role interests you.',
        'submit_selector':'#send','submit':{'path':'/submit','encoding':'json','body':{'job_id':'$job_id','message':'$message','document_ids':'$document_ids','relocate':False}},
        'receipt':{'key':'accepted','value':True,'job_id_key':'job_id'},'verification_signature':'ack-v1',
        'requirements':([{'kind':'supplemental_response','label':'Response','prompt':'Tell us about your experience building Python software.'}] if supplement else [])}
    preparation={'profile':profile,'quick_apply':quick,'entry_selector':'#send' if quick else None,'requirements_selector':'#requirements','requirements_hash':digest('Resume and narrative response')}
    inspection={'company_selector':'h2','title_selector':'h1','posting_selector':'#posting','external_selector':'#external','requirements':submission['requirements']}
    discovery={'card_selector':'article','link_selector':'a','title_selector':'b','company_selector':'i','location_selector':'span'}
    mapping={'inspection':[{'evidence':inspection}],'preparation':[{'evidence':preparation}],'submission':[{'evidence':submission,'signature':signature}],'verification':[{'signature':'ack-v1','evidence':{}}],'discovery':[{'evidence':discovery}]}
    # In-memory fixture injection only. No production flow records are installed.
    get=lambda s,c,cap:mapping.get(cap,[]) if c==connection else []
    monkeypatch.setattr('hireme.platform_browser.flows',get);monkeypatch.setattr(pc,'flows',get)
    return mapping


def saved_native(store,connection='handshake'):
    url='https://app.joinhandshake.com/jobs/123' if connection=='handshake' else 'https://www.workatastartup.com/jobs/123'
    key=pc.import_listing(store,url,'Acme',TITLE,'San Francisco, United States','Build Python and TypeScript software.')
    pc.configure(store,connection,{'enabled':True,'native_apply_enabled':True})
    return json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(key,)).fetchone()[0])


def test_handshake_exact_immutable_private_upload_and_durable_intent(store,native_site,monkeypatch):
    job=saved_native(store)
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/native') as browser:
        fixture_flows(store,browser,monkeypatch)
        assert browser.apply(job)=='confirmed'
        with pytest.raises(Blocked,match='duplicate_or_uncertain'):browser.apply(job)
    upload,submit=native_site[1]
    resume=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    assert upload[0]=='/upload' and base64.b64decode(upload[1]['document'])==(store.root/'documents'/resume['filename']).read_bytes()
    assert upload[1]['public'] is False and upload[2]==0 and submit[2]==1
    assert submit[1]['document_ids']==['new-1']
    assert store.db.execute('SELECT remote_id FROM platform_documents').fetchone()[0]=='new-1'
    app=store.db.execute('SELECT id FROM applications').fetchone()[0]
    assert json.loads(store.application_record(app)['connection_receipt']['receipt'])['accepted'] is True
    assert 'connection_receipt' not in store.snapshot(include_packages=False)['applications'][0]


def test_workatastartup_preserves_introduction_and_acknowledgement(store,native_site,monkeypatch):
    job=saved_native(store,'workatastartup');model=setup_letter(store)
    monkeypatch.setattr('hireme.provider.ManagedProvider',lambda *args:model)
    with PlatformBrowser(store,'workatastartup',test_url=native_site[0]+'/native') as browser:
        fixture_flows(store,browser,monkeypatch,'workatastartup')
        assert browser.apply(job)=='confirmed'
    assert len(native_site[1])==1 and len(native_site[1][0][1]['message'])>=50
    app=dict(store.db.execute('SELECT * FROM applications').fetchone())
    assert app['confirmation']=='Introduction sent'
    assert json.loads(app['package'])['answers'][0]['value']==native_site[1][0][1]['message']
    assert store.db.execute('SELECT COUNT(*) FROM application_artifacts').fetchone()[0]==1


@pytest.mark.parametrize('mode,reason',[('relocate','profile_change_review'),('signed_out','sign_in_required'),('missing_ack','upload_verification_failed')])
def test_native_requirements_and_session_stop_before_intent(store,native_site,monkeypatch,mode,reason):
    job=saved_native(store)
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/native') as browser:
        fixture_flows(store,browser,monkeypatch);native_site[2]['value']=mode
        with pytest.raises(Blocked,match=reason):browser.apply(job)
    assert not any(record[0]=='/submit' for record in native_site[1])
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('mode',['extra_write','ambiguous'])
def test_ambiguous_or_unrecognized_submit_stays_held_across_restart(store,native_site,monkeypatch,mode):
    job=saved_native(store);native_site[2]['value']=mode
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/native') as browser:
        fixture_flows(store,browser,monkeypatch)
        if mode=='extra_write':
            with pytest.raises(Blocked,match='unapproved_draft_write'):browser.apply(job)
        else:assert browser.apply(job)=='unknown'
    root=store.root;reopened=Store(root)
    try:
        reopened.recover()
        assert reopened.db.execute('SELECT state FROM applications').fetchone()[0]=='unknown'
        with PlatformBrowser(reopened,'handshake',test_url=native_site[0]+'/native') as browser:
            with pytest.raises(Blocked,match='duplicate_or_uncertain'):browser.apply(job)
    finally:reopened.close()
    assert len([r for r in native_site[1] if r[0]=='/submit'])==(0 if mode=='extra_write' else 1)


def test_quick_apply_prepares_without_clicking_final_action(store,native_site,monkeypatch):
    job=saved_native(store);doc=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    store.db.execute('INSERT INTO platform_documents VALUES(?,?,?,?,?)',('handshake',doc['hash'],'resume','approved',now()))
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/native') as browser:
        fixture_flows(store,browser,monkeypatch,quick=True)
        assert browser.apply(job,live=False)=='prepared'
    assert not native_site[1]


@pytest.mark.parametrize('mode,total,pages',[('normal',10,5),('hundred',100,4)])
def test_discovery_bounds_and_external_inspection(store,native_site,monkeypatch,mode,total,pages):
    native_site[2]['value']=mode
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/search') as browser:
        fixture_flows(store,browser,monkeypatch)
        listings=list(browser.discover())
    assert len(listings)==total
    assert len([url for url in native_site[3] if url.startswith('/search?')])==pages


def test_verified_external_destination_uses_canonical_employer_identity_without_submission(store,native_site,monkeypatch,job):
    native_site[2]['value']='external'
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/search') as browser:
        fixture_flows(store,browser,monkeypatch)
        listing=next(browser.discover())
    assert listing['external_url']==job['url']
    assert pc.import_listing(store,**listing)==job['id']
    assert not native_site[1] and not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('bad',[{'extra':True}, {'job_id':True}, {'job_id':1}])
def test_operation_grant_rejects_extra_fields_types_and_reuse(bad):
    from types import SimpleNamespace
    grant=OperationGrant('https://app.joinhandshake.com',{'path':'/submit','encoding':'json','body':{'job_id':'1'}},{})
    request=lambda data:SimpleNamespace(url='https://app.joinhandshake.com/submit',method='POST',post_data_buffer=json.dumps(data).encode())
    assert not grant.consume(request(bad))
    assert grant.consume(request({'job_id':'1'}))
    assert not grant.consume(request({'job_id':'1'}))


def test_supplemental_document_pdf_is_private_acknowledged_and_attached(store,native_site,monkeypatch):
    job=saved_native(store);model=setup_letter(store)
    monkeypatch.setattr('hireme.provider.ManagedProvider',lambda *args:model)
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/native') as browser:
        fixture_flows(store,browser,monkeypatch,supplement=True)
        assert browser.apply(job)=='confirmed'
    uploads=[r for r in native_site[1] if r[0]=='/upload'];submit=native_site[1][-1]
    assert len(uploads)==2 and submit[1]['document_ids']==['new-1','new-2']
    assert uploads[1][1]['kind']=='supplemental_response' and base64.b64decode(uploads[1][1]['document']).startswith(b'%PDF-')
    assert all(r[1]['visibility']=='private' and r[1]['public'] is False for r in uploads)


@pytest.mark.parametrize('fault,reason',[('stale_profile','profile_changed'),('changed_requirements','requirements_changed'),('unsupported','supplied_document_required'),('size','document_too_large'),('missing_name','missing_fact'),('pause','paused'),('budget','cycle_timeout')])
def test_preparation_safeguards_never_reach_submit(store,native_site,monkeypatch,fault,reason):
    job=saved_native(store)
    with PlatformBrowser(store,'handshake',test_url=native_site[0]+'/native') as browser:
        mapping=fixture_flows(store,browser,monkeypatch)
        if fault=='stale_profile':mapping['preparation'][0]['evidence']['profile']['fingerprint']='changed'
        elif fault=='changed_requirements':mapping['preparation'][0]['evidence']['requirements_hash']='changed'
        elif fault=='unsupported':mapping['submission'][0]['evidence']['requirements']=[{'kind':'work_sample','label':'Original work sample'}]
        elif fault=='size':
            data=b'%PDF-'+b'x'*(1024*1024);h=hashlib.sha256(data).hexdigest();(store.root/'documents'/(h+'.pdf')).write_bytes(data)
            store.db.execute("UPDATE documents SET hash=?,filename=? WHERE kind='resume'",(h,h+'.pdf'))
            for key in ['preparation','submission']:mapping[key][0]['evidence']['profile']['resume_hash']=h
        elif fault=='missing_name':store.put_facts({},clear_keys=['full_name'])
        elif fault=='pause':
            store.run_generation=store.control_generation();store.update_settings({'live_enabled':False})
        elif fault=='budget':store.run_deadline=0
        with pytest.raises(Blocked,match=reason):browser.apply(job)
    assert not any(r[0]=='/submit' for r in native_site[1]) and not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
