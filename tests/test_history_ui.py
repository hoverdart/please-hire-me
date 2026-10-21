import pytest

from tests.test_history_api import history_server


@pytest.mark.parametrize('width',[320,1440])
def test_history_editor_repeat_entries_refresh_reconfirm_withdraw_and_literal_sources(store,history_server,width):
    from playwright.sync_api import sync_playwright, expect
    base=history_server
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':900});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/#token=fixture-capability');page.locator('[data-view=profile]').click()
        expect(page.locator('#history-list')).to_contain_text('No structured entries saved')
        page.locator('#history-add-education').click()
        form=page.locator('#history-form')
        assert form.locator('.confirmation').evaluate('node => getComputedStyle(node).flexDirection')=='row'
        institution='Berkeley <img src=x onerror="window.historyXss=true">'
        form.locator('[name=institution]').fill(institution)
        form.locator('[name=degree]').fill('BS')
        form.locator('[name=expected_end]').fill('2028-05')
        form.locator('[name=current]').select_option('true')
        form.locator('[name=source]').fill('I checked my enrollment record <script>alert(1)</script>')
        page.evaluate('refresh()')
        expect(form.locator('[name=institution]')).to_have_value(institution)
        form.locator('[name=confirmed]').check()
        with page.expect_response('**/api/history') as response:form.get_by_role('button',name='Save confirmed entry',exact=True).click()
        assert response.value.status==200
        expect(form).to_be_hidden()
        expect(page.locator('#history-list')).to_contain_text(institution)
        assert page.locator('#history-list img, #history-list script').count()==0
        assert page.evaluate('window.historyXss') is None
        school=store.history_entries()[0]
        assert school['values']=={'institution':institution,'degree':'BS','expected_end':'2028-05','current':True}
        assert 'start' not in school['values']
        page.locator('#history-add-employment').click()
        form.locator('[name=organization]').fill('Acme')
        form.locator('[name=title]').fill('Software engineering intern')
        form.locator('[name=start]').fill('2026-06')
        form.locator('[name=end]').fill('2026-08')
        form.locator('[name=current]').select_option('false')
        form.locator('[name=source]').fill('I checked my employment records')
        form.locator('[name=confirmed]').check()
        with page.expect_response('**/api/history'):form.get_by_role('button',name='Save confirmed entry',exact=True).click()
        expect(form).to_be_hidden()
        assert len(store.history_entries())==2
        page.reload();page.locator('[data-view=profile]').click()
        school_row=page.locator(f'[data-history-id="{school["id"]}"]')
        school_row.get_by_role('button',name='Edit entry',exact=True).click()
        expect(form.locator('[name=confirmed]')).not_to_be_checked()
        form.locator('[name=confirmed]').check()
        form.locator('[name=degree]').fill('Bachelor of Science')
        expect(form.locator('[name=confirmed]')).not_to_be_checked()
        form.locator('[name=degree]').blur();page.evaluate('refresh()')
        expect(form.locator('[name=degree]')).to_have_value('Bachelor of Science')
        # Switching entries cannot silently discard the current draft.
        page.locator('#history-add-employment').click()
        expect(page.locator('#history-feedback')).to_contain_text('Save or discard')
        expect(form.locator('[name=degree]')).to_have_value('Bachelor of Science')
        form.locator('[name=confirmed]').check()
        with page.expect_response('**/api/history'):form.get_by_role('button',name='Save confirmed entry',exact=True).click()
        expect(form).to_be_hidden()
        assert next(row for row in store.history_entries() if row['id']==school['id'])['revision']==2
        employment=next(row for row in store.history_entries() if row['kind']=='employment')
        work_row=page.locator(f'[data-history-id="{employment["id"]}"]')
        with page.expect_response('**/api/history-withdraw'):work_row.get_by_role('button',name='Stop using entry',exact=True).click()
        expect(work_row).to_contain_text('Not in use')
        assert len(store.history_entries())==1 and len(store.history_entries(confirmed_only=False))==2
        work_row.get_by_role('button',name='Review and confirm',exact=True).click()
        expect(form.locator('[name=organization]')).to_have_value('Acme')
        expect(form.locator('[name=confirmed]')).not_to_be_checked()
        form.locator('[name=confirmed]').check()
        with page.expect_response('**/api/history'):form.get_by_role('button',name='Save confirmed entry',exact=True).click()
        expect(form).to_be_hidden()
        assert len(store.history_entries())==2
        page.locator('#history-add-education').click()
        form.locator('[name=institution]').fill('Unsaved new school')
        page.evaluate('refresh()')
        expect(form.locator('[name=institution]')).to_have_value('Unsaved new school')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#history-panel').screenshot(path=f'/tmp/hireme-history-{width}.png')
        assert not errors
        browser.close()


def test_history_holds_offer_direct_editor_in_action_desk_and_question_queue(store,job,history_server):
    from playwright.sync_api import sync_playwright, expect
    from hireme.job_holds import hold
    store.db.execute("UPDATE jobs SET status='blocked',reason='history_confirmation_required' WHERE id=?",(job['id'],))
    hold(store,job,'history_confirmation_required','Education start month')
    store.ask(job['id'],job['host'],'Education start month',[],reason='history_confirmation_required')
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page()
        page.goto(history_server+'/#token=fixture-capability')
        page.locator('[data-view=opportunities]').click()
        page.locator('#jobs').get_by_role('button',name='Review education and employment').click()
        expect(page.locator('#history-panel')).to_be_visible()
        expect(page.locator('#history-add-education')).to_be_focused()
        page.locator('[data-view=questions]').click()
        page.locator('#question-list').get_by_role('button',name='Review education and employment').click()
        expect(page.locator('#history-panel')).to_be_visible()
        browser.close()
