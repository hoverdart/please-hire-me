import pytest

from hireme.util import Blocked
from hireme.workday_history import put,resolve_field,validate_answer


def test_reviewed_history_wording_is_replayed_without_a_model_and_bound_to_revision(store):
    entry=put(store,'education',{'institution':'Fixture University','field_of_study':'Computer Sciences'},source='fixture',confirmed=True)['id']
    field={'label':'Field of Study*','type':'combobox','required':True,'options':['Computer Science','History'],
        'section':'education','history_entry_id':entry,'history_key':'field_of_study'}
    class Reviewed:
        calls=0
        def map_option(self,observed,source):
            self.calls+=1
            assert source['value']=='Computer Sciences'
            assert observed['options']==['Computer Science','History']
            return 'Computer Science'
    provider=Reviewed()
    answer=resolve_field(store,field,provider)
    assert answer['value']=='Computer Science' and provider.calls==1
    assert resolve_field(store,field)==answer
    validate_answer(store,answer)
    assert store.db.execute('SELECT COUNT(*) FROM option_mappings').fetchone()[0]==1
    put(store,'education',{'institution':'Fixture University','field_of_study':'History'},source='fixture',confirmed=True,entry_id=entry)
    with pytest.raises(Blocked,match='history_changed'):validate_answer(store,answer)


def test_history_translation_cannot_return_an_unlisted_choice(store):
    entry=put(store,'education',{'institution':'Fixture University','field_of_study':'Computer Sciences'},source='fixture',confirmed=True)['id']
    field={'label':'Field of Study*','type':'combobox','required':True,'options':['Computer Science'],
        'section':'education','history_entry_id':entry,'history_key':'field_of_study'}
    class Unlisted:
        def map_option(self,*args):return 'Invented Course'
    with pytest.raises(Blocked):resolve_field(store,field,Unlisted())
    assert not store.db.execute('SELECT 1 FROM option_mappings').fetchone()


@pytest.mark.parametrize('options,expected',[
    (['Electrical Engineering'],'Electrical Engineering'),
    (['Computer Science'],'Computer Science'),
])
def test_joint_program_can_use_one_literal_field_category_without_changing_history(store,options,expected):
    from hireme.workday_history import put,resolve_field,validate_answer
    entry=put(store,'education',{'institution':'Fixture University','field_of_study':'Electrical Engineering and Computer Sciences'},source='Confirmed synthetic joint program',confirmed=True)
    field={'label':'Field of Study','type':'combobox','required':False,'section':'education',
           'history_entry_id':entry['id'],'history_key':'field_of_study','options':options}
    answer=resolve_field(store,field)
    assert answer['value']==expected
    assert store.history_entries('education')[0]['values']['field_of_study']=='Electrical Engineering and Computer Sciences'
    validate_answer(store,answer)
    put(store,'education',{'institution':'Fixture University','field_of_study':'History'},entry_id=entry['id'],source='Corrected synthetic history',confirmed=True)
    with pytest.raises(Blocked):validate_answer(store,answer)


@pytest.mark.parametrize('label,options',[
    ('Exact major name',['Electrical Engineering']),
    ('Field of Study',['Mechanical Engineering']),
    ('Field of Study',['Electrical Engineering','Computer Science']),
])
def test_joint_field_classification_does_not_invent_a_degree_or_choose_between_two_fields(store,label,options):
    from hireme.workday_history import put,resolve_field
    entry=put(store,'education',{'institution':'Fixture University','field_of_study':'Electrical Engineering and Computer Sciences'},source='Confirmed synthetic joint program',confirmed=True)
    field={'label':label,'type':'combobox','required':False,'section':'education',
           'history_entry_id':entry['id'],'history_key':'field_of_study','options':options}
    with pytest.raises(Blocked,match='mapping_review'):resolve_field(store,field)


def _primary_gpa_fixture(store):
    values={'institution':'Fixture University','degree':'B.S.','field_of_study':'Computer Science',
            'start':'2024-08','expected_end':'2028-05'}
    store.put_facts({'school':'Fixture University','degree':'B.S.','major':'Computer Science',
                     'college_start':'2024-08','graduation':'2028-05','gpa':'3.76/4.00'})
    entry=put(store,'education',values,source='Confirmed synthetic primary college',confirmed=True)
    return entry,{'label':'Overall Result (GPA)*','type':'text','required':True,'section':'education',
                  'history_entry_id':entry['id'],'history_key':'gpa','options':[]}


def test_primary_college_can_use_confirmed_profile_gpa_without_rewriting_history(store):
    entry,field=_primary_gpa_fixture(store);answer=resolve_field(store,field)
    assert answer['value']=='3.76' and 'gpa_fact_revision' in answer['provenance']
    validate_answer(store,answer)
    assert store.history_entries('education')[0]['revision']==entry['revision']
    assert 'gpa' not in store.history_entries('education')[0]['values']
    store.put_facts({'gpa':'3.90/4.00'})
    with pytest.raises(Blocked,match='history_changed'):validate_answer(store,answer)


@pytest.mark.parametrize('key,value',[
    ('school','Another University'),('degree','MS'),('major','History'),
    ('college_start','2025-08'),('graduation','2029-05'),
])
def test_profile_gpa_cannot_transfer_to_another_school_degree_or_study_period(store,key,value):
    _,field=_primary_gpa_fixture(store);store.put_facts({key:value})
    with pytest.raises(Blocked,match='history_confirmation_required'):resolve_field(store,field)
