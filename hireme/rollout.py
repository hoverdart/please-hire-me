"""Explicit stages and code pinning for restricted unattended applications."""
import hashlib
import json
import time
from pathlib import Path
from .util import Blocked,atomic_json,now,digest

MAX_DAILY=10
MAX_CYCLE=3
MAX_ATTEMPTS=8


def check_connections(store):
    """Verify transport outside SQLite; consume only a fresh local proof inside."""
    settings=store.settings()
    if settings['rollout_stage']!='restricted':return
    from .gmail import status as gmail_status,check_connection
    identity=digest([settings,store.facts().get('email'),gmail_status(store),store.control_generation()])
    if store.db.in_transaction:
        proof=getattr(store,'rollout_connection_proof',None)
        if not proof or proof[1]!=identity or not 0<=time.monotonic()-proof[0]<=60:
            raise Blocked('rollout_connection_check_required','Verify connections before opening the submission transaction')
        return
    store.rollout_connection_proof=None
    from .connections import status
    if not status(store,verify=True)['ready']:raise Blocked('provider_unavailable')
    check_connection(store)
    # Health metadata can change during the successful check.
    identity=digest([store.settings(),store.facts().get('email'),gmail_status(store),store.control_generation()])
    store.rollout_connection_proof=(time.monotonic(),identity)


def code_fingerprint():
    root=Path(__file__).parent
    files=sorted([*root.glob('*.py'),*root.glob('static/*')])
    return digest([(str(p.relative_to(root)),hashlib.sha256(p.read_bytes()).hexdigest()) for p in files if p.is_file()])


def commission(store, stage):
    if stage not in {'dry_run','controlled','restricted'}:raise ValueError('Choose a rollout stage')
    settings=store.settings();scope=settings['allowed_application_ats']
    if not scope or set(scope)-{'lever','ashby','workday'}:raise ValueError('Restrict applications to Lever, Ashby and/or Workday first')
    if store.missing_setup() or not settings['onboarding_complete']:raise ValueError('Complete applicant setup first')
    proofs={}
    if stage=='restricted':
        from .application_evidence import snapshot
        for row in store.db.execute("SELECT id FROM application_records WHERE state='confirmed' ORDER BY attempted DESC"):
            bundle=snapshot(store,row['id'])
            required='verified_ui_application_readback' if bundle['ats']=='workday' else 'native_ats_acknowledgement'
            if bundle['ats'] in scope and bundle['verification_method']==required:
                proofs.setdefault(bundle['ats'],bundle['attempt_id'])
        if set(proofs)!=set(scope):raise ValueError('A verified native submission or owner-bound Workday application readback is required for each enabled ATS')
        if settings['max_per_cycle']>MAX_CYCLE or settings['max_per_day']>MAX_DAILY or settings['max_attempts_per_cycle']>MAX_ATTEMPTS:
            raise ValueError('Restricted rollout allows at most three submissions and eight attempts per cycle, and ten submissions per day')
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
                or settings['max_per_cycle']>MAX_CYCLE or settings['max_per_day']>MAX_DAILY
                or settings['max_attempts_per_cycle']>MAX_ATTEMPTS or not settings['stop_on_uncertain_outcome']):
            raise Blocked('rollout_limits','Restricted rollout limits or ATS evidence changed')
        from .application_evidence import snapshot
        for ats,aid in record['proofs'].items():
            app=store.application_record(aid)
            if not app or app['state']!='confirmed':raise Blocked('rollout_evidence_changed')
            bundle=snapshot(store,aid)
            method='verified_ui_application_readback' if ats=='workday' else 'native_ats_acknowledgement'
            if bundle['ats']!=ats or bundle['verification_method']!=method:raise Blocked('rollout_evidence_changed')
        check_connections(store)
        if store.missing_setup():raise Blocked('setup_incomplete')
    return live
