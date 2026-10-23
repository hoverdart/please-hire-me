import json
from datetime import datetime,timedelta,timezone

import pytest

from hireme import recruiting_feedback
from hireme.application_journey import capture_context
from hireme.recruiting_insights import report,METRICS
from hireme.util import digest
from tests.test_recruiting_feedback import message
from tests.test_history_api import history_server,request


def application(store,job,package,index,age=30,state='confirmed',clock=None,source='fixture-source',captured=True):
    clock=clock or datetime.now(timezone.utc).replace(microsecond=0)
    url=job['url']+'-'+str(index);candidate={**job,'id':digest(url),'url':url,'source':source}
    store.upsert_job(candidate)
    proof={**package,'job_id':candidate['id'],'url':url};checksum=digest(proof);aid=digest(['insight-fixture',index])
    stamp=(clock-timedelta(days=age)).isoformat(timespec='seconds')
    store.db.execute('''INSERT INTO applications(id,job_id,company_key,state,package,hash,created,updated,attempted,confirmation,screenshot)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(aid,candidate['id'],store.company(candidate['company']),state,json.dumps(proof),checksum,stamp,stamp,stamp,'Synthetic fixture',''))
    if captured:store.event('submit_intent',aid,{'hash':checksum,'job_context':capture_context(store,candidate)})
    return aid,candidate,stamp


def mail(store,candidate,aid,attempted,category='interview',after=2,confirmed=True,identity='mail'):
    bodies={'interview':'Please schedule your interview.','assessment':'Please complete our coding assessment.',
            'offer':'We are pleased to offer you this position.','rejection':'We will not be moving forward.',
            'acknowledgement':'We received your application.'}
    data=message(candidate,body=bodies[category]+' '+candidate['url'],identity=identity)
    received=datetime.fromisoformat(attempted)+timedelta(days=after)
    data['internalDate']=str(int(received.timestamp()*1000))
    assert recruiting_feedback.ingest(store,data)
    if confirmed:
        row=next(row for row in recruiting_feedback.listing(store)['messages'] if row['message_id']==identity)
        recruiting_feedback.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed','category':category,'application_id':aid})


@pytest.fixture
def cohort(store,job,package):
    clock=datetime.now(timezone.utc).replace(microsecond=0)
    rows=[application(store,job,package,index,age,state,clock,source='source-'+str(index%2))
          for index,(age,state) in enumerate([(40,'confirmed'),(25,'confirmed'),(20,'confirmed'),(2,'confirmed'),(30,'unknown'),(30,'not_submitted')])]
    for index,category,after,confirmed in [(0,'assessment',1,True),(0,'interview',3,True),(0,'offer',20,True),
        (1,'offer',5,True),(2,'acknowledgement',2,True),(2,'interview',3,False),(3,'interview',1,True),
        (4,'interview',2,True),(5,'rejection',2,True)]:
        aid,candidate,stamp=rows[index];mail(store,candidate,aid,stamp,category,after,confirmed,'m-'+str(index)+'-'+category)
    return clock,rows


def test_counts_use_mature_confirmed_submissions_and_observed_window_milestones(store,cohort):
    clock,rows=cohort;before=store.db.total_changes;data=report(store,at=clock)
    counts=data['counts']
    assert {key:counts[key] for key in ('attempts','confirmed','mature','recent','uncertain','not_submitted')}=={
        'attempts':6,'confirmed':4,'mature':3,'recent':1,'uncertain':1,'not_submitted':1}
    assert {key:counts[key] for key in ('responded','assessment','interview','offer','rejection','reviewed_mail','pending_review','awaiting')}=={
        'responded':2,'assessment':1,'interview':1,'offer':1,'rejection':0,'reviewed_mail':3,'pending_review':1,'awaiting':1}
    assert store.db.total_changes==before and not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    assert 'test@candidate.invalid' not in json.dumps(data) and 'provenance' not in json.dumps(data)


def test_every_metric_and_group_count_drills_to_exact_matching_attempts(store,cohort):
    clock,rows=cohort;data=report(store,at=clock)
    for metric in METRICS:
        result=report(store,metric=metric,at=clock)
        assert result['applications']['total']==data['counts'][metric]
    for group in data['groups']['items']:
        result=report(store,group_id=group['id'],metric='interview',at=clock)
        assert result['applications']['total']==group['counts']['interview']
    interviews=report(store,metric='interview',at=clock)['applications']['items']
    assert [row['id'] for row in interviews]==[rows[0][0]]
    assert {'assessment','interview'}=={item['category'] for item in interviews[0]['counted_feedback']}


def test_recent_and_uncertain_outcomes_do_not_enter_rates_or_imply_rejection(store,cohort):
    clock,rows=cohort
    assert report(store,metric='recent',at=clock)['applications']['items'][0]['id']==rows[3][0]
    assert report(store,metric='uncertain',at=clock)['applications']['items'][0]['id']==rows[4][0]
    assert report(store,metric='awaiting',at=clock)['applications']['items'][0]['id']==rows[2][0]
    all_time=report(store,window=30,at=clock)
    assert all_time['counts']['mature']==1 and all_time['counts']['offer']==1
    assert store.application_record(rows[4][0])['state']=='unknown'


def test_offer_does_not_invent_an_interview_and_pending_match_is_not_a_response(store,job,package):
    aid,candidate,stamp=application(store,job,package,0)
    mail(store,candidate,aid,stamp,'offer',identity='offer')
    mail(store,candidate,aid,stamp,'interview',confirmed=False,identity='pending')
    data=report(store)
    assert data['counts']['offer']==1 and data['counts']['interview']==0 and data['counts']['pending_review']==1


def test_full_window_and_received_boundary_are_inclusive_without_counting_late_mail(store,job,package):
    clock=datetime.now(timezone.utc).replace(microsecond=0)
    first,candidate,stamp=application(store,job,package,0,age=14,clock=clock)
    # The same instant expressed in another timezone has the same exposure.
    store.db.execute('UPDATE applications SET attempted=? WHERE id=?',
        (datetime.fromisoformat(stamp).astimezone(timezone(timedelta(hours=-7))).isoformat(),first))
    mail(store,candidate,first,stamp,'interview',after=14,identity='boundary')
    second,other,stamp=application(store,job,package,1,age=20,clock=clock)
    mail(store,other,second,stamp,'interview',after=14.0001,identity='late')
    data=report(store,at=clock)
    assert data['counts']['mature']==2 and data['counts']['interview']==1
    assert report(store,metric='interview',at=clock)['applications']['items'][0]['id']==first


def test_captured_identity_survives_refresh_and_fallback_groups_are_separate(store,job,package):
    aid,candidate,_=application(store,job,package,0,source='original')
    store.upsert_job({**candidate,'company':'Renamed Company','source':'changed'})
    application(store,job,package,1,source='original',captured=False)
    data=report(store)
    assert len(data['groups']['items'])==2 and data['counts']['current_metadata']==1
    row=next(item for item in data['applications']['items'] if item['id']==aid)
    assert row['source']=='original' and row['company']=='Acme'
    store.db.execute('DELETE FROM jobs WHERE id=?',(candidate['id'],))
    assert report(store)['counts']['attempts']==2


def test_old_history_archives_and_large_pages_are_counted_without_snapshot_caps(store,job,package):
    from hireme.store import APPLICATION_COLUMNS
    for index in range(503):application(store,job,package,index,source='source-'+str(index))
    first=store.db.execute('SELECT id FROM applications LIMIT 1').fetchone()[0]
    store.db.execute(f"INSERT INTO archived_applications SELECT {APPLICATION_COLUMNS},'fixture' FROM applications WHERE id=?",(first,))
    store.db.execute('DELETE FROM applications WHERE id=?',(first,))
    data=report(store,offset=999,group_offset=999)
    assert data['counts']['attempts']==data['groups']['total']==503
    assert data['applications']['offset']==500 and len(data['applications']['items'])==3
    assert data['groups']['offset']==500 and len(data['groups']['items'])==3


def test_resume_groups_use_recorded_bytes_and_never_guess_a_revision(store,job,package):
    aid,_,_=application(store,job,package,0)
    data=report(store,group_by='resume')
    assert data['applications']['items'][0]['resumes']==[{'hash':package['documents'][0]['hash'],'revision':None,'artifact_id':None}]
    assert data['groups']['items'][0]['counts']['mature']==1
    store.db.execute('UPDATE applications SET package=? WHERE id=?',('{}',aid))
    assert report(store,group_by='resume')['groups']['items'][0]['label']=='Resume reference unavailable'


def test_other_owner_dismissed_and_out_of_window_mail_never_enters_response_counts(store,cohort):
    clock,_=cohort
    store.db.execute("UPDATE recruiting_feedback SET owner_hash='another-owner'")
    data=report(store,at=clock)
    assert data['counts']['responded']==data['counts']['rejection']==data['counts']['pending_review']==0
    assert data['counts']['awaiting']==data['counts']['mature']


def test_unavailable_dates_and_zero_denominators_are_explicit(store,job,package):
    aid,_,_=application(store,job,package,0)
    store.db.execute('UPDATE applications SET attempted=? WHERE id=?',('not-a-date',aid))
    data=report(store)
    assert data['counts']['confirmed']==data['counts']['timing_unavailable']==1
    assert data['counts']['mature']==0 and data['groups']['items'][0]['interview_share'] is None
    assert data['applications']['items'][0]['age_days'] is None


def test_lookback_excludes_old_valid_attempts_and_prepared_drafts(store,job,package):
    application(store,job,package,0,age=120)
    store.prepare(job,package)
    assert report(store)['counts']['attempts']==0
    assert report(store,lookback=0)['counts']['attempts']==1


@pytest.mark.parametrize('kwargs',[{'lookback':True},{'lookback':12},{'window':0},{'window':True},{'offset':-1},
    {'group_offset':True},{'group_id':'bad'},{'group_by':'secret'},{'metric':'private_answers'}])
def test_invalid_filters_are_rejected(store,kwargs):
    with pytest.raises(ValueError):report(store,**kwargs)


def test_insights_api_requires_authentication_and_rejects_bad_filters(store,cohort,history_server):
    assert request(history_server,'/api/recruiting-insights',headers={'X-Hireme-Token':''})[0]==403
    assert request(history_server,'/api/recruiting-insights?window=0')[0]==400
    status,data=request(history_server,'/api/recruiting-insights?metric=interview')
    assert status==200 and data['applications']['total']==1
