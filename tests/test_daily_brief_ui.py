"""Navigation and notification behavior on an isolated, paused applicant fixture."""
from pathlib import Path
import pytest
from tests.test_connected_workspace_ui import dashboard
from tests.dashboard_evidence import open_view


@pytest.mark.parametrize('width', [320, 1440])
def test_daily_brief_routes_and_notifications_preserve_work(dashboard, width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':width,'height':1000})
        errors=[];page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(dashboard+'/#token=fixture-capability')
        expect(page.locator('#metric-opportunities')).to_have_text('2')
        expect(page).to_have_title('HireMeMaybe · Your job search')
        expect(page.locator('#today .ledger-panel')).to_have_count(0)
        expect(page.locator('#today-attention')).to_be_visible()
        expect(page.locator('#brief-activity')).to_be_visible()
        open_view(page, 'opportunities')
        page.locator('#job-search').fill('Acme')
        open_view(page, 'today');open_view(page, 'opportunities')
        expect(page.locator('#job-search')).to_have_value('Acme')
        open_view(page, 'applications')
        expect(page.locator('#application-outcomes')).to_be_visible()
        page.locator('#run-history-panel > summary').click()
        expect(page.locator('#run-history-page')).to_contain_text('recorded batches')
        open_view(page, 'materials');expect(page.locator('#materials')).to_be_visible()
        open_view(page, 'connections')
        expect(page.locator('#gmail-state')).to_be_visible()
        expect(page.locator('#optional-connections')).not_to_have_attribute('open', '')
        assert page.locator('#connections').evaluate('(e) => e.querySelector(".section").querySelector("h2").textContent') == 'Gmail'
        open_view(page, 'settings')
        field=page.locator('#settings-form [name=locations]');field.fill('Unsaved notification test');field.focus()
        page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        position=page.evaluate('scrollY')
        page.evaluate('note("Connection updated <img src=x onerror=alert(1)>")')
        expect(page.locator('#toast')).to_be_visible()
        assert page.evaluate('document.querySelector(".brand").getBoundingClientRect().right <= document.querySelector("#find-tool").getBoundingClientRect().left')
        expect(page.locator('#notice')).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator('#notice img')).to_have_count(0)
        expect(field).to_be_focused();expect(field).to_have_value('Unsaved notification test')
        assert abs(page.evaluate('scrollY')-position)<2
        assert page.locator('#toast').evaluate('(e) => {const r=e.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth}')
        page.locator('#dismiss-notification').click()
        expect(page.locator('#toast')).to_be_hidden()
        page.locator('#find-tool').click()
        page.evaluate('note("Connection failed. Try again.", true)')
        # Top-layer notification stays above the open native modal.
        assert page.locator('#toast').evaluate('(e) => {const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.left+20,r.top+20))}')
        page.locator('#dismiss-notification').click()
        page.keyboard.press('Escape')
        page.locator('#open-notifications').click()
        expect(page.locator('#notification-history')).to_contain_text('Connection failed. Try again.')
        expect(page.locator('#notification-history')).to_contain_text('<img src=x onerror=alert(1)>')
        page.locator('#close-notifications').click()
        expect(page.locator('#open-notifications')).to_be_focused()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
        browser.close()


def test_notification_lifetime_pauses_for_reading_and_errors_persist(dashboard):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page()
        page.clock.install();page.goto(dashboard+'/#token=fixture-capability')
        expect(page.locator('#metric-opportunities')).to_have_text('2')
        page.evaluate('note("Saved successfully")')
        page.locator('#toast').hover();page.clock.fast_forward(10000)
        expect(page.locator('#toast')).to_be_visible()
        page.mouse.move(0,0);page.clock.fast_forward(6600)
        expect(page.locator('#toast')).to_be_hidden()
        page.evaluate('note("Read with the keyboard")')
        page.locator('#dismiss-notification').focus();page.clock.fast_forward(10000)
        expect(page.locator('#toast')).to_be_visible()
        page.locator('#find-tool').focus();page.clock.fast_forward(6600)
        expect(page.locator('#toast')).to_be_hidden()
        page.evaluate('note("Persistent failure", true)');page.clock.fast_forward(60000)
        expect(page.locator('#toast')).to_be_visible();expect(page.locator('#notice')).to_have_attribute('role','alert')
        browser.close()


@pytest.mark.parametrize('width', [320, 768, 1024, 1440])
def test_batch_labels_and_settings_headings_fit_without_clipping(dashboard, width):
    """Text must fit its own box, even when the document has no overflow."""
    import json
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000})
        errors=[];page.on('pageerror', lambda error:errors.append(str(error)))
        def response(route):
            payload=route.fetch().json()
            payload['cycle_funnel']={'mode':'live','status':'paused','run_id':'synthetic-batch-20fbf154',
                'discovered':None,'eligible':None,'attempted':0,'prepared':0,'confirmed':0,'blocked':0,'uncertain':0}
            payload['runs']=[{'id':'synthetic-batch-20fbf154','started':'2026-10-25T16:39:00+00:00',
                'status':'paused','submitted':0,'detail':json.dumps({'attempts':0,'reason':'paused'})}]
            route.fulfill(status=200,json=payload)
        page.route('**/api/state*',response)
        page.goto(dashboard+'/#token=fixture-capability')
        expect(page.locator('#metric-opportunities')).to_have_text('2')
        open_view(page,'applications')
        expect(page.locator('#cycle-funnel-status')).to_have_text('Applications · Paused')
        assert 'synthetic-batch-' not in page.locator('#cycle-funnel-status').inner_text()
        page.locator('#cycle-details > summary').click()
        expect(page.locator('#cycle-record')).to_have_text('Batch ID: synthetic-batch-20fbf154')
        assert page.locator('#cycle-funnel').evaluate('''parent => [...parent.querySelectorAll('dt,dd')].every(e => {
            const box=e.parentElement.getBoundingClientRect(), range=document.createRange();range.selectNodeContents(e);
            return [...range.getClientRects()].every(r=>r.left>=box.left-1&&r.right<=box.right+1);
        })''')
        funnel=page.locator('[aria-labelledby=cycle-funnel-title]').bounding_box()
        heading=page.get_by_role('heading',name='Recent batches',exact=True).bounding_box()
        assert heading['y']>=funnel['y']+funnel['height']+16
        expect(page.locator('#runs')).not_to_contain_text('attempted. paused')
        destination=Path('output/hirememaybe-layout-repair');destination.mkdir(parents=True,exist_ok=True)
        page.evaluate('scrollTo(0,0)');page.screenshot(path=str(destination/f'applications-{width}.png'),full_page=True)
        open_view(page,'settings')
        group=page.get_by_role('group',name='Companies and boundaries',exact=True)
        legend=group.locator('legend').bounding_box();box=group.bounding_box()
        assert legend['y']>=box['y']+16
        assert legend['x']>=box['x']+16 and legend['x']+legend['width']<=box['x']+box['width']-16
        expect(group).not_to_contain_text('Keep your search focused')
        # Every bordered form group follows the same layout, including optional settings.
        for panel in page.locator('#settings fieldset.settings-group, #settings fieldset.section').all():
            heading=panel.locator(':scope > legend').bounding_box();bounds=panel.bounding_box()
            assert heading and bounds
            assert heading['y'] >= bounds['y']+16
            assert heading['x'] >= bounds['x']+16 and heading['x']+heading['width'] <= bounds['x']+bounds['width']-16
        group.screenshot(path=str(destination/f'companies-{width}.png'))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.evaluate('scrollTo(0,0)');page.screenshot(path=str(destination/f'settings-{width}.png'),full_page=True)
        assert not errors
        browser.close()
