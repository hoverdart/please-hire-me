"""Wording observed in batch emails must reuse only the matching confirmed fact."""
import pytest

from hireme.answers import resolve, validate_package
from hireme.util import Blocked, digest


def field(label):
    return {'label':label,'type':'text','options':[],'required':True,'maxlength':-1}


@pytest.mark.parametrize('label,key,value,expected',[
    ('If offered a full-time position upon graduation, what is your earliest available start date?',
     'fulltime_start','2028-06-01','June 1, 2028'),
    ('We care about addressing everyone correctly. Add your personal pronouns below to share with the hiring team.',
     'pronouns','he/him','he/him'),
    ('We care about addressing everyone correctly. To help us get it right, please write out how your name is pronounced phonetically to share with the hiring team.',
     'name_pronunciation','Test Person','Test Person'),
    ('What is your expected hourly pay range for this internship?',
     'salary','$35/hr','35'),
])
def test_email_question_uses_confirmed_fact_and_revalidates(store,job,package,label,key,value,expected):
    f=field(label)
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)
    store.put_facts({key:value})
    answer=resolve(store,job['host'],f,context=job)
    assert answer['value']==expected and answer['provenance']['fact_key']==key
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,job,package)
    store.db.execute('DELETE FROM facts WHERE key=?',(key,))
    package['facts_hash']=digest(store.facts())
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_hourly_expectation_never_converts_an_annual_salary(store,job):
    store.put_facts({'salary':'$100000/year'})
    with pytest.raises(Blocked,match='missing_fact'):
        resolve(store,job['host'],field('What is your expected hourly pay range for this internship?'),context=job)


def test_fulltime_start_never_comes_from_internship_start_or_graduation(store,job):
    store.put_facts({'earliest_start':'2027-05'})
    with pytest.raises(Blocked):
        resolve(store,job['host'],field('If offered a full-time position upon graduation, what is your earliest available start date?'),context=job)
