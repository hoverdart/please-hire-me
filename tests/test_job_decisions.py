import json
from pathlib import Path
import pytest
from hireme.util import Blocked,digest
from hireme.worker import cycle


@pytest.mark.parametrize('decision',['manually_applied','skipped'])
def test_decisions_survive_discovery_and_remove_job_from_worker(store,job,decision):
    store.decide_job(job['id'],decision)
    store.upsert_job(job)
    store.block(job['id'],'missing_answers')
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]==decision
    class NeverOpen:
        def __init__(self,*args):raise AssertionError('Excluded jobs must not open browser')
    result=cycle(store,Path('.'),discover=False,browser_factory=NeverOpen)
    assert result['attempts']==0
    store.decide_job(job['id'],'undo')
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='discovered'
    store.check_job_decision(job['id'])


def test_manual_application_counts_company_limits_and_undo_releases_it(store,job):
    store.update_settings({'max_per_company':1})
    store.decide_job(job['id'],'manually_applied')
    another={**job,'id':digest('another'),'url':job['url']+'-another'}
    store.upsert_job(another)
    with pytest.raises(Blocked,match='company_limit'):store._check_budget(another,store.settings())
    store.decide_job(job['id'],'undo')
    store._check_budget(another,store.settings())


def test_skip_does_not_block_other_jobs_at_same_company(store,job):
    store.decide_job(job['id'],'skipped')
    another={**job,'id':digest('another'),'url':job['url']+'-another'}
    store._check_budget(another,store.settings())


def test_decision_during_preparation_prevents_final_submit(store,job,package):
    aid=store.prepare(job,package)
    store.decide_job(job['id'],'skipped')
    with pytest.raises(Blocked,match='skipped'):store.begin_submit(aid)
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]=='prepared'


@pytest.mark.parametrize('outcome',['submitting','unknown','awaiting_verification','confirmed'])
def test_decision_cannot_overwrite_submission_or_uncertainty(store,job,package,outcome):
    aid=store.prepare(job,package);store.begin_submit(aid)
    if outcome!='submitting':store.finish(aid,outcome)
    with pytest.raises(ValueError):store.decide_job(job['id'],'manually_applied')
    with pytest.raises(ValueError):store.decide_job(job['id'],'skipped')
    assert not store.db.execute('SELECT 1 FROM job_decisions').fetchone()


def test_dashboard_manual_actions_and_filters(store,job,tmp_path):
    import multiprocessing,socket,time,urllib.request
    from hireme.server import serve
    from playwright.sync_api import sync_playwright
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    base=f'http://127.0.0.1:{port}'
    process=multiprocessing.Process(target=serve,args=(store.root,Path.cwd(),port),kwargs={'token':'fixture-capability'})
    process.start()
    try:
        for _ in range(50):
            try:urllib.request.urlopen(base).close();break
            except OSError:time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page(viewport={'width':1280,'height':900})
            page.goto(base+'/#token=fixture-capability')
            page.get_by_role('button',name='Applied manually: Acme',exact=False).click()
            page.locator('#jobs .state').filter(has_text='Applied manually').wait_for()
            page.locator('#status-filter').select_option('manually_applied')
            page.screenshot(path=str(tmp_path/'jobs-desktop.png'),full_page=True)
            page.get_by_role('button',name='Undo: Acme',exact=False).click()
            page.locator('#status-filter').select_option('all')
            page.get_by_role('button',name='Don’t apply: Acme',exact=False).click()
            page.locator('#jobs .state').filter(has_text='Don’t apply').wait_for()
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(tmp_path/'jobs-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            page.reload();page.locator('#jobs .state').filter(has_text='Don’t apply').wait_for()
            browser.close()
    finally:process.terminate();process.join(5)


def test_excluded_job_questions_are_hidden_until_undo(store,job):
    store.ask(job['id'],job['host'],'A missing answer',[])
    assert store.snapshot()['questions']
    store.decide_job(job['id'],'skipped')
    assert not store.snapshot()['questions']
    store.decide_job(job['id'],'undo')
    assert store.snapshot()['questions']


def test_manual_decisions_integrate_with_complete_queues_filters_and_saved_views(store, job):
    from hireme.ledger import summary, search_jobs
    from hireme.question_ledger import search_questions
    from hireme.saved_views import change_view
    store.ask(job['id'],job['host'],'A missing personal answer',[])
    assert search_questions(store)['total']==1 and summary(store)['attention_count']==1
    store.decide_job(job['id'],'manually_applied')
    assert search_questions(store)['total']==0 and summary(store)['question_count']==0
    assert summary(store)['attention_count']==0
    result=search_jobs(store,status='manually_applied')
    assert result['total']==1 and result['jobs'][0]['status_label']=='Applied manually'
    view=change_view(store,{'action':'save','name':'Handled elsewhere','status':'manually_applied'})['views'][0]
    assert view['status']=='manually_applied'
    store.decide_job(job['id'],'undo')
    assert search_questions(store)['total']==1 and summary(store)['attention_count']==1
    store.decide_job(job['id'],'skipped')
    result=search_jobs(store,status='skipped')
    assert result['total']==1 and result['jobs'][0]['status_label']=='Don’t apply'
    assert summary(store)['attention_count']==0
