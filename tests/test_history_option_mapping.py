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
