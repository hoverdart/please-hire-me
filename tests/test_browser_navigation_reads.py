from types import SimpleNamespace

import pytest
from playwright.sync_api import Error

from hireme.browser_agent import AdaptiveBrowser
from hireme.browser_agent_protocol import origin
from hireme.browser_observation import observe,retry_dom_read
from hireme.util import Blocked


def test_observation_retries_only_the_read_after_document_replacement(store,monkeypatch):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('<h1>My Information</h1><label>Email<input type=email></label>')
        original=browser.page.evaluate;calls=[]
        def navigating(*args,**kwargs):
            calls.append(True)
            if len(calls)==1:raise Error('Execution context was destroyed, most likely because of a navigation')
            return original(*args,**kwargs)
        monkeypatch.setattr(browser.page,'evaluate',navigating)
        assert observe(browser.page)['fields'][0]['label']=='Email'
        assert len(calls)==2
        assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


def test_security_observation_survives_navigation_without_repeating_an_action(store,monkeypatch):
    with AdaptiveBrowser(store,test_url='http://127.0.0.1') as browser:
        browser.page.set_content('<h1>Review</h1>')
        browser.allowed_origin=origin(browser.page.url);browser.owner_verified=True
        locator_type=type(browser.page.locator('iframe'));original=locator_type.evaluate_all;calls=[]
        def navigating(locator,*args,**kwargs):
            calls.append(True)
            if len(calls)==1:raise Error('Execution context was destroyed, most likely because of a navigation')
            return original(locator,*args,**kwargs)
        monkeypatch.setattr(locator_type,'evaluate_all',navigating)
        browser._check()
        assert len(calls)==2
        assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


@pytest.mark.parametrize('message',['Target page, context or browser has been closed','SyntaxError: invalid selector'])
def test_non_navigation_errors_are_never_retried(message):
    waits=[];page=SimpleNamespace(is_closed=lambda:False,wait_for_timeout=waits.append)
    def failing():raise Error(message)
    with pytest.raises(Error,match=message):retry_dom_read(page,failing)
    assert waits==[]


def test_navigation_read_retry_keeps_pause_authority():
    reads=[];checks=[];page=SimpleNamespace(is_closed=lambda:False,wait_for_timeout=lambda _:None)
    def read():reads.append(True);raise Error('Execution context was destroyed')
    def checkpoint():
        checks.append(True)
        if len(checks)>1:raise Blocked('paused')
    with pytest.raises(Blocked,match='paused'):retry_dom_read(page,read,checkpoint=checkpoint)
    assert len(reads)==1


def test_navigation_read_retry_has_a_deadline():
    reads=[];page=SimpleNamespace(is_closed=lambda:False,wait_for_timeout=lambda _:None)
    def read():reads.append(True);raise Error('Cannot find context with specified id')
    with pytest.raises(Error):retry_dom_read(page,read,timeout=0)
    assert len(reads)==1
