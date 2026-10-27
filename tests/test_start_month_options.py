"""Confirmed start-month choices preserve their date precision and approval scope."""
import pytest
from hireme.answers import _option_value, resolve
from hireme.field_context import save_binding
from hireme.util import Blocked


@pytest.mark.parametrize('label',['May 2030','May, 2030','2030 May','May-2030','2030-05'])
def test_confirmed_start_month_matches_one_equivalent_label(label):
    assert _option_value('earliest_start','2030-05',[label,'June 2030'])==label


@pytest.mark.parametrize('choices',[
    ['June 2030'], ['May 2029'], ['2030'], ['Summer 2030'],
    ['May 1, 2030'], ['May–July 2030'], ['May 2030','2030-05'],
])
def test_start_month_never_becomes_another_month_day_range_or_ambiguous_choice(choices):
    with pytest.raises(Blocked,match='option_mismatch'):
        _option_value('earliest_start','2030-05',choices)


@pytest.mark.parametrize('kind',['select','checkbox-group'])
def test_real_month_question_uses_confirmed_fact_without_another_model_request(store,job,kind):
    store.put_facts({'earliest_start':'2030-05'})
    context={**job,'title':'Frontend Software Engineer Intern - Summer 2030'}
    field={'label':'What month(s) are you available to start?','type':kind,'required':True,
        'options':['May 2030','June 2030','July 2030'],'maxlength':-1}
    save_binding(store,job['host'],field,context,key='earliest_start')
    answer=resolve(store,job['host'],field,context=context)
    assert answer['value']=='May 2030'
    assert answer['provenance']['fact_key']=='earliest_start'
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_month_question_does_not_reuse_or_generate_a_broader_model_mapping(store,job):
    from hireme.answers import _option_mapping_id
    store.put_facts({'earliest_start':'2030-05'})
    context={**job,'title':'Software Engineer Intern - Summer 2030'}
    field={'label':'What month(s) are you available to start?','type':'checkbox-group',
        'required':True,'options':['June 2030','Summer 2030'],'maxlength':-1}
    save_binding(store,job['host'],field,context,key='earliest_start')
    fact=store.facts()['earliest_start']
    store.db.execute('INSERT INTO option_mappings VALUES(?,?,?,?,?)',
        (_option_mapping_id('earliest_start',fact,field),'earliest_start',fact['revision'],'June 2030','2030-01-01T00:00:00+00:00'))
    class Provider:
        def map_option(self,*args):raise AssertionError('A model must not expand the month')
    with pytest.raises(Blocked,match='option_mismatch'):
        resolve(store,job['host'],field,provider=Provider(),context=context)


def test_applicant_can_explicitly_approve_another_available_month(store,job):
    store.put_facts({'earliest_start':'2030-05'})
    context={**job,'title':'Software Engineer Intern - Summer 2030'}
    field={'label':'What month(s) are you available to start?','type':'checkbox-group',
        'required':True,'options':['May 2030','June 2030'],'maxlength':-1}
    question=store.ask(job['id'],job['host'],field['label'],field['options'],field=field,context=context)
    store.answer_question(question,'June 2030')
    answer=resolve(store,job['host'],field,context=context)
    assert answer['value']=='June 2030' and answer['provenance']['answer_id']==question
