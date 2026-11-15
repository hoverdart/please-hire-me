import copy
import json
from types import SimpleNamespace

import pytest

from hireme.draft_journal import DraftJournal
from hireme.util import Blocked,digest
from hireme.workday_browser_continuation import verify
from hireme.workday_browser_posting import public_identity,verified_posting

URL='https://acme.wd12.myworkdayjobs.com/External/job/US/Software-Intern_R_123'
REMOTE='b'*32
BINDING='a'*64


@pytest.fixture
def saved(store,job,package,monkeypatch):
    job={**job,'id':digest(URL),'url':URL,'host':'acme.wd12.myworkdayjobs.com'}
    store.upsert_job(job);store.update_settings({'allowed_application_ats':['workday'],'remote_drafts':True})
    package=copy.deepcopy(package);package.update(job_id=job['id'],url=URL)
    step={'fields':package['steps'][0]['fields'],'answers':package['answers'],'documents':package['documents'],'url':URL}
    package['browser_agent']={'version':1,'profile_binding':BINDING,'last_url':URL,'complete':False,
        'uploaded_documents':[],'completed_steps':[step],'remote_application_hash':digest(REMOTE)}
    journal=DraftJournal(store,job)
    operation=journal.intent([('ui','step')],'ui:next',{'profile_binding':BINDING,'remote_application_hash':digest(REMOTE)},package)
    journal.acknowledge(('ui','step'));step['operation_id']=operation
    store.prepare(job,package)
    info={'externalUrl':URL,'jobPostingId':'Software-Intern_R_123','jobPostingSiteId':'External',
        'jobReqId':'R_123','title':job['title'],'posted':True,'canApply':False,
        'jobApplication':{'id':REMOTE,'draft':True,'dateCompleted':None}}
    job['_workday_public_identity']=public_identity(job,{'jobPostingInfo':info},allow_saved=True)
    response={'status':200,'body':{'application':{'applicationId':REMOTE,'jobPostingAnchorId':'Software-Intern_R_123',
        'jobRequisitionId':'R_123','postingTitle':job['title'],'draft':True,'active':True,'status':None,'applicationType':'EXTERNAL'}}}
    reads=[]
    def evaluate(script,url):reads.append(url);return copy.deepcopy(response)
    agent=SimpleNamespace(store=store,job=job,binding=BINDING,page=SimpleNamespace(evaluate=evaluate),owner_verified=False)
    monkeypatch.setattr('hireme.browser_identity.verify_workday_account',lambda agent:True)
    return agent,job,info,response,reads,operation


def test_saved_application_flag_requires_a_bound_owned_checkpoint_and_exact_config(store,saved):
    agent,job,info,_,reads,_=saved
    assert public_identity(job,{'jobPostingInfo':info}) is None
    proof=verify(agent,job,info)
    assert proof and proof['remote_application_hash']==digest(REMOTE) and agent.owner_verified
    assert reads==['https://acme.wd12.myworkdayjobs.com/wday/calypso/cxs/candidatehome/acme/External/application/'+REMOTE+'/config']
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
    assert store.db.execute('SELECT state FROM applications WHERE job_id=?',(job['id'],)).fetchone()[0]=='prepared'


@pytest.mark.parametrize('field,value',[
    ('applicationId','different'),('jobPostingAnchorId','Other_R_123'),('jobRequisitionId','R_999'),
    ('postingTitle','Different job'),('draft',False),('active',False),('applicationType','INTERNAL'),
    ('status','Application Received'),
])
def test_wrong_or_already_received_record_never_authorizes_continuation(saved,field,value):
    agent,job,info,response,_,_=saved
    response['body']['application'][field]=value
    assert verify(agent,job,info) is None


def test_foreign_draft_id_is_rejected_before_reading_it(saved):
    agent,job,info,_,reads,_=saved;info['jobApplication']['id']='c'*32
    assert verify(agent,job,info) is None and not reads


def test_unverified_owner_is_rejected_before_reading_candidate_record(saved,monkeypatch):
    agent,job,info,_,reads,_=saved
    monkeypatch.setattr('hireme.browser_identity.verify_workday_account',lambda agent:False)
    assert verify(agent,job,info) is None and not reads


def test_uncertain_save_is_still_fenced_before_continuation_read(store,saved):
    agent,job,info,_,reads,operation=saved
    store.db.execute("UPDATE draft_operations SET state='unknown' WHERE id=?",(operation,))
    with pytest.raises(Blocked,match='browser_draft_uncertain'):verify(agent,job,info)
    assert not reads


def test_changed_local_package_cannot_adopt_an_employer_draft(store,saved):
    agent,job,info,_,reads,_=saved
    row=store.db.execute('SELECT package FROM applications WHERE job_id=?',(job['id'],)).fetchone()
    changed=json.loads(row[0]);changed['browser_agent']['remote_application_hash']=digest('foreign')
    store.db.execute('UPDATE applications SET package=? WHERE job_id=?',(json.dumps(changed),job['id']))
    with pytest.raises(Blocked,match='package_tampered'):verify(agent,job,info)
    assert not reads


def test_unbound_acknowledgement_does_not_authorize_a_saved_record(store,saved):
    agent,job,info,_,reads,operation=saved
    store.db.execute('UPDATE draft_operations SET target=? WHERE id=?',(json.dumps({'profile_binding':BINDING}),operation))
    assert verify(agent,job,info) is None and not reads


def test_retained_acknowledged_checkpoint_survives_removal_of_reusable_preview(store,saved):
    agent,job,info,_,_,_=saved
    store.db.execute("DELETE FROM applications WHERE job_id=? AND state='prepared'",(job['id'],))
    assert verify(agent,job,info)['remote_application_hash']==digest(REMOTE)


def test_posting_read_accepts_a_saved_draft_only_after_the_separate_verifier(saved,monkeypatch):
    agent,job,info,_,_,_=saved
    page=SimpleNamespace(evaluate=lambda *args:{'status':200,'data':{'jobPostingInfo':info}})
    monkeypatch.setattr('hireme.workday_browser_posting.inspect_posting',lambda *args,**kwargs:'Verified public description')
    with pytest.raises(Blocked,match='browser_draft_review'):verified_posting(page,job,lambda:None)
    current=verified_posting(page,job,lambda:None,saved_application=lambda current,record:verify(agent,current,record))
    assert current['_workday_saved_application']['remote_application_hash']==digest(REMOTE)
    assert current['description']=='Verified public description'


def operation_for(store,saved,kind='next'):
    from hireme.browser_agent_protocol import UIOperation
    agent,job,_,_,_,_=saved
    package=json.loads(store.db.execute('SELECT package FROM applications WHERE job_id=?',(job['id'],)).fetchone()[0])
    agent.aid=None;agent.preparing=False;agent.staged_documents={};agent.workday_application_hash=digest(REMOTE);agent.owner_verified=True
    agent.journal=DraftJournal(store,job);agent.allowed_origin='https://acme.wd12.myworkdayjobs.com'
    store.browser_agent_session=agent.journal.session
    return agent,UIOperation(agent,{'kind':kind,'id':kind+':exact-control'},package)


@pytest.mark.parametrize('remote,allowed',[(REMOTE,True),('c'*32,False)])
def test_native_saves_cannot_cross_the_resumed_record_boundary(store,saved,remote,allowed):
    agent,operation=operation_for(store,saved)
    request=SimpleNamespace(url='https://acme.wd12.myworkdayjobs.com/wday/calypso/cxs/jobapplication/acme/jobapplication/'+remote+'/name',
        method='POST',headers={'content-type':'application/json'},post_data_buffer=b'{}')
    assert operation.allow(request) is allowed
    if not allowed:assert operation.denied and not operation.requests and operation.record is None
    else:
        row=store.db.execute('SELECT target FROM draft_operations WHERE id=?',(operation.record,)).fetchone()
        assert json.loads(row[0])['remote_application_hash']==digest(REMOTE)


def test_native_creation_response_binds_future_checkpoint_writes_without_retaining_remote_id(store,saved):
    agent,operation=operation_for(store,saved,'entry');agent.workday_application_hash=None
    request=SimpleNamespace(url='https://acme.wd12.myworkdayjobs.com/wday/cxs/acme/jobpostings/opaque-posting/jobapplications',
        method='POST',headers={'content-type':'application/json'},post_data_buffer=b'{}')
    assert operation.allow(request)
    operation.capture(request,200,{'id':REMOTE})
    assert agent.workday_application_hash==digest(REMOTE) and operation.complete
    target=json.loads(store.db.execute('SELECT target FROM draft_operations WHERE id=?',(operation.record,)).fetchone()[0])
    assert target['remote_application_hash']==digest(REMOTE) and REMOTE not in json.dumps(target)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('entry_state,remote_bound,allowed',[
    ('acknowledged',True,True),('acknowledged',False,False),('failed',True,False),
])
def test_empty_draft_requires_its_exact_acknowledged_native_creation(store,saved,entry_state,remote_bound,allowed):
    agent,job,info,_,reads,operation=saved
    row=store.db.execute('SELECT package FROM applications WHERE job_id=?',(job['id'],)).fetchone()
    packet=json.loads(row[0]);packet['browser_agent']['completed_steps']=[]
    store.prepare(job,packet)
    target={'profile_binding':BINDING,**({'remote_application_hash':digest(REMOTE)} if remote_bound else {})}
    store.db.execute('UPDATE draft_operations SET operation=?,state=?,target=? WHERE id=?',
                     ('ui:entry',entry_state,json.dumps(target),operation))
    proof=verify(agent,job,info)
    assert bool(proof) is allowed
    if not allowed:assert not reads
