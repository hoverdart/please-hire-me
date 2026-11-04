import pytest

from hireme.browser_agent import AdaptiveBrowser
from hireme.browser_observation import observe
from hireme.workday_history import put
from hireme.util import Blocked


@pytest.mark.parametrize('order,repair',[
    ([3,1,0,2],False),([1,0,2,2],True),([1,0,2,2],False),
])
def test_saved_workday_rows_bind_by_identity_and_duplicates_need_verified_repair(store,job,monkeypatch,order,repair):
    entries=[put(store,'employment',{'organization':'Company '+str(i),'title':'Engineer '+str(i)},source='fixture',confirmed=True) for i in range(4)]
    if repair:store.verified_history_repair_job=job['id']
    with AdaptiveBrowser(store,test_url=job['url']) as browser:
        browser.job={**job,'answer_scope':job['host']};browser.provider=None;browser.preparing=False
        browser.opened=set();browser.searched=set();browser.option_cache={}
        monkeypatch.setattr(browser,'_check',lambda:None)
        rows=''.join('<div data-fkit-id=workExperience-'+str(row+8)+'--null><label>Job Title*<input value="Engineer '+str(source)+'"></label><label>Company*<input value="Company '+str(source)+'"></label></div>' for row,source in enumerate(order))
        browser.page.set_content('<h1>My Experience</h1><div data-automation-id=applyFlowMyExpPage><section><h2>Work Experience</h2>'+rows+'</section></div>')
        view=observe(browser.page)
        if len(set(order))<4:
            with pytest.raises(Blocked,match='history_confirmation_required'):browser._choices(view)
            return
        choices,answers,_,pending=browser._choices(view)
        actual=[a['provenance']['history_entry_id'] for a in answers if a['field']['label']=='Job Title*']
        assert actual==[entries[i]['id'] for i in order]
        assert not pending
        assert not choices


def test_duplicate_history_never_authorizes_a_delete_control(store,job,monkeypatch):
    for i in range(4):put(store,'employment',{'organization':'Company '+str(i),'title':'Engineer '+str(i)},source='fixture',confirmed=True)
    store.verified_history_remove_plan={'job_id':job['id']}
    with AdaptiveBrowser(store,test_url=job['url']) as browser:
        browser.job={**job,'answer_scope':job['host']};browser.provider=None;browser.preparing=False
        browser.opened=set();browser.searched=set();browser.option_cache={}
        monkeypatch.setattr(browser,'_check',lambda:None)
        rows=''.join('<article><header><button>Delete</button></header><div data-fkit-id=workExperience-'+str(row+8)+'--null><label>Job Title*<input value="Engineer '+str(source)+'"></label><label>Company*<input value="Company '+str(source)+'"></label></div></article>' for row,source in enumerate([1,0,2,2]))
        browser.page.set_content('<h1>My Experience</h1><div data-automation-id=applyFlowMyExpPage><section><h2>Work Experience</h2>'+rows+'</section><section><h2>Websites</h2><button>Delete</button></section></div>')
        with pytest.raises(Blocked,match='history_confirmation_required'):
            browser._choices(observe(browser.page))
