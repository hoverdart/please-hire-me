import json
import os
import pytest

from hireme.accounts import AccountVault, account_key
from hireme.backup import create_backup, restore_backup
from hireme.store import Store
from hireme.util import Blocked

ORIGIN = 'https://careers.example.com'


def test_supplied_credentials_are_reused_without_account_creation_or_secret_logging(store,tmp_path):
    store.update_settings({'live_enabled':False})
    vault=AccountVault(store);email=store.facts()['email']['value'];password='User-chosen-only-test-password42!'
    assert vault.save_supplied(ORIGIN,'Acme',email,password,password)=={'saved':True,'account_created':False}
    assert vault.credentials(ORIGIN,'Acme',create=True)=={'email':email,'password':password}
    assert not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()
    assert password not in json.dumps(store.snapshot()) and password.encode() not in store.path.read_bytes()
    path=vault.directory/(account_key(ORIGIN,'Acme')+'.json');assert path.stat().st_mode & 0o777==0o600
    before=path.read_bytes()
    changes=store.db.total_changes
    assert vault.save_supplied(ORIGIN,'Acme',email,password,password)=={'saved':True,'account_created':False}
    assert path.read_bytes()==before and store.db.total_changes==changes
    with pytest.raises(ValueError,match='already exist'):vault.save_supplied(ORIGIN,'Acme',email,'Another-long-password-only42!','Another-long-password-only42!')
    assert path.read_bytes()==before
    archive=tmp_path/'history.zip';create_backup(store,archive)
    import zipfile
    with zipfile.ZipFile(archive) as z:
        assert all(password.encode() not in z.read(n) for n in z.namelist())


@pytest.mark.parametrize('field,value',[
    ('origin',None),('origin','https://careers.example.com/register'),('email','different@candidate.invalid'),
    ('password','too-short'),('password','x'*20+'\n'),('confirmation','mismatch'),('company',''),
])
def test_supplied_credential_validation_never_writes_secrets(store,field,value):
    store.update_settings({'live_enabled':False})
    vault=AccountVault(store);data=dict(origin=ORIGIN,company='Acme',email=store.facts()['email']['value'],password='User-chosen-only-test-password42!',confirmation='User-chosen-only-test-password42!');data[field]=value
    with pytest.raises(ValueError):vault.save_supplied(**data)
    assert not list(vault.directory.iterdir()) and not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()


def test_supplied_credentials_require_pause_and_worker_lock(store):
    from hireme.store import worker_lock
    vault=AccountVault(store);args=(ORIGIN,'Acme',store.facts()['email']['value'],'User-chosen-only-test-password42!','User-chosen-only-test-password42!')
    with pytest.raises(ValueError,match='Pause'):vault.save_supplied(*args)
    store.update_settings({'live_enabled':False})
    with worker_lock(store.root):
        with pytest.raises(Blocked,match='worker_busy'):vault.save_supplied(*args)
    assert not list(vault.directory.iterdir())


def test_supplied_credentials_release_only_the_matching_employer_hold(store,job):
    from hireme.job_holds import hold,ready
    from hireme.util import digest
    other={**job,'id':digest('other-account-job'),'company':'Another Employer'};store.upsert_job(other)
    hold(store,job,'account_credentials_unavailable');hold(store,other,'account_credentials_unavailable')
    assert not ready(store,job) and not ready(store,other)
    store.update_settings({'live_enabled':False})
    password='User-chosen-only-test-password42!'
    AccountVault(store).save_supplied('https://'+job['host'],job['company'],store.facts()['email']['value'],password,password)
    assert ready(store,job) and not ready(store,other)
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_credentials_are_private_scoped_and_not_logged(store):
    vault = AccountVault(store)
    value = vault.credentials(ORIGIN, 'Acme', create=True)
    assert value == vault.credentials(ORIGIN, 'Acme')
    other = vault.credentials(ORIGIN, 'Other employer', create=True)
    assert value['password'] != other['password']
    path = vault.directory / (account_key(ORIGIN, 'Acme') + '.json')
    assert os.stat(path).st_mode & 0o777 == 0o600
    key = vault.begin_creation(ORIGIN, 'Acme')
    vault.finish_creation(key, confirmed=True)
    assert store.db.execute('SELECT state FROM employer_accounts WHERE id=?', (key,)).fetchone()[0] == 'confirmed'
    assert value['password'] not in json.dumps([dict(r) for r in store.db.execute('SELECT * FROM events')])
    assert value['password'].encode() not in store.path.read_bytes()


def test_crash_and_uncertain_creation_cannot_retry(store):
    vault = AccountVault(store)
    vault.credentials(ORIGIN, 'Acme', create=True)
    key = vault.begin_creation(ORIGIN, 'Acme')
    store.recover()
    assert store.db.execute('SELECT state FROM employer_accounts WHERE id=?', (key,)).fetchone()[0] == 'uncertain'
    with pytest.raises(Blocked, match='account_creation_held'):
        vault.begin_creation(ORIGIN, 'Acme')
    with pytest.raises(Blocked, match='account_creation_held'):
        vault.finish_creation(key, confirmed=True)


def test_restore_preserves_intent_without_credentials(store, tmp_path):
    vault = AccountVault(store)
    secret = vault.credentials(ORIGIN, 'Acme', create=True)['password']
    vault.begin_creation(ORIGIN, 'Acme')
    archive = tmp_path / 'backup.zip'
    create_backup(store, archive)
    destination = tmp_path / 'restored'
    restore_backup(archive, destination)
    restored = Store(destination)
    try:
        assert secret.encode() not in restored.path.read_bytes()
        with pytest.raises(Blocked, match='account_credentials_unavailable'):
            AccountVault(restored).credentials(ORIGIN, 'Acme', create=True)
        assert restored.db.execute('SELECT state FROM employer_accounts').fetchone()[0] == 'uncertain'
    finally:
        restored.close()


def test_paused_creation_never_records_intent(store):
    vault = AccountVault(store)
    vault.credentials(ORIGIN, 'Acme', create=True)
    store.run_generation = store.control_generation()
    store.update_settings({'live_enabled': False})
    with pytest.raises(Blocked, match='paused'):
        vault.begin_creation(ORIGIN, 'Acme')
    assert not store.db.execute('SELECT 1 FROM employer_accounts').fetchone()


@pytest.mark.parametrize('origin', ['http://careers.example.com', 'https://u:p@careers.example.com',
                                  'https://careers.example.com/create', 'https://careers.example.com?redirect=x'])
def test_rejects_ambiguous_origins(origin):
    with pytest.raises(ValueError):
        account_key(origin, 'Acme')


def test_changed_identity_and_symlinks_are_held(store, tmp_path):
    vault = AccountVault(store)
    vault.credentials(ORIGIN, 'Acme', create=True)
    store.put_facts({'email': 'different@candidate.invalid'})
    with pytest.raises(Blocked, match='account_credentials_unavailable'):
        vault.credentials(ORIGIN, 'Acme')
    path = vault.directory / (account_key(ORIGIN, 'Other') + '.json')
    path.symlink_to(tmp_path / 'outside')
    with pytest.raises(Blocked, match='account_credentials_unavailable'):
        vault.credentials(ORIGIN, 'Other', create=True)


def test_signin_crash_is_held_and_manual_confirmation_is_scoped(store):
    vault=AccountVault(store)
    vault.credentials(ORIGIN,'Acme',create=True)
    key=vault.begin_creation(ORIGIN,'Acme')
    vault.finish_creation(key,confirmed=True)
    vault.begin_signin(key)
    store.recover()
    with pytest.raises(Blocked,match='account_creation_held'):vault.begin_signin(key)
    with pytest.raises(ValueError):vault.reconcile(key,'short')
    vault.reconcile(key,'Verified account and signed-in session in dedicated browser')
    assert store.db.execute('SELECT state FROM employer_accounts WHERE id=?',(key,)).fetchone()[0]=='confirmed'
    with pytest.raises(ValueError):vault.reconcile(key,'Already confirmed account cannot be reconciled twice')
    with pytest.raises(ValueError,match='Applicant identity cannot change'):
        store.put_facts({'email':'another@candidate.invalid'})
