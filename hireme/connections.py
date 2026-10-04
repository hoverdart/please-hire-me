"""Applicant-specific provider credentials. Never return secrets to the dashboard."""
from __future__ import annotations
import json
import shutil
import subprocess
from .util import atomic_json, private_dir, Blocked


def key_path(store):return private_dir(store.root/'integrations')/'provider-key.json'


def save_key(store,provider,key):
    if provider not in ('openai-api','anthropic-api'):raise ValueError('Select an API provider first')
    if not isinstance(key,str) or not 20<=len(key)<=512 or any(c.isspace() for c in key):raise ValueError('Invalid API key')
    atomic_json(key_path(store),{'provider':provider,'key':key})


def api_key(store,provider):
    path=key_path(store)
    if path.is_symlink():raise Blocked('provider_unavailable','Unsafe key file')
    try:data=json.loads(path.read_text())
    except (OSError,ValueError):raise Blocked('provider_unavailable','Save an API key for the selected provider') from None
    if not isinstance(data,dict) or data.get('provider')!=provider:raise Blocked('provider_unavailable','Key belongs to a different provider')
    key=data.get('key')
    if not isinstance(key,str) or not 20<=len(key)<=512 or any(c.isspace() for c in key):raise Blocked('provider_unavailable','Save a valid API key')
    return key


def status(store,verify=False):
    s=store.settings();provider=s['provider'];result={'provider':provider,'model':s['provider_model'],'ready':False}
    if provider.endswith('-api'):
        try:api_key(store,provider);result['ready']=bool(s['provider_model'])
        except Blocked:pass
        result['message']='API key and model selected. Credentials are checked on the first request.' if result['ready'] else 'Save an API key and model ID.'
        return result
    binary=shutil.which('claude' if provider=='claude-cli' else 'codex')
    result['installed']=bool(binary);result['ready']=bool(binary)
    result['message']='Installed; check login before enabling applications.' if binary else 'Install the selected CLI, then sign in in your terminal.'
    if verify and binary:
        from .provider import cli_env
        args=[binary,'auth','status'] if provider=='claude-cli' else [binary,'login','status']
        try:
            r=subprocess.run(args,capture_output=True,text=True,timeout=10,env=cli_env())
            if provider=='claude-cli':
                data=json.loads(r.stdout);ready=not r.returncode and data.get('loggedIn') and data.get('authMethod')=='claude.ai' and not data.get('apiKeySource') and data.get('subscriptionType') in ('pro','max','team','enterprise')
            else:ready=not r.returncode and 'chatgpt' in (r.stdout+r.stderr).casefold()
            result['ready']=bool(ready);result['message']='Subscription login verified.' if ready else 'Sign into a subscription account in your terminal; API login is a separate provider choice.'
        except (OSError,ValueError,subprocess.TimeoutExpired):result.update(ready=False,message='Could not verify CLI login.')
    return result
