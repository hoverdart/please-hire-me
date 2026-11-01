import json

import pytest

from hireme.answers import resolve, validate_package
from hireme.saved_answers import list_answers, revoke_answer
from hireme.util import Blocked


def test_reviewed_multiple_choices_are_reused_exactly_in_the_original_employer_context(store, job):
    field = {'label':'Select all classroom tools', 'type':'checkbox-group',
             'options':['Docker', 'Kubernetes', 'GitHub CI'], 'required':True}
    qid = store.ask(job['id'], job['host'], field['label'], field['options'], field=field, context=job)
    store.answer_question(qid, 'Docker; GitHub CI')
    answer = resolve(store, job['host'], field, context=job)
    assert answer['value'] == 'Docker; GitHub CI' and answer['provenance']['answer_id'] == qid
    assert store.saved_answer(job['host'], field['label'], field['options'], field=field,
                              context={**job, 'company':'Other employer'}) is None


@pytest.mark.parametrize('widget,label,value', [
    ('select','Select tools','Docker; Kubernetes'),
    ('checkbox-group','Select tools','Docker; Unknown'),
    ('checkbox-group','Select tools','Docker; Docker'),
    ('checkbox-group','Choose one tool','Docker; Kubernetes'),
    ('checkbox-group','Choose exactly two tools','Docker'),
    ('checkbox-group','Choose up to two tools','Docker; Kubernetes; GitHub CI')])
def test_reviewed_choices_reject_unlisted_duplicate_and_wrong_count_answers(store, job, widget, label, value):
    field = {'label':label, 'type':widget, 'options':['Docker', 'Kubernetes', 'GitHub CI'], 'required':True}
    qid = store.ask(job['id'], job['host'], label, field['options'], field=field, context=job)
    with pytest.raises(ValueError):store.answer_question(qid, value)
    assert not store.db.execute('SELECT 1 FROM answers WHERE id=?',(qid,)).fetchone()
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(qid,)).fetchone()[0] == 0


def test_withdrawing_answer_invalidates_preparation_but_preserves_attempt_evidence(store, job, package):
    field = package['answers'][0]['field']
    qid = store.ask(job['id'], job['host'], field['label'], [])
    store.answer_question(qid, store.facts()['email']['value'], 'email')
    package['answers'][0] = resolve(store, job['host'], field)
    assert package['answers'][0]['provenance']['answer_id'] == qid
    aid = store.prepare(job, package)
    revoke_answer(store, qid)
    assert not store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()
    assert store.saved_answer(job['host'], field['label'], []) is None
    with pytest.raises(Blocked): validate_package(store, job, package)
    assert json.loads((store.root / 'config/answers.json').read_text()) == []
    store.answer_question(qid, store.facts()['email']['value'], 'email')
    package['answers'][0] = resolve(store, job['host'], field)
    aid = store.prepare(job, package); store.begin_submit(aid); store.finish(aid, 'unknown')
    evidence = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    revoke_answer(store, qid)
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == evidence
    with pytest.raises(Blocked): store.prepare(job, package)


def test_saved_answer_search_paginates_unicode_and_literal_wildcards(store, job):
    for index in range(30):
        qid = store.ask(job['id'], job['host'], f'Fixture question {index}', [])
        store.answer_question(qid, 'Synthetic answer')
    qid = store.ask(job['id'], job['host'], 'Café 100%_ question', [])
    store.answer_question(qid, 'Special synthetic answer')
    page = list_answers(store)
    assert page['total'] == 31 and len(page['answers']) == 25
    second = list_answers(store, offset=25)
    assert len(second['answers']) == 6
    assert not {row['id'] for row in page['answers']} & {row['id'] for row in second['answers']}
    assert list_answers(store, search='CAFÉ 100%_')['total'] == 1
    assert list_answers(store, search='special synthetic')['answers'][0]['id'] == qid
    assert list_answers(store, offset=1000)['offset'] == 25


@pytest.mark.parametrize('arguments', [{'search': 'x' * 201}, {'offset': -1}, {'offset': True}, {'limit': 0}])
def test_saved_answer_search_rejects_unbounded_inputs(store, arguments):
    with pytest.raises(ValueError): list_answers(store, **arguments)


def test_withdrawing_missing_answer_does_not_clear_prepared_work(store, job, package):
    aid = store.prepare(job, package)
    with pytest.raises(ValueError): revoke_answer(store, 'missing')
    assert store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()


@pytest.mark.parametrize('widget',['combobox','text'])
def test_new_scoped_approval_supersedes_a_valid_generated_answer_cache(store,job,package,widget):
    from hireme.field_context import field_context
    from hireme.materials import writing_context_hash
    from hireme.util import digest
    store.update_settings({'tailored_writing':True})
    label='Which office would you choose?'
    options=['Austin, TX','Santa Clara, CA'] if widget=='combobox' else []
    f={'label':label,'type':widget,'required':True,'options':options,'maxlength':-1}
    host=job['host']+'|acme'
    text='I can work from Austin, TX or Santa Clara, CA.'
    tid=store.put_template('experience',text)
    writing={'value':'Austin, TX','provenance':{'tailored':True,'context_answer':True,
        'sample_parts':[{'text':text,'template_id':tid,'revision':1}],
        'text_hash':digest('Austin, TX'),'context_hash':writing_context_hash(store,job),
        'field_hash':digest(field_context(host,f,job)),'facts_hash':digest(store.facts())}}
    store.save_writing_answer(host,label,options,writing)
    assert resolve(store,host,f,context=job)['value']=='Austin, TX'
    qid=store.ask(job['id'],host,label,options,field=f,context=job)
    store.answer_question(qid,'Santa Clara, CA')
    answer=resolve(store,host,f,context=job)
    assert answer['value']=='Santa Clara, CA'
    assert answer['provenance']=={'answer_id':qid,'revision':1}
    package.update(answers=[answer],steps=[],facts_hash=digest(store.facts()))
    validate_package(store,{**job,'answer_scope':host},package)
    store.answer_question(qid,'Austin, TX')
    with pytest.raises(Blocked):validate_package(store,{**job,'answer_scope':host},package)
