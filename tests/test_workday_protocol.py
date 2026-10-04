import pytest
from hireme.workday import WriteGrant
from hireme.util import Blocked,digest

@pytest.fixture
def workday_job(store,job):
    j={**job,'host':'nvidia.wd5.myworkdayjobs.com','company':'Nvidia','url':'https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/fixture'}
    j['id']=digest(j['url'])
    store.upsert_job(j)
    return j

def grant(store,job,**kw):
    return WriteGrant(store,job,'My Information','https://'+job['host']+'/fixture/save','POST',b'{"email":"test@candidate.invalid"}',**kw)

def test_permission_is_separate_from_live_submission(store,workday_job):
    with pytest.raises(Blocked,match='remote_draft_permission'):grant(store,workday_job)
    store.update_settings({'live_enabled':False,'remote_drafts':True})
    g=grant(store,workday_job)
    assert g.consume(g.url,'POST',g.payload)
    assert store.db.execute('SELECT COUNT(*) FROM applications').fetchone()[0]==0

def test_exact_one_use_payload_and_ack(store,workday_job):
    store.update_settings({'remote_drafts':True})
    g=grant(store,workday_job)
    assert not g.consume(g.url,'POST',b'changed')
    assert not g.consume(g.url.replace('nvidia','intel'),'POST',g.payload)
    assert g.consume(g.url,'POST',g.payload)
    assert not g.consume(g.url,'POST',g.payload)
    g.acknowledge('draft-version-1')
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='acknowledged'

def test_sources_revocation_and_unknown_step_hold(store,workday_job):
    store.update_settings({'remote_drafts':True})
    g=grant(store,workday_job)
    store.put_facts({'first_name':'Changed'})
    with pytest.raises(Blocked,match='stale_answer'):g.consume(g.url,'POST',g.payload)
    g=grant(store,workday_job)
    assert g.consume(g.url,'POST',g.payload)
    g.uncertain()
    with pytest.raises(Blocked,match='remote_draft_uncertain'):grant(store,workday_job)

def test_parallel_grants_cannot_duplicate_unacknowledged_write(store,workday_job):
    store.update_settings({'remote_drafts':True})
    first,second=grant(store,workday_job),grant(store,workday_job)
    assert first.consume(first.url,'POST',first.payload)
    with pytest.raises(Blocked,match='remote_draft_uncertain'):second.consume(second.url,'POST',second.payload)

def test_final_submit_requires_reserved_intent(store,workday_job):
    store.update_settings({'remote_drafts':True})
    with pytest.raises(Blocked,match='workday_submit_not_reserved'):grant(store,workday_job,final=True)

def test_unknown_operation_never_granted(store,workday_job):
    store.update_settings({'remote_drafts':True})
    with pytest.raises(Blocked,match='workday_unapproved_operation'):
        WriteGrant(store,workday_job,'My Information','https://attacker.invalid/save','POST',b'{}')


def test_unknown_authenticated_transport_never_grants_write(store,workday_job):
    from types import SimpleNamespace
    from hireme.workday import WorkdayAdapter
    b=SimpleNamespace(store=store,_snapshot=lambda:[],_verify=lambda *a:None)
    adapter=WorkdayAdapter(b,workday_job)
    snapshot=adapter.snapshot('My Information')
    with pytest.raises(Blocked,match='multi_step_requires_adapter'):
        adapter.authorize_step(snapshot,'https://'+workday_job['host']+'/save','POST',b'{}',[],[])

def test_workday_questions_stop_before_filling_or_writes(store,workday_job):
    from types import SimpleNamespace
    from hireme.workday import WorkdayAdapter
    f={'label':'Unconfirmed employer-specific certification','required':True,'type':'text','options':[],'maxlength':-1}
    b=SimpleNamespace(store=store,_snapshot=lambda:[f])
    adapter=WorkdayAdapter(b,workday_job)
    with pytest.raises(Blocked,match='missing_answers'):adapter.resolve_step(adapter.snapshot('Application Questions 1 of 2'))
    assert store.db.execute('SELECT count(*) FROM remote_draft_steps').fetchone()[0]==0
    assert store.db.execute('SELECT count(*) FROM questions WHERE resolved=0').fetchone()[0]==1
