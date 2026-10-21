"""Deterministic native multi-step lifecycle, independent of vendor DOM bindings.

The driver must be a source-reviewed implementation of the authenticated native
form definition/field bindings. Installing this runner does not install a driver
or promote fixture evidence into production capability.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from urllib.parse import urlsplit

from .approval_boundary import ApprovalBoundary
from .util import Blocked, digest, now
from .workday import WriteGrant
from .workday_native import (SOURCE_SIGNATURE, NativeOperation, draft_created, encoded,
    finalize_operation, finalized, native_url, pages, saved_record, questionnaire_saved)
from .workday_state import CorrelatedStep, DraftJournal, DraftObservation, GrantQueue


@dataclass(frozen=True)
class StepWrite:
    operation: NativeOperation
    expected: dict
    kind: str = 'record'
    record_id: str | None = None
    attachment_hashes: tuple = ()
    ack_validator: object = None

    def observation(self, data, journal):
        if self.ack_validator is not None:
            self.ack_validator(data)
            # Native aggregate responses can omit fields or contain client-
            # patched values. Only the reviewed driver can join this recognized
            # ACK to a fresh owned record read; the response alone cannot save it.
            raise Blocked('native_readback_required')
        if self.kind == 'questionnaire':
            questionnaire_saved(data, self.expected)
        elif self.kind == 'record':
            saved_record(data, self.expected, self.record_id)
        else:
            raise Blocked('workday_unapproved_operation')
        return DraftObservation(journal.current()['remote_id'], digest(data), journal.session.owner_hash,
            journal.job['id'], digest(self.expected), self.attachment_hashes)


def _matches_response(response, operation):
    request = response.request
    actual={key.lower():value for key,value in request.headers.items()}
    return ((request.url, request.method, request.post_data_buffer or b'') == (operation.url, operation.method, operation.body)
        and all(actual.get(key.lower())==value for key,value in operation.headers.items())
        and 'form-definition' not in actual)


class NativeFlow:
    """All transport happens through the browser's exact pending grants."""
    def __init__(self, controller, driver):
        self.controller, self.driver = controller, driver
        self.browser, self.store, self.job = controller.browser, controller.store, controller.job
        self.session = controller.session
        self.journal = DraftJournal(self.store, self.job, self.session, controller.profile.signature)

    def posting_identifier(self):
        from .workday_native import identifier
        expected=identifier(urlsplit(self.job['url']).path.rstrip('/').split('/')[-1])
        if self.driver.posting_identifier()!=expected:
            raise Blocked('posting_changed_review','The native application identifies a different requisition')
        return expected

    def _draft(self):
        previous = self.journal.current()
        if previous:
            if previous['state'] != 'acknowledged':
                raise Blocked('remote_draft_uncertain', 'Read back the existing employer draft before continuing')
            observation, evidence_hash = self.driver.read_draft(previous)
            self.journal.remember(observation, evidence_hash)
            return previous['remote_id'], self.driver.session_id
        # Creating a remote draft is a write even when its body is empty.
        posting = self.posting_identifier()
        operation = NativeOperation(native_url('jobapplication', f'jobpostings/{posting}/jobapplications', old=True),
                                    'POST', b'{}', {}, 'draft_create')
        operation_key=digest(['create', operation.url])
        creation=self.store.db.execute('SELECT state FROM remote_draft_steps WHERE job_id=? AND step=? LIMIT 1',
            (self.job['id'],'My Information:'+operation_key)).fetchone()
        finder=getattr(self.driver,'find_draft',None)
        adopted=finder() if callable(finder) else None
        if adopted:
            observation,evidence_hash=adopted
            from .workday import source_hash
            previous_creation=self.store.db.execute('SELECT * FROM remote_draft_steps WHERE job_id=? AND step=?',
                (self.job['id'],'My Information:'+operation_key)).fetchall()
            if len(previous_creation)>1 or any(row['sources_hash']!=source_hash(self.store) for row in previous_creation):
                raise Blocked('remote_draft_uncertain','The original creation approval needs manual review')
            with self.store.transaction():
                self.journal.remember(observation,evidence_hash)
                for row in previous_creation:
                    self.store.db.execute("UPDATE remote_draft_steps SET state='acknowledged',acknowledgement=?,updated=? WHERE id=?",
                        (evidence_hash,now(),row['id']))
                self.store.event('workday_draft_adopted',self.job['id'],{'evidence_hash':evidence_hash})
            return observation.remote_id,self.driver.session_id
        if creation:
            raise Blocked('remote_draft_uncertain',
                'Read and adopt the recorded native creation before continuing; another creation is not authorized')
        grant = WriteGrant(self.store, self.job, 'My Information', operation.url, operation.method, operation.body,
                           operation_key=operation_key)
        grant.request_headers=operation.headers
        grant.boundary=ApprovalBoundary(self.store,self.job,
            profile_binding=lambda:self.session.profile_binding if self.session.current(self.store) else None)
        self.browser.workday_grant = grant
        try:
            response = self.driver.create_draft(operation)
            if not grant.used or response.status != 200 or not _matches_response(response, operation):
                raise Blocked('remote_draft_uncertain')
            data = response.json()
            remote, session_id = draft_created(data)
            # A crash cannot leave an acknowledged creation without its owned
            # draft identity; otherwise a later run could create another draft.
            with self.store.transaction():
                self.journal.remember(DraftObservation(remote, digest(data), self.session.owner_hash,
                    self.job['id'], digest({})), digest(data))
                grant.acknowledge(digest(data))
            return remote, session_id
        except Exception:
            grant.uncertain()
            raise
        finally:
            self.browser.workday_grant = None

    def _step(self, page, package):
        self.driver.enter(page)
        snapshotter=getattr(self.driver,'snapshot',None)
        snapshot=snapshotter(page) if callable(snapshotter) else self.controller.snapshot(page['section'])
        answers = self.controller.resolve_step(snapshot, self.driver.provider)
        documents = self.driver.documents(snapshot)
        package['answers'].extend(answers)
        for document in documents:
            package['documents'] = [old for old in package['documents'] if old['kind'] != document['kind']]+[document]
        package['steps'].append({'fields': snapshot['fields'], 'url': self.job['url'], 'native_page': page['id']})
        # Full approval includes earlier sections and the selected resume. The
        # live DOM check below covers only controls that exist on this page.
        boundary = ApprovalBoundary(self.store, self.job, package, require_live=False,
            profile_binding=lambda: self.session.profile_binding if self.session.current(self.store) else None)
        from .answers import validate_package
        validate_package(self.store, self.job, package)
        self._upload(snapshot,documents,package)
        writes = self.driver.writes(page, snapshot, answers, documents)
        resumed=getattr(self.driver,'resumed_writes',None)
        if callable(resumed) and isinstance(writes,list) and writes:
            original=writes
            writes=resumed(writes)
            if original and not writes:
                self.driver.verify(answers,documents,snapshot)
                self.driver.finish_no_write(page)
                return
        if isinstance(writes,list) and not writes and getattr(self.driver,'no_write_step',lambda value:False)(snapshot) is True:
            self.driver.verify(answers,documents,snapshot)
            self.driver.finish_no_write(page)
            return
        if not isinstance(writes, list) or not writes or any(not isinstance(write, StepWrite) for write in writes):
            raise Blocked('workday_unapproved_operation', 'This step has no reviewed native operation plan')
        operations = []
        for write in writes:
            operation = write.operation
            if (operation.purpose != 'draft_write' or operation.body != encoded(write.expected)
                    or self.driver.validate_bindings(write, snapshot, answers, documents) is not True):
                raise Blocked('workday_unapproved_operation', 'The native payload is not bound to the approved fields')
            boundary.check()
            section = snapshot['section']
            grant = WriteGrant(self.store, self.job, section, operation.url, operation.method, operation.body,
                operation_key=digest([operation.url, operation.method, operation.body.hex()]))
            grant.boundary = boundary
            decoder = lambda data, write=write: write.observation(data, self.journal)
            operations.append(CorrelatedStep(self.journal, grant, digest(write.expected), decoder,
                write.attachment_hashes, headers=operation.headers))
        queue = GrantQueue(operations)
        self.browser.workday_grant = queue
        try:
            boundary.check()
            self.driver.fill(snapshot,answers,[])
            verifier=getattr(self.driver,'verify',None)
            if callable(verifier):verifier(answers,documents,snapshot)
            else:self.browser._verify(answers,documents,snapshot['fields'])
            self.driver.perform_step(page, queue)
            deadline = time.monotonic()+45
            while not queue.complete and time.monotonic() < deadline:
                self.store.checkpoint()
                if any(step.observed and self.journal.current()['state']=='unknown' for step in queue.steps):
                    if all(step.observed for step in queue.steps):break
                self.driver.wait(100)
            if not queue.complete:
                for step in queue.steps:step.grant.uncertain()
                raise Blocked('remote_draft_uncertain', 'Every native operation must be acknowledged before advancing')
        finally:
            self.browser.workday_grant = None

    def _upload(self,snapshot,documents,package):
        from .workday_uploads import UploadGrant,UploadQueue
        if getattr(self.driver,'resuming',False):
            if not documents or self.driver.reuse_documents(documents):return
        for document in documents:
            scan=UploadGrant(self.journal,document,scan=True,package=package)
            upload=UploadGrant(self.journal,document,package=package)
            queue=UploadQueue([scan,upload])
            self.browser.workday_grant=queue
            try:
                self.driver.upload(snapshot,document,queue)
                deadline=time.monotonic()+45
                while not queue.complete and time.monotonic()<deadline:
                    self.store.checkpoint()
                    if any(grant.observed and not grant.acknowledged for grant in queue.grants):break
                    self.driver.wait(100)
                if not queue.complete:
                    for grant in queue.grants:grant.uncertain()
                    raise Blocked('upload_verification_failed','The native scan and attachment acknowledgement must both match the approved PDF')
                self.driver.bind_attachment(document,upload.attachment)
            except Exception:
                for grant in queue.grants:grant.uncertain()
                raise
            finally:self.browser.workday_grant=None

    def run(self, live=True):
        if not live:
            return self.prepare_local()
        posting=self.posting_identifier()
        flow_response=self.driver.read_flow_pages()
        expected=native_url('jobapplication',f'jobpostings/{posting}/applyflowpages?applyDetail=applyManually')
        if (flow_response.status!=200 or flow_response.request.url!=expected or flow_response.request.method!='GET'
                or flow_response.request.post_data_buffer not in (None,b'', '')):
            raise Blocked('posting_changed_review','The native flow definition does not match this requisition')
        flow = pages(flow_response.json())
        remote, session_id = self._draft()
        self.driver.bind_draft(remote, session_id, self.session)
        self.browser.workday_read_scope = self.driver.read_scope
        package = {'job_id': self.job['id'], 'url': self.job['url'], 'answers': [],
            'documents': self.driver.selected_documents(), 'steps': [], 'facts_hash': digest(self.store.facts())}
        self.driver.approved_package=package
        for page in flow:
            if page['section'] == 'Review':break
            self.store.checkpoint()
            self._step(page, package)
        self.driver.enter(flow[-1])
        if self.journal.current()['state'] != 'acknowledged' or self.driver.review_matches(package, self.journal.current()) is not True:
            raise Blocked('remote_draft_uncertain', 'Official Review does not match the approved accumulated application')
        aid = self.store.prepare(self.job, package)
        operation = finalize_operation(remote, session_id)
        boundary = ApprovalBoundary(self.store, self.job, package, application_id=aid,
            profile_binding=lambda: self.session.profile_binding if self.session.current(self.store) else None)
        from .answers import validate_package
        validate_package(self.store, self.job, package)
        boundary.check()
        self.store.begin_submit(aid)
        self.browser.aid, self.browser.attempted = aid, True
        grant = WriteGrant(self.store, self.job, 'Review', operation.url, operation.method, operation.body,
                           final=True, application_id=aid)
        grant.boundary = boundary
        grant.request_headers=operation.headers
        self.browser.workday_grant = grant
        try:
            response = self.driver.submit(operation)
            if not grant.used or not _matches_response(response, operation):raise Blocked('submission_unknown')
            data = response.json(); finalized(data, remote, response.status)
            if self.driver.completed_application(remote, package, data) is not True:raise Blocked('submission_unknown')
            receipt_hash = digest(data)
            with self.store.transaction():
                grant.acknowledge(receipt_hash)
                self.store.finish(aid, 'confirmed', 'Fresh native Workday acknowledgement and matching application receipt')
                self.store.db.execute("UPDATE workday_drafts SET state='submitted',updated=? WHERE job_id=?", (now(), self.job['id']))
                from .workday_state import receipt_chain
                chain=receipt_chain(self.store,aid,self.controller.profile.signature,self.session.owner_hash,
                    self.session.profile_binding,receipt_hash)
                self.store.event('workday_receipt', aid, {'flow_signature': self.controller.profile.signature,
                    'owner_hash': self.session.owner_hash, 'profile_binding': self.session.profile_binding,
                    'receipt_hash': receipt_hash, 'proof_scope': self.driver.proof_scope,**chain})
            return 'confirmed'
        except Exception:
            grant.uncertain()
            row = self.store.db.execute('SELECT state FROM applications WHERE id=?', (aid,)).fetchone()
            if row and row['state']=='submitting':self.store.finish(aid,'unknown','Native Workday final outcome requires verification')
            raise Blocked('submission_unknown') from None
        finally:
            self.browser.workday_grant = None

    def prepare_local(self):
        """Preparation never creates/saves/uploads into an employer application."""
        previous=self.journal.current()
        reader=getattr(self.driver,'read_only_package',None)
        if not previous or previous['state']!='acknowledged' or not callable(reader):
            raise Blocked('workday_preparation_requires_draft',
                'Native application definitions require an existing owned draft; preparation has not created or changed one')
        package=reader(previous)
        from .answers import validate_package
        validate_package(self.store,self.job,package)
        self.store.prepare(self.job,package)
        return 'prepared'


class NvidiaNativeProfile:
    """Reviewed native driver factory; fixture drivers never enter the registry."""
    signature = SOURCE_SIGNATURE

    def __init__(self, driver_factory):
        self.driver_factory = driver_factory
        self.signature=getattr(driver_factory,'protocol_signature',SOURCE_SIGNATURE)

    def _driver(self,controller):
        if not hasattr(controller,'_native_driver'):controller._native_driver=self.driver_factory(controller)
        return controller._native_driver

    def read_session(self,controller):
        return self._driver(controller).read_profile()

    def apply(self,controller,live=True):
        return NativeFlow(controller,self._driver(controller)).run(live)

    def reconcile(self,controller):
        flow=NativeFlow(controller,self._driver(controller))
        row=flow.journal.current()
        if not row:
            driver=flow.driver
            if not callable(getattr(driver,'find_draft',None)):raise Blocked('remote_draft_uncertain')
            driver.read_only=True
            try:adopted=driver.find_draft()
            finally:driver.read_only=False
            if not adopted:raise Blocked('remote_draft_uncertain','The employer has not exposed one owned draft; another creation is not authorized')
            observation,evidence_hash=adopted
            from .workday import source_hash
            posting=driver.posting_identifier()
            operation_url=native_url('jobapplication',f'jobpostings/{posting}/jobapplications',old=True)
            creation_step='My Information:'+digest(['create',operation_url])
            intents=list(controller.store.db.execute('SELECT * FROM remote_draft_steps WHERE job_id=? AND step=?',
                (controller.job['id'],creation_step)))
            if len(intents)!=1 or intents[0]['sources_hash']!=source_hash(controller.store):
                raise Blocked('remote_draft_uncertain','The original creation approval cannot be uniquely reconciled')
            with controller.store.transaction():
                flow.journal.remember(observation,evidence_hash)
                controller.store.db.execute("UPDATE remote_draft_steps SET state='acknowledged',acknowledgement=?,updated=? WHERE id=?",
                    (evidence_hash,now(),intents[0]['id']))
                controller.store.event('workday_draft_adopted',controller.job['id'],{'evidence_hash':evidence_hash})
            return {'job_id':controller.job['id'],'state':'acknowledged'}
        if row['state']!='unknown':raise Blocked('remote_draft_uncertain')
        pending=json.loads(row['evidence']).get('pending',{})
        for record_id,expected in list(pending.items()):
            observation,evidence_hash=flow.driver.read_pending(row,record_id,expected)
            flow.journal.acknowledge(observation,evidence_hash,reconciled=True,record_id=record_id)
        return {'job_id':controller.job['id'],'state':flow.journal.current()['state']}
