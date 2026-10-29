import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from hireme.ashby_guard import begin, check
from hireme.browser import Browser
from hireme.util import Blocked, digest


@pytest.fixture
def ashby_job(store, job):
    url='https://jobs.ashbyhq.com/acme/11111111-1111-4111-8111-111111111111'
    value={**job,'id':digest(url),'url':url,'host':'jobs.ashbyhq.com'}
    store.upsert_job(value)
    return value


def request():
    return SimpleNamespace(url='https://jobs.ashbyhq.com/api/non-user-graphql',method='POST',
                           post_data='{"private":"answer"}',post_data_buffer=b'{"private":"answer"}')


def route(store):
    class Route:
        request=request()
        called=None
        def continue_(self, **kwargs):
            # A separate connection can already see the intent before transport.
            other=sqlite3.connect(store.path)
            try:self.seen=other.execute("SELECT state FROM remote_draft_steps WHERE step='ashby:attempt'").fetchall()
            finally:other.close()
            self.called=('continue',kwargs)
        def abort(self):self.called=('abort',{})
    return Route()


def browser(store,job):
    b=Browser.__new__(Browser)
    b.store=store;b.current_host='jobs.ashbyhq.com';b.attempted=False;b.ashby_draft=SimpleNamespace(job=job)
    b.write_boundary=SimpleNamespace(package={'private':'approved answer'},check=lambda **kwargs:None)
    b.native_submission=None;b.auth_write=None;b.ashby_submission=None;b.ashby_draft_fence=None;b.denied_write=False;b.aid=None
    return b


def test_first_draft_write_is_committed_before_transport_and_private(store,ashby_job):
    b=browser(store,ashby_job);r=route(store)
    b._continue_route(r,_ashby_draft_write=True)
    assert r.called[0]=='continue' and r.seen==[('intent',)]
    row=store.db.execute('SELECT * FROM remote_draft_steps').fetchone()
    assert row['id']==b.ashby_draft_fence and row['step']=='ashby:attempt'
    assert row['request_hash']==hashlib.sha256(r.request.post_data_buffer).hexdigest()
    assert 'approved answer' not in repr(tuple(row)) and 'private' not in repr(tuple(row))
    again=route(store);b._continue_route(again,_ashby_draft_write=True)
    assert again.called[0]=='continue' and store.db.execute('SELECT COUNT(*) FROM remote_draft_steps').fetchone()[0]==1


def test_upload_override_is_hashed_as_transmitted(store,ashby_job):
    b=browser(store,ashby_job);r=route(store)
    b._continue_route(r,_ashby_draft_write=True,post_data=b'approved binary bytes')
    row=store.db.execute('SELECT request_hash FROM remote_draft_steps').fetchone()
    assert row[0]==hashlib.sha256(b'approved binary bytes').hexdigest()
    assert r.called==('continue',{'post_data':b'approved binary bytes'})


def test_failed_intent_commit_never_continues(store,ashby_job,monkeypatch):
    b=browser(store,ashby_job);r=route(store)
    monkeypatch.setattr(store,'event',lambda *args: (_ for _ in ()).throw(RuntimeError('commit failure')))
    b._continue_route(r,_ashby_draft_write=True)
    assert r.called[0]=='abort' and not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_nested_transaction_never_allows_uncommitted_intent_to_escape(store,ashby_job):
    b=browser(store,ashby_job);r=route(store)
    with store.transaction():b._continue_route(r,_ashby_draft_write=True)
    assert r.called[0]=='abort' and not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_recovery_blocks_same_session_further_writes_and_final_transport(store,ashby_job):
    b=browser(store,ashby_job);first=route(store);b._continue_route(first,_ashby_draft_write=True)
    store.recover()
    row=store.db.execute('SELECT state FROM remote_draft_steps').fetchone()
    assert row[0]=='unknown'
    assert store.db.execute('SELECT reason FROM job_holds WHERE job_id=?',(ashby_job['id'],)).fetchone()[0]=='ashby_draft_uncertain'
    again=route(store);b._continue_route(again,_ashby_draft_write=True)
    assert again.called[0]=='abort'
    b.attempted=True;b.ashby_submission=SimpleNamespace(used=True,request_signature=None);b.post_submit_denial_recorded=False
    final=route(store);b._continue_route(final)
    assert final.called[0]=='abort'
    assert b.post_submit_denial_recorded and b.denied_request['reason']=='ashby_draft_uncertain'
    with pytest.raises(Blocked):check(store,ashby_job['id'])


def test_legacy_failure_blocks_direct_apply_before_navigation(store,ashby_job):
    from hireme.job_holds import hold
    hold(store,ashby_job,'ashby_draft_save_failed','possibly sent','test')
    b=browser(store,ashby_job);b.workday_grant=None;b.host_cache={};b.test_url=None
    with pytest.raises(Blocked,match='ashby_draft_uncertain'):
        b.apply(ashby_job,live=True)


def test_recovery_does_not_overwrite_archived_outcome(store,ashby_job,package):
    proof={**package,'job_id':ashby_job['id'],'url':ashby_job['url']}
    aid=store.prepare(ashby_job,proof)
    store.db.execute("UPDATE applications SET state='unknown' WHERE id=?",(aid,))
    store.db.execute("INSERT INTO archived_applications SELECT *, 'fixture' FROM applications WHERE id=?",(aid,))
    store.db.execute('DELETE FROM applications WHERE id=?',(aid,))
    store.db.execute("UPDATE jobs SET status='unknown',reason='fixture outcome' WHERE id=?",(ashby_job['id'],))
    begin(store,ashby_job,proof,request(),{})
    store.recover()
    row=store.db.execute('SELECT status,reason FROM jobs WHERE id=?',(ashby_job['id'],)).fetchone()
    assert tuple(row)==('unknown','fixture outcome')
    assert store.db.execute('SELECT state FROM archived_applications WHERE id=?',(aid,)).fetchone()[0]=='unknown'


@pytest.mark.parametrize('fails',[False,True])
def test_normal_attempt_exit_closes_only_own_intent_without_claiming_success(store,ashby_job,monkeypatch,fails):
    b=browser(store,ashby_job)
    def run(job,live):
        b.ashby_draft_fence=begin(store,job,{},request(),{})
        if fails:raise Blocked('upload_verification_failed')
        return 'confirmed'
    monkeypatch.setattr(b,'_apply',run)
    if fails:
        with pytest.raises(Blocked):b.apply(ashby_job)
    else:assert b.apply(ashby_job)=='confirmed'
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='unknown'
    from hireme.recovery import worker_status
    assert worker_status(store)['unfinished_drafts']==0
    with pytest.raises(Blocked):check(store,ashby_job['id'])


def test_failed_fence_close_preserves_result_and_leaves_recoverable_intent(store,ashby_job,monkeypatch):
    b=browser(store,ashby_job)
    def run(job,live):
        b.ashby_draft_fence=begin(store,job,{},request(),{})
        return 'confirmed'
    monkeypatch.setattr(b,'_apply',run)
    monkeypatch.setattr('hireme.ashby_guard.finish',lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError('busy')))
    assert b.apply(ashby_job)=='confirmed'
    assert store.db.execute('SELECT state FROM remote_draft_steps').fetchone()[0]=='intent'


@pytest.mark.parametrize('host',['jobs.ashbyhq.com','api.ashbyhq.com','storage.googleapis.com'])
@pytest.mark.parametrize('path',['/upload','/files/file.pdf','/attachments','/documents','/uncacheable_attributes/presigned_fields','/cdn-cgi/challenge-platform/test'])
@pytest.mark.parametrize('body',['{"operationName":"Write","query":"mutation Write { save }"}',
                                 '{"operationName":"Read","query":"query Read { posting { id } }"}'])
def test_ashby_arbitrary_upload_paths_cannot_bypass_reviewed_grants(store,monkeypatch,host,path,body):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com'
    req=SimpleNamespace(url='https://'+host+path,method='POST',post_data=body,post_data_buffer=body.encode())
    actions=[];r=SimpleNamespace(request=req,abort=lambda:actions.append('abort'),continue_=lambda **kw:actions.append('continue'))
    b._route(r)
    assert actions==['abort']
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


@pytest.mark.parametrize('url,method,body',[
    ('https://jobs.ashbyhq.com/api/non-user-graphql','POST','{"query":"query Read { posting { id } }"}'),
    ('https://jobs.ashbyhq.com/upload','GET',''),
    ('https://www.google.com/recaptcha/api2/reload','POST','passive-verification'),
])
def test_ashby_reviewed_reads_and_passive_recaptcha_remain_available(store,monkeypatch,url,method,body):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    b=Browser(store);b.current_host='jobs.ashbyhq.com'
    req=SimpleNamespace(url=url,method=method,post_data=body,post_data_buffer=body.encode())
    actions=[];r=SimpleNamespace(request=req,abort=lambda:actions.append('abort'),continue_=lambda **kw:actions.append('continue'))
    b._route(r)
    assert actions==['continue']
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()
