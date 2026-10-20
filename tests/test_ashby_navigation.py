import pytest

from hireme.browser import Browser

POSTING='https://jobs.ashbyhq.com/acme/11111111-1111-4111-8111-111111111111'


@pytest.mark.parametrize('target',[
    POSTING+'/application',
    'https://other.invalid/acme/11111111-1111-4111-8111-111111111111/application',
    POSTING.replace('/acme/','/other/')+'/application',
    POSTING+'/application?submit=true',
    POSTING+'/application#submit',
    POSTING.replace('https:','http:')+'/application',
])
def test_ashby_application_tab_must_be_exact_posting_navigation(store,target):
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com'
        b.page.set_content(f'<a role="tab" href="{target}">Application</a><button>Apply for this job</button><button>Apply for this job</button>')
        entry=b._ashby_application_entry({'url':POSTING})
        assert (entry is not None)==(target==POSTING+'/application')
        assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('html',[
    f'<form><a role="tab" href="{POSTING}/application">Application</a></form>',
    '<button role="tab">Application</button>',
    f'<a role="tab" href="{POSTING}/application">Application</a>'*2,
])
def test_ambiguous_or_form_bound_tabs_do_not_authorize_navigation(store,html):
    with Browser(store) as b:
        b.current_host='jobs.ashbyhq.com';b.page.set_content(html)
        assert b._ashby_application_entry({'url':POSTING}) is None
