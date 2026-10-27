import json
import multiprocessing
from pathlib import Path
import socket
from types import SimpleNamespace

from tests.dashboard_evidence import open_fixture_dashboard,open_view
from tests.test_dashboard import launch,wait_for_dashboard,assert_layout_fits_viewport
from tests.test_prior_applications import Mailbox
from hireme.util import atomic_json,Blocked


def launch_mail_fixture(root,repo,port,job):
    from hireme import prior_applications
    mailbox=Mailbox(job)
    prior_applications.GmailClient=lambda _:SimpleNamespace(email='test@candidate.invalid',service=mailbox)
    launch(root,repo,port)


def test_email_preferences_scan_and_exact_role_review_on_desktop_and_mobile(store,job,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    atomic_json(store.root/'integrations/gmail-account.json',{'email':'test@candidate.invalid'})
    atomic_json(store.root/'integrations/gmail-token.json',{})
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    process=multiprocessing.Process(target=launch_mail_fixture,args=(str(store.root),str(Path(__file__).parent.parent),port,job));process.start()
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process,base)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            open_fixture_dashboard(page,base);open_view(page,'connections')
            page.locator('#mail-settings-form [name=gmail_prior_applications]').check()
            page.locator('#mail-settings-form [name=gmail_feedback]').check()
            page.get_by_role('button',name='Save email preferences',exact=True).click()
            expect(page.locator('#notice')).to_contain_text('Email preferences saved')
            assert store.settings()['gmail_prior_applications'] and store.settings()['gmail_feedback']
            panel=page.locator('#prior-applications-panel');panel.locator('summary').click()
            expect(panel.locator('#prior-list')).to_contain_text('No past application emails')
            panel.locator('#prior-company').fill('Acme')
            panel.get_by_role('button',name='Scan application mail',exact=True).click()
            expect(panel.locator('#prior-status')).to_contain_text('This scan is complete')
            expect(panel.locator('#prior-list article')).to_have_count(2)
            first=panel.locator('#prior-list article').first
            first.locator('select[name=job_id]').select_option(job['id'])
            for width in (1440,390):
                page.set_viewport_size({'width':width,'height':1000 if width==1440 else 844})
                assert_layout_fits_viewport(page)
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(tmp_path/f'prior-applications-{width}.png'),full_page=True)
            expect(first.locator('.feedback-attempt')).to_contain_text(job['url'])
            expect(first.locator('.feedback-attempt')).to_contain_text(job['title'])
            first.get_by_role('button',name='Record past application',exact=True).click()
            expect(panel.locator('#prior-status')).to_contain_text('Past application recorded')
            row=store.db.execute("SELECT * FROM prior_application_mail WHERE state='confirmed'").fetchone()
            assert row['job_id']==job['id'] and not store.db.execute('SELECT 1 FROM applications').fetchone()
            assert not errors
            browser.close()
    finally:process.terminate();process.join(timeout=10)
