"""Dependency-based holds. Submission uncertainty never authorizes another attempt."""
from __future__ import annotations
import json
import time
from .field_context import MAPPING_VERSION, ADAPTER_VERSION
from .presentation import NOT_MATCH_REASONS, WAIT_REASONS
from .util import digest, now, Blocked

FACT_REASONS = {'missing_answers','missing_fact','required_answer_missing','stale_answer','answer_too_long','numeric_answer_needed','phone_country_review'}
MAPPING_REASONS = {'mapping_review','option_mismatch','form_changed','field_verification_failed','unsupported_form','invalid_option_metadata','invalid_binding_source','invalid_date_answer'}
DOCUMENT_REASONS = {'document_tampered','document_missing','upload_verification_failed','missing_resume','missing_transcript','stale_writing_context','unsupported_or_stale_sample','writing_upgrade_needed'}
ACCOUNT_REASONS = {'captcha_blocked','account_or_verification_blocked','account_automation_disabled','account_result_uncertain','account_creation_held','account_credentials_unavailable','company_verification_pending','account_blocked','account_required','company_uncertain','email_verification_required','captcha','email_verification_failed'}
TRANSIENT = {'posting_fetch_failed','network_error','navigation_failed'}
UNCERTAIN = {'unknown','submitting','awaiting_verification','confirmed','rejected','not_submitted'}

def category(reason):
    if reason in NOT_MATCH_REASONS:return 'eligibility'
    if reason in FACT_REASONS:return 'information'
    if reason in MAPPING_REASONS:return 'mapping'
    if reason in DOCUMENT_REASONS:return 'documents'
    if reason in ACCOUNT_REASONS:return 'account'
    if reason=='multi_step_requires_adapter':return 'unsupported'
    if reason in TRANSIENT:return 'transient'
    if reason in WAIT_REASONS:return 'limits'
    return 'review'

def dependency(store, job, kind):
    saved=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
    posting=json.loads(saved['payload']) if saved else job
    data={'posting':posting.get('_listing_hash') or digest({k:posting.get(k,'') for k in ('url','company','title','location','description')}),'mapping':MAPPING_VERSION,'adapter':ADAPTER_VERSION}
    if kind=='unsupported':return digest({'host':job['host'],'adapter':ADAPTER_VERSION})
    if kind in {'information','mapping','documents','eligibility','account'}:
        data['facts']={k:v['revision'] for k,v in store.facts().items()}
        keys={'summer_2027_locations','school_locations','locations','roles','seniority','skip_companies','interview_companies','company_aliases','min_fit_score','max_years_required','min_annual_usd','min_hourly_usd'} if kind=='eligibility' else {'prior_employers','contextual_preferences','tailored_writing','cover_letters','signed_in_portals','employer_accounts','gmail_verification'}
        data['settings']={k:v for k,v in store.settings().items() if k in keys}
    if kind in {'information','mapping','documents'}:
        data['answers']=[tuple(r) for r in store.db.execute('SELECT id,revision FROM answers ORDER BY id')]
        data['templates']=[tuple(r) for r in store.db.execute('SELECT id,revision FROM templates ORDER BY id')]
        data['documents']=[tuple(r) for r in store.db.execute('SELECT kind,hash FROM documents ORDER BY kind')]
        data['document_files']=[(r['filename'], (store.root/'documents'/r['filename']).stat().st_mtime_ns if (store.root/'documents'/r['filename']).is_file() else None) for r in store.db.execute('SELECT filename FROM documents ORDER BY kind')]
        data['materials']=[tuple(r) for r in store.db.execute('SELECT id,revision,confirmed,role FROM materials ORDER BY id')]
    if kind=='account':data['accounts']=[tuple(r) for r in store.db.execute('SELECT id,state,updated FROM employer_accounts ORDER BY id')]
    return digest(data)

def safe_state(store, job_id):
    row=store.db.execute('SELECT state FROM applications WHERE job_id=?',(job_id,)).fetchone()
    return not row or row[0]=='prepared'

def ready(store, job, at=None):
    if not safe_state(store,job['id']):return False
    row=store.db.execute('SELECT * FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
    if not row:return True
    if row['category']=='limits':return True # Existing policy checks the current day/company limits.
    if row['category']=='transient':return row['retry_at'] is not None and (at or time.time())>=row['retry_at']
    if row['category']=='review':return False
    return row['dependency']!=dependency(store,job,row['category'])

def hold(store, job, reason, detail='', stage='application', at=None):
    if not safe_state(store,job['id']):return
    saved=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
    if saved:job=json.loads(saved['payload'])
    kind=category(reason)
    fields=[dict(row) for row in store.db.execute('SELECT label,reason,options FROM questions WHERE job_id=? AND resolved=0',(job['id'],))]
    if reason=='missing_answers' and any(f['reason'] in MAPPING_REASONS for f in fields):kind='mapping'
    previous=store.db.execute('SELECT retry_count,dependency FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
    fingerprint=dependency(store,job,kind)
    retries=previous['retry_count']+1 if previous and previous['dependency']==fingerprint else 0
    retry_at=(at or time.time())+(1800 if retries==0 else 7200) if kind=='transient' and retries<2 else None
    guidance={'eligibility':'Change relevant preferences or wait for a posting update.',
        'information':'Confirm the missing answer in Your facts or the question queue.',
        'mapping':'Review the exact employer choices. A corrected answer or mapping update releases this hold.',
        'documents':'Repair or update the approved document or writing source.',
        'account':'Verify the employer account or review the verification task.',
        'unsupported':'Complete manually or wait for an application adapter update.',
        'transient':'Wait for the scheduled retry; exhausted retries require review.',
        'limits':'Wait for existing company or daily limits to clear.',
        'review':'Review this failure before allowing another attempt.'}
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
    job=json.loads(row['payload'])
    from .policy import eligible
    try:eligible(job,store.settings(),store.facts())
    except Blocked as error:
        hold(store,job,error.reason,error.detail,'screening');return {'ready':False,'reason':error.reason}
    available=ready(store,job)
    if available:
        clear(store,job_id)
        store.db.execute("UPDATE jobs SET status='discovered',reason='' WHERE id=?",(job_id,))
    return {'ready':available,'reason':'' if available else 'unchanged_hold'}
