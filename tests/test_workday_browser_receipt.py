import copy
from types import SimpleNamespace

import pytest

from hireme.workday_browser_receipt import received_record,verify_submission


HOST='synthetic.wd12.myworkdayjobs.com'
REMOTE='a'*32
JOB={'id':'one-job','host':HOST,'title':'Compiler Intern',
     'url':f'https://{HOST}/en-US/External/job/California/Compiler-Intern_JR123'}


def record():
    return {'application':{'applicationId':REMOTE,'jobPostingAnchorId':'Compiler-Intern_JR123',
        'jobRequisitionId':'JR123','postingTitle':'Compiler Intern','draft':False,'active':True,
        'applicationType':'EXTERNAL','status':'Application Received','dateApplied':'November 4, 2026'}}


@pytest.mark.parametrize('change',[
    {'applicationId':'another-application'},{'jobPostingAnchorId':'Other-Intern_JR456'},
    {'jobRequisitionId':'JR456'},{'postingTitle':'Another Intern'},{'draft':True},{'draft':0},
    {'active':False},{'active':1},{'status':'Draft'},{'dateApplied':''},{'applicationType':'INTERNAL'},
])
def test_workday_receipt_requires_the_exact_received_external_application(change):
    data=record();assert received_record(JOB,REMOTE,data)
    data['application'].update(change)
    assert not received_record(JOB,REMOTE,data)


def agent_and_operation(store,monkeypatch):
    calls=[];owner_checks=[]
    def evaluate(script,url):
        assert "method:'GET'" in script and "cache:'no-store'" in script and "redirect:'error'" in script
        calls.append(url);return {'status':200,'body':record()}
    page=SimpleNamespace(locator=lambda selector:SimpleNamespace(inner_text=lambda:'Your application has been submitted.'),evaluate=evaluate)
    agent=SimpleNamespace(job=JOB,page=page,store=store,receipt_baseline='Review',_receipt=lambda text,before:True)
    monkeypatch.setattr('hireme.browser_identity.verify_workday_account',lambda current:owner_checks.append(current) or True)
    request=SimpleNamespace(method='POST',url=f'https://{HOST}/wday/cxs/synthetic/jobapplication/{REMOTE}/finalize')
    operation=SimpleNamespace(final=True,denied=False,complete=True,requests={'one':{'request':request}},responses={'one':{'ok':True}})
    return agent,operation,calls,owner_checks


def test_opaque_final_ack_can_use_a_fresh_owner_bound_received_record(store,monkeypatch):
    agent,operation,calls,owners=agent_and_operation(store,monkeypatch)
    assert verify_submission(agent,operation)
    assert owners==[agent]
    assert calls==[f'https://{HOST}/wday/calypso/cxs/candidatehome/synthetic/External/application/{REMOTE}/config']
    assert not verify_submission(agent,operation) and len(calls)==1
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='browser_submission_readback_verified'").fetchone()[0]==1


@pytest.mark.parametrize('condition',[
    'non_final','denied','missing_ack','missing_receipt','unverified_owner','foreign_host','other_tenant',
    'ordinary_save','foreign_query','dot_application','two_finalizers','draft_record','failed_read',
])
def test_readback_cannot_promote_unacknowledged_or_unrelated_outcomes(store,monkeypatch,condition):
    agent,operation,calls,owners=agent_and_operation(store,monkeypatch)
    if condition=='non_final':operation.final=False
    elif condition=='denied':operation.denied=True
    elif condition=='missing_ack':operation.responses['one']['ok']=False
    elif condition=='missing_receipt':agent._receipt=lambda *args:False
    elif condition=='unverified_owner':monkeypatch.setattr('hireme.browser_identity.verify_workday_account',lambda current:False)
    elif condition=='foreign_host':operation.requests['one']['request'].url=operation.requests['one']['request'].url.replace(HOST,'foreign.invalid')
    elif condition=='other_tenant':operation.requests['one']['request'].url=operation.requests['one']['request'].url.replace('/synthetic/','/another/')
    elif condition=='ordinary_save':operation.requests['one']['request'].url=operation.requests['one']['request'].url.replace('/finalize','/save')
    elif condition=='foreign_query':operation.requests['one']['request'].url+='?other=true'
    elif condition=='dot_application':operation.requests['one']['request'].url=operation.requests['one']['request'].url.replace(REMOTE,'..')
    elif condition=='two_finalizers':
        operation.requests['two']=copy.deepcopy(operation.requests['one']);operation.responses['two']={'ok':True}
    elif condition=='draft_record':
        data=record();data['application']['draft']=True
        agent.page.evaluate=lambda *args:{'status':200,'body':data}
    elif condition=='failed_read':agent.page.evaluate=lambda *args:{'status':401,'body':{}}
    assert not verify_submission(agent,operation)
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='browser_submission_readback_verified'").fetchone()
