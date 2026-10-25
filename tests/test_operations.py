import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from hireme.util import atomic_json,now


def health_ready(store,monkeypatch):
    from hireme import health
    monkeypatch.setattr('hireme.release_check._integrations',lambda *_:{'ready':True})
    store.update_settings({'live_enabled':False})
    atomic_json(store.root/'operations/backup-ack.json',{'verified_at':now()})
    store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',('synthetic',now(),now(),'paused',0,'{}'))
    return health.check_health(store.root,services=False,verify_login=False)


def test_health_reads_current_wal_without_changing_applicant_rows(store,monkeypatch):
    before=[tuple(row) for row in store.db.execute('SELECT * FROM facts')]
    result=health_ready(store,monkeypatch)
    assert result['ready'] and result['status']=='ok'
    assert result['latest_run']['status']=='paused'
    assert [tuple(row) for row in store.db.execute('SELECT * FROM facts')]==before
    assert 'Test Person' not in json.dumps(result) and 'candidate.invalid' not in json.dumps(result)


def test_health_reports_storage_and_backup_failure_without_initializing_missing_root(tmp_path,monkeypatch):
    from hireme.health import check_health
    missing=tmp_path/'missing'
    result=check_health(missing,services=False,verify_login=False)
    assert not result['ready'] and 'health_check_unavailable' in result['failures']
    assert not missing.exists()


def test_health_detects_stalled_worker_and_stale_backups(store,monkeypatch):
    from hireme import health
    health_ready(store,monkeypatch)
    store.db.execute("UPDATE runs SET started='2020-01-01T00:00:00+00:00',finished=NULL,status='running'")
    (store.root/'operations/backup-ack.json').unlink()
    result=health.check_health(store.root,services=False,verify_login=False)
    assert 'worker_stalled' in result['failures'] and 'off_device_backup_overdue' in result['warnings']


def test_pi_health_cannot_claim_ready_when_kernel_ignores_memory_limits(store,monkeypatch):
    from hireme.health import check_health
    health_ready(store,monkeypatch)
    store.update_settings({'deployment':'pi'})
    monkeypatch.setattr('hireme.pi.memory_controller_available',lambda:False)
    result=check_health(store.root,services=False,verify_login=False)
    assert not result['ready'] and result['status']=='critical'
    assert result['failures']==['memory_controller_unavailable']
    monkeypatch.setattr('hireme.pi.memory_controller_available',lambda:True)
    assert check_health(store.root,services=False,verify_login=False)['ready']


def test_malformed_backup_status_is_a_health_failure(store,monkeypatch):
    from hireme.health import check_health
    health_ready(store,monkeypatch)
    atomic_json(store.root/'operations/backup-ack.json',[])
    result=check_health(store.root,services=False,verify_login=False)
    assert not result['ready'] and 'health_check_unavailable' in result['failures']


def test_health_requires_recent_discovery_only_while_applications_are_enabled(store,monkeypatch):
    from hireme.health import check_health
    health_ready(store,monkeypatch)
    store.update_settings({'live_enabled':True})
    assert 'discovery_checks_overdue' in check_health(store.root,services=False,verify_login=False)['warnings']
    from hireme.discovery import source_result
    source_result(store,'synthetic',[])
    assert 'discovery_checks_overdue' not in check_health(store.root,services=False,verify_login=False)['warnings']


def test_low_storage_refuses_backup_without_partial_files(store,tmp_path,monkeypatch):
    from hireme.backup import create_backup
    monkeypatch.setattr('hireme.storage.shutil.disk_usage',lambda _:SimpleNamespace(free=1,total=1024**3))
    with pytest.raises(ValueError,match='Insufficient disk space'):create_backup(store,tmp_path/'backup.zip')
    assert not (tmp_path/'backup.zip').exists()
    assert not list(tmp_path.glob('.backup-*'))


def test_remote_key_cannot_execute_shell_or_arbitrary_commands(store):
    from hireme.remote_ops import execute
    for command in ['', 'sh', 'cat ledger.sqlite3','backup; rm -rf /','health --data-dir /other']:
        with pytest.raises(ValueError,match='permits'):execute(store.root,command)


def test_remote_health_uses_worker_path_without_forwarding_other_unit_environment(store,tmp_path,monkeypatch,capsys):
    from hireme.remote_ops import execute
    home=tmp_path/'home';unit=home/'.config/systemd/user/please-hire-me-worker.service'
    unit.parent.mkdir(parents=True)
    unit.write_text('Environment="PATH=/synthetic/cli:/usr/bin"\nEnvironment="SENSITIVE_VALUE=not-forwarded"\n')
    monkeypatch.setattr(Path,'home',lambda:home)
    monkeypatch.setenv('PATH','/usr/bin');monkeypatch.delenv('SENSITIVE_VALUE',raising=False)
    def check(_):
        assert os.environ['PATH']=='/synthetic/cli:/usr/bin'
        assert 'SENSITIVE_VALUE' not in os.environ
        return {'ready':True}
    monkeypatch.setattr('hireme.health.check_health',check)
    assert execute(store.root,'health')==0
    assert json.loads(capsys.readouterr().out)['ready']


def test_remote_backup_ack_is_bounded_and_contains_only_verified_metadata(store):
    from hireme.remote_ops import execute
    good={'verified_at':now(),'sha256':'a'*64,'bytes':1234,'applications':119}
    assert execute(store.root,'backup-ack',input_stream=io.StringIO(json.dumps(good)))==0
    assert json.loads((store.root/'operations/backup-ack.json').read_text())==good
    for bad in [{**good,'extra':'private data'},{**good,'verified_at':'2000-01-01T00:00:00+00:00'},[],{**good,'bytes':-1}]:
        with pytest.raises(ValueError):execute(store.root,'backup-ack',input_stream=io.StringIO(json.dumps(bad)))


def test_streamed_backup_restores_uncertainty_and_excludes_credentials(store,job,package,tmp_path):
    from hireme.remote_ops import stream_backup
    from hireme.backup import restore_backup
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown','Synthetic uncertain receipt')
    atomic_json(store.root/'integrations/gmail-token.json',{'token':'synthetic-secret'})
    stream=io.BytesIO();stream_backup(store,stream)
    archive=tmp_path/'stream.zip';archive.write_bytes(stream.getvalue())
    root=tmp_path/'restored';restore_backup(archive,root)
    from hireme.store import Store
    restored=Store(root)
    try:
        assert restored.db.execute('SELECT state FROM applications').fetchone()[0]=='unknown'
        assert not restored.settings()['live_enabled']
        assert not (root/'integrations/gmail-token.json').exists()
    finally:restored.close()
    assert not list((store.root/'tmp').iterdir())


def test_agent_failure_does_not_acknowledge_or_prune_prior_backups(tmp_path,monkeypatch):
    from hireme import backup_agent
    config={'state_directory':str(tmp_path/'state'),'destination':str(tmp_path/'backups')}
    monkeypatch.setattr(backup_agent,'load_config',lambda _:config)
    monkeypatch.setattr(backup_agent,'_remote',lambda *a,**kw:(_ for _ in ()).throw(OSError('synthetic outage')))
    monkeypatch.setattr(backup_agent,'_notify',lambda:None)
    monkeypatch.setattr(backup_agent,'prune_verified',lambda _:pytest.fail('Cannot prune after a failed operation'))
    result=backup_agent.run_agent(tmp_path/'config')
    assert not result['ready'] and not result['last_backup'] and not result['last_acknowledged']
    assert result['error_type']=='OSError'


def test_invalid_remote_health_becomes_actionable_failure_instead_of_crashing(tmp_path,monkeypatch):
    from hireme import backup_agent
    monkeypatch.setattr(backup_agent,'load_config',lambda _:{'state_directory':str(tmp_path/'state')})
    monkeypatch.setattr(backup_agent,'_remote',lambda *a,**kw:{'ready':False,'failures':None,'warnings':'invalid'})
    monkeypatch.setattr(backup_agent,'_notify',lambda:None)
    result=backup_agent.run_agent(tmp_path/'config')
    assert not result['ready'] and result['error_type']=='ValueError'


def test_operations_config_rejects_ssh_option_injection_and_exposed_keys(tmp_path):
    from hireme.backup_agent import load_config
    for name in ['ssh-key','age-key']:
        (tmp_path/name).write_text('synthetic');(tmp_path/name).chmod(0o600)
    config={'host':'applicant@pi.local','identity':str(tmp_path/'ssh-key'),'age_identity':str(tmp_path/'age-key'),
            'age_binary':'/usr/bin/true','ssh_binary':'/usr/bin/ssh','destination':str(tmp_path/'backups'),
            'state_directory':str(tmp_path/'state'),'recipient':'age1'+'a'*58}
    path=tmp_path/'config.json';atomic_json(path,config)
    assert load_config(path)['host']=='applicant@pi.local'
    atomic_json(path,{**config,'host':'-oProxyCommand=unexpected'})
    with pytest.raises(ValueError,match='SSH user and host'):load_config(path)
    atomic_json(path,config);(tmp_path/'ssh-key').chmod(0o644)
    with pytest.raises(ValueError,match='private regular'):load_config(path)
    (tmp_path/'ssh-key').chmod(0o600)
    alias=tmp_path/'ssh-binary';alias.symlink_to('/usr/bin/ssh')
    atomic_json(path,{**config,'ssh_binary':str(alias)})
    assert load_config(path)['ssh_binary']==str(Path('/usr/bin/ssh').resolve())


def test_agent_acknowledges_only_after_restoration_and_retains_recoverable_status(tmp_path,monkeypatch):
    from hireme import backup_agent
    config={'state_directory':str(tmp_path/'state'),'destination':str(tmp_path/'backups')}
    monkeypatch.setattr(backup_agent,'load_config',lambda _:config)
    order=[]
    def remote(config,command,payload=None):
        order.append(command)
        if command=='backup-ack':return {'recorded':True}
        return {'ready':True,'warnings':[],'failures':[]}
    monkeypatch.setattr(backup_agent,'_remote',remote)
    def backup(_):
        order.append('verified_restore')
        return {'verified_at':now(),'sha256':'a'*64,'bytes':1234,'applications':119}
    monkeypatch.setattr(backup_agent,'create_encrypted_backup',backup)
    monkeypatch.setattr(backup_agent,'prune_verified',lambda _:None)
    first=backup_agent.run_agent(tmp_path/'config')
    assert first['ready'] and order==['health','verified_restore','backup-ack','health']
    order.clear()
    second=backup_agent.run_agent(tmp_path/'config')
    assert second['ready'] and order==['health']


def test_agent_retention_preserves_newest_two_unverified_files_and_other_archives(tmp_path):
    from hireme.backup_agent import prune_verified
    directory=tmp_path/'backups';directory.mkdir()
    for i in range(4):
        name=f'backup-2020010{i+1}T000000Z-aaaaaaaa.zip.age'
        p=directory/name;p.write_bytes(b'synthetic')
        atomic_json(p.with_name(name+'.json'),{'filename':name,'verified':True,'verified_at':f'2020-01-0{i+1}T00:00:00+00:00'})
    unknown=directory/'unverified.age';unknown.write_bytes(b'preserve')
    unrelated=directory/'retired-workspaces.tar.age';unrelated.write_bytes(b'preserve')
    prune_verified({'destination':str(directory)})
    assert len(list(directory.glob('backup-*.zip.age')))==2
    assert unknown.exists() and unrelated.exists()


@pytest.mark.skipif(not shutil.which('age') or not shutil.which('age-keygen'),reason='Optional age CLI is not installed')
def test_real_age_backup_roundtrip_rejects_wrong_key_and_corruption(store,tmp_path):
    from hireme.backup import create_backup
    from hireme.backup_agent import verify_encrypted
    keys=[]
    for name in ['key','wrong-key']:
        p=tmp_path/name
        subprocess.run(['age-keygen','-o',str(p)],check=True,capture_output=True)
        keys.append(p)
    recipient=subprocess.check_output(['age-keygen','-y',str(keys[0])],text=True).strip()
    plain=tmp_path/'history.zip';create_backup(store,plain)
    encrypted=tmp_path/'history.age'
    subprocess.run(['age','-r',recipient,'-o',str(encrypted),str(plain)],check=True)
    config={'age_binary':shutil.which('age'),'age_identity':str(keys[0])}
    assert verify_encrypted(config,encrypted,tmp_path)['restores_paused']
    with pytest.raises(ValueError,match='authentication'):
        verify_encrypted({**config,'age_identity':str(keys[1])},encrypted,tmp_path)
    data=bytearray(encrypted.read_bytes());data[-1]^=1;encrypted.write_bytes(data)
    with pytest.raises(ValueError,match='authentication'):verify_encrypted(config,encrypted,tmp_path)
    assert not list(tmp_path.glob('.verify-*'))
