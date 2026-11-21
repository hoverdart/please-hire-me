import json
import zipfile

import pytest

from hireme.accounts import AccountVault
from hireme.backup import create_backup
from hireme.util import Blocked
from hireme.workday_account_policy import configure,credentials,disable,path,status

URL='https://acme.wd1.myworkdayjobs.com/External/job/US/Synthetic_R12345'
JOB={'url':URL,'host':'acme.wd1.myworkdayjobs.com'}
SECRET='Example42!xyz'


def saved_policy(store):
    store.update_settings({'live_enabled':False})
    return configure(store,SECRET,SECRET,accept_account_terms=True)


def test_chosen_password_is_private_and_bound_to_main_email_and_tenant(store):
    result=saved_policy(store)
    assert result['enabled'] and SECRET not in json.dumps(result)
    assert path(store).stat().st_mode&0o777==0o600
    first=credentials(store,JOB,create=True)
    assert first=={'email':store.facts()['email']['value'],'password':SECRET}
    assert credentials(store,JOB)==first
    assert not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()
    assert SECRET not in json.dumps(store.snapshot()) and SECRET.encode() not in store.path.read_bytes()
    assert disable(store)['enabled'] is False
    assert credentials(store,JOB)==first


def test_normal_backup_excludes_registration_policy_and_credentials(store,tmp_path):
    saved_policy(store);credentials(store,JOB,create=True)
    backup=tmp_path/'history.zip';create_backup(store,backup)
    with zipfile.ZipFile(backup) as archive:
        assert not any('integrations' in name for name in archive.namelist())
        assert all(SECRET.encode() not in archive.read(name) for name in archive.namelist())


def test_default_password_change_does_not_replace_existing_tenant_credential(store):
    saved_policy(store);original=credentials(store,JOB,create=True)
    configure(store,'OtherSecret42!','OtherSecret42!',accept_account_terms=True)
    assert credentials(store,JOB,create=True)==original
    other={**JOB,'url':URL.replace('acme.wd1','other.wd1'),'host':'other.wd1.myworkdayjobs.com'}
    assert credentials(store,other,create=True)['password']=='OtherSecret42!'


def test_missing_credential_after_restore_never_generates_a_replacement(store):
    saved_policy(store);credentials(store,JOB,create=True)
    vault=AccountVault(store);key=vault.begin_creation('https://'+JOB['host'],'acme')
    vault.finish_creation(key,confirmed=True)
    (vault.directory/(key+'.json')).unlink()
    with pytest.raises(Blocked,match='account_credentials_unavailable'):credentials(store,JOB,create=True)


@pytest.mark.parametrize('password,confirmation,accept',[
    ('short','short',True),(SECRET,'different',True),(SECRET,SECRET,False),
    (SECRET+'\n',SECRET+'\n',True),
])
def test_invalid_policy_never_writes_credentials(store,password,confirmation,accept):
    store.update_settings({'live_enabled':False})
    with pytest.raises(ValueError):configure(store,password,confirmation,accept_account_terms=accept)
    assert not path(store).exists()


def test_live_configuration_cannot_race_worker_account_creation(store):
    with pytest.raises(ValueError,match='Pause'):configure(store,SECRET,SECRET,accept_account_terms=True)
    assert not status(store)['enabled']


def test_unconfigured_workday_does_not_fall_back_to_a_generated_password(store):
    with pytest.raises(Blocked,match='workday_registration_not_configured'):credentials(store,JOB,create=True)


@pytest.mark.parametrize('url,host',[(URL,'evil.invalid'),
    (URL.replace('myworkdayjobs.com','myworkdayjobs.com.evil.invalid'),'acme.wd1.myworkdayjobs.com.evil.invalid')])
def test_credentials_are_not_provided_for_unverified_workday_destinations(store,url,host):
    saved_policy(store)
    with pytest.raises((ValueError,Blocked)):credentials(store,{'url':url,'host':host},create=True)
