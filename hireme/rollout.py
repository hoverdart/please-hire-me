"""Explicit stages and code pinning for restricted unattended applications."""
import hashlib
import json
from pathlib import Path
from .util import Blocked,atomic_json,now,digest


def code_fingerprint():
    root=Path(__file__).parent
    files=sorted([*root.glob('*.py'),*root.glob('static/*')])
    return digest([(str(p.relative_to(root)),hashlib.sha256(p.read_bytes()).hexdigest()) for p in files if p.is_file()])


def commission(store, stage):
    if stage not in {'dry_run','controlled','restricted'}:raise ValueError('Choose a rollout stage')
    settings=store.settings();scope=settings['allowed_application_ats']
    if not scope or set(scope)-{'lever','ashby'}:raise ValueError('Restrict applications to Lever and/or Ashby first')
    if store.missing_setup() or not settings['onboarding_complete']:raise ValueError('Complete applicant setup first')
    proofs={}
    if stage=='restricted':
        from .application_evidence import snapshot
        for row in store.db.execute("SELECT id FROM application_records WHERE state='confirmed' ORDER BY attempted DESC"):
            bundle=snapshot(store,row['id'])
            if bundle['ats'] in scope and bundle['verification_method']=='native_ats_acknowledgement':
                proofs.setdefault(bundle['ats'],bundle['attempt_id'])
        if set(proofs)!=set(scope):raise ValueError('A verified native submission is required for each enabled ATS')
        if settings['max_per_cycle']>2 or settings['max_per_day']>4 or settings['max_attempts_per_cycle']>2:
            raise ValueError('Restricted rollout allows at most two attempts per cycle and four per day')
        if not settings['stop_on_uncertain_outcome']:raise ValueError('Enable stop-on-uncertainty first')
    record={'stage':stage,'code_fingerprint':code_fingerprint(),'ats':scope,'proofs':proofs,'commissioned_at':now()}
    atomic_json(store.root/'config/rollout.json',record)
    store.update_settings({'rollout_stage':stage})
    store.event('rollout_commissioned',stage,record)
    return record


def check(store, *, live, job_ids):
    settings=store.settings();stage=settings['rollout_stage']
    if stage=='manual':return live
    path=store.root/'config/rollout.json'
    try:record=json.loads(path.read_text()) if not path.is_symlink() else {}
    except (OSError,ValueError):record={}
    if (record.get('stage')!=stage or record.get('code_fingerprint')!=code_fingerprint()
            or record.get('ats')!=settings['allowed_application_ats']):
        raise Blocked('rollout_stale','The code or application scope changed; revalidate and recommission this rollout')
    if stage=='dry_run':return False
    if live and stage=='controlled' and len(job_ids or ())!=1:
        raise Blocked('rollout_controlled','Select exactly one new posting for a controlled live run')
    if live and stage=='restricted':
        if (set(record.get('proofs',{}))!=set(settings['allowed_application_ats'])
                or settings['max_per_cycle']>2 or settings['max_per_day']>4
                or settings['max_attempts_per_cycle']>2 or not settings['stop_on_uncertain_outcome']):
            raise Blocked('rollout_limits','Restricted rollout limits or ATS evidence changed')
        for aid in record['proofs'].values():
            app=store.application_record(aid)
            if not app or app['state']!='confirmed':raise Blocked('rollout_evidence_changed')
        from .connections import status
        from .gmail import check_connection
        if not status(store,verify=True)['ready']:raise Blocked('provider_unavailable')
        check_connection(store)
        if store.missing_setup():raise Blocked('setup_incomplete')
    return live
