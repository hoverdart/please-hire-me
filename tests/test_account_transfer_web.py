import base64
import json

import pytest

from hireme.account_transfer_web import export_payload,import_payload
from hireme.accounts import AccountVault
from hireme.backup import create_backup,restore_backup
from hireme.store import Store,worker_lock

PHRASE='synthetic browser transfer phrase only'
ORIGIN='https://careers.example.com'


def saved_account(store):
    vault=AccountVault(store)
    credential=vault.credentials(ORIGIN,'Synthetic Employer',create=True)
    key=vault.begin_creation(ORIGIN,'Synthetic Employer')
    vault.finish_creation(key,confirmed=False)
    store.update_settings({'live_enabled':False})
    return key,credential


def test_dashboard_transport_encrypts_and_recovers_matching_restored_history(store,tmp_path):
    key,credential=saved_account(store)
    payload=export_payload(store,PHRASE,PHRASE)
    assert credential['password'].encode() not in payload and credential['email'].encode() not in payload
    assert PHRASE.encode() not in payload and not list(store.root.glob('.account-transfer-*'))
    archive=tmp_path/'history.zip';create_backup(store,archive)
    root=tmp_path/'restored';restore_backup(archive,root)
    restored=Store(root)
    try:
        result=import_payload(restored,base64.b64encode(payload).decode(),PHRASE)
        assert result=={'accounts':1,'states_preserved':True,'paused':True}
        assert AccountVault(restored).credentials(ORIGIN,'Synthetic Employer')==credential
        assert restored.db.execute('SELECT state FROM employer_accounts WHERE id=?',(key,)).fetchone()[0]=='uncertain'
        assert not restored.settings()['live_enabled'] and not list(root.glob('.account-transfer-*'))
        assert credential['password'] not in json.dumps(restored.snapshot())
        assert PHRASE not in str([dict(row) for row in restored.db.execute('SELECT * FROM events')])
    finally: restored.close()


@pytest.mark.parametrize('phrase,confirmation',[(None,None),('short','short'),('x'*1025,'x'*1025),(PHRASE,'different phrase'),('x'*12+'\ud800','x'*12+'\ud800')])
def test_export_validation_precedes_private_account_reads(store,monkeypatch,phrase,confirmation):
    def forbidden(*args): raise AssertionError('Read private account credentials')
    monkeypatch.setattr('hireme.account_transfer_web.export_accounts',forbidden)
    changes=store.db.total_changes
    with pytest.raises(ValueError): export_payload(store,phrase,confirmation)
    assert store.db.total_changes==changes and not list(store.root.glob('.account-transfer-*'))


@pytest.mark.parametrize('encoded',[None,'','not base64','a'*2796205,base64.b64encode(b'not an encrypted transfer').decode()])
def test_import_rejects_invalid_transport_without_password_writes(store,encoded):
    saved_account(store)
    before=store.snapshot()
    with pytest.raises(ValueError): import_payload(store,encoded,PHRASE)
    assert store.snapshot()==before and not list(store.root.glob('.account-transfer-*'))


def test_transport_reports_pause_worker_and_missing_password_requirements(store):
    key,_=saved_account(store)
    with worker_lock(store.root):
        with pytest.raises(ValueError,match='Wait for active work'): export_payload(store,PHRASE,PHRASE)
    store.update_settings({'live_enabled':True})
    with pytest.raises(ValueError,match='Pause'): export_payload(store,PHRASE,PHRASE)
    store.update_settings({'live_enabled':False})
    (AccountVault(store).directory/(key+'.json')).unlink()
    with pytest.raises(ValueError,match='Stored employer passwords are unavailable'): export_payload(store,PHRASE,PHRASE)
    assert not list(store.root.glob('.account-transfer-*'))
