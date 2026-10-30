import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from hireme.util import Blocked,atomic_json


def test_read_features_do_not_require_send_permission_and_reports_do(store):
    from hireme.gmail import required_scopes,READ_SCOPES,SEND_SCOPES,GmailClient
    assert required_scopes(store)==READ_SCOPES
    store.update_settings({'gmail_reports':True})
    assert set(required_scopes(store))==set(READ_SCOPES+SEND_SCOPES)
    from tests.test_gmail import Service
    service=Service('test@candidate.invalid',None);client=GmailClient(store,service)
    client.credentials=SimpleNamespace(has_scopes=lambda scopes:scopes==READ_SCOPES)
    with pytest.raises(Blocked,match='gmail_scopes_missing'):client.send_report('Fixture','body','id')
    assert not service.sent


def test_revoked_authorization_is_visible_without_assuming_oauth_project_mode(store):
    from hireme.gmail import check_connection,status
    def revoked(_):raise Blocked('gmail_reconnect_required')
    with pytest.raises(Blocked):check_connection(store,revoked)
    assert status(store)['access_state']=='gmail_reconnect_required'
    assert status(store)['oauth_publishing_status']=='unverified'


def test_refresh_persists_effective_grants_for_the_next_process(store,monkeypatch):
    from hireme import gmail
    from google.oauth2.credentials import Credentials
    from tests.test_gmail import Service
    from datetime import datetime,timedelta,timezone
    folder=store.root/'integrations'
    atomic_json(folder/'gmail-account.json',{'email':'test@candidate.invalid'})
    atomic_json(folder/'gmail-token.json',{'token':'old','token_uri':'https://oauth2.googleapis.com/token','scopes':gmail.SCOPES})
    credentials=Credentials(token='old',refresh_token='fixture-refresh',token_uri='https://oauth2.googleapis.com/token',
        client_id='fixture',client_secret='fixture',scopes=gmail.SCOPES,expiry=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=1))
    def refresh(_):
        credentials.token='new';credentials.expiry=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(hours=1)
        credentials._granted_scopes=gmail.READ_SCOPES
    monkeypatch.setattr(credentials,'refresh',refresh)
    monkeypatch.setattr(Credentials,'from_authorized_user_info',lambda _:credentials)
    service=Service('test@candidate.invalid',None)
    monkeypatch.setattr(gmail,'_service',lambda _:service)
    client=gmail.GmailClient(store)
    assert credentials.valid
    assert json.loads((folder/'gmail-token.json').read_text())['scopes']==gmail.READ_SCOPES
    with pytest.raises(Blocked,match='gmail_scopes_missing'):client.send_report('Fixture','body','id')
    assert not service.sent


@pytest.mark.parametrize('reports,grants,accepted',[
    (False,['readonly','send'],True),(True,['readonly'],False),(True,['send'],False),
])
def test_incremental_consent_uses_granted_scopes_and_preserves_token_on_failure(store,monkeypatch,reports,grants,accepted):
    from hireme import gmail
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from tests.test_gmail import Service
    store.update_settings({'gmail_reports':reports})
    folder=store.root/'integrations'
    atomic_json(folder/'gmail-client.json',{'installed':{'client_id':'fixture','client_secret':'fixture-secret'}})
    atomic_json(folder/'gmail-token.json',{'token':'old-token'})
    old=(folder/'gmail-token.json').read_bytes()
    granted=['https://www.googleapis.com/auth/gmail.'+g for g in grants]
    credentials=Credentials(token='new-token',refresh_token='new-refresh',token_uri='https://oauth2.googleapis.com/token',
        client_id='fixture',client_secret='fixture-secret',scopes=gmail.required_scopes(store),granted_scopes=granted)
    class Flow:
        oauth2session=SimpleNamespace(token={})
        def run_local_server(self,**kwargs):
            assert kwargs['include_granted_scopes']=='true'
            warning=Warning('Scope changed');warning.token={'access_token':'fixture-token','scope':granted}
            raise warning
    flow=Flow();flow.credentials=credentials
    monkeypatch.setattr(InstalledAppFlow,'from_client_secrets_file',lambda *a,**kw:flow)
    monkeypatch.setattr(gmail,'_service',lambda _:Service('test@candidate.invalid',None))
    if accepted:
        assert gmail.connect(store)['connected']
        assert set(json.loads((folder/'gmail-token.json').read_text())['scopes'])==set(granted)
    else:
        with pytest.raises(Blocked):gmail.connect(store)
        assert (folder/'gmail-token.json').read_bytes()==old


def test_local_source_config_preserves_ssh_format_and_requires_private_safe_paths(store,tmp_path):
    from hireme.backup_agent import load_config,create_encrypted_backup
    key=tmp_path/'key';key.write_text('fixture-key');key.chmod(0o600)
    config={'source_root':str(store.root),'age_identity':str(key),'age_binary':'/usr/bin/true',
        'destination':str(tmp_path/'backups'),'state_directory':str(tmp_path/'state'),'recipient':'age1'+'a'*58}
    path=tmp_path/'operations.json';atomic_json(path,config)
    loaded=load_config(path)
    (tmp_path/'backups').mkdir()
    with pytest.raises(ValueError,match='different filesystem'):create_encrypted_backup(loaded)
    (tmp_path/'unsafe').symlink_to(tmp_path/'backups',target_is_directory=True)
    atomic_json(path,{**config,'destination':str(tmp_path/'unsafe'/'subdir')})
    with pytest.raises(ValueError,match='symlinks'):load_config(path)


def test_linux_monitor_install_preserves_unrelated_cron_without_mutating_worker(tmp_path,monkeypatch):
    from hireme import backup_agent
    monkeypatch.setattr(backup_agent.sys,'platform','linux')
    monkeypatch.setattr(backup_agent,'load_config',lambda _:{'state_directory':str(tmp_path/'state')})
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout='0 */6 * * * existing-worker\n*/15 * * * * old # please-hire-me-operations\n')
    monkeypatch.setattr(backup_agent.subprocess,'run',run)
    assert backup_agent.install_agent(tmp_path/'config',tmp_path)['installed']
    text=calls[-1][1]['input']
    assert 'existing-worker' in text and text.count('# please-hire-me-operations')==1
    assert calls[-1][0]==['crontab','-']


def test_receipt_candidate_scan_cannot_send_mail_or_change_outcomes(store,job,package):
    from hireme.receipt_reconciliation import scan_candidates
    from tests.test_prior_applications import mail,Response
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    class ReadOnly:
        def users(self):return self
        def messages(self):return self
        def list(self,**kwargs):return Response({'messages':[{'id':'receipt'}]})
        def get(self,**kwargs):return Response(mail(job,identity='receipt',age=0,body=f'We have received your job application. {job["url"]}'))
    before=store.db.total_changes
    result=scan_candidates(store,client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=ReadOnly()))
    assert result['candidates'][0]['application_id']==aid and not result['auto_confirmation_enabled']
    assert store.db.total_changes==before and store.application_record(aid)['state']=='unknown'
