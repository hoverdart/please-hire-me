"""Durable native draft projection and correlated acknowledgement bookkeeping.

Observations are an internal normalized type, not an assumed vendor response.
Only a reviewed transport decoder may produce them from native traffic.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import uuid

from .util import Blocked, digest, now


def receipt_chain(store, application_id, signature, owner, binding, receipt_hash):
    """Derive receipt authority from the saved package and acknowledged native chain."""
    app=store.db.execute('SELECT * FROM applications WHERE id=?',(application_id,)).fetchone()
    if not app or app['state']!='confirmed':raise ValueError('A confirmed native application is required')
    try:package=json.loads(app['package'])
    except (TypeError,ValueError):raise ValueError('The saved native package is invalid') from None
    job=store.db.execute('SELECT host,url FROM jobs WHERE id=?',(app['job_id'],)).fetchone()
    draft=store.db.execute('SELECT * FROM workday_drafts WHERE job_id=?',(app['job_id'],)).fetchone()
    if (not isinstance(package,dict) or digest(package)!=app['hash'] or not job or not draft
            or package.get('job_id')!=app['job_id'] or package.get('url')!=job['url']
            or draft['state']!='submitted' or draft['tenant']!=job['host']
            or draft['owner_hash']!=owner or draft['profile_binding']!=binding
            or draft['flow_signature']!=signature):
        raise ValueError('The submitted native draft and approved package must match')
    try:
        if json.loads(draft['evidence']).get('pending'):raise ValueError()
    except (TypeError,ValueError,AttributeError):raise ValueError('The native draft still has pending evidence') from None
    rows=list(store.db.execute('SELECT * FROM remote_draft_steps WHERE job_id=? ORDER BY created,id',(app['job_id'],)))
    if len(rows)<3 or any(row['tenant']!=job['host'] or row['state']!='acknowledged'
            or not row['acknowledgement'] or not re.fullmatch(r'[a-f0-9]{64}',row['request_hash'])
            or not re.fullmatch(r'[a-f0-9]{64}',row['sources_hash']) for row in rows):
        raise ValueError('Every native operation must have a durable acknowledgement')
    final=[row for row in rows if row['step']=='Review']
    if len(final)!=1 or final[0]['acknowledgement']!=receipt_hash:
        raise ValueError('The final native acknowledgement does not match the receipt')
    # The exact IDs identify the durable intents; their content hash includes
    # original source/request hashes and acknowledgement evidence, never answers.
    chain=[{key:row[key] for key in ('id','job_id','tenant','step','request_hash','sources_hash','state','acknowledgement')}
           for row in rows]
    projection={key:draft[key] for key in ('job_id','tenant','site','owner_hash','profile_binding','remote_id',
                                          'native_revision','flow_signature','state','evidence')}
    return {'chain_version':1,'package_hash':app['hash'],'draft_hash':digest(projection),
            'step_chain_hash':digest(chain),'step_record_ids':[row['id'] for row in rows]}


def check_receipt_chain(store,application_id,evidence):
    expected=receipt_chain(store,application_id,evidence.get('flow_signature'),evidence.get('owner_hash'),
        evidence.get('profile_binding'),evidence.get('receipt_hash'))
    if any(evidence.get(key)!=value for key,value in expected.items()):
        raise ValueError('The receipt does not bind the acknowledged native operation chain')
    return expected


@dataclass(frozen=True)
class DraftObservation:
    remote_id: str
    native_revision: str
    owner_hash: str
    job_id: str
    values_hash: str
    attachment_hashes: tuple = ()


class DraftJournal:
    def __init__(self, store, job, session, signature):
        if not session.current(store) or session.host != job['host']:
            raise Blocked('workday_session_unverified')
        if not isinstance(signature, str) or not re.fullmatch(r'[a-f0-9]{64}', signature):
            raise Blocked('workday_transport_unverified')
        self.store, self.job, self.session, self.signature = store, job, session, signature

    def current(self):
        if not self.session.current(self.store):
            raise Blocked('workday_session_unverified')
        row = self.store.db.execute('SELECT * FROM workday_drafts WHERE job_id=?', (self.job['id'],)).fetchone()
        if row and (row['tenant'] != self.job['host'] or row['owner_hash'] != self.session.owner_hash
                    or row['profile_binding'] != self.session.profile_binding or row['flow_signature'] != self.signature):
            raise Blocked('remote_draft_uncertain', 'The saved draft belongs to another profile or adapter version')
        return dict(row) if row else None

    def remember(self, observation, evidence_hash):
        if (not isinstance(observation, DraftObservation) or observation.owner_hash != self.session.owner_hash
                or observation.job_id != self.job['id'] or not observation.remote_id or not observation.native_revision
                or not re.fullmatch(r'[a-f0-9]{64}', str(evidence_hash))):
            raise Blocked('remote_draft_uncertain', 'The official draft identity is unverified')
        from .portals import WORKDAY
        site = next(row[3] for row in WORKDAY if row[4] == self.job['host'])
        with self.store.transaction():
            previous = self.current()
            if previous and previous['remote_id'] != observation.remote_id:
                raise Blocked('remote_draft_uncertain', 'Another draft identity cannot replace this application')
            if previous and previous['state'] == 'unknown':
                raise Blocked('remote_draft_uncertain', 'Reconcile the unacknowledged save first')
            self.store.db.execute('INSERT OR REPLACE INTO workday_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (self.job['id'], self.job['host'], site, self.session.owner_hash, self.session.profile_binding,
                 observation.remote_id, observation.native_revision, self.signature, 'acknowledged',
                 json.dumps({'read_hash': evidence_hash}), now()))

    def pending(self, grant, values_hash, attachment_hashes=(), *, batch_id=None):
        row = self.current()
        if not row or not grant.used or not grant.record_id:
            raise Blocked('remote_draft_uncertain')
        batch_id = batch_id or grant.record_id
        old = json.loads(row['evidence'])
        if row['state'] != 'acknowledged' and (row['state'] != 'unknown' or old.get('batch_id') != batch_id):
            raise Blocked('remote_draft_uncertain')
        pending = old.get('pending', {}) if row['state'] == 'unknown' else {}
        pending[grant.record_id] = {'values_hash': values_hash,
                    'attachment_hashes': sorted(attachment_hashes), 'sources_hash': grant.sources,
                    'request_hash': hashlib.sha256(grant.payload).hexdigest()}
        evidence = {'batch_id': batch_id, 'pending': pending}
        self.store.db.execute("UPDATE workday_drafts SET state='unknown',evidence=?,updated=? WHERE job_id=?",
                              (json.dumps(evidence), now(), self.job['id']))

    def acknowledge(self, observation, evidence_hash, *, reconciled=False, record_id=None):
        row = self.current()
        if not row or row['state'] != 'unknown' or not isinstance(observation, DraftObservation):
            raise Blocked('remote_draft_uncertain')
        evidence = json.loads(row['evidence'])
        pending = evidence.get('pending', {})
        if record_id is None:
            if len(pending) != 1:raise Blocked('remote_draft_uncertain')
            record_id = next(iter(pending))
        expected = pending.get(record_id)
        if not expected:raise Blocked('remote_draft_uncertain')
        from .workday import source_hash
        if (observation.remote_id != row['remote_id'] or observation.owner_hash != self.session.owner_hash
                or observation.job_id != self.job['id'] or not observation.native_revision
                or observation.values_hash != expected.get('values_hash')
                or sorted(observation.attachment_hashes) != expected.get('attachment_hashes')
                or source_hash(self.store) != expected.get('sources_hash')
                or not re.fullmatch(r'[a-f0-9]{64}', str(evidence_hash))):
            raise Blocked('remote_draft_uncertain', 'The employer has not acknowledged the approved draft')
        with self.store.transaction():
            step = self.store.db.execute('SELECT * FROM remote_draft_steps WHERE id=? AND job_id=?',
                (record_id, self.job['id'])).fetchone()
            if not step or step['state'] not in {'intent', 'unknown'} or step['request_hash'] != expected['request_hash']:
                raise Blocked('remote_draft_uncertain')
            self.store.db.execute("UPDATE remote_draft_steps SET state='acknowledged',acknowledgement=?,updated=? WHERE id=?",
                (evidence_hash, now(), step['id']))
            pending.pop(record_id)
            evidence = {**evidence, 'pending': pending} if pending else {'read_hash': evidence_hash}
            self.store.db.execute("UPDATE workday_drafts SET state=?,native_revision=?,evidence=?,updated=? WHERE job_id=?",
                ('unknown' if pending else 'acknowledged', observation.native_revision, json.dumps(evidence), now(), self.job['id']))
            self.store.event('workday_draft_reconciled' if reconciled else 'workday_draft_acknowledged', self.job['id'],
                             {'record_id': step['id'], 'evidence_hash': evidence_hash})


class CorrelatedStep:
    """A profile-supplied decoder cannot attach an unrelated response to a save."""
    def __init__(self, journal, grant, values_hash, decoder, attachment_hashes=(), *, headers=None, batch_id=None):
        self.journal, self.grant, self.decoder = journal, grant, decoder
        self.values_hash, self.attachment_hashes = values_hash, tuple(attachment_hashes)
        self.observed = False
        self.headers, self.batch_id = headers, batch_id

    def consume_request(self, request):
        if self.headers is not None:
            actual = {key.lower(): value for key, value in request.headers.items()}
            if (any(actual.get(key.lower()) != value for key, value in self.headers.items())
                    or ('form-definition' not in {key.lower() for key in self.headers} and 'form-definition' in actual)):
                return False
        return self.consume(request.url, request.method, request.post_data_buffer or b'')

    def consume(self, url, method, payload):
        # Parent routing must install this object, not its unjournaled grant.
        with self.journal.store.transaction():
            if not self.grant.consume(url, method, payload):
                return False
            self.journal.pending(self.grant, self.values_hash, self.attachment_hashes,batch_id=self.batch_id)
        return True

    def observe_response(self, response):
        request = response.request
        if (not self.grant.used or self.observed or request.url != self.grant.url
                or request.method != self.grant.method or (request.post_data_buffer or b'') != self.grant.payload):
            return False
        if self.headers is not None:
            actual={key.lower():value for key,value in request.headers.items()}
            if (any(actual.get(key.lower())!=value for key,value in self.headers.items())
                    or ('form-definition' not in {key.lower() for key in self.headers} and 'form-definition' in actual)):
                return False
        try:
            if not 200 <= response.status < 300:
                raise ValueError()
            data = response.json()
            # Body decoding yields to Playwright's caller. Another exact
            # observer may finish first; never replay its durable projection.
            if self.observed:return False
            observation = self.decoder(data)
            self.journal.acknowledge(observation, digest(data),record_id=self.grant.record_id)
            return True
        except (Blocked, ValueError, TypeError, AttributeError, KeyError):
            if not self.observed:self.grant.uncertain()
            return False
        finally:
            self.observed = True


class GrantQueue:
    """One reviewed page can emit several independent native field saves."""
    def __init__(self, steps):
        self.steps=list(steps)
        if not self.steps:raise Blocked('workday_unapproved_operation')
        signatures=[(step.grant.url,step.grant.method,step.grant.payload) for step in self.steps]
        if len(signatures)!=len(set(signatures)):raise Blocked('workday_unapproved_operation')
        batch=uuid.uuid4().hex
        for step in self.steps:step.batch_id=batch

    def consume_request(self,request):
        matches=[step for step in self.steps if not step.grant.used and
            (request.url,request.method,request.post_data_buffer or b'')==(step.grant.url,step.grant.method,step.grant.payload)]
        return matches[0].consume_request(request) if len(matches)==1 else False

    def consume(self,url,method,payload):
        # Native authenticated requests must bind session/schema headers too.
        return False

    def observe_response(self,response):
        return any(step.observe_response(response) for step in self.steps)

    @property
    def complete(self):
        return all(step.observed and step.grant.used for step in self.steps) and self.steps[0].journal.current()['state']=='acknowledged'


def recover_drafts(store):
    """Project crashed native saves without replacing final submission outcomes."""
    store.db.execute("UPDATE remote_draft_steps SET state='unknown',updated=? WHERE state='intent'",(now(),))
    rows=list(store.db.execute("""SELECT DISTINCT j.id,j.payload,j.status,j.reason FROM jobs j
        WHERE NOT EXISTS(SELECT 1 FROM application_records a WHERE a.job_id=j.id AND a.state!='prepared') AND
          (EXISTS(SELECT 1 FROM remote_draft_steps d WHERE d.job_id=j.id AND d.state='unknown')
           OR EXISTS(SELECT 1 FROM workday_drafts d WHERE d.job_id=j.id AND d.state='unknown'))"""))
    from .job_holds import hold
    for row in rows:
        ashby=store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step LIKE 'ashby:%' AND state='unknown'",(row['id'],)).fetchone()
        reason='ashby_draft_uncertain' if ashby else 'remote_draft_uncertain'
        if row['status']=='blocked' and row['reason'].startswith(reason):
            continue
        job=json.loads(row['payload'])
        detail=('The Ashby guest draft may have reached the employer; automatic retry and owner-bound readback are unavailable'
                if ashby else 'Read back the existing employer draft before any further write')
        store.block(row['id'],reason,detail)
        hold(store,job,reason,detail,'recovery')
