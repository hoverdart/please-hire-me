import json
import multiprocessing
import socket
from pathlib import Path
import pytest
from hireme.materials import basic_context
from tests.test_dashboard import launch,wait_for_dashboard


@pytest.mark.parametrize('width',[320,1440])
def test_basic_context_save_reload_and_refresh_preserves_unsaved_edits(store,width):
    from playwright.sync_api import sync_playwright,expect
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    process=multiprocessing.Process(target=launch,args=(str(store.root),str(Path.cwd()),port));process.start()
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_dashboard(process,base)
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':900});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/#token=fixture-capability');page.locator('[data-view=profile]').click()
            form=page.locator('#basic-context-form');text=form.locator('textarea')
            expect(form).to_be_visible()
            text.fill('I build manufacturing software at a startup and study computer science.')
            page.evaluate('refresh()');expect(text).to_have_value('I build manufacturing software at a startup and study computer science.')
            form.locator('input[type=checkbox]').check()
            with page.expect_response('**/api/basic-context'):form.locator('button').click()
            expect(page.locator('#notice')).to_contain_text('Basic context saved')
            expect(page.locator('#basic-context-status')).to_contain_text('Saved and approved')
            assert basic_context(store)['confirmed']
            page.reload();page.locator('[data-view=profile]').click()
            expect(text).to_have_value(basic_context(store)['text'])
            text.fill('I have a new unsaved factual context draft at the moment.')
            text.blur();page.evaluate('refresh()')
            expect(text).to_have_value('I have a new unsaved factual context draft at the moment.')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=f'/tmp/hireme-basic-context-{width}.png',full_page=True)
            assert not errors
            browser.close()
    finally:process.terminate();process.join(5)
