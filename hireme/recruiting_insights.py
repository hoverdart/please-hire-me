"""Descriptive recruiting comparisons with explicit exposure, review coverage and evidence."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .application_journey import _snapshot
from .gmail import owner_email
from .util import Blocked, digest

METRICS = {
    'attempts':'Final intents', 'confirmed':'Confirmed submissions', 'mature':'Full observation window',
    'recent':'Still within observation window', 'uncertain':'Uncertain submissions',
    'not_submitted':'Not submitted / rejected at submission', 'timing_unavailable':'Submission timing unavailable',
    'responded':'Reviewed recruiting responses', 'assessment':'Assessments', 'interview':'Interviews',
    'offer':'Offers', 'rejection':'Recruiting rejections', 'reviewed_mail':'Any reviewed mail',
    'pending_review':'Matching mail needs review', 'awaiting':'No reviewed recruiting response',
    'current_metadata':'Current posting metadata only', 'identity_unavailable':'Posting identity unavailable',
}
STAGES = {'assessment','interview','offer','rejection'}


def _time(value):
    try:
        result=datetime.fromisoformat(value)
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError,TypeError):return None


def _contexts(store,records):
    found={};identities=list(records)
    for start in range(0,len(identities),400):
        chunk=identities[start:start+400];marks=','.join('?' for _ in chunk)
        for row in store.db.execute(f"""SELECT kind,subject,detail FROM events
            WHERE kind IN ('prepared','submit_intent') AND subject IN ({marks}) ORDER BY seq DESC""",chunk):
            aid=row['subject']
            if aid in found and (found[aid][1]=='submit_intent' or row['kind']=='prepared'):continue
            try:detail=json.loads(row['detail'])
            except (ValueError,TypeError):continue
            context=detail.get('job_context') if isinstance(detail,dict) else None
            if (isinstance(context,dict) and context.get('id')==records[aid]['job_id'] and
                    detail.get('hash',detail.get('package_hash'))==records[aid]['hash']):
                found[aid]=(context,row['kind'])
    return found


def _resumes(record,artifacts):
    try:package=json.loads(record['package'])
    except (ValueError,TypeError):return []
    if not isinstance(package,dict) or digest(package)!=record['hash'] or not isinstance(package.get('documents'),list):return []
    result={}
    for doc in package['documents']:
        if not isinstance(doc,dict) or doc.get('kind')!='resume':continue
        checksum=doc.get('hash')
        if not isinstance(checksum,str) or not re.fullmatch(r'[a-f0-9]{64}',checksum) or doc.get('filename')!=checksum+'.pdf':continue
        item={'hash':checksum,'revision':None,'artifact_id':None}
        artifact=artifacts.get(doc.get('artifact_id')) if isinstance(doc.get('artifact_id'),str) else None
        if artifact and (artifact['job_id'],artifact['kind'],artifact['hash'],artifact['filename'])==(record['job_id'],'resume',checksum,doc['filename']):
            item.update(revision=artifact['revision'],artifact_id=artifact['id'])
        result[checksum]=item
    return [result[key] for key in sorted(result)]


def _mail(store,records,clock):
    result={aid:{'reviewed':[],'pending':[]} for aid in records}
    try:owner=digest(owner_email(store))
    except Blocked:return result
    for row in store.db.execute("""SELECT id,received,subject,category,state,application_id,candidates
        FROM recruiting_feedback WHERE owner_hash=? AND state IN ('pending','confirmed') ORDER BY received,id""",(owner,)):
        received=_time(row['received'])
        if received is None or received>clock:continue
        if row['state']=='confirmed':
            ids=[row['application_id']]
        else:
            try:candidates=json.loads(row['candidates'])
            except (ValueError,TypeError):continue
            ids={item.get('id') for item in candidates if isinstance(item,dict) and isinstance(item.get('id'),str)} if isinstance(candidates,list) else []
        for aid in ids:
            if aid not in result:continue
            record=records[aid]
            if record['_at'] is None or not record['_at']<=received<=record['_end']:continue
            item={key:row[key] for key in ('id','received','subject','category')}
            result[aid]['reviewed' if row['state']=='confirmed' else 'pending'].append(item)
    return result


def _members(record):
    state=record['state'];mature=state=='confirmed' and record['_at'] is not None and record['_end']<=record['_clock']
    stages={item['category'] for item in record['_mail']['reviewed']} & STAGES
    result={'attempts'}
    if record['_at'] is None:result.add('timing_unavailable')
    if record['_basis']=='current_job_metadata':result.add('current_metadata')
    if record['_basis']=='unavailable':result.add('identity_unavailable')
    if state=='confirmed':
        result.add('confirmed')
        if mature:result.add('mature')
        elif record['_at'] is not None:result.add('recent')
    if state in {'unknown','submitting','awaiting_verification'}:result.add('uncertain')
    if state in {'not_submitted','rejected'}:result.add('not_submitted')
    if mature:
        result.update(stages)
        result.add('responded' if stages else 'awaiting')
        if record['_mail']['reviewed']:result.add('reviewed_mail')
        if record['_mail']['pending']:result.add('pending_review')
    return result


def _group(record,group_by):
    if group_by=='resume':
        hashes=[item['hash'] for item in record['_resumes']]
        return digest(['resume',hashes]), ' / '.join('Resume '+value[:12] for value in hashes) or 'Resume reference unavailable'
    context=record['_context'];key={'company':'company','role':'title','source':'source'}[group_by]
    value=context.get(key)
    value=' '.join(value.split()) if isinstance(value,str) and value.strip() else 'Not recorded'
    basis='captured' if record['_basis'] in {'prepared','submit_intent'} else 'current metadata' if record['_basis']=='current_job_metadata' else 'unavailable'
    return digest([group_by,value.casefold(),basis]),value+(' (current metadata)' if basis=='current metadata' else ' (identity unavailable)' if basis=='unavailable' else '')


def _counts(records):
    result={key:0 for key in METRICS}
    for record in records:
        for key in record['_members']:result[key]+=1
    return result


def report(store,*,group_by='source',lookback=90,window=14,metric='attempts',group_id='',offset=0,group_offset=0,at=None):
    if group_by not in {'company','role','source','resume'} or metric not in METRICS:
        raise ValueError('Choose a supported recruiting comparison and metric')
    if type(lookback) is not int or lookback not in {0,30,90,180,365} or type(window) is not int or window not in {7,14,30}:
        raise ValueError('Choose an application period and a 7, 14 or 30 day observation window')
    if not isinstance(group_id,str) or group_id and not re.fullmatch(r'[a-f0-9]{64}',group_id):
        raise ValueError('Choose an existing comparison group')
    if any(type(value) is not int or not 0<=value<=1000000 for value in (offset,group_offset)):
        raise ValueError('Choose valid insight pages')
    clock=(at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff=clock-timedelta(days=lookback) if lookback else None
    with _snapshot(store):
        records={}
        for row in store.db.execute("""SELECT a.*,j.company AS current_company,j.title AS current_title,
            j.url AS current_url,j.source AS current_source FROM application_records a
            LEFT JOIN jobs j ON j.id=a.job_id WHERE a.attempted IS NOT NULL ORDER BY a.attempted DESC,a.id"""):
            record=dict(row);stamp=_time(record['attempted'])
            # Unplaceable dates remain visible for review, excluded from all rate denominators.
            if stamp is not None and stamp<=clock and cutoff and stamp<cutoff:continue
            if stamp is not None and stamp>clock:stamp=None
            record.update(_at=stamp,_end=stamp+timedelta(days=window) if stamp else None,_clock=clock)
            records[record['id']]=record
        contexts=_contexts(store,records)
        artifacts={row['id']:dict(row) for row in store.db.execute("SELECT id,job_id,kind,hash,filename,revision FROM application_artifacts WHERE kind='resume'")}
        mail=_mail(store,records,clock)
        groups={}
        for aid,record in records.items():
            fallback={key:record['current_'+key] for key in ('company','title','url','source')}
            record['_context'],record['_basis']=contexts.get(aid,(fallback,'current_job_metadata' if record['current_url'] else 'unavailable'))
            record['_mail']=mail[aid];record['_resumes']=_resumes(record,artifacts);record['_members']=_members(record)
            key,label=_group(record,group_by)
            record['_group_id']=key
            group=groups.setdefault(key,{'id':key,'label':label,'records':[]})
            group['records'].append(record)
        if group_id and group_id not in groups:raise ValueError('This comparison group changed. Clear the group filter and refresh.')
        scope=groups[group_id]['records'] if group_id else list(records.values())
        selected=[record for record in scope if metric in record['_members']]
        offset=min(offset,max(0,(len(selected)-1)//25*25))
        items=[]
        for record in selected[offset:offset+25]:
            items.append({'id':record['id'],'job_id':record['job_id'],'hash':record['hash'],'updated':record['updated'],
                'state':record['state'],'screenshot':record['screenshot'],'attempted':record['attempted'],
                'company':record['_context'].get('company') or 'Company unavailable','title':record['_context'].get('title') or 'Role unavailable',
                'source':record['_context'].get('source') or 'Not recorded','context_source':record['_basis'],
                'resumes':record['_resumes'],'age_days':max(0,(clock-record['_at']).days) if record['_at'] else None,
                'mature':'mature' in record['_members'],'counted_feedback':record['_mail']['reviewed'],
                'pending_feedback':record['_mail']['pending'],'group_id':record['_group_id']})
        group_rows=[]
        for group in groups.values():
            counts=_counts(group['records'])
            group_rows.append({'id':group['id'],'label':group['label'],'counts':counts,
                'interview_share':round(counts['interview']/counts['mature']*100,1) if counts['mature'] else None,
                'small_sample':counts['mature']<20})
        group_rows.sort(key=lambda group:(-group['counts']['mature'],group['label'].casefold(),group['id']))
        group_offset=min(group_offset,max(0,(len(group_rows)-1)//20*20))
        return {'as_of':clock.isoformat(timespec='seconds'),'filters':{'group_by':group_by,'lookback':lookback,'window':window,
                'metric':metric,'group_id':group_id},'scope':groups[group_id]['label'] if group_id else 'All groups',
            'metrics':METRICS,'counts':_counts(scope),
            'groups':{'items':group_rows[group_offset:group_offset+20],'total':len(group_rows),'offset':group_offset,'limit':20},
            'applications':{'items':items,'total':len(selected),'offset':offset,'limit':25},
            'quality':{'current_metadata_attempts':sum(r['_basis']=='current_job_metadata' for r in scope),
                       'unavailable_identity_attempts':sum(r['_basis']=='unavailable' for r in scope)},
            'notes':['Milestones and response shares use confirmed submissions old enough for the full observation window.',
                     'Only reviewed messages received within that window count. Missing or unreviewed mail is not rejection.',
                     'Milestones can overlap; an offer does not imply an observed interview. Comparisons are descriptive, not causal.',
                     'Small sample means fewer than 20 mature submissions, a display caution rather than a significance test.',
                     'Unplaceable submission dates are retained for review and excluded from denominators.',
                     'Resume groups identify exact PDF bytes; identical bytes are grouped even when artifact revisions differ.']}
