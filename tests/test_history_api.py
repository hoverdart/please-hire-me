import json
import multiprocessing
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.test_dashboard import launch, launch_demo, wait_for_dashboard


@pytest.fixture
def history_server(store):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    process=multiprocessing.Process(target=launch,args=(str(store.root),str(Path.cwd()),port));process.start()
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process,base)
        yield base
    finally:process.terminate();process.join(5)


def request(base,path='/api/history',data=None,headers=None):
    headers={'X-Hireme-Token':'fixture-capability','Content-Type':'application/json',**(headers or {})}
    req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
    try:
        with urllib.request.urlopen(req) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as error:return error.code,json.load(error)


def education(**overrides):
    return {'revision':0,'kind':'education','values':{'institution':'University of California, Berkeley',
        'degree':'BS','expected_end':'2028-05','current':True},'source':'Applicant checked enrollment record','confirmed':True,**overrides}


def test_history_api_repeat_entries_revisions_confirmation_and_withdrawal(store,history_server):
    base=history_server
    status,result=request(base,data=education())
    assert status==200
    school=result['history'][0]
    assert school['revision']==1 and school['confirmed'] and 'payload' not in school
    assert 'start' not in school['values'] and 'end' not in school['values']
    employment={'revision':0,'kind':'employment','values':{'organization':'Acme','title':'Intern',
        'start':'2026-06','end':'2026-08','current':False},'source':'Applicant checked employment record','confirmed':True}
    status,result=request(base,data=employment)
    assert status==200 and len(result['history'])==2
    job=next(entry for entry in result['history'] if entry['kind']=='employment')
    edit=education(id=school['id'],revision=1,values={**school['values'],'field_of_study':'Computer Science'})
    status,result=request(base,data=edit)
    assert status==200
    school=next(entry for entry in result['history'] if entry['kind']=='education')
    assert school['revision']==2 and school['values']['field_of_study']=='Computer Science'
    status,result=request(base,'/api/history-withdraw',{'id':job['id'],'revision':1})
    assert status==200 and len(result['history'])==2
    withdrawn=next(entry for entry in result['history'] if entry['id']==job['id'])
    assert not withdrawn['confirmed'] and withdrawn['revision']==2 and withdrawn['values']==job['values']
    assert len(store.history_entries())==1
    status,result=request(base,'/api/state')
    assert status==200 and result['history']==request(base)[1]['history']
    events=[dict(row) for row in store.db.execute("SELECT detail FROM events WHERE kind LIKE 'history_%'")]
    assert 'enrollment record' not in json.dumps(events) and 'Computer Science' not in json.dumps(events)


def test_history_api_rejects_unconfirmed_malformed_and_stale_edits_without_mutation(store,history_server):
    base=history_server
    bad=[[],education(confirmed=False),education(revision=True),education(source=''),education(extra='unexpected'),
         education(id='f'*32),education(values={'institution':'University','start':'2028'}),
         education(values={'institution':'University','current':'true'}),
         education(values={'institution':'University','current':True,'end':'2028-05'}),
         education(values={'institution':'University','end':'2028-05','expected_end':'2028-06'}),
         education(values={'institution':'University','start':'2028-09','expected_end':'2028-05'}),
         education(values={'institution':'University','unsupported':'invented'})]
    for body in bad:
        status,_=request(base,data=body)
        assert status==400,body
        assert not store.history_entries(confirmed_only=False)
    _,result=request(base,data=education())
    saved=result['history'][0]
    for path,body in [('/api/history',education(id=saved['id'],revision=0)),
                      ('/api/history-withdraw',{'id':saved['id'],'revision':0})]:
        status,result=request(base,path,body)
        assert status==409 and 'another window' in result['error']
    status,_=request(base,data=education(id=saved['id'],revision=1,kind='employment',
        values={'organization':'Acme','title':'Engineer'}))
    assert status==400
    assert request(base)[1]['history']==[saved]


@pytest.mark.parametrize('headers',[{'X-Hireme-Token':''},{'Origin':'https://outside.invalid'}, {'Host':'outside.invalid'}])
def test_history_api_authenticates_reads_and_writes_before_record_access(store,history_server,headers):
    assert request(history_server,headers=headers)[0]==403
    assert request(history_server,data=education(),headers=headers)[0]==403
    assert request(history_server,'/api/history-withdraw',{'id':'a'*32,'revision':1},headers)[0]==403
    assert not store.history_entries(confirmed_only=False)


def test_demo_history_is_read_only(tmp_path):
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    process=multiprocessing.Process(target=launch_demo,args=(str(tmp_path/'sample'),str(Path.cwd()),port));process.start()
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process,base)
        for path,body in [('/api/history',education()),('/api/history-withdraw',{'id':'a'*32,'revision':1})]:
            status,result=request(base,path,body)
            assert status==403 and 'read-only' in result['error']
    finally:process.terminate();process.join(5)
