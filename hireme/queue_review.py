"""Reassess saved holds without discarding drafts or authorizing retries."""
import json
from collections import Counter
from .util import Blocked,now
from .application_evidence import ats_for


def revalidate(store, *, ats=('lever','ashby'), refresh=True):
    from .policy import eligible
    from .job_holds import safe_state,hold,clear
    from .ashby_guard import screening_snapshot
    from .posting_inspection import inspect
    from .browser_agent_protocol import guard_pending
    settings=store.settings();facts=store.facts();drafts=screening_snapshot(store)
    rows=list(store.db.execute('''SELECT j.payload,h.reason FROM job_holds h JOIN jobs j ON j.id=h.job_id
        WHERE j.status IN ('blocked','discovered','prepared') ORDER BY j.score DESC,j.id'''))
    results=[]
    for row in rows:
        job=json.loads(row['payload'])
        if ats_for(job['url']) not in ats:continue
        store.checkpoint()
        result={'job_id':job['id'],'previous_reason':row['reason'],'status':'held','posting_refreshed':False}
        try:
            eligible(job,settings,facts)
            if not safe_state(store,job['id']):raise Blocked('duplicate_or_uncertain')
            if drafts.blocks(job):raise Blocked('ashby_draft_uncertain')
            guard_pending(store,job)
            store._check_budget(job,settings)
            if refresh:
                job=inspect(store,job);store.upsert_job(job);result['posting_refreshed']=True
                eligible(job,settings,facts)
            # Clear only the local scheduling hold. Native draft fences and
            # saved packages remain; Browser.prepare re-extracts and verifies
            # the current form, answers and attachments before a new package.
            clear(store,job['id'])
            store.db.execute("UPDATE jobs SET status='discovered',reason='' WHERE id=?",(job['id'],))
            result.update(status='needs_form_preparation',reason='')
        except Blocked as error:
            if error.reason in {'paused','cycle_timeout'}:raise
            result['reason']=error.reason
        except Exception as error:
            result['reason']='posting_review_unavailable';result['error_type']=type(error).__name__
        results.append(result)
        # Retain current decisions for review without rewriting historical
        # submission outcomes or laundering an uncertain draft into a retry.
        if result['previous_reason']!=result.get('reason') or result['posting_refreshed']:
            store.event('held_application_revalidated',job['id'],result)
    report={'assessed_at':now(),'counts':dict(Counter(r.get('reason') or r['status'] for r in results)),
            'jobs':results,'forms_pending':sum(r['status']=='needs_form_preparation' for r in results)}
    store.event('held_queue_revalidated','queue',{'assessed_at':report['assessed_at'],'counts':report['counts'],'forms_pending':report['forms_pending']})
    return report


def prepare_review(store, repo, report):
    """Finish the current-form checks one posting at a time, without submission."""
    from .worker import cycle
    from .job_holds import clear,safe_state,FACT_REASONS,MAPPING_REASONS,DOCUMENT_REASONS
    try:
        for item in report['jobs']:
            if item['status']!='needs_form_preparation' or item.get('form_review'):continue
            # An explicit revalidation must revisit an old answer/form hold
            # even when its dependency fingerprint has not changed. Keep all
            # submission and uncertain-draft fences; cycle rechecks them.
            held=store.db.execute('SELECT reason FROM job_holds WHERE job_id=?',(item['job_id'],)).fetchone()
            if held and held[0] in FACT_REASONS|MAPPING_REASONS|DOCUMENT_REASONS and safe_state(store,item['job_id']):
                clear(store,item['job_id'])
            previous=store.db.execute("SELECT hash FROM applications WHERE job_id=? AND state='prepared'",(item['job_id'],)).fetchone()
            try:
                result=cycle(store,repo,discover=False,live=False,limit=1,max_attempts=1,job_ids=[item['job_id']])
                item['form_review']={'prepared':result.get('prepared',0)==1,'result':result}
            except Blocked as error:
                if error.reason in {'paused','cycle_timeout','rollout_stale'}:raise
                item['form_review']={'prepared':False,'reason':error.reason}
            store.event('held_form_revalidated',item['job_id'],{'previous_package_hash':previous[0] if previous else None,**item['form_review']})
    finally:
        report['forms_pending']=sum(item['status']=='needs_form_preparation' and not item.get('form_review') for item in report['jobs'])
        report['forms_prepared']=sum(item.get('form_review',{}).get('prepared',False) for item in report['jobs'])
    return report
