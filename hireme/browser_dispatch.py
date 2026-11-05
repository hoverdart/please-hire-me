"""Select native transports where operational, otherwise use the application UI."""
from __future__ import annotations

from .util import Blocked

NATIVE={'jobs.ashbyhq.com','jobs.lever.co','jobs.eu.lever.co','boards.greenhouse.io',
        'job-boards.greenhouse.io','boards.eu.greenhouse.io','job-boards.eu.greenhouse.io'}


def use_adaptive(store,job):
    if not store.settings()['adaptive_browser']:return False
    connection=job.get('connection_id')
    if connection:
        from .platform_connections import configuration,flows
        config=configuration(store,connection)
        if not config['enabled'] or not config['native_apply_enabled']:return False
        return not all(flows(store,connection,capability) for capability in ('inspection','preparation','submission','verification'))
    if job['host'] in NATIVE:return False
    if job['host'].endswith('.myworkdayjobs.com'):
        from .workday import VERSION
        from .workday_profiles import operational_evidence
        if operational_evidence(store,job['host'],VERSION):return False
        # A transport change cannot authorize abandoning an existing native
        # draft. Keep that identity on its original readback/reconciliation lane.
        if (store.db.execute('SELECT 1 FROM workday_drafts WHERE job_id=?',(job['id'],)).fetchone()
                or store.db.execute('SELECT 1 FROM remote_draft_steps WHERE job_id=?',(job['id'],)).fetchone()):
            return False
    return True


def can_fallback(store,job,browser,error):
    if not store.settings()['adaptive_browser'] or browser.attempted:return False
    if error.reason not in {'unsupported_form','unsupported_widget','unsupported_submit','multi_step_requires_adapter','unlabeled_required_field'}:return False
    if store.db.execute('SELECT 1 FROM draft_operations WHERE job_id=?',(job['id'],)).fetchone():return False
    if store.db.execute('SELECT 1 FROM remote_draft_steps WHERE job_id=?',(job['id'],)).fetchone():return False
    row=store.db.execute('SELECT state FROM applications WHERE job_id=?',(job['id'],)).fetchone()
    return not row or row['state']=='prepared'
