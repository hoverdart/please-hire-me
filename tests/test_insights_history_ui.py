from tests.dashboard_evidence import open_view
import json
from pathlib import Path

import pytest

from tests.test_history_api import history_server
from tests.test_history_import import source
from tests.test_recruiting_insights import cohort


@pytest.mark.parametrize('width',[320,1440])
def test_insight_drilldown_and_reviewed_history_import(store,job,package,cohort,source,history_server,width):
    from playwright.sync_api import sync_playwright,expect
    store.update_settings({'live_enabled':False})
    original=store.application_record(cohort[1][4][0])
    destination=Path('output/insights-history');destination.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':1000});errors=[];mail_requests=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('request',lambda request:mail_requests.append(request.url) if '/gmail/' in request.url or 'recruiting-feedback/sync' in request.url else None)
        page.goto(history_server+'/#token=fixture-capability')
        open_view(page, 'applications')
        page.locator('#recruiting-insights-panel > summary').click()
        expect(page.locator('#insights-status')).to_contain_text('Saved data as of')
        metrics=page.locator('#insights-metrics')
        expect(metrics).to_contain_text('Full observation window · 3')
        expect(metrics).to_contain_text('Interviews · 1')
        expect(page.locator('#insights-method')).to_contain_text('14-day window')
        metrics.locator('[data-insight-count=interview]').click()
        expect(page.locator('#insights-page')).to_have_text('1–1 of 1 attempts')
        expect(page.locator('#insights-selection')).to_be_focused()
        page.locator('#insights-applications').get_by_role('button',name='View application evidence').click()
        evidence=page.locator('#insights-evidence')
        expect(evidence).to_contain_text('Comparison evidence')
        expect(evidence).to_contain_text('full application history below can include later responses')
        expect(evidence).to_contain_text('Recruiting stage: offer')
        expect(evidence).to_contain_text(cohort[1][0][0])
        # Every drilldown reaches the original PDF and package, not the current selected upload.
        with page.expect_download() as downloading:evidence.get_by_role('button',name='Download recorded resume',exact=True).click()
        original_pdf=destination/('recorded-resume-'+str(width)+'.pdf');downloading.value.save_as(original_pdf)
        assert original_pdf.read_bytes()==(store.root/'documents'/package['documents'][0]['filename']).read_bytes()
        page.locator('#recruiting-insights-panel').screenshot(path=str(destination/('insights-'+str(width)+'.png')))
        page.locator('#insights-groups').screenshot(path=str(destination/('insights-groups-'+str(width)+'.png')))
        page.screenshot(path=str(destination/('insights-workspace-'+str(width)+'.png')))
        # Unavailable results do not leave old counts under changed filters.
        page.route('**/api/recruiting-insights?*',lambda route:route.fulfill(status=503,json={'error':'Synthetic insight outage'}))
        page.locator('#insights-filters').get_by_role('button',name='Refresh insights').click()
        expect(page.locator('#insights-status')).to_contain_text('Synthetic insight outage')
        expect(metrics).to_be_empty()
        page.unroute('**/api/recruiting-insights?*')
        page.locator('#insights-filters').get_by_role('button',name='Refresh insights').click()
        expect(metrics).to_contain_text('Interviews · 1')
        open_view(page, 'profile')
        page.locator('#history-import-panel > summary').click()
        candidates=page.locator('#history-import-candidates')
        expect(candidates).to_contain_text('Previous Employer')
        assert not store.history_entries()
        candidates.get_by_role('button',name='employment · Previous Employer · lines',exact=False).click()
        form=page.locator('#history-import-form')
        expect(page.locator('#history-import-excerpt')).to_contain_text('May 2025 - August 2025')
        expect(form.get_by_label('Organization',exact=True)).to_have_value('Previous Employer')
        expect(form.get_by_role('combobox',name='Current status',exact=True)).to_have_value('')
        form.get_by_label('Responsibilities and accomplishments').fill('Built Python services for scheduling.\nTested distributed systems.')
        page.evaluate('refresh()')
        expect(form.get_by_label('Responsibilities and accomplishments')).to_have_value('Built Python services for scheduling.\nTested distributed systems.')
        assert not store.history_entries()
        form.get_by_label('I reviewed this excerpt',exact=False).check()
        form.get_by_role('button',name='Confirm and import entry').click()
        expect(page.locator('#history-import-status')).to_contain_text('Entry imported and confirmed')
        assert len(store.history_entries())==1
        expect(page.locator('#history-list')).to_contain_text('Previous Employer')
        # A repeated click cannot duplicate history or overwrite the applicant's confirmed entry.
        form.get_by_label('I reviewed this excerpt',exact=False).check()
        delayed_refresh=[]
        page.route('**/api/state*',lambda route:delayed_refresh.append(route))
        with page.expect_request('**/api/state*'):
            form.get_by_role('button',name='Confirm and import entry').click()
        # Server-confirmed saving must be announced before unrelated refresh work completes.
        expect(page.locator('#history-import-status')).to_contain_text('already exists')
        assert len(store.history_entries())==1
        page.unroute('**/api/state*')
        page.locator('#history-import-panel').screenshot(path=str(destination/('history-import-'+str(width)+'.png')))
        page.locator('#history-import-excerpt').screenshot(path=str(destination/('history-excerpt-'+str(width)+'.png')))
        page.screenshot(path=str(destination/('history-workspace-'+str(width)+'.png')))
        # Changing source bounds requires a fresh proposal and explicit confirmation.
        form.get_by_label('Last source line',exact=True).fill(str(source['candidates'][0]['end_line']+1))
        expect(form.get_by_role('button',name='Confirm and import entry')).to_be_disabled()
        expect(form.get_by_label('I reviewed this excerpt',exact=False)).not_to_be_checked()
        assert store.application_record(original['id'])==original
        assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
        assert not mail_requests and not errors and page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        browser.close()
