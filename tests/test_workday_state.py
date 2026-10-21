import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.util import Blocked, digest
from hireme.workday import VERSION, WorkdayController, WriteGrant, source_hash, status, verify_capability
from hireme.workday_profiles import NVIDIA, PROFILE_READ, bind_session, native_profile_read, profile_binding
from hireme.workday_state import CorrelatedStep, DraftJournal, DraftObservation


@pytest.fixture
def wd_job(store, job):
    value = {**job, 'host': NVIDIA, 'company': 'Nvidia',
             'url': f'https://{NVIDIA}/en-US/NVIDIAExternalCareerSite/job/US-CA/Engineering_JR12345'}
    value['id'] = digest(value['url']); store.upsert_job(value)
    return value


def session_response(store, **changes):
    request = SimpleNamespace(url=changes.get('url', PROFILE_READ), method=changes.get('method', 'GET'), post_data_buffer=None)
    return SimpleNamespace(request=request, status=changes.get('status', 200),
        json=lambda: changes.get('data', {'emailAddress': store.facts()['email']['value']}))


def journal(store, wd_job):
    session = bind_session(store, session_response(store))
    value = DraftJournal(store, wd_job, session, digest('reviewed-fixture'))
    observation = DraftObservation('remote-fixture', 'revision-1', session.owner_hash, wd_job['id'], digest({}))
    value.remember(observation, digest('official-fixture-read'))
    return value


def test_workday_status_never_creates_profile_or_treats_login_flag_as_proof(store):
    store.update_settings({'signed_in_portals': [NVIDIA], 'remote_drafts': True})
    before = list(store.root.rglob('*')); changes = store.db.total_changes
    result = status(store)
    assert result['version'] == VERSION
    assert all(not tenant['enabled'] for tenant in result['tenants'])
    assert result['tenants'][0]['protocol_reviewed'] is True
    assert result['tenants'][0]['reason'] == 'sign_in_required'
    assert list(store.root.rglob('*')) == before and store.db.total_changes == changes


@pytest.mark.parametrize('change', [dict(data={'emailAddress': 'other@candidate.invalid'}),
    dict(status=403), dict(data={}), dict(method='POST'), dict(url=PROFILE_READ+'?write=true')])
def test_authenticated_profile_is_exact_and_owner_bound(store, change):
    with pytest.raises(Blocked): bind_session(store, session_response(store, **change))
    assert not (store.root/'browser'/'workday-binding.json').exists()


def test_reviewed_profile_read_is_not_general_workday_authority():
    assert native_profile_read(NVIDIA, PROFILE_READ, 'GET')
    assert not native_profile_read(NVIDIA, PROFILE_READ, 'POST', b'{}')
    assert not native_profile_read(NVIDIA, PROFILE_READ.replace('nvidia', 'intel'), 'GET')


def test_history_changes_need_confirmation_and_cannot_supply_unknown_dates(store):
    row = store.put_history_entry('employment', {'organization': 'Acme', 'title': 'Engineer'}, source='Applicant review')
    assert store.history_entries() == []
    row = store.put_history_entry('employment', {**row['values'], 'start': '2025-06'},
        source='Applicant review', confirmed=True, entry_id=row['id'])
    from hireme.workday_history import resolve_field, validate_answer
    field = {'section': 'employment', 'history_entry_id': row['id'], 'history_key': 'start',
             'label': 'Start date year', 'type': 'number', 'options': [], 'maxlength': -1}
    answer = resolve_field(store, field)
    assert answer['value'] == '2025'
    validate_answer(store, answer)
    with pytest.raises(Blocked): resolve_field(store, {**field, 'history_key': 'end'})
    store.withdraw_history_entry(row['id'])
    with pytest.raises(Blocked): validate_answer(store, answer)
    assert len(store.history_entries(confirmed_only=False)) == 1


@pytest.mark.parametrize('values', [dict(organization='Acme', title='Engineer', current=True, end='2026-01'),
    dict(organization='Acme', title='Engineer', start='2026-02', end='2026-01'),
    dict(organization='Acme', title='Engineer', start='2026'),
    dict(organization='Acme', title='Engineer', current='Yes')])
def test_structured_history_rejects_ambiguous_or_contradictory_entries(store, values):
    with pytest.raises(ValueError): store.put_history_entry('employment', values, source='Applicant', confirmed=True)
    assert not store.history_entries(confirmed_only=False)


def test_history_revision_changes_native_step_sources(store):
    before = source_hash(store)
    row = store.put_history_entry('education', {'institution': 'University'}, source='Applicant', confirmed=True)
    assert source_hash(store) != before
    store.withdraw_history_entry(row['id'])
    assert source_hash(store) == before


def test_unknown_draft_save_can_only_be_reconciled_to_same_values_owner_and_job(store, wd_job):
    j = journal(store, wd_job); store.update_settings({'remote_drafts': True})
    grant = WriteGrant(store, wd_job, 'My Information', f'https://{NVIDIA}/fixture/save', 'POST', b'approved-fixture')
    observed = DraftObservation('remote-fixture', 'revision-2', j.session.owner_hash, wd_job['id'], digest('approved'))
    operation = CorrelatedStep(j, grant, observed.values_hash, lambda data: observed)
    assert operation.consume(grant.url, 'POST', grant.payload)
    assert j.current()['state'] == 'unknown'
    grant.uncertain()
    with pytest.raises(Blocked): j.remember(observed, digest('another-read'))
    with pytest.raises(Blocked): j.acknowledge(DraftObservation('other-draft', 'revision-2', j.session.owner_hash,
        wd_job['id'], observed.values_hash), digest('wrong-read'), reconciled=True)
    j.acknowledge(observed, digest('matching-read'), reconciled=True)
    assert j.current()['state'] == 'acknowledged'
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0] == 'acknowledged'
    assert store.db.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['request', 'payload', 'method', 'http', 'values', 'owner', 'attachment'])
def test_step_ack_needs_the_exact_transmitted_request_and_approved_state(store, wd_job, change):
    j = journal(store, wd_job); store.update_settings({'remote_drafts': True})
    grant = WriteGrant(store, wd_job, 'My Experience', f'https://{NVIDIA}/fixture/save', 'POST', b'approved-fixture')
    observation = DraftObservation('remote-fixture', 'revision-2', j.session.owner_hash, wd_job['id'], digest('approved'), ('resume-hash',))
    bad = {**observation.__dict__}
    if change == 'values': bad['values_hash'] = digest('wrong')
    if change == 'owner': bad['owner_hash'] = digest('other')
    if change == 'attachment': bad['attachment_hashes'] = ()
    operation = CorrelatedStep(j, grant, observation.values_hash, lambda data: DraftObservation(**bad), ('resume-hash',))
    assert operation.consume(grant.url, grant.method, grant.payload)
    request = SimpleNamespace(url=grant.url, method=grant.method, post_data_buffer=grant.payload)
    if change == 'request': request.url += '/other'
    if change == 'payload': request.post_data_buffer = b'other'
    if change == 'method': request.method = 'PUT'
    response = SimpleNamespace(request=request, status=500 if change == 'http' else 200, json=lambda: {'fixture': True})
    assert not operation.observe_response(response)
    assert j.current()['state'] == 'unknown'
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0] in {'intent', 'unknown'}


def test_correlated_step_ack_and_private_profile_restore_boundary(store, wd_job, tmp_path):
    j = journal(store, wd_job); store.update_settings({'remote_drafts': True})
    grant = WriteGrant(store, wd_job, 'My Information', f'https://{NVIDIA}/fixture/save', 'POST', b'approved-fixture')
    observation = DraftObservation('remote-fixture', 'revision-2', j.session.owner_hash, wd_job['id'], digest('approved'))
    operation = CorrelatedStep(j, grant, observation.values_hash, lambda data: observation)
    assert operation.consume(grant.url, grant.method, grant.payload)
    response = SimpleNamespace(request=SimpleNamespace(url=grant.url, method=grant.method, post_data_buffer=grant.payload),
        status=200, json=lambda: {'fixture': 'acknowledged'})
    assert operation.observe_response(response)
    assert not operation.observe_response(response)
    assert j.current()['native_revision'] == 'revision-2'
    from hireme.backup import create_backup, restore_backup
    archive = tmp_path/'history.zip'; create_backup(store, archive)
    restored_root = tmp_path/'restored'
    restore_backup(archive, restored_root)
    from hireme.store import Store
    with_store = Store(restored_root)
    try:
        assert with_store.db.execute('SELECT remote_id FROM workday_drafts').fetchone()[0] == 'remote-fixture'
        assert not profile_binding(with_store, NVIDIA)
        assert all(not item['enabled'] for item in status(with_store)['tenants'])
        assert not with_store.settings()['live_enabled']
    finally: with_store.close()


def test_unknown_transport_cannot_be_enabled_by_boolean_or_database_evidence(store, wd_job, monkeypatch):
    from hireme.workday_profiles import REVIEWED_TRANSPORTS
    monkeypatch.setitem(REVIEWED_TRANSPORTS,NVIDIA,None)
    browser = SimpleNamespace(store=store)
    controller = WorkdayController(browser, wd_job)
    controller.transport_verified = True
    store.db.execute('INSERT INTO adapter_verifications VALUES(?,?,?,?,?)',
        (NVIDIA, digest('fake-profile'), VERSION, 'fake-app', '2026-10-20'))
    with pytest.raises(Blocked, match='workday_transport_unverified'): controller.apply()
    with pytest.raises(Blocked, match='workday_transport_unverified'): controller.commission()
    with pytest.raises(ValueError): verify_capability(store, NVIDIA, digest('fake-profile'), 'fake-app')
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_pause_then_resume_cannot_revive_a_workday_grant(store, wd_job):
    store.update_settings({'remote_drafts': True})
    grant = WriteGrant(store, wd_job, 'My Information', f'https://{NVIDIA}/fixture/save', 'POST', b'approved-fixture')
    store.update_settings({'live_enabled': False}); store.update_settings({'live_enabled': True})
    with pytest.raises(Blocked, match='paused'): grant.consume(grant.url, grant.method, grant.payload)
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()



def test_education_expected_end_is_distinct_confirmed_history_and_never_infers_start(store):
    from hireme.workday_history import resolve_field, validate_answer
    row=store.put_history_entry('education', {'institution':'University of California, Berkeley',
        'current':True,'expected_end':'2028-05'},source='Applicant confirmed expected graduation',confirmed=True)
    field={'section':'education','history_entry_id':row['id'],'history_key':'expected_end',
        'label':'To (Actual or Expected) year','type':'number','options':[],'maxlength':-1}
    answer=resolve_field(store,field)
    assert answer['value']=='2028'
    validate_answer(store,answer)
    with pytest.raises(Blocked,match='history_confirmation_required'):
        resolve_field(store,{**field,'history_key':'start','label':'From year'})
    with pytest.raises(Blocked,match='history_confirmation_required'):
        resolve_field(store,{**field,'history_key':'end'})
    store.put_history_entry('education',{**row['values'],'expected_end':'2028-06'},
        source='Applicant changed expected graduation',confirmed=False,entry_id=row['id'])
    with pytest.raises(Blocked):validate_answer(store,answer)


@pytest.mark.parametrize('kind,values', [
    ('education',{'institution':'University','current':True,'end':'2028-05'}),
    ('education',{'institution':'University','end':'2028-05','expected_end':'2028-05'}),
    ('education',{'institution':'University','expected_end':'May 2028'}),
    ('education',{'institution':'University','start':'2028-06','expected_end':'2028-05'}),
    ('employment',{'organization':'Acme','title':'Engineer','expected_end':'2028-05'}),
])
def test_history_rejects_ambiguous_expected_education_dates(store,kind,values):
    with pytest.raises(ValueError):
        store.put_history_entry(kind,values,source='Applicant',confirmed=True)



@pytest.mark.parametrize('state,expected',[('intent','reconciliation_required'),('unknown','reconciliation_required'),('acknowledged','draft_adoption_required')])
def test_workday_status_exposes_persisted_holds_without_identity_or_recovery(store,wd_job,state,expected):
    store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('private-row-id',wd_job['id'],NVIDIA,'My Information:'+digest('creation'),'request-hash','sources',state,'created','updated','PRIVATE_NATIVE_ACK'))
    before=store.db.total_changes
    result=next(row for row in status(store)['tenants'] if row['host']==NVIDIA)
    assert result['commissioning_state']==expected and not result['enabled']
    assert result['hold']['reason']=='remote_draft_uncertain'
    assert 'Do not create another draft' in result['hold']['action']
    assert 'PRIVATE_NATIVE_ACK' not in json.dumps(result) and 'private-row-id' not in json.dumps(result)
    assert store.db.total_changes==before
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]==state


def test_workday_status_reads_old_schema_without_initializing_new_tables(tmp_path):
    import sqlite3
    connection=sqlite3.connect(':memory:')
    view=SimpleNamespace(db=connection,root=tmp_path/'missing-private-root',facts=lambda:{},settings=lambda:{'remote_drafts':True})
    result=status(view)
    assert all(row['pending_draft_count']==0 and row['step_states']=={} for row in result['tenants'])
    assert result['tenants'][0]['commissioning_state']=='sign_in_required'
    assert all(row['commissioning_state']=='transport_required' for row in result['tenants'][1:])
    assert not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
    assert not view.root.exists()
    connection.close()
