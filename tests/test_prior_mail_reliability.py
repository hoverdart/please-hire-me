"""Synthetic Gmail outages must not turn into retry storms or applications."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hireme import prior_applications as prior
from hireme.util import Blocked, digest
from hireme.worker import cycle
from tests.test_prior_applications import Mailbox, ReachedBrowser, optiver, saved_gmail_connection


def api_error(status, reason=None):
    error=RuntimeError('PRIVATE_PROVIDER_MESSAGE')
    error.resp=SimpleNamespace(status=status)
    error.content=json.dumps({'error':{'message':'PRIVATE_PROVIDER_MESSAGE','errors':[
        {'reason':reason,'message':'PRIVATE_MAIL_CONTENT','location':'PRIVATE_URL'}]}}).encode()
    return error


@pytest.mark.parametrize('status,code,expected',[
    (401,'authError','access_denied'),(403,'insufficientPermissions','access_denied'),
    (403,'accessNotConfigured','access_denied'),(403,None,'access_denied'),
    (403,'userRateLimitExceeded','rate_limited'),(403,'rateLimitExceeded','rate_limited'),
    (403,'dailyLimitExceeded','rate_limited'),(429,None,'rate_limited'),
    (503,None,'unavailable'),(400,'PRIVATE_UNKNOWN_CODE','unavailable'),
])
def test_safe_error_classification(status,code,expected):
    diagnostic,error=prior._scan_failure(api_error(status,code))
    assert error.reason=='prior_application_mail_'+expected
    assert diagnostic['status']==status
    assert 'PRIVATE' not in json.dumps(diagnostic)+error.detail


@pytest.mark.parametrize('content',[b'{',b'[]',b'{"error": null}',b'{"error":{"errors":{}}}',b'x'*65537])
def test_malformed_error_body_stays_denied(content):
    error=api_error(403);error.content=content
    diagnostic,blocked=prior._scan_failure(error)
    assert diagnostic=={'category':'http','status':403}
    assert blocked.reason=='prior_application_mail_access_denied'


@pytest.mark.parametrize('status,code,reason',[(403,'insufficientPermissions','access_denied'),(429,None,'rate_limited')])
def test_batch_stops_repeated_service_failures_and_recovers_next_cycle(store,optiver,monkeypatch,status,code,reason):
    store.update_settings({'gmail_prior_applications':True})
    other={**optiver,'id':digest('second-company'),'company':'Another Company',
           'url':'https://job-boards.greenhouse.io/another/jobs/123'}
    store.upsert_job(other)
    calls=[]
    def fail(_):
        calls.append(1)
        raise api_error(status,code)
    monkeypatch.setattr(prior,'GmailClient',fail)
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id'],other['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==0 and result['confirmed']==0
    assert result['reasons']=={'prior_application_mail_'+reason:2}
    assert len(calls)==1
    assert store.prior_mail_service_failure is None
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert all(row[0] is None for row in store.db.execute('SELECT completed FROM prior_application_scans'))
    details=[row[0] for row in store.db.execute("SELECT detail FROM events WHERE kind='prior_mail_scan_error'")]
    assert len(details)==1 and 'PRIVATE' not in details[0]
    service=Mailbox(optiver);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    # Manual recovery on the same Store must work without starting a new batch.
    prior.preflight(store,optiver)
    prior.require_recent_check(store,optiver)
    result=cycle(store,Path('.'),discover=False,job_ids=[optiver['id']],browser_factory=ReachedBrowser)
    assert result['attempts']==1
    prior.require_recent_check(store,optiver)


def test_existing_duplicate_precedes_cached_service_outage(store,optiver):
    from tests.test_prior_applications import mail
    prior.ingest(store,mail(optiver))
    store.prior_mail_checked=set()
    store.prior_mail_service_failure=('prior_application_mail_access_denied','Denied')
    with pytest.raises(Blocked,match='prior_application_email'):prior.preflight(store,optiver)


def test_standalone_failure_does_not_cache_after_an_earlier_batch(store,optiver,monkeypatch):
    store.update_settings({'gmail_prior_applications':True})
    store.prior_mail_checked=None
    def fail(_):raise api_error(403,'insufficientPermissions')
    monkeypatch.setattr(prior,'GmailClient',fail)
    with pytest.raises(Blocked,match='prior_application_mail_access_denied'):prior.preflight(store,optiver)
    assert getattr(store,'prior_mail_service_failure',None) is None
    service=Mailbox(optiver);service.pages=[[]]
    monkeypatch.setattr(prior,'GmailClient',lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    prior.preflight(store,optiver)
    prior.require_recent_check(store,optiver)
