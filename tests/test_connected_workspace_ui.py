import json
import multiprocessing
import socket
from pathlib import Path
import pytest
from tests.test_dashboard import launch,wait_for_dashboard
from tests.test_letters import setup_letter
from hireme import platform_connections as pc
from hireme.application_artifacts import generate

@pytest.fixture
def dashboard(store,job):
    store.update_settings({'live_enabled':False})
    pc.import_listing(store,'https://app.joinhandshake.com/jobs/123',job['company'],job['title'],job['location'],job['description'])
    generate(store,job,'introduction','Tell us about your experience building Python software.',setup_letter(store))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    process=multiprocessing.Process(target=launch,args=(str(store.root),str(Path.cwd()),port));process.start()
    base=f'http://127.0.0.1:{port}'
    try:wait_for_dashboard(process,base);yield base
    finally:process.terminate();process.join(5)


@pytest.mark.parametrize('width',[320,1440])
def test_connected_workspace_drafts_focus_filters_and_disabled_capabilities(dashboard,width):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(dashboard+'/#token=fixture-capability')
        expect(page.locator('#today-attention')).to_be_visible()
        expect(page.locator('#worker-strip')).to_be_visible()
        expect(page.locator('#review-queue .metric-top').get_by_text('Needs your attention')).to_be_visible()
        expect(page.locator('.metric-top').get_by_text('Opportunities',exact=True)).to_be_visible()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=f'/tmp/hireme-workspace-today-{width}.png',full_page=True)
        page.locator('[data-view=connections]').click()
        connection=page.locator('[data-connection-id=handshake]')
        expect(connection).to_contain_text('bulk collection')
        expect(connection.get_by_role('button',name='Search now')).to_be_disabled()
        enabled=connection.locator('[name=enabled]');enabled.check();enabled.focus()
        page.evaluate('refresh()');expect(enabled).to_be_checked();expect(enabled).to_be_focused()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=f'/tmp/hireme-workspace-connections-{width}.png',full_page=True)
        page.locator('[data-view=opportunities]').click()
        page.locator('#connection-source').select_option('handshake')
        expect(page.locator('#jobs .opportunity-details')).to_have_count(1)
        page.locator('#jobs .opportunity-details').click()
        expect(page.locator('#job-dialog')).to_be_visible()
        page.locator('#job-writing-panel summary').click()
        draft=page.locator('#artifact-form [name=prompt]');draft.fill('My unsaved narrative prompt for this employer.');draft.focus()
        page.evaluate('refresh()');expect(draft).to_have_value('My unsaved narrative prompt for this employer.');expect(draft).to_be_focused()
        page.evaluate('window.scrollTo(0,0)')
        page.locator('#job-dialog').evaluate('dialog=>dialog.scrollTop=0')
        page.screenshot(path=f'/tmp/hireme-workspace-jobs-{width}.png',full_page=width>900)
        page.locator('#close-job-dialog').click()
        page.locator('[data-view=materials]').click()
        expect(page.locator('#artifact-library textarea')).to_have_count(1)
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=f'/tmp/hireme-workspace-materials-{width}.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('[data-view=opportunities]').click();page.locator('#jobs .opportunity-details').click()
        expect(draft).to_have_value('My unsaved narrative prompt for this employer.')
        assert not errors
        browser.close()


def test_connection_and_artifact_routes_require_token_and_validate_input(dashboard):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        request=p.request.new_context(base_url=dashboard)
        for path in ['/api/connections','/api/artifacts']:
            assert request.get(path).status==403
            assert request.get(path,headers={'X-Hireme-Token':'fixture-capability'}).status==200
        headers={'X-Hireme-Token':'fixture-capability'}
        assert request.post('/api/connections/handshake/configure',headers=headers,data={'enabled':True,'discovery_enabled':True}).status==200
        assert request.post('/api/connections/handshake/discover',headers=headers,data={}).status==409
        for body in [[],{'enabled':1},{'live_verified':True}]:assert request.post('/api/connections/handshake/configure',headers=headers,data=body).status==400
        assert request.post('/api/connections/handshake/import',headers=headers,data={'url':None,'title':'Role','company':'Company'}).status==400
        assert request.post('/api/artifacts/generate',headers=headers,data=[]).status==400
        assert request.get('/api/jobs?source=handshake&destination=native',headers=headers).json()['total']==1
        request.dispose()
