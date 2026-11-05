"""Resume only the exact observed saved Workday application."""
import pytest

from hireme.browser_agent import AdaptiveBrowser
from hireme.util import Blocked


def test_expired_workday_session_requests_sign_in_before_candidate_home_navigation(store):
    with AdaptiveBrowser(store,test_url='https://synthetic.wd12.myworkdayjobs.com') as browser:
        browser.owner_verified=False
        browser.page.set_content('<button onclick="window.navigated=true">Candidate Home</button>')
        with pytest.raises(Blocked,match='account_blocked'):browser._resume_workday()
        assert not browser.page.evaluate('!!window.navigated')


@pytest.mark.parametrize('slug,allowed',[
    ('Engineer_JR123',True),('Different_Engineer_JR999',False),
])
def test_candidate_home_continuation_is_bound_to_its_own_requisition(store,monkeypatch,slug,allowed):
    job={'host':'synthetic.wd12.myworkdayjobs.com','title':'Engineer',
        'url':'https://synthetic.wd12.myworkdayjobs.com/en-US/External/job/California/Engineer_JR123'}
    with AdaptiveBrowser(store,test_url='https://synthetic.wd12.myworkdayjobs.com') as browser:
        browser.job=job;browser.allowed_origin='https://synthetic.wd12.myworkdayjobs.com'
        monkeypatch.setattr(browser,'_check',lambda:None)
        browser.page.set_content('''<button id=home>Candidate Home</button><div id=cards hidden>
          <button aria-label="Related Actions Different Engineer" onclick="window.wrong=true">Other actions</button>
          <button id=related aria-label="Related Actions Engineer">Actions</button></div>
          <div id=menu hidden><a role=menuitem href="https://synthetic.wd12.myworkdayjobs.com/en-US/External/job/'''+slug+'''/apply">Continue Application</a>
          <button onclick="window.deleted=true">Delete Application</button></div><script>
          home.onclick=()=>cards.hidden=false;related.onclick=()=>menu.hidden=false;</script>''')
        performed=[]
        monkeypatch.setattr(browser,'_perform',lambda choice,*args:performed.append(choice))
        if allowed:
            browser._resume_workday()
            assert len(performed)==1 and performed[0]['kind']=='entry'
            assert performed[0]['action']['href'].endswith('/Engineer_JR123/apply')
        else:
            with pytest.raises(Blocked,match='browser_draft_review'):browser._resume_workday()
            assert not performed
        assert not browser.page.evaluate('!!window.wrong||!!window.deleted')
