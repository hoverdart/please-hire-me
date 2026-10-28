"""Dependency-based holds. Submission uncertainty never authorizes another attempt."""
from __future__ import annotations
import json
import time
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from .field_context import MAPPING_VERSION, ADAPTER_VERSION, CAPTCHA_DETECTION_VERSION
from .presentation import NOT_MATCH_REASONS, WAIT_REASONS, REASON_GUIDANCE
from .util import digest, now, Blocked

FACT_REASONS = {'missing_answers','missing_fact','required_answer_missing','stale_answer','answer_too_long','numeric_answer_needed','phone_country_review','model_budget_exhausted','history_confirmation_required','history_changed'}
MAPPING_REASONS = {'mapping_review','option_mismatch','form_changed','field_verification_failed','unsupported_form','invalid_option_metadata','invalid_binding_source','invalid_date_answer','invalid_fields','invalid_single_line_answer','numeric_answer_out_of_range','repeated_entry_review','unsupported_widget','ashby_draft_unsupported','ashby_submission_unsupported','workday_unapproved_operation','workday_package_incomplete','workday_submit_not_reserved'}
DOCUMENT_REASONS = {'document_tampered','document_missing','upload_verification_failed','missing_resume','missing_transcript','stale_writing_context','unsupported_or_stale_sample','writing_upgrade_needed','writing_link_unverified'}
ACCOUNT_REASONS = {'account_identity_unconfirmed','account_fields_changed','account_fields_unavailable','captcha_blocked','account_or_verification_blocked','account_automation_disabled','account_result_uncertain','account_creation_held','account_credentials_unavailable','company_verification_pending','account_blocked','account_required','company_uncertain','email_verification_required','captcha','email_verification_failed'}
WORKDAY_ACCOUNT_REASONS = {'workday_session_unverified','workday_identity_mismatch','workday_acceptance_unverified','remote_draft_permission_required'}
TRANSIENT = {'posting_fetch_failed','network_error','navigation_failed','provider_timeout','provider_error','provider_invalid_output','writing_unsupported','ashby_draft_save_failed'}
UNCERTAIN = {'unknown','submitting','awaiting_verification','confirmed','rejected','not_submitted'}

ELIGIBILITY_REVIEWS={'citizenship_or_clearance_review','degree_requirement_review'}
LEGACY_MAIL_FAILURES={'gmail_reconnect_required','gmail_not_connected','gmail_account_mismatch','gmail_owner_missing','worker_busy'}


def mail_screening_hold(row):
    if row['reason'].startswith('prior_application_'):return row['reason']!='prior_application_email'
    if row['reason'] not in LEGACY_MAIL_FAILURES:return False
    try:return json.loads(row['evidence']).get('stage')=='screening'
    except (ValueError,TypeError,AttributeError):return False


def mail_check_failed(row):
    return (row['reason'] in {'prior_application_mail_unavailable','prior_application_company_review'}
            or row['reason'] in LEGACY_MAIL_FAILURES and mail_screening_hold(row))

# Each exclusion is already a proven blocker. Other profile/preferences edits
# cannot make that blocker pass and must not release it for another browser run.
ELIGIBILITY_INPUTS = {
    'seniority_mismatch': (set(),set()),
    'role_mismatch': (set(),{'roles'}),
    'internship_out_of_scope': (set(),{'seniority'}),
    'fulltime_out_of_scope': (set(),{'seniority'}),
    'parttime_out_of_scope': (set(),{'seniority'}),
    'location_mismatch': ({'summer_2027_relocate'},{'locations','summer_2027_locations','school_locations'}),
    'experience_mismatch': ({'professional_years'},{'max_years_required'}),
    'degree_mismatch': ({'degree'},set()),
    'degree_requirement_review': ({'degree'},set()),
    'sponsorship_mismatch': ({'needs_sponsorship'},set()),
    'citizenship_mismatch': ({'us_person'},set()),
    'citizenship_or_clearance_review': ({'citizenship','us_person'},set()),
    'graduation_mismatch': ({'graduation'},set()),
    'start_window_mismatch': ({'earliest_start','latest_start','fulltime_start'},set()),
    'compensation_mismatch': (set(),{'min_annual_usd','min_hourly_usd'}),
    'company_blocked': (set(),{'skip_companies','interview_companies','company_aliases'}),
    'low_fit': ({'skills'},{'min_fit_score'}),
    'expired_posting': (set(),set()),
}

def category(reason):
    if reason=='prior_application_email':return 'mail'
    if reason.startswith('prior_application_'):return 'limits'
    if reason=='workday_schema_unreviewed':return 'unsupported'
    if reason=='workday_native_read_failed':return 'account'
    if reason.startswith('connection_') and reason != 'connection_identity_review':return 'connection'
    if reason in NOT_MATCH_REASONS or reason in ELIGIBILITY_REVIEWS:return 'eligibility'
    if reason in FACT_REASONS:return 'information'
    if reason in MAPPING_REASONS:return 'mapping'
    if reason in DOCUMENT_REASONS:return 'documents'
    if reason in ACCOUNT_REASONS|WORKDAY_ACCOUNT_REASONS or reason=='account_agreement_review':return 'account'
    if reason in {'multi_step_requires_adapter','workday_transport_unverified','workday_preparation_requires_draft'}:return 'unsupported'
    if reason in TRANSIENT:return 'transient'
    if reason in WAIT_REASONS|{'company_uncertain','company_verification_pending','company_submission_rejected'}:return 'limits'
    return 'review'

def dependency(store, job, kind, reason=None):
    if kind=='mail':
        from .prior_applications import matches
        return digest([(row['id'],row['revision'],row['state'],row['match_reason']) for row in matches(store,job)])
    saved=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
    posting=json.loads(saved['payload']) if saved else job
    if reason=='remote_draft_uncertain':
        # Only a native readback/reconciliation changes this dependency. Facts,
        # settings and adapter upgrades never authorize repeating a saved step.
        return digest({
            'draft':[tuple(row) for row in store.db.execute('SELECT state,native_revision,evidence FROM workday_drafts WHERE job_id=?',(job['id'],))],
            'steps':[tuple(row) for row in store.db.execute('SELECT id,state,acknowledgement,updated FROM remote_draft_steps WHERE job_id=? ORDER BY id',(job['id'],))],
        })
    workday=None
    if reason and (reason.startswith('workday_') or reason=='remote_draft_permission_required'):
        from .workday import VERSION
        from .workday_profiles import owner_hash,profile_binding,transport,operational_evidence
        profile=transport(job['host'])
        workday={'version':VERSION,'profile':profile.signature if profile else None,
            'binding':profile_binding(store,job['host']),'owner':owner_hash(store),
            'verified':[tuple(sorted(row.items())) for row in operational_evidence(store,job['host'],VERSION)],
            'remote_drafts':store.settings()['remote_drafts']}
        if reason=='workday_preparation_requires_draft':
            workday['draft']=[tuple(row) for row in store.db.execute('SELECT state,native_revision,evidence FROM workday_drafts WHERE job_id=?',(job['id'],))]
            return digest(workday)
        if reason in WORKDAY_ACCOUNT_REASONS or reason=='workday_transport_unverified':
            return digest(workday)
    if reason and reason.startswith('connection_'):
        from .platform_connections import configuration,VERSION
        connection_id=job.get('connection_id')
        if connection_id:
            return digest({'version':VERSION,'connection':configuration(store,connection_id),
                'flows':[tuple(row) for row in store.db.execute('SELECT capability,version,signature,evidence,verified FROM connection_flows WHERE connection_id=? ORDER BY capability,signature',(connection_id,))],
                'facts':store.facts(),'documents':[tuple(row) for row in store.db.execute('SELECT kind,hash FROM documents ORDER BY kind')],
                'posting':posting.get('_listing_hash')})
    if kind=='eligibility':
        from .policy import ELIGIBILITY_VERSION
        fact_keys,setting_keys=ELIGIBILITY_INPUTS.get(reason,(
            set().union(*(v[0] for v in ELIGIBILITY_INPUTS.values())),
            set().union(*(v[1] for v in ELIGIBILITY_INPUTS.values()))))
        facts=store.facts() if fact_keys else {};settings=store.settings() if setting_keys else {}
        data={'version':2,'policy':ELIGIBILITY_VERSION,'reason':reason,
            'posting':posting.get('_listing_hash') or digest({k:posting.get(k,'') for k in ('url','company','title','location','description')}),
            'posting_constraints':{k:posting.get(k) for k in ('min_years','compensation','employment_type','source')},
            'facts':{k:facts[k]['value'] for k in sorted(fact_keys) if k in facts},
            'settings':{k:settings[k] for k in sorted(setting_keys) if k in settings}}
        if reason=='expired_posting':data['adapter']=ADAPTER_VERSION
        if reason in {'citizenship_mismatch','citizenship_or_clearance_review'}:
            from .policy import CITIZENSHIP_VERSION
            data['citizenship_policy']=CITIZENSHIP_VERSION
        return digest(data)
    data={'posting':posting.get('_listing_hash') or digest({k:posting.get(k,'') for k in ('url','company','title','location','description')}),'mapping':MAPPING_VERSION,'adapter':ADAPTER_VERSION}
    if reason=='ashby_submission_unsupported':
        from .ashby_submission import VERSION
        data['ashby_submission']=VERSION
    if workday is not None:data['workday']=workday
    if reason in {'captcha','captcha_blocked'}:data['captcha_detection']=CAPTCHA_DETECTION_VERSION
    if kind=='unsupported':return digest({'host':job['host'],'adapter':ADAPTER_VERSION})
    if kind in {'information','mapping','documents','eligibility','account'}:
        data['facts']={k:v['revision'] for k,v in store.facts().items()}
        keys={'summer_2027_locations','school_locations','locations','roles','seniority','skip_companies','interview_companies','company_aliases','min_fit_score','max_years_required','min_annual_usd','min_hourly_usd'} if kind=='eligibility' else {'prior_employers','contextual_preferences','tailored_writing','cover_letters','signed_in_portals','employer_accounts','gmail_verification'}
        data['settings']={k:v for k,v in store.settings().items() if k in keys}
    if kind in {'information','mapping','documents'}:
        from .answers import AUTOMATIC_ANSWER_VERSION
        data['automatic_answers']=AUTOMATIC_ANSWER_VERSION
        data['history']=[tuple(row) for row in store.db.execute('SELECT id,revision,confirmed FROM confirmed_history ORDER BY id')]
        data['answers']=[tuple(r) for r in store.db.execute('SELECT id,revision FROM answers ORDER BY id')]
        data['templates']=[tuple(r) for r in store.db.execute('SELECT id,revision FROM templates ORDER BY id')]
        data['documents']=[tuple(r) for r in store.db.execute('SELECT kind,hash FROM documents ORDER BY kind')]
        data['document_files']=[(r['filename'], (store.root/'documents'/r['filename']).stat().st_mtime_ns if (store.root/'documents'/r['filename']).is_file() else None) for r in store.db.execute('SELECT filename FROM documents ORDER BY kind')]
        data['materials']=[tuple(r) for r in store.db.execute('SELECT id,revision,confirmed,role FROM materials ORDER BY id')]
    if kind=='account':
        data['accounts']=[tuple(r) for r in store.db.execute('SELECT id,state,updated FROM employer_accounts ORDER BY id')]
        from .accounts import account_key
        key=account_key('https://'+job['host'],store.company(job['company']))
        row=store.db.execute("SELECT seq FROM events WHERE kind='account_credentials_saved' AND subject=? ORDER BY seq DESC LIMIT 1",(key,)).fetchone()
        # A scoped revision, never a password or a hash of password contents.
        if row:data['supplied_credentials_revision']=row['seq']
    return digest(data)

def safe_state(store, job_id):
    row=store.db.execute('SELECT state FROM applications WHERE job_id=?',(job_id,)).fetchone()
    return not row or row[0]=='prepared'

def _uncertain_draft(store,job_id):
    return bool(store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND state IN ('intent','unknown') LIMIT 1",(job_id,)).fetchone()
                or store.db.execute("SELECT 1 FROM workday_drafts WHERE job_id=? AND state='unknown'",(job_id,)).fetchone())

def ready(store, job, at=None):
    if not safe_state(store,job['id']):return False
    if _uncertain_draft(store,job['id']):return False
    row=store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
    if not row:return True
    if mail_screening_hold(row):return True
    if row['reason']=='model_budget_exhausted' and model_available(store,at):return True
    if row['category']=='limits':return True # Existing policy checks the current day/company limits.
    if row['category']=='transient':
        # A repaired adapter/mapping or a changed posting starts a new bounded
        # retry sequence. An unchanged exhausted failure stays held.
        return (row['dependency']!=dependency(store,job,'transient',row['reason'])
                or row['retry_at'] is not None and (at or time.time())>=row['retry_at'])
    if row['reason']=='citizenship_or_clearance_review':
        return row['dependency']!=dependency(store,job,'eligibility',row['reason'])
    if row['category']=='review':
        if row['reason']=='ambiguous_experience':
            # Legacy screening holds were classified as manual reviews. Only
            # revalidate this one policy reason, never an application attempt
            # or a different review. Archived attempts also remain held.
            try:evidence=json.loads(row['evidence'])
            except (ValueError,TypeError):return False
            if (not isinstance(evidence,dict) or evidence.get('stage')!='screening'
                    or store.db.execute("SELECT 1 FROM application_records WHERE job_id=? AND state!='prepared' LIMIT 1",(job['id'],)).fetchone()):
                return False
            from .policy import COMPETITIVE_TIMELINE,eligible
            saved=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if not saved:return False
            try:posting=json.loads(saved['payload'])
            except (ValueError,TypeError):return False
            if (not isinstance(posting,dict) or not isinstance(posting.get('description'),str)
                    or {k:v for k,v in posting.items() if k!='_listing_hash'}
                       != {k:v for k,v in job.items() if k!='_listing_hash'}
                    or not COMPETITIVE_TIMELINE.search(posting['description'])):
                return False
            try:eligible(posting,store.settings(),store.facts())
            except (Blocked,TypeError,ValueError,KeyError,AttributeError):return False
            return True
        return (row['reason']=='remote_draft_uncertain'
                and bool(store.db.execute("SELECT 1 FROM workday_drafts WHERE job_id=? AND state='acknowledged'",(job['id'],)).fetchone())
                and row['dependency']!=dependency(store,job,'review',row['reason']))
    return row['dependency']!=dependency(store,job,row['category'],row['reason'])

def model_available(store,at=None):
    settings=store.settings();moment=datetime.fromtimestamp(time.time() if at is None else at,timezone.utc)
    today=moment.astimezone(ZoneInfo(settings['timezone'])).date()
    daily=sum(datetime.fromisoformat(r[0]).astimezone(ZoneInfo(settings['timezone'])).date()==today for r in store.db.execute('SELECT timestamp FROM model_requests WHERE timestamp>=?',((moment-timedelta(days=2)).isoformat(),)))
    run=getattr(store,'active_run_id',None)
    used=store.db.execute('SELECT count(*) FROM model_requests WHERE run_id=?',(run,)).fetchone()[0] if run else 0
    return daily<settings['max_model_requests_per_day'] and used<settings['max_model_requests_per_cycle']

def hold(store, job, reason, detail='', stage='application', at=None):
    if not safe_state(store,job['id']):return
    saved=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
    if saved:job=json.loads(saved['payload'])
    kind=category(reason)
    fields=[dict(row) for row in store.db.execute('SELECT label,reason,options FROM questions WHERE job_id=? AND resolved=0',(job['id'],))]
    if reason=='missing_answers' and any(f['reason'] in MAPPING_REASONS for f in fields):kind='mapping'
    previous=store.db.execute('SELECT retry_count,dependency FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
    fingerprint=dependency(store,job,kind,reason)
    retries=previous['retry_count']+1 if previous and previous['dependency']==fingerprint else 0
    retry_at=(at or time.time())+(1800 if retries==0 else 7200) if kind=='transient' and retries<2 else None
    guidance={'eligibility':'Change relevant preferences or wait for a posting update.',
        'mail':'Review Past applications in Settings → Connections. Check the original email before applying again.',
        'connection':'Open Connections to restore sign-in or validate the affected platform flow. Enabling a setting alone cannot validate access.',
        'information':'Confirm the missing answer in Your facts or the question queue.',
        'mapping':'Review the exact employer choices. A corrected answer or mapping update releases this hold.',
        'documents':'Repair or update the approved document or writing source.',
        'account':'Verify the employer account or review the verification task.',
        'unsupported':'Complete manually or wait for an application adapter update.',
        'transient':'Wait for the scheduled retry; exhausted retries require review.',
        'limits':'Wait for existing company or daily limits to clear.',
        'review':'Review this failure before allowing another attempt.'}
    if reason=='model_budget_exhausted':guidance[kind]='Approve the unanswered fields, or wait for the next model allowance. Request limits remain unchanged.'
    if reason in REASON_GUIDANCE and (reason.startswith(('workday_','prior_application_')) or reason in {'remote_draft_uncertain','remote_draft_permission_required','history_confirmation_required','history_changed'}):
        guidance[kind]=REASON_GUIDANCE[reason][1]
    # References and field labels only: no answer values, passwords, or OTPs.
    evidence={'stage':stage,'category':kind,'field':str(detail)[:300] if reason in FACT_REASONS|MAPPING_REASONS else '',
        'fields':[{**f,'options':json.loads(f['options'])} for f in fields],
        'sources':[{'fact_key':k,'revision':v['revision']} for k,v in store.facts().items()] if kind in {'information','mapping'} else [],
        'next_action':guidance[kind]}
    store.db.execute('INSERT OR REPLACE INTO job_holds VALUES(?,?,?,?,?,?,?,?)',
        (job['id'],kind,reason,fingerprint,retries,retry_at,json.dumps(evidence),now()))
    store.event('job_held',job['id'],{'reason':reason,**evidence})

def clear(store,job_id):store.db.execute('DELETE FROM job_holds WHERE job_id=?',(job_id,))

def annotate(store,jobs):
    for job in jobs:
        if job.get('status')!='blocked':continue
        row=store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
        if row:
            job['hold']={**json.loads(row['evidence']),'reason':row['reason'],'retry_at':row['retry_at'],'retry_count':row['retry_count']}
    return jobs

def recheck(store,job_id):
    row=store.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise ValueError('Opportunity not found')
    store.check_job_decision(job_id)
    if not safe_state(store,job_id):raise ValueError('Verify the existing submission outcome; another attempt is not authorized')
    if _uncertain_draft(store,job_id):return {'ready':False,'reason':'remote_draft_uncertain'}
    try:job=json.loads(row['payload'])
    except (ValueError,TypeError):return {'ready':False,'reason':'invalid_posting'}
    if not isinstance(job,dict) or job.get('id')!=job_id or not isinstance(job.get('description'),str):
        return {'ready':False,'reason':'invalid_posting'}
    from .policy import eligible
    try:eligible(job,store.settings(),store.facts())
    except Blocked as error:
        hold(store,job,error.reason,error.detail,'screening');return {'ready':False,'reason':error.reason}
    except (TypeError,ValueError,KeyError,AttributeError):
        return {'ready':False,'reason':'invalid_posting'}
    available=ready(store,job)
    previous=store.db.execute('SELECT category,retry_at,retry_count,reason,dependency FROM job_holds WHERE job_id=?',(job_id,)).fetchone()
    if not available and previous and previous['reason'] in {'workday_session_unverified','workday_identity_mismatch'}:
        # Recheck requests a fresh authenticated read; the adapter must verify
        # the actual owner/session before any write. Unknown drafts stay above.
        available=True
        store.event('workday_session_recheck_requested',job_id,{'reason':previous['reason']})
    if not available and previous and previous['reason']=='human_verification_unavailable':
        # The desktop can recover without changing stored preferences. This
        # explicit action retries only preflight; safe_state above excludes any
        # existing submission intent and Browser checks actual visibility again.
        available=True
        store.event('human_browser_recheck_requested',job_id,{'reason':previous['reason']})
    if (not available and previous and previous['reason']=='human_work_sample'
            and previous['dependency']!=dependency(store,job,previous['category'],previous['reason'])):
        # An explicit review can request a fresh read after a guard repair.
        # The browser and question resolver still refuse actual assessments;
        # safe_state above prevents this from reconciling an existing attempt.
        available=True
        store.event('work_sample_recheck_requested',job_id,{'reason':previous['reason']})
    if not available and previous and previous['category']=='transient' and previous['retry_at'] is None:
        # This explicit dashboard action is the review path promised for an
        # exhausted transient hold. It never reconciles an attempted outcome.
        available=True
        store.event('transient_recheck_requested',job_id,{'reason':previous['reason'],'previous_retries':previous['retry_count']})
    if available:
        clear(store,job_id)
        store.db.execute("UPDATE jobs SET status='discovered',reason='' WHERE id=?",(job_id,))
    return {'ready':available,'reason':'' if available else 'unchanged_hold'}
