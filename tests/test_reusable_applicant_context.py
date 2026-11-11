import pytest

from hireme.answers import resolve, field_key, _compatible_field
from hireme.config import validate_fact
from hireme.util import Blocked


def field(label, kind='text', options=None, **extra):
    return {'label':label,'type':kind,'required':True,'options':options or [],'maxlength':-1,**extra}


def test_confirmed_dates_scores_and_clearance_resolve_without_inference(store,job):
    store.put_facts({'internship_start':'2027-05-14','internship_end':'2027-08-25',
        'high_school_graduation':'2025-06','high_school_graduation_year':'2025',
        'sat_score':'1540','sat_superscore':'1560','active_top_secret':'No'})
    for control,expected,key in [
        (field('Ideal start date in office'),'May 14, 2027','internship_start'),
        (field('Ideal end date',date_format='YYYY-MM-DD'),'2027-08-25','internship_end'),
        (field('When did you graduate from High School?'),'June 2025','high_school_graduation'),
        (field('High school graduation year'),'2025','high_school_graduation_year'),
        (field('Provide your best result on SAT:',kind='number'),'1540','sat_score'),
        (field('SAT superscore',kind='number'),'1560','sat_superscore'),
        (field('Do you currently possess an active U.S. Top Secret security clearance?',kind='radio',options=['Yes','No']),'No','active_top_secret'),
    ]:
        answer=resolve(store,job['host'],control,context=job)
        assert answer['value']==expected
        assert answer['provenance']['fact_key']==key


@pytest.mark.parametrize('label',[
    'Are you eligible to obtain a Top Secret clearance?',
    'Have you ever held a Top Secret clearance?',
    'Do you hold an active Secret clearance?',
    'SAT math score', 'ACT score',
])
def test_new_facts_never_answer_different_clearances_or_test_sections(label):
    assert field_key(label) not in {'active_top_secret','sat_score','sat_superscore'}


def test_internship_dates_never_supply_permanent_start_dates(store,job):
    store.put_facts({'internship_start':'2027-05-14'})
    control=field('Ideal start date in office')
    context={**job,'title':'Software Engineer New Grad'}
    assert not _compatible_field('internship_start',control,context)
    with pytest.raises(Blocked):resolve(store,job['host'],control,context=context)


@pytest.mark.parametrize('key,value',[
    ('sat_score','1561'),('sat_superscore','1601'),('sat_score','36'),
    ('internship_start','2027-02-30'),('high_school_graduation','2025-13'),
    ('active_top_secret','Probably'),
])
def test_invalid_context_is_rejected(key,value):
    with pytest.raises(ValueError):validate_fact(key,value)
