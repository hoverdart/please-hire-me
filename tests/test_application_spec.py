import copy
from dataclasses import FrozenInstanceError

import pytest

from hireme.application_spec import plan
from hireme.util import Blocked


def test_planner_is_immutable_and_reordered_controls_keep_question_identity(store,job,package):
    first=plan(store,job,package)
    changed=copy.deepcopy(package)
    changed['steps'][0]['fields'].reverse()
    second=plan(store,job,changed)
    assert first.specification==second.specification
    with pytest.raises(FrozenInstanceError):first.source_revision='changed'
    decoded=first.package();decoded['answers'].clear()
    assert first.package()['answers']


@pytest.mark.parametrize('change',['unsupported','duplicate','missing_document','document_bytes','choices','required_custom'])
def test_entire_visible_plan_is_validated_without_transport(store,job,package,change):
    p=copy.deepcopy(package)
    fields=p['steps'][0]['fields']
    if change=='unsupported':fields.append({'label':'Unrecognized optional widget','type':'canvas','required':False})
    elif change=='duplicate':fields.append(copy.deepcopy(fields[0]))
    elif change=='missing_document':p['documents']=[]
    elif change=='document_bytes':(store.root/'documents'/p['documents'][0]['filename']).write_bytes(b'changed')
    elif change=='choices':fields.append({'label':'Custom','type':'select','options':['A','A'],'required':False})
    else:fields.append({'label':'Custom legal declaration','type':'checkbox','required':True})
    with pytest.raises(Blocked):plan(store,job,p)
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()
    assert not store.db.execute('SELECT 1 FROM remote_draft_steps').fetchone()


def test_planner_holds_changed_approved_source(store,job,package):
    store.put_facts({'email':'changed@candidate.invalid'})
    with pytest.raises(Blocked):plan(store,job,package)


@pytest.mark.parametrize('value',['Yes','No'])
def test_single_checkbox_binds_one_checked_wire_value_without_a_fake_no_control(store,job,package,value):
    from hireme.answers import resolve
    from hireme.field_context import field_context
    field={'label':'Consent to receiving text messages','type':'checkbox','required':True,
        'options':['Yes','No'],'option_values':['native-checked'],'maxlength':-1,'ref':{'id':'sms'}}
    store.put_facts({'sms':value})
    answer=resolve(store,job['host'],field,context=job)
    assert answer['value']==value
    package['answers'].append(answer);package['steps'][0]['fields'].append(field)
    from hireme.util import digest
    package['facts_hash']=digest(store.facts())
    approved=plan(store,job,package)
    checkbox=next(q for q in approved.specification.questions if q.kind=='checkbox')
    assert checkbox.native_values==('native-checked',)
    assert field_context(job['host'],field,job)['options']==[('No',''),('Yes','native-checked')]
    field['type']='radio'
    with pytest.raises(Blocked):field_context(job['host'],field,job)


@pytest.mark.parametrize('change',['none','chosen_duplicate','native_duplicate','no_native','no_name','multiple','radio','unanswered'])
def test_duplicate_unselected_native_select_labels_allow_only_one_exact_approved_choice(store,job,package,change):
    from hireme.answers import resolve
    from hireme.util import digest
    store.put_facts({'school':'Fixture University'})
    f={'label':'Current school or university','type':'select','required':True,'maxlength':-1,
       'ref':{'name':'university'},'options':['Other school','Fixture University','Other school'],
       'option_values':['other-a','approved-id','other-b']}
    answer=resolve(store,job['host'],f,context=job)
    package['answers'].append(answer);package['steps'][0]['fields'].append(f)
    package['facts_hash']=digest(store.facts())
    if change=='chosen_duplicate':f['options'][0]='Fixture University'
    elif change=='native_duplicate':f['option_values'][0]='approved-id'
    elif change=='no_native':f.pop('option_values')
    elif change=='no_name':f['ref']={}
    elif change=='multiple':f['multiple']=True
    elif change=='radio':f['type']='radio'
    elif change=='unanswered':package['answers'].remove(answer);f['required']=False
    if change!='none':
        with pytest.raises(Blocked):plan(store,job,package)
        return
    approved=plan(store,job,package)
    q=next(q for q in approved.specification.questions if q.identity=='name:university')
    assert q.choices==tuple(f['options']) and q.native_values==tuple(f['option_values'])
