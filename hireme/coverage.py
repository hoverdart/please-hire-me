"""Read-only ATS/source coverage and per-run outcome denominators."""
import json
from pathlib import Path
from .field_context import ats
from .util import now

def cycle_funnel(store):
    run=store.db.execute('SELECT * FROM runs ORDER BY started DESC LIMIT 1').fetchone()
    if not run:return None
    try:detail=json.loads(run['detail'] or '{}')
    except ValueError:detail={}
    attempted=set();blocked=set();outcomes={};screened=set()
    for event in store.db.execute("SELECT kind,subject,detail FROM events WHERE timestamp>=? AND kind IN ('application_started','application_finished','job_screening_blocked')",(run['started'],)):
        try:data=json.loads(event['detail'])
        except ValueError:continue
        if data.get('run_id')!=run['id']:continue
        if event['kind']=='application_started':attempted.add(event['subject'])
        elif event['kind']=='job_screening_blocked':screened.add(event['subject'])
        elif event['kind']=='application_finished':
            outcome=data.get('outcome','error');outcomes[event['subject']]=outcome
            if outcome in {'blocked','error'}:blocked.add(event['subject'])
    uncertain=sum(1 for row in store.db.execute("SELECT job_id,state FROM applications WHERE state IN ('unknown','submitting','awaiting_verification')") if row['job_id'] in attempted)
    counts={k:sum(v==k for v in outcomes.values()) for k in ('prepared','confirmed')}
    return {'run_id':run['id'],'status':run['status'],'mode':detail.get('mode','live'),
        'discovered':detail.get('discovered'), 'eligible':detail.get('eligible'),
        'attempted':len(attempted),'prepared':counts['prepared'],'confirmed':counts['confirmed'],
        'blocked':len(blocked),'uncertain':uncertain,'screened_out':len(screened)}

def coverage(store):
    groups={}
    def group(platform,source):
        key=(platform,source)
        if key not in groups:
            groups[key]={'ats':platform,'source':source,'discovered':0,'eligible':0,'attempted':0,'confirmed':0,'blocked':0,'uncertain':0,
                'configured':False,
                'discovery_supported':True,'application_capability':'single-step forms' if platform in {'Greenhouse','Ashby','Lever'} else 'manual completion',
                'account_required':platform=='Workday','limitations':'Account setup and multi-step submission adapter required.' if platform=='Workday' else 'CAPTCHA and unsupported widgets may require review.',
                'health':'not_checked','checked_at':None}
        return groups[key]
    from .discovery import board_sources
    repo=Path(__file__).resolve().parents[1]
    if (repo/'data/boards.md').exists():
        for kind,slug in board_sources(repo,store=store):group({'gh':'Greenhouse','ash':'Ashby','lv':'Lever'}.get(kind,kind),kind+':'+slug)['configured']=True
    from .portals import WORKDAY
    for label,tenant,wd,site,host in WORKDAY:
        configured=group('Workday','portal:'+label)
        configured.update(configured=True,host=host)
    for row in store.db.execute('SELECT j.host,j.source,j.status,a.state,a.attempted FROM jobs j LEFT JOIN applications a ON a.job_id=j.id'):
        g=group(ats(row['host']),row['source']);g['discovered']+=1
        g['eligible']+=row['status'] in {'prepared','confirmed','unknown','submitting','awaiting_verification'}
        g['attempted']+=bool(row['attempted']);g['confirmed']+=row['state']=='confirmed'
        g['blocked']+=row['status']=='blocked';g['uncertain']+=row['state'] in {'unknown','submitting','awaiting_verification'}
    # Browser attempts that stopped before submit intent still count as attempts.
    attempts={r['subject'] for r in store.db.execute("SELECT DISTINCT subject FROM events WHERE kind='application_started'")}
    actual={}
    for row in store.db.execute('SELECT id,host,source FROM jobs'):
        if row['id'] in attempts:
            key=(ats(row['host']),row['source']);actual[key]=actual.get(key,0)+1
    for key,g in groups.items():g['attempted']=max(g['attempted'],actual.get(key,0))
    health={row['id']:row for row in store.db.execute('SELECT id,status,checked FROM sources')}
    for g in groups.values():
        row=health.get(g['source'])
        if g['ats']=='Workday' and 'portals' in health:
            g['aggregate_health']=health['portals']['status'];g['aggregate_checked_at']=health['portals']['checked']
        if row:
            g['health']=row['status'];g['checked_at']=row['checked']
    return {'checked_at':now(),'groups':sorted(groups.values(),key=lambda g:(g['ats'],g['source'])),
        'eligible_definition':'Current prepared/submission states; screening failures and unreviewed discoveries excluded.',
        'funnel':cycle_funnel(store)}
