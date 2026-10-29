"""One-time conservative protection for pre-journal Ashby guest attempts.

Historical records cannot prove that an unlogged direct browser call did not
write a guest draft. This migration protects the evidence that was recorded;
absence of such evidence is not proof that no earlier draft exists.
"""
from __future__ import annotations

import json

from .util import digest, now

MARKER = 'ashby_legacy_guard_v1'


def _object(value):
    try:
        data=json.loads(value)
    except (TypeError,ValueError):
        return {}
    return data if isinstance(data,dict) else {}


def protect_legacy_ashby(store):
    """Fence previously observed ambiguous/live attempts, exactly once.

    The caller may already hold a transaction (e.g. worker recovery). Marker
    and fences share that outer transaction, or an immediate transaction here.
    No applicant values or request bodies are copied into the new records.
    """
    with store.transaction():
        if store.db.execute('SELECT 1 FROM events WHERE kind=? AND subject=? LIMIT 1',(MARKER,'migration')).fetchone():
            return
        events=store.db.execute("""SELECT e.seq,e.kind,e.subject,e.detail FROM events e
            JOIN jobs j ON j.id=e.subject
            WHERE lower(j.host)='jobs.ashbyhq.com' AND e.kind IN
              ('application_started','application_finished','ashby_draft_write','ashby_texting_write','field_answered','field_filling')
            ORDER BY e.seq""")
        runs={row['id']:_object(row['detail']).get('mode') for row in store.db.execute('SELECT id,detail FROM runs')}
        # Field events carry no run ID. Exempt them only when enclosed by one
        # paired prepare-mode attempt. Unpaired/interleaved events stay held.
        active={}; ambiguous=set()
        for event in events:
            job=event['subject']; kind=event['kind']; detail=_object(event['detail'])
            if kind=='application_started':
                if job in active:
                    ambiguous.add(job)
                rid=detail.get('run_id')
                mode=runs.get(rid) if isinstance(rid,str) else None
                if mode!='prepare':ambiguous.add(job)
                active[job]={'run_id':rid,'prepare':mode=='prepare','fields':False}
            elif kind=='application_finished':
                previous=active.pop(job,None)
                if previous and previous['fields'] and (not previous['prepare'] or detail.get('run_id')!=previous['run_id']):
                    ambiguous.add(job)
            elif kind in ('ashby_draft_write','ashby_texting_write'):
                ambiguous.add(job)
            else:
                previous=active.get(job)
                if previous is None or not previous['prepare']:
                    ambiguous.add(job)
                else:
                    previous['fields']=True
        ambiguous.update(job for job,attempt in active.items() if attempt['fields'])
        protected=0
        for job in sorted(ambiguous):
            if store.db.execute("SELECT 1 FROM application_records WHERE job_id=? AND state!='prepared' LIMIT 1",(job,)).fetchone():
                continue
            if store.db.execute("SELECT 1 FROM remote_draft_steps WHERE job_id=? AND step LIKE 'ashby:%' AND state IN ('intent','unknown') LIMIT 1",(job,)).fetchone():
                continue
            record=digest([MARKER,job]); stamp=now()
            store.db.execute('INSERT INTO remote_draft_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
                (record,job,'jobs.ashbyhq.com','ashby:legacy',digest('legacy-activity'),digest(MARKER),'unknown',stamp,stamp,''))
            protected+=1
        store.event(MARKER,'migration',{'protected_jobs':protected})
