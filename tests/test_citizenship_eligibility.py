import pytest
from hireme.policy import eligible
from hireme.job_holds import hold,ready
from hireme.util import Blocked


@pytest.mark.parametrize('citizenship',['United States of America','U.S. Citizen','USA'])
@pytest.mark.parametrize('requirement',['U.S. Citizenship is Required.','Must be a United States citizen.','United States citizenship required.'])
def test_explicit_citizenship_requirement_uses_confirmed_citizenship(store,job,citizenship,requirement):
    store.put_facts({'citizenship':citizenship})
    eligible({**job,'description':requirement+' Build Python software.'},store.settings(),store.facts())


@pytest.mark.parametrize('citizenship',[None,'Canadian','U.S. Permanent Resident'])
@pytest.mark.parametrize('requirement',['U.S. Citizenship is Required.','Must be a United States citizen.','United States citizenship required.'])
def test_us_person_does_not_establish_us_citizenship(store,job,citizenship,requirement):
    if citizenship:store.put_facts({'citizenship':citizenship})
    with pytest.raises(Blocked,match='citizenship_or_clearance_review'):
        eligible({**job,'description':requirement},store.settings(),store.facts())


def test_generic_export_definition_does_not_require_separate_citizenship(store,job):
    eligible({**job,'description':'ITAR: U.S. persons include U.S. citizens and lawful permanent residents.'},store.settings(),store.facts())


def test_us_citizenship_never_establishes_clearance(store,job):
    store.put_facts({'citizenship':'United States of America'})
    with pytest.raises(Blocked,match='citizenship_or_clearance_review'):
        eligible({**job,'description':'Must be a US citizen with an active US security clearance.'},store.settings(),store.facts())


def test_legacy_citizenship_review_rechecks_once_then_tracks_relevant_facts(store,job):
    j={**job,'description':'U.S. Citizenship is Required.'};store.upsert_job(j)
    hold(store,j,'citizenship_or_clearance_review')
    store.db.execute("UPDATE job_holds SET category='review',dependency='legacy-review' WHERE job_id=?",(job['id'],))
    assert ready(store,j)
    hold(store,j,'citizenship_or_clearance_review')
    assert not ready(store,j)
    store.put_facts({'phone':'5557654321'})
    assert not ready(store,j)
    store.put_facts({'citizenship':'United States of America'})
    assert ready(store,j)


def test_clearance_review_still_requires_visible_attention():
    from hireme.presentation import job_display
    shown=job_display({'status':'blocked','reason':'citizenship_or_clearance_review'})
    assert shown['requires_attention'] and shown['display_status']=='blocked'
