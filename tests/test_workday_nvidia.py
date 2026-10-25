"""Closed tests for the real NVIDIA driver; never native employer writes."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme.util import Blocked,digest
from hireme.workday_native import NativeOperation,native_url
from hireme.workday_nvidia import NativeBrowserTransport,definition_properties,native_date,native_history_budget
from hireme.workday_profiles import NVIDIA


@pytest.mark.parametrize('value,precision,expected',[('2024-08','YYYY','2024'),('2028-05','YYYY-MM','2028-05'),
    ('2028-05-19','YYYY-MM-DD','2028-05-19')])
def test_native_manual_dates_use_observed_precision_without_inventing_components(value,precision,expected):
    assert native_date(value,precision)==expected


@pytest.mark.parametrize('value,precision',[('2024-08-malicious','YYYY'),('2024-08-32','YYYY'),
    ('2024','YYYY-MM'),('May 2028','YYYY'),('2024-00','YYYY'),('2028-05','unknown')])
def test_native_dates_reject_malformed_source_even_when_prefix_would_match(value,precision):
    with pytest.raises(Blocked):native_date(value,precision)


def test_real_public_us_country_definitions_are_bound_to_known_roots():
    data=json.loads((Path(__file__).parent/'fixtures/workday-nvidia-us-definitions.json').read_text())
    properties,required=definition_properties(data['definitions']['name']['data'],'name')
    assert required=={'firstName','lastName'} and properties['firstName']['type']=='string'
    properties,required=definition_properties(data['definitions']['address']['data'],'address')
    assert not required and properties['countryRegion']['type']=='object'
    with pytest.raises(Blocked):definition_properties(data['definitions']['address']['data'],'name')


def test_native_final_ack_rejects_extra_unknown_or_error_fields_even_with_matching_id():
    from hireme.workday_native import finalized
    assert finalized({'id':'owned-native'},'owned-native',200)=='owned-native'
    with pytest.raises(Blocked,match='submission_unknown'):
        finalized({'id':'owned-native','errorCode':'unreviewed'},'owned-native',200)


def test_native_history_missing_date_and_incomplete_count_are_never_empty_history(store,job):
    with pytest.raises(Blocked,match='workday_history_review_required'):
        native_history_budget(store,job,[{'total':1,'data':[{'applicationId':'owned-prior'}]}])
    with pytest.raises(Blocked,match='workday_history_review_required'):
        native_history_budget(store,job,[{'total':4,'data':[]}])


def test_native_and_local_company_histories_cannot_each_pass_the_same_combined_limit(store,job):
    store.update_settings({'max_per_company':2})
    old={**job,'id':digest('older-same-company'),'url':job['url']+'-older'};store.upsert_job(old)
    store.db.execute("INSERT INTO applications(id,job_id,company_key,state,package,hash,created,attempted,updated) "
        "VALUES(?,?,?,'confirmed','{}','hash','2020-01-01','2020-01-01','2020-01-01')",
        ('older-app',old['id'],store.company(job['company'])))
    history=[{'total':1,'data':[{'applicationId':'native-other-app','dateApplied':'January 1, 2020'}]}]
    with pytest.raises(Blocked,match='workday_history_review_required'):native_history_budget(store,job,history)


def test_native_history_cooldown_uses_official_completed_date(store,job):
    store.update_settings({'company_cooldown_days':90})
    from datetime import datetime,timezone
    today=datetime.now(timezone.utc).strftime('%B %d, %Y')
    with pytest.raises(Blocked,match='company_cooldown'):
        native_history_budget(store,job,[{'total':1,'data':[{'applicationId':'prior','dateApplied':today}]}])


def test_unlimited_native_history_allows_new_roles_after_multiple_recent_applications(store,job,package):
    from datetime import datetime,timezone
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed')
    other={**job,'id':digest('new-role-with-unlimited-company-history'),'url':job['url']+'-new-role'}
    store.upsert_job(other)
    today=datetime.now(timezone.utc).strftime('%B %d, %Y')
    history=[{'total':3,'data':[{'applicationId':'native-prior-'+str(i),'dateApplied':today} for i in range(3)]}]
    native_history_budget(store,other,history)
    assert store.application_record(aid)['state']=='confirmed'


def test_optional_native_company_count_limit_is_still_enforced(store,job):
    store.update_settings({'max_per_company':2,'company_cooldown_days':0})
    history=[{'total':3,'data':[{'applicationId':'prior-'+str(i),'dateApplied':'January 1, 2020'} for i in range(3)]}]
    with pytest.raises(Blocked,match='company_limit'):
        native_history_budget(store,job,history)


def test_unlimited_native_history_still_rejects_unreadable_completed_dates(store,job):
    with pytest.raises(Blocked,match='workday_history_review_required'):
        native_history_budget(store,job,[{'total':1,'data':[{'applicationId':'prior','dateApplied':'unknown'}]}])


def test_native_browser_transport_uses_fresh_browser_csrf_and_requires_exact_write_grant(store):
    from playwright.sync_api import sync_playwright
    origin='https://'+NVIDIA
    seen=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        context=browser.new_context(service_workers='block');page=context.new_page()
        def guard(route):
            request=route.request
            if request.url==origin+'/board':
                return route.fulfill(status=200,content_type='text/html',body="<html lang='en-US'><script>window.workday={tenant:'nvidia',siteId:'NVIDIAExternalCareerSite',token:'synthetic-fresh-native-csrf'}</script></html>")
            seen.append(request)
            assert request.headers['x-calypso-csrf-token']=='synthetic-fresh-native-csrf'
            return route.fulfill(status=200,content_type='application/json',body='{}')
        context.route('**/*',guard);page.goto(origin+'/board')
        wrapped=SimpleNamespace(store=store,page=page,workday_grant=None,workday_read_scope=None)
        transport=NativeBrowserTransport(SimpleNamespace(store=store,browser=wrapped))
        from hireme.workday_profiles import PROFILE_READ
        operation=NativeOperation(PROFILE_READ,'GET',b'',{},'read')
        assert transport.request(operation).status==200 and len(seen)==1
        with pytest.raises(Blocked,match='workday_unapproved_operation'):
            transport.request(NativeOperation(native_url('jobapplication','unreviewed-read'),'GET',b'',{},'read'))
        assert len(seen)==1
        operation=NativeOperation(native_url('jobapplication','fixture-write'),'POST',b'{}',{'content-type':'application/json'},'draft_write')
        with pytest.raises(Blocked,match='workday_unapproved_operation'):transport.request(operation)
        assert len(seen)==1
        wrapped.workday_grant=object()
        response=transport.request(operation)
        assert response.status==200 and seen[-1].post_data_buffer==b'{}'
        context.close();browser.close()


def owned_driver(store,job):
    from hireme.workday_nvidia import NvidiaNativeDriver
    from tests.test_workday_state import session_response
    from hireme.workday_profiles import bind_session
    selected={**job,'host':NVIDIA,'company':'Nvidia','url':'https://'+NVIDIA+'/en-US/NVIDIAExternalCareerSite/job/US-CA/Engineering_JR12345'}
    selected['id']=digest(selected['url']);store.upsert_job(selected)
    session=bind_session(store,session_response(store))
    browser=SimpleNamespace(store=store,page=SimpleNamespace(),workday_read_scope=None)
    controller=SimpleNamespace(store=store,browser=browser,job=selected,session=session,
        profile=SimpleNamespace(signature=digest('reviewed-fixture')))
    driver=NvidiaNativeDriver(controller);driver.info={'jobReqId':'JR12345'}
    calls=[]
    def read(url):
        assert driver.allow_read(url),'Read exceeded the source-reviewed owned application scope'
        calls.append(url)
        if 'draftsOnly=true' in url:
            return {'total':1,'data':[{'applicationId':'owned-draft','draft':True,'posting':{'isPosted':True}}]}
        if url.endswith('/owned-draft/config'):
            return {'application':{'applicationId':'owned-draft','draft':True,'dateApplied':None,
                'jobPostingAnchorId':'Engineering_JR12345','jobRequisitionId':'JR12345'}}
        raise AssertionError(url)
    driver.transport=SimpleNamespace(json=read)
    return driver,calls


def test_owned_existing_draft_adoption_is_read_only_and_binds_empty_native_resume_session(store,job):
    driver,calls=owned_driver(store,job)
    observation,proof=driver.find_draft()
    assert observation.remote_id=='owned-draft' and len(proof)==64
    assert driver.resuming and driver.session_id==''
    assert driver.read_scope.session_id=='' and len(calls)==3
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('change',['owner','job','completed','multiple'])
def test_owned_draft_adoption_rejects_changed_owner_requisition_completed_or_ambiguous_drafts(store,job,change):
    driver,calls=owned_driver(store,job);read=driver.transport.json
    def changed(url):
        value=read(url)
        if change=='multiple' and 'draftsOnly=true' in url:
            return {'total':2,'data':value['data']*2}
        if url.endswith('/config'):
            if change=='job':value['application']['jobRequisitionId']='JR_DIFFERENT'
            if change=='completed':value['application'].update(draft=False,dateApplied='2026-10-21')
        return value
    driver.transport.json=changed
    if change=='owner':store.put_facts({'email':'different@candidate.invalid'})
    with pytest.raises(Blocked):driver.find_draft()
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_resumed_definition_reads_require_explicit_owner_profile_bound_native_proof(store,job):
    from hireme.workday_native import NativeReadScope
    driver,_=owned_driver(store,job)
    with pytest.raises(Blocked):NativeReadScope(store,driver.controller.session,'owned-draft','')
    driver.find_draft()
    scope=driver.read_scope
    operation=NativeOperation(native_url('jobapplication','jobapplication/owned-draft/phonenumber'),
        'POST',b'{}',{'X-Cxs-Session-Id':'','form-definition':'true'},'definition_read')
    assert scope.allow(operation.url,operation.method,operation.body,operation.headers)
    assert not scope.allow(operation.url,'POST',b'{"phoneNumber":"unapproved"}',operation.headers)


def test_pending_record_reconciliation_rebuilds_same_approval_and_reads_without_resending(store,job):
    import hashlib
    from hireme.workday import WriteGrant,source_hash
    from hireme.workday_flow import StepWrite
    from hireme.workday_native import PAGES,encoded,record_operation
    from hireme.workday_nvidia import RecordPlan
    from hireme.workday_state import DraftJournal,DraftObservation,CorrelatedStep
    driver,calls=owned_driver(store,job)
    store.update_settings({'remote_drafts':True})
    session=driver.controller.session;selected=driver.job
    journal=DraftJournal(store,selected,session,driver.controller.profile.signature)
    journal.remember(DraftObservation('owned-draft','initial',session.owner_hash,selected['id'],digest({})),digest('initial'))
    body={'legalName':{'firstName':'Test','lastName':'Applicant','country':{'id':'country'}}}
    operation=record_operation('owned-draft','prior-session','name',body)
    key=digest([operation.url,operation.method,operation.body.hex()])
    grant=WriteGrant(store,selected,'My Information',operation.url,operation.method,operation.body,operation_key=key)
    step=CorrelatedStep(journal,grant,digest(body),lambda data:None)
    assert step.consume(operation.url,operation.method,operation.body);grant.uncertain()
    row=journal.current();pending=json.loads(row['evidence'])['pending'][grant.record_id]
    native_read=driver.transport.json
    driver.transport.json=lambda url:({'legalName':{**body['legalName'],'id':'native-name'},'preferredName':None}
        if url.endswith('/names') else native_read(url))
    page_ids=[key for key,value in PAGES.items() if value in {'My Information','Review'}]
    driver.read_flow_pages=lambda:SimpleNamespace(json=lambda:{'data':[{'id':key} for key in page_ids]})
    plan=RecordPlan('name',[],{},digest('schema'))
    snapshot={'fields':[]}
    driver.enter=lambda page:None
    driver.snapshot=lambda page:snapshot
    driver.controller.resolve_step=lambda value,provider:[]
    def writes(*args):
        write=StepWrite(operation,body);driver.saved=[(write,plan)];return [write]
    driver.writes=writes
    observed,proof=driver.read_pending(row,grant.record_id,pending)
    assert observed.values_hash==digest(body) and proof==observed.native_revision
    journal.acknowledge(observed,proof,reconciled=True,record_id=grant.record_id)
    assert journal.current()['state']=='acknowledged'
    assert len(calls)==2 and not driver.read_only
    assert store.db.execute('SELECT COUNT(*) FROM remote_draft_steps').fetchone()[0]==1
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_native_upload_abort_is_bounded_and_marks_sent_intent_unknown_without_replay(store,job,monkeypatch):
    from playwright.sync_api import sync_playwright
    from hireme.workday_flow import NativeFlow
    from hireme.workday_nvidia import NvidiaNativeDriver
    from hireme.workday_state import DraftJournal,DraftObservation
    driver,_=owned_driver(store,job)
    controller=driver.controller;store.update_settings({'remote_drafts':True})
    journal=DraftJournal(store,driver.job,controller.session,controller.profile.signature)
    journal.remember(DraftObservation('owned-draft','initial',controller.session.owner_hash,driver.job['id'],digest({})),digest('initial'))
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    monkeypatch.setattr('hireme.workday_nvidia.NATIVE_TIMEOUT_MS',25)
    sent=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True);context=browser.new_context(service_workers='block');page=context.new_page()
        controller.browser.page=page
        def route(request):
            if request.request.url==ORIGIN+'/board':
                return request.fulfill(content_type='text/html',body="<html><script>window.workday={tenant:'nvidia',siteId:'NVIDIAExternalCareerSite',token:'fixture'}</script></html>")
            accepted=controller.browser.workday_grant.consume_request(request.request)
            assert accepted
            sent.append(request.request.url)
            # A closed intercepted server never responds; the fetch timer must
            # abort without an external network connection.
        ORIGIN='https://'+NVIDIA
        context.route('**/*',route);page.goto(ORIGIN+'/board')
        driver.transport=NativeBrowserTransport(controller)
        flow=NativeFlow(controller,driver)
        with pytest.raises(Blocked,match='upload_verification_failed'):
            flow._upload({'fields':[]},[document],None)
        assert sent==[native_url('common','scanfile')]
        assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='unknown'
        assert not store.db.execute('SELECT 1 FROM applications').fetchone()
        context.close();browser.close()


@pytest.mark.parametrize('resource,ack,saved',[
    ('phonenumber',{'id':'other-native-phone'},{'id':'approved-native-phone','phoneNumber':'5551234567'}),
    ('name',{'legalName':{'id':'other-native-name'}},{'legalName':{'id':'approved-native-name'}}),
    ('uspersonalinfo',{'id':'unproved-package-record'},{'hispanicOrLatino':False})])
def test_native_record_ack_cannot_borrow_matching_values_from_an_unrelated_record(resource,ack,saved):
    from hireme.workday_nvidia import NvidiaNativeDriver,RecordPlan
    with pytest.raises(Blocked,match='remote_draft_uncertain'):
        NvidiaNativeDriver._correlate_saved_ack(RecordPlan(resource,[],{},digest('schema')),ack,saved)
    assert NvidiaNativeDriver._correlate_saved_ack(RecordPlan(resource,[],{},digest('schema')),{},saved)
