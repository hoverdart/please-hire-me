import pytest
from hireme.queue_review import prepare_review
from hireme.util import Blocked


def test_preparation_resumes_after_interruption_without_replaying_holds(store,monkeypatch,tmp_path):
    report={'jobs':[{'job_id':'uncertain','status':'held','reason':'duplicate_or_uncertain'},
                    {'job_id':'first','status':'needs_form_preparation'},
                    {'job_id':'second','status':'needs_form_preparation'}]}
    calls=[]
    def cycle(s,repo,**kwargs):
        assert not kwargs['live'] and kwargs['max_attempts']==1
        calls.append(kwargs['job_ids'][0])
        if len(calls)==2:raise Blocked('rollout_stale')
        return {'prepared':1}
    monkeypatch.setattr('hireme.worker.cycle',cycle)
    with pytest.raises(Blocked,match='rollout_stale'):prepare_review(store,tmp_path,report)
    assert report['forms_prepared']==1 and report['forms_pending']==1
    assert 'form_review' not in report['jobs'][2]
    prepare_review(store,tmp_path,report)
    assert calls==['first','second','second']
    assert report['forms_prepared']==2 and report['forms_pending']==0


def test_missing_answers_remain_a_reviewed_hold(store,monkeypatch,tmp_path):
    report={'jobs':[{'job_id':'missing','status':'needs_form_preparation'}]}
    def cycle(*args,**kwargs):raise Blocked('missing_answers')
    monkeypatch.setattr('hireme.worker.cycle',cycle)
    prepare_review(store,tmp_path,report)
    assert report['jobs'][0]['form_review']=={'prepared':False,'reason':'missing_answers'}
    assert report['forms_prepared']==0 and report['forms_pending']==0


def test_explicit_revalidation_reopens_answer_hold_but_preserves_uncertainty(store,job,package,monkeypatch,tmp_path):
    from hireme.job_holds import hold
    hold(store,job,'missing_answers','Saved answer needs rechecking')
    report={'jobs':[{'job_id':job['id'],'status':'needs_form_preparation'}]}
    def cycle(*args,**kwargs):
        assert not store.db.execute('SELECT 1 FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
        return {'prepared':1}
    monkeypatch.setattr('hireme.worker.cycle',cycle)
    prepare_review(store,tmp_path,report)
    hold(store,job,'missing_answers','An old answer hold must not reopen an unknown')
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown','No receipt')
    report={'jobs':[{'job_id':job['id'],'status':'needs_form_preparation'}]}
    def blocked_cycle(*args,**kwargs):
        assert store.db.execute('SELECT 1 FROM job_holds WHERE job_id=?',(job['id'],)).fetchone()
        raise Blocked('duplicate_or_uncertain')
    monkeypatch.setattr('hireme.worker.cycle',blocked_cycle)
    prepare_review(store,tmp_path,report)
    assert report['jobs'][0]['form_review']['reason']=='duplicate_or_uncertain'
