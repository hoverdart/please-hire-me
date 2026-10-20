import json
import multiprocessing
import socket
import time
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


@pytest.mark.parametrize('width',[320,1440])
def test_human_verification_notice_and_preferences_preserve_drafts(dashboard,width):
    from playwright.sync_api import sync_playwright,expect
    phase=['challenge'];expires=time.time()+300
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def state_response(route):
            response=route.fetch();payload=response.json();payload['worker_running']=True
            payload['worker_mode']='live';payload['settings']['live_enabled']=True
            payload['human_verification']=({'company':'Synthetic & Co <test>','title':'Software Engineer Intern',
                'phase':phase[0],'expires_at':expires} if phase[0] else None)
            payload['summary']['attention_count']=int(phase[0]=='challenge')
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/state*',state_response)
        page.goto(dashboard+'/#token=fixture-capability')
        notice=page.locator('#human-verification-notice')
        expect(notice).to_be_visible();expect(notice).to_contain_text('Synthetic & Co <test>')
        expect(notice).to_contain_text('Don’t open a second application or click Submit again')
        expect(page.locator('#worker-state')).to_have_text('Waiting for human verification')
        expect(page.locator('#metric-attention')).to_have_text('1')
        expect(page.locator('#today-attention')).to_contain_text('Complete verification')
        assert 'No action needed' not in page.locator('#today-attention').inner_text()
        page.locator('#today-attention').get_by_role('button',name='Synthetic & Co <test> · Complete verification').click()
        expect(notice).to_be_focused()
        assert notice.locator('img,script').count()==0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=f'/tmp/hireme-human-wait-{width}.png')
        page.locator('[data-view=settings]').click()
        wait=page.locator('#settings-form [name=human_verification_wait_seconds]')
        wait.fill('60');page.locator('#settings-form button[type=submit]').click()
        expect(page.locator('#notice')).to_contain_text('background')
        expect(wait).to_have_value('60')
        page.locator('#settings-form [name=headless]').uncheck()
        page.locator('#settings-form button[type=submit]').click()
        expect(page.locator('#notice')).to_contain_text('preferences saved')
        wait.fill('90');wait.focus();phase[0]='checking_receipt'
        page.evaluate('refresh()')
        expect(page.locator('#human-verification-title')).to_have_text('Checking this application')
        expect(wait).to_have_value('90');expect(wait).to_be_focused()
        wait.locator('..').screenshot(path=f'/tmp/hireme-human-wait-setting-{width}.png')
        phase[0]=None;page.evaluate('refresh()');expect(notice).to_be_hidden()
        expect(wait).to_have_value('90');expect(wait).to_be_focused()
        assert not errors
        browser.close()
