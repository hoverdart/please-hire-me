import base64
import json
import time

import pytest

from tests.test_connected_workspace_ui import dashboard


@pytest.mark.parametrize('width',[320,1440])
@pytest.mark.parametrize('stale',[False,True])
def test_authenticated_handoff_has_one_controller_keyboard_input_and_safe_disconnect(dashboard,width,stale):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page(viewport={'width':width,'height':900});errors=[];calls=[];accepted=[0];rejected=[False]
        fixture=browser.new_page(viewport={'width':360,'height':180})
        fixture.set_content('<style>body{font:16px system-ui;padding:24px;color:#19233b}button{font:inherit;padding:12px;background:#3155d9;color:white;border:0;border-radius:8px}</style><p>Owned verification fixture</p><button>Confirm fixture</button>')
        png=base64.b64encode(fixture.screenshot()).decode();fixture.close()
        page.on('pageerror',lambda error:errors.append(str(error)))
        def state_response(route):
            response=route.fetch();data=response.json()
            data['worker_running']=True;data['settings']['human_verification_handoff']=True
            data['human_verification']={'application_id':'fixture-app','phase':'challenge','company':'Synthetic & Co with a long employer name',
                'title':'Software Engineer Intern Summer 2027','expires_at':time.time()+60}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        def handoff_response(route):
            data=route.request.post_data_json;calls.append(data)
            assert route.request.headers.get('x-hireme-token')=='fixture-capability'
            action=data['action']
            if action=='claim':result={'controller':'fixture-controller'}
            elif action=='frame':result={'png':png,'ready':True,'frame_revision':'new-revision' if rejected[0] else 'fixture-revision',
                'next_sequence':1+accepted[0],'interaction_pending':False,'stale_click':rejected[0] and not accepted[0]}
            elif action=='click' and stale and not rejected[0]:
                rejected[0]=True;result={'stale_click':True,'accepted':False}
            else:
                if action=='click':accepted[0]+=1
                result={'active':True}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(result))
        page.route('**/api/state*',state_response);page.route('**/api/human-verification',handoff_response)
        page.goto(dashboard+'/#token=fixture-capability')
        page.get_by_role('button',name='Complete challenge here').click()
        dialog=page.locator('#captcha-handoff-dialog');frame=page.locator('#captcha-handoff-frame')
        expect(dialog).to_be_visible();expect(frame).to_be_enabled()
        expect(page.locator('#captcha-handoff-status')).to_contain_text('Connected to this challenge')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert dialog.evaluate('e=>e.scrollWidth<=e.clientWidth')
        frame.focus();frame.press('ArrowRight');frame.press('Enter')
        if stale:
            expect(page.locator('#captcha-handoff-status')).to_contain_text('The challenge changed')
            expect(frame).to_be_enabled();frame.press('Enter')
        expect(frame).to_be_disabled()
        expect(frame).to_be_enabled(timeout=5000)
        clicks=[c for c in calls if c['action']=='click']
        assert len(clicks)==(2 if stale else 1) and all(c['sequence']==1 for c in clicks) and clicks[0]['x']==pytest.approx(0.55)
        if not stale:page.screenshot(path=f'/tmp/hireme-captcha-handoff-{width}.png')
        dialog.get_by_role('button',name='Stop handoff').click()
        expect(dialog).to_be_hidden()
        expect(page.locator('#captcha-handoff-image')).to_have_count(0)
        for _ in range(20):
            if any(c['action']=='disconnect' for c in calls):break
            page.wait_for_timeout(50)
        assert sum(c['action']=='claim' for c in calls)==1 and sum(c['action']=='disconnect' for c in calls)==1
        assert not errors
        assert page.request.post(dashboard+'/api/human-verification',data={'action':'claim'}).status==403
        browser.close()
