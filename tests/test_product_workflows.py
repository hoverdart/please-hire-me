"""Synthetic end-to-end product evidence; no employer, inbox or model connections."""
import multiprocessing
import json
import socket
import time
from pathlib import Path

import pytest

from hireme import recruiting_feedback
from hireme.util import atomic_json
from tests.test_dashboard import launch,wait_for_dashboard
from tests.test_recruiting_feedback import Mailbox,message


def launch_workflow_fixture(root,repo,port,job):
    from types import SimpleNamespace
    from hireme import recruiting_feedback
    recruiting_feedback.GmailClient=lambda store:SimpleNamespace(email='test@candidate.invalid',service=Mailbox(job))
    launch(root,repo,port)


@pytest.mark.parametrize('open_before_release',[False,True])
@pytest.mark.parametrize('late_script',[False,True])
def test_feedback_initialization_does_not_depend_on_snapshot_script_order(store,job,package,open_before_release,late_script):
    from playwright.sync_api import sync_playwright,expect
    from tests.test_dashboard import pause_first_request
    application=store.prepare(job,package);store.begin_submit(application);store.finish(application,'unknown')
    store.update_settings({'live_enabled':False,'gmail_feedback':True})
    atomic_json(store.root/'integrations/gmail-token.json',{'token':'synthetic-fixture'})
    atomic_json(store.root/'integrations/gmail-account.json',{'email':'test@candidate.invalid'})
    recruiting_feedback.ingest(store,message(job,identity='startup-mail'))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    process=multiprocessing.Process(target=launch_workflow_fixture,args=(str(store.root),str(Path.cwd()),port,job));process.start()
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process,base)
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page();pending=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            pause_first_request(page,'**/recruiting.js' if late_script else '**/api/state**',pending)
            page.goto(base+'/#token=fixture-capability',wait_until='commit')
            if late_script:expect(page.locator('#ledger-count')).to_have_text('1')
            else:page.wait_for_load_state('domcontentloaded')
            # DOMContentLoaded does not wait for the async snapshot request or
            # dispatch its Playwright route callback. Wait for our actual pause.
            deadline=time.monotonic()+5
            while not pending and time.monotonic()<deadline:page.wait_for_timeout(10)
            assert len(pending)==1
            page.locator('[data-view=connections]').first.click()
            if open_before_release:page.locator('#recruiting-feedback-panel > summary').click()
            pending[0].continue_()
            # Releasing either startup dependency must work without another poll.
            expect(page.locator('#feedback-sync')).to_be_enabled()
            if not open_before_release:page.locator('#recruiting-feedback-panel > summary').click()
            expect(page.locator('#feedback-list')).to_contain_text('Interview invitation')
            assert not errors and store.application_record(application)['state']=='unknown'
            assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
            browser.close()
    finally:process.terminate();process.join(5)


@pytest.mark.parametrize('width',[320,1440])
def test_tailored_resume_and_recruiting_review_workflow(store,job,package,tmp_path,width):
    from playwright.sync_api import sync_playwright,expect
    application=store.prepare(job,package);store.begin_submit(application);store.finish(application,'unknown')
    store.update_settings({'live_enabled':False,'tailored_resumes':True,'gmail_feedback':True})
    store.put_history_entry('employment',{'organization':'Previous Employer','title':'Software Intern',
        'start':'2025-06','end':'2025-08','description':'Built Python services for scheduling.\nImplemented TypeScript interfaces for production teams.'},source='Applicant checked employment record',confirmed=True)
    store.put_history_entry('education',{'institution':'Example University','degree':'B.S.','field_of_study':'Computer Science','start':'2024-08','expected_end':'2028-05'},source='Applicant checked enrollment record',confirmed=True)
    atomic_json(store.root/'integrations/gmail-token.json',{'token':'synthetic-fixture'})
    atomic_json(store.root/'integrations/gmail-account.json',{'email':'test@candidate.invalid'})
    recruiting_feedback.ingest(store,message(job,identity='original-mail'))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    process=multiprocessing.Process(target=launch_workflow_fixture,args=(str(store.root),str(Path.cwd()),port,job));process.start()
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process,base)
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/#token=fixture-capability')
            expect(page.locator('#application-outcomes')).to_contain_text('Uncertain')
            assert page.locator('#application-outcomes dd').all_text_contents()[2]=='1'
            screenshots=Path('output/autoapply-comparison');screenshots.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(screenshots/f'today-{width}.png'),full_page=True)
            page.locator('[data-view=opportunities]').first.click()
            assert page.request.get(base+'/api/application-journey/'+application).status==403
            evidence=page.locator(f'#jobs details[data-evidence-id="{application}"]')
            evidence.locator(':scope > summary').click()
            evidence.get_by_role('button',name='Open application record',exact=True).click()
            record=page.locator('#job-record-evidence .application-journey')
            expect(record).to_contain_text('Verify the existing submission')
            expect(record).to_contain_text('No reviewed response')
            if width==320:
                assert record.locator('h3').bounding_box()['y']>=page.locator('#job-dialog .dialog-head').bounding_box()['height']
            with page.expect_download() as exporting:
                record.get_by_role('button',name='Download application record').click()
            manifest=tmp_path/'application-record.json';exporting.value.save_as(manifest)
            exported=json.loads(manifest.read_text())
            assert exported['application_id']==application and exported['submission']['state']=='unknown'
            assert exported['posting_context_source']=='submit_intent' and not exported['reviewed_feedback']
            assert exported['documents'][0]['hash']==package['documents'][0]['hash']
            record.get_by_role('button',name='Review unresolved submissions').click()
            expect(page.locator('#questions')).to_be_visible()
            expect(page.locator('#job-dialog')).not_to_have_attribute('open','')
            page.locator('[data-view=opportunities]').first.click()
            evidence.locator(':scope > summary').click()
            page.locator('#jobs [data-job-detail]').first.click() if page.locator('#jobs [data-job-detail]').count() else page.get_by_role('button',name='View details').first.click()
            page.locator('#job-writing-panel > summary').click()
            form=page.locator('#artifact-form');form.locator('[name=kind]').select_option('resume')
            expect(form.locator('[name=prompt]')).to_be_hidden()
            with page.expect_response(lambda response:response.url==base+'/api/artifacts/generate' and response.request.method=='POST') as generation:
                form.get_by_role('button',name='Generate material').click()
            assert generation.value.status==200
            download_button=page.get_by_role('button',name='Download tailored resume').first
            expect(download_button).to_be_visible()
            expect(page.locator('#job-artifacts')).to_contain_text('Acme')
            expect(page.locator('#job-artifacts')).not_to_contain_text('No materials generated')
            with page.expect_download() as downloading:download_button.click()
            downloaded=downloading.value;output=tmp_path/'tailored-resume.pdf';downloaded.save_as(output)
            assert output.read_bytes().startswith(b'%PDF-')
            # One bounded visual evidence round, both device classes, outside source.
            screenshots=Path('output/autoapply-comparison');screenshots.mkdir(parents=True,exist_ok=True)
            if width==1440:(screenshots/'synthetic-tailored-resume.pdf').write_bytes(output.read_bytes())
            page.screenshot(path=str(screenshots/f'materials-{width}.png'),full_page=True)
            page.locator('#job-writing-panel').screenshot(path=str(screenshots/f'materials-detail-{width}.png'))
            if width==320:page.locator('#close-job-dialog').click() if page.locator('#close-job-dialog').count() else page.keyboard.press('Escape')
            page.locator('[data-view=connections]').first.click()
            page.locator('#recruiting-feedback-panel > summary').click()
            expect(page.locator('#feedback-list')).to_contain_text('Interview invitation')
            card=page.locator('#feedback-list article').first
            card.get_by_label('Application attempt').select_option(application)
            expect(card.locator('.feedback-attempt')).to_contain_text(application)
            expect(card.locator('.feedback-attempt')).to_contain_text('unknown')
            card.get_by_role('button',name='Confirm feedback').click()
            expect(page.locator('#feedback-status')).to_contain_text('original submission outcome is unchanged')
            page.locator('#feedback-filter').select_option('confirmed')
            expect(page.locator('#feedback-list')).to_contain_text('Update confirmed feedback')
            page.screenshot(path=str(screenshots/f'feedback-{width}.png'),full_page=True)
            page.locator('#recruiting-feedback-panel').screenshot(path=str(screenshots/f'feedback-detail-{width}.png'))
            page.locator('#feedback-sync').click()
            expect(page.locator('#feedback-status')).to_contain_text('1 new messages saved')
            page.locator('[data-view=opportunities]').first.click()
            if evidence.evaluate('(node)=>node.open'):evidence.locator(':scope > summary').click()
            evidence.locator(':scope > summary').click()
            evidence.get_by_role('button',name='Open application record',exact=True).click()
            expect(record).to_contain_text('Recruiting stage: interview')
            expect(record).to_contain_text('Submission: unknown')
            record.get_by_text('Application timeline',exact=True).click()
            expect(record).to_contain_text('Reviewed interview')
            journey_screenshots=Path('output/feedback-loop');journey_screenshots.mkdir(parents=True,exist_ok=True)
            # A taller capture canvas fits this long mobile record below the sticky header;
            # the interaction checks above still run at the original 320x1000 viewport.
            if width==320:
                page.set_viewport_size({'width':width,'height':1600})
                record.evaluate('''node=>{const dialog=node.closest('dialog');dialog.scrollTop+=node.getBoundingClientRect().top-dialog.querySelector('.dialog-head').getBoundingClientRect().bottom-12;}''')
            page.screenshot(path=str(journey_screenshots/f'workspace-{width}.png'),full_page=True)
            record.screenshot(path=str(journey_screenshots/f'application-record-{width}.png'))
            assert store.application_record(application)['state']=='unknown'
            assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
            assert not errors and page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            browser.close()
    finally:process.terminate();process.join(5)


def test_application_outcomes_use_complete_history_and_keep_email_feedback_separate(store,job,package):
    from hireme.coverage import application_outcomes
    application=store.prepare(job,package);store.begin_submit(application);store.finish(application,'unknown')
    recruiting_feedback.ingest(store,message(job))
    result=application_outcomes(store)
    assert result['final_intents']==result['uncertain']==1 and result['confirmed']==0
    row=recruiting_feedback.listing(store)['messages'][0]
    recruiting_feedback.review(store,{'id':row['id'],'revision':1,'state':'confirmed','category':'offer','application_id':application})
    assert application_outcomes(store)==result
