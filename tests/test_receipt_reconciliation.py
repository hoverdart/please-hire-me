import copy
import time
from types import SimpleNamespace

import pytest

from hireme import prior_applications as prior
from hireme.receipt_reconciliation import candidates
from hireme.util import Blocked
from tests.test_prior_applications import mail,Mailbox


@pytest.mark.parametrize('text,expected',[
    ('We have received your job application.',True),
    ('Your candidacy for Software Engineer has been received.',True),
    ('Hemos recibido tu solicitud.',True),
    ('Nous avons bien reçu votre candidature.',True),
    ('Wir haben Ihre Bewerbung erhalten.',True),
    ('We have not received your job application.',False),
    ('If we received your job application, we will reply.',False),
    ('Please complete your application.',False),
    ('No hemos recibido tu solicitud.',False),
    ('Your job application has not been received.',False),
    ('Your application for Software Engineer is under review. Should you have applied to other roles, we will consider those separately.',True),
    ('Requisition no 4421 – we have received your job application.',True),
    ('Ref no ENG we have received your job application.',True),
    ('No worries we have received your job application.',True),
    ("Nous n'avons pas encore reçu votre candidature.",False),
    ('Wir haben Ihre Bewerbung noch nicht erhalten.',False),
])
def test_labeled_synthetic_receipt_corpus(text,expected):
    assert prior._prior_evidence(text)==expected


def test_confirmed_alias_and_owner_forwarding_produce_only_review_evidence(store,job):
    alias='explicit-alias@candidate.invalid'
    m=mail(job,recipient=alias)
    assert not prior.ingest(store,m)
    store.update_settings({'gmail_confirmed_aliases':[alias]})
    assert prior.ingest(store,m)
    forwarded=mail(job,identity='forwarded',subject='Fwd: Receipt',body=(
        f'---------- Forwarded message ---------\nFrom: Careers <careers@acme.invalid>\nTo: {alias}\n'
        f'We have received your job application for the {job["title"]} position at Acme.'))
    forwarded['payload']['headers'][0]['value']='test@candidate.invalid'
    assert prior.ingest(store,forwarded)
    row=store.db.execute("SELECT state,sender_verified FROM prior_application_mail WHERE message_id='forwarded'").fetchone()
    assert tuple(row)==('pending',0)
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_receipt_correlation_is_read_only_and_cannot_confirm_spoofed_sender(store,job,package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    m=mail(job,age=0,body=f'We have received your job application. {job["url"]}')
    before=store.db.total_changes
    result=candidates(store,m)
    assert result and result[0]['application_id']==aid and not result[0]['auto_confirmed']
    assert result[0]['decision']=='review' and store.db.total_changes==before
    assert store.application_record(aid)['state']=='unknown'
    unrelated=copy.deepcopy(m);unrelated['payload']['headers'][1]['value']='foreign@candidate.invalid'
    assert candidates(store,unrelated)==[]
    late=copy.deepcopy(m);late['internalDate']=str(int((time.time()-10*86400)*1000))
    assert candidates(store,late)==[]


def test_backfill_does_not_prove_freshness_until_overlapping_catchup(store,job,monkeypatch):
    store.update_settings({'gmail_prior_applications':True})
    from hireme.util import atomic_json
    atomic_json(store.root/'integrations/gmail-token.json',{})
    atomic_json(store.root/'integrations/gmail-account.json',{'email':'test@candidate.invalid'})
    service=Mailbox(job);service.pages=[[]]
    factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service)
    prior.scan(store,company='Acme',client_factory=factory)
    with pytest.raises(Blocked):prior.require_recent_check(store,job)
    prior.scan(store,company='Acme',client_factory=factory)
    prior.require_recent_check(store,job)
    assert service.lists[-1]['q'].startswith('after:')
    monkeypatch.setattr(prior,'CLASSIFIER_VERSION',999)
    with pytest.raises(Blocked):prior.require_recent_check(store,job)
    prior.scan(store,company='Acme',client_factory=factory)
    assert not service.lists[-1]['q'].startswith('after:')


@pytest.mark.parametrize('page',[{'messages':[{}]}, {'messages':'invalid'}, {'messages':[],'nextPageToken':3}])
def test_malformed_pagination_cannot_authorize_application(store,job,page):
    store.update_settings({'gmail_prior_applications':True})
    service=Mailbox(job)
    from tests.test_prior_applications import Response
    service.list=lambda **kwargs:Response(page)
    with pytest.raises(Blocked):prior.scan(store,company='Acme',client_factory=lambda _:SimpleNamespace(email='test@candidate.invalid',service=service))
    assert store.db.execute('SELECT completed FROM prior_application_scans').fetchone()[0] is None
