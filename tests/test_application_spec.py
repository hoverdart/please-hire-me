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
