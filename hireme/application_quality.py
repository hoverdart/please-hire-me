"""Local ranking from confirmed sources. Ranking never grants application authority."""
from __future__ import annotations

import re


# Domain matches need both posting text and applicant-confirmed experience.
DOMAINS = {
    'backend': r'\bback[- ]?end\b|\bapi\b|\bserver[- ]side\b',
    'production systems': r'\bproduction\b|\bdistributed systems\b|\breliability\b|\binfrastructure\b',
    'AI engineering': r'\bmachine learning\b|\bllms?\b|\bartificial intelligence\b|\bai\b',
    'manufacturing software': r'\bmanufactur\w*\b|\bshop floor\b|\bindustrial automation\b',
    'research': r'\bresearch\b|\bexperiments?\b',
}


def relevance(job, score, matched_skills, history):
    posting=job.get('title','')+'\n'+job.get('description','')
    matches=[]
    for entry in history:
        if entry['kind']!='employment':continue
        values=entry['values']
        source=values.get('title','')+'\n'+values.get('description','')
        domains=[name for name,pattern in DOMAINS.items()
                 if re.search(pattern,posting,re.I) and re.search(pattern,source,re.I)]
        if domains:matches.append({'history_id':entry['id'],'revision':entry['revision'],'domains':domains})
    bonus=min(10,3*len({domain for match in matches for domain in match['domains']}))
    return min(100,score+bonus),{'matched_skills':matched_skills,'experience':matches}


def rescreen_scope(store,rows,settings):
    """Eligibility changes take precedence over old missing-answer holds."""
    import json
    from .job_holds import hold,dependency,pending_write_jobs,UNCERTAIN_WRITE_REASONS
    from .policy import listing_scope,eligible
    from .posting_inspection import needs_details
    from .retry_authorization import safe_state
    from .util import Blocked
    counts={}
    pending=pending_write_jobs(store)
    for offset in range(0,len(rows),100):
        with store.transaction():
            for row in rows[offset:offset+100]:
                store.checkpoint();job=json.loads(row['payload'])
                if not safe_state(store,job['id']) or store.db.execute('SELECT 1 FROM job_decisions WHERE job_id=?',(job['id'],)).fetchone():continue
                previous=store.db.execute('SELECT category,reason,dependency FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
                if job['id'] in pending or previous and previous['reason'] in UNCERTAIN_WRITE_REASONS:continue
                if previous and previous['category']=='eligibility' and previous['dependency']==dependency(store,job,previous['category'],previous['reason']):continue
                try:
                    listing_scope(job,settings,metadata=True)
                    if not needs_details(job):eligible(job,settings,store.facts())
                except Blocked as error:
                    if previous and previous['reason']==error.reason and previous['dependency']==dependency(store,job,previous['category'],error.reason):continue
                    store.block(job['id'],error.reason,error.detail);hold(store,job,error.reason,error.detail,'screening')
                    counts[error.reason]=counts.get(error.reason,0)+1
                    store.event('job_screening_blocked',job['id'],{'run_id':getattr(store,'active_run_id',None),'outcome':'blocked','reason':error.reason,'detail':error.detail})
    return counts
