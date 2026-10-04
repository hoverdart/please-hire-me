import json
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from hireme.ledger import summary
from hireme.provider import ManagedProvider,APIProvider
from hireme.util import Blocked


def test_usage_counts_failed_and_unmeasured_calls_in_local_day(store):
    records=[('claude-cli','sonnet',1,{'input_tokens':100,'output_tokens':20,'cache_read_input_tokens':5}),
             ('claude-cli','sonnet',0,{}),('codex-cli',None,None,None),
             ('openai-api','fixture',1,{'input_tokens':0,'output_tokens':0,'private':'secret'}),
             ('openai-api','fixture',1,{'input_tokens':True,'output_tokens':-5})]
    for provider,model,success,usage in records:
        rid=store.db.execute('INSERT INTO model_requests(timestamp,run_id,provider) VALUES(?,?,?)',('2026-10-04T07:00:00+00:00','test',provider)).lastrowid
        if model is not None:store.db.execute('INSERT INTO model_request_metadata VALUES(?,?,?,?,?,?)',(rid,model,'',1,success,json.dumps(usage)))
    store.db.execute('INSERT INTO model_requests(timestamp,run_id,provider) VALUES(?,?,?)',('2026-10-04T06:59:59+00:00','test','anthropic-api'))
    before=store.db.total_changes
    result=summary(store,datetime(2026,10,4,12,tzinfo=timezone.utc))
    assert store.db.total_changes==before and result['model_requests_today']==5
    groups={r['provider']:r for r in result['model_usage']}
    assert set(groups)=={'claude-cli','codex-cli','openai-api'}
    assert groups['claude-cli']['tokens']=={'input_tokens':100,'output_tokens':20,'cache_read_input_tokens':5}
    assert groups['claude-cli']['measured_requests']==1 and groups['claude-cli']['failed']==1
    assert groups['codex-cli']['tokens']=={} and groups['codex-cli']['outcome_unavailable']==1
    assert groups['openai-api']['tokens']=={'input_tokens':0,'output_tokens':0}
    assert groups['openai-api']['usage_unavailable']==1
    assert 'secret' not in json.dumps(result)


def test_failed_request_never_reuses_previous_call_token_counts(store,monkeypatch):
    monkeypatch.setattr('hireme.provider.shutil.which',lambda _:'/fixture/claude')
    calls=0
    def run(args,**kw):
        nonlocal calls
        if args[1:]==['auth','status']:return SimpleNamespace(returncode=0,stdout=json.dumps({'loggedIn':True,'authMethod':'claude.ai','subscriptionType':'pro'}))
        calls+=1
        if calls==1:return SimpleNamespace(returncode=0,stdout=json.dumps({'structured_output':{'answer_id':None},'usage':{'input_tokens':50,'output_tokens':2}}))
        return SimpleNamespace(returncode=1,stdout='',stderr='synthetic error')
    monkeypatch.setattr('hireme.provider.subprocess.run',run)
    p=ManagedProvider(store,90)
    p.choose_answer('test',[])
    with pytest.raises(Blocked,match='provider_error'):p.choose_answer('test',[])
    rows=list(store.db.execute('SELECT usage,success FROM model_request_metadata ORDER BY request_id'))
    assert json.loads(rows[0]['usage'])=={'input_tokens':50,'output_tokens':2}
    assert json.loads(rows[1]['usage'])=={} and rows[1]['success']==0


@pytest.mark.parametrize('provider,usage,expected',[
 ('openai-api',{'prompt_tokens':10,'completion_tokens':4,'total_tokens':14}, {'input_tokens':10,'output_tokens':4}),
 ('anthropic-api',{'input_tokens':10,'output_tokens':4,'cache_read_input_tokens':30,'other':'private'}, {'input_tokens':10,'output_tokens':4,'cache_read_input_tokens':30}),
])
def test_api_usage_retained_without_extra_requests_or_private_metadata(store,monkeypatch,provider,usage,expected):
    monkeypatch.setattr('hireme.connections.api_key',lambda *a:'fixture-key')
    store.update_settings({'provider':provider,'provider_model':'fixture-model'})
    result={'usage':usage,'choices':[{'message':{'content':'{"answer_id":null}'}}]} if provider=='openai-api' else {'usage':usage,'content':[{'type':'text','text':'{"answer_id":null}'}]}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return json.dumps(result).encode()
    class Opener:
        def open(self,*a,**kw):return Response()
    monkeypatch.setattr('urllib.request.build_opener',lambda *a:Opener())
    p=APIProvider(store)
    assert p.choose_answer('test',[]) is None and p.last_usage==expected
