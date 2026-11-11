"""Repeated question wording must not redirect an approved answer to another card."""
import pytest
from playwright.sync_api import sync_playwright
from hireme.browser import Browser
from hireme.util import Blocked


def card(name, section):
    return f'''<div class="application-form" data-qa="additional-cards">
      <h4 data-qa="card-name">{section}</h4><div class="application-question">
      <div class="application-label">Do you need sponsorship?*</div>
      <label><input type="radio" name="{name}" value="yes">Yes</label>
      <label><input type="radio" name="{name}" value="no">No</label>
      </div></div>'''


@pytest.fixture
def browser(store):
    with sync_playwright() as p:
        chromium=p.chromium.launch(headless=True)
        browser=Browser(store);browser.page=chromium.new_page()
        browser.page.set_content('<form>'+card('cards[first][field0]','Sponsorship')+card('cards[second][field0]','Eligibility')+'</form>')
        yield browser
        chromium.close()


def test_repeated_labels_fill_and_verify_distinct_named_groups_after_reordering(browser):
    fields=browser._raw_snapshot()
    browser.page.evaluate("document.querySelector('form').prepend(document.querySelectorAll('.application-form')[1])")
    answers=[{'field':fields[0],'value':'No','provenance':{}},
             {'field':fields[1],'value':'Yes','provenance':{}}]
    for answer in answers:browser._fill(answer)
    current=browser._raw_snapshot()
    assert {f['section']:f['value'] for f in current}=={'Lever: Sponsorship':'No','Lever: Eligibility':'Yes'}
    browser._verify(answers,[],current)


@pytest.mark.parametrize('change',[
    "document.querySelector('.application-form').remove()",
    "document.querySelector('.application-question').prepend(document.querySelectorAll('label')[1])",
    "document.querySelector('[data-qa=card-name]').textContent='Different declaration'",
])
def test_missing_or_changed_group_never_uses_the_other_matching_question(browser,change):
    field=browser._raw_snapshot()[0]
    browser.page.evaluate(change)
    with pytest.raises(Blocked,match='form_changed'):
        browser._fill({'field':field,'value':'No'})
    assert browser.page.locator('input:checked').count()==0
