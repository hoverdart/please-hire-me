import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from hireme.connections import save_key,status,api_key
from hireme.provider import ManagedProvider,CodexProvider
from hireme.util import Blocked


def test_keys_are_provider_bound_private_and_never_in_snapshot(store):
    save_key(store,'openai-api','synthetic-private-key-123456')
    store.update_settings({'provider':'openai-api','provider_model':'synthetic-model'})
    assert status(store)['ready']
    assert 'synthetic-private' not in json.dumps(store.snapshot())
    assert (store.root/'integrations/provider-key.json').stat().st_mode & 0o777==0o600
    with pytest.raises(Blocked):api_key(store,'anthropic-api')


def test_model_budget_is_atomic_durable_and_counts_failed_requests(store):
    store.update_settings({'max_model_requests_per_cycle':2,'max_model_requests_per_day':3})
    store.active_run_id='batch-1'
    store.reserve_model_request();store.reserve_model_request()
    with pytest.raises(Blocked,match='model_budget_exhausted'):store.reserve_model_request()
    store.active_run_id='batch-2';store.reserve_model_request()
    with pytest.raises(Blocked,match='model_budget_exhausted'):store.reserve_model_request()
    from hireme.store import Store
    reopened=Store(store.root);reopened.active_run_id='batch-3'
    with pytest.raises(Blocked,match='model_budget_exhausted'):reopened.reserve_model_request()
    reopened.close()


def test_managed_provider_rejects_output_outside_schema(store):
    p=ManagedProvider(store,10)
    p.backend=SimpleNamespace(request=lambda *a:{'answer_id':'invented'})
    p.backend_configuration=(store.settings()['provider'],store.settings()['provider_model'],store.settings()['model_effort'])
    with pytest.raises(Blocked,match='provider_invalid_output'):p.choose_answer('Which?', [{'id':'one'}])
    assert store.db.execute('SELECT count(*) FROM model_requests').fetchone()[0]==1


def test_codex_no_config_tools_key_or_prompt_argv(monkeypatch):
    monkeypatch.setattr('hireme.provider.shutil.which',lambda _: '/fake/codex')
    monkeypatch.setenv('OPENAI_API_KEY','do-not-inherit');seen=[]
    def run(args,**kwargs):
        assert 'OPENAI_API_KEY' not in kwargs['env']
        if args[1:]==['login','status']:return SimpleNamespace(returncode=0,stdout='',stderr='Logged in using ChatGPT')
        seen.append(args)
        assert 'private question' not in ' '.join(args)
        assert 'private question' in kwargs['input']
        Path(args[args.index('--output-last-message')+1]).write_text('{"answer_id":null}')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr('hireme.provider.subprocess.run',run)
    assert CodexProvider().choose_answer('private question',[]) is None
    args=seen[0];assert '--ignore-user-config' in args and '--ephemeral' in args
    assert 'shell_tool' in args and 'plugins' in args and 'read-only' in args


def test_api_mode_fixed_endpoint_no_redirect_and_no_tools(store,monkeypatch):
    from hireme.provider import APIProvider
    save_key(store,'openai-api','synthetic-private-key-123456');store.update_settings({'provider':'openai-api','provider_model':'fixture-model'})
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return json.dumps({'choices':[{'message':{'content':'{"answer_id":null}'}}]}).encode()
    class Opener:
        def open(self,request,timeout):
            seen.append(request);assert timeout==10;return Response()
    monkeypatch.setattr('urllib.request.build_opener',lambda handler:Opener())
    assert APIProvider(store,10).choose_answer('test',[]) is None
    request=seen[0];body=json.loads(request.data)
    assert request.full_url=='https://api.openai.com/v1/chat/completions'
    assert not body.get('tools') and body['max_completion_tokens']==2000 and body['store'] is False


def test_bad_limits_and_model_rejected(store):
    for changes in ({'max_model_requests_per_day':0},{'max_attempts_per_cycle':51},{'provider_model':'bad\nmodel'},{'provider':'unknown'}):
        with pytest.raises(ValueError):store.update_settings(changes)
