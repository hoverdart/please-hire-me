"""Read-only dashboard state for a bounded wait in the existing browser."""
from __future__ import annotations

import json
import math
import time


def current_wait(store):
    if not store.settings()['live_enabled']:return None
    from .recovery import worker_status
    if not worker_status(store)['running']:return None
    run=store.db.execute("SELECT id FROM runs WHERE status='running' ORDER BY started DESC LIMIT 1").fetchone()
    if not run:return None
    app=store.db.execute("""SELECT a.id,a.job_id,j.company,j.title FROM applications a
        JOIN jobs j ON j.id=a.job_id WHERE a.state='submitting' ORDER BY a.updated DESC LIMIT 1""").fetchone()
    if not app:return None
    row=store.db.execute("SELECT detail FROM events WHERE kind='human_verification' AND subject=? ORDER BY seq DESC LIMIT 1",(app['id'],)).fetchone()
    if not row:return None
    try:detail=json.loads(row['detail'])
    except (ValueError,TypeError):return None
    if not isinstance(detail,dict) or detail.get('run_id')!=run['id'] or detail.get('job_id')!=app['job_id']:return None
    expiry=detail.get('expires_at');now=time.time()
    if (detail.get('phase') not in {'challenge','checking_receipt'} or type(expiry) not in (int,float)
            or not math.isfinite(expiry) or not now<expiry<=now+645):return None
    return {'application_id':app['id'],'job_id':app['job_id'],'company':app['company'],'title':app['title'],
            'phase':detail['phase'],'expires_at':expiry}
