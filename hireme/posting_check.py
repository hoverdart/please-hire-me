"""Read-only policy feedback using the saved posting and confirmed applicant facts."""
from __future__ import annotations

import json

from .config import FACTS
from .policy import eligible
from .presentation import job_display, STATUS_LABELS
from .util import Blocked


def check_saved_posting(store, job_id):
    if not isinstance(job_id,str) or not job_id or len(job_id)>100:
        raise ValueError('Choose an existing opportunity')
    row=store.db.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise ValueError('Opportunity not found')
    attempt=store.db.execute('SELECT state FROM applications WHERE job_id=?',(job_id,)).fetchone()
    if attempt and attempt['state']!='prepared':
        label=STATUS_LABELS.get(attempt['state'],attempt['state'].replace('_',' '))
        return {'result':'recorded_outcome','label':'An application outcome is already recorded',
                'detail':f'{label}. This posting cannot be automatically attempted again.', 'score':None}
    missing=store.missing_setup()
    if missing:
        return {'result':'needs_setup','label':'Complete your required facts and resume first',
                'detail':'Still needed: '+', '.join(FACTS.get(key,key) for key in missing), 'score':None}
    try:job=json.loads(row['payload'])
    except (ValueError,TypeError):raise ValueError('The saved posting could not be read') from None
    if not isinstance(job,dict):raise ValueError('The saved posting could not be read')
    score=None
    try:
        settings=store.settings()
        score,_=eligible(job,settings,store.facts())
        store._check_budget(job,settings)
    except Blocked as error:
        display=job_display({'status':'blocked','reason':str(error)})
        return {'result':'held','label':display['reason_label'],'detail':display['next_step'],
                'reason':str(error),'score':score}
    return {'result':'matches','label':'Matches your saved preferences and current limits',
            'detail':'The worker will inspect the live posting and application form again before any submission.', 'score':score}
